"""
Отчёт одним файлом для чата с нейросетью (ChatGPT, Claude и др.): Markdown со всеми данными.
"""
import re
import datetime as dt

URL_VIDEO = "https://www.youtube.com/watch?v="


def n(x):
    if x is None:
        return "—"
    x = float(x)
    for lim, suf in ((1e9, " млрд"), (1e6, " млн"), (1e3, " тыс.")):
        if abs(x) >= lim:
            return f"{x / lim:.1f}".rstrip("0").rstrip(".").replace(".", ",") + suf
    return str(int(x)) if x == int(x) else f"{x:.2f}".replace(".", ",")


def xf(x):
    return "—" if x is None else f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "×"


def age(d):
    if d is None:
        return "—"
    return f"{d} дн." if d < 60 else f"{round(d / 30)} мес." if d < 730 else f"{d / 365:.1f} г.".replace(".", ",")


def cell(s):
    return re.sub(r"\s+", " ", str("" if s is None else s)).replace("|", "/").strip()


def table(head, rows):
    if not rows:
        return "_нет данных_\n"
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


INTRO = """> Файл выгружен из сервиса Niche Finder (разведка ниш YouTube, данные собраны с YouTube через yt-dlp {date}).
> Как использовать: загрузите файл в чат с нейросетью и напишите, например:
> «{ask}»
"""


# ---------------------------------------------------------------- отчёт по нише
FMT = {"long": "длинные видео", "shorts": "Shorts", "mixed": "Shorts и длинные"}
SCORE_NAMES = {"demand": "Спрос", "competition": "Конкуренция (чем меньше, тем легче)", "entry_window": "Окно входа для новичков",
               "monetization": "Монетизация", "freshness": "Свежесть ниши"}


