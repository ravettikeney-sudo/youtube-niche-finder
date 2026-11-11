"""
Насыщенность темы: сколько похожих роликов (копий) вышло за последние дни и как они набирают.
Нужна для «Идей сейчас» (свежая тема или уже выжжена) и для разбора провала (ролик утонул среди копий).
"""
import datetime as dt
import statistics

import analyzer as A
import ideas
import niche

SP_WEEK = "EgIIAw%3D%3D"     # загружено: за эту неделю
SP_MONTH = "EgIIBA%3D%3D"    # за этот месяц


def _terms(title):
    return set(ideas._stems(ideas._content(ideas.main_part(title))))


def query_for(title, n=4):
    """Поисковый запрос из названия: первые значимые слова главной части."""
    words = ideas._content(ideas.main_part(title))
    seen, out = set(), []
    for w in words:
        st = ideas._st(w)
        if st in seen or len(w) < 3:
            continue
        seen.add(st)
        out.append(w)
        if len(out) >= n:
            break
    return " ".join(out)


def _search_dated(q, sp, ylang):
    """Поиск с датами: даты yt-dlp понимает только в английском интерфейсе — подтягиваем их по id."""
    res = A.search(q, 40, sp, ylang) or []
    if res and sum(1 for e in res if A._entry_date(e)) < 0.5 * len(res):
        code, region = niche.split_ylang(ylang) if ylang else (None, None)
        try:
            dated = {e["id"]: e for e in (A.search(q, 40, sp, niche.ylang_for("en", region)) or [])}
        except Exception:
            dated = {}
        res = [dict(e, timestamp=(dated.get(e["id"]) or {}).get("timestamp"), upload_date=(dated.get(e["id"]) or {}).get("upload_date")) for e in res]
    return res


def copies(title, ylang=None, script_want=None, around=None, exclude_ids=(), exclude_channel=None, window_days=14):
    """Похожие ролики: те же ключевые слова в названии, вышли рядом по времени.
    around — дата выхода ролика (для разбора провала), иначе «сейчас». Возвращает None, если искать не по чему."""
    terms = _terms(title)
    if len(terms) < 2:
        return None
    q = query_for(title)
    if not q:
        return None
    today = dt.date.today()
    center = around or today
    sp = SP_MONTH if (today - center).days <= 25 else ""
    try:
        res = _search_dated(q, sp, ylang)
    except Exception:
        return None
    need = max(2, int(len(terms) * 0.5 + 0.5))
    sim = []
    for e in res:
        if e.get("id") in exclude_ids or (exclude_channel and e.get("channel_id") == exclude_channel):
            continue
        if script_want and not niche.same_script(e.get("title"), script_want):
            continue
        if len(_terms(e.get("title") or "") & terms) < min(need, 3):
            continue
        d = A._entry_date(e)
        if d is None or abs((d - center).days) > window_days:
            continue
        sim.append({"id": e["id"], "title": e.get("title"), "channel": e.get("channel") or e.get("uploader"),
                    "views": e.get("view_count") or 0, "days": (today - d).days, "date": d.isoformat()})
    sim.sort(key=lambda x: -x["views"])
    if not sim:
        return {"query": q, "n": 0, "n_week": 0, "items": [], "weak": 0, "top_views": 0, "median": 0}
    top = sim[0]["views"]
    weak = sum(1 for s in sim if top and s["views"] < top * 0.1)
    near = [s for s in sim if abs((dt.date.fromisoformat(s["date"]) - center).days) <= 7]
    return {"query": q, "n": len(sim), "n_week": len(near), "items": sim[:6], "weak": weak, "top_views": top,
            "median": int(statistics.median(s["views"] for s in sim)), "channels": len({s["channel"] for s in sim})}


def verdict(c):
    """Свежо / догоняют / выжжено — по числу копий за последние 2 недели."""
    if c is None:
        return None
    n = c["n"]
    if n <= 3:
        return {"key": "fresh", "label": "Свежо", "icon": "🔥",
                "text": "Похожих роликов почти нет" + (f" ({n})" if n else "") + " — можно успеть со своей версией в ближайшие дни."}
    if n <= 10:
        return {"key": "catching", "label": "Догоняют", "icon": "⚠️",
                "text": f"За 2 недели уже {n} похожих роликов" + (f", {c['weak']} из них почти ничего не набрали" if c["weak"] else "") + ". Заходить только со своим углом, не копией."}
    return {"key": "burned", "label": "Выжжено", "icon": "☠️",
            "text": f"За 2 недели {n}+ похожих роликов" + (f", {c['weak']} из них почти ничего не набрали" if c["weak"] else "") + ". Тема сгорела — не повторяйте."}
