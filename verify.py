"""
Проверка данных перед выдачей отчёта по нише.

1. Сверка цифр: просмотры и подписчики из списков YouTube сравниваются со страницами самих видео.
2. Подозрительные каналы: подписчиков больше, чем могли дать просмотры (накрутка или удалённые видео) —
   такие каналы не считаются «молодыми растущими».
3. Спрос — по двум выборкам: весь поиск и только каналы ниши.
4. Устойчивость оценки: пересчёт на 300 случайных подвыборках → диапазон.
5. Полнота: сколько каналов и запросов реально загрузилось.
"""
import random
import statistics
from concurrent.futures import ThreadPoolExecutor


def clamp(x):
    return max(0.0, min(100.0, x))


def logscore(v, lo, hi):
    import math
    if not v or v <= lo:
        return 0.0
    if v >= hi:
        return 100.0
    return 100 * (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))


RISING = 55


def niche_scores(rel, views, rpm):
    """Оценки ниши из списка каналов ниши и просмотров видео поиска (та же формула, что в отчёте)."""
    young = [r for r in rel if r.get("young")]
    young_rising = [r for r in young if (r.get("rising_score") or 0) >= RISING]
    med_views = statistics.median(views) if views else 0
    subs = [r["subs"] for r in rel if r.get("subs")]
    big = (sum(1 for s in subs if s >= 500_000) / len(subs)) if subs else 0
    med_subs = statistics.median(subs) if subs else 0
    demand = logscore(med_views, 1e3, 1e6)
    competition = clamp(0.6 * big * 100 + 0.4 * logscore(med_subs, 1e4, 5e6))
    window = clamp(len(young_rising) / max(len(rel), 1) * 250)
    monet = clamp(sum(rpm) / 2 / 10 * 100)
    freshness = clamp(len(young) / max(len(rel), 1) * 200)
    overall = 0.30 * demand + 0.25 * (100 - competition) + 0.30 * window + 0.15 * monet
    return {"demand": demand, "competition": competition, "window": window, "monet": monet,
            "freshness": freshness, "overall": overall, "med_views": med_views, "med_subs": med_subs,
            "big_share": big, "young": young, "young_rising": young_rising}


def bootstrap(rel, views, rpm, n=300, seed=11):
    """Диапазон оценки: как она меняется, если взять другой случайный набор каналов и видео."""
    if len(rel) < 3:
        return None
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        rs = [rnd.choice(rel) for _ in rel]
        vs = [rnd.choice(views) for _ in views] if views else []
        vals.append(niche_scores(rs, vs, rpm)["overall"])
    vals.sort()
    return [round(vals[int(0.1 * n)]), round(vals[int(0.9 * n) - 1])]


def suspicious(r):
    """Подписчиков больше, чем могли дать видео канала: накрутка или удалённые ролики."""
    subs = r.get("subs") or 0
    allv = r.get("total_views_all")  # все просмотры канала со страницы «О канале»
    if subs >= 3000 and allv is not None and allv < subs:
        return f"подписчиков ({num(subs)}) больше, чем просмотров за всю историю канала ({num(allv)}) — похоже на накрутку"
    total = r.get("total_views_sample") or 0
    if subs < 3000 or r.get("videos_capped") or not r.get("videos"):
        return None
    if total < subs:
        return f"подписчиков ({num(subs)}) больше, чем просмотров у всех видео ({num(total)}) — накрутка или удалённые ролики"
    if total < 3 * subs and (r.get("median_views") or 0) < 0.02 * subs:
        return "просмотров очень мало для такого числа подписчиков — похоже на накрутку или перезапуск канала"
    return None


def crosscheck(channels, video_info, workers=4, per_channel=2):
    """Сверить цифры из списков со страницами видео. Возвращает результаты по каналам."""
    jobs = []
    for r in channels:
        ents = {e.get("id"): e for e in (r.get("_ents") or []) if e and e.get("id")}
        pick = [h["id"] for h in (r.get("hit_videos") or [])[:1] if h.get("id") in ents]
        rest = [i for i, e in ents.items() if i not in pick and (e.get("view_count") or 0) > 0]
        if rest:
            pick.append(rest[min(len(rest) - 1, 4)])  # свежий, но уже набравший просмотры ролик
        for vid in pick[:per_channel]:
            jobs.append((r, vid, ents[vid].get("view_count") or 0))
    if not jobs:
        return []

    def one(j):
        r, vid, listed = j
        try:
            info = video_info(vid) or {}
        except Exception:
            info = {}
        return r, vid, listed, info
    with ThreadPoolExecutor(workers) as ex:
        got = list(ex.map(one, jobs))
    out = {}
    for r, vid, listed, info in got:
        o = out.setdefault(r["id"], {"id": r["id"], "title": r["title"], "videos": 0, "failed": 0,
                                     "max_diff": 0.0, "subs_diff": None, "wrong_channel": False})
        exact = info.get("view_count")
        if not exact:
            o["failed"] += 1
            continue
        o["videos"] += 1
        if listed and exact:
            o["max_diff"] = max(o["max_diff"], abs(exact - listed) / max(exact, listed))
        if info.get("channel_id") and r.get("id") and info["channel_id"] != r["id"]:
            o["wrong_channel"] = True
        fs = info.get("channel_follower_count")
        if fs and r.get("subs"):
            o["subs_diff"] = abs(fs - r["subs"]) / max(fs, r["subs"])
            o["subs_exact"] = fs
    return list(out.values())


def level_of(checks):
    # «низкая» — только если не хватает самих данных; большой разброс оценки — это «средняя»
    bad = sum(1 for c in checks if c["state"] == "bad" and c.get("key") in ("relevant", "crosscheck", "loaded"))
    warn = sum(1 for c in checks if c["state"] in ("warn", "bad"))
    if bad:
        return "низкая"
    return "средняя" if warn >= 2 else "высокая"


def pct(x):
    return f"{round(100 * x)}%"


def num(x):
    return f"{int(x):,}".replace(",", "\u00a0")
