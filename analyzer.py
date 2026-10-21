"""
Ядро анализа ниши YouTube на базе yt-dlp.

Логика:
1. По ссылке (канал или видео) определяем канал-конкурента.
2. Берём его видео, вытаскиваем ключевые фразы ниши (заголовки + теги).
3. Ищем по этим фразам на YouTube (по релевантности и за этот год).
4. Собираем найденные каналы, анализируем каждый: возраст, подписчики,
   просмотры, частота выпусков, "выстрелившие" ролики.
5. Считаем оценки: каждому каналу (Rising Score) и нише целиком.
"""
import os
import re
import math
import statistics
import datetime as dt
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

import yt_dlp

WORKERS = int(os.environ.get("WORKERS", "6"))

STOP = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе
под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая
много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно
всю между это почему очень самый самые новый новые новая часть выпуск видео обзор шортс серия
the a an and or of to in on for with is are was were be been this that these those it its at by from as how what
why who when where your you my our we me he she they them his her not no yes do does did can will just about into
out up down over new best top vs part episode video official full live shorts short one two most more
knew know prove proved really actually ever never every why there here than then only still make made get got
thing things way first last world people time year years day days don something wrong insane
crazy amazing great good bad big small
""".split())

# Грубая оценка RPM ($ за 1000 просмотров длинных видео, англоязычная аудитория).
CATEGORY_RPM = {
    "Education": (3, 8), "Science & Technology": (4, 10), "Howto & Style": (2, 6),
    "Autos & Vehicles": (3, 8), "News & Politics": (1.5, 5), "People & Blogs": (1, 4),
    "Entertainment": (1, 3), "Comedy": (0.8, 3), "Gaming": (0.8, 3), "Music": (0.5, 2),
    "Film & Animation": (1, 4), "Sports": (1, 4), "Travel & Events": (2, 5),
    "Pets & Animals": (1, 4), "Nonprofits & Activism": (1, 3),
}
FINANCE_HINTS = ("инвест", "деньг", "крипт", "финанс", "бизнес", "трейд", "налог", "ипотек", "кредит",
                 "заработ", "invest", "money", "crypto", "financ", "business", "trading", "tax", "stock")

# без фильтра / загружено в этом году / в этом месяце — свежая выдача поднимает молодые каналы
SEARCH_FILTERS = {"relevance": "", "year": "EgIIBQ%3D%3D", "month": "EgIIBA%3D%3D"}

CH_RE = re.compile(r"(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))", re.I)
VID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})")


# ---------------------------------------------------------------- yt-dlp
class _Silent:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _opts(flat=True, end=None):
    o = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True,
         "socket_timeout": 20, "extractor_retries": 2, "logger": _Silent()}
    if flat:
        o["extract_flat"] = "in_playlist"
        # примерные даты публикации прямо из списка видео — без лишних запросов к каждому ролику
        o["extractor_args"] = {"youtubetab": {"approximate_date": [""]}}
    else:
        o["extractor_args"] = {"youtube": {"skip": ["dash", "hls"]}}
    if end:
        o["playlistend"] = end
    ck = os.environ.get("YT_COOKIES_FILE")
    if ck and os.path.exists(ck):
        o["cookiefile"] = ck
    px = os.environ.get("YT_PROXY")
    if px:
        o["proxy"] = px
    return o


def _extract(url, flat=True, end=None):
    try:
        with yt_dlp.YoutubeDL(_opts(flat, end)) as y:
            return y.extract_info(url, download=False)
    except Exception:
        return None


def _entry_date(e):
    d = _d(e.get("upload_date"))
    if d:
        return d
    ts = e.get("timestamp") or e.get("release_timestamp")
    return dt.datetime.utcfromtimestamp(ts).date() if ts else None


def _d(s):
    try:
        return dt.datetime.strptime(str(s), "%Y%m%d").date()
    except Exception:
        return None


def resolve_channel_base(url):
    url = url.strip()
    if url.startswith("@"):
        return "https://www.youtube.com/" + url
    m = CH_RE.search(url)
    if m:
        return m.group(1).replace("://m.", "://www.").replace("://youtube.", "://www.youtube.")
    v = VID_RE.search(url)
    if v:
        info = _extract("https://www.youtube.com/watch?v=" + v.group(1), flat=False)
        if info and info.get("channel_id"):
            return "https://www.youtube.com/channel/" + info["channel_id"]
        raise ValueError("YouTube не отдал данные видео (возможна блокировка IP — см. README про cookies).")
    raise ValueError("Не удалось распознать ссылку. Нужна ссылка на канал (@имя) или на видео.")


def video_info(vid):
    if not vid:
        return {}
    info = _extract(f"https://www.youtube.com/watch?v={vid}", flat=False) or {}
    keys = ("id", "title", "upload_date", "view_count", "like_count", "comment_count",
            "channel_follower_count", "tags", "categories", "duration")
    return {k: info.get(k) for k in keys}


def channel_listing(base, limit):
    last = None
    for tab in ("/videos", "/shorts", "/streams"):
        info = _extract(base + tab, flat=True, end=limit)
        if not info:
            continue
        last = info
        ents = [e for e in (info.get("entries") or []) if e and e.get("id")]
        if ents:
            return info, ents, tab.strip("/")
    return last, [], None


def search(query, n, sp=""):
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}" + (f"&sp={sp}" if sp else "")
    info = _extract(url, flat=True, end=n) or {}
    return [e for e in (info.get("entries") or []) if e and e.get("id") and e.get("channel_id")]


# ---------------------------------------------------------------- оценки
def clamp(x):
    return max(0.0, min(100.0, float(x)))


def logscore(v, lo, hi):
    if not v or v <= 0:
        return 0.0
    return clamp((math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * 100)


def youth_score(age, capped):
    if age is None:
        return 0.0
    s = 100 if age <= 90 else 90 if age <= 180 else 75 if age <= 365 else 45 if age <= 730 else 15
    return 10 if capped else s


def activity_score(days):
    if days is None:
        return 0.0
    return 100 if days <= 14 else 80 if days <= 30 else 50 if days <= 60 else 25 if days <= 120 else 5


def label(score):
    if score >= 75:
        return "🔥 Взлетает"
    if score >= 60:
        return "📈 Растёт"
    if score >= 40:
        return "➖ Средне"
    return "💤 Слабо"


# ---------------------------------------------------------------- канал
def analyze_channel(base, limit=100, deep=False):
    info, ents, tab = channel_listing(base, limit)
    if not info:
        return None
    newest = ents[0] if ents else {}
    oldest = ents[-1] if ents else {}
    nd, od = _entry_date(newest), _entry_date(oldest)
    nv = {}
    # полные данные видео запрашиваем только если в списке нет дат или подписчиков (Shorts и т.п.)
    if ents and (nd is None or not info.get("channel_follower_count")):
        nv = video_info(newest.get("id"))
        nd = nd or _d(nv.get("upload_date"))
    if ents and od is None:
        ov = video_info(oldest.get("id")) if oldest.get("id") != newest.get("id") else nv
        od = _d(ov.get("upload_date"))

    title = info.get("channel") or info.get("uploader") or re.sub(r"\s-\s(Videos|Shorts|Live).*$", "", info.get("title") or "")
    cid = info.get("channel_id") or (info.get("id") if str(info.get("id", "")).startswith("UC") else None)
    subs = info.get("channel_follower_count") or nv.get("channel_follower_count")

    views = [e.get("view_count") for e in ents if e.get("view_count") is not None]
    med = statistics.median(views) if views else 0
    mx = max(views) if views else 0
    total = sum(views) if views else 0

    today = dt.date.today()
    capped = len(ents) >= limit
    age = (today - od).days if od else None
    last = (today - nd).days if nd else None
    span = (nd - od).days if nd and od else None
    upw = round(len(ents) / max((span or 0) / 7, 1), 2) if span is not None else None

    vps = (med / subs) if subs else None
    vpd = (total / max(age, 1)) if age is not None else None
    outlier = (mx / med) if med else None

    s = {
        "youth": youth_score(age, capped),
        "velocity": logscore(vpd, 10, 1e5),
        "traction": logscore(med, 100, 1e6),
        "efficiency": logscore(vps, 0.05, 2) if vps else 0.0,
        "activity": activity_score(last),
        "outlier": logscore(outlier, 1.5, 15) if outlier else 0.0,
    }
    rising = (0.30 * s["youth"] + 0.25 * s["velocity"] + 0.15 * s["traction"] +
              0.15 * s["efficiency"] + 0.10 * s["activity"] + 0.05 * s["outlier"])

    hits = sorted([e for e in ents if (e.get("view_count") or 0) >= max(3 * med, 1000)],
                  key=lambda e: -(e.get("view_count") or 0))[:5]

    thumbs = {t.get("id"): t.get("url") for t in (info.get("thumbnails") or [])}
    avatar = thumbs.get("avatar_uncropped")
    banner = thumbs.get("banner_uncropped")
    spark = [e.get("view_count") or 0 for e in ents[:24]][::-1]  # просмотры последних роликов, старые → новые

    res = {
        "id": cid, "title": title, "url": base, "format": tab,
        "avatar": (avatar.split("=")[0] + "=s176-c-k-c0x00ffffff-no-rj") if avatar else None,
        "banner": (banner.split("=")[0] + "=w1280-fcrop64=1,00005a57ffffa5a8-k-c0xffffffff-no-nd-rj") if banner else None,
        "spark": spark,
        "subs": subs, "videos": len(ents), "videos_capped": capped,
        "age_days": age, "age_exact": not capped, "first_upload": od.isoformat() if od else None,
        "last_upload_days": last, "uploads_per_week": upw,
        "median_views": int(med), "max_views": mx, "total_views_sample": total,
        "views_per_sub": round(vps, 3) if vps else None,
        "views_per_day": int(vpd) if vpd else None,
        "outlier_ratio": round(outlier, 1) if outlier else None,
        "scores": {k: round(v) for k, v in s.items()},
        "rising_score": round(rising), "label": label(rising),
        "hit_videos": [{"id": e["id"], "title": e.get("title"), "views": e.get("view_count")} for e in hits],
        "titles": [e.get("title") or "" for e in ents],
        "_ents": ents,
    }
    if deep:
        top = sorted(ents, key=lambda e: -(e.get("view_count") or 0))[:6]
        with ThreadPoolExecutor(WORKERS) as ex:
            res["top_info"] = [x for x in ex.map(lambda e: video_info(e["id"]), top) if x]
    return res


# ---------------------------------------------------------------- ключевые слова
def tokenize(t):
    return [w for w in re.findall(r"[a-zа-яё0-9]+", (t or "").lower())
            if len(w) >= 3 and w not in STOP and not w.isdigit()]


def extract_keywords(titles, tags, n=8, exclude="", handle=""):
    names = set(tokenize(exclude)) | {exclude.lower().replace(" ", ""), handle.lower()}
    names.discard("")
    ban = set(names)

    def is_name(w):
        return w in ban or any(len(w) >= 4 and (w in nm or nm in w) for nm in names)
    uni, bi = Counter(), Counter()
    for t in titles:
        tk = [w for w in tokenize(t) if not is_name(w)]
        uni.update(set(tk))
        bi.update(set(zip(tk, tk[1:])))
    tagc = Counter(x.lower().strip() for x in tags if x and 3 <= len(x) <= 40
                   and not is_name(x.lower().strip()) and not all(is_name(w) for w in tokenize(x)))
    cand = [(" ".join(k), c * 3) for k, c in bi.most_common(30) if c >= 2]
    cand += [(k, c * 2) for k, c in tagc.most_common(25) if c >= 2]
    cand += [(k, c) for k, c in uni.most_common(25) if c >= 2]
    out = []
    for k, _ in sorted(cand, key=lambda x: -x[1]):
        if any(k in o or o in k for o in out):
            continue
        out.append(k)
        if len(out) >= n:
            break
    if not out:
        out = [w for w, _ in uni.most_common(n)] or titles[:1]
    return out


TOPIC_HINTS = {
    "Science & Technology": ("science", "physics", "math", "tech", "space", "engineer", "наук", "физик", "космос",
                             "технолог", "гаджет", "смартфон", "нейросет", "программ", "code", "coding", " ai "),
    "Education": ("learn", "lesson", "history", "course", "tutorial", "учеб", "урок", "истори", "курс", "экзамен"),
    "Gaming": ("game", "gaming", "minecraft", "roblox", "игр", "прохожд", "letsplay"),
    "Howto & Style": ("recipe", "cook", "diy", "makeup", "рецепт", "готов", "своими руками", "ремонт", "макияж"),
    "Autos & Vehicles": ("car ", "cars", "auto", "машин", "авто"),
    "Travel & Events": ("travel", "trip", "путешеств", "туризм"),
    "Pets & Animals": ("dog", "cat ", "pets", "animal", "собак", "кошк", "животн"),
    "Music": ("music", "song", "cover", "музык", "песн"),
    "Sports": ("football", "fitness", "workout", "футбол", "спорт", "тренировк"),
}


def estimate_rpm(categories, text):
    t = " " + text.lower() + " "
    if any(h in t for h in FINANCE_HINTS):
        return (6, 15), "Финансы/бизнес"
    if not any(categories):
        hits = Counter({c: sum(t.count(h) for h in hs) for c, hs in TOPIC_HINTS.items()})
        if hits and hits.most_common(1)[0][1] > 0:
            categories = [hits.most_common(1)[0][0]]
    cat = Counter(c for c in categories if c).most_common(1)
    if cat and cat[0][0] in CATEGORY_RPM:
        return CATEGORY_RPM[cat[0][0]], cat[0][0]
    return (1, 4), (cat[0][0] if cat else "Не определена")


# ---------------------------------------------------------------- главный сценарий
def run_analysis(url, keywords=None, max_channels=30, per_query=25, max_age_days=730,
                 progress=lambda p, m: None):
    progress(3, "Определяю канал конкурента…")
    base = resolve_channel_base(url)

    progress(8, "Загружаю видео конкурента…")
    comp = analyze_channel(base, limit=60, deep=True)
    if not comp or not comp["videos"]:
        raise RuntimeError("Не удалось получить видео канала. YouTube мог заблокировать запросы с этого IP — "
                           "запустите сервис на своём компьютере или добавьте cookies (см. README).")

    top_info = comp.pop("top_info", [])
    tags = [t for v in top_info for t in (v.get("tags") or [])]
    cats = [c for v in top_info for c in (v.get("categories") or [])]

    if keywords:
        kws = [k.strip() for k in re.split(r"[,\n;]", keywords) if k.strip()][:10]
    else:
        handle = (re.search(r"/@([^/?#]+)", base) or re.search(r"/(?:c|user)/([^/?#]+)", base))
        kws = extract_keywords(comp["titles"], tags, n=8, exclude=comp["title"] or "",
                               handle=handle.group(1) if handle else "")
    progress(15, f"Ключевые фразы ниши: {', '.join(kws)}")

    # ---- поиск
    jobs = [(k, f, sp) for k in kws for f, sp in SEARCH_FILTERS.items()]
    found = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(search, k, per_query, sp): (k, f) for k, f, sp in jobs}
        for i, fu in enumerate(as_completed(futs), 1):
            found[futs[fu]] = fu.result() or []
            progress(15 + 25 * i / len(jobs), f"Поиск по запросам: {i}/{len(jobs)}")

    comp_id = comp["id"]
    chans = defaultdict(lambda: {"kw": set(), "views": [], "videos": {}, "recent": 0})
    kw_stats = defaultdict(lambda: {"views": [], "channels": set()})
    all_views = []
    for (k, f), ents in found.items():
        for e in ents:
            cid = e["channel_id"]
            v = e.get("view_count") or 0
            kw_stats[k]["views"].append(v)
            kw_stats[k]["channels"].add(cid)
            all_views.append(v)
            if cid == comp_id:
                continue
            c = chans[cid]
            c["name"] = e.get("channel") or e.get("uploader")
            c["url"] = f"https://www.youtube.com/channel/{cid}"
            c["kw"].add(k)
            c["views"].append(v)
            if f != "relevance":
                c["recent"] += 1
            c["videos"][e["id"]] = {"id": e["id"], "title": e.get("title"), "views": v}

    ranked = sorted(chans.items(), key=lambda kv: -(len(kv[1]["kw"]) * 2 + kv[1]["recent"] * 1.5
                                                    + 0.5 * math.log10(sum(kv[1]["views"]) + 1)))
    ranked = ranked[:max_channels]
    if not ranked:
        raise RuntimeError("Поиск ничего не вернул. Попробуйте задать ключевые слова вручную.")

    # ---- анализ каналов
    analyzed = []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(analyze_channel, c["url"]): (cid, c) for cid, c in ranked}
        for i, fu in enumerate(as_completed(futs), 1):
            cid, c = futs[fu]
            r = None
            try:
                r = fu.result()
            except Exception:
                pass
            if r:
                r["id"] = r["id"] or cid
                r["title"] = r["title"] or c["name"]
                r["found_by"] = sorted(c["kw"])
                r["search_videos"] = sorted(c["videos"].values(), key=lambda x: -(x["views"] or 0))[:3]
                r["young"] = bool(r["age_exact"] and r["age_days"] is not None and r["age_days"] <= max_age_days)
                analyzed.append(r)
            progress(40 + 55 * i / len(ranked), f"Анализ каналов: {i}/{len(ranked)}")

    progress(96, "Считаю оценки ниши…")
    analyzed.sort(key=lambda r: -r["rising_score"])
    young = [r for r in analyzed if r["young"]]
    young_rising = [r for r in young if r["rising_score"] >= 55]

    # ---- оценки ниши
    med_views = statistics.median(all_views) if all_views else 0
    subs_list = [r["subs"] for r in analyzed if r["subs"]]
    big_share = (sum(1 for s in subs_list if s >= 500_000) / len(subs_list)) if subs_list else 0
    med_subs = statistics.median(subs_list) if subs_list else 0
    rpm, rpm_cat = estimate_rpm(cats, " ".join(kws + tags + comp["titles"][:40]))

    demand = logscore(med_views, 1e3, 1e6)
    competition = clamp(0.6 * big_share * 100 + 0.4 * logscore(med_subs, 1e4, 5e6))
    window = clamp(len(young_rising) / max(len(analyzed), 1) * 250)
    monet = clamp(sum(rpm) / 2 / 10 * 100)
    freshness = clamp(len(young) / max(len(analyzed), 1) * 200)
    overall = 0.30 * demand + 0.25 * (100 - competition) + 0.30 * window + 0.15 * monet

    if overall >= 70:
        verdict = "Отличная ниша: спрос есть, новички реально растут. Заходить стоит."
    elif overall >= 55:
        verdict = "Хорошая ниша с рабочей точкой входа. Нужен свой угол подачи."
    elif overall >= 40:
        verdict = "Средне: либо высокая конкуренция, либо мало растущих новичков. Ищите подниши."
    else:
        verdict = "Слабая точка входа: ниша занята крупными каналами или спрос низкий."

    # ---- ключевые запросы
    kw_table = []
    young_ids = {r["id"] for r in young}
    for k in kws:
        st = kw_stats.get(k, {"views": [], "channels": set()})
        mv = statistics.median(st["views"]) if st["views"] else 0
        ysh = len(st["channels"] & young_ids) / max(len(st["channels"]), 1)
        kw_table.append({"keyword": k, "median_views": int(mv), "channels": len(st["channels"]),
                         "young_share": round(ysh * 100),
                         "score": round(0.5 * logscore(mv, 1e3, 1e6) + 0.5 * clamp(ysh * 250))})
    kw_table.sort(key=lambda x: -x["score"])

    # ---- темы-точки входа: хиты молодых каналов
    entry = []
    for r in young:
        for h in r["hit_videos"][:3]:
            entry.append({**h, "channel": r["title"], "channel_url": r["url"], "subs": r["subs"],
                          "x_median": round((h["views"] or 0) / max(r["median_views"], 1), 1)})
    entry.sort(key=lambda x: -(x["views"] or 0))

    for r in analyzed + [comp]:
        r.pop("_ents", None)
        r.pop("titles", None)

    progress(100, "Готово")
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "competitor": comp,
        "keywords": kws,
        "niche": {
            "overall": round(overall), "verdict": verdict,
            "scores": {"demand": round(demand), "competition": round(competition),
                       "entry_window": round(window), "monetization": round(monet),
                       "freshness": round(freshness)},
            "median_search_views": int(med_views), "median_subs": int(med_subs),
            "big_channels_share": round(big_share * 100),
            "rpm_range": rpm, "rpm_category": rpm_cat,
            "analyzed": len(analyzed), "young": len(young), "young_rising": len(young_rising),
            "max_age_days": max_age_days,
        },
        "keyword_table": kw_table,
        "channels": analyzed,
        "entry_topics": entry[:20],
    }
