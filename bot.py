"""
Автовыдача ключей: Tribute → наш сервер → Telegram-бот → покупатель.

1. Покупатель платит за цифровой товар в Tribute.
2. Tribute шлёт вебхук new_digital_product на /api/tribute/webhook (подпись trbt-signature, HMAC-SHA256 ключом API).
3. Сервер находит тариф по ID товара, создаёт ключ и через бота отправляет его покупателю (по telegram_user_id).
   Если покупатель ещё не запускал бота, ключ ждёт: бот отдаст его, как только человек нажмёт «Старт».
4. Возврат (digital_product_refunded) — ключ отзывается.
Администратор получает в бот уведомление о каждой покупке.
"""
import os
import re
import hmac
import json
import time
import base64
import hashlib
import secrets
import threading
import traceback
import urllib.error
import urllib.request

import access
import paysettings

TG_API = os.environ.get("TG_API", "https://api.telegram.org")
SITE = os.environ.get("SITE_URL", "")
_state = {"thread": None, "stop": None, "username": None, "error": None, "last_poll": None}
_lock = threading.Lock()


# ---------------------------------------------------------------- настройки
def cfg():
    a = paysettings.section("auto")
    return {"api_key": access.dec(a.get("api_key")) or "", "bot_token": access.dec(a.get("bot_token")) or "",
            "bot_username": a.get("bot_username") or "", "admin_chats": a.get("admin_chats") or [],
            "site": a.get("site") or SITE}


def save_cfg(**upd):
    a = paysettings.section("auto")
    for k, v in upd.items():
        if k in ("api_key", "bot_token"):
            a[k] = access.enc(v) if v else None
        else:
            a[k] = v
    paysettings.save_section("auto", a)


def site_url():
    return (cfg()["site"] or "").rstrip("/")


