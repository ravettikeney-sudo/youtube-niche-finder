"""Что изменилось в нише между двумя пересчётами: новые взлетающие каналы, рост, новые хиты, оценка."""


def _rel(res):
    return {c["id"]: c for c in (res or {}).get("channels", []) if c.get("relevant") is not False and c.get("id")}


def diff(prev, new):
    n0, n1 = (prev or {}).get("niche", {}), new.get("niche", {})
    old, cur = _rel(prev), _rel(new)
    out = {"overall": n1.get("overall"), "overall_delta": (n1.get("overall") - n0["overall"]) if n0.get("overall") is not None else None,
           "scores_delta": {k: v - (n0.get("scores") or {}).get(k, v) for k, v in (n1.get("scores") or {}).items()},
           "first": prev is None}
    # новые растущие каналы, которых раньше не было в нише
    out["new_rising"] = [{"id": c["id"], "title": c["title"], "url": c["url"], "subs": c.get("subs"),
                          "age_days": c.get("age_days"), "rising_score": c.get("rising_score"), "avatar": c.get("avatar")}
                         for c in sorted(cur.values(), key=lambda c: -(c.get("rising_score") or 0))
                         if c["id"] not in old and (c.get("rising_score") or 0) >= 55][:8]
    # кто сильнее всех вырос по подписчикам
    grew = []
    for cid, c in cur.items():
        o = old.get(cid)
        if o and o.get("subs") and c.get("subs") and c["subs"] > o["subs"]:
            grew.append({"id": cid, "title": c["title"], "url": c["url"], "subs": c["subs"], "was": o["subs"],
                         "gain": c["subs"] - o["subs"], "pct": round(100 * (c["subs"] - o["subs"]) / o["subs"], 1)})
    grew.sort(key=lambda g: -g["pct"])
    out["grew"] = grew[:6]
    # новые ролики-хиты
    seen = {g["id"] for g in (prev or {}).get("gallery", [])} | {h["id"] for c in old.values() for h in c.get("hit_videos", [])}
    out["new_hits"] = [g for g in new.get("gallery", []) if g["id"] not in seen][:8]
    out["left"] = len([cid for cid in old if cid not in cur])
    out["young_rising"] = (n0.get("young_rising"), n1.get("young_rising"))
    return out
