"""
Подачи из соседних ниш: какие формулы роликов дают взлёты у соседей по категории, но почти не используются в вашей нише.
Идея — не копировать тему конкурента (её за неделю выжгут копиями), а перенести к себе работающую ПОДАЧУ.
Для каждой подачи — реальные взлёты-примеры и заготовки названий со словами вашей ниши.
"""
import re
import statistics
from collections import Counter

import ideas

# (ключ, название, рег. выражение (ru и en вместе), шаблон-заготовка ru, шаблон en)
PATTERNS = [
    ("what_if", "«Что будет, если…»", r"что (будет|случится|если)|а что если|what (would|will) happen|what if\b",
     "{S}: что будет, если всё изменится за одну ночь?", "What Would Happen If {s} Disappeared Tomorrow?"),
    ("days", "Испытание временем: «N дней / часов»", r"\b\d+\s*(дн|час|сут|недел)[а-яё]*\b|(провёл|провел|прожил|выжил)[^.?!]{0,25}\d+|\b\d+\s*(days?|hours?|weeks?)\b",
     "7 дней внутри темы «{s}»: вот что я узнал", "I Spent 7 Days Inside {s}"),
    ("rules", "«N правил, если…»", r"\d+\s*правил|правила выживания|\brules?\b (if|for|of)|\d+ rules",
     "{S}: 10 правил, о которых молчат", "10 Rules If You Ever End Up in {s}"),
    ("nobody", "«Почему почти никто не…»", r"почему (почти )?никто|никто не (знает|живёт|живет|ездит|может)|why (almost )?(no ?one|nobody)|nobody (knows|lives|goes)",
     "{S}: то, о чём почти никто не знает", "Why Almost Nobody Talks About {s}"),
    ("hidden", "Тайна и скрытое: «Что скрывает…»", r"что скрыва|тайн|секрет|скрыт|не расскаж|hidden|secret|what .* (hides|hiding)|truth about",
     "{S}: что на самом деле скрывают?", "The Hidden Truth About {s}"),
    ("super", "Превосходная степень: «Самый… в мире»", r"самы[йяеи][^.?!]{0,40}(в мире|на земле|в истории|в россии)|(most|biggest|largest|worst|strangest|deadliest)\b[^.?!]{0,40}(in the world|on earth|ever|in history)",
     "{S}: самое странное, что есть в мире", "The Strangest {s} in the World"),
    ("versus", "Сравнение: «А против Б»", r"\bvs\.?\b|\bпротив\b|\bили\b.+\?$|\bversus\b",
     "{S} против {S2}: кто кого?", "{S} vs {S2}: Which Is Better?"),
    ("tested", "Личный эксперимент: «Я проверил / попробовал»", r"\bя (проверил|попробовал|купил|построил|прожил|поехал|съел)|\bi (tested|tried|built|bought|lived|ate|visited)\b",
     "Я проверил на себе: {s} — что вышло", "I Tested {s} So You Don't Have To"),
    ("impossible", "Невозможное: «Почему нельзя / невозможно»", r"невозможн|нельзя|запрещ|impossible|can'?t (be|exist)|banned|illegal",
     "{S}: почему это невозможно повторить", "Why {s} Is Impossible to Copy"),
    ("timejump", "Скачок во времени: «через 100 лет / спустя»", r"через \d+|спустя \d+|\d+ (лет|years?) (спустя|later|from now)|in \d+ years|after \d+ years",
     "{S} через 100 лет", "{s} in 100 Years"),
    ("full", "Всё в одном ролике: «Вся история / объяснение»", r"вся (история|правда)|полная история|за \d+ минут|документальн|full (story|history)|explained in|documentary",
     "{S}: вся история за 15 минут", "The Entire History of {s} in 15 Minutes"),
    ("list", "Подборка: «N фактов / мест / ошибок»", r"^\s*(топ|top)?\s*\d+\s+[a-zа-яё]",
     "{S}: 7 фактов, которых вы не знали", "7 Facts About {s} You Didn't Know"),
]
_RX = {k: re.compile(rx, re.I) for k, _, rx, _, _ in PATTERNS}
NAME = {k: n for k, n, *_ in PATTERNS}


