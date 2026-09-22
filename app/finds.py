"""Нишевые источники, чтение архивов вглубь, Are.na и «находки».

Новые источники (все проверены: отдают текст и фото от 1200 px):
  архитектура — Hidden Architecture, Drawing Matter, Afasia, Inigo (исторические дома),
                Library of Congress HABS/HAER (обмеры и фото, public domain);
  архив       — The Public Domain Review, Wellcome Collection, Europeana (сотни европейских
                коллекций, в том числе Rijksmuseum и Deutsche Fotothek), Are.na;
  фотография  — American Suburb X;
  кино        — Film-Grab (кадры, только мини), MUBI Notebook.

Вглубь: у сайтов на WordPress каждый сбор берёт свежие записи и одну случайную страницу из всего
архива (через их открытый API), у Public Domain Review — случайные записи из последней сотни.

Находки: посты из нишевых источников. Один мини-слот в день (FIND_SLOT, по умолчанию средний
мини-слот) отдаётся находке — в плане полуавтомата и в автомате. Нет находки в запасе — обычный пост.
Подписчики метку не видят: она только на карточке поста в боте.

Are.na: /arena — список каналов, /arena add <ссылка или имя канала>, ✕ — убрать.

Подключается из app/__init__.py после загрузки app.bot. Сами sources.py, pipeline.py, slots.py,
media.py, screen.py и bot.py не менялись — к ним добавлены обёртки."""
import contextvars
from datetime import timedelta
import html
import json
import logging
import os
import random
import re
import urllib.parse

import httpx
from aiogram import Bot, F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app import config, db, media, pipeline, screen, slots, sources

log = logging.getLogger(__name__)

API_UA = "AHMAG-bot/3.9 (+https://t.me/ahmag; curation bot)"   # loc.gov не пускает браузерные UA без JS

# ======================= источники =======================

WP_SITES = {   # имя → адрес сайта на WordPress: свежие записи + случайная страница архива
    "hidden": "https://hiddenarchitecture.net",
    "drawingmatter": "https://drawingmatter.org",
    "afasia": "https://afasiaarchzine.com",
    "asx": "https://americansuburbx.com",
    "socks": "https://socks-studio.com",      # лента socks уже есть — здесь только архив
}
NEW_FEEDS = {
    "pdr": "https://publicdomainreview.org/rss.xml",
    "inigo": "https://inigo.com/feed/",
    "mubi": "https://mubi.com/notebook/posts.rss",
}
API_SOURCES = ("filmgrab", "habs", "wellcome", "europeana", "arena")

LABELS = {
    "hidden": "Hidden Architecture", "drawingmatter": "Drawing Matter", "afasia": "Afasia",
    "asx": "American Suburb X", "socks": "Socks Studio", "pdr": "The Public Domain Review",
    "inigo": "Inigo", "mubi": "MUBI Notebook", "filmgrab": "Film-Grab",
    "habs": "Library of Congress · HABS", "wellcome": "Wellcome Collection",
    "europeana": "Europeana", "arena": "Are.na",
}
HINTS = {
    "hidden": "архитектура, забытые здания", "drawingmatter": "архитектурные чертежи, история архитектуры",
    "afasia": "архитектура небольших бюро", "asx": "фотография, история фотографии",
    "pdr": "архив, старые изображения", "inigo": "исторические дома и интерьеры", "mubi": "кино",
    "filmgrab": "кино, кадры из фильма", "habs": "архитектура, обмеры и фото исторических зданий",
    "wellcome": "архив, научная графика", "europeana": "музейный и архивный объект",
    "arena": "находка из подборки Are.na",
}
FINDS = {"hidden", "drawingmatter", "afasia", "asx", "socks", "pdr", "inigo",
         "filmgrab", "habs", "wellcome", "europeana", "arena"}
MINI_ONLY = {"filmgrab"}          # плюс блоки Are.na без страницы-источника
META_INTRO = {
    "filmgrab": "Кадры из фильма, архив Film-Grab.",
    "habs": "Документация Historic American Buildings Survey / HAER, Библиотека Конгресса США (public domain).",
    "wellcome": "Изображение из открытой коллекции Wellcome Collection (Лондон).",
    "europeana": "Объект из открытых коллекций Europeana.",
    "arena": "Изображение из подборки на Are.na. Кроме названия и описания ниже фактов нет — "
             "не додумывай автора, место и дату.",
}

