"""
«Идеи сейчас»: что выстрелило у похожих каналов за последние 3 недели и не выжжена ли уже тема копиями.
Источник — ваш канал (конкурентов сервис находит сам) или готовый отчёт по нише.
"""
import datetime as dt
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, wait

import analyzer as A
import ideas
import niche
import saturation
import thumbs
import transfer

FRESH_DAYS = 21
CURVE = [(0, 0.12), (1, 0.45), (2, 0.6), (3, 0.7), (7, 0.85), (14, 0.95), (21, 1.0)]


def share(days):
    for (d0, s0), (d1, s1) in zip(CURVE, CURVE[1:]):
        if days <= d1:
            return s0 + (s1 - s0) * (days - d0) / (d1 - d0)
    return 1.0


def _terms(t):
    return set(ideas._stems(ideas._content(ideas.main_part(t))))


def outliers(ch, vids):
    """Свежие ролики канала, которые идут в разы быстрее его обычных роликов того же возраста."""
    long = [v for v in vids if not v.get("duration") or v["duration"] >= 61]
    aged = [v["views"] for v in long if v.get("age") is not None and v["age"] >= 14]
    if len(aged) < 4:
        return []
    med = statistics.median(aged)
    if med < 800:          # у совсем маленьких каналов «×200» — это случайность, а не сигнал ниши
        return []
    out = []
    for v in long:
        a = v.get("age")
        if a is None or a > FRESH_DAYS or not med:
            continue
        x = v["views"] / (med * share(a))
        if a < 1 and x < 4:          # ролику меньше суток — по нему ещё рано судить
            continue
        if x >= 2 and v["views"] >= 1000:
            out.append(dict(v, x=round(x, 1), channel=ch["title"], channel_url=ch["url"], channel_median=med, subs=ch.get("subs")))
    return out


def perf(ch, vs, min_age=5, max_age=150):
    """Ролики канала с x — во сколько раз выше обычного для этого канала (для поиска подач)."""
    long = [v for v in vs if (not v.get("duration") or v["duration"] >= 61) and v.get("views")]
    aged = [v["views"] for v in long if v.get("age") is not None and v["age"] >= 14]
    if len(aged) < 4:
        return []
    med = statistics.median(aged)
    if med < 500:
        return []
    return [dict(v, x=round(v["views"] / med, 1), channel=ch["title"]) for v in long
            if v.get("age") is not None and min_age <= v["age"] <= max_age]


