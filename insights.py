"""
Дополнительная аналитика ниши — всё считается из уже загруженных списков видео,
без лишних запросов к YouTube.
"""
import re
import math
import statistics
from collections import Counter

# ---------------------------------------------------------------- язык
LATIN_HINTS = {
    "en": {"the", "and", "how", "why", "what", "you", "this", "with", "for", "is", "of", "in", "to"},
    "es": {"el", "la", "los", "las", "que", "por", "como", "para", "una", "del", "con", "es"},
    "pt": {"o", "os", "que", "como", "para", "uma", "do", "da", "com", "não", "você", "é"},
    "de": {"der", "die", "das", "und", "wie", "ist", "mit", "ich", "nicht", "ein", "eine", "warum"},
    "fr": {"le", "la", "les", "et", "comment", "pour", "une", "des", "est", "pourquoi", "avec", "du"},
    "it": {"il", "lo", "gli", "che", "come", "per", "una", "della", "con", "perché", "di", "è"},
    "tr": {"ve", "bir", "bu", "nasıl", "için", "neden", "ile", "çok", "en"},
}
LANG_NAMES = {"ru": "Русский", "uk": "Украинский", "en": "Английский", "es": "Испанский", "pt": "Португальский",
              "de": "Немецкий", "fr": "Французский", "it": "Итальянский", "tr": "Турецкий", "hi": "Хинди",
              "ar": "Арабский", "zh": "Китайский", "ja": "Японский", "ko": "Корейский", "other": "Другой"}


def detect_lang(titles):
    text = " ".join(titles[:40])
    cnt = Counter()
    for ch in text:
        o = ord(ch)
        if 0x400 <= o <= 0x4FF: cnt["cyr"] += 1
        elif 0x900 <= o <= 0x97F: cnt["hi"] += 1
        elif 0x600 <= o <= 0x6FF: cnt["ar"] += 1
        elif 0x3040 <= o <= 0x30FF: cnt["ja"] += 1
        elif 0xAC00 <= o <= 0xD7AF: cnt["ko"] += 1
        elif 0x4E00 <= o <= 0x9FFF: cnt["zh"] += 1
        elif ch.isalpha() and o < 0x250: cnt["lat"] += 1
    if not cnt:
        return "other"
    top = cnt.most_common(1)[0][0]
    if top == "cyr":
        return "uk" if re.search(r"[іїєґ]", text.lower()) else "ru"
    if top == "ja" or (top == "zh" and cnt["ja"]):
        return "ja"
    if top != "lat":
        return top
    words = re.findall(r"[a-zà-ÿıçşğöü]+", text.lower())
    score = {lang: sum(1 for w in words if w in hs) for lang, hs in LATIN_HINTS.items()}
    best = max(score, key=score.get)
    return best if score[best] > 0 else "en"


# ---------------------------------------------------------------- релевантность
def relevance(comp_counter, titles, tokenize):
    """Насколько тематика канала совпадает с конкурентом, 0–100 (косинусная близость слов заголовков)."""
    c = Counter()
    for t in titles:
        c.update(set(tokenize(t)))
    if not c or not comp_counter:
        return 0
    common = set(c) & set(comp_counter)
    dot = sum(c[w] * comp_counter[w] for w in common)
    na = math.sqrt(sum(v * v for v in c.values()))
    nb = math.sqrt(sum(v * v for v in comp_counter.values()))
    return round(min(100, dot / (na * nb) * 300)) if na and nb else 0


# ---------------------------------------------------------------- длина видео
BUCKETS = [(0, 61, "до 1 мин"), (61, 300, "1–5 мин"), (300, 600, "5–10 мин"), (600, 1200, "10–20 мин"),
           (1200, 2400, "20–40 мин"), (2400, 10 ** 9, "40+ мин")]


