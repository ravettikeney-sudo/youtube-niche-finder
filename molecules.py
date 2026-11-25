"""
Разбор сценария на молекулы.
Сценарий сравнивается с тем, как на самом деле звучат ролики:
  • ваши хиты (что заходило у вас),
  • ваши провалы (что тянуло вниз),
  • топ-ролики по той же теме у других каналов.
Текст роликов — из субтитров, а если их нет — распознаём речь первых минут. Дальше: хук первых 30 секунд, обещание, вопросы, «крючки» удержания, вода, длинные фразы,
мёртвые зоны без единого крючка, призывы, концовка. Каждый вывод — с конкретной фразой и её местом в тексте.
"""
import base64
from collections import Counter
import io
import re
import time
import statistics
import zipfile
from concurrent.futures import ThreadPoolExecutor, wait

from langdetect import DetectorFactory, LangDetectException
from langdetect.detector_factory import PROFILES_DIRECTORY

import analyzer as A
import niche
import thumbs
import asr
import video

_language_detector = DetectorFactory()
_language_detector.load_profile(PROFILES_DIRECTORY)
_language_detector.set_seed(0)

# ---------------------------------------------------------------- приёмы в тексте
GREET = re.compile(r"(всем привет|привет,? (друзья|ребята|всем|народ)|здравствуйте|добро пожаловать (на|в) (канал|наш|мой)|"
                   r"с вами (снова )?(канал|я\b|на связи)|меня зовут|на (моём|нашем) канале|"
                   r"hello (everyone|guys|friends)|hey (guys|everyone|friends)|welcome back|welcome to (my|the|our) channel|my name is)", re.I)
REHOOK = re.compile(r"\b(?:но это (?:ещё|еще) не вс[ёе]|но|однако|самое (интересное|главное|страшное|странное)|а вот|оказалось|оказывается|"
                    r"секрет|правда в том|запомни|внимание|сейчас (покажу|расскажу)|дальше (будет|хуже|интереснее)|к этому (вернёмся|вернемся)|"
                    r"в конце|есть нюанс|и тут|и вот тут|никто не|почему|представь(?:те)?|"
                    r"but|however|here['’]?s the (thing|catch)|it turns out|the secret|wait|stick around|by the end|nobody|imagine|and then)\b", re.I)
FILLER = re.compile(r"(?<![\w-])(как бы|в общем|короче|на самом деле|типа|собственно|в принципе|так сказать|ну вот|вот так|"
                    r"basically|kind of|sort of|you know|i mean)(?![\w-])", re.I)
YOU = re.compile(r"(?<![\w-])(ты|тебе|тебя|тобой|тво[йяёюи]|тво(?:его|ему|им|их)|вы|вам|вас|вами|ваш(?:а|е|у|и|его|ему|им|их)?|"
                 r"you|your|yours|yourself|yourselves)(?![\w-])", re.I)
STORY = re.compile(r"(однажды|представь|представьте|история|когда я|в (тот|этот) момент|лет назад|в \d{4} году|"
                   r"one day|imagine|story|when i|years ago|in \d{4}\b|on an? (\w+ )?(morning|night|day|evening)|just (bought|lost|found|got))", re.I)
NUM = re.compile(r"\b\d[\d\s.,]*\b|\b(одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|двадцать|тридцать|сорок|пятьдесят|"
                 r"сто|двести|триста|тысяч[аиу]?|миллион\w*|миллиард\w*|процент\w*|"
                 r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|"
                 r"eighty|ninety|dozen|half|hundred|thousand|million|billion|percent)\b", re.I)
TOPIC_STOP = A.STOP | niche.GENERIC | {
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million", "billion", "percent",
    "тысяч", "миллион", "миллиард", "процент", "imagine", "look", "take", "watch", "использовать",
}
TOPIC_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


# ---------------------------------------------------------------- файл → текст
def extract_text(name, b64):
    raw = base64.b64decode(b64.split(",", 1)[-1])
    if len(raw) > 3_000_000:
        raise ValueError("Файл больше 3 МБ — для сценария это слишком много.")
    ext = (name or "").lower().rsplit(".", 1)[-1]
    if ext == "docx" or raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
        except Exception:
            raise ValueError("Не удалось открыть .docx. Сохраните сценарий как .txt или вставьте текстом.")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", " ", xml)
        txt = re.sub(r"<[^>]+>", "", xml)
        import html
        return html.unescape(txt).strip()
    if ext == "pdf" or raw[:4] == b"%PDF":
        raise ValueError("PDF не поддерживается — сохраните сценарий как .docx или .txt.")
    for enc in ("utf-8-sig", "cp1251", "utf-16"):
        try:
            txt = raw.decode(enc)
            break
        except Exception:
            continue
    else:
        txt = raw.decode("utf-8", "ignore")
    if ext == "rtf" or txt.startswith("{\\rtf"):
        txt = re.sub(r"\\'([0-9a-f]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1251", "ignore"), txt)
        txt = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", txt)
    if ext in ("srt", "vtt") or re.search(r"\d\d:\d\d:\d\d[,.]\d+ -->", txt):
        txt = "\n".join(l for l in txt.splitlines() if l.strip() and not re.match(r"^(\d+|WEBVTT.*|[\d:.,]+ --> [\d:.,]+.*)$", l.strip()))
    return txt.strip()


