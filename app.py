"""Веб-сервер Niche Finder: вход по ключу, анализ в фоне, админ-панель ключей."""
import os
import re
import json
import time
import uuid
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import access
from analyzer import run_analysis

ROOT = Path(__file__).parent
RESULTS = access.DATA / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="YouTube Niche Finder", docs_url=None, redoc_url=None)
access.init()
JOBS = {}
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
    return {"role": a["role"], "expires_at": a["expires_at"], "hint": a["hint"]}


@app.get("/api/me")
def me(x_key: Optional[str] = Header(None)):
    a = need(x_key)
    return {"role": a["role"], "expires_at": a["expires_at"], "hint": a["hint"]}


# ---------------------------------------------------------------- анализ
class Req(BaseModel):
    url: str
    keywords: Optional[str] = None
    max_channels: int = Field(30, ge=5, le=80)
    per_query: int = Field(25, ge=10, le=60)
    max_age_days: int = Field(730, ge=30, le=3650)


@app.post("/api/analyze")
def analyze(r: Req, x_key: Optional[str] = Header(None)):
    a = need(x_key)
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"status": "running", "progress": 0, "message": "В очереди…", "result": None, "error": None,
                 "owner": a["hash"]}
    access.log_job(jid, a["hash"], r.url)

    def prog(p, m):
        JOBS[jid].update(progress=round(p), message=m)

    def work():
        with SEM:
            try:
                res = run_analysis(r.url, r.keywords, r.max_channels, r.per_query, r.max_age_days, prog)
                (RESULTS / f"{jid}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                JOBS[jid].update(status="done", progress=100, result=res)
                access.finish_job(jid, "done", res["niche"]["overall"], res["competitor"]["title"])
            except Exception as e:
                JOBS[jid].update(status="error", error=str(e))
                access.finish_job(jid, "error")

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": jid}


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


@app.get("/api/history")
def history(x_key: Optional[str] = Header(None)):
    a = need(x_key, activate=False)
    return access.list_jobs(None if a["role"] == "admin" else a["hash"], 100)


# ---------------------------------------------------------------- админ
class NewCodes(BaseModel):
    count: int = Field(1, ge=1, le=500)
    days: float = Field(1, gt=0, le=3650)
    note: str = ""


class CodeAction(BaseModel):
    action: str
    days: Optional[float] = None
    note: Optional[str] = None


@app.get("/api/admin/codes")
def admin_codes(x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    return {"codes": access.list_codes(), "public_url": public_url()}


@app.post("/api/admin/codes")
def admin_create(b: NewCodes, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    return {"keys": access.create_codes(b.count, b.days, b.note.strip()[:200])}


@app.post("/api/admin/codes/{cid}")
def admin_update(cid: str, b: CodeAction, x_key: Optional[str] = Header(None)):
    need(x_key, admin=True)
    if b.action not in {"revoke", "restore", "extend", "reset", "note", "delete"}:
        raise HTTPException(400, "Неизвестное действие")
    if not access.update_code(cid, b.action, b.days, b.note):
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


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})
