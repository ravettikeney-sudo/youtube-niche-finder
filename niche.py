"""
Точное попадание в нишу: язык, похожесть тематики (TF-IDF), запросы по видео конкурента.
"""
import re
import math
from collections import Counter

# коды языков для интерфейса YouTube (yt-dlp extractor-arg lang)
YT_LANG = {"ru": "ru", "uk": "uk", "es": "es", "pt": "pt", "de": "de", "fr": "fr", "it": "it",
           "tr": "tr", "hi": "hi", "ar": "ar", "ja": "ja", "ko": "ko", "zh": "zh-CN"}

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def script(text):
    """Письменность текста: cyr / lat / other / None (если букв нет)."""
    cnt = Counter()
    for ch in text or "":
        if not ch.isalpha():
            continue
        o = ord(ch)
        cnt["cyr" if 0x400 <= o <= 0x4FF else "lat" if o < 0x250 else "other"] += 1
    if not cnt:
        return None
    s, n = cnt.most_common(1)[0]
    return s if n >= 0.6 * sum(cnt.values()) else "mixed"


def letters(text):
    c = Counter()
    for ch in text or "":
        if ch.isalpha():
            o = ord(ch)
            c["cyr" if 0x400 <= o <= 0x4FF else "lat" if o < 0x250 else "other"] += 1
    return c


def same_script(title, want):
    """Подходит ли название по языку. Названия брендов латиницей («iPhone») в русском заголовке не мешают."""
    c = letters(title)
    if not c:
        return True
    if want == "cyr":
        return c["cyr"] >= 3
    if want == "lat":
        return (c["cyr"] + c["other"]) <= 0.2 * sum(c.values())
    return c[want] >= 0.5 * sum(c.values())


def channel_lang_ok(titles, want, desc=None):
    """Канал на языке конкурента: так написано большинство его названий и описание канала.
    Описание YouTube не переводит, поэтому оно выдаёт каналы с автопереводом названий."""
    c = letters(re.sub(r"https?://\S+|\S+@\S+", " ", desc or ""))
    if sum(c.values()) >= 40:
        if want == "cyr" and c["cyr"] < 0.3 * sum(c.values()):
            return False
        if want == "lat" and c["cyr"] + c["other"] > 0.5 * sum(c.values()):
            return False
    ts = [t for t in titles if letters(t)]
    if not ts:
        return True
    return sum(same_script(t, want) for t in ts) >= 0.7 * len(ts)


_UNITS = [(r"млрд|мільярд|mrd|bn|mil\s+mi|bi", 10 ** 9), (r"млн|мільйон|mln|mio|mi\b|m\b|mn", 10 ** 6),
          (r"тыс|тис|k\b|mil\b|tsd|b\b|bin|mille", 10 ** 3)]


def loc_count(s, orig):
    """Числа в интерфейсе YouTube на любом языке: «1,98 млн», «538 тыс.», «2,3 Mio.», «12 mil»."""
    if s is None:
        return None
    m = re.search(r"(\d[\d\s\u00a0\u202f.,]*)\s*([^\d\s]*)", s)
    if not m:
        return orig(s)
    num, rest = m.group(1).strip(), s[m.end(1):].strip().lower()
    for pat, mul in _UNITS:
        if re.match(pat, rest):
            n = num.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
            n = n.replace(",", ".") if n.count(",") == 1 and "." not in n else n.replace(",", "")
            try:
                return int(float(n) * mul)
            except ValueError:
                return orig(s)
    digits = re.sub(r"[\s\u00a0\u202f.,]", "", num)  # «823 825», «1.873.840» — разделители тысяч
    if digits.isdigit() and (re.search(r"\d[\s\u00a0\u202f]\d{3}\b", num) or re.search(r"\d[.,]\d{3}([.,]\d{3})*$", num.strip())):
        return int(digits)
    return orig(s)


def install_count_parser():
    """Подменяет разбор чисел в yt-dlp на понимающий русский и другие языки интерфейса."""
    import functools
    import yt_dlp.utils as U
    from yt_dlp.extractor.youtube import _base, _tab
    orig = getattr(U, "_nf_orig_parse_count", U.parse_count)
    U._nf_orig_parse_count = orig
    f = functools.partial(loc_count, orig=orig)
    _base.parse_count = f
    _tab.parse_count = f


def clean_query(title, max_words=8):
    """Заголовок видео конкурента → поисковый запрос (без эмодзи, хэштегов и мусора)."""
    t = EMOJI.sub(" ", title or "")
    t = re.sub(r"#\S+", " ", t)
    t = re.split(r"\s[|•]\s|\s[-–—]\s(?=[^-–—]*$)", t)[0]  # отрезаем «| Канал» / « — серия 3»
    t = re.sub(r"[\[\](){}\"«»“”!?.,:;]+", " ", t)
    words = [w for w in t.split() if len(w) > 1][:max_words]
    return " ".join(words)


def stem(w):
    return w[:6] if len(w) > 6 else w


