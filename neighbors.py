"""
Соседние ниши: рядом с оценкой ниши — две-три смежные темы с их оценками, чтобы было видно, где окно шире.

Темы-кандидаты отчёт уже нашёл (общие у нескольких каналов ниши и почти подошедших каналов).
Здесь для каждой: поиск YouTube в той же стране и на том же языке → каналы этой темы (уже проверенные
в отчёте + несколько новых) → те же формулы оценки, что в отчёте, и диапазон на случайных подвыборках.
"""
import datetime as dt
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import analyzer as A
import niche
import verify

MAX_TOPICS = 4      # сколько соседних тем оцениваем (показываем лучшие)
NEW_PER_TOPIC = 8   # сколько новых каналов догружаем на тему


def _stems(text):
    return {niche.stem(w) for w in A.tokenize(text)}


BROAD = set("""education educational entertainment learning documentary documentaries vlog vlogs youtube channel tutorial
tutorials news video videos shorts facts explained podcast interview interviews politics technology science funny
blog review reviews life lifestyle travel family kids music movie movies film films world history
образование обучение развлечения документальный документальные новости видео влог блог факты интересное
познавательное познавательные подкаст интервью политика технологии наука юмор обзор обзоры жизнь музыка кино
фильм фильмы история мир путешествия россия""".split())


def candidates(rel, near, kws, want="lat", excl=lambda t: None, n=6):
    """Темы соседних ниш — из ключевых слов, которые авторы сами указали для каналов (теги канала).
    Тег засчитывается, если его ставит хотя бы один канал ниши и ещё хотя бы один канал (ниши или почти подошедший).
    Темы самой ниши, слишком общие слова («новости», «образование») и названия каналов отбрасываются."""
    base = set().union(*[_stems(k) for k in kws]) if kws else set()
    names = [_stems(c.get("title") or "") for c in rel + near]
    use = {}
    for group, w in ((rel, "rel"), (near, "near")):
        for c in group:
            for t in {x.lower().strip() for x in c.get("channel_tags") or [] if x}:
                if not (3 <= len(t) <= 35) or len(A.tokenize(t)) > 4 or excl(t) or not niche.same_script(t, want):
                    continue
                toks = set(A.tokenize(t))
                st = _stems(t)
                if not toks or toks <= BROAD or not st or st <= base:
                    continue
                if any(st <= nm for nm in names if nm):
                    continue
                use.setdefault(t, {"rel": set(), "near": set()})[w].add(c.get("id"))
    scored = []
    for t, u in use.items():
        if not u["rel"] or len(u["rel"] | u["near"]) < 2:
            continue
        # фразы из 2+ слов точнее: «maps» на YouTube — это ещё и навигаторы, «map facts» — уже тема
        scored.append((2 * len(u["rel"]) + len(u["near"]) + (3 if " " in t else 0), t))
    scored.sort(reverse=True)
    out, seen = [], []
    for _, t in scored:
        st = _stems(t)
        if any(st <= s or s <= st for s in seen):
            continue
        seen.append(st)
        out.append(t)
        if len(out) >= n:
            break
    return out


