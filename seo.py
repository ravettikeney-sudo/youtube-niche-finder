"""Поисковики: robots.txt, sitemap.xml, публичные страницы с текстом, подтверждение прав (Яндекс/Google), IndexNow."""
import datetime as dt
import html
import json
import re
import secrets
import urllib.request
from pathlib import Path

import paysettings

ROOT = Path(__file__).parent
SITE = "https://ytniche.ru"
PAGES_DIR = ROOT / "static" / "seo"

# адрес → (файл с текстом, заголовок вкладки, описание для выдачи, частота обновления, приоритет)
PAGES = {
    "/o-servise": ("o-servise.html",
                   "Сервис для поиска ниши на YouTube и анализа конкурентов — Niche Finder",
                   "Niche Finder находит растущие ниши на YouTube: анализ каналов-конкурентов, идеи роликов, которые выстреливают сейчас, разбор превью и проверка сценария по хитам вашего канала.",
                   "weekly", "0.9"),
    "/kak-najti-nishu-dlya-youtube": ("kak-najti-nishu.html",
                   "Как найти нишу для YouTube-канала в 2026 году: пошаговая инструкция",
                   "Как выбрать нишу для YouTube-канала: где искать молодые растущие каналы, как оценить конкуренцию и спрос, какие темы выстреливают сейчас. Пошагово, с примерами.",
                   "monthly", "0.8"),
    "/proverka-scenariya-youtube": ("proverka-scenariya.html",
                   "Как проверить сценарий ролика для YouTube до съёмки",
                   "Проверка сценария YouTube-ролика: хук в первые секунды, обещание, провисания, призывы к действию. Как сравнить сценарий с хитами своего канала и топ-роликами по теме.",
                   "monthly", "0.8"),
}
LEGAL = {"/terms": "monthly", "/privacy": "monthly"}

ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' "
        "rx='8' fill='%232F5BFF'/%3E%3Ccircle cx='11' cy='20' r='4' fill='white'/%3E%3Ccircle cx='21' cy='11' r='6' "
        "fill='white' opacity='.7'/%3E%3C/svg%3E")


# ---------------------------------------------------------------- настройки (коды подтверждения, ключ IndexNow)
def conf():
    c = paysettings.section("seo", {})
    if not c.get("indexnow"):
        c["indexnow"] = secrets.token_hex(16)
        paysettings.save_section("seo", c)
    return c


def save(yandex="", google=""):
    c = conf()
    clean = lambda s: re.sub(r"[^A-Za-z0-9_\-]", "", (s or "").strip())
    # принимаем и сам код, и целый тег <meta ... content="КОД">, и имя файла yandex_КОД.html / googleКОД.html
    def pick(s, kind):
        s = (s or "").strip()
        m = re.search(r'content=["\']([^"\']+)', s)
        if m:
            return clean(m.group(1))
        m = re.search(r"yandex_([A-Za-z0-9]+)\.html" if kind == "y" else r"google([A-Za-z0-9]+)\.html", s)
        if m:
            return clean(m.group(1))
        return clean(s)
    c["yandex"], c["google"] = pick(yandex, "y"), pick(google, "g")
    paysettings.save_section("seo", c)
    return c


def verify_meta():
    c = conf()
    out = []
    if c.get("yandex"):
        out.append(f'<meta name="yandex-verification" content="{html.escape(c["yandex"])}">')
    if c.get("google"):
        out.append(f'<meta name="google-site-verification" content="{html.escape(c["google"])}">')
    return "\n".join(out)


def verify_file(name):
    """yandex_КОД.html / googleКОД.html — второй способ подтверждения, если мета-тег не подошёл."""
    c = conf()
    if c.get("yandex") and name == f"yandex_{c['yandex']}.html":
        return f'<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">\n</head>\n<body>Verification: {c["yandex"]}</body>\n</html>'
    if c.get("google") and name == f"google{c['google']}.html":
        return f"google-site-verification: google{c['google']}.html"
    if name == f"{conf()['indexnow']}.txt":
        return conf()["indexnow"]
    return None