def niche_md(d):
    c, ni = d["competitor"], d["niche"]
    ex = set(d.get("excluded") or [])
    L = [f"# Отчёт по нише: {c['title']}\n",
         INTRO.format(date=d.get("generated_at", ""), ask="Проанализируй этот отчёт по нише YouTube и предложи план канала: 10 тем первых видео, формат, частоту выпуска и как обогнать конкурентов."),
         "## Итог\n",
         f"- **Оценка ниши:** {ni['overall']} из 100",
         f"- **Вывод:** {ni.get('verdict', '')}",
         f"- Проверено каналов: {ni.get('analyzed')}, молодых (до {ni.get('max_age_days')} дн.): {ni.get('young')}, из них растут: {ni.get('young_rising')}",
         f"- Типичные просмотры видео в поиске ниши: {n(ni.get('median_search_views'))}; типичный канал: {n(ni.get('median_subs'))} подписчиков; доля крупных каналов (500 тыс.+): {ni.get('big_channels_share')}%",
         f"- Тематика для рекламы: {ni.get('rpm_category')}, доход ~${'–'.join(str(x) for x in ni.get('rpm_range') or [])} за 1000 просмотров",
         f"- Лучшая длина видео: {ni.get('best_duration') or '—'}",
         f"- Ключевые слова поиска: {', '.join(d.get('keywords') or [])}\n",
         table(["Показатель", "Оценка 0–100"], [[SCORE_NAMES.get(k, k), v] for k, v in (ni.get("scores") or {}).items()])]
    V = d.get("verification")
    if V:
        L.append(f"## Проверка данных — надёжность: {V.get('level')}\n")
        if V.get("range"):
            L.append(f"При другом наборе каналов оценка была бы {V['range'][0]}–{V['range'][1]}.\n")
        L += [f"- {'✓' if c['state'] == 'ok' else '!' if c['state'] == 'warn' else '✕'} **{c['title']}:** {c['detail']}" for c in V.get("checks") or []]
        L.append("")
    if d.get("plan"):
        L.append("## План входа\n")
        plan = d["plan"] if isinstance(d["plan"], list) else [d["plan"]]
        L += [f"- {cell(p if isinstance(p, str) else (p.get('title', '') + ': ' + p.get('text', '')))}" for p in plan]
        L.append("")
    L += ["## Канал конкурента\n",
          f"- {c['title']} — {c['url']}",
          f"- Подписчики: {n(c.get('subs'))}; медиана просмотров: {n(c.get('median_views'))}; видео в неделю: {c.get('uploads_per_week')}",
          f"- Формат: {FMT.get(c.get('fmt'), c.get('fmt'))}; длинные — медиана {n(c.get('long_median'))}, Shorts — медиана {n(c.get('shorts_median'))}",
          f"- Средняя длина видео: {round((c.get('avg_duration') or 0) / 60)} мин; доля хитов: {c.get('hit_rate')}%",
          f"- Теги канала: {', '.join(c.get('channel_tags') or [])}",
          "- Хиты: " + "; ".join(f"«{h['title']}» ({n(h.get('views'))})" for h in c.get("hit_videos") or []), ""]
    if d.get("examples"):
        L.append("Каналы-примеры: " + ", ".join(e.get("title", "") for e in d["examples"]) + "\n")
    if d.get("exclude_words"):
        L.append("Слова-исключения: " + ", ".join(d["exclude_words"]) + "\n")
    dy = d.get("dynamics") or {}
    if dy:
        L += ["## Пульс ниши\n",
              f"- Видео за последний месяц: {dy.get('month_videos')} от {dy.get('month_channels')} каналов, медиана просмотров {n(dy.get('month_median_views'))}",
              f"- Каналы ускоряются: {dy.get('accelerating')}, замедляются: {dy.get('slowing')} (из {dy.get('trend_known')})",
              "- Языки: " + ", ".join(f"{x['name']} {x['share']}%" for x in dy.get("languages") or []), ""]
    ch = [x for x in d.get("channels") or []]
    rel = [x for x in ch if x.get("relevant", True) and x.get("id") not in ex]
    L.append(f"## Каналы ниши ({len(rel)})\n")
    L.append(table(["Канал", "Ссылка", "Подписчики", "Возраст", "Молодой", "Оценка роста", "Медиана просмотров", "Просмотров в день",
                    "Видео/нед", "Формат", "Тренд", "Похожесть %", "Теги"],
                   [[x["title"], x["url"], n(x.get("subs")), age(x.get("age_days")), "да" if x.get("young") else "",
                     x.get("rising_score"), n(x.get("median_views")), n(x.get("views_per_day")), x.get("uploads_per_week"),
                     FMT.get(x.get("fmt"), x.get("fmt")), xf(x.get("trend")), x.get("relevance"), ", ".join((x.get("channel_tags") or [])[:6])]
                    for x in sorted(rel, key=lambda x: -(x.get("rising_score") or 0))]))
    off = [x for x in ch if x not in rel]
    if off:
        L.append("Не вошли в нишу: " + "; ".join(f"{x['title']} ({'исключён вручную' if x.get('id') in ex else x.get('off_reason') or 'другая тема'})" for x in off) + "\n")
    vi = d.get("video_ideas") or {}
    if vi.get("formats") or vi.get("ideas"):
        L.append("## Идеи роликов\n")
        L.append(f"Проверено {vi.get('checked_videos')} роликов каналов ниши за ~5 месяцев; {vi.get('hits')} набрали ×2+ от обычного для своего канала "
                 "(«×N» — во сколько раз больше медианы просмотров этого канала).\n")
        if vi.get("formats"):
            L.append("### Форматы, которые выстреливают (одна формулировка — разный объект)\n")
            for f in vi["formats"]:
                L.append(f"- **«{f['template']}»** — {f['videos']} роликов, {f['channels']} канал(ов), обычно ×{f['median_ratio']}. {f['why']}")
                L += [f"  - ×{v['ratio']} «{v['title']}» — {v['channel']}, {n(v['views'])} просм., {v['age']} дн. назад — {URL_VIDEO}{v['id']}" for v in f["evidence"][:3]]
            L.append("")
        if vi.get("ideas"):
            L.append("### Темы на подъёме\n")
            for t in vi["ideas"]:
                dm = t.get("demand") or {}
                L.append(f"#### {t['name']} — {t['label']}\n")
                L.append(t["why"])
                L.append(f"- Выстрелили {t['videos']} роликов у {t['channels']} каналов, обычно ×{t['median_ratio']}, лучший ×{t['best_ratio']}; последний {t['newest_days']} дн. назад."
                         + (f" Поиск YouTube за 3 месяца: {dm['fresh']} свежих роликов, медиана {n(dm['fresh_median'])} просмотров." if dm.get("fresh") is not None else ""))
                L += [f"- ×{v['ratio']} «{v['title']}» — {v['channel']}, {n(v['views'])} просм. (обычно {n(v['norm'])}), {v['age']} дн. назад — {URL_VIDEO}{v['id']}" for v in t["evidence"][:4]]
                L += [f"- Как подать: {x}" for x in t["tips"]]
                L.append("")
    nb = (d.get("neighbors") or {}).get("rows") or []
    if nb:
        L.append("## Соседние ниши\n")
        L.append("Смежные темы, оценённые теми же формулами (0–100), для сравнения с этой нишей.\n")
        for r in nb:
            sc = r["scores"]
            L.append(f"- **{r['name']}** — оценка {r['score']}" + (f" (обычно {r['range'][0]}–{r['range'][1]})" if r.get("range") else "")
                     + (f", {r['delta']:+d} к этой нише" if r.get("delta") is not None else "")
                     + f"; окно входа {sc['entry_window']}, конкуренция {sc['competition']}, спрос {sc['demand']}; "
                     + f"растут {r['young_rising']} из {r['channels']} каналов; медиана {r['median_views']} просмотров; надёжность {r['reliability']}")
        L.append("")
    hv = []
    for x in rel:
        for h in x.get("hit_videos") or []:
            hv.append([h.get("title"), x["title"], n(h.get("views")), URL_VIDEO + str(h.get("id"))])
    if d.get("entry_topics"):
        L.append("## Темы, которые выстрелили у молодых каналов\n")
        L.append(table(["Видео", "Канал", "Просмотры", "Во сколько раз выше обычного", "Ссылка"],
                       [[v["title"], v.get("channel"), n(v.get("views")), xf(v.get("x_median")), URL_VIDEO + v["id"]] for v in d["entry_topics"]]))
    if hv:
        L.append("## Хиты каналов ниши\n")
        L.append(table(["Видео", "Канал", "Просмотры", "Ссылка"], hv[:60]))
    if d.get("keyword_table"):
        L.append("## Ключевые запросы\n")
        L.append(table(["Запрос", "Медиана просмотров", "Каналов", "Доля молодых %", "Оценка"],
                       [[k["keyword"], n(k.get("median_views")), k.get("channels"), k.get("young_share"), k.get("score")] for k in d["keyword_table"]]))
    if d.get("searches"):
        L.append("## Что ищут зрители (подсказки YouTube)\n")
        L += [f"- **{s['keyword']}:** {', '.join(s.get('queries') or [])}" for s in d["searches"]]
        L.append("")
    if d.get("durations"):
        L.append("## Какая длина видео работает\n")
        L.append(table(["Длина", "Видео", "Успех относительно канала", "Доля хитов %"],
                       [[b["bucket"], b.get("videos"), xf(b.get("perf")), b.get("hit_share")] for b in d["durations"]]))
    t = d.get("titles") or {}
    if t.get("features"):
        L.append("## Что работает в заголовках\n")
        L.append(f"Длина заголовка у хитов ~{t.get('len_hit')} знаков (у всех {t.get('len_all')}). Хитом становится {t.get('base_hit_rate')}% роликов.\n")
        L.append(table(["Приём", "Доля роликов %", "Доля хитов среди них %", "Подъём"],
                       [[f["name"], f["share"], f["hit_rate"], xf(f.get("lift"))] for f in t["features"]]))
        if t.get("hit_words"):
            L.append("Слова хитов: " + ", ".join(f"{w['word']} ({xf(w['lift'])})" for w in t["hit_words"]) + "\n")
        if t.get("gaps"):
            L.append("Слова хитов, которых нет у конкурента: " + ", ".join(w["word"] for w in t["gaps"]) + "\n")
    fm = d.get("formats") or {}
    if fm:
        L.append("## Shorts или длинные видео\n")
        if fm.get("recommendation"):
            L.append(fm["recommendation"] + "\n")
        L.append(table(["Формат", "Каналов", "Молодых", "Медиана просмотров", "У молодых", "Просмотров на подписчика у молодых"],
                       [[nm, x.get("channels"), x.get("young"), n(x.get("median_views")), n(x.get("young_median_views")), x.get("young_views_per_sub")]
                        for nm, x in (("Длинные", fm.get("long")), ("Shorts", fm.get("shorts"))) if x]))
    fc = d.get("forecast") or {}
    if fc.get("subs_90"):
        L.append("## Прогноз роста нового канала\n")
        L.append(f"По {fc.get('n')} молодым каналам ниши (слабый / типичный / сильный результат):\n")
        L.append(table(["Срок", "Подписчики"], [[lbl, " / ".join(n(v) for v in fc.get(k) or [])]
                                                for lbl, k in (("3 месяца", "subs_90"), ("6 месяцев", "subs_180"), ("12 месяцев", "subs_365"))]))
        if fc.get("views_month"):
            L.append("Просмотров в месяц: " + " / ".join(n(v) for v in fc["views_month"]) + "\n")
    if d.get("gallery"):
        L.append("## Галерея хитов\n")
        L.append(table(["Видео", "Канал", "Просмотры", "Во сколько раз выше обычного", "Ссылка"],
                       [[v["title"], v.get("channel"), n(v.get("views")), xf(v.get("x_median")), URL_VIDEO + v["id"]] for v in d["gallery"]]))
    return "\n".join(L)