def tags(title):
    t = ideas.main_part(title or "")
    got = {k for k, rx in _RX.items() if rx.search(t)}
    return got - {"list"} if len(got) > 1 else got


JUNK = {ideas._st(w) for w in """которую который которые которых почему самый самая самое никто этот этой этого будет если может
можно нельзя только когда очень просто часть серия сезон история истории страшная страшные анимация reacts react video part
episode season official why how what this that these about into from""".split()}


def subjects(own_hits, n=3):
    """Главные предметы вашей ниши — частые содержательные слова в ваших лучших роликах, как они пишутся."""
    titles = [h["title"] for h in own_hits]
    cnt = Counter()
    for t in titles:
        cnt.update(set(ideas._stems(ideas._content(ideas.main_part(t)))))
    out = []
    ranked = [kv for kv in cnt.most_common(30) if kv[1] >= 2] + [kv for kv in cnt.most_common(30) if kv[1] < 2 and len(kv[0]) >= 4]
    for term, c in ranked:
        if len(term) < 3 or term in JUNK:
            continue
        w = ideas._surface(term, titles)
        lw = (w or "").lower()
        if re.search(r"(ed|ing)$", lw) or re.search(r"(ть|ться|л|ла|ли|ло|ет|ит|ют|ят|ешь|ишь)$", lw):
            continue    # глаголы — не предмет ниши
        if w and lw not in {o.lower() for o in out}:
            out.append(w)
        if len(out) >= n:
            break
    return out


def build(own_pool, adj_pool, lang="ru", own_main=None):
    """own_pool / adj_pool — списки роликов {id,title,channel,views,x} (x — во сколько раз выше обычного для канала).
    own_pool — ваша ниша (все ролики), adj_pool — взлёты соседних ниш."""
    if len(adj_pool) < 4:
        return None
    ru = lang in ("ru", "uk")
    own_tags = Counter(t for v in own_pool for t in tags(v["title"]))
    own_n = max(1, len(own_pool))
    own_hits = own_main or sorted(own_pool, key=lambda v: -(v.get("x") or 0))[:15]
    subj = subjects(own_hits) or ["ваша тема"]
    by = {}
    for v in adj_pool:
        for t in tags(v["title"]):
            by.setdefault(t, []).append(v)
    res = []
    for k, vs in by.items():
        chans = {v["channel"] for v in vs}
        if len(vs) < 2 or (len(chans) < 2 and len(vs) < 3):
            continue
        mx = statistics.median(v["x"] for v in vs)
        share_own = own_tags.get(k, 0) / own_n
        if mx < 2 or share_own > 0.15:
            continue
        tpl = next(p for p in PATTERNS if p[0] == k)[3 if ru else 4]
        s2 = subj[1] if len(subj) > 1 else subj[0]
        cap = lambda w: w[:1].upper() + w[1:]
        def fill(x):
            y = s2 if s2 != x else (subj[2] if len(subj) > 2 else s2)
            return tpl.format(s=x, s2=y, S=cap(x), S2=cap(y))
        drafts = list(dict.fromkeys(fill(x) for x in subj[:2]))
        res.append({"key": k, "name": NAME[k], "videos": len(vs), "channels": len(chans), "x": round(mx, 1),
                    "own_share": round(100 * share_own), "own_n": own_tags.get(k, 0),
                    "examples": [{"id": v["id"], "title": v["title"], "channel": v["channel"], "views": v["views"], "x": v["x"]}
                                 for v in sorted(vs, key=lambda v: -v["x"])[:3]],
                    "drafts": drafts,
                    "score": mx * (1 + 0.3 * (len(chans) - 1)) * (1.4 if share_own == 0 else 1)})
    res.sort(key=lambda r: -r["score"])
    return {"items": res[:6], "subjects": subj, "adj_videos": len(adj_pool),
            "adj_channels": len({v["channel"] for v in adj_pool})}