def run_neighbors(res, progress=lambda p, m: None):
    phrases = (res.get("neighbor_candidates") or [])[:MAX_TOPICS]
    if not phrases:
        return {"rows": [], "generated_at": dt.datetime.now().isoformat(timespec="minutes")}
    comp = res.get("competitor") or {}
    lang = comp.get("lang") or "en"
    region = (res.get("dynamics") or {}).get("region")
    ylang = niche.ylang_for(lang, region)
    script = "cyr" if lang in ("ru", "uk") else "lat" if lang == "en" or lang in A.insights.LATIN_HINTS else "other"
    max_age = (res.get("niche") or {}).get("max_age_days") or 730
    rpm = tuple((res.get("niche") or {}).get("rpm_range") or (1, 4))
    base = res.get("niche", {}).get("overall")

    # ---- поиск по каждой теме: обычная выдача и за этот год
    progress(5, "Ищу соседние ниши на YouTube…")
    jobs = [(p, sp) for p in phrases for sp in ("", A.SEARCH_FILTERS["year"])]
    found = {p: [] for p in phrases}
    with ThreadPoolExecutor(A.WORKERS) as ex:
        for (p, sp), ents in zip(jobs, ex.map(lambda j: A.search(j[0], 25, j[1], ylang) or [], jobs)):
            found[p] += [e for e in ents if niche.same_script(e.get("title"), script)]

    known = {c["id"]: c for c in res.get("channels") or [] if c.get("off_reason") != "другой язык"}
    skip = {comp.get("id")} | {x.get("id") for x in res.get("examples") or []}

    # ролик относится к теме, если в названии есть её слова
    members, views = {}, {}
    for p in phrases:
        own = _stems(p)
        need = min(2, len(own))  # «city planning»: нужны оба слова, а не просто «city»
        hits = [e for e in found[p] if len(own & _stems(e.get("title"))) >= need]
        views[p] = [e.get("view_count") or 0 for e in hits if e.get("view_count") is not None]
        members[p] = Counter(e["channel_id"] for e in hits if e.get("channel_id") and e["channel_id"] not in skip)

    # ---- догружаем новые каналы, которых нет в отчёте
    new_ids = []
    for p in phrases:
        for cid, _ in members[p].most_common(12):
            if cid not in known and cid not in new_ids:
                new_ids.append(cid)
                if sum(1 for x in new_ids if x in members[p]) >= NEW_PER_TOPIC:
                    break
    loaded = {}
    if new_ids:
        progress(30, f"Проверяю {len(new_ids)} новых каналов соседних ниш…")

        def load(cid):
            try:
                return cid, A.analyze_channel(f"https://www.youtube.com/channel/{cid}", 60, False, ylang)
            except Exception:
                return cid, None
        with ThreadPoolExecutor(A.WORKERS) as ex:
            for i, (cid, r) in enumerate(ex.map(load, new_ids), 1):
                if r and r.get("videos"):
                    if script == "cyr" and not niche.channel_lang_ok(r.get("titles", [])[:30], script, r.get("description")):
                        continue
                    if script == "lat":  # en/es/pt… по алфавиту не различить: звук и описание канала
                        dl = niche.lang_confident(r.get("description") or "")[0]
                        if (r.get("audio_lang") and r["audio_lang"] != lang) or (dl and dl != lang):
                            continue
                    r["young"] = bool(r.get("age_exact") and r.get("age_days") is not None and r["age_days"] <= max_age)
                    r["id"] = r.get("id") or cid
                    loaded[cid] = r
                progress(30 + 60 * i / len(new_ids), f"Проверяю каналы соседних ниш: {i}/{len(new_ids)}")

    # категория YouTube новых каналов: игры, музыка и спорт — другая аудитория, если ниша не про них
    niche_cat = comp.get("category")
    if loaded:
        def cat(r):
            hv = (r.get("hit_videos") or [{}])[0].get("id") or ((r.get("_ents") or [{}])[0] or {}).get("id")
            return A.video_category(hv, niche.split_ylang(ylang)[1]) if hv else None
        with ThreadPoolExecutor(A.WORKERS) as ex:
            for (cid, r), c in zip(list(loaded.items()), ex.map(cat, list(loaded.values()))):
                r["category"] = c
    far = {"Gaming", "Music", "Sports"} - {niche_cat}
    pool = {cid: r for cid, r in {**known, **loaded}.items() if r.get("category") not in far}
    rows = []
    for p in phrases:
        chans = [pool[cid] for cid in members[p] if cid in pool]
        if not views[p] or len(chans) < 2:
            continue
        S = verify.niche_scores(chans, views[p], rpm)
        rng = verify.bootstrap(chans, views[p], rpm, n=200)
        width = (rng[1] - rng[0]) if rng else 99
        reliab = "высокая" if len(chans) >= 8 and width <= 16 else "средняя" if len(chans) >= 4 and width <= 28 else "низкая"
        yr = S["young_rising"]
        rows.append({
            "name": p, "score": round(S["overall"]), "range": rng, "reliability": reliab,
            "delta": round(S["overall"]) - base if base is not None else None,
            "scores": {"demand": round(S["demand"]), "competition": round(S["competition"]),
                       "entry_window": round(S["window"]), "monetization": round(S["monet"])},
            "channels": len(chans), "young": len(S["young"]), "young_rising": len(yr),
            "median_views": int(S["med_views"]),
            "examples": [{"title": c.get("title"), "url": c.get("url"), "avatar": c.get("avatar"), "subs": c.get("subs"),
                          "age_days": c.get("age_days"), "rising_score": c.get("rising_score")}
                         for c in sorted(yr or S["young"] or chans, key=lambda c: -(c.get("rising_score") or 0))[:2]],
            "low_data": len(chans) < 4,
        })
    rows.sort(key=lambda r: (r["low_data"], -r["score"]))
    progress(100, "Готово")
    return {"rows": rows[:3], "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
            "region_name": niche.REGION_NAMES.get(niche.split_ylang(ylang)[1], niche.split_ylang(ylang)[1])}
