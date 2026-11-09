"""
Идеи роликов из ниши: какие темы недавно «выстрелили» у каналов этой ниши — с доказательствами.

Как считается (только реальные данные YouTube):
1. Берём ролики каналов ниши за последние ~5 месяцев (старше 5 дней — чтобы просмотры успели набраться).
2. Для каждого ролика — во сколько раз он набрал больше обычного для СВОЕГО канала
   (медиана просмотров канала). Так маленький канал с 20 тыс. на ролике при обычных 3 тыс. виден так же,
   как большой с 2 млн при обычных 300 тыс.
3. Выстрелившие ролики (×2 и больше) группируем по общей теме — словам в названиях, которые для ниши
   не общие («город» в нише про города — не идея, а «дата-центры» — идея).
4. Тема засчитывается, если выстрелила у 2+ разных каналов (повторяемость = не случайность)
   или один раз, но очень сильно (×4+) — тогда она помечается как «одиночный взлёт».
5. Для лучших тем проверяем поиск YouTube за последние месяцы: сколько свежих роликов уже есть
   и сколько они набирают — так видно, открыто окно или тема уже насыщается.
"""
import datetime as dt
import math
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import analyzer as A
import niche
from hooks import HOOKS, hooks_of


def human(n):
    n = n or 0
    if n >= 1e6:
        return f"{n / 1e6:.1f} млн".replace(".", ",").replace(",0 ", " ")
    if n >= 1e3:
        return f"{round(n / 1e3)} тыс."
    return str(int(n))

MAX_AGE = 150       # дней: «недавно»
MIN_AGE = 5         # дней: просмотры ещё не набрались
HIT = 2.0           # во сколько раз больше обычного — «выстрелил»
BREAKOUT = 4.0      # одиночный ролик засчитываем только при таком взлёте
HOOK_NAME = {k: n for k, n, _ in HOOKS}

# слова, которые сами по себе не тема (форматы, оценки, связки)
FILLER = set("""
why how what when who which where will would could should does did done make makes made making
real really truly actually literally secret secrets truth hidden inside story stories explained explain
facts fact ever never always every biggest smallest largest worst best most least top bad good great
finally first last next new old part full update reaction react review reviews guide tutorial tips
watch watched watching look looks looking tried try trying test tested testing went goes going happened
happens happen change changed changes became become becoming need needs want wants know knows knew
почему зачем как что кто где когда какой какая какие каким самый самая самые самое главный главная
правда секрет секреты тайна тайны история истории факты факт реально действительно вообще никогда всегда
первый первая последний последняя новый новая новые лучший лучшие худший худшие обзор разбор реакция
стал стала стали становится становятся случилось происходит произошло узнал узнали попробовал проверил
смотреть посмотри смотрите нужно нужен нужна хочу хотят знать знаешь вся весь все
nobody everyone everybody anyone someone quietly suddenly slowly apart impossible sense makes falling fell fall rise
rising regret regrets regretted works working broke broken fixed fix untapped richest poorest emptiest powerful beautiful
strange weird bizarre terrifying incredible insane massive tiny huge entire whole another other same different
talking talk talks says said told tells nothing everything anything something matter matters problem problems
никто никого все всё никак тихо внезапно медленно невозможно невозможный смысл разваливается рухнул упал падение
взлёт взлет пожалел работает сломал сломался починил богатый бедный пустой огромный крошечный странный жуткий
невероятный безумный другой другие говорит говорят молчат ничего всё что-то проблема проблемы
плохо хорошо лучше хуже очень сильно много мало можно нельзя good bad better worse
close closed closing open opened opening end ends ended start started stop stopped
""".split())


def _words(title):
    """Все слова названия в нижнем регистре (со служебными) — для поиска повторяющихся формулировок."""
    return re.findall(r"[a-zа-яё0-9]+(?:['’.][a-zа-яё]+)*", (title or "").lower())


