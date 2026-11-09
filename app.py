"""Веб-сервер Niche Finder: вход по ключу, анализ в фоне, админ-панель ключей."""
import os
import re
import json
import time
import uuid
import hashlib
import secrets
import threading
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import access
import topics
import watchdiff
from analyzer import run_analysis

ROOT = Path(__file__).parent
RESULTS = access.DATA / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="YouTube Niche Finder", docs_url=None, redoc_url=None)
access.init()
JOBS = {}
EXCL_LOCK = threading.Lock()
SEM = threading.Semaphore(int(os.environ.get("MAX_PARALLEL_JOBS", "2")))


# ---------------------------------------------------------------- доступ
def need(key: Optional[str], admin=False, activate=True):
    a = access.check(key or "", activate=activate)
    if not a:
        raise HTTPException(401, "Ключ недействителен или срок истёк")
    if admin and a["role"] != "admin":
        raise HTTPException(403, "Нужен ключ администратора")
    return a


class Login(BaseModel):
    key: str


@app.post("/api/login")
def login(body: Login, request: Request):
    ip = request.client.host if request.client else "?"
    if access.too_many_fails(ip):
        raise HTTPException(429, "Слишком много неверных попыток. Подождите 10 минут.")
    a = access.check(body.key, count=True)
    if not a:
        access.add_fail(ip)
        raise HTTPException(401, "Ключ не подошёл или его срок истёк")
    return me_info(a)


def me_info(a):
    return {"role": a["role"], "expires_at": a["expires_at"], "hint": a["hint"],
            "daily_limit": a.get("daily_limit"), "used_24h": a.get("used_24h", 0),
            "watch_limit": None if a["role"] == "admin" else MAX_WATCHES, "promo": a.get("source") == "promo",
            "tg": bool(access.tg_link_get(a["hash"]))}


@app.get("/api/me")
def me(x_key: Optional[str] = Header(None)):
    return me_info(need(x_key))


# ---------------------------------------------------------------- анализ
class Req(BaseModel):
    url: str
    keywords: Optional[str] = None
    max_channels: int = Field(30, ge=5, le=80)
    per_query: int = Field(25, ge=10, le=60)
    max_age_days: int = Field(730, ge=30, le=3650)
    lang: str = "auto"          # язык ниши: auto / ru / en / ...
    same_lang: bool = True      # только каналы на языке ниши
    strict: bool = True         # строгая похожесть тематики
    exclude: Optional[str] = None  # слова-исключения через запятую
    region: str = "auto"        # страна поиска: auto (по языку ниши) / US / RU / ...


def check_limit(a, cost):
    """Лимит анализов в сутки (скользящие 24 часа). У админа и ключей «без лимита» его нет."""
    lim = a.get("daily_limit")
    if a["role"] == "admin" or not lim:
        return
    used = access.usage(a["hash"])
    if used + cost > lim and a.get("source") == "promo":
        raise HTTPException(429, f"Бесплатные попытки закончились: {used} из {lim}. Понравилось? Купите доступ — от одного дня.")
    if used + cost > lim:
        raise HTTPException(429, f"Лимит анализов на сутки исчерпан: {used} из {lim}"
                                 + (" (поиск по теме считается за 3, разбор превью — за 2)" if cost > 1 else "")
                                 + ". Лимит восстанавливается постепенно, через 24 часа после каждого анализа.")