WP_LATEST, WP_DEEP = 4, 5          # записей за сбор: свежих и из случайной страницы архива
PDR_LATEST, PDR_DEEP = 3, 6
API_PER_RUN = int(os.getenv("FINDS_API_PER_RUN", "2"))   # объектов за сбор у каждого API
ARENA_PER_RUN = int(os.getenv("ARENA_PER_RUN", "6"))
EUROPEANA_KEY = os.getenv("EUROPEANA_KEY", "api2demo")
FIND_RESERVE = 2                   # столько находок держим для слота находки
FIND_STOCK = 4                     # меньше — при оценке добираем материалы из нишевых источников

HABS_QUERIES = ["Frank Lloyd Wright", "Schindler", "Neutra", "Mies van der Rohe", "Louis Kahn", "Eames",
                "Shaker", "lighthouse", "grain elevator", "observatory", "adobe", "mission church",
                "covered bridge", "barn", "courthouse", "library", "synagogue", "water tower", "factory",
                "greenhouse", "pueblo", "plantation house", "Victorian house", "round barn", "bank",
                "theater", "chapel", "mill", "fort", "Greek Revival"]
WELLCOME_QUERIES = ["architectural drawing", "botanical illustration", "astronomy", "celestial map",
                    "Japanese woodblock", "alchemy", "Persian manuscript", "garden", "ornament", "diagram",
                    "crystal", "shells", "costume", "Chinese painting", "observatory", "cosmology"]
EUROPEANA_QUERIES = ["Architekturfotografie", "architectural photograph", "Bauhaus", "modernism building",
                     "interior photograph", "Werkbund", "brutalism", "architectural drawing",
                     "Hans Finsler", "Albert Renger-Patzsch", "Kurt Hielscher", "Carl Blossfeldt",
                     "villa", "staircase", "church interior", "factory building", "bridge photograph",
                     "street photograph", "Japanese print", "still life photograph"]
ARENA_DEFAULT = ["architecture-drawings-and-speculations", "architecture-drawing-i-like",
                 "interior-architecture-art-product", "type-monastery", "tropical-modernism-geoffrey-bawa",
                 "ruins-archive", "cold-ruins", "heavy-focus", "pastoral-brutalism", "interiors-studios",
                 "photobook-5rge5873ke0", "exhibition-design-8ztvkq3su_u"]

_FIND = contextvars.ContextVar("ahmag_find_slot", default=False)
_attached = False


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


async def _off() -> set[str]:
    return await sources.disabled()


# ---------- WordPress: свежее + случайная страница архива ----------

async def _wp_page(client: httpx.AsyncClient, base: str, page: int, per: int) -> tuple[list[dict], int]:
    r = await client.get(f"{base}/wp-json/wp/v2/posts", timeout=40,
                         params={"per_page": per, "page": page, "_fields": "link,title,content"})
    r.raise_for_status()
    return r.json(), int(r.headers.get("x-wp-totalpages") or 1)


async def _wp_posts(client: httpx.AsyncClient, base: str, latest: int, deep: int) -> list[dict]:
    first, pages = await _wp_page(client, base, 1, latest)
    out = list(first)
    deep_pages = pages * latest // deep            # число страниц при размере deep
    if deep_pages > 1:
        more, _ = await _wp_page(client, base, random.randint(2, deep_pages), deep)
        out += more
    return out


async def collect_wp(client: httpx.AsyncClient) -> list[dict]:
    off, items = await _off(), []
    for name, base in WP_SITES.items():
        if name in off:
            continue
        try:
            posts = await (_wp_socks(client, base) if name == "socks" else _wp_posts(client, base, WP_LATEST, WP_DEEP))
        except Exception as exc:
            log.warning("WP %s: %s", name, exc)
            continue
        for p in posts:
            title = _clean((p.get("title") or {}).get("rendered", ""))
            if p.get("link") and title and len((p.get("content") or {}).get("rendered") or "") > 200:
                items.append({"url": p["link"], "title": title, "source": name,
                              "payload": {"content_html": ((p.get("content") or {}).get("rendered") or "")[:200_000]}})
    return items


