"""
Короткая сводка в начале отчёта: 5–8 пунктов «главное жирным — дальше цифры по-человечески».
Считается из уже готового отчёта, поэтому работает и для старых отчётов.
Каждый пункт: {"lead": "...", "text": "...", "go": "id раздела"}.
"""
import statistics

from insights import fmt_n as N

def AGE(d):
    if d is None:
        return "—"
    if d < 60:
        return f"{d} дн."
    if d < 730:
        return f"{round(d / 30)} мес."
    y = round(d / 365)
    return f"{y} {pl(y, 'год', 'года', 'лет')}"


def V(n):
    """«532 просмотра», «15,7 тыс. просмотров»."""
    if n is None:
        return "—"
    return f"{N(n)} {pl(n, 'просмотр', 'просмотра', 'просмотров')}" if n < 1000 else f"{N(n)} просмотров"


def X(v):
    if v is None:
        return "—"
    return f"×{v:.0f}" if v >= 10 else f"×{v:.1f}".replace(".", ",")


def D(x, nd=1):
    """1.0 → «1», 2.45 → «2,5»."""
    x = round(float(x), nd)
    return (str(int(x)) if x == int(x) else str(x)).replace(".", ",")


def pl(n, one, few, many):
    n = abs(int(n))
    return one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def raz(k):
    """«в 3 раза», «в 1,5 раза», «в 125 раз»."""
    k = round(k) if k >= 10 else round(k, 1)
    if k != int(k):
        return f"в {D(k)} раза"
    return f"в {int(k)} {pl(int(k), 'раз', 'раза', 'раз')}"


def times(a, b):
    """«в 3 раза выше» / «в 2,5 раза ниже» / «на уровне»."""
    if not a or not b:
        return None
    r = a / b
    if 0.8 <= r <= 1.25:
        return "на уровне"
    return f"{raz(r if r > 1 else 1 / r)} {'выше' if r > 1 else 'ниже'}"


def _b(lead, text, go=None):
    return {"lead": lead, "text": text, "go": go}


