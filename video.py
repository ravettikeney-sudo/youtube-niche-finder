"""
Ролик: «Разбор ролика» (почему конкретный ролик выстрелил) и «Каркас сценария» (как устроены хиты ниши).

Данные — только публичные: страница ролика (длина, дата, просмотры, лайки, главы, теги),
список роликов его канала (обычные просмотры канала) и субтитры YouTube (свои или автоматические),
по которым видно, как ролик начинается, когда звучит обещание, где призыв подписаться и как он заканчивается.
"""
import datetime as dt
import json
import os
import re
import statistics
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait

import analyzer as A
import niche
from hooks import HOOKS, hooks_of

HOOK_NAME = {k: n for k, n, _ in HOOKS}

# ---------------------------------------------------------------- субтитры
PROMISE = re.compile(r"(в этом (видео|ролике|выпуске)|сегодня (я|мы|расскажу|покажу|разбер|узна)|расскажу|покажу|разбер[её]м|вы узнаете|"
                     r"узнаем|давайте (разбер|посмотр|выясн)|к концу (видео|ролика)|я проверил|мы проверили|"
                     r"in this video|today (i|we)|i'?ll show|i'?m going to|we'?re going to|let'?s (find|see|look|break)|"
                     r"you'?ll (learn|see|find)|by the end|here'?s (why|how|what))", re.I)
CTA = re.compile(r"(подпиш|подписывай|колокольч|лайк|поставь|оставь(те)? комментар|subscribe|hit the bell|like (this|the) video|"
                 r"leave a comment|smash)", re.I)
OUTRO = re.compile(r"(спасибо за просмотр|до встречи|увидимся|до новых|на этом (всё|все)|пока[-, !]|thanks for watching|"
                   r"see you (next|in the)|that'?s (all|it) for|until next time)", re.I)
TRANS = re.compile(r"(^|\s)(во-первых|во-вторых|в-третьих|первое|второе|третье|дальше|далее|итак|теперь|следующ|"
                   r"а теперь|но это не|first(ly)?|second(ly)?|third|next|now let'?s|moving on|but (that'?s|here'?s)|finally|so what)(\s|,|$)", re.I)


def _get(url, timeout=20):
    px = os.environ.get("YT_PROXY")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return op.open(req, timeout=timeout).read()


def full_info(vid, ylang=None):
    """Страница ролика целиком: нужны главы и ссылки на субтитры."""
    return A._extract(f"https://www.youtube.com/watch?v={vid}", flat=False, lang=ylang) or {}


def captions(info, lang=None):
    """Субтитры ролика: [(секунда, текст)]. Сначала авторские, потом автоматические на языке ролика."""
    lang = (lang or info.get("language") or "").split("-")[0] or None
    subs, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    order = []
    for pool in (subs, auto):
        keys = list(pool)
        pref = [k for k in keys if lang and (k == lang or k.startswith(lang + "-"))]
        if pool is auto:
            pref = [k for k in keys if k.endswith("-orig")] + pref  # оригинальная дорожка, а не перевод
        order += [(pool, k) for k in pref]
    if not order and subs:
        order = [(subs, next(iter(subs)))]
    for pool, k in order:
        fm = [t for t in pool.get(k) or [] if t.get("ext") == "json3"]
        if not fm:
            continue
        try:
            d = json.loads(_get(fm[0]["url"]))
        except Exception:
            continue
        out = []
        for e in d.get("events") or []:
            txt = "".join(s.get("utf8", "") for s in e.get("segs") or []).replace("\n", " ").strip()
            if txt:
                out.append((round(e.get("tStartMs", 0) / 1000, 1), txt))
        if len(out) >= 10:
            return out
    return []


