"""Один пересчёт отчёта после ручных исключений для экрана и выгрузки."""
from collections import Counter
from statistics import median

import analyzer
import insights
import verify


def build(report):
    if report is None:
        return None
    d = dict(report)
    basis = d.pop("_score_basis", None)
    excluded = set(d.get("excluded") or [])
    channels = d.get("channels", [])
    if "niche" not in d or not any(c["id"] in excluded and c.get("relevant", True) for c in channels):
        return d

    d["channels"] = [dict(c, relevant=False, off_reason="исключён вручную") if c["id"] in excluded else c for c in channels]
    kept = [c for c in d["channels"] if c.get("relevant", True)]
    urls = {c.get("url") for c in kept}
    for key in ("gallery", "entry_topics"):
        d[key] = [v for v in d.get(key, []) if v.get("channel_url") in urls]
    # Готовые подборки построены по прежней выборке. Обновлённый анализ соберёт их заново.
    d.update(neighbor_candidates=[], neighbors=None, video_ideas=None)
    rpm = d["niche"]["rpm_range"]
    views = [v for cid, v in (basis or {}).get("search", []) if cid not in excluded]
    stats = verify.niche_scores(kept, views, rpm)
    enough = verify.data_sufficient(kept, views)
    overall = round(stats["overall"]) if enough else None
    young, rising = stats["young"], stats["young_rising"]
    incomes = [r["income"] for r in rising if r.get("income")]
    scores = {k: round(stats[v]) if enough else None for k, v in
              (("demand", "demand"), ("competition", "competition"), ("entry_window", "window"),
               ("monetization", "monet"), ("freshness", "freshness"))}
    source = d["niche"]
    d["niche"] = dict(source, overall=overall, scores=scores,
                      verdict=verify.niche_verdict(overall) if basis else "Запустите новый анализ, чтобы пересчитать оценку старого отчёта.",
                      relevant=len(kept), young=len(young), young_rising=len(rising),
                      median_search_views=int(stats["med_views"]) if views else None,
                      median_subs=int(stats["med_subs"]) if kept else None,
                      big_channels_share=round(stats["big_share"] * 100),
                      income_young=[round(median(x[i] for x in incomes)) for i in range(2)] if incomes else None)
    prepared = [dict(c, _ents=(basis or {}).get("entries", {}).get(c["id"], [])) for c in kept]
    d["durations"], d["niche"]["best_duration"] = insights.duration_stats(prepared)
    d["titles"] = insights.title_stats(prepared, analyzer.tokenize, set((basis or {}).get("title_words", [])))
    d["formats"] = insights.format_stats(kept, young, rpm) if kept else None
    d["forecast"] = insights.forecast(young, rising)
    d["keyword_table"] = []
    young_ids = {c["id"] for c in young}
    for key, rows in (basis or {}).get("keywords", {}).items():
        sample = [(cid, count) for cid, count in rows if cid not in excluded]
        if not sample:
            continue
        ids = {cid for cid, _ in sample}
        middle = median(v for _, v in sample)
        fraction = len(ids & young_ids) / len(ids)
        d["keyword_table"].append({"keyword": key, "median_views": int(middle), "channels": len(ids), "young_share": round(100 * fraction),
                                   "score": round(0.5 * verify.logscore(middle, 1000, 10 ** 6) + 0.5 * verify.clamp(fraction * 250))})
    d["keyword_table"].sort(key=lambda r: -r["score"])
    months = [(cid, v) for cid, v in (basis or {}).get("month", []) if cid not in excluded]
    langs = Counter(c["lang"] for c in kept if c.get("lang"))
    trends = [c["trend"] for c in kept if c.get("trend") is not None]
    countries = Counter(c.get("country") for c in kept if c.get("country"))
    rates = [c["uploads_per_week"] for c in rising if c.get("uploads_per_week")]
    d["dynamics"] = dict(d.get("dynamics") or {}, month_median_views=int(median(v for _, v in months)) if months else None,
                         month_videos=len(months), month_channels=len({cid for cid, _ in months}),
                         accelerating=sum(t >= 1.3 for t in trends), slowing=sum(t <= .75 for t in trends), trend_known=len(trends),
                         upw_young=round(median(rates), 1) if rates else None,
                         languages=[{"code": k, "name": insights.LANG_NAMES.get(k, k), "share": round(100 * v / len(kept))}
                                    for k, v in langs.most_common(6)],
                         countries=[{"name": k, "share": round(100 * v / len(kept))} for k, v in countries.most_common(5)])
    d["verification"] = {"level": "средняя" if enough and len(kept) >= 6 else "низкая",
                         "range": verify.bootstrap(kept, views, rpm) if enough else None,
                         "checks": [{"key": "exclusions", "title": "Выборка после исключений", "state": "ok" if enough else "warn",
                                     "detail": f"{len(kept)} каналов ниши и {len(views)} уникальных роликов в поиске"}]}
    d["plan"] = insights.entry_plan(d) if enough else []
    return d