def formats(hits, common):
    """Форматы-серии: одна и та же формулировка, разный объект — и каждый раз взлёт.
    «Why ___ Makes No Sense as a U.S. City», «___ Is Quietly Falling Apart»."""
    grams = defaultdict(dict)
    for h in hits:
        ws = _words(main_part(h["title"]))
        for n in range(3, 9):
            for i in range(len(ws) - n + 1):
                g = tuple(ws[i:i + n])
                grams[g][h["id"]] = h
    cands = []
    for g, vs in grams.items():
        if len(vs) < 3:
            continue
        meaningful = [w for w in g if w not in A.STOP and len(w) >= 3]
        if len(meaningful) < 2 or {_st(w) for w in meaningful} <= common:
            continue
        vs = list(vs.values())
        chans = {v["channel_id"] for v in vs}
        score = sum(math.log2(max(v["ratio"], 1.01)) for v in vs) * (1 + 0.4 * (len(chans) - 1)) * (1 + 0.05 * len(g))
        cands.append({"gram": g, "videos": vs, "channels": len(chans), "score": score, "ids": frozenset(v["id"] for v in vs)})
    # оставляем самые длинные формулировки: «makes no sense as a u.s. city», а не «no sense as»
    cands.sort(key=lambda c: (-len(c["ids"]), -len(c["gram"])))
    keep = []
    for c in cands:
        if any(c["ids"] <= k["ids"] and set(c["gram"]) <= set(k["gram"]) for k in keep):
            continue
        keep.append(c)
    keep.sort(key=lambda c: -c["score"])
    out, used = [], set()
    for c in keep:
        if len(c["ids"] & used) >= 0.6 * len(c["ids"]):
            continue
        used |= c["ids"]
        out.append(c)
        if len(out) >= 5:
            break
    return out


def _template(gram, title):
    """Название лучшего ролика → шаблон: повторяющаяся часть остаётся, объект заменяется на «…»."""
    ws = re.findall(r"\S+", title)
    low = [re.sub(r"[^a-zа-яё0-9'’.]", "", w.lower()).strip(".") for w in ws]
    n = len(gram)
    g = [x.strip(".") for x in gram]
    for i in range(len(low) - n + 1):
        if [re.sub(r"[’]", "'", x) for x in low[i:i + n]] == [re.sub(r"[’]", "'", x) for x in g]:
            pre, mid, post = ws[:i], ws[i:i + n], ws[i + n:]
            lead = [w for w in pre if w.lower().strip("?!.,:") in ("why", "how", "what", "почему", "как", "что", "зачем")]
            t = (" ".join(lead) + " " if lead else "") + ("… " if len(pre) > len(lead) else "") + " ".join(mid) + (" …" if post else "")
            return t.strip()
    return " ".join(gram)


try:
    import snowballstemmer
    _SB = {"ru": snowballstemmer.stemmer("russian"), "en": snowballstemmer.stemmer("english")}
except Exception:  # без библиотеки — простая обрезка, как в остальном сервисе
    _SB = {}


def _st(w):
    """Основа слова: «Японию», «Япония», «Японии» → одна тема (русские окончания), cities → citi."""
    if _SB:
        return _SB["ru" if re.search(r"[а-яё]", w) else "en"].stemWord(w)
    return niche.stem(w)


def _stems(words):
    return [_st(w) for w in words]


def _content(title):
    """Значимые слова названия (без служебных и «оценочных»)."""
    return [w for w in A.tokenize(title) if w not in FILLER and not w.isdigit()]


def main_part(title):
    """Название без «хвоста» после | (гости, рубрика, название канала)."""
    return re.split(r"\s[|•]\s|\s\|", title or "")[0]


def _terms(title):
    """Темы-кандидаты из названия: основы слов и пары соседних значимых слов внутри одной части фразы."""
    uni, bi = set(), set()
    for seg in re.split(r"[:?!.;,()\[\]«»\"]|\s[—–-]\s", main_part(title)):
        st = _stems(_content(seg))
        uni |= set(st)
        bi |= {f"{a} {b}" for a, b in zip(st, st[1:]) if a != b}
    return uni, bi