# ---------------------------------------------------------------- поиск по теме
def topic_md(d):
    L = [f"# Поиск ниш по теме: {d.get('topic')}\n",
         INTRO.format(date=d.get("generated_at", ""), ask="Выбери из этих подниш лучшую для нового канала, объясни почему и предложи 10 тем первых видео."),
         f"**Вывод:** {d.get('verdict', '')}\n", f"Проверено каналов: {d.get('channels_checked')}\n",
         "## Подниши\n",
         table(["Подниша", "Оценка", "Спрос", "Конкуренция", "Окно входа", "Монетизация", "Медиана просмотров", "Каналов", "Молодых", "Растущих новичков", "Доход за 1000"],
               [[s["name"], s["score"], s["scores"].get("demand"), s["scores"].get("competition"), s["scores"].get("entry_window"),
                 s["scores"].get("monetization"), n(s.get("median_views")), s.get("channels"), s.get("young"), s.get("young_rising"),
                 "$" + "–".join(str(x) for x in s.get("rpm_range") or [])] for s in d.get("subniches") or []])]
    for s in d.get("subniches") or []:
        L.append(f"### {s['name']} — {s['score']}\n")
        if s.get("examples"):
            L.append("Каналы: " + "; ".join(f"{e['title']} ({n(e.get('subs'))} подп., {age(e.get('age_days'))}) {e['url']}" for e in s["examples"]) + "\n")
        if s.get("top_videos"):
            L.append("Топ видео: " + "; ".join(f"«{v['title']}» ({n(v.get('views'))}, {v.get('channel')})" for v in s["top_videos"]) + "\n")
    return "\n".join(L)


