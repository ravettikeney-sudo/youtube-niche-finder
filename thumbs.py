"""
Превью: что заходит у вас и у конкурентов — по реальным картинкам и реакции зрителей.

Успех превью = просмотры ролика относительно соседних роликов того же канала
(медиана ±5 видео вокруг). Так не мешают ни размер канала, ни его рост или спад.
Картинку разбираем сами: лица (YuNet), текст (Tesseract), яркость, цвета, детали.
Из закономерностей собираем варианты превью, промты и названия — без внешних нейросетей.
"""
import os
import re
import math
import threading
import statistics
import datetime as dt
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import cv2

import analyzer
import insights
import niche

ROOT = Path(__file__).parent
FACE_MODEL = ROOT / "models" / "face_yunet.onnx"
OWN_LIMIT = int(os.environ.get("THUMBS_OWN", "60"))
COMP_LIMIT = int(os.environ.get("THUMBS_COMP", "45"))
HIT = 1.5     # хит: в 1,5 раза больше соседних роликов
FLOP = 0.67   # слабое: на треть меньше соседних

try:
    import pytesseract
    pytesseract.get_tesseract_version()
    OCR_LANGS = "+".join(l for l in ("rus", "eng") if l in pytesseract.get_languages(config="")) or "eng"
except Exception:
    pytesseract = None
    OCR_LANGS = None


# ---------------------------------------------------------------- загрузка
def _get(url, timeout=10):
    px = os.environ.get("YT_PROXY")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return op.open(req, timeout=timeout).read()


def load_img(vid):
    for name in ("mqdefault", "hqdefault"):
        try:
            data = _get(f"https://i.ytimg.com/vi/{vid}/{name}.jpg")
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            if name == "hqdefault":  # 4:3 с чёрными полосами → вырезаем 16:9
                h, w = img.shape[:2]
                ch = int(w * 9 / 16)
                img = img[(h - ch) // 2:(h - ch) // 2 + ch]
            return cv2.resize(img, (320, 180), interpolation=cv2.INTER_AREA)
        except Exception:
            continue
    return None


def channel_videos(url, limit, prog=None):
    """Длинные ролики канала с просмотрами и оригинальными названиями."""
    base = analyzer.resolve_channel_base(url)
    info = analyzer._extract(base + "/videos", flat=True, end=limit)
    if not info:  # у совсем нового канала вкладки «Видео» может не быть
        info = analyzer._extract(base, flat=True, end=1)
        if info:
            info = dict(info, entries=[])
    if not info:
        raise ValueError(f"Не удалось открыть канал {url}. Проверьте ссылку.")
    ents = [e for e in (info.get("entries") or []) if e and e.get("id") and e.get("view_count") is not None]
    desc = info.get("description") or ""
    titles_en = [e.get("title") or "" for e in ents]
    # язык канала: описание YouTube не переводит, названия — переводит
    lang = insights.detect_lang([desc] * 3 + titles_en) if (desc or titles_en) else "en"
    dl, _ = niche.lang_confident(desc)
    if dl:
        lang = dl
    if niche.script(desc) == "cyr" or (not desc and sum(niche.script(t) == "cyr" for t in titles_en) > len(titles_en) / 3):
        lang = "uk" if len(re.findall(r"[іїєґ]", desc.lower())) >= 5 else "ru"
    if lang != "en":
        loc = analyzer._extract(base + "/videos", flat=True, end=limit, lang=niche.ylang_for(lang)) or {}
        orig = {e.get("id"): e.get("title") for e in (loc.get("entries") or []) if e and e.get("title")}
        ents = [dict(e, title=orig.get(e["id"], e.get("title"))) for e in ents]
    thumbs = {t.get("id"): t.get("url") for t in (info.get("thumbnails") or [])}
    av = thumbs.get("avatar_uncropped")
    ch = {"title": info.get("channel") or info.get("uploader") or re.sub(r"\s-\s(Videos|Видео).*$", "", info.get("title") or ""),
          "url": base, "id": info.get("channel_id") or info.get("id"), "subs": info.get("channel_follower_count"), "lang": lang,
          "description": desc[:2000], "tags": list(info.get("tags") or [])[:40],
          "avatar": (av.split("=")[0] + "=s176-c-k-c0x00ffffff-no-rj") if av else None}
    today = dt.date.today()
    vids = []
    for e in ents:
        d = analyzer._entry_date(e)
        age = (today - d).days if d else None
        vids.append({"id": e["id"], "title": e.get("title") or "", "views": e["view_count"], "age": age,
                     "duration": e.get("duration")})
    return ch, vids


def add_perf(vids):
    """Успех ролика относительно соседей по времени выхода (не считая самых свежих — они ещё набирают)."""
    ok = [v for v in vids if v["age"] is None or v["age"] >= 7]
    for i, v in enumerate(ok):
        nb = [x["views"] for x in ok[max(0, i - 5):i] + ok[i + 1:i + 6] if x["views"]]
        med = statistics.median(nb) if nb else None
        v["perf"] = round(v["views"] / med, 2) if med else None
    return [v for v in ok if v.get("perf")]


# ---------------------------------------------------------------- признаки картинки
_tl = threading.local()  # детектор лиц не потокобезопасен — у каждого потока свой


def _faces(img):
    det = getattr(_tl, "det", None)
    if det is None:
        det = _tl.det = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (320, 180), 0.6, 0.3, 50)
    det.setInputSize((img.shape[1], img.shape[0]))
    _, fs = det.detect(img)
    return [] if fs is None else [f for f in fs if f[2] * f[3] >= 0.004 * img.shape[0] * img.shape[1]]


COLOR_NAMES = [  # (название, оттенок 0–360 от, до)
    ("красный", 345, 360), ("красный", 0, 12), ("оранжевый", 12, 40), ("жёлтый", 40, 70), ("зелёный", 70, 165),
    ("голубой", 165, 200), ("синий", 200, 255), ("фиолетовый", 255, 305), ("розовый", 305, 345)]
COLOR_EN = {"красный": "red", "оранжевый": "orange", "жёлтый": "yellow", "зелёный": "green", "голубой": "cyan",
            "синий": "blue", "фиолетовый": "purple", "розовый": "pink", "чёрный": "black", "белый": "white", "серый": "grey"}


def color_name(bgr):
    hsv = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = int(hsv[0]) * 2, int(hsv[1]) / 255, int(hsv[2]) / 255
    if v < 0.2:
        return "чёрный"
    if s < 0.18:
        return "белый" if v > 0.8 else "серый"
    for n, a, b in COLOR_NAMES:
        if a <= h < b:
            return n
    return "красный"


def _ocr(img):
    if not pytesseract:
        return []
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    try:
        d = pytesseract.image_to_data(cv2.cvtColor(big, cv2.COLOR_BGR2RGB), lang=OCR_LANGS, config="--psm 11",
                                      output_type=pytesseract.Output.DICT, timeout=8)
    except Exception:
        return []
    out = []
    for w, c, bw, bh in zip(d["text"], d["conf"], d["width"], d["height"]):
        w = re.sub(r"^[^\wА-Яа-яЁё]+|[^\wА-Яа-яЁё!?%₽$]+$", "", (w or "").strip())
        if float(c) < 60 or len(w) < 2 or bh < 14:  # мелкие надписи (логотипы, плашки) не считаем
            continue
        letters = sum(ch.isalnum() for ch in w)
        if letters < 0.7 * len(w) or (len(w) < 3 and not w.isdigit() and w.upper() != w):
            continue
        if re.search(r"[A-Za-z]", w) and re.search(r"[А-Яа-яЁё]", w):  # «iPHONE» ок, «АСПА0B» — мусор
            continue
        out.append({"w": w, "area": bw * bh / 4})
    return out


