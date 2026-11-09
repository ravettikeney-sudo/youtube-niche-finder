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
# какую долю «обычных» просмотров ролик набирает к N часам (типичная кривая YouTube)
CURVE = [(0, 0.0), (6, 0.2), (12, 0.32), (24, 0.45), (48, 0.6), (72, 0.7)]
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
        c.execute("UPDATE radars SET running=0")


def share(age_h):
    """Доля обычных просмотров к возрасту age_h часов."""
    for (h0, s0), (h1, s1) in zip(CURVE, CURVE[1:]):
        if age_h <= h1:
            return s0 + (s1 - s0) * (age_h - h0) / (h1 - h0)
    return CURVE[-1][1]


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


# ---------------------------------------------------------------- проверка
def _check_channel(ch):
    base = ch["url"].rstrip("/")
    about = A.channel_about(base)
    info, ents, tab = A.channel_listing(base, 30, "en")
    now = time.time()
    vids = []
    for e in ents:
        if e.get("view_count") is None:
            continue
        ts = e.get("timestamp") or e.get("release_timestamp")
        if not ts:
            d = A._entry_date(e)
            ts = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() if d else None
        vids.append({"id": e["id"], "title": e.get("title") or "", "views": e["view_count"], "ts": ts,
                     "short": tab == "shorts" or (e.get("duration") or 999) < 61})
    aged = [v["views"] for v in vids if v["ts"] and now - v["ts"] >= 7 * 86400]
    med = statistics.median(aged) if len(aged) >= 4 else None
    subs = (info or {}).get("channel_follower_count")
    return {"total": about.get("views"), "subs": subs, "vids": vids, "median": med}


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
                for v in res["vids"]:
                    k = known.get(v["id"])
                    pub = v["ts"]
                    if k is None and prev_run and (pub is None or pub < prev_run):
                        pub = (prev_run + now) / 2       # появился после прошлой проверки
                    elif k is not None and k.get("published"):
                        pub = min(k["published"], pub or k["published"])
                    age_h = (now - pub) / 3600 if pub else None
                    ratio = None
                    if age_h is not None and age_h <= 72 and res["median"] and not v["short"]:
                        exp = res["median"] * max(share(age_h), 0.05)
                        ratio = round(v["views"] / exp, 1) if exp else None
                    is_rocket = ratio is not None and ratio >= ROCKET_X and v["views"] >= ROCKET_MIN_VIEWS
                    rocket_at = (k or {}).get("rocket_at") or (now if is_rocket else None)
                    if k is None:
                        c.execute("""INSERT INTO radar_vids(radar_id, vid, chan_id, title, first_seen, published, views, checked, ratio, rocket_at)
                                     VALUES(?,?,?,?,?,?,?,?,?,?)""", (rid, v["id"], ch["id"], v["title"], now, pub, v["views"], now, ratio, rocket_at))
                    else:
                        c.execute("UPDATE radar_vids SET title=?, published=?, views=?, checked=?, ratio=COALESCE(?, ratio), rocket_at=? WHERE radar_id=? AND vid=?",
                                  (v["title"], pub, v["views"], now, ratio, rocket_at, rid, v["id"]))
                    if is_rocket and not (k or {}).get("alerted"):
                        rockets.append({"id": v["id"], "title": v["title"], "channel": ch["title"], "views": v["views"],
                                        "ratio": ratio, "age_h": round(age_h)})
                        c.execute("UPDATE radar_vids SET alerted=1 WHERE radar_id=? AND vid=?", (rid, v["id"]))
            c.execute("DELETE FROM radar_snaps WHERE radar_id=? AND ts<?", (rid, now - 40 * 86400))
            c.execute("DELETE FROM radar_vids WHERE radar_id=? AND checked<?", (rid, now - 30 * 86400))
            c.execute("UPDATE radars SET last_run=?, next_at=? WHERE id=?", (now, now + EVERY_H * 3600, rid))
        if rockets and notify and prev_run:  # в первую проверку не шлём — это ещё не «новости»
            for x in rockets[:5]:
                notify(r["code_hash"],
                       f"🚀 <b>Ракета в нише «{html.escape(r['title'])}»</b>\n{html.escape(x['title'])}\n"
                       f"{html.escape(x['channel'] or '')} · {A_h(x['views'])} просм. за {x['age_h']} ч — в {str(x['ratio']).replace('.', ',')} раза быстрее обычного для канала",
                       ("Открыть ролик", "https://www.youtube.com/watch?v=" + x["id"]))
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
            base = min((s for s in ss if last["ts"] - s["ts"] >= 3 * 3600), key=lambda s: abs(last["ts"] - s["ts"] - 86400), default=None)
            if base:
                per_day = round((last["total_views"] - base["total_views"]) * 86400 / (last["ts"] - base["ts"]))
                old = min((s for s in ss if base["ts"] - s["ts"] >= 3 * 3600), key=lambda s: abs(base["ts"] - s["ts"] - 86400), default=None)
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
        chans.append(dict(ch, per_day=per_day, prev_day=prev_day, days=days[-14:], subs=lastsub))
    titles = {c["id"]: c["title"] for c in r["channels"]}
    rockets = sorted([v for v in vids if v["rocket_at"] and now - v["rocket_at"] < 7 * 86400],
                     key=lambda v: -(v["rocket_at"] or 0))[:30]
    fresh = sorted([v for v in vids if v["published"] and now - v["published"] < 72 * 3600], key=lambda v: -(v["ratio"] or 0))[:20]
    for v in rockets + fresh:
        v["channel"] = titles.get(v["chan_id"])
        v["age_h"] = round((now - v["published"]) / 3600) if v.get("published") else None
    return {"id": r["id"], "title": r["title"], "source_job": r["source_job"], "last_run": r["last_run"], "next_at": r["next_at"],
            "running": bool(r["running"]), "channels": sorted(chans, key=lambda c: -(c["per_day"] or -1)),
            "rockets": rockets, "fresh": fresh, "snapshots": len({s["ts"] for s in snaps})}