def duration_stats(channels):
    """Как видео разной длины работают относительно обычных просмотров своего канала."""
    rows = {b[2]: [] for b in BUCKETS}
    for ch in channels:
        med = ch.get("median_views") or 0
        if not med:
            continue
        for e in ch.get("_ents", []):
            d, v = e.get("duration"), e.get("view_count")
            if not d or v is None:
                continue
            for lo, hi, name in BUCKETS:
                if lo <= d < hi:
                    rows[name].append(v / med)
                    break
    out = []
    for _, _, name in BUCKETS:
        r = rows[name]
        out.append({"bucket": name, "videos": len(r),
                    "perf": round(statistics.median(r), 2) if r else None,
                    "hit_share": round(100 * sum(1 for x in r if x >= 2) / len(r)) if r else None})
    good = [o for o in out if o["videos"] >= 20 and o["perf"] is not None]
    best = max(good, key=lambda o: o["perf"])["bucket"] if good else None
    return out, best


# ---------------------------------------------------------------- заголовки
FEATURES = [
    ("number", "Есть число («7 причин», «за 30 дней»)", lambda t: bool(re.search(r"\d", t))),
    ("question", "Вопрос в заголовке", lambda t: "?" in t),
    ("why_how", "Начинается с «Почему / Как / Что»", lambda t: bool(re.match(r"\s*(почему|как|что|зачем|why|how|what|when)\b", t, re.I))),
    ("caps", "Слово ЗАГЛАВНЫМИ буквами", lambda t: bool(re.search(r"\b[A-ZА-ЯЁ]{3,}\b", t))),
    ("contrast", "Сравнение или противопоставление (vs, против, но)", lambda t: bool(re.search(r"\b(vs|versus|против|or|или|but|но)\b", t, re.I))),
    ("negative", "Негатив и запрет (худший, никогда, нельзя)", lambda t: bool(re.search(r"(worst|never|nobody|no one|stop|худш|никогда|нельзя|никто|не делай|ошибк)", t, re.I))),
    ("secret", "Тайна и интрига (секрет, скрыт, правда)", lambda t: bool(re.search(r"(secret|hidden|truth|real reason|секрет|скрыт|правда|тайн|на самом деле)", t, re.I))),
    ("personal", "Личный опыт (я, мы, my, I tried)", lambda t: bool(re.search(r"\b(i|i'm|my|we|я|мой|моя|мы|попробовал)\b", t, re.I))),
    ("colon", "Двоеточие или тире-уточнение", lambda t: bool(re.search(r"[:|—–]", t))),
    ("emoji", "Эмодзи", lambda t: any(ord(c) > 0x2600 for c in t)),
]


def title_stats(channels, tokenize, comp_tokens):
    vids = []
    for ch in channels:
        med = ch.get("median_views") or 0
        if not med:
            continue
        for e in ch.get("_ents", []):
            t, v = e.get("title") or "", e.get("view_count")
            if t and v is not None:
                vids.append((t, v / med >= 2))
    if len(vids) < 20:
        return {"features": [], "hit_words": [], "gaps": [], "len_hit": None, "len_all": None, "base_hit_rate": None}
    base = sum(h for _, h in vids) / len(vids)
    feats = []
    for key, name, fn in FEATURES:
        yes = [h for t, h in vids if fn(t)]
        if len(yes) < 8:
            continue
        rate = sum(yes) / len(yes)
        feats.append({"key": key, "name": name, "share": round(100 * len(yes) / len(vids)),
                      "hit_rate": round(100 * rate), "lift": round(rate / base, 2) if base else None})
    feats.sort(key=lambda f: -(f["lift"] or 0))

    allc, hitc = Counter(), Counter()
    for t, h in vids:
        tk = set(tokenize(t))
        allc.update(tk)
        if h:
            hitc.update(tk)
    nh = max(1, sum(h for _, h in vids))
    words = []
    for w, c in hitc.items():
        if c < 3 or len(w) < 4:
            continue
        lift = (c / nh) / (allc[w] / len(vids))
        if lift >= 1.4:
            words.append({"word": w, "hits": c, "lift": round(lift, 1)})
    words.sort(key=lambda x: -(x["lift"] * math.log(1 + x["hits"])))
    gaps = [w for w in words if w["word"] not in comp_tokens][:12]

    hl = [len(t) for t, h in vids if h]
    return {"features": feats, "hit_words": words[:20], "gaps": gaps,
            "len_hit": round(statistics.median(hl)) if hl else None,
            "len_all": round(statistics.median(len(t) for t, _ in vids)),
            "base_hit_rate": round(100 * base)}