def features(img):
    h, w = img.shape[:2]
    area = h * w
    fs = _faces(img)
    big = max(fs, key=lambda f: f[2] * f[3]) if len(fs) else None
    words = _ocr(img)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    b, g, r = [img[:, :, i].astype(np.float32) for i in range(3)]
    rg, yb = r - g, 0.5 * (r + g) - b
    colorful = math.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * math.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    hue, sat, val = hsv[:, :, 0] * 2, hsv[:, :, 1] / 255, hsv[:, :, 2] / 255
    vivid = (sat > 0.35) & (val > 0.3)
    warm = float((vivid & ((hue < 70) | (hue >= 330))).mean())
    small = cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA).reshape(-1, 3).astype(np.float32)
    _, lab, cen = cv2.kmeans(small, 4, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3,
                             cv2.KMEANS_PP_CENTERS)
    cnt = Counter(lab.flatten().tolist())
    pal = []
    for k, n in cnt.most_common(4):
        c = cen[k].astype(int).tolist()
        pal.append({"hex": "#%02x%02x%02x" % (c[2], c[1], c[0]), "name": color_name(c), "share": round(n / len(lab), 2)})
    edges = cv2.Canny(gray, 80, 180)
    text = " ".join(x["w"] for x in words)
    return {
        "faces": len(fs),
        "face_pct": round(float(100 * big[2] * big[3] / area), 1) if big is not None else 0,
        "face_x": None if big is None else ("left" if float(big[0] + big[2] / 2) < w * 0.4 else "right" if float(big[0] + big[2] / 2) > w * 0.6 else "center"),
        "words": len(words), "text": text[:80],
        "text_pct": round(float(100 * sum(x["area"] for x in words) / area), 1),
        "bright": round(float(val.mean()) * 100), "sat": round(float(sat.mean()) * 100),
        "contrast": round(float(gray.std()) / 1.28), "colorful": round(colorful),
        "warm": round(100 * warm), "detail": round(100 * float((edges > 0).mean()), 1),
        "palette": pal,
    }


# ---------------------------------------------------------------- признаки для сравнения
def flags(f, med):
    """Признаки «да/нет». Яркость, насыщенность и т.п. — относительно медианы всех превью этого отчёта."""
    colors = {p["name"] for p in f["palette"] if p["share"] >= 0.12}
    return {
        "face": f["faces"] >= 1,
        "big_face": f["face_pct"] >= 6,
        "two_faces": f["faces"] >= 2,
        "text": f["words"] >= 1,
        "short_text": 1 <= f["words"] <= 4,
        "long_text": f["words"] >= 5,
        "big_text": f["text_pct"] >= 8,
        "bright": f["bright"] > med["bright"],
        "dark": f["bright"] < 35,
        "saturated": f["sat"] > med["sat"],
        "contrast": f["contrast"] > med["contrast"],
        "warm": f["warm"] >= 25,
        "red": "красный" in colors,
        "yellow": "жёлтый" in colors or "оранжевый" in colors,
        "blue": "синий" in colors or "голубой" in colors,
        "simple": f["detail"] < med["detail"],
    }


FLAG_NAMES = {
    "face": ("Лицо в кадре", "лицо в кадре"),
    "big_face": ("Крупное лицо (от 6% кадра)", "крупное лицо"),
    "two_faces": ("Два человека и больше", "двое в кадре"),
    "text": ("Текст на превью", "текст на превью"),
    "short_text": ("Короткий текст (1–4 слова)", "1–4 слова текста"),
    "long_text": ("Много текста (5+ слов)", "много текста"),
    "big_text": ("Крупный текст", "крупный текст"),
    "bright": ("Светлее обычного", "светлая картинка"),
    "dark": ("Тёмный фон", "тёмный фон"),
    "saturated": ("Насыщенные цвета", "насыщенные цвета"),
    "contrast": ("Высокий контраст", "высокий контраст"),
    "warm": ("Тёплые цвета (красный, оранжевый, жёлтый)", "тёплые цвета"),
    "red": ("Красный цвет", "красный"),
    "yellow": ("Жёлтый или оранжевый", "жёлтый/оранжевый"),
    "blue": ("Синий или голубой", "синий"),
    "simple": ("Мало деталей, чистый фон", "чистый фон"),
}

HOOKS = [  # приёмы в названиях: ключ, название, регулярка
    ("pain", "Боль и проблема", r"(ошиб|проблем|не работа|слома|разор|потер|бедн|долг|кредит|устал|страх|боюсь|опасн|вред|обман|развод|кинул|сгор|убива|ненавиж|хуже|(?<!не)плохо|беда|катастроф|кризис|рушит|провал|mistake|problem|broke|lost|scam|danger|fail|worst|hate)"),
    ("money", "Деньги и выгода", r"(\d\s?(₽|руб|\$|тыс|млн|k\b)|деньг|заработ|доход|дешев|дорог|бесплат|эконом|выгод|цен[аы]|стоит|рубл|money|cheap|free|price|\$\d)"),
    ("result", "Результат и превращение", r"(за \d+ (дн|дней|час|минут|недел|месяц|год)|до и после|итог|результат|получилось|сделал|стал |теперь|in \d+ days|before and after|result|i made|i built)"),
    ("curiosity", "Интрига и секрет", r"(секрет|правда|на самом деле|оказалось|никто не|вот что|вот почему|неожидан|шок|тайн|скрыва|что будет|что внутри|secret|truth|nobody|hidden|what happens|inside|shocking)"),
    ("warning", "Запрет и предупреждение", r"(не покупа|не делай|не верь|нельзя|никогда|хватит|перестань|остановись|осторожн|не стоит|don'?t|never|stop|avoid|warning)"),
    ("compare", "Сравнение", r"(\bvs\.?\b|против|или\b|лучше|сравн|versus|\bor\b|better than)"),
    ("number", "Число или список", r"(^|\s)\d+\s"),
    ("question", "Вопрос", r"\?"),
    ("personal", "Личный опыт", r"(^|\s)(я|мой|моя|мои|мы|купил|попробовал|проверил|i|my|we|i tried|i bought)(\s|$)"),
    ("new", "Новизна и срочность", r"(нов[ыоа]|впервые|срочно|сейчас|уже|20\d\d|new|first|now|today|just)"),
    ("caps", "СЛОВО заглавными", r"\b[A-ZА-ЯЁ]{4,}\b"),
]
HOOK_RE = [(k, n, re.compile(rx, re.I if k != "caps" else 0)) for k, n, rx in HOOKS]


def hooks_of(title):
    return {k for k, _, rx in HOOK_RE if rx.search(title or "")}


def _stems(t):
    return {niche.stem(w) for w in analyzer.tokenize(t)}


# ---------------------------------------------------------------- статистика
def _perm_p(a, b, n=1500, seed=7):
    """Насколько вероятно, что разница случайна (перестановочный тест по log-успеху)."""
    a, b = np.log(np.array(a)), np.log(np.array(b))
    obs = abs(a.mean() - b.mean())
    allv = np.concatenate([a, b])
    rng = np.random.default_rng(seed)
    idx = np.array([rng.permutation(len(allv)) for _ in range(n)])
    pa = allv[idx[:, :len(a)]].mean(1)
    pb = allv[idx[:, len(a):]].mean(1)
    return float((np.abs(pa - pb) >= obs - 1e-12).mean())


def conf_label(p):
    return "высокая" if p < 0.05 else "средняя" if p < 0.15 else "низкая"


def effects(items, keyfn, names):
    """Для каждого признака: медиана успеха с ним и без, доля среди хитов, надёжность."""
    out = []
    keys = list(names)
    for k in keys:
        yes = [v["perf"] for v in items if k in keyfn(v)]
        no = [v["perf"] for v in items if k not in keyfn(v)]
        if len(yes) < 3 or len(no) < 3:
            continue
        hits = [v for v in items if v["perf"] >= HIT]
        my, mn = statistics.median(yes), statistics.median(no)
        p = _perm_p(yes, no)
        out.append({"key": k, "name": names[k][0] if isinstance(names[k], tuple) else names[k],
                    "with": round(my, 2), "without": round(mn, 2), "ratio": round(my / mn, 2) if mn else None,
                    "n_with": len(yes), "n_without": len(no),
                    "share_all": round(100 * len(yes) / len(items)),
                    "share_hits": round(100 * sum(k in keyfn(v) for v in hits) / len(hits)) if hits else None,
                    "p": round(p, 3), "confidence": conf_label(p)})
    out.sort(key=lambda e: (e["confidence"] == "низкая", -abs(math.log(e["ratio"] or 1))))
    return out


STYLES = {
    "face_text": ("Лицо + текст", "крупное лицо с эмоцией и 1–4 слова крупным шрифтом"),
    "face_only": ("Лицо без текста", "человек и предмет, история понятна без слов"),
    "text_only": ("Текст без лица", "крупная надпись на картинке или предмете, без человека"),
    "object": ("Предмет или сцена", "одна сильная картинка без людей и надписей"),
}


