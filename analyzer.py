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
import json
import os
import re
import math
import statistics
import datetime as dt
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

import yt_dlp

import insights
import niche
import verify

niche.install_count_parser()
niche.install_region_patch()

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
crazy amazing great good bad big small like many every learn explains explained explain things
спустя месяцев месяц месяца года лет дней день дня понял понять которые который которая которое почему после
теперь стоит вообще реально просто нужно надо можно сделал сделать сделала купил купила будет было были
лучший лучшие лучшая самый самая самое весь всё вся свой своя свои наш наша ваш ваша мой моя
почти даже тоже также ещё уже очень сейчас сегодня вчера завтра когда где куда зачем пока между
против через чтобы если только всего этот эта эти это того тому этом новый новая новые новое
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
REL_MIN = int(os.environ.get("REL_MIN", "16"))  # порог «канал из этой ниши», 0–100
SEARCH_FILTERS = {"relevance": "", "year": "EgIIBQ%3D%3D", "month": "EgIIBA%3D%3D"}

CH_RE = re.compile(r"(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))", re.I)
VID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})")


# ---------------------------------------------------------------- yt-dlp
class _Silent:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _opts(flat=True, end=None, lang=None):
    o = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True,
         "socket_timeout": 20, "extractor_retries": 2, "logger": _Silent()}
    if flat:
        o["extract_flat"] = "in_playlist"
        # примерные даты публикации прямо из списка видео — без лишних запросов к каждому ролику
        o["extractor_args"] = {"youtubetab": {"approximate_date": [""]}}
    else:
        o["extractor_args"] = {"youtube": {"skip": ["dash", "hls"]}}
    code, region = niche.split_ylang(lang)
    if code:  # интерфейс YouTube на языке ниши: оригинальные названия и локальная выдача
        o["extractor_args"].setdefault("youtube", {})["lang"] = [code]
        o["extractor_args"].setdefault("youtubetab", {})["lang"] = [code]
    if region:  # страна выдачи — как у зрителей ниши, а не там, где стоит сервер
        o["extractor_args"].setdefault("youtube", {})["nf_gl"] = [region]
        o["extractor_args"].setdefault("youtubetab", {})["nf_gl"] = [region]
    if end:
        o["playlistend"] = end
    ck = os.environ.get("YT_COOKIES_FILE")
    if ck and os.path.exists(ck):
        o["cookiefile"] = ck
    px = os.environ.get("YT_PROXY")
    if px:
        o["proxy"] = px
    return o


def _extract(url, flat=True, end=None, lang=None):
    try:
        with yt_dlp.YoutubeDL(_opts(flat, end, lang)) as y:
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


def normalize_url(url):
    """Ссылку, как её вставляют люди, — к нормальному виду: без схемы, без косой черты перед @,
    m./music. поддомены, просто «@имя» или «имя», лишние кавычки и знаки в конце."""
    u = (url or "").strip().strip("\"'«»<>()[]").rstrip(".,;!")
    u = re.sub(r"\s+", "", u)
    if not u:
        return u
    u = re.sub(r"^(?:https?:)?/*", "", u, flags=re.I)                       # схема и «//»
    u = re.sub(r"^(?:www\.|m\.|music\.)?youtube\.com", "www.youtube.com", u, flags=re.I)
    u = re.sub(r"^www\.youtube\.com(?=@)", "www.youtube.com/", u)          # youtube.com@имя
    if u.lower().startswith(("www.youtube.com", "youtu.be")):
        return "https://" + u
    if u.startswith("@"):
        return "https://www.youtube.com/" + u
    if re.fullmatch(r"UC[\w-]{22}", u):                                     # id канала
        return "https://www.youtube.com/channel/" + u
    if re.fullmatch(r"[\w-]{11}", u) and re.search(r"[A-Z0-9]", u) and re.search(r"[a-z]", u):  # голый id видео
        return "https://www.youtube.com/watch?v=" + u
    if re.fullmatch(r"[\w.\-]{3,40}", u):                                   # просто имя канала
        return "https://www.youtube.com/@" + u
    return u


def resolve_channel_base(url):
    url = normalize_url(url)
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
    raise ValueError("Не удалось распознать ссылку. Вставьте ссылку на канал (например, youtube.com/@имя) или на любое его видео.")


def explain_missing(base):
    """Канал не открылся: его нет (опечатка в адресе) или YouTube не отвечает серверу?"""
    if _extract("https://www.youtube.com/@YouTube/videos", flat=True, end=1):
        name = base.rsplit("/", 1)[-1]
        return ValueError(f"Канал {name} не найден на YouTube. Проверьте адрес — проще всего скопировать ссылку "
                          "из адресной строки на странице канала или вставить ссылку на любое его видео.")
    return RuntimeError("YouTube сейчас не отвечает серверу. Попробуйте через несколько минут.")


def video_info(vid, lang=None):
    if not vid:
        return {}
    info = _extract(f"https://www.youtube.com/watch?v={vid}", flat=False, lang=lang) or {}
    keys = ("id", "title", "upload_date", "view_count", "like_count", "comment_count",
            "channel_follower_count", "tags", "categories", "duration", "language", "description")
    out = {k: info.get(k) for k in keys}
    if not out.get("upload_date") or out.get("view_count") is None:
        # страница ролика иногда требует «подтвердить, что вы не бот» — дата и просмотры есть и в ответе /next
        nx = video_next_info(vid)
        for k in ("id", "title", "upload_date", "view_count"):
            if out.get(k) in (None, "") and nx.get(k) is not None:
                out[k] = nx[k]
    return out


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def video_next_info(vid, region=None):
    """Запасной источник даты выхода и просмотров ролика (в т.ч. Shorts): ответ /youtubei/v1/next."""
    import urllib.request
    body = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20250101.00.00", "hl": "en",
                                   "gl": (region or "US").upper()}}, "videoId": vid}
    px = os.environ.get("YT_PROXY")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
    req = urllib.request.Request("https://www.youtube.com/youtubei/v1/next?prettyPrint=false", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": "https://www.youtube.com",
                                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
    try:
        j = json.loads(op.open(req, timeout=12).read().decode("utf-8", "ignore"))
    except Exception:
        return {}

    def find(o, key):
        if isinstance(o, dict):
            if key in o:
                return o[key]
            for v in o.values():
                r = find(v, key)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v, key)
                if r is not None:
                    return r
        return None
    pi = find(j, "videoPrimaryInfoRenderer") or {}
    out = {"id": vid}
    runs = (pi.get("title") or {}).get("runs") or []
    if runs:
        out["title"] = "".join(r.get("text", "") for r in runs)
    vc = (((pi.get("viewCount") or {}).get("videoViewCountRenderer") or {}).get("viewCount") or {})
    vtxt = vc.get("simpleText") or "".join(r.get("text", "") for r in vc.get("runs") or [])
    if vtxt:
        out["view_count"] = 0 if re.match(r"\s*no views", vtxt, re.I) else int(re.sub(r"[^\d]", "", vtxt) or 0)
    dtxt = (pi.get("dateText") or {}).get("simpleText") or ""
    m = re.search(r"([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})", dtxt)
    if m and m.group(1).lower() in _MONTHS:
        out["upload_date"] = f"{m.group(3)}{_MONTHS[m.group(1).lower()]:02d}{int(m.group(2)):02d}"
    elif re.search(r"\b(ago|today|now)\b", dtxt, re.I):
        out["upload_date"] = dt.date.today().strftime("%Y%m%d")
    return out


