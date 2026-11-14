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

import analyzer as A
import niche
import thumbs
import asr
import video

# ---------------------------------------------------------------- приёмы в тексте
GREET = re.compile(r"(всем привет|привет,? (друзья|ребята|всем|народ)|здравствуйте|добро пожаловать (на|в) (канал|наш|мой)|"
                   r"с вами (снова )?(канал|я\b|на связи)|меня зовут|на (моём|нашем) канале|"
                   r"hello (everyone|guys|friends)|hey (guys|everyone|friends)|welcome back|welcome to (my|the|our) channel|my name is)", re.I)
REHOOK = re.compile(r"(\bно\b|однако|самое (интересное|главное|страшное|странное)|а вот|но это (ещё|еще) не вс|оказалось|оказывается|"
                    r"секрет|правда в том|запомни|внимание|сейчас (покажу|расскажу)|дальше (будет|хуже|интереснее)|к этому (вернёмся|вернемся)|"
                    r"в конце|есть нюанс|и тут|и вот тут|никто не|почему\b|представь|"
                    r"\bbut\b|however|here'?s the (thing|catch)|it turns out|the secret|wait|stick around|by the end|nobody|imagine|and then)", re.I)
FILLER = re.compile(r"(?<![\w-])(как бы|в общем|короче|на самом деле|типа|собственно|в принципе|так сказать|ну вот|вот так|"
                    r"basically|kind of|sort of|you know|i mean)(?![\w-])", re.I)
YOU = re.compile(r"(?<![\w-])(ты|тебе|тебя|твой|твоя|твои|вы|вам|вас|ваш|ваша|ваши|you|your)(?![\w-])", re.I)
STORY = re.compile(r"(однажды|представь|представьте|история|когда я|в (тот|этот) момент|лет назад|в \d{4} году|"
                   r"one day|imagine|story|when i|years ago|in \d{4}\b|on an? (\w+ )?(morning|night|day|evening)|just (bought|lost|found|got))", re.I)
NUM = re.compile(r"\b\d[\d\s.,]*\b|\b(одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|двадцать|тридцать|сорок|пятьдесят|"
                 r"сто|двести|триста|тысяч[аиу]?|миллион\w*|миллиард\w*|процент\w*|"
                 r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|"
                 r"eighty|ninety|dozen|half|hundred|thousand|million|billion|percent)\b", re.I)
TSTOP = {"почему", "как", "что", "зачем", "когда", "который", "можно", "нельзя", "этот", "будет", "если", "самый", "самая", "about", "what", "when", "your"}


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
        for s in re.split(r"(?<=[.!?…])\s+(?=[«\"A-ZА-ЯЁ0-9—-])", para):
            s = re.sub(r"\s+", " ", s).strip(" -—•")
            if len(s.split()) >= 2 or s.endswith(("?", "!")):
                out.append({"n": len(out) + 1, "para": pi + 1, "text": s})
    return out


def timed(sents, wpm):
    t = 0.0
    for s in sents:
        s["t"] = round(t, 1)
        t += len(s["text"].split()) / wpm * 60
    return t


# ---------------------------------------------------------------- признаки
def feats(items, dur, title=""):
    """items — [(секунда, текст)]. Признаки одинаковые для сценария и для субтитров ролика."""
    if not items:
        return None
    text = " ".join(t for _, t in items)
    words = max(1, len(text.split()))
    mins = max(dur / 60, 0.3)
    first15 = " ".join(t for s, t in items if s < 15)
    first30 = " ".join(t for s, t in items if s < 30)
    tw = {w for w in re.findall(r"[\wа-яё]{5,}", (title or "").lower()) if w not in TSTOP}
    events = [s for s, t in items if _event(t)]
    gaps = [b - a for a, b in zip([0] + events, events + [dur])]
    promise = next((s for s, t in items if video.PROMISE.search(t) and s < min(240, dur * 0.5)), None)
    cta = [s for s, t in items if video.CTA.search(t)]
    outro = next((s for s, t in items if video.OUTRO.search(t) and s > dur * 0.7), None)
    return {
        "dur": round(dur), "words": words,
        "greet": bool(GREET.search(first15)),
        "q20": any("?" in t for s, t in items if s < 20),
        "num30": bool(NUM.search(first30)),
        "topic15": bool(tw and any(w[:5] in first15.lower() for w in tw)),
        "promise_at": promise,
        "q_pm": round(sum(t.count("?") for _, t in items) / mins, 2),
        "rehook_pm": round(sum(len(REHOOK.findall(t)) for _, t in items) / mins, 2),
        "you_p100": round(100 * len(YOU.findall(text)) / words, 2),
        "filler_p100": round(100 * len(FILLER.findall(text)) / words, 2),
        "num_pm": round(len(NUM.findall(text)) / mins, 2),
        "story": bool(STORY.search(first30)),
        "max_gap": round(max(gaps)) if gaps else None,
        "cta_first": round(cta[0] / dur, 2) if cta else None, "cta_n": len(cta),
        "outro_len": round(dur - outro) if outro else None,
        "first30": first30[:500],
    }


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