def _fmt_t(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def transcript_stats(cap, duration, chapters=None):
    """Как устроен ролик по субтитрам: обещание, призывы, переходы, концовка, темп."""
    if not cap:
        return None
    dur = duration or (cap[-1][0] + 5)
    words = sum(len(t.split()) for _, t in cap)
    wpm = round(words / max(dur / 60, 0.5))
    first30 = " ".join(t for s, t in cap if s < 30)
    first60 = " ".join(t for s, t in cap if s < 60)
    promise = next((s for s, t in cap if PROMISE.search(t) and s < min(240, dur * 0.4)), None)
    q_early = next((s for s, t in cap if "?" in t and s < 20), None)
    cta = [s for s, t in cap if CTA.search(t)]
    outro = next((s for s, t in cap if OUTRO.search(t) and s > dur * 0.75), None)
    trans = [s for s, t in cap if TRANS.search(t) and 30 < s < dur * 0.92]
    # главы автора точнее любых догадок; без них — переходы по словам-связкам (не чаще раза в 40 секунд)
    marks = []
    for s in trans:
        if not marks or s - marks[-1] >= 40:
            marks.append(s)
    parts = [c.get("start_time", 0) for c in (chapters or [])] or [0] + marks
    seg = [b - a for a, b in zip(parts, parts[1:] + [dur])]
    numbers = len(re.findall(r"\b\d+\b", first60))
    return {
        "duration": dur, "wpm": wpm, "words": words,
        "first30": first30[:600], "first60": first60[:1200],
        "promise_at": promise, "promise_text": next((t for s, t in cap if s == promise), None) if promise is not None else None,
        "question_at": q_early, "numbers_in_intro": numbers,
        "first_cta": cta[0] if cta else None, "cta_count": len(cta),
        "outro_at": outro, "outro_len": round(dur - outro) if outro else None,
        "parts": len(parts), "part_len": round(statistics.median(seg)) if seg else None,
        "chapters": [{"t": c.get("start_time", 0), "title": c.get("title")} for c in (chapters or [])][:30],
        "has_chapters": bool(chapters),
    }


# ---------------------------------------------------------------- разбор ролика
def _channel_norm(base, ylang, n=40):
    info, ents, tab = A.channel_listing(base, n, ylang)
    today = dt.date.today()
    long = [e for e in ents if e.get("view_count") is not None and (not e.get("duration") or e["duration"] >= 60)]
    aged = [e for e in long if (lambda d: d is None or (today - d).days >= 7)(A._entry_date(e))]
    med = statistics.median([e["view_count"] for e in aged]) if len(aged) >= 3 else None
    durs = [e["duration"] for e in long if e.get("duration")]
    return {"median": med, "ents": long, "dur_med": statistics.median(durs) if durs else None,
            "title": (info or {}).get("channel") or (info or {}).get("uploader"), "n": len(long)}


def _thumb_diff(vid, others):
    """Чем превью этого ролика отличается от обычных превью канала."""
    try:
        import thumbs
    except Exception:
        return None
    ex = ThreadPoolExecutor(4)
    fs = {v: ex.submit(lambda v=v: (lambda im: thumbs.features(im) if im is not None else None)(thumbs.load_img(v))) for v in [vid] + others[:12]}
    wait(list(fs.values()), timeout=60)
    ex.shutdown(wait=False, cancel_futures=True)
    got = {v: f.result() for v, f in fs.items() if f.done() and not f.cancelled() and f.exception() is None and f.result()}
    me = got.get(vid)
    rest = [f for v, f in got.items() if v != vid]
    if not me:
        return None
    out = {"me": {k: me[k] for k in ("faces", "face_pct", "words", "text", "bright", "sat", "contrast", "warm")},
           "palette": me["palette"], "diff": []}
    if len(rest) >= 4:
        med = {k: statistics.median(f[k] for f in rest) for k in ("face_pct", "words", "bright", "sat", "contrast", "warm")}
        face_share = sum(1 for f in rest if f["faces"]) / len(rest)
        d = out["diff"]
        if me["faces"] and face_share < 0.4:
            d.append("в кадре лицо — у канала это редкость")
        elif not me["faces"] and face_share > 0.6:
            d.append("без лица — хотя обычно канал ставит лицо")
        if me["face_pct"] >= max(8, med["face_pct"] * 1.6):
            d.append("лицо крупнее обычного")
        if me["words"] and med["words"] <= 0.5:
            d.append("есть текст на превью — обычно у канала его нет")
        elif not me["words"] and med["words"] >= 2:
            d.append("без текста — обычно у канала есть надпись")
        elif me["words"] and me["words"] <= med["words"] - 2:
            d.append("текста меньше обычного")
        if me["bright"] >= med["bright"] + 10:
            d.append("светлее обычного")
        if me["sat"] >= med["sat"] + 12:
            d.append("насыщеннее обычного")
        if me["contrast"] >= med["contrast"] + 10:
            d.append("контрастнее обычного")
        if me["warm"] >= med["warm"] + 15:
            d.append("больше тёплых цветов (красный/оранжевый/жёлтый)")
        out["channel_median"] = med
        out["compared"] = len(rest)
    return out


def teardown(url, prog=lambda p, m: None):
    m = A.VID_RE.search(A.normalize_url(url))
    if not m:
        raise ValueError("Нужна ссылка на ролик YouTube (youtube.com/watch?v=… или youtu.be/…).")
    vid = m.group(1)
    prog(5, "Открываю страницу ролика…")
    info = full_info(vid)
    if not info or not info.get("title"):
        raise ValueError("Ролик не открылся: проверьте ссылку или попробуйте позже.")
    lang = (info.get("language") or "").split("-")[0] or None
    if not lang:
        lang = "ru" if niche.script(info.get("title") or "") == "cyr" else "en"
    ylang = niche.ylang_for(lang)
    base = A.resolve_channel_base("https://www.youtube.com/channel/" + info["channel_id"]) if info.get("channel_id") else None
    prog(20, "Смотрю обычные просмотры канала…")
    ch = _channel_norm(base, ylang) if base else {"median": None, "ents": [], "dur_med": None, "n": 0}
    views = info.get("view_count") or 0
    up = A._d(info.get("upload_date"))
    age = (dt.date.today() - up).days if up else None
    ratio = round(views / ch["median"], 2) if ch.get("median") else None
    rank = 1 + sum(1 for e in ch["ents"] if (e.get("view_count") or 0) > views) if ch["ents"] else None
    prog(40, "Читаю субтитры…")
    cap = captions(info, lang)
    ts = transcript_stats(cap, info.get("duration"), info.get("chapters"))
    prog(60, "Сравниваю превью с обычными превью канала…")
    others = [e["id"] for e in ch["ents"] if e.get("id") != vid]
    th = _thumb_diff(vid, others)
    title = info.get("title") or ""
    hk = sorted(hooks_of(title))
    ch_hooks = Counter(h for e in ch["ents"] for h in hooks_of(e.get("title") or ""))
    rare_hooks = [h for h in hk if ch["ents"] and ch_hooks[h] / max(len(ch["ents"]), 1) < 0.25 and h != "caps"]
    likes, comments = info.get("like_count"), info.get("comment_count")
    eng = round(100 * (likes or 0) / views, 2) if views and likes else None
    dur = info.get("duration")
    prog(85, "Собираю выводы…")
    why, repeat = [], []
    if ratio:
        if ratio >= 2:
            why.append(f"Набрал в {_x(ratio)} раза больше обычного для своего канала ({A_h(views)} при обычных {A_h(ch['median'])}) — это настоящий выброс, а не просто большой канал.")
        elif ratio >= 1.2:
            why.append(f"Набрал больше обычного для канала (×{_x(ratio)}), но это не взрыв — скорее удачный, чем исключительный ролик.")
        else:
            why.append(f"Для своего канала ролик обычный или слабее обычного (×{_x(ratio)}). Его просмотры — заслуга размера канала, а не самого ролика.")
    if rare_hooks:
        why.append("В названии приём, который канал использует редко: " + ", ".join(HOOK_NAME[h].lower() for h in rare_hooks) + ".")
        repeat.append("Используйте в названии: " + ", ".join(HOOK_NAME[h].lower() for h in rare_hooks) + f" — как в «{title[:80]}».")
    if th and th.get("diff"):
        why.append("Превью отличается от обычных превью канала: " + "; ".join(th["diff"]) + ".")
        repeat.append("В превью: " + "; ".join(th["diff"]) + ".")
    if dur and ch.get("dur_med"):
        if dur >= ch["dur_med"] * 1.4:
            why.append(f"Ролик длиннее обычного для канала ({_fmt_t(dur)} против {_fmt_t(ch['dur_med'])}).")
        elif dur <= ch["dur_med"] * 0.7:
            why.append(f"Ролик короче обычного для канала ({_fmt_t(dur)} против {_fmt_t(ch['dur_med'])}).")
    if ts:
        if ts["promise_at"] is not None and ts["promise_at"] <= 30:
            why.append(f"Обещание звучит быстро — на {_fmt_t(ts['promise_at'])}: «{(ts['promise_text'] or '')[:100]}».")
            repeat.append(f"Скажите, что зритель получит, в первые {max(10, int(ts['promise_at']) + 5)} секунд.")
        elif ts["promise_at"] is not None:
            why.append(f"Обещание звучит на {_fmt_t(ts['promise_at'])} — не сразу; ролик держит другим (история, интрига).")
        if ts["question_at"] is not None:
            why.append(f"В первые секунды — вопрос зрителю (на {_fmt_t(ts['question_at'])}).")
            repeat.append("Начните с вопроса, который зритель задаёт себе сам.")
        if ts["first_cta"] is not None and ts["first_cta"] < 60:
            why.append(f"Просьба подписаться звучит рано — на {_fmt_t(ts['first_cta'])}.")
        elif ts["first_cta"] is not None:
            repeat.append(f"Призыв подписаться — не в начале, а на {_fmt_t(ts['first_cta'])}, когда зритель уже вовлёкся.")
        if ts["has_chapters"]:
            repeat.append(f"Разбейте ролик на главы ({ts['parts']} шт., обычно по {_fmt_t(ts['part_len'])}).")
        repeat.append(f"Темп речи около {ts['wpm']} слов в минуту.")
    if age is not None and age <= 30 and ratio and ratio >= 2:
        why.append(f"Ролику всего {age} дн. — тема свежая и ещё набирает.")
    if eng is not None:
        why.append(f"Лайков {str(eng).replace('.', ',')}% от просмотров" + (" — зрители реагируют активно." if eng >= 4 else "." if eng >= 2 else " — реакция сдержанная, ролик скорее «смотрят», чем обсуждают."))
    return {
        "type": "teardown", "generated_at": dt.datetime.now().isoformat(timespec="minutes"), "lang": lang,
        "video": {"id": vid, "title": title, "views": views, "likes": likes, "comments": comments, "duration": dur,
                  "upload_date": info.get("upload_date"), "age_days": age, "tags": (info.get("tags") or [])[:25],
                  "description": (info.get("description") or "")[:1500], "channel": info.get("channel"),
                  "channel_url": base, "subs": info.get("channel_follower_count")},
        "channel_norm": {"median": ch.get("median"), "dur_med": ch.get("dur_med"), "videos": ch.get("n"), "rank": rank},
        "ratio": ratio, "engagement": eng, "hooks": [HOOK_NAME[h] for h in hk if h != "caps"],
        "thumb": th, "transcript": ts, "has_captions": bool(cap), "why": why, "repeat": repeat,
    }


def _x(v):
    return f"{v:.1f}".replace(".", ",") if v < 10 else str(round(v))


def A_h(n):
    n = n or 0
    return f"{n / 1e6:.1f} млн".replace(".", ",") if n >= 1e6 else f"{round(n / 1e3)} тыс." if n >= 1e3 else str(int(n))


# ---------------------------------------------------------------- каркас сценария
def _niche_hits(src, ylang):
    """Хиты ниши для каркаса: из отчёта (каналы ниши и их хиты) или из канала (его лучшие ролики)."""
    vids = []
    if isinstance(src, dict):  # отчёт по нише
        for c in src.get("channels") or []:
            if not c.get("relevant"):
                continue
            for h in (c.get("hit_videos") or [])[:2]:
                vids.append({"id": h["id"], "title": h.get("title"), "views": h.get("views"), "channel": c.get("title"),
                             "x": round((h.get("views") or 0) / max(c.get("median_views") or 1, 1), 1)})
        for h in ((src.get("competitor") or {}).get("hit_videos") or [])[:3]:
            vids.append({"id": h["id"], "title": h.get("title"), "views": h.get("views"), "channel": (src.get("competitor") or {}).get("title"), "x": None})
    else:
        base = A.resolve_channel_base(src)
        ch = _channel_norm(base, ylang, 60)
        med = ch.get("median") or 1
        for e in sorted(ch["ents"], key=lambda e: -(e.get("view_count") or 0))[:15]:
            vids.append({"id": e["id"], "title": e.get("title"), "views": e.get("view_count"), "channel": ch.get("title"),
                         "x": round((e.get("view_count") or 0) / med, 1)})
    seen, out = set(), []
    for v in sorted(vids, key=lambda v: -(v["x"] or 0)):
        if v["id"] not in seen:
            seen.add(v["id"])
            out.append(v)
    return out[:14]


def script_plan(src, target_min=None, prog=lambda p, m: None, lang=None):
    """Каркас сценария: как устроены хиты ниши (по их субтитрам) → план ролика с таймингом."""
    ylang = niche.ylang_for(lang) if lang else None
    prog(5, "Собираю хиты ниши…")
    hits = _niche_hits(src, ylang)
    if len(hits) < 3:
        raise ValueError("Мало длинных роликов, чтобы найти закономерность (каркас строится по роликам от 2 минут; Shorts не подходят). Выберите отчёт по нише или канал с длинными видео.")
    prog(15, f"Читаю субтитры {len(hits)} хитов…")
    ex = ThreadPoolExecutor(4)

    def one(v):
        info = full_info(v["id"], ylang)
        if not info:
            return None
        cap = captions(info, lang)
        ts = transcript_stats(cap, info.get("duration"), info.get("chapters"))
        if not ts or (info.get("duration") or 0) < 120:
            return None
        return dict(v, ts=ts, duration=info.get("duration"), lang=(info.get("language") or "").split("-")[0])
    fs = [ex.submit(one, v) for v in hits]
    done_n = 0
    for f in fs:
        try:
            f.result(timeout=max(5, 150 - 10 * done_n))
        except Exception:
            pass
        done_n += 1
        prog(15 + 70 * done_n / len(fs), f"Читаю субтитры: {done_n}/{len(fs)}")
    ex.shutdown(wait=False, cancel_futures=True)
    got = [f.result() for f in fs if f.done() and not f.cancelled() and f.exception() is None and f.result()]
    if len(got) < 3:
        raise ValueError("У хитов этой ниши почти нет субтитров — каркас по ним не построить. Попробуйте другую нишу или канал.")
    med = lambda k: statistics.median([g["ts"][k] for g in got if g["ts"].get(k) is not None]) if any(g["ts"].get(k) is not None for g in got) else None
    dur = med("duration")
    target = (target_min or round(dur / 60)) * 60
    promise = med("promise_at")
    cta = med("first_cta")
    outro_len = med("outro_len")
    part = med("part_len")
    wpm = med("wpm")
    share_q = sum(1 for g in got if g["ts"]["question_at"] is not None) / len(got)
    share_ch = sum(1 for g in got if g["ts"]["has_chapters"]) / len(got)
    share_promise = sum(1 for g in got if g["ts"]["promise_at"] is not None) / len(got)
    # план по таймингу: хук → обещание → блоки → кульминация → призыв/концовка
    hook_end = int(min(max(promise or 20, 10), 60))
    intro_end = int(min(hook_end + 25, target * 0.12 + 20))
    outro = int(min(max(outro_len or 25, 15), 90))
    body = max(target - intro_end - outro, 60)
    n_blocks = max(2, min(8, round(body / max(part or 150, 60))))
    blk = body / n_blocks
    plan = [{"from": 0, "to": hook_end, "name": "Хук",
             "what": "Сразу главный вопрос или самый сильный момент ролика" + (" — вопросом к зрителю (так начинают " + f"{round(100 * share_q)}% хитов)" if share_q >= 0.3 else "") + ". Без приветствий и «всем привет»."},
            {"from": hook_end, "to": intro_end, "name": "Обещание",
             "what": "Скажите, что зритель узнает или увидит к концу ролика, и почему досмотреть стоит" + (f". В хитах ниши обещание звучит примерно на {_fmt_t(promise)}." if promise else ".")}]
    t = intro_end
    for i in range(n_blocks):
        last = i == n_blocks - 1
        plan.append({"from": int(t), "to": int(t + blk), "name": f"Блок {i + 1}" + (" — кульминация" if last else ""),
                     "what": ("Самый сильный факт или развязка — то, ради чего досматривают." if last else
                              "Одна мысль: факт → пример → почему это важно. В конце — мостик к следующему блоку («но это ещё не всё…»).")})
        t += blk
    fr = [g["ts"]["first_cta"] / g["duration"] for g in got if g["ts"].get("first_cta") is not None and g.get("duration")]
    cta_frac = statistics.median(fr) if len(fr) >= 2 else None
    cta_t = int(cta_frac * target) if cta_frac else int(intro_end + blk)
    cta_t = int(min(max(cta_t, intro_end + 15), target - outro))
    plan.append({"from": int(t), "to": int(target), "name": "Концовка",
                 "what": "Короткий вывод одной фразой и следующий ролик (подсказка на похожее видео) — без долгих прощаний."})
    examples = [{"title": g["title"], "id": g["id"], "channel": g["channel"], "x": g.get("x"), "views": g.get("views"),
                 "first30": g["ts"]["first30"][:300], "promise_at": g["ts"]["promise_at"], "duration": g["duration"]}
                for g in sorted(got, key=lambda g: -(g.get("x") or 0))[:6]]
    rules = []
    if promise is not None:
        rules.append(f"Обещание ролика — в первые {_fmt_t(promise)} (медиана хитов; так делают {round(100 * share_promise)}%).")
    if share_q >= 0.3:
        rules.append(f"{round(100 * share_q)}% хитов задают вопрос в первые 20 секунд.")
    if cta is not None:
        rules.append(f"Призыв подписаться хиты ставят примерно на {round(100 * cta_frac)}% длины ролика — у вас это {_fmt_t(cta_t)}" + (" (не в начале)." if cta_frac > 0.15 else ".") if cta_frac else f"Призыв подписаться в хитах звучит обычно на {_fmt_t(cta)}.")
    if share_ch >= 0.4:
        rules.append(f"{round(100 * share_ch)}% хитов разбиты на главы — добавьте таймкоды в описание.")
    if wpm:
        rules.append(f"Темп речи — около {round(wpm)} слов в минуту" + (" (быстро)." if wpm >= 165 else " (спокойно)." if wpm <= 125 else "."))
    if outro_len:
        rules.append(f"Концовка после прощания короткая — около {round(outro_len)} секунд.")
    prog(100, "Готово")
    return {"type": "script", "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
            "analyzed": len(got), "requested": len(hits), "median_duration": dur, "target": target,
            "promise_at": promise, "first_cta": cta, "cta_at": cta_t, "wpm": wpm, "part_len": part, "outro_len": outro_len,
            "share_question": round(share_q, 2), "share_chapters": round(share_ch, 2),
            "plan": [dict(p, from_t=_fmt_t(p["from"]), to_t=_fmt_t(p["to"])) for p in plan],
            "rules": rules, "examples": examples, "channel_title": hits[0].get("channel") if not isinstance(src, dict) else None}
