"""
Ключи доступа.

- Ключи хранятся ТОЛЬКО в виде хэшей SHA-256: даже если кто-то увидит код или базу, ключ не восстановить.
- Срок ключа начинается с первого входа.
- Админ-ключ — бессрочный, даёт доступ к панели управления ключами.
- Стартовый набор ключей лежит в access/seed_codes.json (только хэши) и загружается в базу при первом запуске.
"""
import os
import json
import time
import hashlib
import secrets
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "app.db"
SEED = ROOT / "access" / "seed_codes.json"
ADMIN_HASH_FILE = ROOT / "access" / "admin.sha256"

UNLIMITED = 0  # days = 0 — бессрочный ключ обычного пользователя
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих O/0, I/1
_lock = threading.Lock()


def normalize(key: str) -> str:
    return "".join(ch for ch in (key or "").upper() if ch.isalnum())


def khash(key: str) -> str:
    return hashlib.sha256(("nf:" + normalize(key)).encode()).hexdigest()


def new_key(prefix="NF") -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return f"{prefix}-" + "-".join(body[i:i + 4] for i in range(0, 16, 4))


def hint(key: str) -> str:
    return "…" + normalize(key)[-4:]


def _exp(start, days):
    """Момент окончания доступа; None — бессрочно."""
    return None if not days else start + days * 86400


def r_days(c, h):
    return c.execute("SELECT days FROM codes WHERE hash=?", (h,)).fetchone()["days"]