def _surface(term, titles):
    """Как тема пишется в названиях: «data centers», «Гонконг», «дома-клетки»."""
    parts = term.split()
    forms = Counter()
    for t in titles:
        words = re.findall(r"[\w']+", t or "")
        low = [_st(re.sub(r"[^a-zа-яё0-9]", "", w.lower())) for w in words]
        for i in range(len(words) - len(parts) + 1):
            if low[i:i + len(parts)] == parts:
                forms[" ".join(words[i:i + len(parts)])] += 1
    if not forms:
        return term
    best = max(forms.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]  # самая частая форма, при равенстве — короче
    if best.isupper() and len(best) > 3:   # «ГОРОДА» в капс-названиях — пишем по-человечески
        best = best.capitalize()
    return best


def _age(e, today):
    d = A._entry_date(e)
    return (today - d).days if d else None


def collect(rel, comp, today, long_only=True):
    """Выстрелившие ролики каналов ниши за последние месяцы."""
    out = []
    for r in rel + ([comp] if comp else []):
        ents = [e for e in (r.get("_ents") or []) if e and e.get("id") and e.get("view_count") is not None]
        if long_only:
            ents = [e for e in ents if not e.get("duration") or e["duration"] >= 90]
        if len(ents) < 4:
            continue
        aged = [(e, _age(e, today)) for e in ents]
        base = [e["view_count"] for e, a in aged if a is None or a >= 7]
        norm = statistics.median(base) if len(base) >= 3 else None
        if not norm:
            continue
        for e, a in aged:
            if a is None or not (MIN_AGE <= a <= MAX_AGE):
                continue
            ratio = e["view_count"] / max(norm, 1)
            if ratio >= (1.6 if a < 14 else HIT) and e["view_count"] >= 3000:
                out.append({"id": e["id"], "title": e.get("title") or "", "views": e["view_count"], "ratio": round(ratio, 2),
                            "age": a, "channel": r.get("title"), "channel_id": r.get("id"), "channel_url": r.get("url"),
                            "subs": r.get("subs"), "young": bool(r.get("young")), "is_comp": r is comp, "norm": int(norm)})
    return out


def common_words(rel, kws):
    df = Counter()
    for r in rel:
        df.update({s for t in (r.get("titles") or [])[:60] for s in _stems(_content(t))})
    n_ch = max(1, len(rel))
    common = {s for s, c in df.items() if c >= max(3, 0.35 * n_ch)}
    return common | {_st(w) for k in kws for w in A.tokenize(k)}


def themes(hits, rel, comp, kws, fmt_grams=()):
    """Группируем выстрелившие ролики по общей теме."""
    # слова, общие для всей ниши, — не идея (их ставит почти каждый канал); слова формулировок-серий — это формат, не тема
    common = common_words(rel, kws) | {_st(w) for g in fmt_grams for w in g}
    by_term = defaultdict(list)
    for h in hits:
        uni, bi = _terms(h["title"])
        uni = {u for u in uni if u not in common and len(u) >= 3}
        bi = {b for b in bi if not set(b.split()) <= common}
        for t in uni | bi:
            by_term[t].append(h)
    cands = []
    for t, vs in by_term.items():
        uniq = {}
        for v in vs:  # один ролик — один раз
            uniq[v["id"]] = v
        vs = list(uniq.values())
        chans = {v["channel_id"] for v in vs}
        top = max(v["ratio"] for v in vs)
        if len(chans) < 2 and not (len(vs) >= 1 and top >= BREAKOUT):
            continue
        rec = sum(math.log2(max(v["ratio"], 1.01)) * (1.0 if v["age"] <= 60 else 0.7) for v in vs)
        score = rec * (1 + 0.6 * (len(chans) - 1)) * (1.15 if " " in t else 1.0)
        cands.append({"term": t, "videos": vs, "channels": len(chans), "score": score, "single": len(chans) < 2})
    cands.sort(key=lambda c: -c["score"])
    singles = 0
    picked = []
    for c in cands:
        ids = {v["id"] for v in c["videos"]}
        dup = next((p for p in picked if len(ids & p["_ids"]) >= 0.5 * min(len(ids), len(p["_ids"]))), None)
        if dup:  # та же группа роликов — это синоним уже выбранной темы
            if " " in c["term"] and " " not in dup["term"] and ids >= dup["_ids"]:
                dup["term"] = c["term"]
            continue
        if c["single"] and len(ids) == 1:
            if singles >= 2:   # одиночных взлётов показываем не больше двух — это наименее надёжные идеи
                continue
            singles += 1
        c["_ids"] = ids
        picked.append(c)
        if len(picked) >= 10:
            break
    return picked


