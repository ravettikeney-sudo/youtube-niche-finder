"""
Ключи доступа.

- Проверка ключа идёт по хэшу SHA-256. Чтобы администратор видел и раздавал ключи целиком, сами ключи
  дополнительно хранятся на сервере в зашифрованном виде (data/app.db + секрет data/secret.key).
  В репозиторий попадают только хэши.
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
DEFAULT_LIMIT = int(os.environ.get("DEFAULT_DAILY_LIMIT", "10"))  # анализов в сутки для ключей со сроком
DAY = 86400


def default_limit(days):
    """Ключам со сроком — лимит по умолчанию, бессрочным — без лимита."""
    return DEFAULT_LIMIT if days else None
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


# ---- шифрование ключей для показа администратору
SECRET_FILE = DATA / "secret.key"
_fernet = None


def _f():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        if not SECRET_FILE.exists():
            SECRET_FILE.write_bytes(Fernet.generate_key())
            try:
                os.chmod(SECRET_FILE, 0o600)
            except OSError:
                pass
        _fernet = Fernet(SECRET_FILE.read_bytes().strip())
    return _fernet


def enc(key):
    try:
        return _f().encrypt(key.encode()).decode()
    except Exception:
        return None


def dec(token):
    if not token:
        return None
    try:
        return _f().decrypt(token.encode()).decode()
    except Exception:
        return None


def _exp(start, days):
    """Момент окончания доступа; None — бессрочно."""
    return None if not days else start + days * 86400


def _used(c, h):
    """Сколько анализов (с учётом «веса») ключ сделал за последние 24 часа. Ошибки не считаются."""
    r = c.execute("""SELECT COALESCE(SUM(COALESCE(cost,1)),0) FROM jobs WHERE code_hash=? AND created_at>?
                     AND status!='error' AND COALESCE(kind,'channel')!='watch'""", (h, time.time() - DAY)).fetchone()
    return int(r[0] or 0)


def usage(code_hash):
    with _lock, _db() as c:
        return _used(c, code_hash)


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
        # миграции: лимит анализов в сутки, вид и «вес» анализа
        cols = {r[1] for r in c.execute("PRAGMA table_info(codes)")}
        if "daily_limit" not in cols:
            c.execute("ALTER TABLE codes ADD COLUMN daily_limit INTEGER")
            c.execute("UPDATE codes SET daily_limit=? WHERE days>0", (DEFAULT_LIMIT,))
        if "secret" not in cols:
            c.execute("ALTER TABLE codes ADD COLUMN secret TEXT")
        if "given_at" not in cols:
            c.execute("ALTER TABLE codes ADD COLUMN given_at REAL")
        c.execute("""CREATE TABLE IF NOT EXISTS purchases(
            purchase_id TEXT PRIMARY KEY, created_at REAL, product_id TEXT, product_name TEXT, plan_id TEXT,
            amount REAL, currency TEXT, tg_id INTEGER, tg_username TEXT, trb_user_id TEXT,
            code_hash TEXT, delivered_at REAL, status TEXT, info TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)""")
        # «не та ниша»: каналы, которые пользователь убрал из ниши конкурента, — не показываем их снова
        c.execute("""CREATE TABLE IF NOT EXISTS niche_excl(code_hash TEXT, comp_id TEXT, chan_id TEXT, title TEXT,
            created_at REAL, PRIMARY KEY(code_hash, comp_id, chan_id))""")
        jcols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        if "kind" not in jcols:
            c.execute("ALTER TABLE jobs ADD COLUMN kind TEXT DEFAULT 'channel'")
        if "cost" not in jcols:
            c.execute("ALTER TABLE jobs ADD COLUMN cost INTEGER DEFAULT 1")
        c.execute("""CREATE TABLE IF NOT EXISTS watches(
            id TEXT PRIMARY KEY, code_hash TEXT, url TEXT, params TEXT, title TEXT,
            created_at REAL, every_days REAL DEFAULT 7, next_at REAL, last_job TEXT, prev_job TEXT,
            last_overall INTEGER, prev_overall INTEGER, changes TEXT, active INTEGER DEFAULT 1,
            running_job TEXT, last_run REAL, history TEXT DEFAULT '[]')""")
        c.execute("UPDATE watches SET running_job=NULL")  # после перезапуска сервиса незавершённых пересчётов нет
        # политика конфиденциальности: данные покупателя храним не дольше года
        c.execute("UPDATE purchases SET tg_id=NULL, tg_username='', trb_user_id='' WHERE created_at < ?", (time.time() - 365 * 86400,))
        if SEED.exists():
            for row in json.loads(SEED.read_text(encoding="utf-8")):
                c.execute("INSERT OR IGNORE INTO codes(hash,hint,days,note,created_at,source,daily_limit) VALUES(?,?,?,?,?,?,?)",
                          (row["hash"], row["hint"], row["days"], row.get("note", ""), time.time(), "seed",
                           default_limit(row["days"])))


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
                        "days": r["days"], "hint": r["hint"], "fresh": True,
                        "daily_limit": r["daily_limit"], "used_24h": _used(c, h), "source": r["source"]}
            act = now
            c.execute("UPDATE codes SET activated_at=? WHERE hash=?", (act, h))
        exp = _exp(act, r["days"])
        if exp is not None and now > exp:
            return None
        if count:
            c.execute("UPDATE codes SET uses=uses+1, last_used=? WHERE hash=?", (now, h))
        else:
            c.execute("UPDATE codes SET last_used=? WHERE hash=?", (now, h))
        return {"role": "user", "id": h[:12], "hash": h, "expires_at": exp, "days": r["days"], "hint": r["hint"],
                "daily_limit": r["daily_limit"], "used_24h": _used(c, h), "source": r["source"]}


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
        rows = c.execute("SELECT * FROM codes WHERE COALESCE(source,'')!='promo' ORDER BY created_at DESC, days").fetchall()
        jobs = dict(c.execute("SELECT code_hash, COUNT(*) FROM jobs GROUP BY code_hash").fetchall())
        used = dict(c.execute("""SELECT code_hash, SUM(COALESCE(cost,1)) FROM jobs WHERE created_at>? AND status!='error'
                                 AND COALESCE(kind,'channel')!='watch' GROUP BY code_hash""", (now - DAY,)).fetchall())
    out = []
    for r in rows:
        exp = _exp(r["activated_at"], r["days"]) if r["activated_at"] else None
        out.append({"id": r["hash"][:12], "hint": r["hint"], "days": r["days"], "note": r["note"],
                    "created_at": r["created_at"], "activated_at": r["activated_at"], "expires_at": exp,
                    "status": status_of(r, now), "uses": r["uses"], "last_used": r["last_used"],
                    "analyses": jobs.get(r["hash"], 0), "source": r["source"],
                    "daily_limit": r["daily_limit"], "used_24h": used.get(r["hash"], 0),
                    "key": dec(r["secret"]), "given_at": r["given_at"]})
    return out


def create_codes(count, days, note="", daily_limit="default"):
    if daily_limit == "default":
        daily_limit = default_limit(days)
    keys = []
    with _lock, _db() as c:
        for _ in range(count):
            k = new_key()
            c.execute("INSERT INTO codes(hash,hint,days,note,created_at,source,daily_limit,secret) VALUES(?,?,?,?,?,?,?,?)",
                      (khash(k), hint(k), days, note, time.time(), "admin", daily_limit, enc(k)))
            keys.append(k)
    return keys


def update_code(cid, action, days=None, note=None, limit=None):
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
            c.execute("UPDATE codes SET days=MAX(0.04, days+?) WHERE hash=?", (float(days), h))
        elif action == "set_days":  # задать срок целиком; 0 — бессрочно
            c.execute("UPDATE codes SET days=? WHERE hash=?", (max(0.0, float(days or 0)), h))
        elif action == "given":     # отметка «ключ выдан» (скопирован для человека)
            c.execute("UPDATE codes SET given_at=COALESCE(given_at, ?) WHERE hash=?", (time.time(), h))
        elif action == "ungiven":
            c.execute("UPDATE codes SET given_at=NULL WHERE hash=?", (h,))
        elif action == "reset":  # срок начнётся заново со следующего входа
            c.execute("UPDATE codes SET activated_at=NULL WHERE hash=?", (h,))
        elif action == "limit":  # limit=None или 0 — без лимита
            c.execute("UPDATE codes SET daily_limit=? WHERE hash=?", (int(limit) if limit else None, h))
        elif action == "note":
            c.execute("UPDATE codes SET note=? WHERE hash=?", (note or "", h))
        elif action == "delete":
            c.execute("DELETE FROM codes WHERE hash=?", (h,))
        return True


def import_plain(keys):
    """Загрузить полные ключи для показа в панели. Принимаются только ключи, чей хэш уже есть в базе."""
    n = 0
    with _lock, _db() as c:
        for k in keys:
            h = khash(k)
            if c.execute("SELECT 1 FROM codes WHERE hash=?", (h,)).fetchone():
                c.execute("UPDATE codes SET secret=? WHERE hash=?", (enc(k.strip().upper()), h))
                n += 1
    return n


# ---- покупки через Tribute и выдача ключей ботом
def create_key_for(days, limit, note):
    """Один новый ключ; возвращает (ключ, хэш)."""
    k = create_codes(1, days, note, limit if limit else None)[0]
    with _lock, _db() as c:
        c.execute("UPDATE codes SET given_at=? WHERE hash=?", (time.time(), khash(k)))
    return k, khash(k)


def purchase_get(pid):
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM purchases WHERE purchase_id=?", (str(pid),)).fetchone()
    return dict(r) if r else None


def purchase_add(**f):
    f.setdefault("created_at", time.time())
    cols = ",".join(f)
    with _lock, _db() as c:
        c.execute(f"INSERT OR IGNORE INTO purchases({cols}) VALUES({','.join('?' * len(f))})", tuple(f.values()))


def purchase_set(pid, **f):
    with _lock, _db() as c:
        c.execute(f"UPDATE purchases SET {','.join(k + '=?' for k in f)} WHERE purchase_id=?", (*f.values(), str(pid)))


def purchases(tg_id=None, pending=False, limit=50):
    q, a = "SELECT * FROM purchases", []
    w = []
    if tg_id is not None:
        w.append("tg_id=?"); a.append(int(tg_id))
    if pending:
        w.append("delivered_at IS NULL AND code_hash IS NOT NULL AND status='ok'")
    if w:
        q += " WHERE " + " AND ".join(w)
    q += " ORDER BY created_at DESC LIMIT ?"; a.append(limit)
    with _lock, _db() as c:
        return [dict(r) for r in c.execute(q, a).fetchall()]


def code_info(h):
    """Ключ целиком и его состояние — для сообщений бота."""
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM codes WHERE hash=?", (h,)).fetchone()
    if not r:
        return None
    exp = _exp(r["activated_at"], r["days"]) if r["activated_at"] else None
    return {"key": dec(r["secret"]), "days": r["days"], "limit": r["daily_limit"], "status": status_of(r),
            "activated_at": r["activated_at"], "expires_at": exp, "hash": h}


def revoke_hash(h):
    with _lock, _db() as c:
        c.execute("UPDATE codes SET revoked=1 WHERE hash=?", (h,))


def create_promo_key(ends_at, attempts, promo_id):
    """Ключ акции: включается сразу и действует ровно до конца акции; попыток — attempts."""
    now = time.time()
    days = max(1, ends_at - now) / DAY  # ровно до конца акции, кто бы когда ни пришёл
    k = new_key()
    with _lock, _db() as c:
        c.execute("""INSERT INTO codes(hash,hint,days,note,created_at,source,daily_limit,secret,activated_at,given_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (khash(k), hint(k), days, f"Акция {promo_id}", now, "promo", attempts, enc(k), now, now))
    return k


