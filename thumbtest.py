"""
Тест превью и названия до публикации: ваши 1–3 варианта превью и до 3 вариантов названия
сравниваются с хитами ниши — что у них общего, выделяется ли ваше превью в выдаче, читается ли текст на телефоне.
"""
import base64
import datetime as dt
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, wait

import cv2
import numpy as np

import thumbs
import video
from hooks import HOOKS, hooks_of

HOOK_NAME = {k: n for k, n, _ in HOOKS}
MOBILE_W = 168          # ширина превью в мобильной выдаче YouTube, px
MIN_MOBILE_TEXT = 9     # ниже этой высоты буквы на телефоне не читаются, px


def decode(data_url):
    """data:image/...;base64 → картинка 320×180 (обрезка до 16:9 по центру)."""
    raw = base64.b64decode(re.sub(r"^data:image/[\w+.-]+;base64,", "", data_url), validate=False)
    if len(raw) > 4_000_000:
        raise ValueError("Картинка слишком большая — до 4 МБ.")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Не удалось открыть картинку. Подойдут JPG, PNG или WebP.")
    h, w = img.shape[:2]
    if w / h > 16 / 9:
        nw = int(h * 16 / 9)
        img = img[:, (w - nw) // 2:(w - nw) // 2 + nw]
    else:
        nh = int(w * 9 / 16)
        img = img[(h - nh) // 2:(h - nh) // 2 + nh]
    return cv2.resize(img, (320, 180), interpolation=cv2.INTER_AREA)


def _jpeg(img):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _share(items, fn):
    return sum(1 for x in items if fn(x)) / len(items) if items else 0


def _hue(img):
    """Средний «цветовой характер» картинки: доминирующий цвет и его доля."""
    small = cv2.resize(img, (64, 36)).reshape(-1, 3).astype(np.float32)
    return small.mean(axis=0)


def _words(t):
    return set(re.findall(r"[\wа-яё]{3,}", (t or "").lower()))


def thumb_checks(f, img, niche, nf_med, nf_share, avg_cols):
    """Проверки одного варианта превью против хитов ниши. Возвращает (checks, score)."""
    checks, score = [], 50

    def add(ok, label, detail, w=8):
        nonlocal score
        checks.append({"ok": ok, "label": label, "detail": detail})
        score += w if ok is True else -w if ok is False else 0

    face_share = nf_share["face"]
    if face_share >= 0.6:
        add(bool(f["faces"]), "Лицо в кадре", f"У {round(100 * face_share)}% хитов ниши есть лицо" + ("" if f["faces"] else " — у вас его нет."), 10)
    elif face_share <= 0.25:
        add(None if f["faces"] else True, "Лицо в кадре",
            f"В нише лица ставят редко ({round(100 * face_share)}%)" + (" — ваше лицо выделит превью, но это непроверенный для ниши приём." if f["faces"] else " — без лица, как у хитов."), 4)
    else:
        add(None, "Лицо в кадре", f"Лицо есть у {round(100 * face_share)}% хитов — обязательным не является.", 0)
    if f["faces"] and f["face_pct"] < 3:
        add(False, "Размер лица", f"Лицо занимает {f['face_pct']}% кадра — на телефоне его не разглядеть. У хитов обычно {nf_med['face_pct']}%+.", 6)
    elif f["faces"]:
        add(True, "Размер лица", f"Лицо занимает {f['face_pct']}% кадра — хорошо видно.", 4)

    tshare = nf_share["text"]
    if f["words"] == 0:
        add(False if tshare >= 0.6 else True if tshare <= 0.3 else None, "Текст на превью",
            f"Надпись есть у {round(100 * tshare)}% хитов. На вашем превью сервис не прочитал текста" + (" — если надпись есть, она слишком мелкая или сливается с фоном." if tshare >= 0.6 else "."), 8)
    else:
        lim = max(4, round(nf_med["words"]) + 2)
        add(f["words"] <= lim, "Сколько слов", f"У вас {f['words']} сл., у хитов обычно {round(nf_med['words'])}" + (" — сократите, превью читают за секунду." if f["words"] > lim else "."), 8)
        # читаемость на телефоне: высота букв при ширине 168 px
        hs = [w["h"] for w in f.get("_words") or []]
        if hs:
            mob = max(hs) * MOBILE_W / 320
            add(mob >= MIN_MOBILE_TEXT, "Текст читается на телефоне",
                f"Самые крупные буквы на телефоне — около {mob:.0f} px" + (" — читается." if mob >= MIN_MOBILE_TEXT else f", а нужно от {MIN_MOBILE_TEXT} px. Увеличьте шрифт."), 10)

    # выделяется ли среди хитов: цвет и яркость против средней картинки выдачи
    me_col = _hue(img)
    dist = float(np.linalg.norm(me_col - avg_cols)) if avg_cols is not None else 0
    stand = dist >= 45
    add(stand, "Выделяется в выдаче",
        "По цвету превью заметно отличается от соседних роликов ниши." if stand else "По цвету превью сливается с остальной выдачей ниши — попробуйте другой фон или акцентный цвет.", 8)
    add(f["contrast"] >= nf_med["contrast"] - 5, "Контраст",
        f"Контраст {f['contrast']} (у хитов {round(nf_med['contrast'])})" + ("." if f["contrast"] >= nf_med["contrast"] - 5 else " — картинка выглядит блёклой на маленьком экране."), 6)
    if f["bright"] < 30:
        add(False, "Яркость", "Слишком тёмное превью — в тёмной теме YouTube потеряется.", 6)
    if f["detail"] > nf_med["detail"] * 1.5 and f["detail"] > 12:
        add(False, "Простота", "Много мелких деталей — на телефоне превью будет «кашей». Уберите лишнее, оставьте 1–2 объекта.", 6)
    elif f["detail"] <= nf_med["detail"]:
        add(True, "Простота", "Картинка простая — читается с одного взгляда.", 4)
    return checks, max(0, min(100, score))


def title_checks(t, thumb_text, niche_titles):
    checks, score = [], 50
    lens = [len(x) for x in niche_titles if x]
    med = statistics.median(lens) if lens else 55

    def add(ok, label, detail, w=8):
        nonlocal score
        checks.append({"ok": ok, "label": label, "detail": detail})
        score += w if ok is True else -w if ok is False else 0
    n = len(t)
    add(n <= 70 and n >= 0.5 * med, "Длина", f"{n} симв., у хитов ниши обычно {round(med)}" + (" — на телефоне конец обрежется после ~70." if n > 70 else " — коротковато, мало информации." if n < 0.5 * med else "."), 8)
    hk = hooks_of(t)
    cnt = {}
    for x in niche_titles:
        for h in hooks_of(x):
            cnt[h] = cnt.get(h, 0) + 1
    top = [h for h, c in sorted(cnt.items(), key=lambda kv: -kv[1]) if c >= max(2, 0.25 * len(niche_titles)) and h != "caps"]
    used = [h for h in top if h in hk]
    if top:
        add(bool(used), "Приёмы хитов ниши",
            ("Использует " + ", ".join(HOOK_NAME[h].lower() for h in used) + " — как хиты.") if used else
            "Хиты ниши чаще используют: " + ", ".join(HOOK_NAME[h].lower() for h in top[:3]) + ". У вас этого нет.", 10)
    if not hk - {"caps"}:
        add(False, "Цепляет", "В названии нет ни одного приёма: ни интриги, ни числа, ни вопроса, ни выгоды.", 8)
    words = re.findall(r"\w+", t)
    caps = [w for w in words if len(w) >= 4 and w.isupper()]
    if len(caps) > 2:
        add(False, "Заглавные", "Больше двух слов КАПСОМ — выглядит как спам.", 6)
    tw, th = _words(t), _words(thumb_text)
    if th:
        same = len(tw & th) / max(1, len(th))
        add(same < 0.6, "Не повторяет превью",
            "Текст на превью дополняет название." if same < 0.6 else "Текст на превью почти дословно повторяет название — зря тратите место. Пусть превью добавляет эмоцию или деталь.", 8)
    nw = set().union(*[_words(x) for x in niche_titles]) if niche_titles else set()
    if nw and tw:
        add(len(tw & nw) >= 1, "Слова ниши", "Есть слова, по которым ищут ролики ниши." if tw & nw else "Ни одного слова, общего с хитами ниши, — YouTube может не понять тему.", 6)
    return checks, max(0, min(100, score))


def run(images, titles, src, lang=None, prog=lambda p, m: None):
    prog(5, "Собираю хиты ниши…")
    ylang = None
    hits = video._niche_hits(src, ylang)
    if len(hits) < 4:
        raise ValueError("Мало роликов в нише для сравнения. Укажите отчёт по нише или канал побольше.")
    prog(20, f"Разбираю превью {len(hits)} хитов ниши…")
    ex = ThreadPoolExecutor(4)
    fut = {v["id"]: ex.submit(lambda v=v: (lambda im: (im, thumbs.features(im)) if im is not None else None)(thumbs.load_img(v["id"]))) for v in hits}
    wait(list(fut.values()), timeout=75)
    ex.shutdown(wait=False, cancel_futures=True)
    got = {k: f.result() for k, f in fut.items() if f.done() and not f.cancelled() and f.exception() is None and f.result()}
    if len(got) < 4:
        raise ValueError("YouTube не отдал превью ниши — попробуйте ещё раз через пару минут.")
    nfs = [f for _, f in got.values()]
    nf_med = {k: statistics.median(f[k] for f in nfs) for k in ("face_pct", "words", "bright", "sat", "contrast", "detail")}
    nf_share = {"face": _share(nfs, lambda f: f["faces"]), "text": _share(nfs, lambda f: f["words"])}
    avg_cols = np.mean([_hue(im) for im, _ in got.values()], axis=0)
    prog(70, "Проверяю ваши варианты…")
    variants = []
    for i, d in enumerate(images[:3]):
        img = decode(d)
        f = thumbs.features(img)
        f["_words"] = thumbs._ocr(img)
        checks, score = thumb_checks(f, img, hits, nf_med, nf_share, avg_cols)
        f.pop("_words", None)
        variants.append({"n": i + 1, "img": _jpeg(img), "score": score, "checks": checks,
                         "faces": f["faces"], "face_pct": f["face_pct"], "words": f["words"], "text": f["text"], "palette": f["palette"]})
    niche_titles = [v.get("title") or "" for v in hits]
    thumb_text = max(variants, key=lambda v: v["score"])["text"] if variants else ""
    tvars = []
    for i, t in enumerate([t.strip() for t in titles if t and t.strip()][:3]):
        checks, score = title_checks(t, thumb_text, niche_titles)
        tvars.append({"n": i + 1, "text": t, "score": score, "checks": checks, "hooks": [HOOK_NAME[h] for h in hooks_of(t) if h != "caps"]})
    best_v = max(variants, key=lambda v: v["score"])["n"] if variants else None
    best_t = max(tvars, key=lambda v: v["score"])["n"] if tvars else None
    niche = [{"id": v["id"], "title": v.get("title"), "channel": v.get("channel"), "views": v.get("views"), "x": v.get("x")}
             for v in hits if v["id"] in got][:11]
    prog(100, "Готово")
    return {"type": "thumbtest", "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
            "variants": variants, "titles": tvars, "best_thumb": best_v, "best_title": best_t,
            "title": (next((t for t in tvars if t["n"] == best_t), None) or {}).get("text") or "без названия",
            "niche": niche, "niche_stats": {"videos": len(nfs), "face_share": round(nf_share["face"], 2), "text_share": round(nf_share["text"], 2),
                                            "words": nf_med["words"], "face_pct": nf_med["face_pct"]}}
