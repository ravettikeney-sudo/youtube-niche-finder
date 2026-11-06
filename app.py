"""Веб-сервер Niche Finder: вход по ключу, анализ в фоне, админ-панель ключей."""
import os
import re
import json
import time
import uuid
import threading
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, PlainTextResponse
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
            "watch_limit": None if a["role"] == "admin" else MAX_WATCHES}


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


def check_limit(a, cost):
    """Лимит анализов в сутки (скользящие 24 часа). У админа и ключей «без лимита» его нет."""
    lim = a.get("daily_limit")
    if a["role"] == "admin" or not lim:
        return
    used = access.usage(a["hash"])
    if used + cost > lim:
        raise HTTPException(429, f"Лимит анализов на сутки исчерпан: {used} из {lim}"
                                 + (" (поиск по теме считается за 3)" if cost > 1 else "")
                                 + ". Лимит восстанавливается постепенно, через 24 часа после каждого анализа.")


def start_job(owner, kind, cost, label, fn, params=None, on_done=None):
    """Запускает задачу в фоне: анализ канала, поиск по теме или пересчёт отслеживаемой ниши."""
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"status": "running", "progress": 0, "message": "В очереди…", "result": None, "error": None,
                 "owner": owner, "kind": kind}
    access.log_job(jid, owner, label, kind, cost)

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
            except Exception as e:
                JOBS[jid].update(status="error", error=str(e))
                access.finish_job(jid, "error")
                if on_done:
                    on_done(jid, None)

    threading.Thread(target=work, daemon=True).start()
    return jid


def channel_runner(p):
    return lambda prog: run_analysis(p["url"], p.get("keywords"), p.get("max_channels", 30), p.get("per_query", 25),
                                     p.get("max_age_days", 730), prog, same_lang=p.get("same_lang", True),
                                     strict=p.get("strict", True), lang=p.get("lang", "auto"), exclude=p.get("exclude"))


@app.post("/api/analyze")
def analyze(r: Req, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    check_limit(a, 1)
    p = r.model_dump()
    return {"job_id": start_job(a["hash"], "channel", 1, r.url, channel_runner(p), p)}


# ---------------------------------------------------------------- поиск ниш по теме
TOPIC_COST = 3


class TopicReq(BaseModel):
    topic: str = Field(..., min_length=2, max_length=80)
    lang: str = "auto"
    same_lang: bool = True
    exclude: Optional[str] = None
    max_age_days: int = Field(730, ge=30, le=3650)


@app.post("/api/topic")
def topic(r: TopicReq, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    check_limit(a, TOPIC_COST)
    p = r.model_dump()
    fn = lambda prog: topics.run_topic(r.topic, prog, lang=r.lang, same_lang=r.same_lang, exclude=r.exclude,
                                       max_age_days=r.max_age_days)
    return {"job_id": start_job(a["hash"], "topic", TOPIC_COST, "Тема: " + r.topic, fn, p)}


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
    if not res or res.get("type") == "topic":
        raise HTTPException(404, "Следить можно за готовым отчётом по каналу")
    if a["role"] != "admin" and len(access.list_watches(a["hash"])) >= MAX_WATCHES:
        raise HTTPException(429, f"Можно следить максимум за {MAX_WATCHES} нишами. Уберите одну из вкладки «Слежение».")
    p = res.get("params") or {"url": res["competitor"]["url"]}
    if not p.get("url"):
        p["url"] = res["competitor"]["url"]
    wid = access.add_watch(a["hash"], p["url"], json.dumps(p, ensure_ascii=False), res["competitor"]["title"],
                           b.job_id, res["niche"]["overall"], WATCH_EVERY_DAYS)
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


def run_watch(w):
    p = json.loads(w["params"])
    access.watch_started(w["id"])

    def done(jid, res):
        if res is None:
            access.watch_finished(w["id"], None, None, None, WATCH_EVERY_DAYS)
            return
        prev = load_result(w["last_job"]) if w.get("last_job") else None
        ch = watchdiff.diff(prev, res)
        access.watch_finished(w["id"], jid, res["niche"]["overall"], json.dumps(ch, ensure_ascii=False), WATCH_EVERY_DAYS)

    jid = start_job(w["code_hash"], "watch", 0, p["url"], channel_runner(p), p, on_done=done)
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
    raise HTTPException(404, "Отчёт не найден")


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
    return {"excluded": res["excluded"]}


@app.get("/api/history")
def history(x_key: Optional[str] = Header(None)):
    a = need(x_key, activate=False)
    return access.list_jobs(None if a["role"] == "admin" else a["hash"], 100)


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
    if b.action not in {"revoke", "restore", "extend", "reset", "note", "delete", "limit"}:
        raise HTTPException(400, "Неизвестное действие")
    if not access.update_code(cid, b.action, b.days, b.note, b.limit):
        raise HTTPException(404, "Ключ не найден")
    return {"ok": True}


@app.get("/api/admin/codes.csv")
def admin_csv(key: str = ""):
    need(key, admin=True)
    rows = access.list_codes()
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""
    st = {"new": "не активирован", "active": "активен", "expired": "истёк", "revoked": "отозван"}
    lines = ["Ключ (конец);Срок, дней;Статус;Активирован;Истекает;Входов;Анализов;Заметка"]
    for r in rows:
        lines.append(";".join(str(x) for x in (r["hint"], r["days"], st[r["status"]], fmt(r["activated_at"]),
                                               fmt(r["expires_at"]), r["uses"], r["analyses"],
                                               (r["note"] or "").replace(";", ","))))
    return PlainTextResponse("﻿" + "\n".join(lines), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=keys.csv"})


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
    return sum(1 for j in JOBS.values() if j.get("status") == "running")


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


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})
