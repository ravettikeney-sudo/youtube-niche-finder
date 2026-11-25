"""
Радар ниши: раз в несколько часов проверяет каналы ниши.
  • «ракеты» — ролики до 72 часов, которые набирают в разы быстрее обычного для своего канала;
  • реальные просмотры канала за сутки — по разнице общего счётчика просмотров между проверками.
О ракетах сразу пишет в Telegram.
"""
import datetime as dt
import html
import json
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait

import access
import analyzer as A

EVERY_H = 4             # как часто проверять
MAX_CHANNELS = 25
MAX_PER_USER = 2
ROCKET_X = 3.0          # во сколько раз быстрее обычного
ROCKET_MIN_VIEWS = 2000
MIN_BASELINE = 4
AGE_TOLERANCE_H = 6
_lock = threading.Lock()


def init():
    with access._lock, access._db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS radars(id TEXT PRIMARY KEY, code_hash TEXT, title TEXT, source_job TEXT,
            channels TEXT, created_at REAL, last_run REAL, next_at REAL, active INTEGER DEFAULT 1, running INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS radar_snaps(radar_id TEXT, chan_id TEXT, ts REAL, total_views INTEGER, subs INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS radar_snaps_i ON radar_snaps(radar_id, chan_id, ts)")
        c.execute("""CREATE TABLE IF NOT EXISTS radar_vids(radar_id TEXT, vid TEXT, chan_id TEXT, title TEXT, first_seen REAL,
            published REAL, views INTEGER, checked REAL, ratio REAL, rocket_at REAL, alerted INTEGER DEFAULT 0,
            PRIMARY KEY(radar_id, vid))""")
        columns = {row["name"] for row in c.execute("PRAGMA table_info(radar_vids)")}
        if "growth_hour" not in columns:
            c.execute("ALTER TABLE radar_vids ADD COLUMN growth_hour INTEGER")
            c.execute("ALTER TABLE radar_vids ADD COLUMN age_precise INTEGER NOT NULL DEFAULT 0")
            # Прежняя версия могла подставить время обнаружения вместо даты публикации.
            c.execute("UPDATE radar_vids SET published=NULL, ratio=NULL, rocket_at=NULL, alerted=0")
        c.execute("""CREATE TABLE IF NOT EXISTS radar_obs(radar_id TEXT, vid TEXT, chan_id TEXT, ts REAL,
            age_h REAL, precise INTEGER, views INTEGER, PRIMARY KEY(radar_id, vid, ts))""")
        c.execute("CREATE INDEX IF NOT EXISTS radar_obs_channels ON radar_obs(radar_id, chan_id, ts)")
        c.execute("UPDATE radars SET running=0")


def measured_ratio(video_id, age_h, precise, views, observations):
    if age_h is None or not 0 <= age_h <= 72:
        return None
    chosen = {}
    for point in observations:
        if point["vid"] == video_id or point["precise"] != precise:
            continue
        distance = abs(point["age_h"] - age_h)
        if distance <= AGE_TOLERANCE_H and (point["vid"] not in chosen or distance < chosen[point["vid"]][0]):
            chosen[point["vid"]] = (distance, point["views"])
    if len(chosen) < MIN_BASELINE:
        return None
    typical = statistics.median(v for _, v in chosen.values())
    return round(views / typical, 1) if typical else None


# ---------------------------------------------------------------- хранение
def _row(r):
    d = dict(r)
    d["channels"] = json.loads(d.get("channels") or "[]")
    return d


def create(code_hash, report, job_id):
    comp = report.get("competitor") or {}
    chans = [{"id": comp.get("id"), "url": comp.get("url"), "title": comp.get("title"), "avatar": comp.get("avatar")}]
    rel = sorted([c for c in report.get("channels") or [] if c.get("relevant")], key=lambda c: -(c.get("views_per_day") or c.get("median_views") or 0))
    for c in rel:
        if len(chans) >= MAX_CHANNELS:
            break
        if c.get("id") and c["id"] not in {x["id"] for x in chans}:
            chans.append({"id": c["id"], "url": c.get("url"), "title": c.get("title"), "avatar": c.get("avatar")})
    chans = [c for c in chans if c.get("id") and c.get("url")]
    rid = uuid.uuid4().hex[:10]
    with access._lock, access._db() as c:
        c.execute("INSERT INTO radars(id, code_hash, title, source_job, channels, created_at, next_at) VALUES(?,?,?,?,?,?,?)",
                  (rid, code_hash, comp.get("title") or "Ниша", job_id, json.dumps(chans, ensure_ascii=False), time.time(), time.time()))
    return rid


def list_for(code_hash=None):
    with access._lock, access._db() as c:
        rows = c.execute("SELECT * FROM radars WHERE active=1" + ("" if code_hash is None else " AND code_hash=?") + " ORDER BY created_at DESC",
                         () if code_hash is None else (code_hash,)).fetchall()
    return [_row(r) for r in rows]


def get(rid):
    with access._lock, access._db() as c:
        r = c.execute("SELECT * FROM radars WHERE id=?", (rid,)).fetchone()
    return _row(r) if r else None


def delete(rid):
    with access._lock, access._db() as c:
        c.execute("UPDATE radars SET active=0 WHERE id=?", (rid,))
        c.execute("DELETE FROM radar_snaps WHERE radar_id=?", (rid,))
        c.execute("DELETE FROM radar_vids WHERE radar_id=?", (rid,))
        c.execute("DELETE FROM radar_obs WHERE radar_id=?", (rid,))


# ---------------------------------------------------------------- проверка
def _check_channel(ch):
    base = ch["url"].rstrip("/")
    about = A.channel_about(base)
    info, ents, tab = A.channel_listing(base, 30, "en")
    now = time.time()
    vids = []
    for e in ents:
        if e.get("view_count") is None or A.video_format(e, tab) != "long":
            continue
        exact = e.get("release_timestamp")
        date = A._entry_date(e)
        # Дата без часов остаётся датой. Время обнаружения в неё не подставляем.
        if not exact and (not date or 0 <= (dt.date.today() - date).days <= 3) and len(vids) < 4:
            details = A.video_info(e["id"])
            exact = details.get("release_timestamp") or details.get("timestamp")
            date = A._entry_date(details) or date
        stamp = exact or (dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc).timestamp() if date else None)
        if not stamp or not 0 <= now - stamp <= 4 * 86400:
            continue
        vids.append({"id": e["id"], "title": e.get("title") or "", "views": e["view_count"], "ts": stamp,
                     "precise": int(bool(exact))})
    subs = (info or {}).get("channel_follower_count")
    return {"total": about.get("views"), "subs": subs, "vids": vids}


def run(rid, notify=None):
    """Одна проверка радара. notify(code_hash, text, button) — уведомление в Telegram."""
    r = get(rid)
    if not r or not r["active"]:
        return
    with access._lock, access._db() as c:
        if c.execute("SELECT running FROM radars WHERE id=?", (rid,)).fetchone()["running"]:
            return
        c.execute("UPDATE radars SET running=1 WHERE id=?", (rid,))
    try:
        prev_run = r.get("last_run")
        ex = ThreadPoolExecutor(3)
        fs = {ch["id"]: ex.submit(_check_channel, ch) for ch in r["channels"]}
        wait(list(fs.values()), timeout=600)
        ex.shutdown(wait=False, cancel_futures=True)
        now = time.time()
        rockets = []
        with access._lock, access._db() as c:
            known = {row["vid"]: dict(row) for row in c.execute("SELECT * FROM radar_vids WHERE radar_id=?", (rid,))}
            for ch in r["channels"]:
                f = fs.get(ch["id"])
                if not f or not f.done() or f.cancelled() or f.exception():
                    continue
                res = f.result()
                if res["total"] or res["subs"]:
                    c.execute("INSERT INTO radar_snaps VALUES(?,?,?,?,?)", (rid, ch["id"], now, res["total"], res["subs"]))
                history = list(c.execute("SELECT vid, age_h, precise, views FROM radar_obs WHERE radar_id=? AND chan_id=? AND ts>=?",
                                         (rid, ch["id"], now - 40 * 86400)))
                for v in res["vids"]:
                    k = known.get(v["id"]) or {}
                    pub, precise = v["ts"], v.get("precise", 0)
                    if k.get("age_precise") and not precise:
                        pub, precise = k["published"], 1
                    age_h = (now - pub) / 3600 if pub else None
                    ratio = measured_ratio(v["id"], age_h, precise, v["views"], history)
                    gap = (now - k["checked"]) / 3600 if k.get("checked") else 0
                    gain = v["views"] - k["views"] if k.get("views") is not None else None
                    growth = round(gain / gap) if gain is not None and gain >= 0 and 1 <= gap <= 12 else None
                    if age_h is not None and 0 <= age_h <= 72:
                        c.execute("INSERT OR IGNORE INTO radar_obs VALUES(?,?,?,?,?,?,?)",
                                  (rid, v["id"], ch["id"], now, age_h, precise, v["views"]))
                    is_rocket = ratio is not None and ratio >= ROCKET_X and v["views"] >= ROCKET_MIN_VIEWS
                    rocket_at = k.get("rocket_at") or (now if is_rocket else None)
                    if not k:
                        c.execute("""INSERT INTO radar_vids(radar_id, vid, chan_id, title, first_seen, published, views, checked,
                                     ratio, rocket_at, growth_hour, age_precise) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (rid, v["id"], ch["id"], v["title"], now, pub, v["views"], now, ratio, rocket_at, growth, precise))
                    else:
                        c.execute("""UPDATE radar_vids SET title=?, published=?, views=?, checked=?, ratio=COALESCE(?, ratio),
                                     rocket_at=?, growth_hour=?, age_precise=? WHERE radar_id=? AND vid=?""",
                                  (v["title"], pub, v["views"], now, ratio, rocket_at, growth, precise, rid, v["id"]))
                    if is_rocket and not k.get("alerted"):
                        rockets.append({"id": v["id"], "title": v["title"], "channel": ch["title"], "views": v["views"],
                                        "ratio": ratio, "age_h": round(age_h) if precise else None})
            c.execute("DELETE FROM radar_snaps WHERE radar_id=? AND ts<?", (rid, now - 40 * 86400))
            c.execute("DELETE FROM radar_vids WHERE radar_id=? AND checked<?", (rid, now - 30 * 86400))
            c.execute("DELETE FROM radar_obs WHERE radar_id=? AND ts<?", (rid, now - 40 * 86400))
            c.execute("UPDATE radars SET last_run=?, next_at=? WHERE id=?", (now, now + EVERY_H * 3600, rid))
        if rockets and notify and prev_run:  # в первую проверку не шлём — это ещё не «новости»
            for x in sorted(rockets, key=lambda r: -r["ratio"])[:5]:
                age = f" за {x['age_h']} ч" if x["age_h"] is not None else ""
                notify(r["code_hash"],
                       f"🚀 <b>Ракета в нише «{html.escape(r['title'])}»</b>\n{html.escape(x['title'])}\n"
                       f"{html.escape(x['channel'] or '')} · {A_h(x['views'])} просмотров{age}. "
                       f"В {str(x['ratio']).replace('.', ',')} раза больше обычного к этому возрасту.",
                       ("Открыть ролик", "https://www.youtube.com/watch?v=" + x["id"]))
                with access._lock, access._db() as c:
                    c.execute("UPDATE radar_vids SET alerted=1 WHERE radar_id=? AND vid=?", (rid, x["id"]))
    finally:
        with access._lock, access._db() as c:
            c.execute("UPDATE radars SET running=0 WHERE id=?", (rid,))


def A_h(n):
    n = n or 0
    return f"{n / 1e6:.1f} млн".replace(".", ",") if n >= 1e6 else f"{round(n / 1e3)} тыс." if n >= 1e3 else str(int(n))


def scheduler(notify):
    time.sleep(90)
    while True:
        try:
            with access._lock, access._db() as c:
                due = [row["id"] for row in c.execute("SELECT id FROM radars WHERE active=1 AND running=0 AND next_at<=?", (time.time(),))]
            for rid in due:
                run(rid, notify)
        except Exception:
            pass
        time.sleep(120)


# ---------------------------------------------------------------- для страницы
def view(rid):
    r = get(rid)
    now = time.time()
    with access._lock, access._db() as c:
        snaps = [dict(x) for x in c.execute("SELECT chan_id, ts, total_views, subs FROM radar_snaps WHERE radar_id=? ORDER BY ts", (rid,))]
        vids = [dict(x) for x in c.execute("SELECT * FROM radar_vids WHERE radar_id=?", (rid,))]
    by = {}
    for s in snaps:
        by.setdefault(s["chan_id"], []).append(s)
    chans = []
    for ch in r["channels"]:
        ss = [s for s in by.get(ch["id"], []) if s["total_views"]]
        per_day, prev_day, days = None, None, []
        if len(ss) >= 2:
            last = ss[-1]
            # просмотры за последние ~сутки: ближайший снимок к «24 часа назад» (не ближе 3 часов)
            base = min((s for s in ss if 18 * 3600 <= last["ts"] - s["ts"] <= 30 * 3600), key=lambda s: abs(last["ts"] - s["ts"] - 86400), default=None)
            if base:
                per_day = round((last["total_views"] - base["total_views"]) * 86400 / (last["ts"] - base["ts"]))
                old = min((s for s in ss if 18 * 3600 <= base["ts"] - s["ts"] <= 30 * 3600), key=lambda s: abs(base["ts"] - s["ts"] - 86400), default=None)
                if old:
                    prev_day = round((base["total_views"] - old["total_views"]) * 86400 / (base["ts"] - old["ts"]))
            # по дням: последний снимок каждого дня → разница
            daily = {}
            for s in ss:
                daily[dt.datetime.utcfromtimestamp(s["ts"]).date().isoformat()] = s
            ks = sorted(daily)
            for a, b in zip(ks, ks[1:]):
                sa, sb = daily[a], daily[b]
                days.append({"day": b, "views": round((sb["total_views"] - sa["total_views"]) * 86400 / max(sb["ts"] - sa["ts"], 3600))})
        lastsub = next((s["subs"] for s in reversed(by.get(ch["id"], [])) if s["subs"]), None)
        chans.append(dict(ch, per_day=per_day, prev_day=prev_day, days=days[-14:], subs=lastsub, total=ss[-1]["total_views"] if ss else None))
    titles = {c["id"]: c["title"] for c in r["channels"]}
    rockets = sorted([v for v in vids if v["rocket_at"] and now - v["rocket_at"] < 7 * 86400],
                     key=lambda v: -(v["rocket_at"] or 0))[:30]
    fresh = sorted([v for v in vids if v["published"] and now - v["published"] < 72 * 3600], key=lambda v: -(v["ratio"] or 0))[:20]
    for v in rockets + fresh:
        v["channel"] = titles.get(v["chan_id"])
        v["age_h"] = round((now - v["published"]) / 3600) if v.get("published") and v.get("age_precise") else None
    return {"id": r["id"], "title": r["title"], "source_job": r["source_job"], "last_run": r["last_run"], "next_at": r["next_at"],
            "running": bool(r["running"]), "channels": sorted(chans, key=lambda c: -(c["per_day"] or -1)),
            "rockets": rockets, "fresh": fresh, "snapshots": len({s["ts"] for s in snaps})}