# ---------------------------------------------------------------- превью
def _group_md(g, who):
    if not g:
        return [f"### {who}\n", "_нет данных_\n"]
    L = [f"### {who}: {g['n']} превью, хитов {g['hits']}, слабых {g['flops']}\n",
         "**Стили превью** (успех = просмотры относительно соседних роликов того же канала, 1× — как обычно):\n",
         table(["Стиль", "Превью", "Доля %", "Обычный успех", "Доля хитов %"],
               [[s["name"], s["n"], s["share"], xf(s["median"]), s["hit_rate"]] for s in g["styles"]]),
         "**Что влияет на просмотры** (с признаком / без него):\n",
         table(["Признак", "С ним", "Без него", "Разница", "Превью с признаком", "Доля среди хитов %", "Надёжность"],
               [[e["name"], xf(e["with"]), xf(e["without"]), xf(e["ratio"]), e["n_with"], e["share_hits"], e["confidence"]] for e in g["visual"]]),
         "**Приёмы в названиях:**\n",
         table(["Приём", "С ним", "Без него", "Разница", "Роликов", "Надёжность"],
               [[e["name"], xf(e["with"]), xf(e["without"]), xf(e["ratio"]), e["n_with"], e["confidence"]] for e in g["hooks"]])]
    rc = g.get("recipe")
    if rc:
        L.append(f"**Как выглядят хиты:** лицо в {rc['face_share']}% (обычно {rc['face_pct']}% кадра, {rc.get('face_side') or '—'}), "
                 f"текст в {rc['text_share']}% (обычно {rc['words']} слов), яркость {rc['bright']}/100, насыщенность {rc['sat']}/100, "
                 f"цвета: {', '.join(rc['colors'])} ({', '.join(rc['swatches'])})\n")
    if g.get("title_len"):
        L.append(f"Длина названия у хитов: ~{g['title_len']} знаков\n")
    L.append("**Лучшие превью:**\n")
    L.append(table(["Название", "Канал", "Просмотры", "Успех", "Текст на превью", "Признаки", "Ссылка"],
                   [[v["title"], v.get("channel"), n(v["views"]), xf(v["perf"]), v.get("text"), ", ".join(v["tags"] + v.get("hooks", [])), URL_VIDEO + v["id"]] for v in g["top"]]))
    if g.get("weak"):
        L.append("**Слабые превью:**\n")
        L.append(table(["Название", "Просмотры", "Успех", "Текст на превью", "Признаки", "Ссылка"],
                       [[v["title"], n(v["views"]), xf(v["perf"]), v.get("text"), ", ".join(v["tags"]), URL_VIDEO + v["id"]] for v in g["weak"]]))
    return L


