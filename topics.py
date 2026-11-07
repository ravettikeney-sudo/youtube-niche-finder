"""
Поиск ниш по теме, без канала-конкурента.

Тема («кулинария») → поднишы из подсказок поиска YouTube («кулинария для начинающих», …) →
для каждой: поиск, каналы, их возраст и рост → те же оценки, что и в обычном отчёте → рейтинг поднишей.
"""
import re
import statistics
import datetime as dt
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import analyzer as A
import insights
import niche
import verify

MAX_SUB = 8          # сколько поднишей оцениваем
PER_SUB = 6          # каналов на поднишу
MAX_CHANNELS = 40    # всего каналов на анализ


def _stems(text):
    return {w[:5] for w in A.tokenize(text)}


# кликбейт и «вода» — не темы
FILLER = set("""деле рту тает минуту минуты минут съедят съели съест вкусно вкуснее вкусный вкусная вкусные вкусное
обалденный обалденно потрясающе потрясающий невероятно простых просто быстро быстрый секрет секреты никто всех
каждый этого такого такой такая честно правда шок шокирует ахнете ахнули удивит удивились проще легко лучше
бабушка бабушки научила хитрость хитрости лайфхак лайфхаки способ способа ошибок ошибки нищим богатым
рецепт рецептом вместо больше никогда показываю расскажу узнаете всей семьи""".split())


def _name_tokens(name):
    return set(re.findall(r"[a-zа-яё0-9]+", (name or "").lower())) - {"the", "and", "tv", "channel", "канал", "official"}


def families(chans):
    """Сети однотипных каналов («Candy DO Russian», «Multi DO Smile Russian»…) считаем одним каналом:
    иначе одна сеть выдаёт свои фразы за поднишу. Общие два слова в названии или сходство ≥ 50%."""
    ids = [c["id"] for c in chans]
    toks = {c["id"]: _name_tokens(c.get("title") or c.get("channel")) for c in chans}
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ta, tb = toks[a], toks[b]
            inter = len(ta & tb)
            if ta and tb and (inter >= 2 or inter / len(ta | tb) >= 0.5):
                parent[find(a)] = find(b)
    return {i: find(i) for i in ids}


def derive_subniches(topic, chans, excl, n=MAX_SUB, want="cyr"):
    """Поднишы из ключевых слов, которые авторы указали для своих каналов, и из названий их роликов.
    Фраза засчитывается, только если её используют несколько разных каналов темы."""
    base = _stems(topic)
    names = [_stems(c["title"] or "") for c in chans]
    fam = families(chans)
    tag_ch, bi_ch = defaultdict(set), defaultdict(set)
    for c in chans:
        for t in c.get("channel_tags") or []:
            t = t.lower().strip()
            st = _stems(t) - base
            if 3 <= len(t) <= 35 and st and not excl(t) and len(A.tokenize(t)) <= 4 and niche.same_script(t, want):
                tag_ch[t].add(fam[c["id"]])
        for title in c["titles"][:60]:
            tk = [w for w in A.tokenize(title) if w[:5] not in base]
            for a, b in zip(tk, tk[1:]):
                if not (a.isdigit() or b.isdigit() or a in FILLER or b in FILLER) and niche.same_script(a + " " + b, want):
                    bi_ch[a + " " + b].add(fam[c["id"]])
    cands = []
    for ph, chs in list(tag_ch.items()) + list(bi_ch.items()):
        k = len(chs)
        is_tag = ph in tag_ch
        if k < (2 if is_tag else 3) or (set(A.tokenize(ph)) & FILLER):
            continue
        st = _stems(ph)
        if any(st and st <= nm for nm in names):  # это название канала, а не тема
            continue
        cands.append((k * (2.0 if is_tag else 1.0) + 0.1 * len(st), ph))
    cands.sort(reverse=True)
    out, seen = [], []
    for _, ph in cands:
        st = _stems(ph) - base
        if not st or any(st <= s or s <= st for s in seen):
            continue
        seen.append(st)
        out.append(ph)
        if len(out) >= n:
            break
    return out