def sentences(text):
    """Сценарий → предложения с номером абзаца. Ремарки в скобках ([кадр], (музыка)) не читаются вслух — выкидываем."""
    out = []
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-ZА-ЯЁ\-—•\d])", text) if p.strip()]
    for pi, para in enumerate(paras):
        para = re.sub(r"\[[^\]]{0,200}\]|\((?:кадр|музыка|пауза|титр|звук|b-?roll|сцена|монтаж|видео|фото)[^)]{0,200}\)", " ", para, flags=re.I)
        para = re.sub(r"^(автор|ведущий|голос|диктор|speaker|host)\s*:\s*", "", para, flags=re.I)
        for s in re.split(r"(?<=[.!?…؟。！？])\s+(?=[«\"\w—-])", para):
            s = re.sub(r"\s+", " ", s).strip(" -—•")
            if len(s.split()) >= 2 or s.endswith(("?", "!")):
                out.append({"n": len(out) + 1, "para": pi + 1, "text": s})
    return out


def timed(sents, wpm):
    t = 0.0
    for s in sents:
        s["t"] = round(t, 1)
        t += word_count(s["text"]) / wpm * 60
    return t


def word_count(text):
    return sum(any(ch.isalnum() for ch in w) for w in text.split())


# ---------------------------------------------------------------- признаки
def opening(items, seconds, wpm=None):
    """Текст, который помещается в отведённое время при выбранном темпе."""
    chunks = []
    for start, text in items:
        if start >= seconds:
            break
        if wpm is not None:
            text = " ".join(text.split()[:max(0, int((seconds - start) * wpm / 60))])
        chunks.append(text)
    return " ".join(chunks).strip()


def feats(items, dur, title="", *, lang="en", partial=False, wpm=None):
    """items — [(секунда, текст)]. Признаки одинаковые для сценария и для субтитров ролика."""
    if not items or dur <= 0:
        return None
    text = " ".join(t for _, t in items).replace("’", "'")
    words = max(1, word_count(text))
    mins = dur / 60
    first15 = opening(items, 15, wpm)
    first20 = opening(items, 20, wpm)
    first30 = opening(items, 30, wpm)
    tw = set(_stems(title))
    events = [s for s, t in items if _event(t)]
    gaps = [b - a for a, b in zip([0] + events, events + [dur])]
    promise = next((s for s, t in items if video.PROMISE.search(t) and s < min(240, dur * 0.5)), None)
    cta = [s for s, t in items if video.CTA.search(t)]
    outro = next((s for s, t in items if video.OUTRO.search(t) and s > dur * 0.7), None)
    f = {
        "dur": round(dur), "words": words,
        "greet": bool(GREET.search(first15)),
        "q20": bool(re.search(r"[?؟？]", first20)),
        "num30": bool(NUM.search(first30)),
        "topic15": len(tw & set(_stems(first15))) >= min(2, len(tw)) if tw else None,
        "promise_at": promise,
        "q_pm": round(len(re.findall(r"[?؟？]", text)) / mins, 2),
        "rehook_pm": round(len(REHOOK.findall(text)) / mins, 2),
        "you_p100": round(100 * len(YOU.findall(text)) / words, 2),
        "filler_p100": round(100 * len(FILLER.findall(text)) / words, 2),
        "num_pm": round(len(NUM.findall(text)) / mins, 2),
        "story": bool(STORY.search(first30)),
        "max_gap": round(max(gaps)) if gaps else None,
        "cta_first": round(cta[0] / dur, 2) if cta else None, "cta_n": len(cta),
        "outro_len": round(dur - outro) if outro else None,
        "first30": first30[:500],
    }
    if partial:
        for k in ("q_pm", "rehook_pm", "you_p100", "filler_p100", "num_pm", "max_gap", "outro_len"):
            f[k] = None
    if lang not in ("en", "ru"):
        for k in ("greet", "promise_at", "rehook_pm", "you_p100", "filler_p100", "max_gap", "story", "outro_len"):
            f[k] = None
    return f