def thumb_texts(r, n=3):
    """Текст на обложках трёх самых популярных роликов канала (OCR). Пусто, если OCR недоступен."""
    try:
        import thumbs
        if not thumbs.pytesseract:
            return []
        ents = sorted((e for e in (r.get("_ents") or []) if e and e.get("id")), key=lambda e: -(e.get("view_count") or 0))[:n]
        out = []
        for e in ents:
            img = thumbs.load_img(e["id"])
            if img is not None:
                out.append(" ".join(w["w"] for w in thumbs._ocr(img)))
        return [t for t in out if t]
    except Exception:
        return []


def video_category(vid, region=None):
    """Категория YouTube ролика («Sports», «Gaming»…). yt-dlp её больше не отдаёт, берём из ответа плеера сайта.
    Если YouTube не ответил — None, тогда тематика проверяется только по названиям."""
    import urllib.request
    if not vid:
        return None
    body = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20250101.00.00", "hl": "en",
                                   "gl": (region or "US").upper()}}, "videoId": vid}
    px = os.environ.get("YT_PROXY")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
    req = urllib.request.Request("https://www.youtube.com/youtubei/v1/player?prettyPrint=false", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": "https://www.youtube.com",
                                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
    try:
        j = json.loads(op.open(req, timeout=12).read().decode("utf-8", "ignore"))
        return ((j.get("microformat") or {}).get("playerMicroformatRenderer") or {}).get("category")
    except Exception:
        return None


def _views_num(label):
    m = re.search(r"([\d.,]+)\s*(thousand|million|billion|K|M|B)?", label or "", re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    mult = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9}.get((m.group(2) or "").lower(), 1)
    return int(n * mult)


def related(vid, region=None):
    """«Похожие видео» справа от ролика — что смотрит та же аудитория. [{id, channel_id, channel_url, channel, views}]."""
    import urllib.request
    body = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20250101.00.00", "hl": "en",
                                   "gl": (region or "US").upper()}}, "videoId": vid}
    px = os.environ.get("YT_PROXY")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
    req = urllib.request.Request("https://www.youtube.com/youtubei/v1/next?prettyPrint=false", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": "https://www.youtube.com",
                                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
    try:
        j = json.loads(op.open(req, timeout=15).read().decode("utf-8", "ignore"))
    except Exception:
        return []
    res = (((j.get("contents") or {}).get("twoColumnWatchNextResults") or {}).get("secondaryResults") or {}).get("secondaryResults") or {}
    out = []
    for it in res.get("results") or []:
        lv = it.get("lockupViewModel")
        if not lv or lv.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
            continue
        s = json.dumps(lv)
        bm = re.search(r'"browseId": "(UC[\w-]{22})"(?:, "canonicalBaseUrl": "(/[^"]+)")?', s)
        if not bm:
            continue
        vm = re.search(r'"accessibilityLabel": "([\d.,]+ (?:thousand |million |billion )?views?)"', s)
        cm = re.search(r'"metadataParts": \[\{"text": \{"content": "([^"]+)"\}\}\]', s)
        out.append({"id": lv.get("contentId"), "channel_id": bm.group(1),
                    "channel_url": "https://www.youtube.com" + (bm.group(2) or "/channel/" + bm.group(1)),
                    "channel": cm.group(1) if cm else None, "views": _views_num(vm.group(1)) if vm else None})
    return out


ABOUT_RE = {
    "joined": re.compile(r'"joinedDateText":\{"content":"Joined ([A-Z][a-z]{2} \d{1,2}, \d{4})"'),
    "views": re.compile(r'"viewCountText":"([\d,]+) views?"'),
    "country": re.compile(r'"country":"([^"]{2,40})","subscriberCountText"'),
    "videos": re.compile(r'"videoCountText":"([\d,]+) videos?"'),
}


def channel_about(base, region=None):
    """Страница «О канале»: дата регистрации, все просмотры канала, страна, число видео."""
    import urllib.request
    url = base.rstrip("/") + "/about"
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9", "Cookie": f"SOCS=CAI; PREF=hl=en&gl={region or 'US'}"}
    px = os.environ.get("YT_PROXY")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
    try:
        html = op.open(urllib.request.Request(url, headers=hdr), timeout=15).read().decode("utf-8", "ignore")
    except Exception:
        return {}
    out = {}
    m = ABOUT_RE["joined"].search(html)
    if m:
        try:
            out["joined"] = dt.datetime.strptime(m.group(1), "%b %d, %Y").date()
        except ValueError:
            pass
    for k in ("views", "videos"):
        m = ABOUT_RE[k].search(html)
        if m:
            out[k] = int(m.group(1).replace(",", ""))
    m = ABOUT_RE["country"].search(html)
    if m:
        out["country"] = m.group(1)
    return out


def channel_listing(base, limit, lang=None):
    last = None
    for tab in ("/videos", "/shorts", "/streams"):
        info = _extract(base + tab, flat=True, end=limit, lang=lang)
        if not info:
            continue
        last = info
        ents = [e for e in (info.get("entries") or []) if e and e.get("id")]
        if ents:
            return info, ents, tab.strip("/")
    return last, [], None


def suggest(q, hl="en", gl=None):
    """Подсказки поиска YouTube — что реально набирают зрители."""
    import json as _json
    import urllib.request
    url = ("https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=" + hl +
           (f"&gl={gl}" if gl else "") + "&q=" + quote_plus(q))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        px = os.environ.get("YT_PROXY")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
        data = _json.loads(opener.open(req, timeout=6).read().decode("utf-8", "ignore"))
        return [x for x in data[1] if isinstance(x, str)]
    except Exception:
        return []


def on_topic(kw, suggestions, vocab):
    """Подсказки, в которых к ключу добавлены слова из тематики конкурента (а не из чужой ниши)."""
    own = {w[:5] for w in tokenize(kw)}
    out = []
    for x in suggestions:
        if not x.lower().startswith(kw.lower()):
            continue
        extra = {w[:5] for w in tokenize(x)} - own
        if extra and extra & vocab:
            out.append(x)
    return out


def search(query, n, sp="", lang=None):
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}" + (f"&sp={sp}" if sp else "")
    info = _extract(url, flat=True, end=n, lang=lang) or {}
    # только ролики: в выдаче бывают карточки каналов и плейлистов — у них нет просмотров
    return [e for e in (info.get("entries") or []) if e and e.get("id") and e.get("channel_id")
            and re.search(r"/watch\?v=|/shorts/", e.get("url") or "")]


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
def analyze_channel(base, limit=100, deep=False, lang=None):
    # цифры и даты — из английского интерфейса (их yt-dlp разбирает надёжно)
    info, ents, tab = channel_listing(base, limit)
    code, region = niche.split_ylang(lang)
    about = channel_about(base, region) if info else {}
    if lang and code != "en" and ents and tab:
        # оригинальные названия — из интерфейса на языке ниши (в английском YouTube их переводит)
        loc = _extract(f"{base}/{tab}", flat=True, end=limit, lang=lang) or {}
        orig = {e.get("id"): e.get("title") for e in (loc.get("entries") or []) if e and e.get("title")}
        if orig:
            ents = [dict(e, title=orig.get(e["id"], e.get("title"))) for e in ents]
        info = dict(info, description=loc.get("description") or info.get("description"),
                    channel=loc.get("channel") or info.get("channel"))
    if not info:
        return None
    if deep and tab == "shorts" and ents:
        # во вкладке Shorts YouTube не отдаёт даты, а у части роликов и просмотры: дозапрашиваем
        # у свежих роликов главного канала, чтобы возраст, медиана и «обычный ролик» были настоящими
        need = [e for e in ents[:15] if e.get("view_count") is None or not _entry_date(e)]
        if need:
            with ThreadPoolExecutor(WORKERS) as ex:
                full = dict(zip([e["id"] for e in need], ex.map(lambda e: video_info(e["id"]), need)))
            ents = [dict(e, view_count=e.get("view_count") if e.get("view_count") is not None else (full.get(e["id"]) or {}).get("view_count"),
                         upload_date=e.get("upload_date") or (full.get(e["id"]) or {}).get("upload_date"))
                    if e["id"] in full else e for e in ents]
    newest = ents[0] if ents else {}
    oldest = ents[-1] if ents else {}
    nd, od = _entry_date(newest), _entry_date(oldest)
    nv, ov = {}, {}
    # полные данные видео запрашиваем только если в списке нет дат или подписчиков (Shorts и т.п.)
    if ents and (nd is None or not info.get("channel_follower_count")):
        nv = video_info(newest.get("id"))
        nd = nd or _d(nv.get("upload_date"))
    # «1 год назад» в списке — это любая дата от 1 до 2 лет: для возраста канала уточняем точную дату
    coarse = od is not None and (dt.date.today() - od).days >= 300 and len(ents) < limit
    if ents and (od is None or coarse):
        ov = video_info(oldest.get("id")) if oldest.get("id") != newest.get("id") else nv
        od = _d(ov.get("upload_date")) or od

    title = info.get("channel") or info.get("uploader") or re.sub(r"\s-\s(Videos|Shorts|Live).*$", "", info.get("title") or "")
    cid = info.get("channel_id") or (info.get("id") if str(info.get("id", "")).startswith("UC") else None)
    subs = info.get("channel_follower_count") or nv.get("channel_follower_count")

    views = [e.get("view_count") for e in ents if e.get("view_count") is not None]
    today = dt.date.today()
    # «обычный ролик» канала — без роликов младше недели: они ещё набирают просмотры и занижают медиану
    # (у молодых каналов, которые выкладывают каждый день, таких бывает треть списка)
    settled = [e.get("view_count") for e in ents if e.get("view_count") is not None
               and (_entry_date(e) is None or (today - _entry_date(e)).days >= 7)]
    base_views = settled if len(settled) >= 5 else views
    med = statistics.median(base_views) if base_views else 0
    mx = max(views) if views else 0
    total = sum(views) if views else 0

    capped = len(ents) >= limit
    age = (today - od).days if od else None
    joined = about.get("joined")
    age_src = "первое видео" if age is not None and not capped else None
    if capped and joined:  # видео больше, чем в списке: возраст — по дате регистрации канала
        age, capped, age_src = (today - joined).days, False, "дата регистрации"
    last = (today - nd).days if nd else None
    span = (nd - od).days if nd and od else None
    upw = round(len(ents) / max((span or 0) / 7, 1), 2) if span is not None else None

    vps = (med / subs) if subs else None
    # просмотры в день — по всем просмотрам канала со страницы «О канале» (в списке может быть не всё)
    all_views = about.get("views") if about.get("views") and about["views"] >= total else total
    vpd = (all_views / max(age, 1)) if age is not None else None
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

    # язык звуковой дорожки — самый надёжный признак языка канала (названия YouTube может переводить)
    audio = (ov or {}).get("language") or (nv or {}).get("language")
    res = {
        "id": cid, "title": title, "url": base, "format": tab, "audio_lang": (audio or "").split("-")[0].lower() or None,
        "avatar": (avatar.split("=")[0] + "=s176-c-k-c0x00ffffff-no-rj") if avatar else None,
        "banner": (banner.split("=")[0] + "=w1280-fcrop64=1,00005a57ffffa5a8-k-c0xffffffff-no-nd-rj") if banner else None,
        "spark": spark,
        "subs": subs, "videos": len(ents), "videos_capped": capped,
        "age_days": age, "age_exact": not capped and age is not None, "age_source": age_src,
        "first_upload": od.isoformat() if od else None, "joined": joined.isoformat() if joined else None,
        "country": about.get("country"), "total_views_all": about.get("views"), "video_count_all": about.get("videos"),
        "last_upload_days": last, "uploads_per_week": upw,
        "median_views": int(med), "max_views": mx, "total_views_sample": total,
        "views_per_sub": round(vps, 3) if vps else None,
        "views_per_day": int(vpd) if vpd else None,
        "outlier_ratio": round(outlier, 1) if outlier else None,
        "scores": {k: round(v) for k, v in s.items()},
        "rising_score": round(rising), "label": label(rising),
        "hit_videos": [{"id": e["id"], "title": e.get("title"), "views": e.get("view_count")} for e in hits],
        "titles": [e.get("title") or "" for e in ents],
        "channel_tags": [t for t in (info.get("tags") or []) if isinstance(t, str)][:40],
        "description": (info.get("description") or "")[:400],
        "_ents": ents,
    }
    # ---- дополнительные показатели канала (из того же списка видео, без новых запросов)
    durs = [e.get("duration") for e in ents if e.get("duration")]
    vv = [e.get("view_count") or 0 for e in ents]
    # ---- Shorts отдельно: у канала с длинными видео они лежат на своей вкладке
    if tab == "shorts":
        sh_views, long_views = [e.get("view_count") or 0 for e in ents], []
    else:
        sh = _extract(base + "/shorts", flat=True, end=30) or {}
        sh_views = [e.get("view_count") or 0 for e in (sh.get("entries") or []) if e and e.get("id")]
        long_views = base_views
    rec, prev = vv[3:9], vv[9:24]  # 3 самых свежих пропускаем: они ещё не успели набрать просмотры
    pm = statistics.median(prev) if len(prev) >= 5 else 0
    pm = pm if pm >= 50 else 0
    res.update({
        "avg_duration": int(statistics.median(durs)) if durs else None,
        "shorts_count": len(sh_views), "long_count": len(long_views),
        "shorts_median": int(statistics.median(sh_views)) if sh_views else None,
        "long_median": int(statistics.median(long_views)) if long_views else None,
        "shorts_max": max(sh_views) if sh_views else None,
        "shorts_share": round(100 * len(sh_views) / max(1, len(sh_views) + min(len(long_views), 30))),
        "fmt": ("shorts" if len(long_views) < 5 and len(sh_views) >= 5 else
                "mixed" if len(long_views) >= 5 and len(sh_views) >= 5 else "long"),
        "trend": round(min(statistics.median(rec) / pm, 10), 2) if len(rec) >= 4 and pm else None,
        "hit_rate": round(100 * sum(1 for v in base_views if v >= 2 * med) / len(base_views)) if base_views and med else None,
        "lang": insights.detect_lang(res["titles"]),
    })
    if deep:
        top = sorted(ents, key=lambda e: -(e.get("view_count") or 0))[:6]
        with ThreadPoolExecutor(WORKERS) as ex:
            res["top_info"] = [x for x in ex.map(lambda e: video_info(e["id"]), top) if x]
    return res