# ---------------------------------------------------------------- Telegram
def tg(method, token=None, timeout=20, **params):
    token = token or cfg()["bot_token"]
    if not token:
        raise RuntimeError("Не задан токен бота")
    req = urllib.request.Request(f"{TG_API}/bot{token}/{method}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read())
        except Exception:
            d = {"ok": False, "description": str(e), "error_code": e.code}
    if not d.get("ok"):
        err = RuntimeError(d.get("description") or "Ошибка Telegram")
        err.code = d.get("error_code")
        raise err
    return d["result"]


def send(chat_id, text, button=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if button:
        p["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
    return tg("sendMessage", **p)


def notify_admins(text):
    for chat in cfg()["admin_chats"]:
        try:
            send(chat, text)
        except Exception:
            pass


def h(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- ключи → сообщения
def days_txt(d):
    if not d:
        return "бессрочно"
    if d < 1:
        return f"{max(1, round(d * 24))} ч"
    n = int(d) if float(d).is_integer() else d
    if isinstance(n, int):
        w = "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней"
        return f"{n} {w}"
    return f"{d} дн."


def left_txt(info):
    if info["status"] == "revoked":
        return "отозван"
    if not info["days"]:
        return "бессрочный"
    if info["status"] == "new":
        return f"{days_txt(info['days'])}, отсчёт начнётся с первого входа"
    left = info["expires_at"] - time.time()
    if left <= 0:
        return "срок истёк"
    d, hh = int(left // 86400), int(left % 86400 // 3600)
    return f"осталось {d} д {hh} ч" if d else f"осталось {hh} ч {int(left % 3600 // 60)} мин"


def key_text(info, plan_name):
    site = site_url()
    lim = f"{info['limit']} анализов в сутки" if info["limit"] else "без лимита анализов"
    return (f"🎉 <b>Спасибо за покупку «{h(plan_name)}»!</b>\n\n"
            f"Ваш ключ доступа:\n<code>{h(info['key'])}</code>\n\n"
            f"Срок: {days_txt(info['days'])} — отсчёт начнётся с первого входа.\nЛимит: {lim}.\n\n"
            + (f"Нажмите кнопку ниже — сайт откроется, ключ подставится сам. Или зайдите на {h(site)} и вставьте ключ.\n\n" if site else "")
            + "Ваши ключи всегда можно посмотреть командой /keys"
            + (f"\n\n<i>Условия: {h(site)}/terms · Конфиденциальность: {h(site)}/privacy</i>" if site else ""))


def deliver(purchase):
    """Отправить ключ покупателю. True — доставлено."""
    if not purchase.get("tg_id") or not purchase.get("code_hash"):
        return False
    info = access.code_info(purchase["code_hash"])
    if not info or not info["key"]:
        return False
    site = site_url()
    btn = ("Открыть Niche Finder", f"{site}/?key={info['key']}") if site.startswith("https://") else None
    try:
        send(purchase["tg_id"], key_text(info, purchase.get("product_name") or "Niche Finder"), btn)
    except Exception as e:
        if getattr(e, "code", None) in (400, 403):  # бот ещё не запущен покупателем
            return False
        raise
    access.purchase_set(purchase["purchase_id"], delivered_at=time.time())
    return True


# ---------------------------------------------------------------- Tribute
def verify(body: bytes, sig: str, api_key: str) -> bool:
    if not sig or not api_key:
        return False
    mac = hmac.new(api_key.encode(), body, hashlib.sha256).digest()
    sig = sig.strip()
    for cand in (mac.hex(), base64.b64encode(mac).decode()):
        if hmac.compare_digest(cand, sig) or hmac.compare_digest(cand.lower(), sig.lower()):
            return True
    if sig.lower().startswith("sha256="):
        return verify(body, sig[7:], api_key)
    return False


def plan_for(payload, pay):
    pid = str(payload.get("product_id") or "")
    for p in pay["plans"]:
        if pid and str(p.get("tribute_id") or "").strip() == pid:
            return p
        m = re.search(r"(?:startapp=p|/p/)(\d+)", p.get("link") or "")
        if pid and m and m.group(1) == pid:
            return p
    # запасной вариант: по сумме (в рублях), если тариф с такой ценой один
    if str(payload.get("currency", "")).lower() in ("rub", "₽"):
        amt = (payload.get("amount") or 0) / 100
        same = [p for p in pay["plans"] if abs(float(p["price"]) - amt) < 0.01]
        if len(same) == 1:
            return same[0]
    return None


def process_purchase(payload, test=False):
    """Создать ключ по покупке и попытаться отправить. Повторный вебхук с тем же purchase_id ничего не меняет."""
    pid = str(payload.get("purchase_id") or payload.get("transaction_id") or
              f"{payload.get('product_id')}-{payload.get('telegram_user_id')}-{payload.get('purchase_created_at')}")
    if access.purchase_get(pid):
        return access.purchase_get(pid)
    pay = paysettings.load_pay()
    plan = plan_for(payload, pay)
    tg_id = payload.get("telegram_user_id")
    uname = payload.get("telegram_username") or ""
    rec = dict(purchase_id=pid, product_id=str(payload.get("product_id") or ""), product_name=payload.get("product_name") or (plan or {}).get("name") or "",
               amount=(payload.get("amount") or 0) / 100, currency=str(payload.get("currency") or "").upper(),
               tg_id=int(tg_id) if tg_id else None, tg_username=uname, trb_user_id=payload.get("trb_user_id") or "",
               status="test" if test else "ok", info="")
    who = f"@{uname}" if uname else (f"id {tg_id}" if tg_id else payload.get("trb_user_id") or "покупатель без Telegram")
    if not plan:
        rec.update(status="unmatched", info="Не найден тариф для этого товара Tribute")
        access.purchase_add(**rec)
        notify_admins(f"⚠️ <b>Покупка без тарифа</b>\nТовар: {h(rec['product_name'])} (ID {h(rec['product_id'])}), {rec['amount']:g} {h(rec['currency'])}\n"
                      f"Покупатель: {h(who)}\nУкажите ID товара у тарифа в «Ключи → Оплата и тарифы» и выдайте ключ вручную.")
        return access.purchase_get(pid)
    note = f"{'ТЕСТ · ' if test else ''}Tribute · {plan['name']} · {who}"
    key, kh = access.create_key_for(plan["days"], plan.get("limit") or 0, note)
    rec.update(plan_id=plan["id"], code_hash=kh, product_name=plan["name"] if test else rec["product_name"] or plan["name"])
    if test:
        rec["status"] = "ok"
        rec["info"] = "тестовая покупка"
    access.purchase_add(**rec)
    p = access.purchase_get(pid)
    sent = False
    if rec["tg_id"]:
        try:
            sent = deliver(p)
        except Exception as e:
            access.purchase_set(pid, info=f"Ошибка отправки: {e}")
    notify_admins(f"💰 <b>{'Тестовая покупка' if test else 'Новая покупка'}: {h(plan['name'])}</b> — {rec['amount']:g} {h(rec['currency'])}\n"
                  f"Покупатель: {h(who)}\nКлюч: <code>{h(key)}</code>\n"
                  + ("✅ Ключ отправлен покупателю в бот." if sent else
                     "⏳ Ключ ждёт: покупатель получит его, когда откроет бота и нажмёт «Старт»." if rec["tg_id"] else
                     "✉️ У покупателя нет Telegram ID (оплата по почте) — отправьте ключ вручную."))
    return access.purchase_get(pid)


def process_refund(payload):
    pid = str(payload.get("purchase_id") or "")
    p = access.purchase_get(pid)
    if not p:
        return
    if p.get("code_hash"):
        access.revoke_hash(p["code_hash"])
    access.purchase_set(pid, status="refunded", info=f"Возврат: {payload.get('refund_reason') or ''}")
    notify_admins(f"↩️ <b>Возврат</b>: {h(p['product_name'])}, покупатель {h('@' + p['tg_username'] if p['tg_username'] else p['tg_id'])}. Ключ отозван.")


def handle_webhook(body: bytes, sig: str):
    c = cfg()
    if not c["api_key"]:
        return 503, "Ключ API Tribute не задан"
    if not verify(body, sig, c["api_key"]):
        access.kv_set("tribute_last_bad", time.time())
        return 401, "Неверная подпись"
    try:
        ev = json.loads(body)
    except Exception:
        return 400, "Неверный JSON"
    access.kv_set("tribute_last", {"at": time.time(), "name": ev.get("name")})
    name = ev.get("name")
    try:
        if name == "new_digital_product":
            process_purchase(ev.get("payload") or {})
        elif name in ("digital_product_refunded", "digital_product_refund"):
            process_refund(ev.get("payload") or {})
    except Exception:
        traceback.print_exc()
        return 500, "Ошибка обработки"
    return 200, "ok"


# ---------------------------------------------------------------- бот: команды
def admin_link_url():
    """Одноразовая ссылка для привязки чата администратора к уведомлениям."""
    code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
    access.kv_set("admin_link", {"code": code, "until": time.time() + 900})
    u = cfg()["bot_username"]
    return f"https://t.me/{u}?start=admin_{code}" if u else None


def on_message(m):
    chat = m.get("chat", {}).get("id")
    uid = m.get("from", {}).get("id")
    text = (m.get("text") or "").strip()
    if not chat or not text:
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    site = site_url()
    if cmd == "/start" and arg.startswith("admin_"):
        link = access.kv_get("admin_link") or {}
        if link.get("code") == arg[6:] and link.get("until", 0) > time.time():
            chats = cfg()["admin_chats"]
            if chat not in chats:
                chats.append(chat)
                save_cfg(admin_chats=chats)
            access.kv_set("admin_link", {})
            return send(chat, "✅ Готово! Сюда будут приходить уведомления о покупках и возвратах.")
        return send(chat, "Ссылка устарела. Получите новую в панели «Ключи» → «Автовыдача ключей».")
    if cmd in ("/start", "/keys", "/key", "/ключ"):
        pend = access.purchases(tg_id=uid, pending=True)
        for p in pend:
            try:
                deliver(p)
            except Exception:
                pass
        mine = [p for p in access.purchases(tg_id=uid, limit=20) if p.get("code_hash") and p["status"] in ("ok", "test")]
        if not mine:
            return send(chat, "👋 Это бот Niche Finder — здесь выдаются ключи доступа после оплаты.\n\n"
                              "Покупок на ваш Telegram пока нет. Если вы только что оплатили — подождите минуту и нажмите /keys."
                              + (f"\n\nТарифы: {h(site)}/#pricing" if site else ""),
                        ("Выбрать тариф", f"{site}/#pricing") if site.startswith("https://") else None)
        if pend and cmd == "/start":
            return  # ключи только что отправлены отдельными сообщениями
        lines = ["🔑 <b>Ваши ключи</b>"]
        for p in mine:
            info = access.code_info(p["code_hash"])
            if info and info["key"]:
                lines.append(f"\n<b>{h(p['product_name'])}</b>\n<code>{h(info['key'])}</code> — {h(left_txt(info))}")
        if site:
            lines.append(f"\nСайт: {h(site)}")
        return send(chat, "\n".join(lines))
    if cmd == "/help":
        return send(chat, "Команды:\n/keys — ваши ключи и сколько осталось\n" + (f"\nТарифы и оплата: {h(site)}/#pricing\nУсловия: {h(site)}/terms\nКонфиденциальность: {h(site)}/privacy\nПоддержка: v01610050@gmail.com" if site else ""))
    send(chat, "Нажмите /keys, чтобы посмотреть свои ключи." + (f"\nКупить доступ: {h(site)}/#pricing" if site else ""))


def _loop(token, stop):
    offset = None
    try:
        me = tg("getMe", token)
        _state["username"] = me.get("username")
        if me.get("username") and me["username"] != cfg()["bot_username"]:
            save_cfg(bot_username=me["username"])
        tg("setMyCommands", token, commands=[{"command": "keys", "description": "Мои ключи и срок"},
                                             {"command": "help", "description": "Помощь"}])
        tg("deleteWebhook", token)
        _state["error"] = None
    except Exception as e:
        _state["error"] = f"Бот не запустился: {e}"
    while not stop.is_set():
        try:
            ups = tg("getUpdates", token, timeout=35, offset=offset, allowed_updates=["message"])
            _state["last_poll"] = time.time()
            _state["error"] = None
            for u in ups:
                offset = u["update_id"] + 1
                if u.get("message"):
                    try:
                        on_message(u["message"])
                    except Exception:
                        traceback.print_exc()
        except Exception as e:
            _state["error"] = str(e)
            if getattr(e, "code", None) == 401:  # неверный токен — ждём, пока админ сменит
                stop.wait(60)
            else:
                stop.wait(5)


def start():
    """Запустить (или перезапустить) опрос бота с текущим токеном."""
    with _lock:
        if _state["stop"]:
            _state["stop"].set()
        token = cfg()["bot_token"]
        if not token or os.environ.get("BOT_POLLING", "1") == "0":
            _state.update(thread=None, stop=None)
            return
        stop = threading.Event()
        t = threading.Thread(target=_loop, args=(token, stop), daemon=True)
        _state.update(thread=t, stop=stop)
        t.start()


def status():
    c = cfg()
    last = access.kv_get("tribute_last")
    return {"bot_token_set": bool(c["bot_token"]), "api_key_set": bool(c["api_key"]),
            "bot_username": c["bot_username"] or _state["username"], "bot_running": bool(_state["thread"] and _state["thread"].is_alive()),
            "bot_error": _state["error"], "last_poll": _state["last_poll"], "admin_chats": len(c["admin_chats"]),
            "last_webhook": last, "last_bad_signature": access.kv_get("tribute_last_bad"), "site": site_url()}


def tribute_products():
    key = cfg()["api_key"]
    if not key:
        raise RuntimeError("Сначала сохраните ключ API Tribute")
    req = urllib.request.Request("https://tribute.tg/api/v1/products?size=100&type=digital", headers={"Api-Key": key})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    return d.get("rows") or []