# какой признак, как сравнивать, как назвать
SPEC = [
    ("greet", "bool_low", "Приветствие в первые 15 секунд", "в начале здороваются / представляются"),
    ("q20", "bool_high", "Вопрос зрителю в первые 20 секунд", "сразу задают вопрос"),
    ("num30", "bool_high", "Цифра или конкретика в первые 30 секунд", "в начале звучит цифра"),
    ("topic15", "bool_high", "Тема из названия звучит в первые 15 секунд", "тема названа сразу"),
    ("promise_at", "early", "Обещание «что будет в ролике»", "обещание звучит рано"),
    ("q_pm", "high", "Вопросов в минуту", "вопросов в минуту"),
    ("rehook_pm", "high", "«Крючков» в минуту (но / оказалось / самое интересное…)", "крючков в минуту"),
    ("you_p100", "high", "Обращений к зрителю на 100 слов (ты / вы)", "обращений к зрителю на 100 слов"),
    ("num_pm", "high", "Цифр и фактов в минуту", "цифр в минуту"),
    ("filler_p100", "low", "Воды на 100 слов (короче / в общем / как бы…)", "слов-паразитов на 100 слов"),
    ("max_gap", "low", "Самый долгий кусок без единого крючка, сек", "секунд без крючка"),
    ("dur", "mid", "Длина, сек", "длина"),
]


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _share(xs):
    xs = [x for x in xs if x is not None]
    return sum(1 for x in xs if x) / len(xs) if xs else None


def agg(fs, minimum=1):
    out = {}
    for k, kind, *_ in SPEC:
        vals = [f[k] for f in fs if f.get(k) is not None]
        out[k] = (_share(vals) if kind.startswith("bool") else _med(vals)) if len(vals) >= minimum else None
    return out


def fmt_v(k, v):
    if v is None:
        return "—"
    if k in ("greet", "q20", "num30", "topic15"):
        return f"{round(100 * v)}%" if isinstance(v, float) and not isinstance(v, bool) else ("да" if v else "нет")
    if k in ("promise_at", "max_gap", "dur"):
        v = int(v)
        return f"{v // 60}:{v % 60:02d}"
    return str(round(v, 1)).replace(".", ",")


def closer(me, good, bad):
    """Ближе сценарий к «хорошим» или к «плохим»? True / False / None."""
    if me is None or good is None or bad is None:
        return None
    me = float(me)
    g, b = abs(me - good), abs(me - bad)
    return None if abs(g - b) < 1e-9 else g < b


def differs(kind, good, bad):
    """Отличаются ли хиты от провалов по признаку заметно."""
    if good is None or bad is None:
        return False
    if kind.startswith("bool"):
        return abs(good - bad) >= 0.34
    if kind == "mid":
        return abs(good - bad) / max(good, bad, 1) >= 0.25
    return abs(good - bad) / max(abs(good), abs(bad), 0.5) >= 0.3


# ---------------------------------------------------------------- субтитры роликов
def _cap_feats(v, ylang, lang):
    info = v.get("_info") or video.full_info(v["id"], ylang)
    cap = video.captions(info, lang, strict=True)
    if not cap:
        embedded = video.full_info(v["id"], ylang, embedded=True)
        cap = video.captions(embedded, lang, strict=True)
        info = info or embedded
    if not cap:
        v["_dur"] = info.get("duration") or v.get("duration")
        return None
    dur = info.get("duration") or v.get("duration") or cap[-1][0] + 5
    partial = cap[-1][0] < min(0.8 * dur, dur - 45)
    f = feats(cap, dur, info.get("title") or v.get("title"), lang=lang, partial=partial)
    f.update(id=v["id"], title=info.get("title") or v.get("title"), views=info.get("view_count") or v.get("views"),
             channel=info.get("channel") or v.get("channel"), x=v.get("x"), wpm=None if partial else round(f["words"] * 60 / dur),
             src="subs")
    return f


def _asr_feats(v, lang):
    """Распознанное начало подходит для сравнения хуков, а полный текст нужен для метрик всего ролика."""
    r = asr.transcribe(v["id"], lang, v.get("_dur") or v.get("duration"), strict=True)
    if not r:
        return None
    items, win = r["items"], r["window"]
    dur = r["duration"] or v.get("_dur") or v.get("duration") or win
    title = r.get("title") or v.get("title")
    f = feats(items, win, title, lang=lang, partial=win + 1 < dur)
    f.update(dur=round(dur), id=v["id"], title=title, views=v.get("views"), channel=v.get("channel"), x=v.get("x"),
             wpm=round(f["words"] * 60 / win), src="asr", asr_window=round(win), asr_secs=r["secs"])
    return f