# ---------------------------------------------------------------- ключевые слова
def tokenize(t):
    return [w for w in re.findall(r"[a-zа-яё0-9]+", (t or "").lower())
            if len(w) >= 3 and w not in STOP and not w.isdigit()]


def extract_keywords(titles, tags, n=8, exclude="", handle="", primary=()):
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
    # ключевые слова, которые автор сам указал для канала, — самый точный источник тематики
    for t in primary:
        k = t.lower().strip()
        if not (3 <= len(k) <= 40) or is_name(k) or all(is_name(w) for w in tokenize(k)) or not tokenize(k):
            continue
        if any(k == o or k.rstrip("s") == o.rstrip("s") for o in out):
            continue
        out.append(k)
        if len(out) >= max(4, n - 3):
            break
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
def split_inputs(url):
    """Одна или несколько ссылок через пробел, запятую или с новой строки (до 3)."""
    parts = [p for p in re.split(r"[\s,;]+", url or "") if p]
    return parts[:3] or [url]


def excl_matcher(words):
    """Слова-исключения: «новости» ловит и «новостной», «гта» — «GTA 6»."""
    pats = []
    for w in words:
        w = w.lower().strip()
        if not w:
            continue
        st = w[:max(4, len(w) - 2)] if len(w) > 5 else w
        pats.append((w, re.compile(r"(?<![\w])" + re.escape(st), re.I)))
    def hit(text):
        for w, p in pats:
            if p.search(text or ""):
                return w
        return None
    return hit