def style_of(f):
    if f["faces"]:
        return "face_text" if f["words"] else "face_only"
    return "text_only" if f["words"] else "object"


def style_table(items):
    rows = []
    for k, (name, desc) in STYLES.items():
        xs = [v["perf"] for v in items if v["style"] == k]
        if not xs:
            continue
        rows.append({"key": k, "name": name, "desc": desc, "n": len(xs), "share": round(100 * len(xs) / len(items)),
                     "median": round(statistics.median(xs), 2),
                     "hit_rate": round(100 * sum(x >= HIT for x in xs) / len(xs))})
    rows.sort(key=lambda r: -r["median"])
    return rows


def recipe(items):
    """Как выглядят хиты: типичный размер лица, число слов, яркость, цвета."""
    hits = [v for v in items if v["perf"] >= HIT] or sorted(items, key=lambda v: -v["perf"])[:max(3, len(items) // 4)]
    if not hits:
        return None
    fx = Counter(v["f"]["face_x"] for v in hits if v["f"]["face_x"])
    cols = Counter()
    for v in hits:
        for p in v["f"]["palette"]:
            if p["share"] >= 0.12 and p["name"] not in ("серый",):
                cols[p["name"]] += 1
    sw = [p for v in hits for p in v["f"]["palette"][:2]]
    med = lambda k: statistics.median(v["f"][k] for v in hits)
    words = [v["f"]["words"] for v in hits if v["f"]["words"]]
    return {"n": len(hits), "face_share": round(100 * sum(v["f"]["faces"] > 0 for v in hits) / len(hits)),
            "face_pct": round(statistics.median([v["f"]["face_pct"] for v in hits if v["f"]["faces"]] or [0]), 1),
            "face_side": fx.most_common(1)[0][0] if fx else None,
            "text_share": round(100 * len(words) / len(hits)),
            "words": round(statistics.median(words)) if words else 0,
            "bright": round(med("bright")), "sat": round(med("sat")), "contrast": round(med("contrast")),
            "colors": [c for c, _ in cols.most_common(3)],
            "swatches": [p["hex"] for p in sorted(sw, key=lambda p: -p["share"])][:6]}


def pairing(items):
    """Связка «название + текст на превью»: дополняет или повторяет."""
    rows = [v for v in items if v["f"]["words"]]
    if len(rows) < 6:
        return None
    for v in rows:
        a, b = _stems(v["f"]["text"]), _stems(v["title"])
        v["_rep"] = bool(a) and len(a & b) / len(a) >= 0.5
    rep = [v["perf"] for v in rows if v["_rep"]]
    add = [v["perf"] for v in rows if not v["_rep"]]
    if len(rep) < 3 or len(add) < 3:
        return {"repeat_share": round(100 * len(rep) / len(rows)), "verdict": None}
    mr, ma = statistics.median(rep), statistics.median(add)
    p = _perm_p(rep, add)
    return {"repeat_share": round(100 * len(rep) / len(rows)), "repeat": round(mr, 2), "complement": round(ma, 2),
            "confidence": conf_label(p),
            "verdict": "complement" if ma > mr * 1.1 else "repeat" if mr > ma * 1.1 else "same"}


def thumb_card(v):
    f = v["f"]
    tags = []
    if f["faces"]:
        tags.append("крупное лицо" if f["face_pct"] >= 6 else "лицо" if f["faces"] == 1 else f"{f['faces']} человека")
    if f["words"]:
        tags.append(f"{f['words']} {_pl(f['words'], 'слово', 'слова', 'слов')} текста")
    else:
        tags.append("без текста")
    tags.append(" + ".join(p["name"] for p in f["palette"][:2]))
    return {"id": v["id"], "title": v["title"], "views": v["views"], "perf": v["perf"], "text": f["text"],
            "tags": tags, "hooks": [n for k, n, _ in HOOK_RE if k in v["hooks"]][:3], "channel": v.get("ch")}


def _pl(n, a, b, c):
    n = abs(n) % 100
    d = n % 10
    return c if 10 < n < 20 else b if 1 < d < 5 else a if d == 1 else c


def analyze_group(items):
    if not items:
        return None
    med = {k: statistics.median(v["f"][k] for v in items) for k in ("bright", "sat", "contrast", "detail")}
    for v in items:
        v["flags"] = {k for k, on in flags(v["f"], med).items() if on}
        v["style"] = style_of(v["f"])
        v["hooks"] = hooks_of(v["title"])
    by = sorted(items, key=lambda v: -v["perf"])
    hit_titles = [v["title"] for v in items if v["perf"] >= HIT]
    return {
        "n": len(items), "hits": sum(v["perf"] >= HIT for v in items), "flops": sum(v["perf"] <= FLOP for v in items),
        "visual": effects(items, lambda v: v["flags"], FLAG_NAMES),
        "hooks": effects(items, lambda v: v["hooks"], {k: n for k, n, _ in HOOK_RE}),
        "styles": style_table(items), "recipe": recipe(items), "pairing": pairing(items),
        "top": [thumb_card(v) for v in by[:8]],
        "weak": [thumb_card(v) for v in by[::-1][:4] if v["perf"] <= FLOP],
        "title_len": round(statistics.median(len(t) for t in hit_titles)) if hit_titles else None,
    }


# ---------------------------------------------------------------- сценарий
PAIN_RX = re.compile(r"(ошиб|проблем|не работа|не получа|слома|ломает|теря|потер|бедн|долг|устал|страх|боит|боятся|опасн|вред|обман|сложн|не хватает|мешает|бесит|раздража|болит|боль\b|сливают|провал|не знают|не понимают|жалуют|садится|сажает|тормоз|греется|лага|глюч|умира|сгора|переплач|дорого|отвалива|трескает|треска|протека|плесен|ржаве|гниет|гниёт|портит|ломается|неправильн|зря|обидно|полдня|часами|долго|mistake|problem|struggl|lose|waste|afraid|drain|broken|slow)", re.I)
GAIN_RX = re.compile(r"(получит|узнает|узнаете|сможет|сможете|научит|поможет|покажу|расскажу|разбер|сэконом|заработ|результат|быстрее|проще|легко|бесплат|выгод|секрет|способ|лайфхак|продлева|увелич|ускор|улучш|will learn|you can|i'll show|save|earn|faster|easier|boost|result)", re.I)
NUM_RX = re.compile(r"(?<![\w.])\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?\s?(?:%|₽|\$|[a-zа-яё]{3,})", re.I)
ENTITY_RX = re.compile(r"\b(?:[A-Z][\w+]*|[a-z]+[A-Z][\w+]*)(?:\s+(?:[A-Z][\w+]*|\d+[\w+]*|Pro|Max|Ultra|Plus|Mini|Air|Lite))*")
NUM_SKIP = re.compile(r"^(года?|лет|году|из|до|от|на|по|the|and|for|with|сек)$", re.I)


def _is_pain(text):
    """Есть боль, и это не отрицание («свёкла не теряет цвет» — это решение, а не проблема)."""
    for m in PAIN_RX.finditer(text):
        if m.group(0).lower().startswith("не ") or not re.search(r"(?:^|\s)(не|not|don't|doesn't|never|без)\s+$", text[:m.start()].lower()[-9:]):
            return True
    return False
LEAD = re.compile(r"^(и|но|а|ведь|ну|так|вот|и вот|потому что|что|чтобы|которые|который|которая|как|если|когда|это|и это|ведь это|because|and|but|so|that|which|if|when)\s+", re.I)
GENERIC = re.compile(r"^(это|и это|that|this|it)\b.*(проблем|беда|ужас|problem)", re.I)
FUNC = {"к", "в", "на", "с", "у", "о", "по", "за", "из", "до", "от", "и", "а", "но", "не", "без", "для", "при", "про",
        "что", "как", "то", "же", "ли", "чтобы", "потому", "если", "когда", "который", "которые", "это", "один", "одна", "два", "три",
        "the", "a", "an", "to", "of", "in", "on", "and", "for", "with", "because", "they", "that", "this", "is", "are",
        "i'll", "you", "your", "how", "if", "when", "at", "by", "from", "or", "but"}
SHOW_LEAD = re.compile(r"^(?:(?:в этом (?:видео|ролике)|сегодня|в конце|дальше|ещё|также)\s+)?(?:я\s+)?(?:покажу|расскажу|объясню|разберу|узнаете|узнаешь)(?:\s+вам)?,?\s*(?:как|что|почему)?\s+"
                       r"|^(?:in this video,?\s+)?(?:i'll|i will|i'm going to|we'll)\s+(?:show|teach|tell)\s+you\s+(?:how\s+to\s+|why\s+|what\s+)?"
                       r"|^you(?:'ll| will)\s+learn\s+(?:how\s+to\s+)?", re.I)
TIME_RX = re.compile(r"(час|мин|дн|дней|недел|месяц|год|лет|hour|minute|day|week|month|year)", re.I)
CUR_RX = re.compile(r"(₽|руб|\$|тыс|млн|долл|евро|dollar|usd|€)", re.I)


def _sentences(text):
    return [x.strip() for x in re.split(r"(?<=[.!?…])\s+|\n+", text or "") if 12 <= len(x.strip()) <= 300]


def _clean(c):
    c = c.strip(" .,!?—-–:;«»\"")
    for _ in range(2):
        c = LEAD.sub("", c)
    return c


REL = re.compile(r"^(которые|который|которая|которое|что|чтобы|which|that)\s", re.I)


def _raw_clauses(sent):
    return [x.strip(" .,!?—-–:;«»\"") for x in re.split(r"[,;:()]|\s[—–-]\s", sent) if x.strip(" .,!?—-–:;«»\"")]


def _tail(c, n=2):
    w = [x for x in c.split()]
    return " ".join(w[-n:])


def _phrase(sent, rx, maxw=10):
    """Содержательная часть предложения вокруг найденного слова — дословно, чтобы не ломать падежи."""
    cl = _raw_clauses(sent)
    for i, raw in enumerate(cl):
        if not rx.search(raw):
            continue
        c = _clean(raw)
        nxt = cl[i + 1] if i + 1 < len(cl) else ""
        if re.match(r"^(что|that)\s", raw, re.I) and len(c.split()) >= 3:  # «многие думают, что борщ варится полдня»
            pass
        elif REL.match(raw) and i > 0:            # «три настройки, которые сливают заряд»
            c = _tail(cl[i - 1]) + ", " + raw
        elif re.match(r"^(что|that)\s", nxt, re.I) and len(raw.split()) <= 3:  # «многие жалуются, что батарея садится»
            c = _clean(nxt)
        elif REL.match(nxt):                    # «5 настроек, которые продлевают жизнь батареи»
            c = _tail(raw) + ", " + nxt
        elif GENERIC.search(c) or len(c.split()) < 3:
            nb = [_clean(x) for x in cl[:i][::-1] + cl[i + 1:] if len(_clean(x).split()) >= 3 and not GENERIC.search(_clean(x))]
            if not nb:
                continue
            c = nb[0]
        w = c.split()
        return " ".join(w[:maxw]).rstrip(",;:—-")
    return None


def _entities(text):
    c = Counter()
    for m in ENTITY_RX.finditer(text or ""):
        e = m.group(0).strip()
        before = (text or "")[:m.start()].rstrip()
        single = len(e.split()) == 1 and not re.search(r"[0-9]|[a-z][A-Z]|^[A-Z]{2,}", e)
        if single and (not before or before[-1] in ".!?…\n:"):
            continue  # «Most», «The» в начале предложения
        if len(e) >= 3 and not re.fullmatch(r"(I|I'm|I'll|You|We|They|The|This|That|In|My|It)", e) and re.search(r"[A-Za-z]", e):
            c[e] += 1
    # «iPhone» и «iPhone 18 Pro»: длинное название включает короткое
    out = []
    for e, n in sorted(c.items(), key=lambda x: (-x[1] * (1 + 0.3 * len(x[0].split())), -len(x[0]))):
        if not any(e in o or o in e for o in out):
            out.append(e)
    return out


TERM_SKIP = {"привет", "сегодня", "спойлер", "также", "конце", "многие", "использования", "видео", "ролик", "ролике",
             "канал", "канале", "подписывайтесь", "лайк", "друзья", "всем", "очень", "просто", "можно", "нужно"}


def parse_script(text, topic=None):
    text = text or ""
    sents = _sentences(text)
    toks = analyzer.tokenize(text)
    stem_c = Counter(niche.stem(w) for w in toks if len(w) >= 4)
    surf = {}
    for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text):
        if len(w) >= 4 and w.lower() not in analyzer.STOP:
            surf.setdefault(niche.stem(w.lower()), Counter())[w] += 1
    ents = _entities(text)
    ent_stems = {niche.stem(x.lower()) for e in ents for x in e.split()}
    terms = [surf[st].most_common(1)[0][0] for st, _ in stem_c.most_common(12) if st in surf and st not in ent_stems
             and surf[st].most_common(1)[0][0].lower() not in TERM_SKIP
             and not re.search(r"(ем|ете|ют|ишь|ешь|ает|ают|или|ила|ило|ться|ется)$", surf[st].most_common(1)[0][0].lower())][:6]
    pains, gains = [], []
    for x in sents:
        if _is_pain(x):
            ph = _phrase(x, PAIN_RX)
            if ph and _is_pain(ph) and not GAIN_RX.search(ph) and ph.lower() not in (p.lower() for p in pains):
                pains.append(ph)
        if GAIN_RX.search(x):
            ph = _phrase(SHOW_LEAD.sub("", x.strip()), GAIN_RX) if SHOW_LEAD.search(x.strip()) else _phrase(x, GAIN_RX)
            if ph and ph.lower() not in (g.lower() for g in gains):
                gains.append(ph)
    nums = []
    for m in NUM_RX.finditer(text):
        x = re.sub(r"\s+", " ", m.group(0)).strip()
        unit = re.sub(r"^[\d\s.,]+", "", x)
        if NUM_SKIP.match(unit) or unit.lower() in FUNC or x in nums or re.fullmatch(r"\d+\s?[A-Za-z]+", x) and any(x in e for e in ents):
            continue
        nums.append(x)
    vs = None
    m = re.search(r"(.{2,60}?)\s+(?:vs\.?|против|или|versus)\s+(.{2,60})", text, re.I)
    if m:
        left = [e for e in ents if e in m.group(1)]
        right = [e for e in ents if e in m.group(2)]
        if left and right:
            vs = [max(left, key=lambda e: m.group(1).rfind(e)), min(right, key=lambda e: m.group(2).find(e))]
        else:
            lw = [w for w in m.group(1).split() if w.lower() not in FUNC]
            rw = [w for w in m.group(2).split() if w.lower() not in FUNC]
            if lw and rw:
                vs = [lw[-1].strip(".,"), rw[0].strip(".,")]
    t = (topic or "").strip() or (ents[0] if ents else (terms[0][:1].upper() + terms[0][1:]) if terms else "")
    return {"topic": t, "entities": ents[:4], "terms": terms, "pains": pains[:5], "gains": gains[:5],
            "numbers": nums[:5], "versus": vs, "sentences": len(sents)}


# ---------------------------------------------------------------- варианты превью и названия
ANGLES = {
    "pain": {"name": "Боль", "hook": "pain", "why": "зритель узнаёт свою проблему и кликает, чтобы её решить",
             "emo": ("досада и напряжение, брови сведены", "frustrated, worried expression, furrowed brows")},
    "result": {"name": "Результат", "hook": "result", "why": "показываете итог, который хочет получить зритель",
               "emo": ("радость и удивление, широко открытые глаза", "excited, amazed, wide eyes, big smile")},
    "curiosity": {"name": "Интрига", "hook": "curiosity", "why": "недосказанность: ответ можно узнать только в ролике",
                  "emo": ("заинтригован, прищур, лёгкая улыбка", "intrigued, one raised eyebrow, slight smirk")},
    "warning": {"name": "Предупреждение", "hook": "warning", "why": "страх ошибиться сильнее желания выиграть",
                "emo": ("шок, рука у лица", "shocked, hand on face, mouth open")},
    "compare": {"name": "Сравнение", "hook": "compare", "why": "зритель хочет узнать, что выбрать",
                "emo": ("задумчиво смотрит то на один, то на другой предмет", "thoughtful, comparing two items in hands")},
    "money": {"name": "Деньги", "hook": "money", "why": "конкретная сумма цепляет сильнее общих слов",
              "emo": ("уверенная улыбка, держит деньги или предмет", "confident smile, holding cash or the product")},
}


def _cap(s):
    """Заглавная первая буква, но «iPhone» остаётся «iPhone»."""
    if not s or re.match(r"[a-z]+[A-Z]", s):
        return s
    return s[:1].upper() + s[1:]


def _low(s):
    """Строчная первая буква для середины фразы, кроме названий («Samsung», «iPhone»)."""
    if not s or re.match(r"([A-Z][a-z]*[A-Z0-9]|[a-z]+[A-Z])", s) or (s.split()[0][:1].isupper() and re.match(r"[A-Za-z]", s)):
        return s
    return s[:1].lower() + s[1:]


def _thumb_texts(angle, s, nwords, lang):
    ru = lang != "en"
    pain = s["pains"][0] if s["pains"] else None
    gain = s["gains"][0] if s["gains"] else None
    gn = [n for n in s["numbers"] if gain and n in gain]
    gnum = gn[-1] if gn else None  # цифра из обещания: «+3 часа», а не цена из другого абзаца
    T = s["topic"]
    def short(x, n):
        if not x:
            return None
        w = re.split(r"[,;:—–]", x)[0].split()[:n]
        while w and (w[-1].lower() in FUNC or len(w[-1]) <= 2 or (len(w) > 1 and w[-2].lower() in FUNC)):
            w.pop()
        return " ".join(w).upper() if w else None
    opts = {
        "pain": [short(pain, nwords) and short(pain, nwords) + "?", "ВОТ В ЧЁМ ПРОБЛЕМА" if ru else "THE REAL PROBLEM", ("ХВАТИТ ЭТО ТЕРПЕТЬ" if ru else "STOP THIS")],
        "result": [gnum and (("+" if re.search(r"(час|мин|дн|%|hour|day)", gnum, re.I) else "") + gnum.upper()),
                   short(gain, nwords), "ЭТО РАБОТАЕТ!" if ru else "IT WORKS!"],
        "curiosity": ["НИКТО НЕ ГОВОРИТ" if ru else "NOBODY TELLS YOU", "ВОТ ПОЧЕМУ…" if ru else "HERE'S WHY…", short(T, 2) and short(T, 2) + "?"],
        "warning": ["НЕ ДЕЛАЙ ТАК!" if ru else "DON'T DO THIS!", "ГЛАВНАЯ ОШИБКА" if ru else "BIG MISTAKE", ("НЕ ПОКУПАЙ" if ru else "DON'T BUY")],
        "compare": [s["versus"] and f"{s['versus'][0].split()[0].upper()} VS {s['versus'][1].split()[0].upper()}", "ЧТО ВЫБРАТЬ?" if ru else "WHICH ONE?", "КТО ЛУЧШЕ?" if ru else "WHO WINS?"],
        "money": [next((n.upper() for n in s["numbers"] if CUR_RX.search(n)), None), "ПЕРЕПЛАТА?" if ru else "WORTH THE MONEY?", "СКОЛЬКО СТОИТ?" if ru else "HOW MUCH?"],
    }[angle]
    return [o for o in dict.fromkeys(o for o in opts if o)][:3]


def _titles(angle, s, lang, hook_ok, target_len):
    ru = lang != "en"
    T = _cap(s["topic"]) or ("Эта тема" if ru else "This")
    P = s["pains"][0] if s["pains"] else None
    G = s["gains"][0] if s["gains"] else None
    N = s["numbers"][0] if s["numbers"] else None
    NT = next((n for n in s["numbers"] if TIME_RX.search(n)), None)
    NC = next((n for n in s["numbers"] if CUR_RX.search(n)), None)
    ND = next((n for n in s["numbers"] if re.search(r"(дн|дней|недел|месяц|год|лет|day|week|month|year)", n, re.I)), None)
    Y = str(dt.date.today().year)
    V = s["versus"]
    if ru:
        bank = {
            "pain": [P and f"{_cap(P)}? Вот что с этим делать", P and f"Почему {_low(P)} — и как это исправить",
                     f"{T}: главная проблема, о которой молчат", f"{T} — разбираю, что не так"],
            "result": [G and re.search(r"\d", G) and _cap(G), G and NT and not re.search(r"\d", G) and f"{_cap(G)} — за {NT}",
                       G and f"{T}: {_low(G)}",
                       f"{T}: проверил на себе — итог удивил", ND and f"{T}: что изменилось за {ND}"],
            "curiosity": [f"{T}: что на самом деле происходит", f"{T} — вся правда, которую не говорят",
                          f"{T}: оказалось, всё не так", f"Вот что не так с {T}" if re.match(r"[A-Za-z]", T) else f"{T}: никто не ожидал"],
            "warning": [f"{T}: не делай так, пока не посмотришь это", f"{T}: главная ошибка, которую делают все",
                        f"{T} — так делать нельзя", P and f"Хватит терпеть: {_low(P)}"],
            "compare": [V and f"{V[0]} или {V[1]}? Честное сравнение", V and f"{V[0]} vs {V[1]}: что выбрать в {Y}",
                        f"{T}: что выбрать в {Y}?", f"{T} — сравнил и выбрал лучшее"],
            "money": [NC and f"{T} за {NC} — стоит ли того?",
                      f"{T}: сколько это стоит на самом деле", f"{T}: как не переплатить",
                      NC and f"{T}: {NC} — мой опыт"],
        }[angle]
    else:
        bank = {
            "pain": [P and f"{_cap(P)}? Here's the fix", f"{T}: the problem nobody talks about", f"Why {T} isn't working for you"],
            "result": [G and re.search(r"\d", G) and _cap(G), NT and f"I tried {T} for {NT} — results", G and f"{_cap(G)} (step by step)", f"{T}: what actually happened"],
            "curiosity": [f"The truth about {T}", f"{T}: what nobody tells you", f"I was wrong about {T}"],
            "warning": [f"Don't start {T} before watching this", f"{T}: the biggest mistake", f"Stop doing this with {T}"],
            "compare": [V and f"{V[0]} vs {V[1]}: which one in {Y}?", f"{T}: which one should you pick?", f"I compared every {T}"],
            "money": [NC and f"{T} for {NC} — worth it?", f"{T}: the real cost", f"How to save on {T}"],
        }[angle]
    out = []
    for t in bank:
        if not t:
            continue
        t = re.sub(r"\s+", " ", t).strip()
        if hook_ok.get("number") and N and not re.search(r"\d", t) and len(out) == 0 and ru:
            t = f"{t} ({N})"
        if hook_ok.get("caps") and len(out) == 1:
            ws = sorted(t.split(), key=len, reverse=True)
            if ws and len(ws[0]) >= 4:
                t = t.replace(ws[0], ws[0].upper(), 1)
        if len(t) > 95:  # YouTube показывает около 70–100 знаков; длиннее — обрезаем по слову
            t = t[:92].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
        out.append(t)
    uniq = []
    for t in out:  # без почти одинаковых: «X» и «Тема: X»
        core = re.sub(r"^[^:]{1,40}:\s*", "", t).lower()
        if any(core in u.lower() or u.lower() in t.lower() for u in uniq):
            continue
        uniq.append(t)
    return uniq[:3]


def _prompt(angle, s, rec, faces_ok, text, lang, side):
    a = ANGLES[angle]
    subj = ", ".join(dict.fromkeys([x for x in ([s["topic"]] + (s["entities"][1:2] if angle == "compare" else []) + s["terms"][:1]) if x])) or "тема ролика"
    cols = rec["colors"] if rec and rec["colors"] else ["жёлтый", "чёрный"]
    bright = rec and rec["bright"] >= 55
    other = {"left": "справа", "right": "слева", "center": "сверху"}.get(side or "right", "слева")
    side_ru = {"left": "слева", "right": "справа", "center": "по центру"}.get(side or "right", "справа")
    side_en = {"left": "left", "right": "right", "center": "center"}.get(side or "right", "right")
    other_en = {"left": "right", "right": "left", "center": "top"}.get(side or "right", "left")
    ru = ["Превью для YouTube, формат 16:9, фотореализм."]
    en = ["YouTube thumbnail, 16:9, photorealistic."]
    if faces_ok:
        size = "крупный план, лицо занимает около трети высоты кадра" if not rec or rec["face_pct"] >= 6 else "в кадре по пояс"
        ru.append(f"Человек {side_ru}, {size}, эмоция: {a['emo'][0]}, смотрит в камеру.")
        en.append(f"Person on the {side_en}, {'close-up, face fills a third of the frame' if not rec or rec['face_pct'] >= 6 else 'waist-up shot'}, {a['emo'][1]}, looking at the camera.")
    ru.append(f"В кадре: {subj} — крупно, легко узнать за секунду.")
    en.append(f"Main subject: [{subj} — переведите на английский], large and instantly recognizable.")
    if angle == "compare":
        ru.append("Два предмета рядом, разделены по центру.")
        en.append("Two items side by side, split composition.")
    if angle == "result":
        ru.append("Видна разница «было → стало».")
        en.append("Clear before/after contrast.")
    ru.append(f"Фон {'светлый' if bright else 'тёмный, однотонный'}, цвета: {', '.join(cols)}. Высокий контраст, сочные цвета, резкий фокус, без мелких деталей.")
    en.append(f"{'Bright' if bright else 'Dark plain'} background, color palette: {', '.join(COLOR_EN.get(c, c) for c in cols)}. High contrast, vivid colors, sharp focus, no clutter.")
    if text:
        ru.append(f"Свободное место {other} под крупную надпись «{text}».")
        en.append(f"Empty space on the {other_en} for a bold headline.")
    ru.append("Без водяных знаков, без мелкого текста.")
    en.append("No watermark, no small text.")
    return " ".join(ru), " ".join(en)


def concepts(script, own, comp, lang):
    """3–4 варианта превью. Порядок — по тому, какие приёмы реально поднимают просмотры в нише."""
    groups = [g for g in (own, comp) if g]
    score = Counter()
    for g in groups:
        for h in g["hooks"]:
            if h["ratio"]:
                w = {"высокая": 1.0, "средняя": 0.7, "низкая": 0.35 * min(1, h["n_with"] / 8)}[h["confidence"]]
                score[h["key"]] += w * math.log(h["ratio"])
    s = script
    avail = {"pain": True, "result": True, "curiosity": True, "warning": True,
             "compare": bool(s["versus"]) or score["compare"] > 0.1, "money": bool(s["numbers"]) or score["money"] > 0.1}
    if s["pains"]:
        score["pain"] += 0.15
    if s["gains"] or s["numbers"]:
        score["result"] += 0.1
    if s["versus"]:  # в сценарии прямое сравнение — этот вариант обязателен
        score["compare"] += 0.3
    order = sorted([a for a in ANGLES if avail[a]], key=lambda a: -score[ANGLES[a]["hook"]])[:4]

    # визуальные решения — сначала по своему каналу (если вывод надёжный), иначе по конкурентам
    def eff(key):  # учитываем только выводы со средней или высокой надёжностью
        for g in groups:
            for e in g["visual"]:
                if e["key"] == key and e["confidence"] != "низкая":
                    return e["ratio"]
        return None
    fr = eff("face")
    faces_ok = fr is None or fr >= 0.85
    # как выглядят хиты: по своему каналу, если хитов достаточно, иначе по конкурентам
    rec = (own or {}).get("recipe") if (own or {}).get("hits", 0) >= 6 else (comp or {}).get("recipe")
    rec = rec or (own or comp or {}).get("recipe")
    tr = eff("text")
    nwords = max(2, min(4, (rec or {}).get("words") or 3))
    hook_ok = {h["key"]: (h["ratio"] or 0) > 1.05 for g in groups for h in g["hooks"]}
    tl = (own or comp or {}).get("title_len")
    pv = ((own or {}).get("pairing") or {}).get("verdict")
    out = []
    for a in order:
        texts = _thumb_texts(a, s, nwords, lang) if tr is None or tr >= 0.85 else []
        titles = _titles(a, s, lang, hook_ok, tl)
        # текст на превью не повторяет название — дополняет его
        if pv != "repeat":  # по умолчанию текст на превью дополняет название, а не повторяет его
            texts = [t for t in texts if not titles or len(_stems(t) & _stems(titles[0])) < max(1, len(_stems(t)) // 2)] or texts[:1]
        pr_ru, pr_en = _prompt(a, s, rec, faces_ok, texts[0] if texts else None, lang, (rec or {}).get("face_side"))
        hk = ANGLES[a]["hook"]
        ev = next((h for g in groups for h in g["hooks"] if h["key"] == hk), None)
        out.append({"angle": a, "name": ANGLES[a]["name"], "why": ANGLES[a]["why"],
                    "evidence": ev and {"ratio": ev["ratio"], "confidence": ev["confidence"], "n": ev["n_with"]},
                    "thumb_text": texts, "titles": titles, "prompt_ru": pr_ru, "prompt_en": pr_en,
                    "face": faces_ok, "emotion": ANGLES[a]["emo"][0] if faces_ok else None})
    return out


# ---------------------------------------------------------------- выводы
def _fmt_ratio(r):
    return f"в {str(round(r, 1)).replace('.', ',')} раза больше" if r >= 1 else f"на {round(100 * (1 - r))}% меньше"


def summary(own, comp, comp_label="У конкурентов"):
    out = []
    for g, who in ((own, "У вас"), (comp, comp_label)):
        if not g:
            continue
        st = [s for s in g["styles"] if s["n"] >= 3]
        if st:
            out.append(f"{who} лучше всего работает стиль «{st[0]['name']}»: такие превью набирают обычно {str(st[0]['median']).replace('.', ',')}× от соседних роликов.")
        good = [e for e in g["visual"] if e["confidence"] != "низкая" and e["ratio"] and e["ratio"] >= 1.15][:2]
        bad = [e for e in g["visual"] if e["confidence"] != "низкая" and e["ratio"] and e["ratio"] <= 0.85][:1]
        for e in good:
            out.append(f"{who}: «{e['name'].lower()}» — {_fmt_ratio(e['ratio'])} просмотров (надёжность {e['confidence']}).")
        for e in bad:
            out.append(f"{who}: «{e['name'].lower()}» снижает просмотры — {_fmt_ratio(e['ratio'])}.")
        hk = [h for h in g["hooks"] if h["confidence"] != "низкая" and h["ratio"] and h["ratio"] >= 1.15][:2]
        if hk:
            out.append(f"{who} в названиях лучше заходят приёмы: " + ", ".join(f"«{h['name'].lower()}» ({_fmt_ratio(h['ratio'])})" for h in hk) + ".")
    if own and own.get("pairing") and own["pairing"].get("verdict") in ("complement", "repeat"):
        p = own["pairing"]
        out.append("Текст на превью лучше дополняет название, а не повторяет его." if p["verdict"] == "complement"
                   else "У вас лучше заходит, когда текст на превью повторяет главное слово названия.")
    if not out:
        out.append("Явных закономерностей мало: превью канала похожи друг на друга или роликов немного. Ориентируйтесь на хиты конкурентов ниже.")
    return out


def copy_from(own, comp):
    """Что у конкурентов приносит просмотры, а у вас почти не используется."""
    if not own or not comp:
        return []
    mine = {e["key"]: e["share_all"] for e in own["visual"]}
    mine_h = {e["key"]: e["share_all"] for e in own["hooks"]}
    out = []
    for e in comp["visual"]:
        if e["ratio"] and e["ratio"] >= 1.15 and e["confidence"] != "низкая" and mine.get(e["key"], 0) + 15 <= (e["share_hits"] or 0):
            out.append({"kind": "превью", "name": e["name"], "ratio": e["ratio"], "confidence": e["confidence"],
                        "theirs": e["share_hits"], "yours": mine.get(e["key"], 0),
                        "text": f"У конкурентов это есть в {e['share_hits']}% хитов, у вас — в {mine.get(e['key'], 0)}% превью."})
    for e in comp["hooks"]:
        if e["ratio"] and e["ratio"] >= 1.15 and e["confidence"] != "низкая" and mine_h.get(e["key"], 0) + 15 <= (e["share_hits"] or 0):
            out.append({"kind": "название", "name": e["name"], "ratio": e["ratio"], "confidence": e["confidence"],
                        "theirs": e["share_hits"], "yours": mine_h.get(e["key"], 0),
                        "text": f"В названиях хитов конкурентов — {e['share_hits']}%, у вас — {mine_h.get(e['key'], 0)}%."})
    ost = {s["key"]: s for s in own["styles"]}
    for s in comp["styles"][:1]:
        if s["n"] >= 4 and s["median"] >= 1.1 and ost.get(s["key"], {}).get("share", 0) < 25:
            out.append({"kind": "стиль", "name": s["name"], "ratio": s["median"], "confidence": None,
                        "theirs": s["share"], "yours": ost.get(s["key"], {}).get("share", 0),
                        "text": f"Лучший стиль конкурентов, у вас таких превью {ost.get(s['key'], {}).get('share', 0)}%."})
    out.sort(key=lambda x: -(x["ratio"] or 0))
    return out[:6]


def best_style(own, comp):
    pool = Counter()
    n = Counter()
    for g, w in ((own, 1.0), (comp, 0.8)):
        for s in (g or {}).get("styles", []):
            if s["n"] >= 3:
                pool[s["key"]] += w * s["n"] * math.log(max(s["median"], 0.05))
                n[s["key"]] += w * s["n"]
    if not n:
        return None
    k = max(n, key=lambda k: pool[k] / n[k])
    used = {s["key"]: s["share"] for s in (own or {}).get("styles", [])}
    closest = max(used, key=used.get) if used else None
    return {"key": k, "name": STYLES[k][0], "desc": STYLES[k][1], "yours_share": used.get(k, 0),
            "closest": closest and {"key": closest, "name": STYLES[closest][0], "share": used[closest]}}


def attract(own, comp, script):
    """Чем зацепить и какую боль показать."""
    out = {"triggers": [], "pains": [], "promises": []}
    seen = set()
    for g in (own, comp):
        for h in (g or {}).get("hooks", []):
            if h["key"] in seen or not h["ratio"] or h["ratio"] < 1.1:
                continue
            seen.add(h["key"])
            ex = [v["title"] for v in (g or {}).get("top", []) if h["name"] in v["hooks"]][:2]
            out["triggers"].append({"name": h["name"], "ratio": h["ratio"], "confidence": h["confidence"], "examples": ex})
    out["triggers"].sort(key=lambda x: -x["ratio"])
    out["triggers"] = out["triggers"][:5]
    out["pains"] = script["pains"][:4] if script else []
    if not out["pains"]:
        for g in (own, comp):
            out["pains"] += [v["title"] for v in (g or {}).get("top", []) if "Боль и проблема" in v["hooks"]][:3]
    out["promises"] = (script or {}).get("gains", [])[:3]
    return out


def niche_take(comp):
    """Новый канал: что взять из хитов ниши (своих превью для сравнения ещё мало)."""
    out = []
    for e in comp["visual"] + comp["hooks"]:
        if e["ratio"] and e["ratio"] >= 1.15 and e["confidence"] != "низкая":
            kind = "название" if e in comp["hooks"] else "превью"
            out.append({"kind": kind, "name": e["name"], "ratio": e["ratio"], "confidence": e["confidence"],
                        "theirs": e["share_hits"], "yours": None,
                        "text": f"Есть в {e['share_hits']}% хитов ниши." if e.get("share_hits") is not None else ""})
    for s in comp["styles"][:1]:
        if s["n"] >= 4 and s["median"] >= 1.1:
            out.append({"kind": "стиль", "name": s["name"], "ratio": s["median"], "confidence": None, "theirs": s["share"],
                        "yours": None, "text": f"Так сделано {s['share']}% превью в нише."})
    out.sort(key=lambda x: -(x["ratio"] or 0))
    return out[:6]


def own_vs_niche(own_items, comp_items, comp):
    """Новый канал: каждое ваше превью рядом с тем, что работает в нише (без оценки успеха — роликов мало)."""
    if not own_items or not comp_items:
        return []
    med = {k: statistics.median(v["f"][k] for v in comp_items) for k in ("bright", "sat", "contrast", "detail")}
    good = [e for e in comp["visual"] if e["ratio"] and e["ratio"] >= 1.1 and e["confidence"] != "низкая"][:4]
    bad = [e for e in comp["visual"] if e["ratio"] and e["ratio"] <= 0.9 and e["confidence"] != "низкая"][:2]
    best = (comp["styles"] or [{}])[0]
    out = []
    for v in own_items:
        v.setdefault("hooks", hooks_of(v["title"]))
        fl = {k for k, on in flags(v["f"], med).items() if on}
        st = style_of(v["f"])
        card = thumb_card(dict(v, perf=v.get("perf") or 0))
        card.update({"style": STYLES[st][0], "style_best": st == best.get("key"),
                     "has": [e["name"] for e in good if e["key"] in fl], "missing": [e["name"] for e in good if e["key"] not in fl],
                     "avoid": [e["name"] for e in bad if e["key"] in fl], "age": v.get("age")})
        out.append(card)
    return out


# ---------------------------------------------------------------- поиск конкурентов
def find_competitors(own_ch, own_vids, prog, want=3, hidden_ids=(), topic=None, extra_queries=()):
    """Конкуренты без подсказки: ищем на YouTube по темам лучших роликов канала (на языке канала и как из его страны),
    берём каналы, которые попадаются по нескольким запросам, и проверяем их так же строго, как в анализе ниши:
    язык (по звуку видео и описанию), категория YouTube, доля роликов по теме, своя главная тема,
    перепроверка кликбейтных названий по описаниям и обложкам. Каналы, которые вы убирали раньше, пропускаются."""
    lang = own_ch["lang"]
    yl = niche.ylang_for(lang)
    region = niche.split_ylang(yl)[1]
    script_want = "cyr" if lang in ("ru", "uk") else "lat"
    best = sorted(own_vids, key=lambda v: -(v.get("perf") or 0) * math.log10(max(v["views"], 10)))
    queries = [q for q in extra_queries if q][:3]
    for v in best:
        q = niche.clean_query(v["title"], 6)
        if len(q.split()) >= 2 and q.lower() not in (x.lower() for x in queries):
            queries.append(q)
        if len(queries) >= 8:
            break
    tally = {}
    with ThreadPoolExecutor(4) as ex:
        for qi, res in enumerate(ex.map(lambda q: analyzer.search(q, 20, lang=yl), queries)):
            for e in res or []:
                cid = e.get("channel_id")
                if not cid or cid == own_ch.get("id") or cid in hidden_ids or not niche.same_script(e.get("title") or "", script_want):
                    continue
                t = tally.setdefault(cid, {"q": set(), "views": 0, "url": e.get("uploader_url") or e.get("channel_url"),
                                           "title": e.get("channel"), "titles": [], "id": cid})
                t["q"].add(qi)
                t["views"] += e.get("view_count") or 0
                t["titles"].append(e.get("title") or "")
    prog(12, f"Нашёл {len(tally)} похожих каналов, проверяю язык и тематику…")
    cands = sorted(tally.values(), key=lambda t: (-len(t["q"]), -t["views"]))
    cands = [c for c in cands if len(c["q"]) >= 2][:10] or cands[:7]
    own_titles = [v["title"] for v in own_vids] + list(extra_queries)
    own_doc = niche.doc_terms(own_titles, analyzer.tokenize)
    gate = niche.TopicGate(own_titles, list(own_ch.get("tags") or []) + ([topic] if topic else []), analyzer.tokenize)
    top_own = sorted(own_vids, key=lambda v: -(v.get("views") or 0))[:3]
    with ThreadPoolExecutor(3) as ex:
        own_cats = [c for c in ex.map(lambda v: analyzer.video_category(v["id"], region), top_own) if c]
    own_ch["category"] = own_cats[0] if own_cats else None

    def load(c):
        try:
            ch, vs = channel_videos(c["url"], COMP_LIMIT)
        except Exception:
            return c, None, None, "канал не открылся"
        vs = add_perf(vs)
        if len(vs) < 8:
            return c, None, None, "мало длинных роликов"
        if ch["lang"] != lang:
            return c, None, None, "другой язык"
        top = max(vs, key=lambda v: v["views"])
        info = analyzer.video_info(top["id"], yl) or {}
        audio = (info.get("language") or "").split("-")[0].lower()
        if script_want == "lat" and audio and audio != lang:  # названия переведены, а говорят на другом языке
            return c, None, None, "другой язык (по звуку видео)"
        ch["category"] = analyzer.video_category(top["id"], region)
        g = gate.check([v["title"] for v in vs])
        why = niche.TopicGate.verdict(g, ch["category"], own_cats)
        if why and not why.startswith("категория"):
            # кликбейтные названия: смотрим описания, теги и текст на обложках
            ocr = []
            for v in sorted(vs, key=lambda v: -v["views"])[:3]:
                img = load_img(v["id"])
                if img is not None:
                    ocr.append(" ".join(w["w"] for w in _ocr(img)))
            hits = gate.evidence([ch.get("description") or ""] + list(ch.get("tags") or []) + list(info.get("tags") or [])
                                 + [info.get("description") or ""] + ocr)
            if len(hits) >= 3:
                ch["rescued"] = ", ".join(hits[:5])
                why = None
        if why:
            why = why.replace("у ниши —", "у вашего канала —").replace("по теме ниши", "по вашей теме")
            return c, None, None, "другая тематика: " + why
        # сверка цифр: просмотры из списка канала против страницы самого ролика
        if info.get("view_count") and top["views"]:
            ch["views_diff"] = round(abs(info["view_count"] - top["views"]) / max(info["view_count"], top["views"]), 3)
        ch["lang_source"] = "звук видео" if audio else None
        ch["topic_coverage"] = g["coverage"] if g else None
        return c, ch, vs, None
    got, rejected = [], []
    rest = [c for c in sorted(tally.values(), key=lambda t: (-len(t["q"]), -t["views"])) if c not in cands]
    for batch in (cands, rest[:8]):  # если после проверок осталось мало — добираем из следующих по похожести
        if len(got) >= want + 1 or not batch:
            break
        if batch is not cands:
            prog(16, "Добираю конкурентов: прошлые не прошли проверку языка или тематики…")
        with ThreadPoolExecutor(3) as ex:
            for c, ch, vs, why in ex.map(load, batch):
                if ch:
                    got.append((c, ch, vs))
                elif why and why != "канал не открылся":
                    rejected.append({"id": c["id"], "title": c["title"], "url": c["url"], "reason": why})
    if not got:
        return [], rejected
    docs = {r[1]["url"]: niche.doc_terms([v["title"] for v in r[2]], analyzer.tokenize) for r in got}
    sim = niche.Similarity(own_doc, docs)
    for c, ch, vs in got:
        ch["sim"] = round(100 * sim.score(docs[ch["url"]]))
        ch["found_by"] = len(c["q"])
    got.sort(key=lambda r: -(r[1]["sim"] * (1 + 0.15 * r[1]["found_by"])))
    top = got[0][1]["sim"] or 1
    keep = [(ch, vs) for _, ch, vs in got if ch["sim"] >= 0.5 * top][:want]
    for _, ch, _ in got:
        if ch["sim"] < 0.5 * top:
            rejected.append({"id": ch.get("id"), "title": ch["title"], "url": ch["url"], "reason": "мало похож по темам роликов"})
    return keep, rejected


# ---------------------------------------------------------------- запуск
def _analyze_imgs(vids, prog, p0, p1, label):
    out = []
    done = [0]

    def one(v):
        img = load_img(v["id"])
        done[0] += 1
        if done[0] % 5 == 0:
            prog(p0 + (p1 - p0) * done[0] / len(vids), f"{label}: разбираю превью {done[0]} из {len(vids)}…")
        if img is None:
            return None
        return dict(v, f=features(img))

    with ThreadPoolExecutor(3) as ex:
        for r in ex.map(one, vids):
            if r:
                out.append(r)
    return out


def run_thumbs(own_url, comp_urls, script_text, topic, prog, remembered=None):
    if not FACE_MODEL.exists():
        raise ValueError("Не найдена модель распознавания лиц — обновите сервис.")
    prog(3, "Открываю ваш канал…")
    own_ch, raw = channel_videos(own_url, OWN_LIMIT)
    own_v = add_perf([dict(v) for v in raw])
    new_mode = len(own_v) < 8  # своих роликов мало: успех превью сравнивать не с чем — смотрим, что работает в нише
    if new_mode:
        if not raw and not (topic or script_text):
            raise ValueError("На канале пока нет роликов. Напишите в поле «Тема нового ролика», о чём канал (или вставьте сценарий), — "
                             "и я покажу, какие превью и названия работают в этой нише.")
        if not raw and not own_ch.get("description"):  # язык канала не по чему определить — берём по теме или сценарию
            txt = (topic or "") + " " + (script_text or "")[:2000]
            if niche.script(txt) == "cyr":
                own_ch["lang"] = "uk" if len(re.findall(r"[іїєґ]", txt.lower())) >= 5 else "ru"
            else:
                own_ch["lang"] = niche.lang_confident(txt, min_hits=2)[0] or insights.detect_lang([txt]) or "en"
        own_v = [dict(v, perf=1.0) for v in raw]
        prog(5, f"На канале {len(raw)} {_pl(len(raw), 'ролик', 'ролика', 'роликов')} — для своей статистики мало. Смотрю, что работает в нише…")
    for v in own_v:
        v["ch"] = own_ch["title"]
    comps = []
    auto = not comp_urls
    rejected, hidden = [], []
    if auto:
        prog(6, "Ищу конкурентов по темам ваших лучших роликов…")
        try:
            hidden = remembered(own_ch.get("id")) if remembered else []
        except Exception:
            hidden = []
        extra = []
        if new_mode:
            sc0 = parse_script(script_text, topic) if script_text else None
            extra = [topic, (sc0 or {}).get("topic")]
        try:
            comps, rejected = find_competitors(own_ch, own_v, prog, hidden_ids={h["id"] for h in hidden}, topic=topic,
                                               extra_queries=[q for q in extra if q])
        except Exception:
            comps = []
    for i, u in enumerate(comp_urls[:3]):
        prog(8 + 4 * i, f"Открываю канал конкурента {i + 1}…")
        try:
            ch, vs = channel_videos(u, COMP_LIMIT)
        except Exception:
            continue
        comps.append((ch, add_perf(vs)))
    for ch, vs in comps:
        for v in vs:
            v["ch"] = ch["title"]
        ch["n"] = len(vs)
        ch["auto"] = auto
    if new_mode and not comps:
        raise ValueError("На канале мало роликов, а похожих каналов на том же языке не нашлось. Уточните тему в поле «Тема нового ролика» "
                         "или впишите 1–3 канала конкурентов вручную.")
    prog(20, "Скачиваю и разбираю превью…")
    own_items = _analyze_imgs(own_v, prog, 20, 60 if comps else 88, "Ваш канал")
    comp_items = []
    for i, (ch, vs) in enumerate(comps):
        a = 60 + 28 * i / len(comps)
        comp_items += _analyze_imgs(vs, prog, a, a + 28 / len(comps), ch["title"])
    prog(90, "Ищу закономерности…")
    own_ch["n"] = len(own_items)
    comp = analyze_group(comp_items) if len(comp_items) >= 8 else None
    if new_mode and not comp:
        raise ValueError("Превью конкурентов не загрузились — попробуйте ещё раз или впишите конкурентов вручную.")
    own = None if new_mode else analyze_group(own_items)
    lang = own_ch["lang"]
    sc = parse_script(script_text, topic) if (script_text or topic) else None
    if new_mode and not sc:  # без сценария варианты превью строим по темам уже вышедших роликов
        sc = parse_script(". ".join(v["title"] for v in raw[:7]), niche.clean_query(max(raw, key=lambda v: v["views"] or 0)["title"], 5) if raw else None)
    if sc and not sc["topic"]:
        sc["topic"] = topic or ""
    res = {
        "type": "thumbs", "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "channel": own_ch, "competitors": [ch for ch, _ in comps], "competitors_auto": auto, "lang": lang,
        "rejected": rejected, "hidden_channels": hidden,
        "own": own, "comp": comp,
        "new_channel": {"videos": len(raw), "eligible": len([v for v in raw if v["age"] is None or v["age"] >= 7])} if new_mode else None,
        "own_vs": own_vs_niche(own_items, comp_items, comp) if new_mode else [],
        "summary": summary(own, comp, "В нише" if new_mode else "У конкурентов"), "best_style": best_style(own, comp),
        "copy": niche_take(comp) if new_mode else copy_from(own, comp), "attract": attract(own, comp, sc),
        "script": sc, "concepts": concepts(sc, own, comp, lang) if sc else [],
        "ocr": bool(pytesseract),
        # для истории и общего обработчика задач
        "niche": {"overall": None}, "competitor": {"title": "Превью: " + own_ch["title"]},
    }
    prog(100, "Готово")
    return res