def _collect(vs, ylang, lang, budget):
    """→ (признаки роликов с субтитрами, ролики без субтитров)"""
    ex = ThreadPoolExecutor(4)
    fs = [ex.submit(_cap_feats, v, ylang, lang) for v in vs]
    wait(fs, timeout=budget)
    ex.shutdown(wait=False, cancel_futures=True)
    got, miss = [], []
    for v, f in zip(vs, fs):
        if f.done() and not f.cancelled() and f.exception() is None:
            if f.result():
                got.append(f.result())
            else:
                miss.append(v)
    return got, miss


ASR_MAX = 4          # сколько роликов без субтитров распознаём за один разбор
ASR_BUDGET = 9 * 60  # и не дольше этого


# ---------------------------------------------------------------- главный разбор
def run(text, channel_url, title="", report=None, prog=lambda p, m: None):
    text = (text or "").strip()
    m = re.match(r"^\s*(название|заголовок|тема|title)\s*[:—-]\s*(.+)$", text.split("\n", 1)[0], re.I)
    if m:      # строка «Название: …» — это не речь, а название ролика
        title = title or m.group(2).strip()
        text = text.split("\n", 1)[1].strip() if "\n" in text else ""
    sents = sentences(text)
    if len(sents) < 5 or len(text.split()) < 60:
        raise ValueError("Сценарий слишком короткий — нужно хотя бы 60 слов, чтобы было что разбирать.")
    try:
        detector = _language_detector.create()
        detector.append(" ".join(s["text"] for s in sents)[:10000])
        lang = video.language_code(detector.detect())
    except LangDetectException:
        raise ValueError("Не удалось определить язык сценария. Вставьте текст, который читает диктор.") from None
    ylang = niche.ylang_for(lang)
    given_title = (title or "").strip()      # название, которое дал автор (или строка «Название:»)
    title = given_title or sents[0]["text"][:90]   # только для заголовка отчёта

    prog(8, "Открываю ваш канал…")
    base = A.resolve_channel_base(channel_url) if channel_url else None
    if not base:
        raise ValueError("Не удалось открыть ваш канал. Проверьте ссылку.")
    ch, vs = thumbs.channel_videos(base, 60)
    long = [v for v in vs if A.video_format(v, "videos") == "long" and v.get("views") is not None]
    aged = [v for v in long if v.get("age") is not None and v["age"] >= 7]
    med = statistics.median([v["views"] for v in aged]) if len(aged) >= 3 else None
    # хит/провал — относительно соседних по времени роликов (±5), а не всего канала:
    # иначе у выросшего канала все старые ролики — «провалы», а все новые — «хиты»
    for i, v in enumerate(aged):
        nb = [x["views"] for x in aged[max(0, i - 5):i] + aged[i + 1:i + 6] if x["views"]]
        m = statistics.median(nb) if len(nb) >= 3 else med
        v["x"] = round(v["views"] / m, 2) if m else None
    ranked = sorted([v for v in aged if v.get("x")], key=lambda v: -v["x"])
    hits = [v for v in ranked if v["x"] >= 1.2][:5]
    flops = [v for v in reversed(ranked) if v["x"] <= 0.8][:4]

    prog(18, "Ищу топ-ролики на ту же тему…")
    comp, q, comp_rejected = competitors(given_title, text, ch, long, ylang, report)

    prog(30, f"Читаю субтитры: {len(hits)} ваших хитов, {len(flops)} провалов, {len(comp)} топ-роликов по теме…")
    hits_f, hits_m = _collect(hits, ylang, lang, 120)
    prog(45, "Читаю субтитры провалов…")
    flops_f, flops_m = _collect(flops, ylang, lang, 90)
    prog(55, "Читаю субтитры топ-роликов по теме…")
    comp_f, comp_m = _collect(comp, ylang, lang, 90)

    # ролики без субтитров — распознаём речь (дольше). Сначала там, где не хватает сравнения.
    asr_n = 0
    queue = []
    for f_list, m_list in ((hits_f, hits_m), (flops_f, flops_m), (comp_f, comp_m)):
        need = max(0, 3 - len(f_list))
        queue += [(f_list, v) for v in m_list[:need]]
    for f_list, m_list in ((hits_f, hits_m), (comp_f, comp_m), (flops_f, flops_m)):
        need = max(0, 3 - len(f_list))
        queue += [(f_list, v) for v in m_list[need:]]
    queue = queue[:ASR_MAX] if asr.available() else []
    t0 = time.time()
    for i, (f_list, v) in enumerate(queue):
        if time.time() - t0 > ASR_BUDGET:
            break
        prog(58 + int(25 * i / max(1, len(queue))),
             f"У ролика нет субтитров — распознаю речь ({i + 1} из {len(queue)}), это дольше…")
        try:
            f = _asr_feats(v, lang)
        except Exception:
            f = None
        if f:
            f_list.append(f)
            asr_n += 1
    no_text = len(hits) + len(flops) + len(comp) - len(hits_f) - len(flops_f) - len(comp_f)

    prog(85, "Разбираю сценарий на молекулы…")
    own_wpm = [f["wpm"] for f in hits_f + flops_f if f.get("wpm")]
    comp_wpm = [f["wpm"] for f in comp_f if f.get("wpm")]
    if own_wpm:
        wpm, wpm_src = _med(own_wpm), "по вашим роликам"
    elif comp_wpm:
        wpm, wpm_src = _med(comp_wpm), "по роликам по теме"
    else:
        wpm, wpm_src = (140 if lang == "ru" else 150), "средний темп речи"
    wpm = round(max(110, min(200, wpm)))
    dur = timed(sents, wpm)
    items = [(s["t"], s["text"]) for s in sents]
    me = feats(items, dur, given_title, lang=lang, wpm=wpm)

    H, F, C = agg(hits_f), agg(flops_f), agg(comp_f)
    rows = []
    for k, kind, name, short in SPEC:
        if k in ("topic15",) and not given_title:
            continue
        vals = [v for v in (me.get(k), H.get(k), F.get(k), C.get(k)) if v is not None]
        if not vals or (k in ("greet", "q20", "num30") and not any(vals)):
            continue          # никто так не делает — строка ничего не говорит
        rows.append({"key": k, "kind": kind, "name": name, "me": me[k], "hits": H.get(k), "flops": F.get(k), "comp": C.get(k),
                     "me_t": fmt_v(k, me[k]), "hits_t": fmt_v(k, H.get(k)), "flops_t": fmt_v(k, F.get(k)), "comp_t": fmt_v(k, C.get(k))})

    good, risk, compx, matched = [], [], [], []
    for r in rows:
        k, kind = r["key"], r["kind"]
        enough = all(sum(f.get(k) is not None for f in group) >= 2 for group in (hits_f, flops_f))
        if enough and r["me"] is not None and differs(kind, r["hits"], r["flops"]):
            c = closer(r["me"], r["hits"], r["flops"])
            matched.append(0.5 if c is None else int(c))
            line = f"{r['name']}: у ваших хитов {r['hits_t']}, у провалов {r['flops_t']}, в сценарии {r['me_t']}."
            if c is True:
                good.append({"key": k, "text": line})
            elif c is False:
                risk.append({"key": k, "text": line})
        if r["comp"] is not None and r["me"] is not None and sum(f.get(k) is not None for f in comp_f) >= 2 and differs(kind, r["comp"], r["me"]) \
                and not (r["hits"] is not None and not differs(kind, r["hits"], r["me"])):
            cv, mv = float(r["comp"]), float(r["me"])
            better = {"bool_high": cv > mv, "bool_low": cv < mv, "high": cv > mv, "low": cv < mv, "early": cv < mv, "mid": False}[kind]
            if better:
                compx.append({"key": k, "text": f"{r['name']}: у топ-роликов по теме {r['comp_t']}, в сценарии {r['me_t']}."})

    score = round(100 * statistics.mean(matched)) if len(matched) >= 3 else None
    basis_hits = agg(hits_f, minimum=2)
    supported = lang in ("en", "ru")
    mols = molecules(sents, me, basis_hits, C, dur, wpm, given_title) if supported else []
    base_gap = max(20, min(45, dur * 0.2))
    gap = max(base_gap, basis_hits["max_gap"]) if basis_hits["max_gap"] else base_gap * 1.3

    def ex(fs, n=3):
        return [{"id": f["id"], "title": f["title"], "channel": f.get("channel"), "views": f.get("views"), "x": f.get("x"),
                 "first30": f["first30"], "src": f.get("src")} for f in sorted(fs, key=lambda f: -(f.get("x") or f.get("views") or 0))[:n]]
    timeline = [{"n": s["n"], "t": s["t"], "q": bool(re.search(r"[?؟？]", s["text"])),
                 "num": bool(NUM.search(s["text"])), "hook": supported and bool(REHOOK.search(s["text"])),
                 "cta": supported and bool(video.CTA.search(s["text"])),
                 "promise": supported and bool(video.PROMISE.search(s["text"].replace("’", "'")))} for s in sents]
    prog(100, "Готово")
    return {"type": "molecules", "title": title, "channel": {"title": ch.get("title"), "url": base, "median": med},
            "lang": lang, "wpm": wpm, "est_duration": round(dur), "sentences": len(sents), "words": me["words"],
            "score": score, "me": me, "rows": rows, "good": good, "risk": risk, "comp_diff": compx, "molecules": mols,
            "timeline": timeline, "dead": dead_zones(sents, dur, gap) if supported else [],
            "hits_ex": ex(hits_f), "flops_ex": ex(flops_f, 2), "comp_ex": ex(comp_f),
            "basis": {"wpm_src": wpm_src, "query": q, "comp_rejected": comp_rejected, "title_given": bool(given_title),
                      "own": len(hits_f) + len(flops_f), "score_metrics": len(matched), "language_checks": supported},
            "counts": {"hits": len(hits_f), "flops": len(flops_f), "comp": len(comp_f),
                       "asr": asr_n, "no_text": max(0, no_text)},
            "script_head": [opening(items, 30, wpm)]}


