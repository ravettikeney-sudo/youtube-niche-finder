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