def thumbs_md(d):
    ch = d["channel"]
    comps = d.get("competitors") or []
    L = [f"# Разбор превью: {ch['title']}\n",
         INTRO.format(date=d.get("generated_at", ""), ask="На основе этого разбора превью придумай 5 вариантов превью и названий для моего следующего ролика, опираясь на то, что реально поднимает просмотры."),
         f"- Канал: {ch['title']} — {ch['url']} ({n(ch.get('subs'))} подписчиков)",
         f"- Конкуренты{' (найдены автоматически)' if d.get('competitors_auto') else ''}: " + ("; ".join(f"{c['title']} {c['url']}" for c in comps) or "—"),
         *([f"- Отсеяно похожих каналов: " + "; ".join(f"{r.get('title')} ({r.get('reason')})" for r in d.get("rejected") or [])] if d.get("rejected") else []),
         "- Как считается успех превью: просмотры ролика делятся на медиану 10 соседних роликов того же канала. Надёжность — перестановочный тест: «низкая» значит, что разница может быть случайной.\n",
         *([f"- **Новый канал:** роликов {d['new_channel']['videos']} — для своей статистики нужно хотя бы 8 старше недели, поэтому выводы ниже по хитам похожих каналов ниши.\n"] if d.get("new_channel") else []),
         "## Коротко: что заходит\n"] + [f"- {x}" for x in d.get("summary") or []] + [""]
    bs = d.get("best_style")
    if bs:
        L += ["## Лучший стиль\n", f"**{bs['name']}** — {bs['desc']}." + ("" if d.get("new_channel") else f" У вас таким стилем сделано {bs['yours_share']}% превью" +
              (f"; чаще всего у вас «{bs['closest']['name']}» ({bs['closest']['share']}%)." if bs.get("closest") else ".")), ""]
    L.append("## Подробно\n")
    if d.get("new_channel"):
        if d.get("own_vs"):
            L.append("### Ваши превью рядом с хитами ниши\n")
            L += [f"- «{v['title']}» ({n(v['views'])} просмотров, стиль «{v['style']}»): есть — {', '.join(v['has']) or '—'}; не хватает — {', '.join(v['missing']) or '—'}"
                  + (f"; лучше убрать — {', '.join(v['avoid'])}" if v.get("avoid") else "") + f" — {URL_VIDEO}{v['id']}" for v in d["own_vs"]]
            L.append("")
    else:
        L += _group_md(d.get("own"), "Ваш канал")
    L += _group_md(d.get("comp"), "Ниша (похожие каналы)" if d.get("new_channel") else "Конкуренты")
    if d.get("copy"):
        L.append("## Что взять из хитов ниши\n" if d.get("new_channel") else "## Что взять у конкурентов\n")
        L += [f"- **{c['name']}** ({c['kind']}): {xf(c['ratio'])} {'в нише' if d.get('new_channel') else 'у конкурентов'}. {c['text']}" for c in d["copy"]]
        L.append("")
    a = d.get("attract") or {}
    L.append("## Чем зацепить\n")
    L += [f"- Приём «{t['name']}»: {xf(t['ratio'])} (надёжность {t['confidence']}). Примеры: " + "; ".join(f"«{x}»" for x in t.get("examples") or []) for t in a.get("triggers") or []]
    if a.get("pains"):
        L.append("\n**Боль зрителя:** " + "; ".join(a["pains"]))
    if a.get("promises"):
        L.append("\n**Что пообещать:** " + "; ".join(a["promises"]))
    L.append("")
    sc = d.get("script")
    if sc:
        L += ["## Что найдено в сценарии\n", f"- Тема: {sc.get('topic')}", f"- Названия и бренды: {', '.join(sc.get('entities') or [])}",
              f"- Ключевые слова: {', '.join(sc.get('terms') or [])}", f"- Цифры: {', '.join(sc.get('numbers') or [])}",
              f"- Сравнение: {' vs '.join(sc['versus']) if sc.get('versus') else '—'}", ""]
    for i, c in enumerate(d.get("concepts") or [], 1):
        L += [f"## Вариант {i}: «{c['name']}»\n", f"Почему работает: {c['why']}." +
              (f" В названиях ниши этот приём даёт {xf(c['evidence']['ratio'])} ({c['evidence']['n']} роликов, надёжность {c['evidence']['confidence']})." if c.get("evidence") else ""),
              "", "**Текст на превью:** " + (" / ".join(c["thumb_text"]) or "без текста"),
              f"**Эмоция:** {c.get('emotion') or 'без человека в кадре'}", "", "**Названия:**"]
        L += [f"{j}. {t}" for j, t in enumerate(c["titles"], 1)]
        L += ["", "**Промт (русский):**", "", "```", c["prompt_ru"], "```", "", "**Промт (английский):**", "", "```", c["prompt_en"], "```", ""]
    return "\n".join(L)