def _script_ok(title, want):
    cyr = sum(1 for ch in title if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    lat = sum(1 for ch in title if "a" <= ch.lower() <= "z")
    return cyr >= lat if want == "cyr" else lat > cyr


def discover(chans, ylang, script_want, want=8, DIAG=None):
    DIAG = {} if DIAG is None else DIAG
    """Похожих каналов мало — добираем через поиск YouTube за месяц по темам лучших роликов канала(ов).
    Берём каналы, которые попадаются в выдаче по теме ниши чаще и с бо́льшими просмотрами."""
    vids = [v for _, vs in chans for v in vs if not v.get("duration") or v["duration"] >= 61]
    vocab = {}
    for v in vids:
        for t in _terms(v["title"]):
            vocab[t] = vocab.get(t, 0) + 1
    core = {t for t, n in vocab.items() if n >= 2}
    top = sorted(vids, key=lambda v: -(v.get("views") or 0))[:14]
    qs = list(dict.fromkeys(q for q in (saturation.query_for(v["title"], 3) for v in top) if q and len(q.split()) >= 2))[:8]
    # широкие запросы по главным словам ниши — по ним попадаются и соседние ниши (для подач)
    titles = [v["title"] for v in vids]
    broad = [ideas._surface(t, titles) for t, n in sorted(vocab.items(), key=lambda kv: -kv[1]) if n >= 3 and len(t) >= 4][:4]
    qs += [b for b in broad if b and b not in qs]
    if not qs:
        qs = []
    have = {c["url"].rstrip("/").split("/")[-1] for c, _ in chans} | {c.get("id") for c, _ in chans}
    ex = ThreadPoolExecutor(4)
    fs = [ex.submit(saturation._search_dated, q, saturation.SP_MONTH, ylang) for q in qs]
    wait(fs, timeout=90)
    ex.shutdown(wait=False, cancel_futures=True)
    score = {}
    for f in fs:
        if not f.done() or f.cancelled() or f.exception() is not None:
            continue
        for e in f.result() or []:
            cid = e.get("channel_id")
            if not cid or cid in have or not e.get("view_count") or (e.get("duration") or 0) < 61:
                continue
            if not _script_ok(e.get("title") or "", script_want) or len(_terms(e.get("title") or "") & core) < 1:
                continue
            sc = score.setdefault(cid, {"n": 0, "views": 0, "url": e.get("channel_url") or f"https://www.youtube.com/channel/{cid}"})
            sc["n"] += 1
            sc["views"] += e["view_count"]
    # «Похожие видео» рядом с лучшими роликами: каналы, которые смотрит ТА ЖЕ аудитория — самые точные соседи
    seeds = [v["id"] for v in sorted(chans[0][1], key=lambda v: -(v.get("views") or 0))[:6]]
    seeds += [max(vs, key=lambda v: v.get("views") or 0)["id"] for _, vs in chans[1:4] if vs]
    def rel(vid):
        r = A.related(vid)
        return r or A.related(vid)
    ex = ThreadPoolExecutor(4)
    rf = [ex.submit(rel, v) for v in seeds]
    wait(rf, timeout=45)
    ex.shutdown(wait=False, cancel_futures=True)
    nrel = 0
    for f in rf:
        if not f.done() or f.cancelled() or f.exception() is not None:
            continue
        for e in f.result() or []:
            nrel += 1
            cid = e["channel_id"]
            if cid in have:
                continue
            sc = score.setdefault(cid, {"n": 0, "views": 0, "url": e["channel_url"]})
            sc["rel"] = sc.get("rel", 0) + 1
    relc = sorted([s for s in score.values() if s.get("rel")], key=lambda s: (-s["rel"], -s["n"]))[:16]
    srch = [s for s in sorted(score.values(), key=lambda s: (-s["n"], -s["views"])) if not s.get("rel")]
    pick = (relc + srch)[:max(20, min(32, want * 2 + len(relc)))]
    DIAG["related"] = nrel; DIAG["related_channels"] = len(relc)
    DIAG.update(queries=qs, results=sum(len(f.result() or []) for f in fs if f.done() and not f.cancelled() and f.exception() is None), candidates=len(score), picked=len(pick), dropped_cat=0, dropped_topic=0)
    out, adj = [], []
    with ThreadPoolExecutor(4) as ex2:
        fs2 = [ex2.submit(thumbs.channel_videos, p["url"], 30) for p in pick]
        wait(fs2, timeout=150)
    # категория YouTube: у чужих по смыслу каналов она обычно другая (путешествия vs урбанистика, игры vs анимация)
    def top_cat(vs):
        vs = [v for v in vs if not v.get("duration") or v["duration"] >= 61]
        if not vs:
            return None
        try:
            return A.video_category(max(vs, key=lambda v: v.get("views") or 0)["id"])
        except Exception:
            return None
    okf = [(p_, f) for p_, f in zip(pick, fs2) if f.done() and not f.cancelled() and f.exception() is None]
    got = [f.result() for _, f in okf]
    is_rel = [(p_.get("rel") or 0) >= 2 for p_, _ in okf]   # в «Похожих» у 2+ разных роликов — точно та же аудитория
    with ThreadPoolExecutor(6) as ex3:
        own_cat_f = ex3.submit(top_cat, chans[0][1])
        cats = list(ex3.map(lambda cv: top_cat(cv[1]), got))
        own_cat = own_cat_f.result()
    for (ch, vs), cat, from_rel in zip(got, cats, is_rel):
        if vs and sum(1 for v in vs if _script_ok(v["title"], script_want)) < 0.6 * len(vs):
            continue      # канал на другом языке
        DIAG.setdefault("cats", []).append([ch.get("title"), cat])
        DIAG["own_cat"] = own_cat
        # каналы из «Похожих видео» — та же аудитория, категорию не проверяем
        if not from_rel and own_cat and cat and cat != own_cat and cat not in niche.CATCHALL:
            DIAG["dropped_cat"] += 1
            continue
        if True:
            # канал целиком должен быть про ту же тему: заметная доля его роликов — со словами ниши
            long = [v for v in vs if not v.get("duration") or v["duration"] >= 61]
            if not long:
                continue
            hit = [v for v in long if len(_terms(v["title"]) & core) >= 1]
            used = set().union(*[_terms(v["title"]) & core for v in hit]) if hit else set()
            if len(hit) >= 0.5 * len(long) and len(used) >= 4:
                if len(out) < want:
                    out.append((ch, vs))
            else:
                DIAG["dropped_topic"] += 1
                junk = sum(1 for v in long if re.search(r"\bkids?\b|для детей|детск|nursery|cartoon for|timer|countdown|таймер|\bmusic\b|музык|asmr|lofi|sleep sounds|white noise|compilation|сборник", v["title"], re.I))
                # соседняя ниша: та же категория YouTube, другая тема; детский и фоновый контент — не соседи
                if junk < 0.2 * len(long):
                    adj.append((dict(ch, _rel=from_rel), vs))
    return out, adj


def run(src, prog=lambda p, m: None):
    """src — ссылка на канал или отчёт по нише (dict)."""
    chans = []
    DIAG = {}
    if isinstance(src, dict):
        rep = src
        lang = (rep.get("competitor") or {}).get("lang") or "en"
        urls = [(rep.get("competitor") or {}).get("url")] + [c["url"] for c in sorted(
            [c for c in rep.get("channels") or [] if c.get("relevant")], key=lambda c: -(c.get("median_views") or 0))]
        urls = [u for u in dict.fromkeys(urls) if u][:16]
        title = (rep.get("competitor") or {}).get("title")
        prog(8, f"Открываю {len(urls)} каналов ниши…")
        with ThreadPoolExecutor(4) as ex:
            fs = [ex.submit(thumbs.channel_videos, u, 30) for u in urls]
            wait(fs, timeout=150)
        for f in fs:
            if f.done() and not f.cancelled() and f.exception() is None:
                chans.append(f.result())
    else:
        prog(5, "Открываю ваш канал…")
        own_ch, own_v = thumbs.channel_videos(src, 60)
        lang, title = own_ch["lang"], own_ch["title"]
        thumbs.add_perf(own_v)   # проставляет perf лучшим роликам — по ним ищутся конкуренты
        prog(12, "Ищу похожие каналы…")
        try:
            comps, _ = thumbs.find_competitors(own_ch, own_v, prog, want=10)
        except Exception:
            comps = []
        chans = [(own_ch, own_v)] + list(comps)
        # у конкурентов из поиска загружено мало роликов — дочитываем до 30, чтобы честно посчитать «обычные» просмотры
        prog(45, "Читаю свежие ролики похожих каналов…")
        with ThreadPoolExecutor(4) as ex:
            fs = {ex.submit(thumbs.channel_videos, c["url"], 30): i for i, (c, v) in enumerate(chans) if i and len(v) < 20}
            wait(list(fs), timeout=120)
        for f, i in fs.items():
            if f.done() and not f.cancelled() and f.exception() is None:
                chans[i] = f.result()
    if not chans:
        raise ValueError("Не удалось открыть каналы ниши — попробуйте позже.")
    ylang = niche.ylang_for(lang)
    script_want = "cyr" if lang in ("ru", "uk") else "lat"
    prog(48, "Ищу свежие ролики ниши и соседних ниш в поиске YouTube…")
    try:
        more, adjacent = discover(chans, ylang, script_want, want=max(0, 12 - len(chans) + 8), DIAG=DIAG)
    except Exception:
        more, adjacent = [], []
    chans += more

    prog(55, "Ищу ролики, которые выстрелили за последние 3 недели…")
    outs = []
    for ch, vs in chans:
        outs += outliers(ch, vs)
    outs.sort(key=lambda v: -v["x"])
    # одна тема у нескольких каналов — одна карточка
    groups = []
    for v in outs:
        t = _terms(v["title"])
        g = next((g for g in groups if len(g["terms"] & t) >= 2), None)
        if g:
            g["also"].append(v)
            g["terms"] |= t
        else:
            groups.append({"main": v, "also": [], "terms": t})
    per, kept = {}, []
    for g in groups:
        c = g["main"]["channel_url"]
        if per.get(c, 0) < 3:
            per[c] = per.get(c, 0) + 1
            kept.append(g)
    groups = kept[:12]
    prog(60, "Ищу подачи, которые работают в соседних нишах…")
    try:
        own_url = chans[0][0].get("url")
        others = [(ch, vs) for ch, vs in chans[1:] + adjacent if ch.get("url") != own_url]
        tr = transfer.build([v for v in perf(*chans[0], min_age=0, max_age=3650)] if not isinstance(src, dict)
                            else [v for ch, vs in chans for v in perf(ch, vs, min_age=0, max_age=3650)],
                            [v for ch, vs in others for v in perf(ch, vs) if v["x"] >= 2], lang,
                            own_main=chans[0][1][:60] if len(chans[0][1]) >= 15 else [v for ch, vs in chans for v in vs][:200])
    except Exception:
        tr = None
    if not groups:
        return {"type": "fresh", "transfer": tr, "generated_at": dt.datetime.now().isoformat(timespec="minutes"), "source_title": title,
                "channels_checked": len(chans), "ideas": [], "lang": lang, "diag": dict(DIAG)}

    prog(65, f"Проверяю {len(groups)} тем: сколько копий уже вышло…")
    ex = ThreadPoolExecutor(4)
    fs = [ex.submit(saturation.copies, g["main"]["title"], ylang, script_want, None,
                    tuple([g["main"]["id"]] + [a["id"] for a in g["also"]]), None, 14) for g in groups]
    wait(fs, timeout=180)
    ex.shutdown(wait=False, cancel_futures=True)
    res = []
    for g, f in zip(groups, fs):
        cp = f.result() if f.done() and not f.cancelled() and f.exception() is None else None
        vd = saturation.verdict(cp)
        m = g["main"]
        res.append({"id": m["id"], "title": m["title"], "channel": m["channel"], "channel_url": m["channel_url"],
                    "views": m["views"], "age": m["age"], "x": m["x"], "subs": m.get("subs"),
                    "also": [{"id": a["id"], "title": a["title"], "channel": a["channel"], "views": a["views"], "x": a["x"]} for a in g["also"][:4]],
                    "copies": cp and {k: cp[k] for k in ("query", "n", "n_week", "weak", "items")},
                    "verdict": vd or {"key": "unknown", "label": "Не проверено", "icon": "❔", "text": "Не удалось проверить, сколько копий уже вышло."}})
    order = {"fresh": 0, "catching": 1, "unknown": 2, "burned": 3}
    res.sort(key=lambda r: (order.get(r["verdict"]["key"], 2), -r["x"]))
    prog(100, "Готово")
    return {"type": "fresh", "generated_at": dt.datetime.now().isoformat(timespec="minutes"), "source_title": title,
            "channels_checked": len(chans), "ideas": res, "lang": lang, "diag": dict(DIAG), "transfer": tr,
            "counts": {k: sum(1 for r in res if r["verdict"]["key"] == k) for k in order}}