def _db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS codes(
            hash TEXT PRIMARY KEY, hint TEXT, days REAL, note TEXT DEFAULT '',
            created_at REAL, activated_at REAL, revoked INTEGER DEFAULT 0,
            uses INTEGER DEFAULT 0, last_used REAL, source TEXT DEFAULT 'admin');
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, code_hash TEXT, url TEXT, created_at REAL,
            status TEXT, overall INTEGER, title TEXT);
        """)
        if SEED.exists():
            for row in json.loads(SEED.read_text(encoding="utf-8")):
                c.execute("INSERT OR IGNORE INTO codes(hash,hint,days,note,created_at,source) VALUES(?,?,?,?,?,?)",
                          (row["hash"], row["hint"], row["days"], row.get("note", ""), time.time(), "seed"))


def _admin_hashes():
    hs = set()
    env = os.environ.get("ADMIN_KEY")
    if env:
        hs.add(khash(env))
    if ADMIN_HASH_FILE.exists():
        hs |= {h.strip() for h in ADMIN_HASH_FILE.read_text().split() if h.strip()}
    return hs


def check(key: str, activate=True, count=False):
    """Вернёт dict с ролью и сроком или None, если ключ не действует."""
    if not key:
        return None
    h = khash(key)
    if h in _admin_hashes():
        return {"role": "admin", "id": h[:12], "hash": h, "expires_at": None, "hint": hint(key)}
    now = time.time()
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM codes WHERE hash=?", (h,)).fetchone()
        if not r or r["revoked"]:
            return None
        act = r["activated_at"]
        if act is None:
            if not activate:
                return {"role": "user", "id": h[:12], "hash": h, "expires_at": _exp(now, r["days"]),
                        "days": r["days"], "hint": r["hint"], "fresh": True}
            act = now
            c.execute("UPDATE codes SET activated_at=? WHERE hash=?", (act, h))
        exp = _exp(act, r["days"])
        if exp is not None and now > exp:
            return None
        if count:
            c.execute("UPDATE codes SET uses=uses+1, last_used=? WHERE hash=?", (now, h))
        else:
            c.execute("UPDATE codes SET last_used=? WHERE hash=?", (now, h))
        return {"role": "user", "id": h[:12], "hash": h, "expires_at": exp, "days": r["days"], "hint": r["hint"]}


def status_of(r, now=None):
    now = now or time.time()
    if r["revoked"]:
        return "revoked"
    if r["activated_at"] is None:
        return "new"
    exp = _exp(r["activated_at"], r["days"])
    return "expired" if exp is not None and now > exp else "active"


def list_codes():
    now = time.time()
    with _lock, _db() as c:
        rows = c.execute("SELECT * FROM codes ORDER BY created_at DESC, days").fetchall()
        jobs = dict(c.execute("SELECT code_hash, COUNT(*) FROM jobs GROUP BY code_hash").fetchall())
    out = []
    for r in rows:
        exp = _exp(r["activated_at"], r["days"]) if r["activated_at"] else None
        out.append({"id": r["hash"][:12], "hint": r["hint"], "days": r["days"], "note": r["note"],
                    "created_at": r["created_at"], "activated_at": r["activated_at"], "expires_at": exp,
                    "status": status_of(r, now), "uses": r["uses"], "last_used": r["last_used"],
                    "analyses": jobs.get(r["hash"], 0), "source": r["source"]})
    return out


def create_codes(count, days, note=""):
    keys = []
    with _lock, _db() as c:
        for _ in range(count):
            k = new_key()
            c.execute("INSERT INTO codes(hash,hint,days,note,created_at,source) VALUES(?,?,?,?,?,?)",
                      (khash(k), hint(k), days, note, time.time(), "admin"))
            keys.append(k)
    return keys


def update_code(cid, action, days=None, note=None):
    with _lock, _db() as c:
        r = c.execute("SELECT hash FROM codes WHERE substr(hash,1,12)=?", (cid,)).fetchone()
        if not r:
            return False
        h = r["hash"]
        if action == "revoke":
            c.execute("UPDATE codes SET revoked=1 WHERE hash=?", (h,))
        elif action == "restore":
            c.execute("UPDATE codes SET revoked=0 WHERE hash=?", (h,))
        elif action == "extend" and days and r_days(c, h) != UNLIMITED:
            c.execute("UPDATE codes SET days=days+? WHERE hash=?", (float(days), h))
        elif action == "reset":  # срок начнётся заново со следующего входа
            c.execute("UPDATE codes SET activated_at=NULL WHERE hash=?", (h,))
        elif action == "note":
            c.execute("UPDATE codes SET note=? WHERE hash=?", (note or "", h))
        elif action == "delete":
            c.execute("DELETE FROM codes WHERE hash=?", (h,))
        return True


def log_job(jid, code_hash, url):
    with _lock, _db() as c:
        c.execute("INSERT INTO jobs(id,code_hash,url,created_at,status) VALUES(?,?,?,?,?)",
                  (jid, code_hash, url, time.time(), "running"))


def finish_job(jid, status, overall=None, title=None):
    with _lock, _db() as c:
        c.execute("UPDATE jobs SET status=?, overall=?, title=? WHERE id=?", (status, overall, title, jid))


def list_jobs(code_hash=None, limit=50):
    with _lock, _db() as c:
        if code_hash:
            rows = c.execute("SELECT * FROM jobs WHERE code_hash=? ORDER BY created_at DESC LIMIT ?", (code_hash, limit))
        else:
            rows = c.execute("""SELECT j.*, c.hint FROM jobs j LEFT JOIN codes c ON c.hash=j.code_hash
                                ORDER BY j.created_at DESC LIMIT ?""", (limit,))
        return [dict(r) | {"code_hash": None} for r in rows.fetchall()]


def job_owner(jid):
    with _lock, _db() as c:
        r = c.execute("SELECT code_hash FROM jobs WHERE id=?", (jid,)).fetchone()
        return r["code_hash"] if r else None


# ---- защита от перебора ключей
_fails = {}


def too_many_fails(ip):
    now = time.time()
    lst = [t for t in _fails.get(ip, []) if now - t < 600]
    _fails[ip] = lst
    return len(lst) >= 10


def add_fail(ip):
    _fails.setdefault(ip, []).append(time.time())