def start_job(owner, kind, cost, label, fn, params=None, on_done=None, run=None, jid=None):
    """Запускает задачу в фоне: анализ канала, поиск по теме или пересчёт отслеживаемой ниши.
    run — всё, чтобы перезапустить анализ, если сервер перезапустится (обновление) посреди работы.
    jid — продолжить уже записанный анализ (после перезапуска), без повторного списания."""
    resumed = jid is not None
    jid = jid or uuid.uuid4().hex[:12]
    JOBS[jid] = {"status": "running", "progress": 0, "message": "Сервер обновлялся — продолжаю анализ…" if resumed else "В очереди…",
                 "result": None, "error": None, "owner": owner, "kind": kind}
    if not resumed:
        access.log_job(jid, owner, label, kind, cost, run)

    def prog(p, m):
        JOBS[jid].update(progress=round(p), message=m)

    def work():
        with SEM:
            try:
                res = fn(prog)
                res["params"] = params or {}
                res["job_kind"] = kind
                (RESULTS / f"{jid}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                JOBS[jid].update(status="done", progress=100, result=res)
                if res.get("type") == "topic":
                    access.finish_job(jid, "done", res.get("best_score"), "Тема: " + res.get("topic", ""))
                else:
                    access.finish_job(jid, "done", res["niche"]["overall"], res["competitor"]["title"])
                if on_done:
                    on_done(jid, res)
                notify_done(jid, kind, owner, res)
            except Exception as e:
                JOBS[jid].update(status="error", error=str(e))
                access.finish_job(jid, "error", error=str(e)[:500])
                notify_error(jid, kind, label, e)
                if on_done:
                    on_done(jid, None)

    threading.Thread(target=work, daemon=True).start()
    return jid


def runner_for(kind, run, owner):
    """Функция анализа по сохранённым параметрам — для перезапуска после обновления сервера."""
    if kind == "channel":
        return channel_runner(run, owner)
    if kind == "topic":
        return lambda prog: topics.run_topic(run["topic"], prog, lang=run.get("lang", "auto"), same_lang=run.get("same_lang", True),
                                             exclude=run.get("exclude"), max_age_days=run.get("max_age_days", 730), region=run.get("region"))
    if kind == "thumbs":
        import thumbs
        return lambda prog: thumbs.run_thumbs(run["url"], run.get("competitors") or [], run.get("script"), run.get("topic"), prog,
                                              remembered=lambda own_id: access.excl_list(owner, "th:" + (own_id or "")))
    return None


def resume_interrupted():
    """После перезапуска сервера: продолжить анализы, которые шли в этот момент (не дольше 2 попыток)."""
    access.fail_stale_jobs()
    for j in access.interrupted_jobs():
        run = json.loads(j["run"]) if j.get("run") else None
        fn = runner_for(j.get("kind"), run, j["code_hash"]) if run and (j.get("resumed") or 0) < 2 else None
        if not fn:
            access.finish_job(j["id"], "error", error="Анализ прервался из-за обновления сервера. Запустите его ещё раз — попытка не списана.")
            access.refund_job(j["id"])
            continue
        access.mark_resumed(j["id"])
        params = {k: v for k, v in run.items() if k != "script"}
        if j.get("kind") == "thumbs":
            params["has_script"] = bool(run.get("script"))
        start_job(j["code_hash"], j["kind"], j.get("cost") or 1, j["url"], fn, params, jid=j["id"])


def notify_done(jid, kind, owner, res):
    """«Отчёт готов» в Telegram — анализ идёт пару минут, люди уходят со вкладки."""
    if kind == "watch":
        return
    try:
        import bot
        site = bot.site_url()
        if res.get("type") == "topic":
            what = f"Поиск по теме «{res.get('topic', '')}»"
        elif kind == "thumbs":
            what = f"Разбор превью «{(res.get('channel') or {}).get('title', '')}»"
        else:
            n = res.get("niche") or {}
            what = f"Ниша «{(res.get('competitor') or {}).get('title', '')}»: оценка {n.get('overall')} — {n.get('verdict', '')}"
            ideas = len(((res.get("video_ideas") or {}).get("ideas") or [])) + len(((res.get("video_ideas") or {}).get("formats") or []))
            if ideas:
                what += f"\n💡 Идей роликов: {ideas}"
        url = f"{site}/?job={jid}" if site else None
        bot.notify_key(owner, f"✅ <b>Отчёт готов</b>\n{bot.h(what)}", ("Открыть отчёт", url) if url and url.startswith("https://") else None)
    except Exception:
        pass


def notify_error(jid, kind, label, e):
    try:
        import bot
        if isinstance(e, ValueError):  # ошибка во вводе (нет такого канала и т.п.) — это не сбой сервиса
            return
        bot.notify_admins(f"⚠️ Анализ завершился ошибкой\n{label}\nПричина: {str(e)[:300]}\nid: {jid}")
    except Exception:
        pass


def channel_runner(p, owner=None):
    return lambda prog: run_analysis(p["url"], p.get("keywords"), p.get("max_channels", 30), p.get("per_query", 25),
                                     p.get("max_age_days", 730), prog, same_lang=p.get("same_lang", True),
                                     strict=p.get("strict", True), lang=p.get("lang", "auto"), exclude=p.get("exclude"),
                                     region=p.get("region", "auto"),
                                     remembered=(lambda comp_id: access.excl_list(owner, comp_id)) if owner else None)


@app.post("/api/analyze")
def analyze(r: Req, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    check_limit(a, 1)
    p = r.model_dump()
    return {"job_id": start_job(a["hash"], "channel", 1, r.url, channel_runner(p, a["hash"]), p, run=p)}


# ---------------------------------------------------------------- поиск ниш по теме
TOPIC_COST = 3


class TopicReq(BaseModel):
    topic: str = Field(..., min_length=2, max_length=80)
    lang: str = "auto"
    same_lang: bool = True
    exclude: Optional[str] = None
    max_age_days: int = Field(730, ge=30, le=3650)
    region: str = "auto"


@app.post("/api/topic")
def topic(r: TopicReq, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    cost = 1 if a.get("source") == "promo" else TOPIC_COST  # в акции любой анализ — одна попытка
    check_limit(a, cost)
    p = r.model_dump()
    fn = lambda prog: topics.run_topic(r.topic, prog, lang=r.lang, same_lang=r.same_lang, exclude=r.exclude,
                                       max_age_days=r.max_age_days, region=r.region)
    return {"job_id": start_job(a["hash"], "topic", cost, "Тема: " + r.topic, fn, p, run=p)}


# ---------------------------------------------------------------- превью: что заходит
THUMBS_COST = 2


class ThumbsReq(BaseModel):
    url: str = Field(..., min_length=3, max_length=300)
    competitors: Optional[str] = Field(None, max_length=1000)
    topic: Optional[str] = Field(None, max_length=120)
    script: Optional[str] = Field(None, max_length=30000)


@app.post("/api/thumbs")
def thumbs_run(r: ThumbsReq, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    cost = 1 if a.get("source") == "promo" else THUMBS_COST
    check_limit(a, cost)
    comps = [u for u in re.split(r"[\s,]+", r.competitors or "") if u.strip()][:3]
    p = {"url": r.url, "competitors": comps, "topic": r.topic, "has_script": bool((r.script or "").strip())}
    try:
        import thumbs  # OpenCV подгружаем только здесь: без него остальной сервис продолжает работать
    except Exception as e:
        raise HTTPException(503, "Разбор превью ещё не установлен на сервере: " + str(e)[:120])
    owner = a["hash"]
    fn = lambda prog: thumbs.run_thumbs(r.url, comps, r.script, r.topic, prog,
                                        remembered=lambda own_id: access.excl_list(owner, "th:" + (own_id or "")))
    run = {"url": r.url, "competitors": comps, "topic": r.topic, "script": r.script}
    return {"job_id": start_job(a["hash"], "thumbs", cost, "Превью: " + r.url, fn, p, run=run)}


# ---------------------------------------------------------------- слежение за нишей
MAX_WATCHES = int(os.environ.get("MAX_WATCHES", "3"))
WATCH_EVERY_DAYS = float(os.environ.get("WATCH_EVERY_DAYS", "7"))


def load_result(jid):
    if jid in JOBS and JOBS[jid].get("result"):
        return JOBS[jid]["result"]
    f = RESULTS / f"{jid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


class WatchReq(BaseModel):
    job_id: str


@app.post("/api/watch")
def watch_add(b: WatchReq, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    res = load_result(b.job_id) if re.fullmatch(r"[0-9a-f]{8,32}", b.job_id) else None
    if not res or res.get("type") in ("topic", "thumbs"):
        raise HTTPException(404, "Следить можно за готовым отчётом по каналу")
    if a["role"] != "admin" and len(access.list_watches(a["hash"])) >= MAX_WATCHES:
        raise HTTPException(429, f"Можно следить максимум за {MAX_WATCHES} нишами. Уберите одну из вкладки «Слежение».")
    p = res.get("params") or {"url": res["competitor"]["url"]}
    if not p.get("url"):
        p["url"] = res["competitor"]["url"]
    wid = access.add_watch(a["hash"], p["url"], json.dumps(p, ensure_ascii=False), res["competitor"]["title"],
                           b.job_id, res["niche"]["overall"], WATCH_EVERY_DAYS, extra=watch_point(res))
    return {"watch_id": wid}


@app.get("/api/watches")
def watches(x_key: Optional[str] = Header(None)):
    a = need(x_key, activate=False)
    rows = access.list_watches(None if a["role"] == "admin" else a["hash"])
    for w in rows:
        w["running"] = bool(w.get("running_job") and JOBS.get(w["running_job"], {}).get("status") == "running")
    return {"watches": rows, "limit": None if a["role"] == "admin" else MAX_WATCHES}


def _watch_owner_ok(a, wid):
    w = access.get_watch(wid)
    if not w or (a["role"] != "admin" and w["code_hash"] != a["hash"]):
        raise HTTPException(404, "Ниша не найдена")
    return w


@app.delete("/api/watch/{wid}")
def watch_del(wid: str, x_key: Optional[str] = Header(None)):
    a = need(x_key, activate=False)
    _watch_owner_ok(a, wid)
    access.delete_watch(wid)
    return {"ok": True}


@app.post("/api/watch/{wid}/run")
def watch_run(wid: str, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    w = _watch_owner_ok(a, wid)
    if w.get("running_job") and JOBS.get(w["running_job"], {}).get("status") == "running":
        raise HTTPException(409, "Пересчёт уже идёт")
    if a["role"] != "admin" and w.get("last_run") and time.time() - w["last_run"] < 6 * 3600:
        raise HTTPException(429, "Пересчитать вручную можно раз в 6 часов. Автоматически ниша обновляется раз в неделю.")
    return {"job_id": run_watch(w)}


def watch_point(res):
    """Ключевые цифры ниши для графика динамики."""
    n = res.get("niche") or {}
    sc = n.get("scores") or {}
    return {"demand": sc.get("demand"), "window": sc.get("entry_window"), "competition": sc.get("competition"),
            "views": n.get("median_search_views"), "yr": n.get("young_rising"), "rel": n.get("relevant"),
            "range": (res.get("verification") or {}).get("range")}


def run_watch(w):
    p = json.loads(w["params"])
    access.watch_started(w["id"])

    def done(jid, res):
        if res is None:
            access.watch_finished(w["id"], None, None, None, WATCH_EVERY_DAYS)
            return
        prev = load_result(w["last_job"]) if w.get("last_job") else None
        ch = watchdiff.diff(prev, res)
        access.watch_finished(w["id"], jid, res["niche"]["overall"], json.dumps(ch, ensure_ascii=False), WATCH_EVERY_DAYS,
                              watch_point(res))

    jid = start_job(w["code_hash"], "watch", 0, p["url"], channel_runner(p, w["code_hash"]), p, on_done=done)
    access.watch_set_running(w["id"], jid)
    return jid


def watch_scheduler():
    """Раз в 15 минут ищет ниши, которые пора пересчитать, и пересчитывает по одной."""
    time.sleep(60)
    while True:
        try:
            for w in access.due_watches():
                if not access.hash_valid(w["code_hash"]):
                    continue
                jid = run_watch(w)
                while JOBS.get(jid, {}).get("status") == "running":
                    time.sleep(20)
        except Exception:
            pass
        time.sleep(900)


if os.environ.get("WATCH_SCHEDULER", "1") == "1":
    threading.Thread(target=watch_scheduler, daemon=True).start()


@app.get("/api/job/{jid}")
def job(jid: str, x_key: Optional[str] = Header(None)):
    need(x_key, activate=False)
    if not re.fullmatch(r"[0-9a-f]{8,32}", jid):
        raise HTTPException(404, "Отчёт не найден")
    if jid in JOBS:
        return {k: v for k, v in JOBS[jid].items() if k != "owner"}
    f = RESULTS / f"{jid}.json"
    if f.exists():
        return {"status": "done", "progress": 100, "message": "Готово",
                "result": json.loads(f.read_text(encoding="utf-8")), "error": None}
    row = access.job_row(jid)
    if row and row["status"] == "error":
        return {"status": "error", "progress": 0, "message": "", "result": None,
                "error": row.get("error") or "Анализ не удался. Попробуйте ещё раз."}
    if row and row["status"] == "running":
        return {"status": "running", "progress": 2, "message": "Сервер обновлялся — сейчас продолжу анализ…", "result": None, "error": None}
    raise HTTPException(404, "Отчёт не найден")


@app.get("/api/job/{jid}/export")
def job_export(jid: str, x_key: Optional[str] = Header(None)):
    """Весь отчёт одним файлом Markdown — чтобы загрузить в чат с нейросетью."""
    need(x_key, activate=False)
    if not re.fullmatch(r"[0-9a-f]{8,32}", jid):
        raise HTTPException(404, "Отчёт не найден")
    res = load_result(jid)
    if not res:
        raise HTTPException(404, "Отчёт не найден или ещё не готов")
    import export
    from urllib.parse import quote
    md = export.build(res)
    fn = export.filename(res)
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=\"report.md\"; filename*=UTF-8''{quote(fn)}"})


class Exclude(BaseModel):
    channel_id: str
    excluded: bool = True


@app.post("/api/job/{jid}/exclude")
def exclude_channel(jid: str, b: Exclude, x_key: Optional[str] = Header(None)):
    """«Не та ниша»: канал убирается из отчёта, оценки пересчитываются в браузере. Сохраняется в отчёте."""
    a = need(x_key, activate=False)
    if not re.fullmatch(r"[0-9a-f]{8,32}", jid):
        raise HTTPException(404, "Отчёт не найден")
    if a["role"] != "admin" and access.job_owner(jid) != a["hash"]:
        raise HTTPException(403, "Исключать каналы может только автор отчёта")
    f = RESULTS / f"{jid}.json"
    if not f.exists():
        raise HTTPException(404, "Отчёт не найден")
    with EXCL_LOCK:
        res = json.loads(f.read_text(encoding="utf-8"))
        ex = set(res.get("excluded") or [])
        (ex.add if b.excluded else ex.discard)(b.channel_id)
        res["excluded"] = sorted(ex)
        f.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    if jid in JOBS and JOBS[jid].get("result"):
        JOBS[jid]["result"]["excluded"] = res["excluded"]
    # запоминаем для этой ниши: при следующем анализе того же конкурента канал не появится
    owner = access.job_owner(jid) or a["hash"]
    title = next((c.get("title") for c in res.get("channels") or [] if c.get("id") == b.channel_id), "")
    access.excl_set(owner, (res.get("competitor") or {}).get("id"), b.channel_id, title, b.excluded)
    return {"excluded": res["excluded"]}


# ---------------------------------------------------------------- соседние ниши (фоном, после отчёта)
NEIGH = {}


def _load_result(jid, a):
    if not re.fullmatch(r"[0-9a-f]{8,32}", jid):
        raise HTTPException(404, "Отчёт не найден")
    if a["role"] != "admin" and access.job_owner(jid) != a["hash"]:
        raise HTTPException(403, "Это не ваш отчёт")
    f = RESULTS / f"{jid}.json"
    if not f.exists():
        raise HTTPException(404, "Отчёт не найден")
    return f, json.loads(f.read_text(encoding="utf-8"))


@app.get("/api/job/{jid}/neighbors")
def neighbors_get(jid: str, x_key: Optional[str] = Header(None)):
    a = need(x_key, activate=False)
    f, res = _load_result(jid, a)
    if res.get("neighbors"):
        return {"status": "done", **res["neighbors"]}
    st = NEIGH.get(jid)
    if not st:
        return {"status": "none", "available": bool(res.get("neighbor_candidates"))}
    return {k: v for k, v in st.items() if k != "thread"}


@app.post("/api/job/{jid}/neighbors")
def neighbors_start(jid: str, x_key: Optional[str] = Header(None)):
    """Оценить 2–3 соседние ниши. Бесплатно: это продолжение уже оплаченного отчёта."""
    a = need(x_key, activate=False)
    f, res = _load_result(jid, a)
    if res.get("neighbors"):
        return {"status": "done", **res["neighbors"]}
    if not res.get("neighbor_candidates"):
        return {"status": "done", "rows": []}
    st = NEIGH.get(jid)
    if st and st["status"] == "running":
        return {k: v for k, v in st.items() if k != "thread"}
    st = NEIGH[jid] = {"status": "running", "progress": 0, "message": "Запускаю…"}

    def prog(p, m):
        st["progress"], st["message"] = round(p), m

    def run():
        import neighbors
        try:
            out = neighbors.run_neighbors(res, prog)
            with EXCL_LOCK:
                cur = json.loads(f.read_text(encoding="utf-8"))
                cur["neighbors"] = out
                f.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
            if jid in JOBS and JOBS[jid].get("result"):
                JOBS[jid]["result"]["neighbors"] = out
            st.update(status="done", progress=100, **out)
        except Exception as e:
            st.update(status="error", error=str(e)[:300])
    threading.Thread(target=run, daemon=True).start()
    return {"status": "running", "progress": 0}


class ThExclude(BaseModel):
    channel_id: str
    title: str = ""


@app.post("/api/job/{jid}/thumbs-exclude")
def thumbs_exclude(jid: str, b: ThExclude, x_key: Optional[str] = Header(None)):
    """Превью: «не тот конкурент» — при следующих разборах вашего канала он не будет подобран."""
    a = need(x_key, activate=False)
    f, res = _load_result(jid, a)
    own = (res.get("channel") or {}).get("id")
    owner = access.job_owner(jid) or a["hash"]
    access.excl_set(owner, "th:" + (own or ""), b.channel_id, b.title, True)
    return {"ok": True}


# ---------------------------------------------------------------- акция: бесплатный доступ всем на N часов
PROMO_LOCK = threading.Lock()


def promo_now():
    p = access.kv_get("promo")
    now = time.time()
    if p and p.get("started_at", 0) <= now < p.get("ends_at", 0) and not p.get("stopped"):
        return p
    return None


def promo_upcoming():
    p = access.kv_get("promo")
    if p and p.get("started_at", 0) > time.time() and not p.get("stopped"):
        return p
    return None


@app.get("/api/promo")
def promo_public():
    p = promo_now()
    if not p:
        up = promo_upcoming()
        return {"active": False, "starts_in": round(up["started_at"] - time.time()) if up else None}
    return {"active": True, "ends_at": p["ends_at"], "attempts": p["attempts"], "hours": p["hours"],
            "left": round(p["ends_at"] - time.time())}


@app.post("/api/promo/claim")
def promo_claim(request: Request):
    """Получить бесплатный ключ акции: один на человека (по IP), действует до конца акции."""
    p = promo_now()
    if not p:
        raise HTTPException(410, "Акция закончилась. Доступ можно купить — от одного дня.")
    ip = request.client.host if request.client else "?"
    iph = hashlib.sha256((p["id"] + ip).encode()).hexdigest()[:20]
    with PROMO_LOCK:
        took = access.kv_get("promo_ips:" + p["id"], [])
        if iph in took:
            raise HTTPException(429, "С этого устройства бесплатный доступ уже получен. Если ключ потерялся — купите доступ или напишите в поддержку.")
        key = access.create_promo_key(p["ends_at"], p["attempts"], p["id"])
        took.append(iph)
        access.kv_set("promo_ips:" + p["id"], took)
        p["claims"] = p.get("claims", 0) + 1
        access.kv_set("promo", p)
    return {"key": key, "ends_at": p["ends_at"], "attempts": p["attempts"]}


class PromoReq(BaseModel):
    hours: float = Field(3, ge=0.25, le=72)
    attempts: int = Field(5, ge=1, le=50)
    start_msk: Optional[str] = None   # «2026-11-08T20:00» по Москве; пусто — начать сейчас
    broadcast: bool = True            # разослать подписчикам бота, когда акция начнётся


@app.get("/api/admin/promo")
def promo_admin_get(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    p = access.kv_get("promo")
    return {"promo": p, "active": bool(promo_now()), "upcoming": bool(promo_upcoming()), "subscribers": len(access.tg_users()),
            "stats": access.promo_stats(p["id"]) if p else None}


@app.post("/api/admin/promo")
def promo_admin_start(b: PromoReq, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    now = time.time()
    start = now
    if b.start_msk:
        import datetime as _dt
        from zoneinfo import ZoneInfo
        try:
            start = _dt.datetime.fromisoformat(b.start_msk).replace(tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
        except ValueError:
            raise HTTPException(400, "Не понял дату и время начала")
        if start < now - 60:
            raise HTTPException(400, "Это время по Москве уже прошло — выберите время позже или запустите сейчас")
        start = max(start, now)
    p = {"id": secrets.token_hex(3), "started_at": start, "ends_at": start + b.hours * 3600, "hours": b.hours,
         "attempts": b.attempts, "claims": 0, "scheduled": start > now + 5, "broadcast": b.broadcast}
    access.kv_set("promo", p)
    return {"promo": p, "active": start <= now + 5}


@app.delete("/api/admin/promo")
def promo_admin_stop(revoke: bool = False, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    p = access.kv_get("promo")
    if not p:
        return {"active": False}
    p["stopped"] = True
    p["stopped_at"] = time.time()
    access.kv_set("promo", p)
    n = access.revoke_promo(p["id"]) if revoke else 0
    return {"active": False, "revoked": n}


# ---------------------------------------------------------------- уведомления в Telegram
@app.post("/api/tg/link")
def tg_link(x_key: Optional[str] = Header(None)):
    """Ссылка на бота, которая привяжет уведомления к этому ключу (действует 30 минут)."""
    a = need(x_key, activate=False)
    import bot
    name = bot.cfg()["bot_username"] or bot.status().get("bot_username")
    if not name:
        raise HTTPException(503, "Бот уведомлений ещё не подключён")
    tok = secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")
    access.kv_set("tglink:" + tok, {"hash": a["hash"], "until": time.time() + 1800})
    return {"url": f"https://t.me/{name}?start=k_{tok}"}


@app.delete("/api/tg/link")
def tg_unlink(x_key: Optional[str] = Header(None)):
    a = need(x_key, activate=False)
    access.tg_unlink(a["hash"])
    return {"ok": True}


def _expiry_watch():
    """За сутки и за час до конца доступа — напоминание в Telegram (по одному разу)."""
    import bot
    while True:
        try:
            for within, tag, txt in ((86400, "24h", "⏳ Доступ к Niche Finder закончится через сутки."),
                                     (3600, "1h", "⏰ Доступ к Niche Finder закончится через час.")):
                for k in access.expiring_keys(within):
                    flag = f"expnote:{tag}:{k['hash'][:16]}"
                    if access.kv_get(flag):
                        continue
                    access.kv_set(flag, 1)
                    site = bot.site_url()
                    bot.notify_key(k["hash"], txt + " Продлить можно на сайте — выберите тариф.",
                                   ("Продлить доступ", f"{site}/#pricing") if site.startswith("https://") else None)
            _promo_announce()
        except Exception:
            pass
        time.sleep(60)


def _promo_announce():
    """Акция началась (сразу или по расписанию) — один раз рассказать подписчикам бота, если админ так выбрал."""
    p = promo_now()
    if not p or not p.get("broadcast") or p.get("announced"):
        return
    p["announced"] = time.time()
    access.kv_set("promo", p)
    import bot
    site = bot.site_url()
    end = __import__("datetime").datetime.fromtimestamp(p["ends_at"], __import__("zoneinfo").ZoneInfo("Europe/Moscow")).strftime("%H:%M")
    res = bot.broadcast(f"🎁 <b>Niche Finder бесплатно для всех — до {end} по Москве!</b>\n\n"
                        f"{p['attempts']} бесплатных анализов каждому: ниша, идеи роликов, разбор превью. Одна кнопка, без оплаты.\n\n"
                        "Отписаться от таких сообщений: /stop", ("Получить бесплатный доступ", site) if site.startswith("https://") else None)
    p["announced_to"] = res
    access.kv_set("promo", p)


# ---------------------------------------------------------------- статистика для админа
class Hit(BaseModel):
    src: Optional[str] = Field(None, max_length=40)


@app.post("/api/hit")
def hit(b: Hit, request: Request):
    """Обезличенный счётчик посещений: хэш IP+браузера на этот день. Сам IP не сохраняется."""
    import datetime as _dt
    day = _dt.datetime.utcfromtimestamp(time.time() + 3 * 3600).strftime("%Y-%m-%d")
    ip = request.client.host if request.client else "?"
    ua = request.headers.get("user-agent", "")[:200]
    vid = hashlib.sha256(f"{day}|{ip}|{ua}|{access.kv_get('visit_salt') or ''}".encode()).hexdigest()[:16]
    src = re.sub(r"[^\w\-]", "", (b.src or ""))[:40] or None
    access.visit(day, vid, src)
    return {"ok": True}


@app.get("/api/admin/stats")
def admin_stats(days: int = 14, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    days = max(1, min(90, days))
    rows = access.daily_stats(days)
    tot = {k: sum(r[k] for r in rows) for k in ("visitors", "runs", "purchases", "revenue", "promo")}
    return {"days": rows, "total": tot, "subscribers": len(access.tg_users())}


class Forget(BaseModel):
    comp_id: str
    channel_id: str


@app.post("/api/niche-excl/forget")
def niche_excl_forget(b: Forget, x_key: Optional[str] = Header(None)):
    """Вернуть канал, убранный раньше: в следующих анализах этой ниши он снова будет участвовать."""
    a = need(x_key, activate=False)
    access.excl_set(a["hash"], b.comp_id, b.channel_id, "", False)
    return {"ok": True}


@app.get("/api/history")
def history(x_key: Optional[str] = Header(None)):
    a = need(x_key, activate=False)
    return access.list_jobs(None, 1000) if a["role"] == "admin" else access.list_jobs(a["hash"], 100)


# ---------------------------------------------------------------- админ
class NewCodes(BaseModel):
    count: int = Field(1, ge=1, le=500)
    days: float = Field(1, ge=0, le=3650)  # 0 — бессрочно
    note: str = ""
    daily_limit: Optional[int] = Field(-1, ge=-1, le=10000)  # -1 — по умолчанию, 0/None — без лимита


class CodeAction(BaseModel):
    action: str
    days: Optional[float] = None
    note: Optional[str] = None
    limit: Optional[int] = None


@app.get("/api/admin/codes")
def admin_codes(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    return {"codes": access.list_codes(), "public_url": public_url()}


@app.post("/api/admin/codes")
def admin_create(b: NewCodes, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    lim = "default" if b.daily_limit == -1 else (b.daily_limit or None)
    return {"keys": access.create_codes(b.count, b.days, b.note.strip()[:200], lim)}


@app.post("/api/admin/codes/{cid}")
def admin_update(cid: str, b: CodeAction, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    if b.action not in {"revoke", "restore", "extend", "reset", "note", "delete", "limit", "set_days", "given", "ungiven"}:
        raise HTTPException(400, "Неизвестное действие")
    if not access.update_code(cid, b.action, b.days, b.note, b.limit):
        raise HTTPException(404, "Ключ не найден")
    return {"ok": True}


class PlainKeys(BaseModel):
    keys: list[str] = Field(..., max_length=5000)


@app.post("/api/admin/import-keys")
def admin_import(b: PlainKeys, x_key: Optional[str] = Header(None)):
    """Полные ключи из таблицы администратора — чтобы видеть и раздавать их прямо в панели."""
    need(x_key, admin=True)
    return {"imported": access.import_plain([k for k in b.keys if isinstance(k, str) and len(k) < 64])}


@app.get("/api/admin/codes.csv")
def admin_csv(key: str = ""):
    need(key, admin=True)
    rows = access.list_codes()
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""
    st = {"new": "не активирован", "active": "активен", "expired": "истёк", "revoked": "отозван"}
    lines = ["Ключ;Срок, дней;Статус;Выдан;Активирован;Истекает;Входов;Анализов;Заметка"]
    for r in rows:
        lines.append(";".join(str(x) for x in (r.get("key") or r["hint"], r["days"] or "бессрочно", st[r["status"]], fmt(r.get("given_at")), fmt(r["activated_at"]),
                                               fmt(r["expires_at"]), r["uses"], r["analyses"],
                                               (r["note"] or "").replace(";", ","))))
    return PlainTextResponse("﻿" + "\n".join(lines), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=keys.csv"})


# ---------------------------------------------------------------- тарифы и оплата
import paysettings
import bot
FEATURES = paysettings.FEATURES
load_pay = paysettings.load_pay


@app.get("/api/plans")
def plans():
    d = load_pay()
    return dict(d, features=FEATURES)


class Plan(BaseModel):
    id: str = Field(..., max_length=20)
    name: str = Field(..., max_length=40)
    days: float = Field(..., ge=0, le=3650)
    limit: int = Field(0, ge=0, le=10000)
    price: float = Field(..., ge=0, le=10_000_000)
    link: str = Field("", max_length=300)
    badge: str = Field("", max_length=30)
    tribute_id: str = Field("", max_length=20)


class PaySettings(BaseModel):
    currency: str = Field("₽", max_length=5)
    contact: str = Field("", max_length=60)
    note: str = Field("", max_length=400)
    plans: list[Plan] = Field(..., min_length=1, max_length=8)


@app.post("/api/admin/plans")
def save_plans(b: PaySettings, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    for pl in b.plans:
        if pl.link and not re.match(r"^https://", pl.link):
            raise HTTPException(400, f"Ссылка для тарифа «{pl.name}» должна начинаться с https://")
    paysettings.save_section("pay", b.model_dump())
    return {"ok": True}


# ---------------------------------------------------------------- автовыдача ключей (Tribute + бот)
@app.post("/api/tribute/webhook")
async def tribute_webhook(request: Request):
    body = await request.body()
    code, msg = await __import__("asyncio").to_thread(bot.handle_webhook, body, request.headers.get("trbt-signature", ""))
    if code != 200:
        raise HTTPException(code, msg)
    return {"status": "ok"}


@app.get("/api/admin/auto")
def auto_status(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    st = bot.status()
    st["webhook_url"] = (public_url() or "").rstrip("/") + "/api/tribute/webhook" if public_url() else None
    st["purchases"] = [dict(p, key=(access.code_info(p["code_hash"]) or {}).get("key") if p.get("code_hash") else None)
                       for p in access.purchases(limit=40)]
    return st


class AutoSet(BaseModel):
    api_key: Optional[str] = Field(None, max_length=200)
    bot_token: Optional[str] = Field(None, max_length=200)


@app.post("/api/admin/auto")
def auto_save(b: AutoSet, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    upd = {}
    if b.bot_token is not None:
        tok = b.bot_token.strip()
        if tok:
            if not re.fullmatch(r"\d{5,}:[\w-]{30,}", tok):
                raise HTTPException(400, "Это не похоже на токен бота. Он выглядит так: 123456789:AAH…")
            try:
                me = bot.tg("getMe", tok)
            except Exception as e:
                raise HTTPException(400, f"Telegram не принял токен: {e}")
            upd.update(bot_token=tok, bot_username=me.get("username"))
        else:
            upd.update(bot_token=None, bot_username="")
    if b.api_key is not None:
        upd["api_key"] = b.api_key.strip() or None
    bot.save_cfg(**upd)
    if "bot_token" in upd:
        bot.start()
    return bot.status()


@app.post("/api/admin/auto/link")
def auto_link(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    url = bot.admin_link_url()
    if not url:
        raise HTTPException(400, "Сначала подключите бота")
    return {"url": url}


class AutoTest(BaseModel):
    plan_id: str


@app.post("/api/admin/auto/test")
def auto_test(b: AutoTest, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    c = bot.cfg()
    if not c["admin_chats"]:
        raise HTTPException(400, "Сначала привяжите свой Telegram к боту (шаг 2)")
    pl = next((p for p in load_pay()["plans"] if p["id"] == b.plan_id), None)
    if not pl:
        raise HTTPException(404, "Тариф не найден")
    payload = {"purchase_id": "test-" + uuid.uuid4().hex[:10], "product_id": pl.get("tribute_id") or "test",
               "product_name": pl["name"], "amount": int(round(float(pl["price"]) * 100)), "currency": "rub",
               "telegram_user_id": c["admin_chats"][0], "telegram_username": "", "trb_user_id": "test"}
    saved = pl.get("tribute_id")
    # тест должен найти тариф даже без ID товара
    if not saved:
        payload["product_id"] = "test-" + pl["id"]
        pay = load_pay()
        for p in pay["plans"]:
            if p["id"] == pl["id"]:
                p["tribute_id"] = payload["product_id"]
        old = bot.paysettings.load_pay
        bot.paysettings.load_pay = lambda: pay
        try:
            r = bot.process_purchase(payload, test=True)
        finally:
            bot.paysettings.load_pay = old
    else:
        r = bot.process_purchase(payload, test=True)
    return {"ok": True, "delivered": bool(r and r.get("delivered_at"))}


@app.get("/api/admin/tribute/products")
def tribute_products(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    try:
        return {"rows": bot.tribute_products()}
    except Exception as e:
        raise HTTPException(400, f"Tribute не ответил: {e}")


def public_url():
    """Публичная ссылка Cloudflare-туннеля, если он запущен установщиком."""
    if os.environ.get("PUBLIC_URL"):
        return os.environ["PUBLIC_URL"]
    log = access.DATA / "tunnel.log"
    if log.exists():
        found = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", log.read_text(errors="ignore"))
        if found:
            return found[-1]
    return None


# ---------------------------------------------------------------- обновления
UPDATE_PATH_UNIT = Path("/etc/systemd/system/niche-update.path")


def _git(*args, timeout=20):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip() if r.returncode == 0 else None


def running_jobs():
    return sum(1 for j in JOBS.values() if j.get("status") == "running") + \
        sum(1 for j in NEIGH.values() if j.get("status") == "running")


@app.get("/api/busy")
def busy():
    """Сколько анализов идёт сейчас — автообновление ждёт, пока их не станет 0."""
    return {"running": running_jobs()}


@app.get("/api/admin/version")
def admin_version(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    supported = UPDATE_PATH_UNIT.exists() and (ROOT / ".git").exists()
    head = _git("log", "-1", "--format=%H|%ct") if (ROOT / ".git").exists() else None
    out = {"supported": supported, "commit": None, "date": None, "update_available": None,
           "pending": (access.DATA / "update-request").exists(), "running": running_jobs()}
    if head:
        out["commit"], ts = head.split("|")
        out["date"] = int(ts)
    if supported:
        try:
            _git("fetch", "-q", "origin", "main", timeout=25)
            remote = _git("rev-parse", "origin/main")
            out["update_available"] = bool(remote and remote != out["commit"])
            if out["update_available"]:
                out["changes"] = (_git("log", "--format=%s", "HEAD..origin/main") or "").splitlines()[:8]
        except Exception:
            pass
    return out


@app.post("/api/admin/update")
def admin_update_now(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    if not UPDATE_PATH_UNIT.exists():
        raise HTTPException(400, "Обновление кнопкой работает только на сервере. Один раз обновите его командой "
                                 "niche-finder update — после этого кнопка заработает.")
    (access.DATA / "update-request").write_text(str(time.time()))
    return {"ok": True, "running": running_jobs()}


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/terms")
def terms():
    return FileResponse(ROOT / "static" / "terms.html", headers={"Cache-Control": "no-cache"})


@app.get("/privacy")
def privacy():
    return FileResponse(ROOT / "static" / "privacy.html", headers={"Cache-Control": "no-cache"})


@app.get("/admin")
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- запуск бота автовыдачи
try:
    bot.SITE = public_url() or ""
    bot.start()
except Exception as _e:
    print("Бот не запущен:", _e)


# анализы, прерванные перезапуском (обновлением) сервера, продолжаются сами
threading.Thread(target=lambda: (time.sleep(3), resume_interrupted()), daemon=True).start()
threading.Thread(target=_expiry_watch, daemon=True).start()
if not access.kv_get("visit_salt"):
    access.kv_set("visit_salt", secrets.token_hex(8))