def tfmt(s):
    if s is None:
        return "—"
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def teardown_md(d):
    v, nn, ts, th = d["video"], d.get("channel_norm") or {}, d.get("transcript"), d.get("thumb")
    L = [f"# Разбор ролика: {v['title']}\n",
         INTRO.format(date=d.get("generated_at", ""), ask="Разбери, почему этот ролик выстрелил, и предложи 5 идей роликов для моего канала по тому же принципу."),
         f"- **Ссылка:** {URL_VIDEO}{v['id']}", f"- **Канал:** {v.get('channel') or '—'} ({n(v.get('subs'))} подписчиков)",
         f"- **Просмотры:** {n(v.get('views'))} — в {xf(d.get('ratio'))} больше обычного ролика канала ({n(nn.get('median'))})",
         f"- **Длина:** {tfmt(v.get('duration'))} (обычно на канале {tfmt(nn.get('dur_med'))})",
         f"- **Возраст:** {age(v.get('age_days'))}", f"- **Лайков от просмотров:** {d.get('engagement') if d.get('engagement') is not None else '—'}%",
         f"- **Приёмы в названии:** {', '.join(d.get('hooks') or []) or 'нет явных'}", "",
         "## Почему зашёл", ""] + [f"- {x}" for x in d.get("why") or ["—"]] + ["", "## Что повторить", ""] + [f"- {x}" for x in d.get("repeat") or ["—"]]
    fl = d.get("flop")
    if d.get("mode") == "flop" and fl:
        L[0] = f"# Почему ролик не зашёл: {v['title']}\n"
        L += ["", "## Что пошло не так (сравнение с хитами канала)", ""] + [f"- {x}" for x in fl.get("why_not") or ["—"]]
        L += ["", "## Что исправить в следующем", ""] + [f"- {x}" for x in fl.get("fix") or ["—"]]
        L += ["", "## Хиты канала, с которыми сравнили", ""] + [f"- {h.get('title')} — {n(h.get('views'))} просм. {URL_VIDEO}{h['id']}" for h in fl.get("hits") or []]
    if th:
        L += ["", "## Превью", "", f"- Лицо: {'да, ' + str(th['me'].get('face_pct')) + '% площади' if th['me'].get('faces') else 'нет'}",
              f"- Текст: {th['me'].get('text') or 'нет'}"] + [f"- {x}" for x in th.get("diff") or []]
    if ts:
        L += ["", "## Структура по субтитрам", "", f"- Темп: {ts.get('wpm')} слов/мин",
              f"- Вопрос зрителю: {tfmt(ts.get('question_at'))}", f"- Обещание: {tfmt(ts.get('promise_at'))}",
              f"- Призыв подписаться: {tfmt(ts.get('first_cta'))}", f"- Концовка: {tfmt(ts.get('outro_at'))}", "",
              "**Первые 30 секунд:**", "", "> " + (ts.get("first30") or "—")]
        if ts.get("chapters"):
            L += ["", "**Главы:**", ""] + [f"- {tfmt(c['t'])} {c.get('title') or ''}" for c in ts["chapters"]]
    if v.get("tags"):
        L += ["", "## Теги", "", ", ".join(v["tags"])]
    if v.get("description"):
        L += ["", "## Описание (начало)", "", "```", v["description"], "```"]
    return "\n".join(L)