# ---------------------------------------------------------------- отчёт по нише
def niche(d):
    n, c = d.get("niche") or {}, d.get("competitor") or {}
    dy, fm, fc = d.get("dynamics") or {}, d.get("formats") or {}, d.get("forecast") or {}
    ver = d.get("verification") or {}
    out = []
    ov = n.get("overall")
    if ov is None:
        return None

    # 1. вердикт
    lead = ("Ниша сильная — заходить стоит." if ov >= 70 else "Ниша рабочая, но с оговорками." if ov >= 55
            else "Ниша тяжёлая — нужен точный угол." if ov >= 40 else "Ниша закрыта для новичков.")
    rng = ver.get("range")
    t = f"Оценка {ov} из 100" + (f" (обычно {rng[0]}–{rng[1]})" if rng else "") + "."
    if n.get("median_search_views"):
        t += f" Ролики по запросам ниши обычно набирают {N(n['median_search_views'])} просмотров"
        t += f", каналов-гигантов в выдаче {n.get('big_channels_share', 0)}%." if n.get("big_channels_share") is not None else "."
    t += f" Проверено {n.get('relevant', '—')} каналов ниши" + (f", достоверность — {ver['level']}." if ver.get("level") else ".")
    out.append(_b(lead, t, "sScore"))

    # 2. новички
    y, yr = n.get("young") or 0, n.get("young_rising") or 0
    if y:
        share = yr / y
        lead = ("Новичков мало, но они растут." if y < 4 and share >= 0.5 else "Новички растут." if share >= 0.5
                else "Новички есть, но растут не все." if yr else "Новички не растут.")
        t = f"Из {y} каналов младше 2 лет растут {yr}."
        s180 = (fc.get("subs_180") or [None, None])[1] if fc.get("subs_180") else None
        if s180:
            t += f" Типичный растущий новичок набирает около {N(s180)} подписчиков за полгода"
            inc = n.get("income_young")
            t += f" и ${N(inc[0])}–{N(inc[1])} в месяц с рекламы." if inc else "."
        elif n.get("income_young"):
            inc = n["income_young"]
            t += f" Растущий новичок получает примерно ${N(inc[0])}–{N(inc[1])} в месяц с рекламы."
        out.append(_b(lead, t, "sForecast"))
    else:
        out.append(_b("Новичков в нише почти нет.", "Молодые каналы в выдаче не попались — нишу держат старые каналы, зайти будет трудно.", "sChannels"))

    # 3. волна
    acc, slow, known = dy.get("accelerating") or 0, dy.get("slowing") or 0, dy.get("trend_known") or 0
    if dy.get("month_videos"):
        lead = ("Ниша разгоняется." if acc > slow * 1.3 and acc >= 2 else "Волна остывает." if slow > acc * 1.3 and slow >= 2 else "Ниша ровная, без резких волн.")
        mv, mc = dy['month_videos'], dy.get('month_channels') or 0
        t = (f"За последний месяц вышло {mv} {pl(mv, 'ролик', 'ролика', 'роликов')} от {mc} {pl(mc, 'канала', 'каналов', 'каналов')}, "
             f"медиана — {N(dy.get('month_median_views'))} просмотров.")
        if known:
            t += f" Из {known} каналов ускоряются {acc}, замедляются {slow}."
        out.append(_b(lead, t, "sPulse"))

    # 4. канал-образец
    if c.get("title"):
        fmt_key = "shorts" if c.get("fmt") == "shorts" else "long"
        ym = (fm.get(fmt_key) or {}).get("young_median_views")
        cmp = times(c.get("median_views"), ym)
        lab = (c.get("label") or "").lower()
        lead = ("Канал-образец взлетает." if "взлет" in lab else "Канал-образец растёт." if "раст" in lab
                else "Канал-образец растёт медленно." if "сред" in lab else "Канал-образец буксует." if lab else "Канал-образец.")
        t = (f"{c['title']}: {N(c.get('subs'))} подписчиков, возраст {AGE(c.get('age_days'))}, "
             f"обычный ролик — {V(c.get('median_views'))}")
        t += f" — {cmp}, чем у новичков ниши ({N(ym)})." if cmp and ym else "."
        if c.get("uploads_per_week"):
            t += f" Выкладывает {D(c['uploads_per_week'])} видео в неделю."
        if c.get("outlier_ratio") and c["outlier_ratio"] >= 3:
            t += f" Лучший ролик обогнал обычные {raz(c['outlier_ratio'])}."
        out.append(_b(lead, t, "sComp"))

    # 5. темы
    vi = d.get("video_ideas") or {}
    ideas = [i for i in vi.get("ideas") or [] if not i.get("single")]
    good = [i for i in ideas if i.get("verdict") in ("open", "hot", "good")]
    busy = [i for i in ideas if i.get("verdict") == "busy"]
    fmts = vi.get("formats") or []
    if good or fmts:
        if good:
            names = ", ".join(f"«{i['name']}»" for i in good[:3])
            lead = f"Главный шанс — темы {names}."
            g = good[0]
            gc = g.get('channels') or 0
            t = f"«{g['name']}» дала {X(g.get('median_ratio'))} к обычным просмотрам у {gc} {pl(gc, 'канала', 'каналов', 'каналов')} ({g.get('label', '').lower()})."
        else:
            lead = "Темы: работают форматы, а не отдельные слова."
            t = ""
        if fmts:
            f0 = max(fmts, key=lambda f: (f.get("channels") or 0, f.get("median_ratio") or 0))
            t += f" Рабочий шаблон названия: «{f0['template']}» — {X(f0.get('median_ratio'))}."
        if busy:
            t += " Забито: " + ", ".join(f"«{i['name']}»" for i in busy[:3]) + " — туда не идти."
        out.append(_b(lead, t.strip(), "ideasSec"))
    else:
        gaps = [g["word"] for g in (d.get("titles") or {}).get("gaps") or []][:5]
        if gaps:
            out.append(_b("Свободные темы есть.", "Выстреливают у других каналов ниши, но их нет у конкурента: " + ", ".join(gaps) + ".", "sTitles"))

    # 6. формат и длина
    lo, sh = fm.get("long") or {}, fm.get("shorts") or {}
    if lo.get("young_median_views") or sh.get("young_median_views"):
        lv, sv = lo.get("young_median_views") or 0, sh.get("young_median_views") or 0
        lead = ("Новички растут на Shorts." if sv > lv * 1.5 and sh.get("young") else "Основа — длинные видео.")
        t = f"Обычный ролик новичка: длинное видео — {V(lv) if lv else '—'}, Shorts — {V(sv) if sv else '—'}."
        if sv > lv * 1.5:
            t += " Shorts дают подписчиков, но почти не дают денег — зарабатывают на длинных."
        if n.get("best_duration"):
            best = next((x for x in d.get("durations") or [] if x.get("bucket") == n["best_duration"]), {})
            t += f" Лучшая длина — {n['best_duration']}" + (f" ({X(best.get('perf'))} к обычным)." if best.get("perf") else ".")
        if dy.get("upw_young"):
            t += f" Растущие новички выкладывают {D(dy['upw_young'])} видео в неделю."
        out.append(_b(lead, t, "sFormat"))

    # 7. названия
    ft = [f for f in (d.get("titles") or {}).get("features") or [] if (f.get("lift") or 0) >= 1.08]
    if ft:
        f0 = ft[0]
        lead = f"В названиях работает: {f0['name'].split(' (')[0].lower()}."
        t = f"Такие ролики становятся хитами {raz(f0['lift'])} чаще."
        if len(ft) > 1:
            t += " Ещё помогает: " + ", ".join(f["name"].split(" (")[0].lower() for f in ft[1:3]) + "."
        lh = (d.get("titles") or {}).get("len_hit")
        if lh:
            t += f" Длина названия у хитов — около {lh} символов."
        out.append(_b(lead, t, "sTitles"))

    # 8. соседи
    rows = [r for r in (d.get("neighbors") or {}).get("rows") or [] if not r.get("low_data")]
    better = [r for r in rows if r.get("score") and r["score"] > ov]
    if better:
        r = max(better, key=lambda r: r["score"])
        out.append(_b(f"Рядом есть ниша сильнее — «{r['name']}».",
                      f"Оценка {r['score']} против {ov} у этой: растущих новичков {r.get('young_rising', 0)} из {r.get('channels')}, "
                      f"обычно {N(r.get('median_views'))} просмотров.", "neighSec"))

    # 9. что делать
    steps = []
    if lo.get("young_median_views") or sh.get("young_median_views"):
        steps.append("Shorts для роста + длинные для денег" if (sh.get("young_median_views") or 0) > (lo.get("young_median_views") or 0) * 1.5 and sh.get("young") else "длинные видео")
    if n.get("best_duration"):
        steps.append(f"длина {n['best_duration']}")
    if dy.get("upw_young"):
        steps.append(f"{D(dy['upw_young'])} видео в неделю")
    t = ", ".join(steps).capitalize() + "." if steps else ""
    if good:
        t += f" Начать с темы «{good[0]['name']}»"
        t += f" по шаблону «{fmts[0]['template']}»." if fmts else "."
    elif d.get("entry_topics"):
        t += f" Первое видео — своя версия хита «{d['entry_topics'][0]['title']}»."
    kt = d.get("keyword_table") or []
    if kt:
        k = max(kt, key=lambda r: r.get("score") or 0)
        t += f" Лучший запрос для старта — «{k['keyword']}»."
    t += " Выводы — после 5–7 роликов, не после одного."
    out.append(_b("Что делать:", t.strip(), "sPlan"))
    return {"title": f"Ниша канала «{c.get('title', '')}»", "items": out}