def demand(theme_q, ylang, script_want, niche_median, today, term=None):
    """Поиск YouTube по теме за год: сколько свежих роликов уже есть и сколько они набирают.
    Считаются только ролики, в названии которых правда есть тема."""
    try:
        res = A.search(theme_q, 30, A.SEARCH_FILTERS["year"], ylang) or []
    except Exception:
        res = []
    if res and sum(1 for e in res if A._entry_date(e)) < 0.5 * len(res):
        # даты «2 недели назад» yt-dlp понимает только в английском интерфейсе: тот же поиск по-английски,
        # та же страна, — берём оттуда даты по id роликов (названия оставляем оригинальные)
        code, region = niche.split_ylang(ylang)
        try:
            dated = {e["id"]: e for e in (A.search(theme_q, 30, A.SEARCH_FILTERS["year"], niche.ylang_for("en", region)) or [])}
        except Exception:
            dated = {}
        res = [dict(e, timestamp=(dated.get(e["id"]) or {}).get("timestamp"), upload_date=(dated.get(e["id"]) or {}).get("upload_date")) for e in res]
    known = sum(1 for e in res if A._entry_date(e))
    need = set((term or "").split())
    res = [e for e in res if niche.same_script(e.get("title"), script_want)
           and (not need or need <= set(_stems(_content(e.get("title") or ""))))]
    if res and known < 0.5 * len(res):   # дат нет — про свежесть честно ничего не говорим
        return {"query": theme_q, "found": len(res), "fresh": None, "fresh_median": None, "vs_niche": None}
    fresh = [e for e in res if (_age(e, today) or 999) <= 90]
    views = [e.get("view_count") or 0 for e in fresh if e.get("view_count") is not None]
    med = int(statistics.median(views)) if views else 0
    return {"query": theme_q, "found": len(res), "fresh": len(fresh), "fresh_median": med,
            "vs_niche": round(med / niche_median, 2) if niche_median and med else None}