def run_topic(topic, progress=lambda p, m: None, lang="auto", same_lang=True, exclude=None, max_age_days=730, region=None):
    region = None if region in (None, "", "auto") else region.upper()
    topic = re.sub(r"\s+", " ", topic).strip()
    if lang in (None, "", "auto"):
        lang = "ru" if niche.script(topic) == "cyr" else "en"
    ylang = niche.ylang_for(lang, region)
    want = "cyr" if lang in ("ru", "uk") else "lat" if lang in insights.LATIN_HINTS or lang == "en" else "other"
    excl_words = [w.strip() for w in re.split(r"[,\n;]", exclude or "") if w.strip()][:20]
    excl = A.excl_matcher(excl_words)

    def analyze_many(cids, p0, p1, label):
        out = {}
        with ThreadPoolExecutor(A.WORKERS) as ex:
            futs = {ex.submit(A.analyze_channel, f"https://www.youtube.com/channel/{cid}", 60, False, ylang): cid for cid in cids}
            for i, fu in enumerate(as_completed(futs), 1):
                cid = futs[fu]
                try:
                    r = fu.result()
                except Exception:
                    r = None
                if r:
                    r["id"] = r["id"] or cid
                    r["young"] = bool(r["age_exact"] and r["age_days"] is not None and r["age_days"] <= max_age_days)
                    r["lang_ok"] = niche.channel_lang_ok(r["titles"][:30], want, r.get("description")) \
                        and not (r.get("audio_lang") and r["audio_lang"] != lang)
                    if want == "lat" and not r.get("audio_lang"):  # латиницу различаем по описанию канала
                        dl, _ = niche.lang_confident(r.get("description") or "")
                        if dl and dl != lang:
                            r["lang_ok"] = False
                    if verify.suspicious(r):  # накрутка или удалённые ролики — не «молодой растущий»
                        r["young"] = False
                        r["suspicious"] = True
                    out[r["id"]] = r
                progress(p0 + (p1 - p0) * i / max(len(cids), 1), f"{label}: {i}/{len(cids)}")
        return out

    def ok(e):
        return not excl(e.get("title")) and (not same_lang or niche.same_script(e.get("title"), want))

    # ---- 1. каналы темы: поиск по самой теме
    progress(4, "Ищу каналы по теме…")
    found = defaultdict(list)
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(lambda sp: A.search(topic, 40, sp, ylang), ["", A.SEARCH_FILTERS["year"]]))
    for f, ents in zip(("relevance", "year"), res):
        found[topic.lower()] += [dict(e, _f=f) for e in ents if ok(e)]
    cnt = Counter(e["channel_id"] for e in found[topic.lower()])
    cname = {e["channel_id"]: e.get("channel") for e in found[topic.lower()]}
    fam = families([{"id": cid, "title": cname.get(cid)} for cid in cnt])
    per_fam, seed_ids = Counter(), []
    for cid, _ in cnt.most_common():
        if per_fam[fam[cid]] < 2:  # не больше двух каналов из одной сети
            per_fam[fam[cid]] += 1
            seed_ids.append(cid)
        if len(seed_ids) >= 22:
            break
    if len(seed_ids) < 4:
        raise RuntimeError("По этой теме на YouTube слишком мало роликов на выбранном языке. Попробуйте сформулировать иначе.")
    analyzed = analyze_many(seed_ids, 8, 40, "Изучаю каналы темы")
    topic_chans = [c for c in analyzed.values() if c["lang_ok"] or not same_lang]

    # ---- 2. поднишы — из ключевых слов каналов и названий их роликов
    progress(41, "Определяю поднишы…")
    subs = derive_subniches(topic, topic_chans, excl, want=want)
    if not subs:
        raise RuntimeError("Не удалось выделить поднишы — каналы темы слишком разные. Попробуйте тему поуже.")
    progress(43, f"Поднишы: {', '.join(subs)}")
    subs = [topic.lower()] + subs

    # ---- 3. поиск по каждой поднише (по самой фразе: так её и ищут зрители)
    jobs = [(sn, f, sp) for sn in subs[1:] for f, sp in (("relevance", ""), ("year", A.SEARCH_FILTERS["year"]))]
    with ThreadPoolExecutor(A.WORKERS) as ex:
        futs = {ex.submit(A.search, sn, 25, sp, ylang): (sn, f) for sn, f, sp in jobs}
        for i, fu in enumerate(as_completed(futs), 1):
            sn, f = futs[fu]
            found[sn] += [dict(e, _f=f) for e in (fu.result() or []) if ok(e)]
            progress(43 + 15 * i / len(jobs), f"Поиск по поднишам: {i}/{len(jobs)}")

    # ---- 4. новые каналы из выдачи поднишей
    extra = []
    for k in range(PER_SUB):
        for sn in subs[1:]:
            ids = [cid for cid, _ in Counter(e["channel_id"] for e in found[sn]).most_common(PER_SUB)]
            if k < len(ids) and ids[k] not in analyzed and ids[k] not in extra and len(extra) + len(analyzed) < MAX_CHANNELS + 20:
                extra.append(ids[k])
    analyzed.update(analyze_many(extra, 58, 93, "Проверка каналов поднишей"))

    # канал «из темы», если его ролики похожи на ролики каналов темы (а не случайный по слову)
    vocab = niche.doc_terms([t for c in topic_chans for t in c["titles"][:40]], A.tokenize)
    sim = niche.Similarity(vocab, {cid: niche.doc_terms(c["titles"][:60], A.tokenize) for cid, c in analyzed.items()})
    raw = {cid: sim.score(niche.doc_terms(c["titles"][:60], A.tokenize)) for cid, c in analyzed.items()}
    cut, _ = niche.adaptive_cut(raw.values(), strict=False)
    for cid, c in analyzed.items():
        c["in_topic"] = raw.get(cid, 0) >= cut

    # ---- оценка каждой подниши теми же формулами, что и в отчёте по каналу
    progress(95, "Считаю оценки поднишей…")
    base_st = _stems(topic)
    rows = []
    for s in subs:
        own = (_stems(s) - base_st) or _stems(s)
        chans = []
        cand_ids = set(e["channel_id"] for e in found[s]) | ({c["id"] for c in topic_chans} if s == topic.lower() else set())
        for cid in cand_ids | set(analyzed):
            r = analyzed.get(cid)
            if not r or (same_lang and not r["lang_ok"]) or not r.get("in_topic"):
                continue
            if s == topic.lower():
                if cid in cand_ids:
                    chans.append(r)
                continue
            tagged = any(own <= _stems(t) or (_stems(t) & own and len(own) == 1) for t in (r.get("channel_tags") or []))
            hit = sum(1 for t in r["titles"][:60] if own <= _stems(t))
            if tagged or hit >= max(2, 0.08 * len(r["titles"][:60])):
                chans.append(r)
        views = [e["view_count"] for e in found[s] if e.get("view_count") is not None]
        if not views or not chans:
            continue
        rpm, rpm_cat = A.estimate_rpm([], s + " " + topic)
        # те же формулы, что в отчёте по каналу, + диапазон оценки на случайных подвыборках
        S = verify.niche_scores(chans, views, rpm)
        med_v, young, yr = S["med_views"], S["young"], S["young_rising"]
        demand, competition, window, monet, overall = S["demand"], S["competition"], S["window"], S["monet"], S["overall"]
        rng = verify.bootstrap(chans, views, rpm, n=200)
        width = (rng[1] - rng[0]) if rng else 99
        reliab = "высокая" if len(chans) >= 8 and width <= 16 else "средняя" if len(chans) >= 4 and width <= 28 else "низкая"
        recent = [e for e in found[s] if e["_f"] == "year" and e.get("view_count") is not None]
        top_v = sorted({e["id"]: e for e in found[s]}.values(), key=lambda e: -(e.get("view_count") or 0))[:3]
        rows.append({
            "name": s, "is_topic": s == topic.lower(),
            "score": round(overall),
            "scores": {"demand": round(demand), "competition": round(competition), "entry_window": round(window),
                       "monetization": round(monet)},
            "median_views": int(med_v), "recent_median_views": int(statistics.median([e.get("view_count") or 0 for e in recent])) if recent else None,
            "channels": len(chans), "young": len(young), "young_rising": len(yr),
            "rpm_range": rpm, "rpm_category": rpm_cat,
            "examples": [{"title": c["title"], "url": c["url"], "avatar": c.get("avatar"), "subs": c.get("subs"),
                          "age_days": c.get("age_days"), "rising_score": c["rising_score"], "median_views": c["median_views"]}
                         for c in sorted(yr or young or chans, key=lambda c: -c["rising_score"])[:3]],
            "top_videos": [{"id": e["id"], "title": e.get("title"), "views": e.get("view_count"), "channel": e.get("channel")}
                           for e in top_v],
            "low_data": len(chans) < 3, "range": rng, "reliability": reliab,
        })
    if not rows:
        raise RuntimeError("Не удалось собрать данные по теме. Попробуйте другую формулировку.")
    rows.sort(key=lambda r: (r["low_data"], -r["score"]))
    best = rows[0]
    verdict = (f"Лучшая точка входа — «{best['name']}»: оценка {best['score']}, "
               f"растущих новичков {best['young_rising']} из {best['channels']}, обычно {insights.fmt_n(best['median_views'])} просмотров на видео.")
    progress(100, "Готово")
    return {"type": "topic", "topic": topic, "lang": lang, "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
            "subniches": rows, "best_score": best["score"], "verdict": verdict, "exclude_words": excl_words,
            "channels_checked": len(analyzed), "region": niche.split_ylang(ylang)[1],
            "region_name": niche.REGION_NAMES.get(niche.split_ylang(ylang)[1], niche.split_ylang(ylang)[1])}