# ---------------------------------------------------------------- превью
def thumbs(d):
    o, c = d.get("own") or {}, d.get("comp") or {}
    ch = d.get("channel") or {}
    out = []
    new_mode = d.get("new_mode")
    if o.get("n") and not new_mode:
        out.append(_b(f"Разобрано {o['n']} ваших превью.",
                      f"Хитов (заметно выше соседних роликов) — {o.get('hits', 0)}, провалов — {o.get('flops', 0)}. "
                      f"Конкурентов — {len(d.get('competitors') or [])}, у них разобрано {c.get('n', 0)} превью."
                      + (f" Отсеяно чужих каналов: {len(d['rejected'])}." if d.get("rejected") else ""), "thHeadSec"))
    elif new_mode:
        out.append(_b("Канал ещё новый.", "Роликов мало, чтобы делать выводы о вас, поэтому ниже — что работает у конкурентов в вашей нише.", "thHeadSec"))
    bs = d.get("best_style")
    if bs:
        out.append(_b(f"Ваш лучший стиль — «{bs['name']}».", f"{bs.get('desc', '').capitalize()}. Сейчас так оформлено {bs.get('yours_share', 0)}% ваших превью.", "thStyleSec"))
    hk = [h for h in o.get("hooks") or [] if (h.get("ratio") or 0) >= 1.3 and h.get("n_with", 0) >= 2]
    if hk:
        h = hk[0]
        out.append(_b(f"В названиях у вас работает «{h['name'].lower()}».",
                      f"Ролики с этим приёмом набирают {raz(h['ratio'])} больше, чем без него ({h['n_with']} {pl(h['n_with'], 'ролик', 'ролика', 'роликов')}, уверенность {h.get('confidence', '—')}).", "thHookSec"))
    vis = sorted([v for v in o.get("visual") or [] if v.get("n_with", 0) >= 2], key=lambda v: v.get("ratio") or 1)
    vis = [v for v in vis if v.get("confidence") != "низкая"]
    if vis and (vis[0].get("ratio") or 1) < 0.7:
        v = vis[0]
        out.append(_b(f"Мешает: {v['name'].lower()}.", f"Такие превью набирают {raz(1 / v['ratio'])} меньше остальных у вас.", "thVisSec"))
    rc = c.get("recipe") or {}
    if rc.get("n"):
        col = ", ".join(rc.get("colors") or [])
        out.append(_b("Рецепт хитов конкурентов.",
                      f"Лицо — у {rc.get('face_share', 0)}% хитов, надпись — у {rc.get('text_share', 0)}%, обычно {rc.get('words', 0)} сл. на превью" + (f"; цвета — {col}." if col else "."), "thCompSec"))
    steps = []
    if bs:
        steps.append(f"оформляйте превью в стиле «{bs['name'].lower()}»")
    if hk:
        steps.append(f"в названия добавляйте «{hk[0]['name'].lower()}»")
    if vis and (vis[0].get("ratio") or 1) < 0.7:
        steps.append(f"уберите {vis[0]['name'].lower()}")
    if rc.get("n"):
        steps.append("надпись на превью — " + ("обязательно" if rc.get("text_share", 0) >= 60 else "по желанию") + (f", до {max(2, rc.get('words', 0) + 1)} слов" if rc.get("text_share", 0) >= 30 else ""))
    if steps:
        t = "; ".join(steps)
        out.append(_b("Что делать:", t[0].upper() + t[1:] + ". Проверить новый вариант до публикации — «Тест превью».", "thSumSec"))
    return {"title": f"Превью канала «{ch.get('title', '')}»", "items": out} if out else None