def doc_terms(texts, tokenize):
    """Термины канала: основы слов и пары слов; каждый заголовок считается один раз."""
    c = Counter()
    for t in texts:
        tk = [stem(w) for w in tokenize(t)]
        terms = set(tk) | {a + "_" + b for a, b in zip(tk, tk[1:])}
        c.update(terms)
    return c


class Similarity:
    """Косинусная близость TF-IDF между конкурентом и каналами-кандидатами."""

    def __init__(self, comp_docs, cand_docs):
        docs = [comp_docs] + list(cand_docs.values())
        n = len(docs)
        df = Counter()
        for d in docs:
            df.update(d.keys())
        self.idf = {t: math.log((n + 1) / (f + 1)) + 1 for t, f in df.items()}
        self.comp = self._vec(comp_docs)

    def _vec(self, d):
        v = {t: (1 + math.log(c)) * self.idf.get(t, 1) for t, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1
        return {t: x / norm for t, x in v.items()}

    def score(self, d):
        v = self._vec(d)
        return sum(x * self.comp.get(t, 0) for t, x in v.items())

    def top_terms(self, d, k=6):
        v = self._vec(d)
        common = sorted(((x * self.comp[t], t) for t, x in v.items() if t in self.comp), reverse=True)
        return [t.replace("_", " ") for _, t in common[:k]]


def adaptive_cut(scores, strict=True):
    """Порог «канал из этой ниши» относительно самых похожих каналов выдачи."""
    s = sorted((x for x in scores if x > 0), reverse=True)
    if not s:
        return 1.0, 1.0
    top = s[:5]
    ref = top[len(top) // 2]  # медиана пяти самых похожих: клоны конкурента не завышают планку
    cut = max(0.012, ref * (0.40 if strict else 0.26))
    return cut, ref


# ---------------------------------------------------------------- регион поиска по языку ниши
# Выдача YouTube зависит от страны. Сервер стоит в другой стране, поэтому страну задаём явно:
# английская ниша ищется как из США, испанская — как из Испании, русская — как из России и т.д.
REGION = {"en": "US", "ru": "RU", "uk": "UA", "es": "ES", "pt": "BR", "de": "DE", "fr": "FR", "it": "IT",
          "tr": "TR", "hi": "IN", "ar": "SA", "ja": "JP", "ko": "KR", "zh": "TW", "pl": "PL", "id": "ID", "vi": "VN"}
REGION_NAMES = {"US": "США", "GB": "Великобритания", "CA": "Канада", "AU": "Австралия", "IN": "Индия", "RU": "Россия",
                "UA": "Украина", "KZ": "Казахстан", "BY": "Беларусь", "ES": "Испания", "MX": "Мексика", "AR": "Аргентина",
                "CO": "Колумбия", "BR": "Бразилия", "PT": "Португалия", "DE": "Германия", "AT": "Австрия", "FR": "Франция",
                "IT": "Италия", "TR": "Турция", "SA": "Саудовская Аравия", "EG": "Египет", "JP": "Япония", "KR": "Корея",
                "TW": "Тайвань", "PL": "Польша", "ID": "Индонезия", "VN": "Вьетнам"}


def ylang_for(lang_code, region=None):
    """Параметр для yt-dlp: «язык@страна», например «es@ES»."""
    code = YT_LANG.get(lang_code) or ("en" if lang_code in (None, "en", "other") else None) or "en"
    reg = (region or REGION.get(lang_code) or REGION.get(code.split("-")[0]) or "US").upper()
    return f"{code}@{reg}"


def split_ylang(yl):
    if not yl:
        return None, None
    code, _, reg = yl.partition("@")
    return code or None, reg or None


def install_region_patch():
    """Передаём страну (gl) в запросы yt-dlp к YouTube — у yt-dlp своего параметра для этого нет."""
    from yt_dlp.extractor.youtube import _base
    B = _base.YoutubeBaseInfoExtractor
    if getattr(B, "_nf_region", False):
        return
    orig_ctx, orig_pref = B._extract_context, B._initialize_pref

    def _gl(self):
        for k in ("youtube", "youtubetab"):
            try:
                v = self._configuration_arg("nf_gl", [None], ie_key=k)[0]
            except Exception:
                v = None
            if v:
                return v
        return None

    def ctx(self, ytcfg=None, default_client="web"):
        c = orig_ctx(self, ytcfg, default_client)
        gl = _gl(self)
        if gl and isinstance(c, dict) and isinstance(c.get("client"), dict):
            c["client"]["gl"] = gl.upper()
        return c

    def pref(self):
        orig_pref(self)
        gl = _gl(self)
        if gl:
            import urllib.parse
            ck = self._youtube_cookies.get("PREF")
            d = dict(urllib.parse.parse_qsl(ck.value)) if ck else {}
            d["gl"] = gl.upper()
            self._set_cookie(".youtube.com", name="PREF", value=urllib.parse.urlencode(d))

    B._extract_context, B._initialize_pref, B._nf_region = ctx, pref, True


# ---------------------------------------------------------------- точный язык канала (латиница)
def lang_confident(text, min_hits=3):
    """Язык текста по частым словам; (код, уверенность). Для латиницы различает en/es/pt/de/fr/it/tr."""
    import insights
    words = re.findall(r"[a-zà-ÿıçşğöüñ]+", (text or "").lower())
    if len(words) < 6:
        return None, 0
    sc = {lang: sum(1 for w in words if w in hs) for lang, hs in insights.LATIN_HINTS.items()}
    best = max(sc, key=sc.get)
    second = sorted(sc.values())[-2]
    if sc[best] >= min_hits and sc[best] >= 2 * max(1, second):
        return best, sc[best]
    return None, 0


# ---------------------------------------------------------------- вторая проверка тематики
# Похожесть по словам ловит общие названия: в нише про города «Detroit», «Pittsburgh», «Miami» есть и у хоккея,
# и у местных новостей. Поэтому канал проверяется ещё тремя способами:
# 1) какая доля ЕГО роликов вообще про тему ниши (ядро — слова, которые у конкурента повторяются);
# 2) нет ли у него своей главной темы, которой у конкурента нет совсем («hockey», «nhl»);
# 3) категория YouTube: «Спорт» или «Игры» не бывают нишей документалок о городах.
CAT_RU = {"Education": "Образование", "Science & Technology": "Наука и техника", "Howto & Style": "Хобби и стиль",
          "Autos & Vehicles": "Авто", "News & Politics": "Новости и политика", "People & Blogs": "Люди и блоги",
          "Entertainment": "Развлечения", "Comedy": "Юмор", "Gaming": "Игры", "Music": "Музыка",
          "Film & Animation": "Фильмы и анимация", "Sports": "Спорт", "Travel & Events": "Путешествия",
          "Pets & Animals": "Животные", "Nonprofits & Activism": "Некоммерческие организации"}
# категории, которые сами по себе задают тематику: если у ниши такой нет, канал из другой ниши
STRONG_CATS = {"Sports", "Gaming", "Music", "News & Politics", "Pets & Animals", "Autos & Vehicles", "Comedy",
               "Howto & Style"}
CATCHALL = {"People & Blogs", "Entertainment"}
GENERIC = {"video", "videos", "new", "best", "top", "how", "why", "what", "the", "this", "that", "with", "from",
           "you", "your", "are", "was", "will", "all", "every", "most", "first", "last", "part", "full", "official",
           "shorts", "short", "live", "vs", "day", "time", "year", "years", "things", "минут", "видео", "новый",
           "лучшие", "самые", "почему", "как", "что", "это", "все", "часть", "выпуск"}


class TopicGate:
    def __init__(self, comp_titles, comp_extra, tokenize):
        self.tok = tokenize
        n = max(1, len(comp_titles))
        cnt = Counter(s for t in comp_titles for s in {stem(w) for w in tokenize(t)})
        need = max(2, round(0.06 * n))
        extra = {stem(w) for t in comp_extra for w in tokenize(t)}
        self.core = {s for s, c in cnt.items() if c >= need} | extra
        self.core -= {stem(g) for g in GENERIC}
        self.known = set(cnt) | extra  # всё, что у конкурента встречалось хотя бы раз

    def check(self, titles):
        ts = [t for t in titles[:40] if t]
        if len(ts) < 5:
            return None
        sets = [{stem(w) for w in self.tok(t)} for t in ts]
        cov = sum(1 for s in sets if s & self.core) / len(ts)
        cnt = Counter(x for s in sets for x in s)
        generic = {stem(g) for g in GENERIC}
        foreign = [(s, c / len(ts)) for s, c in cnt.most_common(12)
                   if c / len(ts) >= 0.25 and s not in self.known and s not in generic and len(s) >= 3]
        return {"coverage": round(cov, 2), "foreign": [s for s, _ in foreign[:3]],
                "foreign_share": round(max([f for _, f in foreign], default=0), 2)}

    @staticmethod
    def verdict(g, cand_cat=None, niche_cats=()):
        """Причина, по которой канал не из ниши, или None."""
        if cand_cat and niche_cats and cand_cat in STRONG_CATS and cand_cat not in niche_cats:
            main = Counter(niche_cats).most_common(1)[0][0]
            # «Люди и блоги» и «Развлечения» авторы ставят по умолчанию — тогда категория мало что говорит,
            # и отсеиваем по ней только явно другое (спорт, игры, музыка) или если и по названиям тема не та
            if cand_cat in ("Sports", "Gaming", "Music") or main not in CATCHALL or (g and g["coverage"] < 0.35):
                return f"категория YouTube «{CAT_RU.get(cand_cat, cand_cat)}», у ниши — «{CAT_RU.get(main, main)}»"
        if not g:
            return None
        if g["coverage"] < 0.2:
            return f"по теме ниши только {round(100 * g['coverage'])}% его роликов"
        if g["foreign"] and g["coverage"] < 0.35 and g["foreign_share"] >= 0.4:
            return "у канала своя главная тема: «" + "», «".join(g["foreign"]) + "»"
        return None