def promo_stats(promo_id):
    """Сколько ключей акции взято, сколько анализов по ним сделано."""
    with _lock, _db() as c:
        r = c.execute("""SELECT COUNT(*) AS keys, COALESCE(SUM((SELECT COUNT(*) FROM jobs j WHERE j.code_hash=codes.hash
                         AND j.status!='error')),0) AS runs, COALESCE(SUM(CASE WHEN (SELECT COUNT(*) FROM jobs j WHERE
                         j.code_hash=codes.hash)>0 THEN 1 ELSE 0 END),0) AS users
                         FROM codes WHERE source='promo' AND note=?""", (f"Акция {promo_id}",)).fetchone()
    return {"keys": r["keys"], "runs": r["runs"], "users": r["users"]}


def revoke_promo(promo_id):
    with _lock, _db() as c:
        return c.execute("UPDATE codes SET revoked=1 WHERE source='promo' AND note=? AND revoked=0", (f"Акция {promo_id}",)).rowcount


def kv_get(k, default=None):
    with _lock, _db() as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def excl_set(code_hash, comp_id, chan_id, title, on=True):
    if not (code_hash and comp_id and chan_id):
        return
    with _lock, _db() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO niche_excl VALUES(?,?,?,?,?)", (code_hash, comp_id, chan_id, title or "", time.time()))
        else:
            c.execute("DELETE FROM niche_excl WHERE code_hash=? AND comp_id=? AND chan_id=?", (code_hash, comp_id, chan_id))