def agg(fs):
    out = {}
    for k, kind, *_ in SPEC:
        vals = [f[k] for f in fs if f]
        out[k] = _share(vals) if kind.startswith("bool") else _med(vals)
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


def closer(kind, me, good, bad):
    """Ближе сценарий к «хорошим» или к «плохим»? True / False / None."""
    if me is None or good is None or bad is None:
        return None
    me = float(me)
    if kind.startswith("bool"):
        return abs(me - good) < abs(me - bad)
    return abs(me - good) <= abs(me - bad)


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
    info = video.full_info(v["id"], ylang)
    cap = video.captions(info, lang)
    if not cap:
        v["_dur"] = info.get("duration") or v.get("duration")
        return None
    dur = info.get("duration") or v.get("duration") or cap[-1][0] + 5
    f = feats(cap, dur, info.get("title") or v.get("title"))
    words = sum(len(t.split()) for _, t in cap)
    f.update(id=v["id"], title=info.get("title") or v.get("title"), views=info.get("view_count") or v.get("views"),
             channel=info.get("channel") or v.get("channel"), x=v.get("x"), wpm=round(words / max(dur / 60, .5)),
             src="subs")
    return f


def _asr_feats(v, lang):
    """Ролик без субтитров: распознаём речь в первых минутах. Признаки «в минуту» считаем по распознанному
    отрезку, длину ролика — настоящую."""
    r = asr.transcribe(v["id"], lang, v.get("_dur") or v.get("duration"))
    if not r:
        return None
    items, win = r["items"], r["window"]
    dur = r["duration"] or v.get("_dur") or v.get("duration") or win
    title = r.get("title") or v.get("title")
    f = feats(items, win, title)
    words = sum(len(t.split()) for _, t in items)
    f.update(dur=round(dur), id=v["id"], title=title, views=v.get("views"), channel=v.get("channel"), x=v.get("x"),
             wpm=round(words / max(win / 60, .5)), src="asr", asr_window=round(win), asr_secs=r["secs"])
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
    cyr = len(re.findall(r"[а-яё]", text.lower()))
    lang = "ru" if cyr > len(re.findall(r"[a-z]", text.lower())) else "en"
    ylang = niche.ylang_for(lang)
    given_title = (title or "").strip()      # название, которое дал автор (или строка «Название:»)
    title = given_title or sents[0]["text"][:90]   # только для заголовка отчёта

    prog(8, "Открываю ваш канал…")
    base = A.resolve_channel_base(channel_url) if channel_url else None
    if not base:
        raise ValueError("Не удалось открыть ваш канал. Проверьте ссылку.")
    ch, vs = thumbs.channel_videos(base, 60)
    long = [v for v in vs if (not v.get("duration") or v["duration"] >= 61) and v.get("views") is not None]
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
    comp = []
    if report:
        try:
            comp = [h for h in video._niche_hits(report, ylang) if h.get("channel") != ch.get("title")][:5]
        except Exception:
            comp = []
    own_vocab = Counter(st for v in long for st in _stems(v.get("title") or ""))
    q, q_words = topic_query(given_title, text, own_vocab, lang, [v.get("title") or "" for v in long])
    own_durs = [v["duration"] for v in long if v.get("duration")]
    min_dur = max(90, 0.4 * statistics.median(own_durs)) if len(own_durs) >= 3 else 90
    comp_rejected = 0
    if len(comp) < 3 and q:
        words = q_words
        res = []
        # поиск YouTube иногда отвечает пусто — повторяем, потом пробуем запрос короче
        for qq in (q, q, " ".join(words[:3]) if len(words) > 3 else None):
            if not qq:
                continue
            try:
                res = A.search(qq, 20, "", ylang) or []
            except Exception:
                res = []
            if res:
                break
            time.sleep(2)
        # тот же формат: ролики не короче 40% от обычной длины ваших (новостные сюжеты по 2 минуты — не ваш жанр)
        res = [e for e in res if e.get("view_count") and (e.get("duration") or 0) >= min_dur and e.get("channel_id") != ch.get("id")
               and e.get("channel") != ch.get("title")]
        # только ролики той же тематики, что и ваш канал: общие слова с запросом И с названиями ваших роликов
        # (иначе «Colombian apartment» из сценария про экономику приводит к турам по квартирам)
        qs, vocab = set(_stems(q)), {w for w, c in own_vocab.most_common(60)}
        ok = []
        for e in res:
            st = set(_stems(e.get("title") or ""))
            if (st & qs) and (len(vocab) < 12 or st & vocab) and niche.same_script(e.get("title"), "cyr" if lang == "ru" else "lat"):
                ok.append(e)
        comp_rejected = len(res) - len(ok)
        # первыми — ролики, похожие на ваши по словам в названиях (тот же жанр), потом по просмотрам
        res = sorted(ok, key=lambda e: (-len(set(_stems(e.get("title") or "")) & vocab), -(e.get("view_count") or 0)))
        comp += [{"id": e["id"], "title": e.get("title"), "channel": e.get("channel"), "views": e["view_count"], "duration": e.get("duration")}
                 for e in res[:6 - len(comp)]]

    prog(30, f"Читаю субтитры: {len(hits)} ваших хитов, {len(flops)} провалов, {len(comp)} топ-роликов по теме…")
    hits_f, hits_m = _collect(hits, ylang, lang, 120)
    prog(45, "Читаю субтитры провалов…")
    flops_f, flops_m = _collect(flops, ylang, lang, 90)
    prog(55, "Читаю субтитры топ-роликов по теме…")
    comp_f, comp_m = _collect(comp, ylang, lang, 90)

    # ролики без субтитров — распознаём речь (дольше). Сначала там, где не хватает сравнения.
    asr_n = asr_fail = 0
    queue = []
    for f_list, m_list in ((hits_f, hits_m), (comp_f, comp_m), (flops_f, flops_m)):
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
        else:
            asr_fail += 1
    no_text = len(hits_m) + len(flops_m) + len(comp_m) - asr_n

    if not hits_f and not comp_f:
        raise ValueError("Не удалось получить текст ни у ваших роликов, ни у роликов по теме — сравнивать не с чем. "
                         "У роликов нет субтитров, а речь распознать не получилось (в них нет речи или YouTube не отдал звук).")

    prog(85, "Разбираю сценарий на молекулы…")
    own_wpm = [f["wpm"] for f in hits_f + flops_f if f.get("wpm")]
    if len(own_wpm) >= 2:
        wpm, wpm_src = _med(own_wpm), "по вашим роликам"
    elif comp_f:
        wpm, wpm_src = _med([f["wpm"] for f in comp_f]), "по роликам по теме (ваших с текстом не нашлось)"
    else:
        wpm, wpm_src = (140 if lang == "ru" else 150), "средний темп речи"
    wpm = max(110, min(200, wpm))
    dur = timed(sents, wpm)
    items = [(s["t"], s["text"]) for s in sents]
    me = feats(items, dur, given_title)

    H, F, C = agg(hits_f), agg(flops_f), agg(comp_f)
    rows = []
    for k, kind, name, short in SPEC:
        if k in ("topic15",) and not given_title:
            continue
        vals = [v for v in (me.get(k), H.get(k), F.get(k), C.get(k)) if v is not None]
        if k in ("greet", "q20", "num30") and not any(vals):
            continue          # никто так не делает — строка ничего не говорит
        rows.append({"key": k, "kind": kind, "name": name, "me": me[k], "hits": H.get(k), "flops": F.get(k), "comp": C.get(k),
                     "me_t": fmt_v(k, me[k]), "hits_t": fmt_v(k, H.get(k)), "flops_t": fmt_v(k, F.get(k)), "comp_t": fmt_v(k, C.get(k))})

    good, bad, risk, compx = [], [], [], []
    for r in rows:
        k, kind = r["key"], r["kind"]
        if differs(kind, r["hits"], r["flops"]):
            c = closer(kind, r["me"], r["hits"], r["flops"])
            line = f"{r['name']}: у ваших хитов {r['hits_t']}, у провалов {r['flops_t']}, в сценарии {r['me_t']}."
            if c is True:
                good.append({"key": k, "text": line})
            elif c is False:
                risk.append({"key": k, "text": line})
        if r["comp"] is not None and r["me"] is not None and len(comp_f) >= 2 and differs(kind, r["comp"], r["me"]) \
                and not (r["hits"] is not None and not differs(kind, r["hits"], r["me"])):
            cv, mv = float(r["comp"]), float(r["me"])
            better = {"bool_high": cv > mv, "bool_low": cv < mv, "high": cv > mv, "low": cv < mv, "early": cv < mv, "mid": False}[kind]
            if better:
                compx.append({"key": k, "text": f"{r['name']}: у топ-роликов по теме {r['comp_t']}, в сценарии {r['me_t']}."})

    mols = molecules(sents, me, H, C, dur, wpm, given_title)
    score = 50
    score += 8 * len(good) - 9 * len(risk) - 3 * len(compx)
    score -= 4 * len([m for m in mols if m["level"] == "bad"]) + 1 * len([m for m in mols if m["level"] == "warn"])
    score = max(5, min(98, score))

    def ex(fs, n=3):
        return [{"id": f["id"], "title": f["title"], "channel": f.get("channel"), "views": f.get("views"), "x": f.get("x"),
                 "first30": f["first30"], "src": f.get("src")} for f in sorted(fs, key=lambda f: -(f.get("x") or f.get("views") or 0))[:n]]
    timeline = [{"n": s["n"], "t": s["t"], "q": "?" in s["text"], "hook": bool(REHOOK.search(s["text"])),
                 "num": bool(NUM.search(s["text"])), "cta": bool(video.CTA.search(s["text"])),
                 "promise": bool(video.PROMISE.search(s["text"]))} for s in sents]
    prog(100, "Готово")
    return {"type": "molecules", "title": title, "channel": {"title": ch.get("title"), "url": base, "median": med},
            "lang": lang, "wpm": round(wpm), "est_duration": round(dur), "sentences": len(sents), "words": me["words"],
            "score": score, "me": me, "rows": rows, "good": good, "risk": risk, "comp_diff": compx, "molecules": mols,
            "timeline": timeline, "dead": dead_zones(sents, dur, max(max(20, min(45, dur * 0.2)), H["max_gap"]) if H.get("max_gap") else max(20, min(45, dur * 0.2)) * 1.3),
            "hits_ex": ex(hits_f), "flops_ex": ex(flops_f, 2), "comp_ex": ex(comp_f),
            "basis": {"wpm_src": wpm_src, "query": q, "comp_rejected": comp_rejected, "title_given": bool(given_title),
                      "own": len(hits_f) + len(flops_f)},
            "counts": {"hits": len(hits_f), "flops": len(flops_f), "comp": len(comp_f),
                       "asr": asr_n, "no_text": max(0, no_text)},
            "script_head": [s["text"] for s in sents if s["t"] < 30][:6]}


