"""
«Идеи сейчас»: что выстрелило у похожих каналов за последние 3 недели и не выжжена ли уже тема копиями.
Источник — ваш канал (конкурентов сервис находит сам) или готовый отчёт по нише.
"""
import datetime as dt
import statistics
from concurrent.futures import ThreadPoolExecutor, wait

import analyzer as A
import ideas
import niche
import saturation
import thumbs

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
    out = []
    for v in long:
        a = v.get("age")
        if a is None or a > FRESH_DAYS or not med:
            continue
        x = v["views"] / (med * share(a))
        if x >= 2 and v["views"] >= 1000:
            out.append(dict(v, x=round(x, 1), channel=ch["title"], channel_url=ch["url"], channel_median=med, subs=ch.get("subs")))
    return out


def run(src, prog=lambda p, m: None):
    """src — ссылка на канал или отчёт по нише (dict)."""
    chans = []
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
    groups = groups[:12]
    if not groups:
        return {"type": "fresh", "generated_at": dt.datetime.now().isoformat(timespec="minutes"), "source_title": title,
                "channels_checked": len(chans), "ideas": [], "lang": lang}

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
            "channels_checked": len(chans), "ideas": res, "lang": lang,
            "counts": {k: sum(1 for r in res if r["verdict"]["key"] == k) for k in order}}