def excl_list(code_hash, comp_id):
    if not (code_hash and comp_id):
        return []
    with _lock, _db() as c:
        return [{"id": r[0], "title": r[1]} for r in
                c.execute("SELECT chan_id, title FROM niche_excl WHERE code_hash=? AND comp_id=? ORDER BY created_at", (code_hash, comp_id))]


def kv_set(k, v):
    with _lock, _db() as c:
        c.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))


def log_job(jid, code_hash, url, kind="channel", cost=1):
    with _lock, _db() as c:
        c.execute("INSERT INTO jobs(id,code_hash,url,created_at,status,kind,cost) VALUES(?,?,?,?,?,?,?)",
                  (jid, code_hash, url, time.time(), "running", kind, cost))


def finish_job(jid, status, overall=None, title=None):
    with _lock, _db() as c:
        c.execute("UPDATE jobs SET status=?, overall=?, title=? WHERE id=?", (status, overall, title, jid))


def list_jobs(code_hash=None, limit=50):
    with _lock, _db() as c:
        if code_hash:
            rows = c.execute("SELECT * FROM jobs WHERE code_hash=? ORDER BY created_at DESC LIMIT ?", (code_hash, limit))
        else:
            rows = c.execute("""SELECT j.*, c.hint, c.note, substr(c.hash,1,12) AS code_id FROM jobs j
                                LEFT JOIN codes c ON c.hash=j.code_hash
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


# ---------------------------------------------------------------- слежение за нишами
def add_watch(code_hash, url, params, title, job_id, overall, every_days, extra=None):
    wid = secrets.token_hex(6)
    now = time.time()
    hist = json.dumps([dict({"t": now, "overall": overall, "job": job_id}, **(extra or {}))])
    with _lock, _db() as c:
        c.execute("""INSERT INTO watches(id,code_hash,url,params,title,created_at,every_days,next_at,last_job,
                     last_overall,last_run,history) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (wid, code_hash, url, params, title, now, every_days, now + every_days * DAY, job_id, overall, now, hist))
    return wid