def _topic_words(text):
    return [w for w in TOPIC_WORD.findall((text or "").casefold())
            if len(w) >= 3 and w not in TOPIC_STOP and not NUM.fullmatch(w)]


def _topic_stem(w):
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 4:
        w = w[:-1]
    return w[:5]


def _stems(text):
    return [_topic_stem(w) for w in _topic_words(text)]


def topic_query(given_title, text, own_vocab):
    """Поиск по названию или по частым словам всего сценария с учётом тематики канала."""
    title_terms = {_topic_stem(w): w for w in _topic_words(given_title)}
    if len(title_terms) >= 2:
        return list(title_terms.values())[:6]
    words = {_topic_stem(w): w for w in _topic_words(text)}
    counts = Counter(_stems(text))
    ranked = sorted(counts, key=lambda w: counts[w] * (2 if own_vocab.get(w) else 1), reverse=True)
    return (list(title_terms.values()) + [words[w] for w in ranked if counts[w] >= 2 and w not in title_terms])[:4]


def competitors(title, text, channel, own, ylang, report):
    own_ids = {v["id"] for v in own}
    own_titles = [v.get("title") for v in own]
    own_vocab = Counter(w for t in own_titles for w in set(_stems(t)))
    tagged = set(_stems(" ".join(channel.get("tags") or [])))
    profile = tagged | set(dict(own_vocab.most_common(60)))
    core = tagged | {w for w in profile if own_vocab[w] >= 2}
    words = topic_query(title, text, own_vocab)
    query = " ".join(words)
    topic = set(_stems(query))
    if len(topic) < 2 or len(profile) < 2:
        return [], query, 0

    try:
        found = video._niche_hits(report, ylang) if report else []
    except (KeyError, TypeError):
        found = []

    def is_other(v):
        return (v["id"] not in own_ids and v.get("channel") != channel.get("title")
                and (not channel.get("id") or v.get("channel_id") != channel["id"]))

    for terms in dict.fromkeys((query, " ".join(words[:3]))):
        try:
            results = A.search(terms, 20, lang=ylang) or []
        except Exception:
            results = []
        found += [dict(e, views=e.get("view_count")) for e in results]
        if len({v["id"] for v in found if is_other(v) and len(topic & set(_stems(v.get("title")))) >= 2}) >= 6:
            break

    candidates = {v["id"]: v for v in found if is_other(v)}
    titles = {vid: set(_stems(v.get("title"))) for vid, v in candidates.items()}
    ranked = sorted((v for vid, v in candidates.items() if topic & titles[vid]),
                    key=lambda v: (len(topic & titles[v["id"]]), len(profile & titles[v["id"]]), v.get("views") or 0), reverse=True)
    durations = [v["duration"] for v in own if v.get("duration")]
    min_dur = max(90, 0.4 * statistics.median(durations)) if len(durations) >= 3 else 90
    ranked = [v for v in ranked if A.video_format(v) not in ("shorts", "live") and (not v.get("duration") or v["duration"] >= min_dur)]

    def inspect(v):
        return video.full_info(v["id"], ylang)

    ex = ThreadPoolExecutor(4)
    checks = [(v, ex.submit(inspect, v)) for v in ranked[:12]]
    wait([f for _, f in checks], timeout=75)
    ex.shutdown(wait=False, cancel_futures=True)
    verified = [(v, f.result()) for v, f in checks if f.done() and not f.cancelled() and f.exception() is None]

    distinct = {"Gaming", "Sports", "Music"}
    own_categories = set()
    if any(distinct.intersection(info.get("categories") or []) for _, info in verified):
        with ThreadPoolExecutor(2) as ex:
            own_categories = {c for info in ex.map(inspect, own[:2]) for c in (info.get("categories") or [])}

    matched = []
    for v, info in verified:
        duration = info.get("duration") or v.get("duration")
        if (not info or A.video_format(info) in ("shorts", "live") or (duration or 0) < min_dur
                or (channel.get("id") and info.get("channel_id") == channel["id"])
                or (distinct.intersection(info.get("categories") or []) - own_categories)):
            continue
        evidence = titles[v["id"]] | set(_stems(" ".join(info.get("tags") or []) + " " + (info.get("description") or "")[:400]))
        niche_hits = evidence & profile
        if len(topic & evidence) < 2 or len(niche_hits) < 2 or not (niche_hits - topic or niche_hits & core):
            continue
        matched.append(dict(v, duration=duration, _info=info))
    return matched[:6], query, len(candidates) - len(matched)