def script_md(d):
    L = [f"# Каркас сценария: ниша «{d.get('source_title') or ''}»\n",
         INTRO.format(date=d.get("generated_at", ""), ask="Напиши по этому каркасу полный сценарий ролика на тему: …"),
         f"- Проанализировано хитов: {d.get('analyzed')}", f"- Длина ролика: {tfmt(d.get('target'))} (обычно у хитов {tfmt(d.get('median_duration'))})",
         f"- Обещание звучит на: {tfmt(d.get('promise_at'))}", f"- Призыв подписаться: {tfmt(d.get('cta_at'))}",
         f"- Темп речи: {round(d['wpm']) if d.get('wpm') else '—'} слов/мин", "", "## Правила ниши", ""] + [f"- {x}" for x in d.get("rules") or []]
    L += ["", "## План с таймингом", ""] + [f"- **{p['from_t']}–{p['to_t']} {p['name']}.** {p['what']}" for p in d.get("plan") or []]
    L += ["", "## Как начинают хиты ниши (первые 30 секунд)", ""]
    for x in d.get("examples") or []:
        L += [f"### {x.get('title') or ''}", f"{URL_VIDEO}{x['id']} · {x.get('channel') or ''} · {n(x.get('views'))} просм. · {tfmt(x.get('duration'))}", "", "> " + (x.get("first30") or ""), ""]
    return "\n".join(L)


def thumbtest_md(d):
    ns = d.get("niche_stats") or {}
    mark = lambda c: "✓" if c["ok"] is True else "✕" if c["ok"] is False else "•"
    L = [f"# Тест превью и названия: ниша «{d.get('source_title') or ''}»\n",
         INTRO.format(date=d.get("generated_at", ""), ask="Вот проверка моих превью и названий против хитов ниши. Предложи 5 улучшенных названий и что поменять на превью."),
         f"- Сравнено с {ns.get('videos')} хитами ниши: лицо у {round(100 * (ns.get('face_share') or 0))}%, надпись у {round(100 * (ns.get('text_share') or 0))}%", "",
         "## Превью", ""]
    for v in d.get("variants") or []:
        L += [f"### Вариант {v['n']} — {v['score']} из 100" + (" (лучший)" if v["n"] == d.get("best_thumb") else ""),
              f"Текст на превью: {v.get('text') or 'не распознан'}", ""] + [f"- {mark(c)} **{c['label']}.** {c['detail']}" for c in v["checks"]] + [""]
    if d.get("titles"):
        L += ["## Названия", ""]
        for v in d["titles"]:
            L += [f"### «{v['text']}» — {v['score']} из 100" + (" (лучшее)" if v["n"] == d.get("best_title") else "")] + [f"- {mark(c)} **{c['label']}.** {c['detail']}" for c in v["checks"]] + [""]
    L += ["## Хиты ниши, с которыми сравнивали", ""] + [f"- {x.get('title')} — {x.get('channel')}, {n(x.get('views'))} просм. {URL_VIDEO}{x['id']}" for x in d.get("niche") or []]
    return "\n".join(L)


def fresh_md(d):
    L = [f"# Идеи сейчас: {d.get('source_title') or ''}\n",
         INTRO.format(date=d.get("generated_at", ""), ask="Вот темы, которые выстрелили в моей нише за 3 недели, и сколько у них копий. Для тем «Свежо» и «Догоняют» предложи по 3 своих угла подачи и названия — не копии."),
         f"- Проверено каналов: {d.get('channels_checked')}", ""]
    for i in d.get("ideas") or []:
        v = i["verdict"]
        L += [f"## {v['icon']} {v['label']}: {i['title']}",
              f"{i.get('channel')} · {n(i.get('views'))} просм. · {i.get('age')} дн. назад · ×{i.get('x')} к обычным роликам канала · {URL_VIDEO}{i['id']}",
              "", v["text"]]
        if i.get("also"):
            L += ["Та же тема выстрелила ещё у: " + ", ".join(f"{a['channel']} (×{a['x']})" for a in i["also"])]
        if i.get("copies") and i["copies"].get("items"):
            L += ["", f"Похожие ролики за 2 недели ({i['copies']['n']}):"] + [f"- {x['title']} — {x['channel']}, {n(x['views'])} просм." for x in i["copies"]["items"]]
        L += [""]
    tr = d.get("transfer") or {}
    if tr.get("items"):
        L += ["", "## Подачи, которые работают у соседних каналов (а у вас их почти нет)", ""]
        for it in tr["items"]:
            L += [f"### {it['name']} — {xf(it['x'])}, {it['videos']} взлётов у {it['channels']} каналов, у вас: {it['own_share']}%"]
            L += [f"- {e['title']} — {e.get('channel')}, {xf(e['x'])} {URL_VIDEO}{e['id']}" for e in it["examples"]]
            L += ["Заготовки: " + " / ".join(f"«{t}»" for t in it["drafts"]), ""]
    return "\n".join(L)