def verdict(t, d):
    """Горячая / окно открыто / насыщается / одиночный взлёт — с объяснением."""
    med = t["median_ratio"]
    if t["single"]:
        if len(t["videos"]) >= 3:
            n = len(t['videos'])
            return "single", "Взлёт у одного канала", (f"У одного канала тема выстрелила {n} {_pl(n, 'раз', 'раза', 'раз')} (обычно ×{_x(med)}). "
                                                       "Это не случайность для него — но у других каналов ещё не проверено.")
        return "single", "Одиночный взлёт", f"Выстрелил один ролик (×{_x(t['best_ratio'])}). Может быть удачей — проверьте, сняв свою версию."
    fresh = d.get("fresh") if d else None
    if fresh is not None and fresh >= 12 and (d["vs_niche"] or 0) < 0.8:
        return "busy", "Насыщается", (f"За 3 месяца в поиске уже {d['fresh']} свежих роликов, и набирают они меньше обычного для ниши. "
                                      "Заходить можно только с сильным новым углом.")
    if fresh is not None and fresh <= 5 and med >= 2.2 and d.get("found", 0) >= 5:
        few = "ни одного свежего ролика" if fresh == 0 else f"всего {fresh} {_pl(fresh, 'свежий ролик', 'свежих ролика', 'свежих роликов')}"
        return "open", "Окно открыто", (f"У {t['channels']} {_pl(t['channels'], 'канала', 'каналов', 'каналов')} тема дала ×{_x(med)} к обычным просмотрам, "
                                         f"а в поиске YouTube за 3 месяца по ней {few}. Конкуренция по теме пока низкая.")
    vs = d.get("vs_niche") if d else None
    if (med >= 2.5 and (vs is None or vs >= 0.5)) or (vs or 0) >= 1.5:
        return "hot", "Горячая тема", (f"Выстрелила у {t['channels']} разных {_pl(t['channels'], 'канала', 'каналов', 'каналов')} (обычно ×{_x(med)})"
                                       + (f", свежие ролики по ней в поиске набирают {_x(d['vs_niche'])}× от обычного для ниши" if d and d.get("vs_niche") else "")
                                       + ". Зрителям тема интересна прямо сейчас.")
    return "good", "Работает", (f"Повторилась у {t['channels']} {_pl(t['channels'], 'канала', 'каналов', 'каналов')}, обычно ×{_x(med)} к их просмотрам"
                                + (f", но в общем поиске свежие ролики по теме набирают лишь {_x(vs)}× от обычного для ниши — успех зависит от подачи" if vs is not None and vs < 0.5 else "") + ".")


def _x(v):
    return f"{v:.1f}".replace(".", ",") if v < 10 else str(round(v))


def angles(t, comp_has, lang_ru):
    """Как подать тему — по тому, как её подали выстрелившие ролики."""
    hk = Counter(h for v in t["videos"] for h in hooks_of(v["title"]) if h != "caps")
    out = []
    for k, n in hk.most_common(3):
        if n < 1:
            continue
        ex = next((v["title"] for v in sorted(t["videos"], key=lambda v: -v["ratio"]) if k in hooks_of(v["title"])), "")
        out.append({"hook": HOOK_NAME.get(k, k), "count": n, "example": ex})
    tips = []
    keys = [k for k, _ in hk.most_common()]
    name = t["name"]
    if "question" in keys:
        tips.append(f"Задайте свой вопрос о теме «{name}» — в нише хорошо заходят названия-вопросы.")
    if "number" in keys:
        tips.append(f"Сделайте подборку: «N … про {name}» — списки с числом здесь выстреливали.")
    if "compare" in keys:
        tips.append(f"Сравните «{name}» с чем-то знакомым зрителю — сравнения в нише работают.")
    if "curiosity" in keys:
        tips.append("Пообещайте раскрыть неочевидное — интрига в этих названиях давала взлёты.")
    if "pain" in keys:
        tips.append("Покажите проблему или угрозу — у выстреливших роликов в названии есть боль.")
    if not tips:
        tips.append(f"Возьмите тот же вопрос, что в лучшем ролике, но на своём примере — другая страна, город, случай.")
    if comp_has:
        tips.append(f"У разобранного канала уже есть ролик на эту тему («{comp_has['title'][:70]}», {human(comp_has['views'])} просмотров) — нужен новый угол.")
    return out, tips[:3]