# ---------------------------------------------------------------- доход
SHORTS_RPM = (0.03, 0.1)


def monthly_income(ch, rpm):
    vpd = ch.get("views_per_day")
    if not vpd:
        return None
    ss = (ch.get("shorts_share") or 0) / 100
    lo = rpm[0] * (1 - ss) + SHORTS_RPM[0] * ss
    hi = rpm[1] * (1 - ss) + SHORTS_RPM[1] * ss
    return [round(vpd * 30 * lo / 1000), round(vpd * 30 * hi / 1000)]


# ---------------------------------------------------------------- план входа
def fmt_n(n):
    if n is None:
        return "—"
    for div, s in ((1e6, " млн"), (1e3, " тыс.")):
        if n >= div:
            return f"{n / div:.1f}".replace(".0", "").replace(".", ",") + s
    return str(int(n))


def entry_plan(d):
    n, dur, ttl = d["niche"], d["durations"], d["titles"]
    good = [c for c in d["channels"] if c.get("relevant") and c.get("young") and c["rising_score"] >= 55]
    plan = []
    if good:
        shorts = statistics.median([c.get("shorts_share") or 0 for c in good])
        fmt = "Shorts" if shorts >= 60 else "длинные видео" if shorts <= 25 else "длинные видео + Shorts"
        plan.append(f"Формат: {fmt} — так работают {len(good)} растущих новичков ниши.")
        upw = [c["uploads_per_week"] for c in good if c.get("uploads_per_week")]
        if upw:
            m = statistics.median(upw)
            plan.append(f"Частота: около {str(round(m, 1)).replace('.', ',')} видео в неделю — медиана у растущих молодых каналов.")
        subs = [c["subs"] for c in good if c.get("subs")]
        if subs:
            plan.append(f"Ориентир: растущие новички ниши набрали в среднем {fmt_n(statistics.median(subs))} подписчиков "
                        f"за {round(statistics.median([c['age_days'] for c in good]) / 30)} мес.")
    else:
        plan.append("Внимание: растущих новичков почти нет — заходить имеет смысл только с заметно другим углом подачи или в соседней подниже.")
    if n.get("best_duration"):
        b = next((x for x in dur if x["bucket"] == n["best_duration"]), None)
        if b:
            plan.append(f"Длина: {b['bucket']} — такие видео набирают в {str(b['perf']).replace('.', ',')} раза от обычных просмотров канала, лучше других длин.")
    feats = [f for f in ttl.get("features", []) if f["lift"] and f["lift"] >= 1.2][:3]
    if feats:
        plan.append("Заголовки: " + "; ".join(f"{f['name'].split(' (')[0].lower()} — хиты в {str(f['lift']).replace('.', ',')} раза чаще" for f in feats) + ".")
    if ttl.get("gaps"):
        plan.append("Свободные темы: выстреливают у других каналов ниши, но их нет у конкурента — " + ", ".join(w["word"] for w in ttl["gaps"][:6]) + ".")
    if d.get("searches") and d["keyword_table"]:
        top = d["keyword_table"][0]["keyword"]
        qs = next((x["queries"] for x in d["searches"] if x["keyword"] == top), [])
        if qs:
            plan.append(f"Запросы зрителей: вместе с «{top}» ищут " + ", ".join(f"«{q}»" for q in qs[:4]) + " — готовые формулировки для названий.")
    if d["keyword_table"]:
        k = d["keyword_table"][0]
        plan.append(f"Лучший запрос для старта: «{k['keyword']}» — обычно {fmt_n(k['median_views'])} просмотров, молодых каналов в выдаче {k['young_share']}%.")
    if d["entry_topics"]:
        plan.append(f"Первое видео: возьмите тему хита «{d['entry_topics'][0]['title']}» и сделайте свою версию.")
    if n.get("income_young"):
        lo, hi = n["income_young"]
        plan.append(f"Доход: растущий новичок ниши получает примерно ${fmt_n(lo)}–{fmt_n(hi)} в месяц с рекламы YouTube (грубая оценка).")
    return plan