async def _wp_socks(client: httpx.AsyncClient, base: str) -> list[dict]:
    _, pages = await _wp_page(client, base, 1, WP_DEEP)
    if pages < 2:
        return []
    posts, _ = await _wp_page(client, base, random.randint(2, pages), WP_DEEP)
    return posts


# ---------- Film-Grab: кадры из фильмов ----------

def _filmgrab_item(p: dict) -> dict | None:
    c = (p.get("content") or {}).get("rendered") or ""
    frames = [u for u in dict.fromkeys(re.findall(r"https?://film-grab\.com/wp-content/uploads/photo-gallery/"
                                                   r"[^\"'\s?]+\.jpe?g", c)) if "/thumb/" not in u]
    if len(frames) < 4:
        return None
    step = max(1, len(frames) // 16)
    frames = frames[::step][:16]
    meta = {"title": _clean((p.get("title") or {}).get("rendered", ""))}
    for label, key in (("Director", "director"), ("Director of Photography", "cinematography"),
                       ("Production Design", "production_design"), ("Year", "year")):
        m = re.search(rf"<p>\s*{label}:\s*(.+?)</p>", c, re.S)
        if m:
            meta[key] = _clean(m.group(1))
    return {"url": p["link"], "title": meta["title"], "source": "filmgrab",
            "payload": {"images": frames, "meta": meta}}


async def collect_filmgrab(client: httpx.AsyncClient) -> list[dict]:
    if "filmgrab" in await _off():
        return []
    try:
        posts = await _wp_posts(client, "https://film-grab.com", 2, 3)
    except Exception as exc:
        log.warning("Film-Grab: %s", exc)
        return []
    return [it for it in map(_filmgrab_item, posts) if it]


# ---------- The Public Domain Review: вся сотня записей ленты ----------

async def collect_pdr(client: httpx.AsyncClient) -> list[dict]:
    if "pdr" in await _off():
        return []
    import feedparser
    try:
        r = await client.get(NEW_FEEDS["pdr"], timeout=40)
        r.raise_for_status()
        entries = feedparser.parse(r.content).entries
    except Exception as exc:
        log.warning("PDR: %s", exc)
        return []
    pick = entries[:PDR_LATEST] + random.sample(entries[PDR_LATEST:], min(PDR_DEEP, max(0, len(entries) - PDR_LATEST)))
    out = []
    for e in pick:
        body = (e.get("content") or [{}])[0].get("value", "") or e.get("summary", "")
        if e.get("link"):
            out.append({"url": e["link"], "title": e.get("title", ""), "source": "pdr",
                        "payload": {"content_html": body[:200_000]}})
    return out


# ---------- Library of Congress: HABS/HAER ----------

def _loc_iiif(tif_url: str) -> str | None:
    m = re.search(r"/storage-services/master/(.+)\.tif$", tif_url or "")
    return (f"https://tile.loc.gov/image-services/iiif/master:{m.group(1).replace('/', ':')}/full/full/0/default.jpg"
            if m else None)


async def collect_habs(client: httpx.AsyncClient) -> list[dict]:
    if "habs" in await _off() or API_PER_RUN <= 0:
        return []
    items = []
    async with httpx.AsyncClient(headers={"User-Agent": API_UA}, follow_redirects=True, timeout=40) as loc:
        for q in random.sample(HABS_QUERIES, 2):
            try:
                r = await loc.get("https://www.loc.gov/collections/historic-american-buildings-landscapes-"
                                  "and-engineering-records/", params={"q": q, "fo": "json", "c": 25})
                r.raise_for_status()
                results = [x for x in r.json().get("results") or [] if _habs_photos(x) >= 3]
                random.shuffle(results)
                for x in results[:3]:
                    it = await _habs_item(loc, x)
                    if it:
                        items.append(it)
                    if len(items) >= API_PER_RUN:
                        return items
            except Exception as exc:
                log.warning("HABS %s: %s", q, exc)
    return items


def _habs_photos(x: dict) -> int:
    m = re.search(r"Photo\(s\):\s*(\d+)", " ".join(x.get("description") or []))
    return int(m.group(1)) if m else 0


async def _habs_item(loc: httpx.AsyncClient, x: dict) -> dict | None:
    url = x.get("url") or x.get("id")
    if not url:
        return None
    d = (await loc.get(url.replace("http://", "https://"), params={"fo": "json"})).json()
    photos, sheets = [], []
    for res in d.get("resources") or []:
        cap = (res.get("caption") or "").lower()
        for f in res.get("files") or []:
            tif = next((v.get("url") for v in f if (v.get("mimetype") or "").endswith("tiff")), None)
            iiif = _loc_iiif(tif)
            if iiif:
                (photos if "photo" in cap else sheets if "drawing" in cap else []).append(iiif)
    if len(photos) < 1:
        return None
    it = d.get("item") or {}
    date = it.get("created_published") or it.get("date") or ""
    meta = {"title": it.get("title") or x.get("title"), "date": "; ".join(date) if isinstance(date, list) else date,
            "notes": "; ".join((it.get("notes") or [])[:6])[:900], "summary": "; ".join(it.get("summary") or [])[:900],
            "location": ", ".join((x.get("location") or [])[:4]), "survey": "HABS/HAER, Library of Congress"}
    return {"url": url.replace("http://", "https://"), "title": meta["title"] or "", "source": "habs",
            "payload": {"images": (photos[:8] + sheets[:2])[:config.EVAL_PHOTOS], "meta": meta}}


# ---------- Wellcome Collection ----------

async def collect_wellcome(client: httpx.AsyncClient) -> list[dict]:
    if "wellcome" in await _off() or API_PER_RUN <= 0:
        return []
    items = []
    for q in random.sample(WELLCOME_QUERIES, 2):
        try:
            r = await client.get("https://api.wellcomecollection.org/catalogue/v2/images",
                                 params={"query": q, "pageSize": 30}, timeout=40)
            rows = r.json().get("results") or []
            random.shuffle(rows)
            for im in rows:
                loc = next((l for l in im.get("locations") or [] if "iiif" in (l.get("url") or "")), None)
                src = im.get("source") or {}
                if not loc or not src.get("id"):
                    continue
                w = (await client.get(f"https://api.wellcomecollection.org/catalogue/v2/works/{src['id']}",
                                      params={"include": "contributors,production,notes"}, timeout=40)).json()
                meta = {"title": w.get("title") or src.get("title"),
                        "contributors": "; ".join((c.get("agent") or {}).get("label", "") for c in w.get("contributors") or []),
                        "date": "; ".join(p.get("label", "") for pr in w.get("production") or [] for p in pr.get("dates") or []),
                        "description": _clean(w.get("description") or "")[:1200],
                        "license": ((loc.get("license") or {}).get("label")), "credit": loc.get("credit")}
                items.append({"url": f"https://wellcomecollection.org/works/{src['id']}", "title": meta["title"] or "",
                              "source": "wellcome",
                              "payload": {"images": [loc["url"].replace("/info.json", "/full/!2400,2400/0/default.jpg")],
                                          "meta": meta}})
                if len(items) >= API_PER_RUN:
                    return items
        except Exception as exc:
            log.warning("Wellcome %s: %s", q, exc)
    return items


# ---------- Europeana ----------

async def collect_europeana(client: httpx.AsyncClient) -> list[dict]:
    if "europeana" in await _off() or API_PER_RUN <= 0:
        return []
    items = []
    for q in random.sample(EUROPEANA_QUERIES, 2):
        try:
            r = await client.get("https://api.europeana.eu/record/v2/search.json", timeout=40, params={
                "wskey": EUROPEANA_KEY, "query": q, "rows": 40, "start": 1,
                "reusability": "open", "media": "true", "profile": "rich",
                "qf": ["TYPE:IMAGE", "IMAGE_SIZE:extra_large"]})
            rows = r.json().get("items") or []
            random.shuffle(rows)
            for x in rows:
                img = (x.get("edmIsShownBy") or [None])[0]
                if not img or not x.get("guid"):
                    continue
                first = lambda k: ((x.get(k) or [""])[0] or "")
                meta = {"title": first("title"), "creator": first("dcCreator"), "year": first("year"),
                        "provider": first("dataProvider"), "country": first("country"),
                        "description": _clean(first("dcDescription"))[:1200], "rights": first("rights")}
                items.append({"url": x["guid"].split("?")[0], "title": meta["title"], "source": "europeana",
                              "payload": {"images": [img], "meta": meta}})
                if len(items) >= API_PER_RUN:
                    return items
        except Exception as exc:
            log.warning("Europeana %s: %s", q, exc)
    return items


# ---------- Are.na ----------

BAD_DOM = re.compile(r"instagram|facebook|pinterest|twimg|twitter|x\.com|tumblr|gstatic|google\.|blogspot|"
                     r"amazonaws|cloudfront|imgur|reddit|youtube|vimeo|are\.na|wp\.com|squarespace-cdn|cdn", re.I)
FILEISH = re.compile(r"^(img|dsc|photo|image|screen ?shot|bildschirmfoto|untitled|download|[0-9a-f_\-]{8,})|"
                     r"\.(jpe?g|png|webp|gif)\b|^@|^\(\d+\)", re.I)


def _good_title(t: str) -> bool:
    t = (t or "").strip()
    return len(t) >= 6 and not FILEISH.search(t) and len(re.findall(r"[^\W\d_]{3,}", t)) >= 2


def _page_src(b: dict) -> str:
    u = ((b.get("source") or {}).get("url") or "").strip()
    dom = urllib.parse.urlparse(u).netloc
    if not u.startswith("http") or not dom or BAD_DOM.search(dom) or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", u, re.I):
        return ""
    return u


async def arena_channels() -> list[str]:
    return list(await db.get_setting("arena_channels", ARENA_DEFAULT))


def _arena_item(b: dict, slug: str) -> dict | None:
    img = ((b.get("image") or {}).get("original") or {}).get("url")
    if not img:
        return None
    title, desc = _clean(b.get("title") or ""), _clean(b.get("description") or "")[:800]
    page = _page_src(b)
    if page:
        return {"url": page, "title": title or page, "source": "arena",
                "payload": {"content_html": f"<p>{html.escape(title)}. {html.escape(desc)}</p><img src=\"{img}\">"}}
    if not _good_title(title):
        return None
    meta = {"title": title, "description": desc, "arena_channel": slug,
            "connected_by": ((b.get("connected_by_username") or (b.get("user") or {}).get("full_name")) or "")}
    return {"url": f"https://www.are.na/block/{b['id']}", "title": title, "source": "arena",
            "payload": {"images": [img], "meta": meta}}


async def collect_arena(client: httpx.AsyncClient) -> list[dict]:
    if "arena" in await _off() or ARENA_PER_RUN <= 0:
        return []
    chans = await arena_channels()
    items = []
    for slug in random.sample(chans, min(3, len(chans))):
        try:
            head = (await client.get(f"https://api.are.na/v2/channels/{slug}/thumb", timeout=30)).json()
            n = int(head.get("length") or 0)
            page = random.randint(1, max(1, n // 40))
            r = await client.get(f"https://api.are.na/v2/channels/{slug}/contents", timeout=40,
                                 params={"per": 40, "page": page, "direction": "desc", "sort": "position"})
            blocks = [b for b in r.json().get("contents") or [] if b.get("class") == "Image"]
            random.shuffle(blocks)
            got = [it for it in (_arena_item(b, slug) for b in blocks) if it][:max(1, ARENA_PER_RUN // 3)]
            items += got
        except Exception as exc:
            log.warning("Are.na %s: %s", slug, exc)
    return items[:ARENA_PER_RUN]


COLLECTORS = [collect_wp, collect_filmgrab, collect_pdr, collect_habs, collect_wellcome,
              collect_europeana, collect_arena]


# ======================= фото со страниц: правила по сайтам =======================

WP_SIZE = re.compile(r"-\d{2,4}x\d{2,4}(?=\.(?:jpe?g|png|webp)$)", re.I)


def _stem(u: str) -> str:
    name = urllib.parse.urlparse(u).path.rsplit("/", 1)[-1]
    name = WP_SIZE.sub("", name)
    return re.sub(r"(-\d{1,3})?\.(jpe?g|png|webp)$", "", name, flags=re.I).lower()


def fix_images(url: str, urls: list[str], raw_html: str = "") -> list[str]:
    dom = urllib.parse.urlparse(url).netloc
    if "afasiaarchzine.com" in dom:
        ups = [WP_SIZE.sub("", u.split("?")[0]) for u in urls if "/wp-content/uploads/" in u]
        base = next((_stem(u) for u in ups if "afasia" in _stem(u)), None)
        if base:
            ups = [u for u in ups if _stem(u) == base]
        return list(dict.fromkeys(ups))
    if "publicdomainreview.org" in dom:
        out = [u.split("?")[0] for u in urls if "pdr-assets" in u and "/sources/" not in u]
        return list(dict.fromkeys(out))
    if "inigo.com" in dom:
        found = re.findall(r"https://cdn\.themodernhouse\.com/[^\"'\\\s)]+?_webres\.jpg", raw_html)
        return list(dict.fromkeys(found + [u for u in urls if "themodernhouse" in u]))
    return urls


def _wrap_extract(orig):
    async def extract_article(client, url, feed_html=""):
        art = await orig(client, url, feed_html)
        dom = urllib.parse.urlparse(url).netloc
        if any(d in dom for d in ("afasiaarchzine.com", "publicdomainreview.org", "inigo.com")):
            raw = ""
            if "inigo.com" in dom:
                try:
                    raw = (await client.get(url, timeout=30, follow_redirects=True)).text
                except Exception:
                    pass
            fixed = fix_images(url, art["image_urls"], raw)
            if fixed:
                art = {**art, "image_urls": fixed[:30]}
        return art

    extract_article.__wrapped__ = orig
    return extract_article


# ======================= обёртки: сбор, подготовка, отбор =======================

def _wrap_source_names(orig):
    async def source_names():
        names = await orig()
        return names + [n for n in [*WP_SITES, *NEW_FEEDS, *API_SOURCES] if n not in names]

    source_names.__wrapped__ = orig
    return source_names


PROTECTED = re.compile(r"^\s*(protected|private)\s*:", re.I)


def _wrap_prefilter(orig):
    def prefilter(title: str):
        if PROTECTED.search(title or ""):
            return "запись закрыта паролем"
        return orig(title)

    prefilter.__wrapped__ = orig
    return prefilter


def _wrap_museum_text(orig):
    def _museum_text(source: str, meta: dict) -> str:
        if source not in META_INTRO:
            return orig(source, meta)
        return META_INTRO[source] + "\n" + "\n".join(f"{k}: {v}" for k, v in meta.items() if v)

    _museum_text.__wrapped__ = orig
    return _museum_text


def _wrap_prepare(orig):
    async def _prepare(client, cand, force: bool = False):
        res = await orig(client, cand, force)
        if isinstance(res, dict) and not force:
            payload = json.loads(cand["payload"] or "{}")
            if cand["source"] in MINI_ONLY or (cand["source"] == "arena" and "meta" in payload):
                res["allow_std"] = False       # мало фактов — только мини
        return res

    _prepare.__wrapped__ = orig
    return _prepare


async def ready_finds() -> list:
    return [p for p in await db.ready_posts() if p["source"] in FINDS and p["format"] != "notes"]


def _wrap_select_pool(orig):
    """Находок в запасе мало — добираем в оценку пару материалов из нишевых источников."""
    async def _select_pool(n: int, target: int):
        chosen = await orig(n, target)
        try:
            have = len(await ready_finds()) + sum(1 for c in chosen if c["source"] in FINDS)
            need = min(2, FIND_STOCK - have)
            if need > 0 and n > 0:
                ids = {c["id"] for c in chosen}
                pool = [c for c in await db.candidates("triaged", limit=400)
                        if c["source"] in FINDS and c["id"] not in ids]
                pool.sort(key=lambda c: (-(c["tprio"] or 0), -c["id"]))
                extra = pool[:need]
                if extra:
                    chosen = chosen[:max(0, n - len(extra))] + extra
        except Exception:
            log.warning("Находки: добор в оценку не удался", exc_info=True)
        return chosen

    _select_pool.__wrapped__ = orig
    return _select_pool


def _wrap_pick_next(orig):
    """Слот находки → сначала находка. Остальные слоты не трогают последние FIND_RESERVE находок."""
    async def pick_next(fmt=None, *a, **k):
        try:
            finds = await ready_finds()
            if _FIND.get():
                others = {p["source"] for p in await db.ready_posts()} - FINDS
                post = await orig(fmt, *a, **{**k, "skip_sources": set(k.get("skip_sources") or ()) | others})
                if post:
                    return post
            elif len(finds) <= FIND_RESERVE and finds:
                post = await orig(fmt, *a, **{**k, "skip_sources": set(k.get("skip_sources") or ()) | FINDS})
                if post:
                    return post
        except Exception:
            log.warning("Находки: выбор поста — по-старому", exc_info=True)
        return await orig(fmt, *a, **k)

    pick_next.__wrapped__ = orig
    return pick_next


# ======================= слот находки =======================

def find_slot() -> tuple[int, int] | None:
    raw = os.getenv("FIND_SLOT", "").strip().lower()
    minis = [(h, m) for h, m, f in config.SLOTS if f == "mini"]
    if raw in ("off", "0", "none", "нет"):
        return None
    if raw:
        try:
            h, m = (int(x) for x in raw.split(":"))
            if (h, m) in minis:
                return h, m
            log.warning("FIND_SLOT=%s — такого мини-слота нет, беру средний", raw)
        except ValueError:
            log.warning("FIND_SLOT=%s не разобран, беру средний мини-слот", raw)
    return minis[len(minis) // 2] if minis else None


def is_find_key(key: str) -> bool:
    fs = find_slot()
    return bool(fs) and key[-5:] == f"{fs[0]:02d}:{fs[1]:02d}"


async def build_plan(offset: int) -> tuple[list, int]:
    """Как slots.build_plan, но слот находки сначала пробует находку."""
    made, missing = [], 0
    for s in await slots.day_state(offset):
        if s["state"] != "empty" or s["dt"] <= slots._now() + timedelta(minutes=5):
            continue
        tok = _FIND.set(s["fmt"] == "mini" and is_find_key(s["key"]))
        try:
            post = await pipeline.pick_next(s["fmt"], exclude={p["id"] for p in made}, planned=made)
        finally:
            _FIND.reset(tok)
        if not post:
            missing += 1
            continue
        await slots.propose(post["id"], s["key"])
        if s["fmt"] == "std":
            try:
                post = await pipeline.ensure_text(post["id"])
            except Exception:
                log.exception("Текст для плана, пост %s", post["id"])
        made.append(await db.get_post(post["id"]))
    return made, missing


def _wrap_prepare_slot(orig):
    async def prepare(bot: Bot, h: int, m: int, fmt: str) -> None:
        tok = _FIND.set(fmt == "mini" and find_slot() == (h, m))
        try:
            return await orig(bot, h, m, fmt)
        finally:
            _FIND.reset(tok)

    prepare.__wrapped__ = orig
    return prepare


# ======================= экран =======================

def _wrap_post_kb(orig):
    async def _post_kb(post, mode, idx, n, clipped, sub):
        kbd = await orig(post, mode, idx, n, clipped, sub)
        try:
            if not sub and post["source"] in FINDS:
                kbd.inline_keyboard.insert(0, [screen.btn(f"🔍 Находка · {LABELS.get(post['source'], post['source'])}",
                                                          "v:noop")])
        except Exception:
            log.warning("Метка находки не добавилась", exc_info=True)
        return kbd

    _post_kb.__wrapped__ = orig
    return _post_kb


def _wrap_home(orig):
    async def home(arg: dict):
        photo, text, kbd, arg = await orig(arg)
        try:
            n = len(await ready_finds())
            fs = find_slot()
            label = f"🔍 Находки · {n}" + (f" · слот {fs[0]:02d}:{fs[1]:02d}" if fs else "")
            rows = [list(r) for r in kbd.inline_keyboard]
            rows.insert(max(len(rows) - 1, 0), [screen.btn(label, "fa:info")])
            kbd = InlineKeyboardMarkup(inline_keyboard=rows)
        except Exception:
            log.warning("Кнопка находок не добавилась", exc_info=True)
        return photo, text, kbd, arg

    home.__wrapped__ = orig
    return home


async def _arena_text(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    chans = await arena_channels()
    lines = ["<b>Are.na — каналы для находок</b>"]
    if note:
        lines.append(f"<b>{html.escape(note)}</b>")
    lines.append("Бот каждый сбор заглядывает в три случайных канала, на случайную страницу. "
                 "Добавить: <code>/arena add ссылка-или-имя</code>\n")
    lines += [f'{i + 1}. <a href="https://www.are.na/channel/{c}">{html.escape(c)}</a>' for i, c in enumerate(chans)]
    btns = [screen.btn(f"✕ {i + 1}", f"fa:del:{i}") for i in range(len(chans))]
    rows = [btns[i:i + 6] for i in range(0, len(btns), 6)] + [[screen.btn("Закрыть", "fa:close")]]
    return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=rows)


async def cmd_arena(msg: Message, command: CommandObject, bot: Bot):
    args = (command.args or "").split()
    note = ""
    if len(args) >= 2 and args[0].lower() in ("add", "+"):
        slug = args[1].rstrip("/").rsplit("/", 1)[-1].split("?")[0].lower()
        try:
            async with httpx.AsyncClient(headers={"User-Agent": API_UA}, timeout=30) as c:
                r = await c.get(f"https://api.are.na/v2/channels/{slug}/thumb")
                ok = r.status_code == 200 and (r.json().get("status") or "public") != "private"
        except Exception:
            ok = False
        chans = await arena_channels()
        if not ok:
            note = f"Канал «{slug}» не открылся — проверь ссылку (закрытые каналы не читаются)"
        elif slug in chans:
            note = "Этот канал уже в списке"
        else:
            await db.set_setting("arena_channels", chans + [slug])
            note = f"Добавил: {slug}"
    text, kb = await _arena_text(note)
    await bot.send_message(config.ADMIN_ID, text, reply_markup=kb, disable_web_page_preview=True)
    try:
        await bot.delete_message(config.ADMIN_ID, msg.message_id)
    except Exception:
        pass


async def on_cb(cb: CallbackQuery, bot: Bot):
    p = cb.data.split(":")
    a = p[1]
    if a == "info":
        stock: dict[str, int] = {}
        for post in await ready_finds():
            stock[post["source"]] = stock.get(post["source"], 0) + 1
        fs = find_slot()
        text = ("Находки в запасе: " + (", ".join(f"{LABELS.get(k, k)} {v}" for k, v in
                                                   sorted(stock.items(), key=lambda x: -x[1])) or "пока нет")
                + (f". Слот находки: {fs[0]:02d}:{fs[1]:02d}." if fs else ". Слот находки выключен (FIND_SLOT=off).")
                + " Каналы Are.na — /arena")
        return await cb.answer(text[:200], show_alert=True)
    if a == "close":
        await cb.answer()
        try:
            return await cb.message.delete()
        except Exception:
            return
    if a == "del":
        chans = await arena_channels()
        i = int(p[2])
        if i < len(chans):
            gone = chans.pop(i)
            await db.set_setting("arena_channels", chans)
            note = f"Убрал: {gone}"
        else:
            note = "Список уже изменился"
        await cb.answer()
        text, kb = await _arena_text(note)
        try:
            await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception:
            pass
        return
    await cb.answer()


# ======================= подключение =======================

def attach(bot_module) -> None:
    global _attached
    if _attached:
        return
    for name, url in NEW_FEEDS.items():
        if name != "pdr":                 # PDR читается своим сборщиком — из всей сотни записей
            sources.FEEDS.setdefault(name, url)
    sources.HINTS.update(HINTS)
    for fn in COLLECTORS:
        if fn not in sources.COLLECTORS:
            sources.COLLECTORS.append(fn)
    sources.source_names = _wrap_source_names(sources.source_names)
    sources.prefilter = _wrap_prefilter(sources.prefilter)
    media.extract_article = _wrap_extract(media.extract_article)
    pipeline._museum_text = _wrap_museum_text(pipeline._museum_text)
    pipeline._prepare = _wrap_prepare(pipeline._prepare)
    pipeline._select_pool = _wrap_select_pool(pipeline._select_pool)
    pipeline.pick_next = _wrap_pick_next(pipeline.pick_next)
    slots.build_plan = build_plan
    slots.prepare = _wrap_prepare_slot(slots.prepare)
    screen._post_kb = _wrap_post_kb(screen._post_kb)
    screen.VIEWS["home"] = _wrap_home(screen.VIEWS["home"])
    r = bot_module.router
    r.message.register(cmd_arena, Command("arena"))
    r.callback_query.register(on_cb, F.data.startswith("fa:"))
    _attached = True
    fs = find_slot()
    log.info("Находки подключены: %d новых источников, слот находки %s", len(LABELS),
             f"{fs[0]:02d}:{fs[1]:02d}" if fs else "выключен")