def molecules_md(d):
    L = [f"# Разбор сценария на молекулы: {d.get('title')}\n",
         INTRO.format(date=d.get("generated_at", ""), ask="Перепиши мой сценарий с учётом всех замечаний ниже. Сохрани смысл и мой стиль, исправь хук, мёртвые зоны и фразы под замену."),
         f"- Канал: {(d.get('channel') or {}).get('title')}", f"- Оценка: {d.get('score')} из 100",
         f"- Фраз: {d.get('sentences')}, слов: {d.get('words')}, выйдет ~{d.get('est_duration')} сек при {d.get('wpm')} слов/мин",
         f"- Сравнено: {d['counts']['hits']} ваших хитов, {d['counts']['flops']} провалов, {d['counts']['comp']} топ-роликов по теме", "",
         "## Фразы под замену", ""]
    for i, m in enumerate(d.get("molecules") or [], 1):
        L += [f"{i}. **{m['where']}** ({'тянет вниз' if m['level'] == 'bad' else 'улучшить'})", f"   > {m['quote']}",
              f"   - Проблема: {m['problem']}", f"   - Как исправить: {m['fix']}"]
    L += ["", "## Что заходило у вас — есть и тут", ""] + [f"- {x['text']}" for x in d.get("good") or []] or ["- —"]
    L += ["", "## Что тянуло вниз — повторяется", ""] + [f"- {x['text']}" for x in d.get("risk") or []]
    L += ["", "## Что делают топ-ролики по теме — а тут нет", ""] + [f"- {x['text']}" for x in d.get("comp_diff") or []]
    L += ["", "## Все признаки", "", "| Признак | Сценарий | Ваши хиты | Провалы | Топ по теме |", "|---|---|---|---|---|"]
    L += [f"| {r['name']} | {r['me_t']} | {r['hits_t']} | {r['flops_t']} | {r['comp_t']} |" for r in d.get("rows") or []]
    for name, key in (("Так начинаются ваши хиты", "hits_ex"), ("Так начинаются топ-ролики по теме", "comp_ex")):
        if d.get(key):
            L += ["", f"## {name}", ""] + [f"- **{x['title']}** ({x.get('channel')}): «{x['first30']}»" for x in d[key]]
    return "\n".join(L)


def build(d):
    kind = d.get("type")
    md = (topic_md(d) if kind == "topic" else thumbs_md(d) if kind == "thumbs" else teardown_md(d) if kind == "teardown"
          else molecules_md(d) if kind == "molecules"
          else script_md(d) if kind == "script" else thumbtest_md(d) if kind == "thumbtest" else fresh_md(d) if kind == "fresh" else niche_md(d))
    try:
        import brief
        b = brief.build(d)
    except Exception:
        b = None
    if b and b.get("items"):
        head, _, rest = md.partition("\n")
        md = head + "\n\n## Главное\n\n" + "\n".join(f"- **{i['lead']}** {i['text']}" for i in b["items"]) + "\n" + rest
    return md.rstrip() + "\n"


def filename(d):
    kind = d.get("type")
    name = (d.get("topic") if kind == "topic" else (d.get("channel") or {}).get("title") if kind == "thumbs"
            else d.get("title") if kind == "molecules"
            else (d.get("video") or {}).get("title") if kind == "teardown" else d.get("source_title") if kind in ("script", "thumbtest", "fresh")
            else (d.get("competitor") or {}).get("title"))
    pre = {"topic": "темы", "thumbs": "превью", "teardown": "разбор", "script": "сценарий", "thumbtest": "тест_превью", "fresh": "идеи_сейчас", "molecules": "сценарий_молекулы"}.get(kind, "ниша")
    safe = re.sub(r"[^\w\-]+", "_", name or "отчёт", flags=re.U).strip("_")[:50]
    return f"{pre}_{safe}_{dt.date.today().isoformat()}.md"