def build(rel, comp, kws, ylang, script_want, niche_median, progress=lambda p, m: None, today=None):
    today = today or dt.date.today()
    long_only = (comp or {}).get("fmt") != "shorts"
    hits = collect(rel, comp, today, long_only)
    if not hits:
        return {"ideas": [], "checked_videos": 0, "hits": 0,
                "note": "За последние месяцы у каналов ниши не было роликов, которые заметно выстрелили."}
    checked = sum(len([e for e in (r.get("_ents") or []) if e]) for r in rel)
    fm = formats(hits, common_words(rel, kws))
    ts = themes(hits, rel, comp, kws, [f["gram"] for f in fm])
    for t in ts:
        vs = sorted(t["videos"], key=lambda v: -v["ratio"])
        t["name"] = _surface(t["term"], [v["title"] for v in vs])
        if t["single"] and len(vs) == 1:  # один ролик: тема — это сам ролик, а не пара слов из него
            t["name"] = niche.clean_query(main_part(vs[0]["title"]), 9) or t["name"]
        t["median_ratio"] = round(statistics.median(v["ratio"] for v in vs), 2)
        t["best_ratio"] = vs[0]["ratio"]
        t["views_total"] = sum(v["views"] for v in vs)
        t["newest_days"] = min(v["age"] for v in vs)
        t["young_channels"] = len({v["channel_id"] for v in vs if v["young"]})
        t["evidence"] = vs[:5]
    # спрос в поиске — для лучших тем (параллельно)
    top = ts[:7]
    progress(98, f"Проверяю спрос на {len(top)} тем в поиске YouTube…")
    core = (kws or [""])[0]

    def q(t):
        name = t["name"]
        # одно слово без контекста ниши уводит поиск в сторону («maps» — навигаторы): добавляем слово ниши
        return f"{name} {core}".strip() if " " not in name and core and core.lower() not in name.lower() else name
    with ThreadPoolExecutor(4) as ex:
        dem = list(ex.map(lambda t: demand(q(t), ylang, script_want, niche_median, today, t["term"]), top))
    comp_titles = [(e.get("title") or "", e.get("view_count") or 0) for e in ((comp or {}).get("_ents") or []) if e]
    out = []
    for i, t in enumerate(top):
        d = dem[i]
        key, label, why = verdict(t, d)
        parts = set(t["term"].split())
        has = next(({"title": tt, "views": vv} for tt, vv in comp_titles if parts <= set(_stems(_content(tt)))), None)
        ang, tips = angles(t, has, script_want == "cyr")
        out.append({"name": t["name"], "term": t["term"], "verdict": key, "label": label, "why": why,
                    "channels": t["channels"], "videos": len(t["videos"]), "median_ratio": t["median_ratio"],
                    "best_ratio": t["best_ratio"], "views_total": t["views_total"], "newest_days": t["newest_days"],
                    "young_channels": t["young_channels"], "single": t["single"], "demand": d,
                    "comp_has": has, "hooks": ang, "tips": tips,
                    "evidence": [{k: v[k] for k in ("id", "title", "views", "ratio", "age", "channel", "channel_url", "subs", "norm", "young")}
                                 for v in t["evidence"]]})
    rank = {"open": 0, "hot": 1, "good": 2, "busy": 3, "single": 4}
    out.sort(key=lambda x: (rank[x["verdict"]], -x["median_ratio"] * (1 + 0.3 * x["channels"])))
    fmts = []
    for f in fm:
        vs = sorted(f["videos"], key=lambda v: -v["ratio"])
        med = round(statistics.median(v["ratio"] for v in vs), 2)
        fmts.append({"template": _template(f["gram"], vs[0]["title"]), "phrase": " ".join(f["gram"]), "videos": len(vs),
                     "channels": f["channels"], "median_ratio": med, "best_ratio": vs[0]["ratio"],
                     "newest_days": min(v["age"] for v in vs),
                     "why": (f"{len(vs)} {_pl(len(vs), 'ролик', 'ролика', 'роликов')} с этой формулировкой выстрелили"
                             + (f" у {f['channels']} разных каналов" if f["channels"] > 1 else " у одного канала — это его рабочая серия")
                             + f", обычно ×{_x(med)} к своим просмотрам. Подставьте свой объект — формат проверен."),
                     "evidence": [{k: v[k] for k in ("id", "title", "views", "ratio", "age", "channel", "channel_url", "subs", "norm", "young")}
                                  for v in vs[:5]]})
    return {"ideas": out, "formats": fmts, "checked_videos": checked, "hits": len(hits), "max_age": MAX_AGE, "hit": HIT}


def _pl(n, a, b, c):
    n = abs(n) % 100
    return c if 11 <= n <= 19 else a if n % 10 == 1 else b if 2 <= n % 10 <= 4 else c