STOP_T = {"этот", "этого", "который", "которые", "когда", "потом", "очень", "просто", "можно", "будет", "было", "были", "есть",
          "только", "тоже", "чтобы", "потому", "если", "даже", "after", "before", "their", "there", "these", "those", "which",
          "would", "could", "about", "where", "while", "every", "first", "years", "people", "because", "today", "still"}


def _stems(t):
    return [w[:5] for w in re.findall(r"[a-zа-яё]{5,}", (t or "").lower()) if w not in STOP_T and w not in TSTOP]


def topic_query(given_title, text, own_vocab, lang, own_titles=()):
    """Поисковый запрос для «топ-роликов по теме». С названием — по названию. Без него — самые частые слова
    всего сценария, и в первую очередь те, что есть в названиях ваших роликов (тематика канала), — а не первая фраза."""
    if given_title:
        w = [x for x in re.findall(r"[\wа-яё]{4,}", given_title.lower()) if x not in TSTOP and x not in STOP_T][:6]
        return " ".join(w), w
    cnt, form = Counter(), {}
    for w in re.findall(r"[a-zа-яё]{5,}", text.lower()):
        if w in STOP_T or w in TSTOP:
            continue
        st = w[:5]
        cnt[st] += 1
        form.setdefault(st, w)
    if not cnt:
        return "", []
    score = {st: c * (3 if own_vocab.get(st) else 1) for st, c in cnt.items() if c >= 2}
    top = [form[st] for st, _ in sorted(score.items(), key=lambda x: -x[1])[:3]]
    # жанр канала: слово, которое повторяется в названиях ваших роликов («rich», «выжить»…)
    forms = Counter()
    for t in own_titles:
        for w in re.findall(r"[a-zа-яё]{4,}", t.lower()):
            if w not in STOP_T and w not in TSTOP and w not in ("this", "that", "with", "from", "have", "what", "your", "were", "here", "went"):
                forms[w] += 1
    genre = next((w for w, c in forms.most_common(10) if c >= 2 and w[:5] not in {x[:5] for x in top}), None)
    if genre:
        top.append(genre)
    return " ".join(top), top


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