def run_analysis(url, keywords=None, max_channels=30, per_query=25, max_age_days=730,
                 progress=lambda p, m: None, same_lang=True, strict=True, lang=None, exclude=None, region=None,
                 remembered=None):
    progress(3, "Определяю канал конкурента…")
    inputs = split_inputs(url)
    base = resolve_channel_base(inputs[0])
    extra_bases = []
    for u in inputs[1:]:
        try:
            b = resolve_channel_base(u)
            if b != base and b not in extra_bases:
                extra_bases.append(b)
        except Exception:
            pass
    excl_words = [w.strip() for w in re.split(r"[,\n;]", exclude or "") if w.strip()][:20]
    excl = excl_matcher(excl_words)

    # ---- язык конкурента: по описанию канала (YouTube его не переводит) и оригинальным названиям
    progress(5, "Определяю язык канала…")
    probe = _extract(base + "/videos", flat=True, end=12) or {}
    if not probe and not _extract(base, flat=True, end=1):
        raise explain_missing(base)
    ptitles = [e.get("title") or "" for e in (probe.get("entries") or []) if e]
    desc = probe.get("description") or ""
    cname = probe.get("channel") or probe.get("uploader") or ""
    comp_lang = insights.detect_lang([desc] * 3 + ptitles) if (desc or ptitles) else "en"
    # имя и описание канала YouTube обычно не переводит — они надёжнее переведённых названий
    for sig in (desc, cname):
        if comp_lang == "en" and sig and niche.script(sig) not in ("lat", None, "mixed"):
            comp_lang = insights.detect_lang([sig])
    if lang and lang != "auto":
        comp_lang = lang
    region = (region or "").upper() if region and region != "auto" else None
    ylang = niche.ylang_for(comp_lang, region)

    progress(8, "Загружаю видео конкурента…")
    comp = analyze_channel(base, limit=60, deep=True, lang=ylang)
    if not comp:
        raise explain_missing(base)
    if not comp["videos"]:
        raise ValueError("На этом канале нет роликов, по которым можно определить нишу. Вставьте канал, где уже есть видео, "
                         "или найдите нишу по теме (вкладка «По теме»).")
    if (not lang or lang == "auto") and comp.get("audio_lang") and comp["audio_lang"] in niche.YT_LANG | {"en": None} \
            and comp["audio_lang"] != comp_lang:
        # язык дорожки видео конкурента точнее, чем язык по названиям — перезапускаем с ним
        comp_lang = comp["audio_lang"]
        ylang = niche.ylang_for(comp_lang, region)
        comp = analyze_channel(base, limit=60, deep=True, lang=ylang) or comp
    comp["lang"] = comp_lang
    comp_script = "cyr" if comp_lang in ("ru", "uk") else "lat" if comp_lang in insights.LATIN_HINTS or comp_lang == "en" else "other"

    # ---- другие каналы-примеры: вместе с конкурентом они задают профиль ниши
    examples = []
    if extra_bases:
        progress(10, f"Загружаю ещё {len(extra_bases)} канала-примера…")
        with ThreadPoolExecutor(WORKERS) as ex:
            examples = [x for x in ex.map(lambda b: analyze_channel(b, 60, False, ylang), extra_bases) if x and x["videos"]]
    prof_titles = list(comp["titles"])
    prof_ctags = list(comp.get("channel_tags") or [])
    for x in examples:
        prof_titles += x["titles"]
        prof_ctags += x.get("channel_tags") or []
    example_ids = {comp.get("id")} | {x.get("id") for x in examples}

    top_info = comp.pop("top_info", [])
    hl = {"zh": "zh-CN"}.get(comp_lang, comp_lang)
    search_region = niche.split_ylang(ylang)[1]
    tags = [t for v in top_info for t in (v.get("tags") or [])]
    cats = [c for v in top_info for c in (v.get("categories") or [])]
    # ядро тематики: слова, которые встречаются хотя бы в двух заголовках или тегах конкурента
    vc = Counter(w[:5] for t in prof_titles + tags for w in set(tokenize(t)))
    vocab = {w for w, c in vc.items() if c >= 2}

    if keywords:
        kws = [k.strip() for k in re.split(r"[,\n;]", keywords) if k.strip()][:10]
    else:
        handle = (re.search(r"/@([^/?#]+)", base) or re.search(r"/(?:c|user)/([^/?#]+)", base))
        kws = extract_keywords(prof_titles, tags, n=9, exclude=" ".join([comp["title"] or ""] + [x["title"] or "" for x in examples]),
                               handle=handle.group(1) if handle else "", primary=prof_ctags)
        kws = [k for k in kws if not excl(k)][:7]
    progress(13, f"Ключевые фразы ниши: {', '.join(kws)}")
    with ThreadPoolExecutor(WORKERS) as ex:
        sugg = dict(zip(kws, ex.map(lambda k: suggest(k, hl, search_region), kws)))

    # ---- запросы: ключевые фразы + названия лучших и свежих видео конкурента (самый точный сигнал ниши)
    seeds = []
    per = 6 if not examples else max(3, 9 // (1 + len(examples)))
    for ch in [comp] + examples:
        ents_c = [e for e in (ch.get("_ents") or []) if not excl(e.get("title"))]
        by_views = sorted(ents_c, key=lambda e: -(e.get("view_count") or 0))[:per]
        for e in by_views + ents_c[:2 if examples else 4]:
            q = niche.clean_query(e.get("title"))
            if len(q.split()) >= 2 and q not in seeds:
                seeds.append(q)
    seeds = seeds[:12 if examples else 9]
    jobs = [("kw", k, f, sp) for k in kws for f, sp in SEARCH_FILTERS.items()]
    jobs += [("seed", q, "relevance", "") for q in seeds]
    found = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(search, q, per_query, sp, ylang): (kind, q, f) for kind, q, f, sp in jobs}
        for i, fu in enumerate(as_completed(futs), 1):
            found[futs[fu]] = fu.result() or []
            progress(13 + 25 * i / len(jobs), f"Поиск по YouTube: {i}/{len(jobs)}")

    comp_id = comp["id"]
    chans = defaultdict(lambda: {"kw": set(), "views": [], "videos": {}, "recent": 0, "seed": 0, "titles": [],
                                 "foreign": 0, "native": 0})
    kw_stats = defaultdict(lambda: {"views": [], "channels": set()})
    all_views = []
    view_pairs = []  # (канал, просмотры) — чтобы спрос можно было посчитать только по каналам ниши
    for (kind, k, f), ents in found.items():
        for e in ents:
            cid = e["channel_id"]
            v = e.get("view_count") or 0
            if excl(e.get("title")):
                continue
            ok_lang = niche.same_script(e.get("title"), comp_script)
            if kind == "kw" and ok_lang:
                kw_stats[k]["views"].append(v)
                kw_stats[k]["channels"].add(cid)
                all_views.append(v)
                view_pairs.append((cid, v))
            if cid == comp_id or cid in example_ids:
                continue
            c = chans[cid]
            c["name"] = e.get("channel") or e.get("uploader")
            c["url"] = f"https://www.youtube.com/channel/{cid}"
            if kind == "kw":
                c["kw"].add(k)
            else:
                c["seed"] += 1
            c["views"].append(v)
            c["native" if ok_lang else "foreign"] += 1
            if f != "relevance":
                c["recent"] += 1
            if e["id"] not in c["videos"]:
                c["titles"].append(e.get("title") or "")
            c["videos"][e["id"]] = {"id": e["id"], "title": e.get("title"), "views": v}
    if same_lang:
        chans = {cid: c for cid, c in chans.items() if c["native"] >= c["foreign"]}
    # каналы, которые пользователь уже убирал из этой ниши, — не анализируем и не показываем
    hidden = []
    try:
        hidden = remembered(comp_id) if remembered else []
    except Exception:
        hidden = []
    hidden_ids = {h["id"] for h in hidden}
    chans = {cid: c for cid, c in chans.items() if cid not in hidden_ids}

    # ---- предварительный отбор: похожесть найденных роликов на видео конкурента
    comp_docs = niche.doc_terms(prof_titles * 2 + tags + prof_ctags * 2 + kws * 3, tokenize)
    pre = niche.Similarity(comp_docs, {cid: niche.doc_terms(c["titles"], tokenize) for cid, c in chans.items()})
    def pre_score(c):
        sim = pre.score(niche.doc_terms(c["titles"], tokenize))
        return sim * 10 + len(c["kw"]) * 1.2 + min(c["seed"], 4) * 1.5 + c["recent"] * 0.5 \
            + 0.3 * math.log10(sum(c["views"]) + 1)
    ranked = sorted(chans.items(), key=lambda kv: -pre_score(kv[1]))
    ranked = ranked[:int(max_channels * 1.4)]
    if not ranked:
        raise RuntimeError("Поиск ничего не вернул. Попробуйте задать ключевые слова вручную"
                           + (" или отключите «Только язык конкурента»." if same_lang else "."))

    # ---- анализ каналов
    analyzed = []
    load_failed = 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(analyze_channel, c["url"], 100, False, ylang): (cid, c) for cid, c in ranked}
        for i, fu in enumerate(as_completed(futs), 1):
            cid, c = futs[fu]
            r = None
            try:
                r = fu.result()
            except Exception:
                pass
            if not r:
                load_failed += 1
            if r:
                r["id"] = r["id"] or cid
                r["title"] = r["title"] or c["name"]
                r["found_by"] = sorted(c["kw"])
                r["similar_videos"] = c["seed"]
                r["search_videos"] = sorted(c["videos"].values(), key=lambda x: -(x["views"] or 0))[:3]
                r["young"] = bool(r["age_exact"] and r["age_days"] is not None and r["age_days"] <= max_age_days)
                analyzed.append(r)
            progress(38 + 57 * i / len(ranked), f"Анализ каналов: {i}/{len(ranked)}")

    # ---- страница одного ролика каждого канала: категория YouTube и язык звука (заодно пойдёт в сверку цифр)
    vi_cache = {}

    def vinfo(vid, lng=None):
        if vid not in vi_cache:
            vi_cache[vid] = video_info(vid, lng or ylang) or {}
        return vi_cache[vid]

    def probe_vid(r):
        ents = {e.get("id") for e in (r.get("_ents") or []) if e}
        hit = [h["id"] for h in (r.get("hit_videos") or [])[:1] if h.get("id") in ents]
        if hit:
            return hit[0]
        return r["_ents"][min(2, len(r["_ents"]) - 1)].get("id") if r.get("_ents") else None
    progress(95, f"Проверяю категорию и язык {len(analyzed)} каналов по страницам их видео…")
    # не дольше ~2,5 минут: если YouTube отвечает медленно, недополученные каналы проверяются только по названиям
    from concurrent.futures import wait as _wait
    ex = ThreadPoolExecutor(WORKERS)
    cat_f = {r["id"]: ex.submit(video_category, probe_vid(r), search_region) for r in analyzed}
    cc_f = [ex.submit(video_category, h.get("id"), search_region) for h in (comp.get("hit_videos") or [])[:3]]
    vi_f = {r["id"]: ex.submit(lambda r=r: vinfo(probe_vid(r)) if probe_vid(r) else {}) for r in analyzed}
    _wait(list(cat_f.values()) + cc_f + list(vi_f.values()), timeout=150)
    ex.shutdown(wait=False, cancel_futures=True)
    res_of = lambda f, d=None: f.result() if f.done() and not f.cancelled() and f.exception() is None else d
    comp_cats = [res_of(f) for f in cc_f]
    for r in analyzed:
        vi = res_of(vi_f[r["id"]], {}) or {}
        if True:
            r["category"] = res_of(cat_f[r["id"]]) or ((vi.get("categories") or [None])[0])
            r["_probe"] = [(vi.get("description") or "")[:2000]] + list(vi.get("tags") or [])[:30]
            # строгий язык: латинские языки (en/es/pt/de/fr/it/tr) по алфавиту не различить,
            # а названия YouTube переводит. Надёжны язык звуковой дорожки и описание канала.
            a = (vi.get("language") or "").split("-")[0].lower()
            if a and not r.get("audio_lang") and same_lang and comp_script == "lat":
                r["audio_lang"] = a

    # ---- точность ниши: TF-IDF похожесть всего канала на конкурента + язык
    progress(96, "Проверяю, какие каналы точно из этой ниши…")
    cand_docs = {r["id"]: niche.doc_terms(r["titles"] + (r.get("channel_tags") or []) * 2
                                          + ([r.get("description")] if r.get("description") else []), tokenize)
                 for r in analyzed}
    sim = niche.Similarity(comp_docs, cand_docs)
    raw = {rid: sim.score(d) for rid, d in cand_docs.items()}
    cut, ref = niche.adaptive_cut(raw.values(), strict)
    gate = niche.TopicGate(prof_titles, tags + prof_ctags + kws, tokenize)
    at_risk = []
    niche_cats = [c for c in cats + comp_cats if c]
    comp["category"] = Counter(niche_cats).most_common(1)[0][0] if niche_cats else None
    for r in analyzed:
        sc = raw.get(r["id"], 0)
        r["lang"] = r.get("lang") or insights.detect_lang(r["titles"])
        lang_ok = niche.channel_lang_ok(r["titles"][:30], comp_script, r.get("description"))
        if r.get("audio_lang") and comp_lang and r["audio_lang"] != comp_lang:
            lang_ok = False  # говорят на другом языке, хотя названия переведены
        r["lang_source"] = "звук видео" if r.get("audio_lang") else None
        if comp_script == "lat":
            dl, _ = niche.lang_confident(r.get("description") or "")
            if not r.get("audio_lang") and dl:
                r["lang_source"] = "описание"
                if dl != comp_lang:
                    lang_ok = False
            r["lang"] = r.get("audio_lang") or dl or r["lang"]
        elif comp_script == "cyr" and comp_lang == "ru" and not r.get("audio_lang"):
            uk_letters = len(re.findall(r"[іїєґІЇЄҐ]", r.get("description") or ""))
            if uk_letters >= 5:  # описание на украинском
                lang_ok = False
                r["lang"] = "uk"
        r["relevance"] = round(min(100, 100 * sc / ref)) if ref else 0
        r["match_terms"] = sim.top_terms(cand_docs[r["id"]])
        r["relevant"] = bool(sc >= cut and (lang_ok or not same_lang))
        r["off_reason"] = None if r["relevant"] else ("другой язык" if not lang_ok and same_lang else "другая тематика")
        # вторая проверка тематики: доля роликов по теме, своя главная тема, категория YouTube
        r["topic"] = gate.check(r["titles"])
        if r["relevant"]:
            why = niche.TopicGate.verdict(r["topic"], r.get("category"), niche_cats)
            if why:
                r["_why"] = why
                at_risk.append(r)
        if excl_words:
            hits = Counter(w for w in map(excl, r["titles"][:40]) if w)
            if hits and sum(hits.values()) >= 0.25 * max(1, len(r["titles"][:40])):
                r["relevant"] = False
                r["off_reason"] = f"слово-исключение «{hits.most_common(1)[0][0]}»"
    # кликбейтные названия («Я в ШОКЕ…») тему не выдают: прежде чем отсеять канал по названиям,
    # смотрим описания и теги его роликов и текст на обложках
    at_risk = [r for r in at_risk if r["relevant"]]
    if at_risk:
        progress(97, f"Перепроверяю {len(at_risk)} каналов по описаниям и обложкам…")
        ex = ThreadPoolExecutor(WORKERS)
        fs = [ex.submit(thumb_texts, r) for r in at_risk]
        _wait(fs, timeout=60)
        ex.shutdown(wait=False, cancel_futures=True)
        ocr = [f.result() if f.done() and not f.cancelled() and f.exception() is None else [] for f in fs]
        for r, o in zip(at_risk, ocr):
            extra = [r.get("description") or ""] + list(r.get("channel_tags") or []) + r.get("_probe", []) + o
            hits = gate.evidence(extra)
            if r["topic"] is not None:
                r["topic"]["extra_hits"] = hits
            if not r["_why"].startswith("категория") and len(hits) >= 3:
                r["topic"]["rescued"] = "описания и обложки: " + ", ".join(hits[:5])
                continue  # названия кликбейтные, но по описаниям и обложкам канал из ниши
            r["relevant"] = False
            r["off_reason"] = "другая тематика: " + r["_why"]
    for r in analyzed:
        r.pop("_why", None)
        r.pop("_probe", None)
    analyzed = [r for r in analyzed if r["relevant"]] + [r for r in analyzed if not r["relevant"]]
    keep = [r for r in analyzed if r["relevant"]][:max_channels]
    analyzed = keep + [r for r in analyzed if not r["relevant"]][:max(5, max_channels - len(keep))]
    analyzed.sort(key=lambda r: (not r["relevant"], -r["rising_score"]))
    rel = [r for r in analyzed if r["relevant"]] or analyzed

    # ---- проверка данных перед выдачей
    progress(97, "Проверяю данные: сверяю цифры с YouTube…")
    checks = []
    susp = []
    for r in rel:
        why = verify.suspicious(r)
        if why:
            r["suspicious"] = why
            if r["young"]:
                r["young"] = False
            susp.append({"title": r["title"], "url": r["url"], "why": why})
    young0 = [r for r in rel if r["young"]]
    driving = [r for r in young0 if r["rising_score"] >= verify.RISING][:8]
    driving += [r for r in sorted(rel, key=lambda r: -(r.get("subs") or 0)) if r not in driving][:max(0, 4 - len(driving))]
    cc = verify.crosscheck(driving, lambda v: vinfo(v), workers=min(WORKERS, 4))
    cc_ok = [c for c in cc if c["videos"]]
    broken = set()
    for c in cc_ok:
        r = next(x for x in rel if x["id"] == c["id"])
        if c["wrong_channel"] or c["max_diff"] > 0.5:
            broken.add(c["id"])
            r["young"] = False
            r["verify"] = "цифры не сошлись со страницей видео — канал не влияет на выводы"
        elif c["max_diff"] > 0.15 or (c.get("subs_diff") or 0) > 0.2:
            r["verify"] = "небольшое расхождение с YouTube"
            if (c.get("subs_diff") or 0) > 0.2 and c.get("subs_exact"):
                r["subs"] = c["subs_exact"]  # точное число со страницы видео
        else:
            r["verify"] = "сверено ✓"
    # спрос — по видео каналов ниши, если их достаточно
    rel_ids = {r["id"] for r in rel} | {comp_id} | example_ids
    niche_views = [v for cid, v in view_pairs if cid in rel_ids]
    demand_views = niche_views if len(niche_views) >= 12 else all_views
    rpm, rpm_cat = estimate_rpm(cats, " ".join(kws + tags + prof_titles[:60]))
    S = verify.niche_scores(rel, demand_views, rpm)
    young, young_rising = S["young"], S["young_rising"]
    med_views, med_subs, big_share = S["med_views"], S["med_subs"], S["big_share"]
    demand, competition, window, monet, freshness, overall = (S[k] for k in ("demand", "competition", "window", "monet", "freshness", "overall"))
    rng = verify.bootstrap(rel, demand_views, rpm)

    lang_conf = sum(1 for r in rel if r.get("lang_source"))
    lang_off = sum(1 for r in analyzed if r.get("off_reason") == "другой язык")
    checks.append({"key": "lang", "title": "Язык и страна поиска", "state": "ok",
                   "detail": f"поиск как у зрителей из страны «{niche.REGION_NAMES.get(search_region, search_region)}» на языке «{insights.LANG_NAMES.get(comp_lang, comp_lang)}»"
                             + (f"; язык подтверждён по звуку видео или описанию у {lang_conf} из {len(rel)} каналов" if comp_script == "lat" else "")
                             + (f"; отсеяно {lang_off} каналов на другом языке" if lang_off else "")})
    planned = len(ranked)
    checks.append({"key": "loaded", "title": "Каналы загружены",
                   "state": "ok" if load_failed <= 0.2 * planned else "warn" if load_failed <= 0.4 * planned else "bad",
                   "detail": f"{planned - load_failed} из {planned}" + (f" — {load_failed} YouTube не отдал, оценка по остальным" if load_failed else "")})
    nq = len(jobs)
    q_ok = sum(1 for v in found.values() if v)
    checks.append({"key": "search", "title": "Поиск по YouTube", "state": "ok" if q_ok >= 0.8 * nq else "warn",
                   "detail": f"ответили {q_ok} из {nq} запросов"})
    nrel = len([r for r in analyzed if r["relevant"]])
    topic_off = sum(1 for r in analyzed if (r.get("off_reason") or "").startswith("другая тематика: "))
    checks.append({"key": "relevant", "title": "Каналы точно из ниши",
                   "state": "ok" if nrel >= 12 else "warn" if nrel >= 6 else "bad",
                   "detail": f"{nrel} каналов" + (f", ещё {len(analyzed) - nrel} отсеяно как чужая тема или язык" if len(analyzed) > nrel else "")
                             + (f" (из них {topic_off} — второй проверкой: доля роликов по теме, своя главная тема, категория YouTube)" if topic_off else "")
                             + ("" if nrel >= 12 else " — мало для уверенных выводов")})
    exact = sum(1 for r in rel if r.get("age_exact"))
    by_join = sum(1 for r in rel if r.get("age_source") == "дата регистрации")
    checks.append({"key": "young", "title": "Молодые каналы для выводов",
                   "state": "ok" if len(young) >= 5 else "warn",
                   "detail": f"{len(young)} молодых, из них растут {len(young_rising)}; точный возраст известен у {exact} из {len(rel)} каналов"
                             + (f" ({by_join} — по дате регистрации канала)" if by_join else "")})
    if cc_ok:
        nv = sum(c["videos"] for c in cc_ok)
        worst = max(c["max_diff"] for c in cc_ok)
        checks.append({"key": "crosscheck", "title": "Сверка цифр со страницами видео",
                       "state": "bad" if len(broken) > len(cc_ok) / 2 else "warn" if broken or worst > 0.15 else "ok",
                       "detail": f"проверено {nv} видео у {len(cc_ok)} ключевых каналов, наибольшее расхождение {verify.pct(worst)}"
                                 + (f"; {len(broken)} канал(а) с несовпадающими цифрами исключены из выводов" if broken else "")})
    else:
        checks.append({"key": "crosscheck_na", "title": "Сверка цифр со страницами видео", "state": "warn",
                       "detail": "YouTube не отдал страницы видео — цифры взяты только из списков видео"})
    checks.append({"key": "suspicious", "title": "Накрутка и перезапущенные каналы",
                   "state": "ok" if not susp else "warn",
                   "detail": "не найдено" if not susp else f"{len(susp)} канал(а) не считаются молодыми растущими: " + "; ".join(x["title"] for x in susp[:4])})
    if len(niche_views) >= 12 and all_views:
        a_med, n_med = statistics.median(all_views), statistics.median(niche_views)
        ratio = max(a_med, n_med) / max(1, min(a_med, n_med))
        checks.append({"key": "demand", "title": "Спрос по двум выборкам",
                       "state": "ok" if ratio <= 3 else "warn",
                       "detail": f"весь поиск — {verify.num(a_med)} просмотров на видео, каналы ниши — {verify.num(n_med)}; в оценку взяты каналы ниши"
                                 + ("" if ratio <= 3 else ". Поиск по ключевым словам сильно отличается от ниши — проверьте ключевые слова")})
    else:
        checks.append({"key": "demand", "title": "Спрос по двум выборкам", "state": "warn",
                       "detail": "видео каналов ниши в поиске мало — спрос посчитан по всему поиску"})
    if rng:
        band = lambda x: 0 if x < 40 else 1 if x < 55 else 2 if x < 70 else 3
        names = ["слабая", "средняя", "хорошая", "отличная"]
        lo_b, hi_b = band(rng[0]), band(rng[1])
        width = rng[1] - rng[0]
        if lo_b == hi_b:
            st, tail = "ok", f" — вывод «{names[lo_b]} ниша» не меняется"
        elif hi_b - lo_b == 1 and width <= 25:
            st, tail = "warn", f" — на границе: ниша может оказаться и «{names[lo_b]}», и «{names[hi_b]}»"
        else:
            st, tail = "bad", " — разброс большой, вывод ориентировочный"
        checks.append({"key": "stability", "title": "Устойчивость оценки", "state": st,
                       "detail": f"при другом наборе каналов оценка была бы {rng[0]}–{rng[1]}" + tail})
    verification = {"level": verify.level_of(checks), "range": rng, "checks": checks, "suspicious": susp,
                    "crosscheck": cc}

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
            if same_lang and not niche.same_script(h.get("title") or "", comp_script):
                continue
            entry.append({**h, "channel": r["title"], "channel_url": r["url"], "subs": r["subs"],
                          "x_median": round((h["views"] or 0) / max(r["median_views"], 1), 1)})
    entry.sort(key=lambda x: -(x["views"] or 0))

    # ---- что ищут зрители: подсказки поиска YouTube по каждому ключевому запросу
    # оставляем подсказки, чьи слова встречаются в роликах каналов ниши (отсекаем омонимы вроде «mac miller»)
    nv = Counter(w[:5] for r in rel for t in set(r["titles"]) for w in set(tokenize(t)))
    niche_vocab = {w for w, c in nv.items() if c >= 2} | vocab
    searches = []
    for k in kws:
        own_t = {w[:5] for w in tokenize(k)}
        qs = []
        for x in sugg.get(k, []):
            if x.lower() == k.lower() or not re.match(re.escape(k.lower()) + r"s?\b", x.lower()):
                continue
            extra = {w[:5] for w in tokenize(x)} - own_t
            if not extra or extra & niche_vocab or re.search(r"\d", x):
                qs.append(x)
        if qs:
            searches.append({"keyword": k, "queries": qs[:6]})

    # ---- дополнительная аналитика
    for r in analyzed + [comp]:
        r["income"] = insights.monthly_income(r, rpm)
    durations, best_dur = insights.duration_stats(rel)
    comp_tokens = set()
    for t in prof_titles:
        comp_tokens |= set(tokenize(t))
    titles_info = insights.title_stats(rel, tokenize, comp_tokens)
    langs = Counter(r["lang"] for r in rel)
    trends = [r["trend"] for r in rel if r.get("trend")]
    month_views = [e.get("view_count") or 0 for (kind, k, f), es in found.items() if f == "month" and kind == "kw" for e in es if niche.same_script(e.get("title"), comp_script)]
    month_channels = {e["channel_id"] for (kind, k, f), es in found.items() if f == "month" and kind == "kw" for e in es if niche.same_script(e.get("title"), comp_script)}
    inc_young = [r["income"] for r in young_rising if r.get("income")]
    dynamics = {
        "month_median_views": int(statistics.median(month_views)) if month_views else None,
        "month_videos": len(month_views), "month_channels": len(month_channels),
        "accelerating": sum(1 for t in trends if t >= 1.3), "slowing": sum(1 for t in trends if t <= 0.75),
        "trend_known": len(trends),
        "languages": [{"code": c, "name": insights.LANG_NAMES.get(c, c), "share": round(100 * n / len(rel))}
                      for c, n in langs.most_common(6)],
        "comp_lang": insights.LANG_NAMES.get(comp.get("lang"), comp.get("lang")),
        "region": search_region, "region_name": niche.REGION_NAMES.get(search_region, search_region),
        "countries": [{"name": c, "share": round(100 * k / len(rel))} for c, k in Counter(r.get("country") for r in rel if r.get("country")).most_common(5)],
        "upw_young": round(statistics.median([r["uploads_per_week"] for r in young_rising if r.get("uploads_per_week")]), 1)
                     if any(r.get("uploads_per_week") for r in young_rising) else None,
        "off_topic": len(analyzed) - len([r for r in analyzed if r["relevant"]]),
    }
    eng = [v for v in top_info if v.get("view_count")]
    if eng:
        likes = [v["like_count"] / v["view_count"] for v in eng if v.get("like_count")]
        comms = [v["comment_count"] / v["view_count"] for v in eng if v.get("comment_count")]
        comp["engagement"] = {"likes_pct": round(100 * statistics.median(likes), 2) if likes else None,
                              "comments_per_1k": round(1000 * statistics.median(comms), 1) if comms else None}

    # ---- кандидаты в соседние ниши: темы, которые общие у нескольких каналов этой ниши и соседних с ней
    # (почти подошедших), но не совпадают с ключевыми фразами самой ниши. Оцениваются отдельно, по кнопке/фоном.
    # ---- идеи роликов: темы, которые недавно выстрелили у каналов ниши (с доказательствами и проверкой спроса)
    video_ideas = None
    try:
        import ideas
        video_ideas = ideas.build([r for r in analyzed if r["relevant"]], comp, kws, ylang, comp_script, med_views, progress)
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(e.__traceback__)[-3:]
        video_ideas = {"ideas": [], "error": str(e)[:200], "where": " < ".join(f"{f.name}:{f.lineno}" for f in reversed(tb))}

    neighbor_cands = []
    try:
        import neighbors
        near = [r for r in analyzed if not r["relevant"] and r.get("relevance", 0) >= 15 and r.get("off_reason") != "другой язык"]
        excl_f = excl if excl_words else (lambda t: None)
        neighbor_cands = neighbors.candidates([r for r in analyzed if r["relevant"]], near, kws, comp_script, excl_f)
    except Exception:
        neighbor_cands = []

    for r in analyzed + [comp] + examples:
        r.pop("_ents", None)
        r.pop("titles", None)
        r.pop("top_info", None)

    progress(100, "Готово")
    result = {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "competitor": comp,
        "keywords": kws,
        "hidden_channels": hidden,
        "neighbor_candidates": neighbor_cands,
        "video_ideas": video_ideas,
        "niche": {
            "overall": round(overall), "verdict": verdict,
            "scores": {"demand": round(demand), "competition": round(competition),
                       "entry_window": round(window), "monetization": round(monet),
                       "freshness": round(freshness)},
            "median_search_views": int(med_views), "median_subs": int(med_subs),
            "big_channels_share": round(big_share * 100),
            "rpm_range": rpm, "rpm_category": rpm_cat,
            "analyzed": len(analyzed), "relevant": len(rel), "young": len(young), "young_rising": len(young_rising),
            "max_age_days": max_age_days, "best_duration": best_dur,
            "income_young": [round(statistics.median(x[0] for x in inc_young)), round(statistics.median(x[1] for x in inc_young))] if inc_young else None,
        },
        "verification": verification,
        "keyword_table": kw_table,
        "channels": analyzed,
        "entry_topics": entry[:20],
        "durations": durations,
        "titles": titles_info,
        "dynamics": dynamics,
        "searches": searches,
        "examples": [{k: x.get(k) for k in ("id", "title", "url", "avatar", "subs", "median_views")} for x in examples],
        "exclude_words": excl_words,
    }
    result["formats"] = insights.format_stats(rel, young, rpm)
    result["forecast"] = insights.forecast(young, young_rising)
    result["gallery"] = insights.gallery(rel, title_ok=(lambda t: niche.same_script(t, comp_script)) if same_lang else (lambda t: True))
    result["plan"] = insights.entry_plan(result)
    return result
