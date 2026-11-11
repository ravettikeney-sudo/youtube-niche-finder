"""
«Почему ролик НЕ зашёл»: слабый ролик сравнивается не с обычными роликами канала, а с его же хитами —
тема, название, превью, длина, начало ролика и копии, среди которых он вышел.
"""
import datetime as dt
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait

import analyzer as A
import ideas
import saturation
from hooks import HOOKS, hooks_of

HOOK_NAME = {k: n for k, n, _ in HOOKS}


def _t(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def pick_hits(ents, vid, median):
    today = dt.date.today()
    aged = [e for e in ents if e.get("id") != vid and e.get("view_count") is not None
            and (lambda d: d is None or (today - d).days >= 7)(A._entry_date(e))]
    hits = [e for e in aged if median and e["view_count"] >= 1.5 * median]
    if len(hits) < 3:   # мало явных хитов — берём верхнюю четверть канала
        hits = sorted(aged, key=lambda e: -e["view_count"])[:max(3, len(aged) // 4)]
    return sorted(hits, key=lambda e: -e["view_count"])[:8]


def _feat(vid):
    import thumbs
    im = thumbs.load_img(vid)
    return thumbs.features(im) if im is not None else None


def analyze(vid, info, ch, ts, lang, ylang, prog=lambda p, m: None):
    """Возвращает {why_not, fix, hits, copies} или None, если сравнивать не с чем."""
    from video import captions, full_info, transcript_stats
    med = ch.get("median")
    hits = pick_hits(ch.get("ents") or [], vid, med)
    if len(hits) < 2:
        return None
    title = info.get("title") or ""
    why, fix = [], []

    # 1. тема: за что канал смотрят и есть ли это в ролике
    hit_terms = Counter()
    for h in hits:
        hit_terms.update(set(ideas._stems(ideas._content(ideas.main_part(h.get("title") or "")))))
    mine = set(ideas._stems(ideas._content(ideas.main_part(title))))
    shared = [t for t, c in hit_terms.most_common(8) if c >= 2 and len(t) >= 3]
    if shared and not (mine & set(shared)):
        words = [ideas._surface(t, [h.get("title") or "" for h in hits]) for t in shared[:3]]
        why.append("Тема в стороне от того, за что вас смотрят: ваши хиты про " + ", ".join(f"«{w}»" for w in words if w) + ", а в этом ролике этого нет.")
        fix.append("Следующий ролик — ближе к темам хитов: " + ", ".join(f"«{w}»" for w in words if w) + ", или свяжите новую тему с ними в названии.")

    # 2. название: приёмы и длина
    hook_cnt = Counter(hk for h in hits for hk in hooks_of(h.get("title") or ""))
    my_hooks = hooks_of(title)
    missing = [hk for hk, c in hook_cnt.most_common() if hk != "caps" and c / len(hits) >= 0.4 and hk not in my_hooks]
    if missing:
        names = ", ".join(HOOK_NAME[h].lower() for h in missing[:2])
        why.append(f"В названии нет приёма, который есть у ваших хитов: {names}.")
        fix.append(f"Добавьте в название {names} — так устроены ваши лучшие ролики.")
    if not (my_hooks - {"caps"}) and hook_cnt:
        why.append("В названии нет ни одного цепляющего приёма — ни вопроса, ни числа, ни интриги, ни конфликта.")
    # одинаковое начало у хитов («Why …», «Почему …», «Как я …»)
    first = Counter(((h.get("title") or "").split() or [""])[0].lower().strip("«\"'") for h in hits)
    fw, fc = first.most_common(1)[0] if first else ("", 0)
    my_first = (title.split() or [""])[0].lower().strip("«\"'")
    if fw and len(fw) >= 2 and fc >= max(3, 0.5 * len(hits)) and my_first != fw:
        orig = next(((h.get("title") or "").split()[0] for h in hits if (h.get("title") or "").lower().startswith(fw)), fw)
        why.append(f"Ваши хиты устроены одинаково — {fc} из {len(hits)} начинаются с «{orig} …», а этот ролик — нет.")
        fix.append(f"Попробуйте ту же формулу: «{orig} …» + тема ролика.")
    lens = [len(h.get("title") or "") for h in hits]
    lm = statistics.median(lens)
    if len(title) > lm + 22 and len(title) > 60:
        why.append(f"Название длинное — {len(title)} симв., у хитов около {round(lm)}. На телефоне конец обрежется.")
        fix.append(f"Укладывайте название в {round(lm)}–{round(lm) + 10} символов.")
    elif len(title) < lm - 22:
        why.append(f"Название короткое — {len(title)} симв. против {round(lm)} у хитов: из него не понятно, что внутри.")

    # 3. длина
    dur = info.get("duration")
    hd = [h["duration"] for h in hits if h.get("duration")]
    if dur and len(hd) >= 2:
        hm = statistics.median(hd)
        if dur > hm * 1.5:
            why.append(f"Ролик длиннее ваших хитов: {_t(dur)} против обычных {_t(hm)}.")
            fix.append(f"Держите длину ближе к {_t(hm)} — с такой длиной у вас хиты.")
        elif dur < hm * 0.6:
            why.append(f"Ролик короче ваших хитов: {_t(dur)} против {_t(hm)} — зритель может не успеть вовлечься, а YouTube получает меньше времени просмотра.")

    prog(70, "Сравниваю превью и начало ролика с вашими хитами…")
    ex = ThreadPoolExecutor(5)
    f_me = ex.submit(_feat, vid)
    f_hits = {h["id"]: ex.submit(_feat, h["id"]) for h in hits[:8]}

    def hit_ts(h):
        i = full_info(h["id"])
        return transcript_stats(captions(i, lang), i.get("duration"), i.get("chapters")) if i else None
    f_ts = [ex.submit(hit_ts, h) for h in hits[:3]] if ts else []
    up = A._d(info.get("upload_date"))
    age = (dt.date.today() - up).days if up else None
    f_cp = ex.submit(saturation.copies, title, ylang, None, up, (vid,), info.get("channel_id"), 7) if age is not None and age <= 60 else None
    allf = [f_me, *f_hits.values(), *f_ts] + ([f_cp] if f_cp else [])
    wait(allf, timeout=90)
    ex.shutdown(wait=False, cancel_futures=True)
    ok = lambda f: f.done() and not f.cancelled() and f.exception() is None

    # 4. превью против превью хитов
    me = f_me.result() if ok(f_me) else None
    hf = [f.result() for f in f_hits.values() if ok(f) and f.result()]
    thumb = None
    if me and len(hf) >= 2:
        hm = {k: statistics.median(f[k] for f in hf) for k in ("face_pct", "words", "bright", "contrast", "detail", "sat")}
        face_share = sum(1 for f in hf if f["faces"]) / len(hf)
        text_share = sum(1 for f in hf if f["words"]) / len(hf)
        d = []
        if not me["faces"] and face_share >= 0.6:
            d.append(f"нет лица — а у {round(100 * face_share)}% ваших хитов оно есть")
        elif me["faces"] and me["face_pct"] < 3 and hm["face_pct"] >= 6:
            d.append("лицо мелкое — у хитов оно крупное")
        if not me["words"] and text_share >= 0.6:
            d.append("нет надписи — у хитов она обычно есть")
        elif me["words"] >= hm["words"] + 3:
            d.append(f"много текста ({me['words']} сл., у хитов около {round(hm['words'])})")
        if me["bright"] <= hm["bright"] - 12:
            d.append("темнее, чем превью хитов")
        if me["contrast"] <= hm["contrast"] - 10:
            d.append("бледнее и менее контрастное")
        if me["detail"] >= hm["detail"] * 1.5 and me["detail"] > 10:
            d.append("перегружено деталями — на телефоне превращается в «кашу»")
        if me["sat"] <= hm["sat"] - 15:
            d.append("цвета тусклее, чем у хитов")
        thumb = {"diff": d, "compared": len(hf)}
        if d:
            why.append("Превью проигрывает вашим же хитам: " + "; ".join(d) + ".")
            fix.append("Перед выкладкой прогоните превью через «Тест превью» — он сравнит его с хитами ниши.")

    # 5. начало ролика против начала хитов
    hts = [f.result() for f in f_ts if ok(f) and f.result()]
    if ts and hts:
        hp = [h["promise_at"] for h in hts if h["promise_at"] is not None]
        if hp and statistics.median(hp) <= 60:
            hpm = statistics.median(hp)
            if ts["promise_at"] is None:
                why.append(f"В начале не звучит, что зритель получит. У ваших хитов обещание — на {_t(hpm)}.")
                fix.append(f"Скажите в первые {max(15, int(hpm) + 5)} секунд, что зритель узнает или увидит.")
            elif ts["promise_at"] > hpm + 20 and ts["promise_at"] > 30:
                why.append(f"Обещание прозвучало поздно — на {_t(ts['promise_at'])}, у хитов на {_t(hpm)}. Длинное вступление — зритель уходит раньше.")
                fix.append(f"Сократите вступление: обещание — до {_t(max(20, hpm + 5))}.")
        hc = [h["first_cta"] for h in hts if h["first_cta"] is not None]
        if ts["first_cta"] is not None and ts["first_cta"] < 40 and (not hc or statistics.median(hc) > 90):
            why.append(f"Просьба подписаться звучит слишком рано — на {_t(ts['first_cta'])}, ещё до пользы. У хитов позже.")
            fix.append("Призыв подписаться — после первой пользы, не в начале.")
        hw = [h["wpm"] for h in hts if h.get("wpm")]
        if hw and ts.get("wpm") and ts["wpm"] < statistics.median(hw) * 0.82:
            why.append(f"Темп речи медленнее хитов: {ts['wpm']} слов в минуту против {round(statistics.median(hw))}.")
            fix.append("Убирайте паузы и «воду» при монтаже — темп ближе к хитам.")
        hq = sum(1 for h in hts if h.get("question_at") is not None)
        if hq >= 2 and ts.get("question_at") is None:
            why.append("Хиты начинаются с вопроса зрителю, а этот ролик — нет.")
            fix.append("Начните с вопроса, который зритель задаёт себе сам.")

    # 6. копии
    cp = f_cp.result() if f_cp and ok(f_cp) else None
    if cp and cp["n_week"] >= 6:
        nw = cp['n_week']
        why.append(f"Ролик утонул среди копий: в ту же неделю вышло ещё {nw} {'ролик' if nw % 10 == 1 and nw % 100 != 11 else 'ролика' if 2 <= nw % 10 <= 4 and not 12 <= nw % 100 <= 14 else 'роликов'} на эту тему"
                   + (f" от {cp['channels']} каналов" if cp.get("channels") else "") + ".")
        fix.append("Проверяйте тему в «Идеях сейчас» перед съёмкой: если копий уже много — нужен свой угол или другая тема.")

    return {"why_not": why, "fix": fix,
            "hits": [{"id": h["id"], "title": h.get("title"), "views": h.get("view_count"),
                      "x": round(h["view_count"] / med, 1) if med else None} for h in hits[:6]],
            "thumb_vs_hits": thumb, "copies": cp}