def _event(t):
    return "?" in t or REHOOK.search(t) or NUM.search(t) or video.TRANS.search(t) or re.search(r"\b(первое|второе|третье|четв[её]ртое|пятое|правило|шаг|ошибка|причина)\b", t, re.I)


def dead_zones(sents, dur, min_len=None):
    min_len = min_len or max(20, min(45, dur * 0.2))
    """Куски сценария дольше min_len секунд без вопроса, крючка и цифры — тут зритель уходит."""
    out, start = [], None
    for i, s in enumerate(sents):
        ev = _event(s["text"])
        if ev:
            if start is not None:
                a, b = sents[start], sents[i - 1]
                end_t = s["t"]
                if end_t - a["t"] >= min_len:
                    out.append({"from_n": a["n"], "to_n": b["n"], "from_t": a["t"], "to_t": end_t, "sec": round(end_t - a["t"]),
                                "quote": a["text"][:160]})
            start = None
        elif start is None:
            start = i
    if start is not None and dur - sents[start]["t"] >= min_len:
        a = sents[start]
        out.append({"from_n": a["n"], "to_n": sents[-1]["n"], "from_t": a["t"], "to_t": dur, "sec": round(dur - a["t"]), "quote": a["text"][:160]})
    return out[:8]


def _mm(t):
    t = int(t or 0)
    return f"{t // 60}:{t % 60:02d}"