def _watch_row(r):
    d = dict(r)
    d["changes"] = json.loads(d["changes"]) if d.get("changes") else None
    d["history"] = json.loads(d.get("history") or "[]")
    return d


def list_watches(code_hash=None):
    with _lock, _db() as c:
        if code_hash:
            rows = c.execute("SELECT * FROM watches WHERE code_hash=? AND active=1 ORDER BY created_at DESC", (code_hash,))
        else:
            rows = c.execute("""SELECT w.*, c.hint FROM watches w LEFT JOIN codes c ON c.hash=w.code_hash
                                WHERE w.active=1 ORDER BY w.created_at DESC""")
        out = []
        for r in rows.fetchall():
            d = _watch_row(r)
            d.pop("params", None)
            d.pop("code_hash", None)
            out.append(d)
        return out


def get_watch(wid):
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM watches WHERE id=? AND active=1", (wid,)).fetchone()
        return dict(r) if r else None


def delete_watch(wid):
    with _lock, _db() as c:
        c.execute("UPDATE watches SET active=0 WHERE id=?", (wid,))


def watch_started(wid):
    with _lock, _db() as c:
        c.execute("UPDATE watches SET last_run=? WHERE id=?", (time.time(), wid))


def watch_set_running(wid, jid):
    with _lock, _db() as c:
        c.execute("UPDATE watches SET running_job=? WHERE id=?", (jid, wid))


def watch_finished(wid, jid, overall, changes, every_days, extra=None):
    now = time.time()
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM watches WHERE id=?", (wid,)).fetchone()
        if not r:
            return
        if jid is None:  # ошибка — попробуем снова через 6 часов
            c.execute("UPDATE watches SET running_job=NULL, next_at=? WHERE id=?", (now + 6 * 3600, wid))
            return
        hist = json.loads(r["history"] or "[]") + [dict({"t": now, "overall": overall, "job": jid}, **(extra or {}))]
        c.execute("""UPDATE watches SET running_job=NULL, prev_job=last_job, prev_overall=last_overall, last_job=?,
                     last_overall=?, changes=?, next_at=?, history=? WHERE id=?""",
                  (jid, overall, changes, now + every_days * DAY, json.dumps(hist[-26:]), wid))


def due_watches():
    with _lock, _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM watches WHERE active=1 AND next_at<=? AND running_job IS NULL ORDER BY next_at", (time.time(),))]


def hash_valid(h):
    """Ключ владельца ещё действует (для фонового пересчёта). Админ — всегда."""
    if h in _admin_hashes():
        return True
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM codes WHERE hash=?", (h,)).fetchone()
    if not r or r["revoked"]:
        return False
    exp = _exp(r["activated_at"], r["days"]) if r["activated_at"] else None
    return exp is None or time.time() < exp
