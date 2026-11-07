"""Настройки оплаты: тарифы, ссылки Tribute, автовыдача. Хранятся на сервере в data/settings.json."""
import json
import threading
import access

SETTINGS = access.DATA / "settings.json"
_lock = threading.Lock()

FEATURES = ["Поиск ниши по каналу и по теме", "Разбор превью и названий", "Слежение за нишами", "Скачать отчёт для чата"]
DEFAULT_PAY = {
    "currency": "₽",
    "contact": "",
    "note": "Оплата картой российского или зарубежного банка через Tribute в Telegram. После оплаты бот сразу пришлёт ключ доступа.",
    "plans": [
        {"id": "day", "name": "Пробный", "days": 1, "limit": 5, "price": 149, "link": "", "badge": "", "tribute_id": ""},
        {"id": "d3", "name": "3 дня", "days": 3, "limit": 10, "price": 349, "link": "", "badge": "", "tribute_id": ""},
        {"id": "d10", "name": "10 дней", "days": 10, "limit": 15, "price": 790, "link": "", "badge": "Выгодно", "tribute_id": ""},
        {"id": "m1", "name": "Месяц", "days": 30, "limit": 20, "price": 1490, "link": "", "badge": "Популярный", "tribute_id": ""},
    ],
}


def _read():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def section(name, default=None):
    with _lock:
        v = _read().get(name)
    return v if v is not None else (json.loads(json.dumps(default)) if default is not None else {})


def save_section(name, data):
    with _lock:
        d = _read()
        d[name] = data
        tmp = SETTINGS.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(SETTINGS)


def load_pay():
    d = section("pay")
    if not d or not d.get("plans"):
        return json.loads(json.dumps(DEFAULT_PAY))
    for p in d["plans"]:
        p.setdefault("tribute_id", "")
    return d