# ---------------------------------------------------------------- поиск по теме
def topic(d):
    subs = d.get("subniches") or []
    if not subs:
        return None
    out = []
    b = subs[0]
    out.append(_b(f"Лучшая подниша — «{b['name']}» ({b['score']} из 100).",
                  f"Растущих новичков {b.get('young_rising', 0)} из {b.get('channels')} каналов, обычно {N(b.get('median_views'))} просмотров на видео.", "subList"))
    weak = [s for s in subs if s["score"] < 45]
    ok = [s for s in subs if s["score"] >= 60]
    out.append(_b(f"Проверено {len(subs)} подниш, {d.get('channels_checked', '—')} каналов.",
                  (f"Стоит смотреть: " + ", ".join(f"«{s['name']}» ({s['score']})" for s in ok[:4]) + "." if ok else "Сильных подниш не нашлось — тема плотно занята.")
                  + (f" Слабые: " + ", ".join(f"«{s['name']}»" for s in weak[:3]) + "." if weak else ""), "subList"))
    out.append(_b("Что делать:", f"Откройте «Подробный анализ» у «{b['name']}» — сервис разберёт её по каналу-примеру, с идеями роликов и планом входа.", "subList"))
    return {"title": f"Тема «{d.get('topic', '')}»", "items": out}


def build(d):
    t = d.get("type")
    try:
        if t == "thumbs":
            return thumbs(d)
        if t == "topic":
            return topic(d)
        if t in (None, "channel") and "niche" in d:
            return niche(d)
    except Exception:
        return None
    return None