def molecules(sents, me, H, C, dur, wpm, title):
    """Пофразовые замечания: где именно, какая фраза, что не так, как переделать."""
    out = []

    def add(level, where, quote, problem, fix):
        out.append({"level": level, "where": where, "quote": quote, "problem": problem, "fix": fix})

    head = [s for s in sents if s["t"] < 30]
    first = sents[0]
    hits_g = H.get("greet")
    if GREET.search(" ".join(s["text"] for s in sents if s["t"] < 15)) and not (hits_g is not None and hits_g >= 0.5):
        g = next(s for s in sents if s["t"] < 15 and GREET.search(s["text"]))
        add("bad" if hits_g is not None else "warn", f"Фраза {g['n']} · {_mm(g['t'])}", g["text"],
            "Ролик начинается с приветствия — первые секунды уходят не на тему."
            + ((" Ни один из ваших хитов так не начинается." if hits_g == 0 else f" У ваших хитов так начинается только {round(100 * hits_g)}%.") if hits_g is not None else ""),
            "Уберите приветствие или перенесите после хука. Первая фраза — самый сильный факт, вопрос или обещание ролика.")
    hooky = max(H.get("q20") or 0, H.get("num30") or 0) if H.get("q20") is not None else None
    if not me["q20"] and not me["num30"] and not me["story"] and (hooky is None or hooky >= 0.5):
        add("warn", f"Фраза {first['n']} · 0:00", first["text"],
            "В первые 20–30 секунд нет ни вопроса, ни цифры, ни истории — зрителю не за что зацепиться."
            + (f" Ваши хиты так начинаются в {round(100 * hooky)}% случаев." if hooky is not None else ""),
            "Начните с вопроса («А что если…?»), с конкретной цифры или с момента из середины истории.")
    elif not me["q20"] and (H.get("q20") or 0) >= 0.5:
        add("warn", f"Фраза {first['n']} · 0:00", first["text"],
            f"Ваши хиты в {round(100 * H['q20'])}% случаев задают вопрос в первые 20 секунд, а сценарий — нет.",
            "Добавьте в первые 2–3 фразы вопрос, на который зритель захочет узнать ответ.")
    if me["promise_at"] is None:
        add("warn", "Первые 30 секунд", " ".join(s["text"] for s in head)[:200],
            "Нет обещания: зритель не знает, что получит, если досмотрит.",
            "Добавьте фразу вроде «к концу ролика вы узнаете…» / «сейчас покажу…» в первые 20–30 секунд.")
    elif me["promise_at"] > 40 and (H.get("promise_at") is None or me["promise_at"] > (H["promise_at"] or 0) + 15):
        p = next(s for s in sents if s["t"] >= me["promise_at"] - 0.1)
        add("warn", f"Фраза {p['n']} · {_mm(p['t'])}", p["text"],
            f"Обещание звучит только на {_mm(me['promise_at'])}" + (f", а у ваших хитов — около {_mm(H['promise_at'])}." if H.get("promise_at") is not None else "."),
            "Перенесите эту фразу в первые 20 секунд.")
    if me["topic15"] is False and title:
        add("warn", "Первые 15 секунд", " ".join(s["text"] for s in sents if s["t"] < 15)[:200],
            "Тема из названия не звучит в первые 15 секунд — зритель пришёл за ней и не слышит её.",
            "Назовите тему (ключевые слова из названия) в первой-второй фразе.")
    base_gap = max(20, min(45, dur * 0.2))
    gap = max(base_gap, H["max_gap"]) if H.get("max_gap") else base_gap * 1.3
    for z in dead_zones(sents, dur, gap):
        add("bad" if z["sec"] >= gap * 1.6 else "warn", f"Фразы {z['from_n']}–{z['to_n']} · {_mm(z['from_t'])}–{_mm(z['to_t'])}", z["quote"],
            f"{z['sec']} {'секунда' if z['sec'] % 10 == 1 and z['sec'] % 100 != 11 else 'секунды' if 2 <= z['sec'] % 10 <= 4 and not 12 <= z['sec'] % 100 <= 14 else 'секунд'} подряд без вопроса, крючка и цифр — здесь удержание проседает.",
            "Вставьте в середину куска крючок: «но самое странное…», «и тут оказалось…», вопрос к зрителю или конкретную цифру.")
    longs = [s for s in sents if len(s["text"].split()) >= 35]
    for s in longs[:4]:
        add("warn", f"Фраза {s['n']} · {_mm(s['t'])}", s["text"][:220] + ("…" if len(s["text"]) > 220 else ""),
            f"{len(s['text'].split())} слов в одном предложении — на слух теряется мысль.",
            "Разбейте на 2–3 коротких предложения.")
    fil = [(s, FILLER.findall(s["text"])) for s in sents]
    fil = [(s, f) for s, f in fil if f]
    if me["filler_p100"] < max(0.4, (H.get("filler_p100") or 0) * 1.5):
        fil = []
    for s, f in sorted(fil, key=lambda sf: -len(sf[1]))[:4]:
        add("warn", f"Фраза {s['n']} · {_mm(s['t'])}", s["text"][:200],
            "Вода: " + ", ".join(sorted({x.lower() if isinstance(x, str) else x[0].lower() for x in f})) + ".",
            "Удалите эти слова — смысл не изменится, а фраза станет плотнее.")
    ctas = [s for s in sents if video.CTA.search(s["text"])]
    if ctas and ctas[0]["t"] < 30:
        add("bad", f"Фраза {ctas[0]['n']} · {_mm(ctas[0]['t'])}", ctas[0]["text"],
            "Просьба подписаться в первые 30 секунд — до того, как зритель получил пользу.",
            "Перенесите призыв после первой сильной части (обычно 30–40% ролика).")
    if len(ctas) >= 4:
        add("warn", "Весь сценарий", f"{len(ctas)} просьб подписаться / поставить лайк",
            "Слишком много призывов — раздражает и выглядит как просьба о помощи.", "Оставьте 1–2 призыва.")
    if me["outro_len"] and me["outro_len"] > 40:
        o = next((s for s in sents if video.OUTRO.search(s["text"]) and s["t"] > dur * 0.7), sents[-1])
        add("warn", f"Фраза {o['n']} · {_mm(o['t'])}", o["text"], f"Прощание длится {me['outro_len']} сек — зрители уходят раньше, ролик теряет досмотр.",
            "Сократите концовку до 10–15 секунд или закончите сразу на сильной мысли.")
    if H.get("you_p100") is not None and me["you_p100"] < H["you_p100"] * 0.5 and H["you_p100"] >= 1.0:
        add("warn", "Весь сценарий", "обращений к зрителю почти нет",
            f"У ваших хитов около {fmt_v('you_p100', H['you_p100'])} обращений «ты / вы» на 100 слов, в сценарии — {fmt_v('you_p100', me['you_p100'])}.",
            "Говорите со зрителем: «представь», «ты наверняка думаешь…», «смотри».")
    if H.get("dur") and dur > H["dur"] * 1.5:
        add("warn", "Длина", f"~{_mm(dur)} при темпе {round(wpm)} слов/мин",
            f"Сценарий выйдет на ~{_mm(dur)}, а ваши хиты идут около {_mm(H['dur'])}.", "Сократите самые слабые части или разбейте на два ролика.")
    elif H.get("dur") and dur < H["dur"] * 0.55:
        add("warn", "Длина", f"~{_mm(dur)} при темпе {round(wpm)} слов/мин",
            f"Сценарий выйдет на ~{_mm(dur)}, а ваши хиты — около {_mm(H['dur'])}.", "Добавьте пример, историю или ещё один факт.")
    order = {"bad": 0, "warn": 1}
    out.sort(key=lambda m: order[m["level"]])
    return out