# ---------------------------------------------------------------- robots / sitemap / IndexNow
def robots():
    return ("User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "Disallow: /*?job=\n"
            "Disallow: /*?key=\n"
            "Disallow: /admin\n"
            "Clean-param: job&key&ref&utm_source&utm_medium&utm_campaign /\n\n"
            f"Sitemap: {SITE}/sitemap.xml\n")


def _mtime(p):
    try:
        return dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def urls():
    return [SITE + "/"] + [SITE + u for u in PAGES]


def sitemap():
    rows = [(SITE + "/", _mtime(ROOT / "static" / "index.html"), "weekly", "1.0")]
    rows += [(SITE + u, _mtime(PAGES_DIR / f), fr, pr) for u, (f, _, _, fr, pr) in PAGES.items()]
    body = "".join(f"  <url><loc>{u}</loc><lastmod>{m}</lastmod><changefreq>{f}</changefreq><priority>{p}</priority></url>\n"
                   for u, m, f, p in rows)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}</urlset>\n'


def indexnow_ping(lst=None):
    """Сообщаем Яндексу (и Bing) об адресах — без регистрации, по протоколу IndexNow."""
    key = conf()["indexnow"]
    data = json.dumps({"host": "ytniche.ru", "key": key, "keyLocation": f"{SITE}/{key}.txt",
                       "urlList": lst or urls()}).encode()
    res = {}
    for name, url in (("yandex", "https://yandex.com/indexnow"), ("bing", "https://www.bing.com/indexnow")):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}), timeout=20)
            res[name] = r.status
        except urllib.error.HTTPError as e:
            res[name] = e.code
        except Exception as e:
            res[name] = repr(e)[:120]
    return res


# ---------------------------------------------------------------- страницы
def _price_from():
    try:
        p = paysettings.load_pay()
        cheapest = min(x["price"] for x in p["plans"] if x.get("price"))
        return f"{int(cheapest) if float(cheapest).is_integer() else cheapest} {p.get('currency', '₽')}"
    except Exception:
        return None


def _faq_ld(body):
    qa = re.findall(r'<details class="faq"><summary>(.*?)</summary>(.*?)</details>', body, re.S)
    if not qa:
        return None
    strip = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": strip(q),
                            "acceptedAnswer": {"@type": "Answer", "text": strip(a)}} for q, a in qa]}


def app_ld():
    d = {"@context": "https://schema.org", "@type": "WebApplication", "name": "Niche Finder", "url": SITE + "/",
         "applicationCategory": "BusinessApplication", "operatingSystem": "Web", "inLanguage": "ru",
         "description": PAGES["/o-servise"][2]}
    try:
        p = paysettings.load_pay()
        prices = [x["price"] for x in p["plans"] if x.get("price")]
        d["offers"] = {"@type": "AggregateOffer", "priceCurrency": "RUB", "lowPrice": min(prices), "highPrice": max(prices),
                       "offerCount": len(prices)}
    except Exception:
        pass
    return d


def page(path):
    f, title, desc, _, _ = PAGES[path]
    body = (PAGES_DIR / f).read_text(encoding="utf-8")
    price = _price_from()
    body = body.replace("{{PRICE}}", price or "небольшой суммы")
    lds = [{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Niche Finder", "item": SITE + "/"},
        {"@type": "ListItem", "position": 2, "name": re.sub(r"\s+—.*$", "", title), "item": SITE + path}]}]
    if path == "/o-servise":
        lds.append(app_ld())
    else:
        lds.append({"@context": "https://schema.org", "@type": "Article", "headline": title, "description": desc,
                    "inLanguage": "ru", "mainEntityOfPage": SITE + path, "image": SITE + "/og.png",
                    "datePublished": "2026-11-13", "dateModified": _mtime(PAGES_DIR / f),
                    "author": {"@type": "Organization", "name": "Niche Finder", "url": SITE + "/"},
                    "publisher": {"@type": "Organization", "name": "Niche Finder", "url": SITE + "/"}})
    faq = _faq_ld(body)
    if faq:
        lds.append(faq)
    ld = "\n".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>' for x in lds)
    cur = ' aria-current="page"'
    nav = "".join(f'<a href="{u}"{cur if u == path else ""}>{n}</a>' for u, n in
                  (("/o-servise", "О сервисе"), ("/kak-najti-nishu-dlya-youtube", "Как найти нишу"),
                   ("/proverka-scenariya-youtube", "Проверка сценария")))
    e = html.escape
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{SITE}{path}">
<meta property="og:type" content="{'website' if path == '/o-servise' else 'article'}">
<meta property="og:site_name" content="Niche Finder">
<meta property="og:locale" content="ru_RU">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{SITE}{path}">
<meta property="og:image" content="{SITE}/og.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
{verify_meta()}
<link rel="icon" href="{ICON}">
<link href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&family=Unbounded:wght@500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/seo/seo.css?v=1">
{ld}
</head>
<body>
<header class="sh"><div class="sw">
  <a class="sb" href="/"><span class="sl" aria-hidden="true"></span>Niche <b>Finder</b></a>
  <nav class="sn" aria-label="Статьи">{nav}</nav>
  <a class="scta" href="/">Открыть сервис</a>
</div></header>
<main class="sw sm">
{body}
</main>
<footer class="sf"><div class="sw">
  <span>© 2026 Niche Finder</span>
  <a href="/o-servise">О сервисе</a><a href="/kak-najti-nishu-dlya-youtube">Как найти нишу</a><a href="/proverka-scenariya-youtube">Проверка сценария</a>
  <a href="/terms">Пользовательское соглашение</a><a href="/privacy">Конфиденциальность</a>
  <span class="sfr">Сервис не связан с YouTube и Google</span>
</div></footer>
</body>
</html>"""


def index_html(raw):
    """Главная: подставляем коды подтверждения и описание сервиса для поисковиков."""
    ld = f'<script type="application/ld+json">{json.dumps(app_ld(), ensure_ascii=False)}</script>'
    return raw.replace("<!--SEO-->", verify_meta() + "\n" + ld, 1)
