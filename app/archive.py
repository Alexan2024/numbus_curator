"""Архив сайта (6.2): чистка старых записей theahmag.com и перенос постов Instagram.

Работает в фоне сам, кусками раз в 10 минут, пока не пройдёт всё или не кончится свой бюджет
(ARCHIVE_BUDGET_USD, по умолчанию $20 — отдельно от дневного потолка бота: дневные лимиты он не трогает).

Этапы
1. site — старые записи сайта, у которых фото лежат лентой по 800 px. Для каждой ищем оригиналы тех же
   снимков: страница источника из базы бота → ссылки из кредитов → поиск по сайту издания на WordPress
   (бесплатно) → поиск в сети (Claude Haiku, ~$0.03). Тот же ли это снимок, решает app/photomatch.py.
   • Нашлись оригиналы крупнее (от 1.5× ленты, лучше от 1600 px) — запись получает их. Если совпала хотя бы
     половина фото, в записи остаются только они; если меньше — остальные фото остаются как были (800 px).
   • Совпало, но крупнее нет — запись не трогаем.
   • Не совпало ничего, и все найденные страницы открылись, — запись скрывается (её можно вернуть:
     /archive restore <номер>). Сбой сети, сайта или Claude — не повод скрывать: повтор позже, после трёх
     сбоев запись остаётся как есть.
   • Не скрываются: записи, которые публиковал сам бот; записи с видео; записи, у фото которых нет деталей
     для сравнения (небо, туман, белая стена).
   • Предохранитель: скрытия не уходят на сайт, пока на первых 20 «чужих» записях (не из бота) оригиналы
     не нашлись хотя бы у 15%. Нашлись реже — пауза и вопрос автору. Дальше он следит за последними 20.
2. ig_fetch — все посты Instagram через API (видео и рилсы — мимо).
3. ig_curate — дубли постов из канала долой, остальное оценивает Claude Sonnet пакетами (вкус и качество).
4. ig_import — для отобранных (лучшие первыми): дубль записи сайта по фото — мимо; оригиналы — тем же способом,
   от 1600 px, не меньше половины фото; запись на двух языках по подписи; на сайт. Без оригиналов не переносится.
5. done — отчёт.

Записи из Instagram: номер от 100000 (IG_BASE), src: "ig", ig: ссылка на пост, d — дата поста; на сайте они
встают по дате. Выкладка — пачками, в паузах между слотами публикации.
Управление: /archive — где сейчас, расход, пауза, скрытые; /archive restore <номер> — вернуть скрытую запись.
Пауза, бюджет и предохранитель лежат в control.json отдельно от хода работы: кнопка, нажатая посреди захода,
не теряется."""
from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import math
import os
import re
import shutil
import time
from pathlib import Path

import anthropic
import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from PIL import Image, ImageOps

from app import brand, config, curator, db, instagram, media, niche, photomatch, sitepub, sources, voice

log = logging.getLogger(__name__)
router = Router(name="archive")
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)

CAP = float(os.getenv("ARCHIVE_BUDGET_USD", "20"))
ENABLED = os.getenv("ARCHIVE", "1").strip().lower() not in ("0", "off", "false", "no")
MIN_SCORE = float(os.getenv("ARCHIVE_MIN_SCORE", "7"))
IG_BASE = 100_000
TICK_SECONDS = 300               # сколько работать за один заход (заходы раз в 10 минут)
COMMIT_EVERY = 10                # изменений в одной выкладке на сайт
PAGES_MAX = 3                    # страниц-кандидатов на запись
CANDS_PER_PAGE = 24
SEARCH_COST = 0.04               # оценка одного поиска в сети, $ (с запасом)
CURATE_COST = 0.004              # оценка одного поста Instagram в пакете, $
WRITE_COST = 0.02                # запись на двух языках, $
BATCH_MAX = 300                  # постов в одном пакете оценки
TRIES = 3                        # сбоев на запись или пост — потом оставляем как есть
GUARD_N, GUARD_RATE = 20, 0.15   # предохранитель: окно «чужих» записей и доля найденных
COVER_SITE, COVER_IG = 0.6, 0.4  # photomatch.score(min_cover): лента сайта — весь кадр, Instagram — 4:5

ROOT = sitepub.ROOT / "archive"
STATE = ROOT / "state.json"
CONTROL = ROOT / "control.json"
HIDDEN = sitepub.ROOT / "hidden.json"
WORK = ROOT / "work"
SKIP_HOSTS = ("t.me", "telegram", "instagram.com", "pinterest", "facebook.com", "tumblr", "twitter.com", "x.com",
              "theahmag.com", "youtube.com", "vimeo.com")

SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_ig (
    media_id TEXT PRIMARY KEY,
    ts TEXT,
    caption TEXT,
    permalink TEXT,
    mtype TEXT,
    status TEXT NOT NULL,   -- new | video | nothumb | dup_bot | dup_site | curating | low | selected | imported | notfound | error
    score REAL,
    note TEXT,
    data TEXT,              -- json: фото, оценка (category, query, via), число сбоев
    rec_id INTEGER,
    updated_at TEXT
);
"""

_running = False
_acc: list = [0.0]               # траты текущего захода (curator.cost_sink)
_covers: dict = {}               # особые точки обложек сайта: {номер записи: Feat}


class Stop(Exception):
    """Остановить заход без отметки о сбое у записи: API Anthropic недоступен, на счёте нет денег."""


# ======================= состояние и управление =======================

def _new_state() -> dict:
    return {"v": 2, "phase": "site", "spent": 0.0, "started": db.now(), "finished": None, "search_model": None,
            "site": {"done": [], "fixed": 0, "hidden": 0, "kept": 0, "errors": {}, "free": 0, "paid": 0, "window": []},
            "ig": {"cursor": None, "fetched": 0, "batch": None, "selected": 0, "imported": 0, "notfound": 0,
                   "dups": 0, "low": 0},
            "pending": {"put": {}, "hide": {}}, "last_error": {}}


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


def load_state() -> dict | None:
    return _read(STATE)


def save_state(st: dict) -> None:
    _write(STATE, st)


CTL_DEFAULT = {"paused": False, "why": "", "detail": "", "cap": CAP, "guard_ok": False, "guard_user": False,
               "told": False}


def ctl() -> dict:
    """Пауза, бюджет, предохранитель — отдельный файл: его пишут и кнопки, и заход, по одному полю."""
    return {**CTL_DEFAULT, **(_read(CONTROL) or {})}


def set_ctl(**kw) -> dict:
    c = {**ctl(), **kw}
    _write(CONTROL, c)
    return c


def _pause(why: str, detail: str = "") -> None:
    set_ctl(paused=True, why=why, detail=detail, told=False)


def spent(st: dict) -> float:
    return st["spent"] + _acc[0]


def can_spend(st: dict, est: float) -> bool:
    return spent(st) + est <= ctl()["cap"]


def _load_hidden() -> dict:
    return _read(HIDDEN) or {}


def _save_hidden(h: dict) -> None:
    _write(HIDDEN, h)


async def init() -> None:
    async with db.connect() as c:
        await c.executescript(SCHEMA)
        await c.commit()


# ======================= поиск оригиналов =======================

def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _wp_sites() -> dict[str, str]:
    """Издания на WordPress, у которых можно искать бесплатно: {имя без пробелов и регистра: адрес сайта}."""
    out = {}
    for name, url in {**sources.FEEDS, **niche.NEW_FEEDS}.items():
        m = re.match(r"(https?://[^/]+)", url)
        if m and "feedburner" not in url and "rss.xml" not in url and "mubi.com" not in url:
            out[name] = m.group(1)
    out.update(niche.WP_SITES)
    full = {}
    for k, v in out.items():
        full[_norm_name(k)] = v
        host = re.sub(r"^https?://(www\.)?", "", v).split(".")[0]
        full[_norm_name(host)] = v
    return full


def wp_site(via: str | None) -> str | None:
    if not via:
        return None
    sites = _wp_sites()
    key = _norm_name(via)
    if key in sites:
        return sites[key]
    for k, v in sites.items():          # «Dezeen Magazine», «haggerty museum of art / thisiscolossal»
        if k and len(k) >= 5 and k in key:
            return v
    return None


async def wp_search(client: httpx.AsyncClient, base: str, query: str) -> list[str]:
    """Бесплатный поиск по сайту на WordPress: до 3 ссылок на записи (сбой — пустой список)."""
    q = " ".join(re.findall(r"[\w’'-]+", query or ""))[:80]
    if not q:
        return []
    try:
        r = await client.get(f"{base}/wp-json/wp/v2/search", timeout=30,
                             params={"search": q, "per_page": 3, "type": "post"})
        r.raise_for_status()
        return [x["url"] for x in r.json() if isinstance(x, dict) and str(x.get("url", "")).startswith("http")][:3]
    except Exception as exc:
        log.info("Архив: поиск на %s не ответил: %r", base, exc)
        return []


SEARCH_SYSTEM = """Ты помогаешь журналу AHMAG (архитектура, искусство, фотография, кино, архивные находки) найти в сети оригинальные фотографии работы, о которой у журнала уже есть пост. Найди до 4 страниц, где опубликованы фотографии именно этой работы в хорошем качестве: сайт автора, бюро или фотографа; профильные издания (Dezeen, ArchDaily, designboom, Divisare, Leibal, Colossal, Hyperallergic и другие); страница музея или архива. Не соцсети (Instagram, Pinterest, Facebook, Tumblr, X), не стоки, не магазины. Не путай с однофамильцами и одноимёнными работами.
Верни ТОЛЬКО JSON: {"urls": ["https://...", "..."]}. Ничего не нашёл — {"urls": []}."""


async def web_search(st: dict, query: str) -> list[str] | None:
    """Поиск страниц с оригиналами через Claude с веб-поиском. → ссылки ([] — честно ничего нет) или None —
    ответ не разобрался (повторим позже). Нет денег на счёте или API лежит — Stop."""
    models = [st.get("search_model") or config.TRIAGE_MODEL]
    if models[0] != config.CLAUDE_MODEL:
        models.append(config.CLAUDE_MODEL)
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    for model in models:
        try:
            out = await curator._call(f"Работа: {query}\nНайди страницы с её фотографиями. Верни JSON.",
                                      system=SEARCH_SYSTEM, model=model, max_tokens=800, tools=tools)
        except curator.NoCredits:
            raise Stop("credits")
        except curator.ApiDown:
            raise Stop("down")
        except anthropic.BadRequestError as exc:     # модель не умеет веб-поиск — дальше ищем другой
            log.warning("Архив: поиск через %s не принят: %r", model, exc)
            st["search_model"] = None
            continue
        except Exception as exc:
            log.warning("Архив: ответ поиска не разобрался: %r", exc)
            return None
        st["search_model"] = model
        urls = out.get("urls") if isinstance(out, dict) else None
        if not isinstance(urls, list):
            return None
        urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
        return [u for u in urls if not any(h in u for h in SKIP_HOSTS)][:4]
    return None


def _small_from_bytes(raw: bytes, p: Path) -> bool:
    try:
        im = Image.open(io.BytesIO(raw))
        if max(im.size) < 400:
            return False
        im.draft("RGB", (1000, 1000))            # JPEG распаковывается сразу в уменьшенном виде — память
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1000, 1000))
        im.save(p, "JPEG", quality=88)
        return True
    except Exception:
        return False


async def _fetch_small(client: httpx.AsyncClient, urls: list[str], dest: Path) -> list[tuple[Path, str]]:
    """Кандидаты для сравнения: фото со страницы как есть, уменьшенные до 1000 px. Крупное качаем только для
    совпавших. → [(файл, адрес)]"""
    dest.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(4)

    async def one(i: int, u: str):
        async with sem:
            try:
                r = await client.get(u, timeout=30, follow_redirects=True)
                r.raise_for_status()
                if len(r.content) > media.MAX_BYTES:
                    return None
            except Exception:
                return None
            p = dest / f"k{i:02d}.jpg"
            return (p, u) if await asyncio.to_thread(_small_from_bytes, r.content, p) else None

    got = await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
    return [g for g in got if g]


async def find_originals(client: httpx.AsyncClient, refs: list[Path], ref_f: list, pages: list[str], folder: Path,
                         found: dict, min_long: int, min_cover: float) -> tuple[list[dict], bool]:
    """Ищет на страницах те же снимки, что refs. found — {номер фото: путь оригинала или None («совпало, но
    крупнее нет»)}, дополняется на месте. → (страницы, где совпало: [{url, title, text}], все ли страницы открылись)"""
    used: list[dict] = []
    healthy = True
    for n, url in enumerate(pages[:PAGES_MAX]):
        left = [i for i in range(len(refs)) if found.get(i) is None and ref_f[i] is not None]
        if not left:
            break
        try:
            art = await media.extract_article(client, url)
        except Exception as exc:
            log.info("Архив: страница не открылась %s: %r", url, exc)
            healthy = False
            continue
        img_urls = list(dict.fromkeys(art["image_urls"]))[:CANDS_PER_PAGE]
        cands = await _fetch_small(client, img_urls, folder / f"p{n}_{len(used)}")
        if img_urls and not cands:
            healthy = False                       # картинки есть, но не скачались — не знаем
            continue
        if not cands:
            continue
        cand_f = await asyncio.to_thread(photomatch.load_all, [p for p, _ in cands])
        ranks = await asyncio.to_thread(photomatch.ranked, [ref_f[i] for i in left], cand_f, min_cover)
        hit = False
        for li, order in ranks.items():
            i = left[li]
            hit = True
            got = None
            for ci in order[:3]:                  # лучший кандидат не скачался в полном размере — следующий
                hi = await media.download_images(client, [cands[ci][1]], folder / f"hi{i}_{ci}", min_long=min_long,
                                                 min_short=0, max_ratio=4.0, max_keep=1)
                if hi:
                    got = hi[0]
                    break
            if got is not None or i not in found:
                found[i] = got                    # None — тот же снимок, но крупнее нет
        if hit:
            used.append({"url": url, "title": art.get("title") or "", "text": (art.get("text") or "")[:1500]})
    return used, healthy


def stamped(paths: list[Path]) -> list[Image.Image]:
    out = []
    for p in paths:
        im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        out.append(brand.stamp(im) if brand.enabled() else im)
    return out


def _store_files(rid: int | str, files: dict[str, bytes]) -> list[str]:
    d = WORK / "up" / str(rid)
    shutil.rmtree(d, ignore_errors=True)
    for rel, b in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    return list(files)


# ======================= 1. старые записи сайта =======================

def _old(rec: dict) -> bool:
    return (not rec.get("tmp") and rec.get("src") != "ig" and (rec.get("img") or {}).get("v", 0) < 2
            and rec.get("id", 0) < IG_BASE)


def _cut_strip(raw: bytes, segs: list, folder: Path) -> list[Path]:
    strip = Image.open(io.BytesIO(raw)).convert("RGB")
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n, g in enumerate(segs):
        y0, w, h = int(g[0]), int(g[1]), int(g[2])
        if y0 + h > strip.height + 2:
            break
        p = folder / f"ref{n:02d}.jpg"
        strip.crop((0, y0, min(w, strip.width), min(y0 + h, strip.height))).save(p, quality=92)
        out.append(p)
    return out


async def _site_refs(client: httpx.AsyncClient, rec: dict, folder: Path) -> list[Path] | None:
    """Фото записи с сайта: лента img/p, нарезанная по segs. Обложка вместо ленты — только если ленты нет
    (404) или фото одно. None — сайт не ответил (повтор позже)."""
    key = rec.get("ik") or rec["id"]
    segs = (rec.get("img") or {}).get("segs") or []
    try:
        r = await client.get(f"{sitepub.SITE_URL}/img/p/{key}.jpg", timeout=40)
        if r.status_code == 200:
            refs = await asyncio.to_thread(_cut_strip, r.content, segs, folder)
            return refs or None
        if r.status_code != 404 and len(segs) > 1:
            return None
    except Exception as exc:
        log.info("Архив: лента записи %s не скачалась: %r", rec["id"], exc)
        return None
    try:
        r = await client.get(f"{sitepub.SITE_URL}/img/c/{key}.jpg", timeout=40)
        r.raise_for_status()
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / "ref00.jpg"
        p.write_bytes(r.content)
        return [p]
    except Exception:
        return None


async def _bot_post(rid: int):
    async with db.connect() as c:
        cur = await c.execute("SELECT * FROM posts WHERE channel_msg_id=? AND status='published' "
                              "ORDER BY id DESC LIMIT 1", (rid,))
        return await cur.fetchone()


def _title_query(rec: dict) -> tuple[str, str]:
    """→ (название, «название автор» для поиска) — по-английски, если есть."""
    t = rec.get("t") or {}
    head = (t.get("en") or t.get("ru") or "")
    parts = [x.strip() for x in head.split("//") if x.strip()]
    title = parts[0] if parts else head
    return title, " ".join(parts[:2]) or title


def _via(rec: dict) -> str | None:
    for c in rec.get("cr") or []:
        if c and c[0] == "source":
            return (c[3] if len(c) > 3 and c[3] else c[1]) or None
    return None


async def _site_one(st: dict, client: httpx.AsyncClient, rec: dict) -> str:
    """Одна старая запись. → fixed | hidden | kept | error | budget (Stop — наружу)."""
    rid = rec["id"]
    if any(len(g) > 3 and g[3] for g in (rec.get("img") or {}).get("segs") or []):
        return "kept"                                 # запись с видео — не трогаем
    folder = WORK / "s" / str(rid)
    shutil.rmtree(folder, ignore_errors=True)
    try:
        refs = await _site_refs(client, rec, folder / "ref")
        if not refs:
            return "error"
        ref_f = await asyncio.to_thread(photomatch.load_all, refs)
        usable = sum(f is not None for f in ref_f)
        if not usable:
            return "kept"                             # фото без деталей — сравнивать нечем, скрывать не за что
        with Image.open(refs[0]) as im0:
            ref_long = max(im0.size)
        min_long = max(1000, math.ceil(1.5 * ref_long))
        post = await _bot_post(rid)
        own = post is not None
        pages = []
        if own and str(post["url"] or "").startswith("http") and (post["source"] or "") not in ("request", "notes", "digest"):
            pages.append(post["url"])
        pages += [c[2] for c in rec.get("cr") or [] if len(c) > 2 and str(c[2] or "").startswith("http")
                  and not any(h in c[2] for h in SKIP_HOSTS)]
        found: dict = {}
        await find_originals(client, refs, ref_f, list(dict.fromkeys(pages)), folder, found, min_long, COVER_SITE)
        title, query = _title_query(rec)
        healthy = True
        if not any(v for v in found.values()):
            base = wp_site(_via(rec))
            if base:
                urls = await wp_search(client, base, title)
                if urls:
                    await find_originals(client, refs, ref_f, urls, folder, found, min_long, COVER_SITE)
                    if any(v for v in found.values()):
                        st["site"]["free"] += 1
        if not found:                                 # не совпало вообще ничего — платный поиск
            if not can_spend(st, SEARCH_COST):
                return "budget"
            via = _via(rec)
            urls = await web_search(st, query + (f" ({via})" if via else ""))
            if urls is None:
                return "error"
            st["site"]["paid"] += 1
            if urls:
                _, healthy = await find_originals(client, refs, ref_f, urls, folder, found, min_long, COVER_SITE)
        hi = {i: p for i, p in found.items() if p}
        if hi:
            if len(found) >= math.ceil(usable / 2):   # совпало много — в записи только совпавшие
                paths = [found[i] or refs[i] for i in sorted(found)]
            else:                                     # совпало мало — заменяем совпавшие, остальное как было
                paths = [hi.get(i) or refs[i] for i in range(len(refs))]
            photos = await asyncio.to_thread(stamped, paths)
            key = rec.get("ik") or rid
            new_key = key if str(key).startswith("b") else f"{rid}r"     # новый адрес картинок — без старого кэша
            img, files = await asyncio.to_thread(sitepub.sitebuild.make_images, photos, new_key)
            st["pending"]["put"][str(rid)] = {"replace": True, "img": img, "ik": new_key,
                                              "files": await asyncio.to_thread(_store_files, rid, files)}
            return "fixed"
        if found or own:
            return "kept"            # снимок узнан (только крупнее нет) или пост бота — запись законная
        if not healthy:
            return "error"           # часть страниц не открылась — не знаем, не скрываем
        st["pending"]["hide"][str(rid)] = "оригиналы фото не нашлись"
        return "hidden"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def _site_step(st: dict, client: httpx.AsyncClient, D: dict) -> bool | None:
    """Следующая старая запись. → True — дальше, False — этап закончен или остановлен, None — сбой, до
    следующего захода."""
    done = set(st["site"]["done"])
    errs = st["site"]["errors"]
    todo = [o for o in D["objects"] if _old(o) and o["id"] not in done and str(o["id"]) not in st["pending"]["put"]
            and str(o["id"]) not in st["pending"]["hide"]]
    if not todo:
        _guard(st, final=True)
        return False
    # от свежих к старым (у свежих источник известен); запись со сбоем — в конец очереди
    rec = max(todo, key=lambda o: (-errs.get(str(o["id"]), 0), o["id"]))
    try:
        res = await _site_one(st, client, rec)
    except Stop:
        raise
    except Exception:
        log.exception("Архив: запись %s", rec["id"])
        res = "error"
    if res == "budget":
        _pause("budget")
        return False
    if res == "error":
        errs[str(rec["id"])] = errs.get(str(rec["id"]), 0) + 1
        if errs[str(rec["id"])] < TRIES:
            return None
        res = "kept"                                # три сбоя — оставляем как есть
    st["site"]["done"].append(rec["id"])
    st["site"][res] += 1
    if res in ("fixed", "hidden") and not await _bot_post(rec["id"]):
        st["site"]["window"] = (st["site"]["window"] + [1 if res == "fixed" else 0])[-GUARD_N:]
        _guard(st)
    return True


def _guard(st: dict, final: bool = False) -> None:
    """Предохранитель по «чужим» записям: окно из последних GUARD_N. final — этап кончился, окно может быть меньше."""
    c = ctl()
    if c["guard_user"]:
        return
    w = st["site"]["window"]
    if len(w) < GUARD_N and not (final and w):
        return
    if sum(w) / len(w) < GUARD_RATE:
        if not (c["paused"] and c["why"] == "guard"):
            _pause("guard")
            set_ctl(guard_ok=False)
    elif not c["guard_ok"]:
        set_ctl(guard_ok=True)


# ======================= 2–4. Instagram =======================

FIELDS = "id,caption,media_type,media_url,permalink,timestamp,children{media_type,media_url}"


def _images_of(m: dict) -> list[str]:
    if m.get("media_type") == "CAROUSEL_ALBUM":
        kids = (m.get("children") or {}).get("data") or []
        return [k["media_url"] for k in kids if k.get("media_type") == "IMAGE" and k.get("media_url")][:10]
    if m.get("media_type") == "IMAGE" and m.get("media_url"):
        return [m["media_url"]]
    return []


async def _images(client: httpx.AsyncClient, m: dict) -> list[str]:
    """Фото поста; у карусели без раскрытых children — отдельным запросом."""
    imgs = _images_of(m)
    if not imgs and m.get("media_type") == "CAROUSEL_ALBUM":
        try:
            kids = await instagram._get(client, f"{m['id']}/children", fields="media_type,media_url")
            imgs = _images_of({"media_type": "CAROUSEL_ALBUM", "children": kids})
        except Exception:
            log.info("Архив: фото карусели %s не получены", m.get("id"))
    return imgs


def _thumb_bytes(raw: bytes, dest: Path, side: int) -> bool:
    try:
        im = Image.open(io.BytesIO(raw))
        im.draft("RGB", (side, side))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((side, side))
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "JPEG", quality=88)
        return True
    except Exception:
        return False


async def _thumb(client: httpx.AsyncClient, url: str, dest: Path, side: int = 512) -> bool:
    try:
        r = await client.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except Exception:
        return False
    return await asyncio.to_thread(_thumb_bytes, r.content, dest, side)


async def _ig_fetch(st: dict, client: httpx.AsyncClient) -> bool:
    """Страница постов Instagram. → False, когда всё забрано."""
    if not instagram.configured():
        _pause("noig")
        return False
    ig = st["ig"]
    params = {"fields": FIELDS, "limit": 25}
    if ig.get("cursor"):
        params["after"] = ig["cursor"]
    data = await instagram._get(client, f"{instagram.ENV_USER}/media", **params)
    rows = data.get("data") or []
    for m in rows:
        imgs = await _images(client, m)
        status = "new" if imgs else "video"
        if imgs and not await _thumb(client, imgs[0], WORK / "ig" / f"{m['id']}.jpg"):
            status = "nothumb"
        async with db.connect() as c:
            await c.execute(
                "INSERT OR IGNORE INTO archive_ig(media_id, ts, caption, permalink, mtype, status, data, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (m["id"], m.get("timestamp"), m.get("caption") or "", m.get("permalink"), m.get("media_type"),
                 status, json.dumps({"images": imgs}), db.now()))
            await c.commit()
    ig["fetched"] += len(rows)
    paging = data.get("paging") or {}
    nxt = (paging.get("cursors") or {}).get("after")
    if not rows or not paging.get("next") or not nxt:
        await _dedup_known(st)
        return False
    ig["cursor"] = nxt
    return True


def _tokens(s: str) -> set[str]:
    stop = {"the", "and", "for", "with", "house", "дом", "of", "in", "by"}
    return {w for w in re.findall(r"[a-zа-яё0-9]{3,}", (s or "").lower()) if w not in stop}


def headline_of(caption: str) -> str:
    return (caption or "").strip().split("\n", 1)[0].strip()


async def _dedup_known(st: dict) -> None:
    """Посты, которые бот сам выложил из канала, и посты с тем же заголовком, что у записи сайта, — мимо."""
    D = await sitepub.load()
    titles = [(_tokens((o.get("t") or {}).get("en", "")), _tokens((o.get("t") or {}).get("ru", ""))) for o in D["objects"]]
    async with db.connect() as c:
        cur = await c.execute("SELECT media_id FROM ig_posts WHERE media_id IS NOT NULL")
        mine = {r["media_id"] for r in await cur.fetchall()}
        cur = await c.execute("SELECT media_id, caption FROM archive_ig WHERE status='new'")
        rows = await cur.fetchall()
    marks = []
    for r in rows:
        why = None
        if r["media_id"] in mine:
            why = "dup_bot"
        else:
            head = _tokens(headline_of(r["caption"]))
            if len(head) >= 2 and any(t and len(head & t) >= 2 and len(head & t) / len(head | t) >= 0.6
                                      for en, ru in titles for t in (en, ru)):
                why = "dup_site"
        if why:
            marks.append((why, db.now(), r["media_id"]))
    if marks:
        async with db.connect() as c:
            await c.executemany("UPDATE archive_ig SET status=?, updated_at=? WHERE media_id=?", marks)
            await c.commit()
    st["ig"]["dups"] += len(marks)


def _curate_system() -> str:
    taste = "\n\n".join(x for x in (curator._section(curator.PROFILE, 3), curator._section(curator.PROFILE, 6)) if x)
    return f"""Ты — редактор-куратор журнала AHMAG. Перед тобой старый пост из Instagram журнала (подпись и первое фото). Реши, стоит ли переносить его в архив на сайте theahmag.com. Сайт — витрина вкуса журнала: сюда идёт только сильное.

{taste}

Оцени по шкале 0–10 целым числом, будь строгим: 7 и выше — то, что достойно архива. Учитывай и вкус, и качество фото на превью.
single — пост про одну конкретную работу (здание, интерьер, произведение, серию фотографа, фильм), а не подборка, цитата, анонс или реклама.
query — поисковый запрос по-английски, по которому найдётся оригинальная публикация этой работы: название и автор (бюро, художник, фотограф, режиссёр), можно город. via — издание или сайт, если он назван в подписи (via: …), иначе null.

Верни ТОЛЬКО JSON: {{"score": 0, "single": true, "category": "architecture|art|photography|archive|cinema", "query": "...", "via": null, "why": "3–8 слов по-русски"}}"""


def _num(v) -> float:
    m = re.search(r"\d+(?:[.,]\d+)?", str(v if v is not None else ""))
    return float(m.group(0).replace(",", ".")) if m else 0.0


def _build_requests(rows: list, system: str) -> list[dict]:
    reqs = []
    for r in rows:
        cap = re.sub(r"(#\w+\s*)+$", "", (r["caption"] or "").strip())[:1500]
        content = [{"type": "text", "text": f"# Подпись\n{cap or '(без подписи)'}\n\n# Первое фото"},
                   curator._img(WORK / "ig" / f"{r['media_id']}.jpg", 384)]
        reqs.append({"custom_id": f"ig{r['media_id']}",
                     "params": {"model": config.CLAUDE_MODEL, "max_tokens": 300, "system": curator._system(system),
                                "messages": [{"role": "user", "content": content}]}})
    return reqs


async def _ig_curate(st: dict) -> bool | None:
    """Пакеты оценки: создать, дождаться, разобрать. → True — дальше, False — этап закончен, None — ждём пакет."""
    ig = st["ig"]
    if ig.get("batch"):
        ended, _ = await curator.batch_status(ig["batch"])
        if not ended:
            return None
        # сначала все ответы (учёт расхода сам пишет в базу), потом одной записью разбираем
        results = [x async for x in curator.batch_results(ig["batch"])]
        marks = []
        for cid, data, err in results:
            mid = str(cid)[2:]
            try:
                if data is None:
                    marks.append(("error", None, str(err)[:200], "{}", mid))
                    continue
                score = _num(data.get("score"))
                ok = score >= MIN_SCORE and data.get("single") is not False and bool(data.get("query"))
                marks.append(("selected" if ok else "low", score, str(data.get("why") or "")[:200],
                              json.dumps({k: data.get(k) for k in ("category", "query", "via", "single")},
                                         ensure_ascii=False), mid))
                ig["selected" if ok else "low"] += 1
            except Exception:
                log.warning("Архив: ответ оценки %s не разобрался", mid, exc_info=True)
                marks.append(("error", None, "ответ не разобрался", "{}", mid))
        async with db.connect() as c:
            await c.executemany("UPDATE archive_ig SET status=?, score=?, note=?, "
                                "data=json_patch(COALESCE(data,'{}'), ?), updated_at=datetime('now') WHERE media_id=?",
                                marks)
            await c.execute("UPDATE archive_ig SET status='new' WHERE status='curating'")
            await c.commit()
        ig["batch"] = None
        save_state(st)
        return True
    async with db.connect() as c:
        await c.execute("UPDATE archive_ig SET status='new' WHERE status='curating'")   # пакета нет — ничего не «на оценке»
        await c.commit()
        cur = await c.execute("SELECT media_id, caption FROM archive_ig WHERE status='new' ORDER BY ts DESC")
        rows = await cur.fetchall()
    rows = [r for r in rows if (WORK / "ig" / f"{r['media_id']}.jpg").exists()][:BATCH_MAX]
    if not rows:
        return False
    room = int((ctl()["cap"] - spent(st)) / CURATE_COST) - 1
    if room < len(rows):
        rows = rows[:max(0, room)]
        if not rows:
            _pause("budget")
            return False
    reqs = await asyncio.to_thread(_build_requests, rows, _curate_system())
    bid = await curator.batch_create(reqs, check_budget=False)
    ig["batch"] = bid
    save_state(st)                                  # номер пакета — сразу на диск: перезапуск не закажет второй
    async with db.connect() as c:
        await c.executemany("UPDATE archive_ig SET status='curating' WHERE media_id=?", [(r["media_id"],) for r in rows])
        await c.commit()
    log.info("Архив: на оценку ушло %s постов Instagram (пакет %s)", len(reqs), bid)
    return None


RECORD_SYSTEM = f"""Ты готовишь запись для архива сайта theahmag.com (журнал AHMAG: архитектура, интерьеры, искусство, фотография, кино, архивные находки) по старому посту из Instagram журнала и странице, где нашлись оригиналы фото. Запись на двух языках.

{voice.RULES}

Правила
- Только факты из подписи и со страницы. Ничего не выдумывай: неизвестные год, место, автор — null.
- Текст: 1–2 коротких абзаца, 250–600 знаков на каждом языке. Русский пиши сразу по-русски, живо, как автор журнала, а не переводом. Английский — простой, конкретный, тем же голосом. В тексте должно быть то, чего не видно на фото: история, контекст, деталь. Без пересказа пресс-релиза и перечня материалов.
- Заголовок: обычно «Название // Автор // Место, Год». Названия работ и имена в латинице — как в оригинале; русские имена в английском — стандартной транслитерацией; места переводятся.
- People — только авторы (architect, studio, artist, photographer, director, designer, writer). Есть в списке имён — бери его id; нет — новый id: латиница, строчные, через дефис, и обязательно ru и en.
- Страны — id из списка; новой — английский slug и названия на двух языках.
- Год: "ru"/"en" как написано, "sort" — первый год числом; века → первый год. Нет — null.
- Кредиты: pr — автор проекта, ph — фотограф, via — издание или сайт, где нашлись фото.

Верни ТОЛЬКО JSON:
{{"category": "architecture|art|photography|archive|cinema",
 "title": {{"ru": "...", "en": "..."}},
 "people": [{{"id": "...", "ru": "...", "en": "...", "role": "architect"}}],
 "place": {{"ru": "...", "en": "..."}},
 "year": {{"ru": "...", "en": "...", "sort": 1932}},
 "countries": [{{"id": "japan", "ru": "Япония", "en": "Japan"}}],
 "text_ru": ["абзац", "..."], "text_en": ["paragraph", "..."],
 "credits": {{"pr": null, "ph": null, "via": null}}}}"""


def _rec_from(out: dict, D: dict, rid: int, d: str, permalink: str, page: dict | None) -> dict | None:
    """Ответ Claude → запись сайта (как sitepub.object_record) с полями src/ig. Без заголовка — None."""
    title = sitepub._bi(out.get("title"))
    if not title:
        return None
    people, new_people = [], {}
    for p in out.get("people") or []:
        if not isinstance(p, dict):
            continue
        pid = sitepub.slug(str(p.get("id") or p.get("en") or p.get("ru") or ""))
        if pid == "x" or pid in (x["id"] for x in people):
            continue
        if pid not in D["people"]:
            if not (p.get("ru") or p.get("en")):
                continue                         # без имени в указатель не попадёт — и в запись не ставим
            role = p.get("role") if p.get("role") in sitepub.ROLES else "architect"
            new_people[pid] = {"ru": str(p.get("ru") or p.get("en")), "en": str(p.get("en") or p.get("ru")),
                               "roles": [role], "objs": []}
        people.append({"id": pid})
    countries, new_countries = [], {}
    for c in out.get("countries") or []:
        cid = sitepub.slug(str((c.get("id") if isinstance(c, dict) else c) or ""))
        if cid == "x" or cid in countries:
            continue
        if cid not in D["countries"]:
            if not (isinstance(c, dict) and c.get("ru") and c.get("en")):
                continue
            new_countries[cid] = {"ru": str(c["ru"]), "en": str(c["en"]), "n": 0}
        countries.append(cid)
    y = None
    year = out.get("year") if isinstance(out.get("year"), dict) else None
    if year and (year.get("ru") or year.get("en")):
        try:
            sort = int(year.get("sort")) if year.get("sort") is not None else None
        except (TypeError, ValueError):
            sort = None
        y = {"ru": str(year.get("ru") or year.get("en")), "en": str(year.get("en") or year.get("ru")), "s": sort}

    def paras(v) -> list[str]:
        v = [v] if isinstance(v, str) else (v or [])
        return [str(x).strip() for x in v if str(x).strip()]
    ru, en = paras(out.get("text_ru")), paras(out.get("text_en"))
    if len(en) != len(ru):
        en = [" ".join(en)] if en and len(ru) == 1 else []
    cred = out.get("credits") or {}
    cr = []
    for k, role in (("pr", "project"), ("ph", "photo"), ("via", "source")):
        name = str(cred.get(k) or "").strip()
        if name and name.lower() != "null":
            cr.append([role, name, page["url"] if (k == "via" and page) else None])
    cat = config.CATEGORY_ALIASES.get(out.get("category") or "", out.get("category") or "")
    rec = {"id": rid, "d": d, "cats": [cat if cat in sitepub.CATS else "architecture"], "co": countries,
           "t": title, "p": people, "pl": sitepub._bi(out.get("place")), "y": y,
           "per": sitepub.period_of(y["s"]) if y else None,
           "b": {"ru": ru, "en": en if len(en) == len(ru) else []}, "cr": cr, "src": "ig", "ig": permalink}
    if ru:
        rec["s"] = {"ru": sitepub.first_sentence(ru[0]), "en": sitepub.first_sentence(en[0]) if en else sitepub.first_sentence(ru[0])}
    for k in ("pl", "y", "per"):
        if not rec[k]:
            rec.pop(k)
    rec["_new"] = {"people": new_people, "countries": new_countries}
    return rec


async def _covers_ready(client: httpx.AsyncClient, D: dict) -> None:
    """Особые точки обложек всех записей сайта — чтобы узнать пост Instagram, который уже есть на сайте."""
    for o in D["objects"]:
        rid, key = o["id"], (o.get("ik") or o["id"])
        if rid in _covers or o.get("tmp"):
            continue
        p = WORK / "covers" / f"{key}.jpg"
        if not p.exists():
            await _thumb(client, f"{sitepub.SITE_URL}/img/c/{key}.jpg", p, 900)
        _covers[rid] = await asyncio.to_thread(photomatch.features, p) if p.exists() else None


async def _next_ig_id(st: dict, D: dict) -> int:
    """Номер новой записи из Instagram: больше всех уже занятых (на сайте, среди скрытых, ждущих и в базе)."""
    taken = [o["id"] for o in D["objects"] if o["id"] >= IG_BASE]
    taken += [int(k) for k in _load_hidden() if k.isdigit() and int(k) >= IG_BASE]
    taken += [int(k) for k in st["pending"]["put"] if k.isdigit() and int(k) >= IG_BASE]
    async with db.connect() as c:
        cur = await c.execute("SELECT MAX(rec_id) m FROM archive_ig")
        row = await cur.fetchone()
    if row and row["m"]:
        taken.append(int(row["m"]))
    return max([IG_BASE] + taken) + 1


async def _ig_import_one(st: dict, client: httpx.AsyncClient, row, D: dict) -> str:
    """→ imported | notfound | dup_site | error | budget (Stop — наружу)"""
    mid = row["media_id"]
    data = json.loads(row["data"] or "{}")
    folder = WORK / "i" / mid
    shutil.rmtree(folder, ignore_errors=True)
    try:
        try:                                           # адреса фото в Instagram живут недолго — берём свежие
            m = await instagram._get(client, mid, fields="id,media_type,media_url,children{media_type,media_url}")
            urls = await _images(client, m)
        except Exception:
            urls = data.get("images") or []
        refs = []
        for n, u in enumerate(urls):
            p = folder / "ref" / f"ref{n:02d}.jpg"
            if await _thumb(client, u, p, 1080):
                refs.append(p)
        if not refs:
            return "error"
        ref_f = await asyncio.to_thread(photomatch.load_all, refs)
        usable = sum(f is not None for f in ref_f)
        if not usable:
            return "notfound"
        await _covers_ready(client, D)
        if await asyncio.to_thread(photomatch.any_match, ref_f[:3], list(_covers.values()), COVER_IG):
            return "dup_site"
        via = data.get("via") or None
        query = data.get("query") or headline_of(row["caption"])
        found: dict = {}
        pages: list[dict] = []
        base = wp_site(via)
        if base:
            urls = await wp_search(client, base, query)
            if urls:
                pages, _ = await find_originals(client, refs, ref_f, urls, folder, found, config.MIN_LONG_SIDE, COVER_IG)
        need = math.ceil(usable / 2)
        if sum(1 for v in found.values() if v) < need:
            if not can_spend(st, SEARCH_COST + WRITE_COST):
                return "budget"
            urls = await web_search(st, query + (f" ({via})" if via else ""))
            if urls is None:
                return "error"
            if urls:
                more, _ = await find_originals(client, refs, ref_f, urls, folder, found, config.MIN_LONG_SIDE, COVER_IG)
                pages += more
        hi = [found[i] for i in sorted(found) if found[i]]
        if len(hi) < need:
            return "notfound"
        if not can_spend(st, WRITE_COST):
            return "budget"
        page = pages[0] if pages else None
        content = (f"# Подпись поста в Instagram\n{(row['caption'] or '').strip()[:2500]}\n\n"
                   + (f"# Страница с оригиналами\n{page['url']}\n{page['title']}\n{page['text']}\n\n" if page else "")
                   + f"# Рубрика по оценке\n{data.get('category') or '—'}\n\n"
                   f"# Имена, которые уже есть на сайте (id | рус | англ)\n{sitepub._people_list(D)}\n\n"
                   f"# Страны на сайте (id | рус | англ)\n{sitepub._countries_list(D)}")
        try:
            out = await curator._call(content, system=RECORD_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=3000)
        except curator.NoCredits:
            raise Stop("credits")
        except curator.ApiDown:
            raise Stop("down")
        rid = await _next_ig_id(st, D)
        rec = _rec_from(out, D, rid, (row["ts"] or db.now())[:10], row["permalink"] or "", page)
        if not rec:
            return "error"
        photos = await asyncio.to_thread(stamped, hi)
        img, files = await asyncio.to_thread(sitepub.sitebuild.make_images, photos, rid)
        rec["img"] = img
        st["pending"]["put"][str(rid)] = {"replace": False, "rec": rec,
                                          "files": await asyncio.to_thread(_store_files, rid, files)}
        save_state(st)                                 # запись ждёт выкладки — до отметки в базе
        _covers[rid] = await asyncio.to_thread(photomatch.features, photos[0])
        async with db.connect() as c:
            await c.execute("UPDATE archive_ig SET rec_id=? WHERE media_id=?", (rid, mid))
            await c.commit()
        return "imported"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def _ig_import(st: dict, client: httpx.AsyncClient, D: dict) -> bool | None:
    async with db.connect() as c:
        cur = await c.execute("SELECT * FROM archive_ig WHERE status='selected' ORDER BY score DESC, ts DESC LIMIT 1")
        row = await cur.fetchone()
    if not row:
        return False
    try:
        res = await _ig_import_one(st, client, row, D)
    except Stop:
        raise
    except Exception:
        log.exception("Архив: пост Instagram %s", row["media_id"])
        res = "error"
    if res == "budget":
        _pause("budget")
        return False
    if res == "error":
        data = json.loads(row["data"] or "{}")
        tries = int(data.get("_tries") or 0) + 1
        status = "selected" if tries < TRIES else "error"
        async with db.connect() as c:
            await c.execute("UPDATE archive_ig SET status=?, score=CASE WHEN ?='selected' THEN score - 0.01 ELSE score END, "
                            "data=json_set(COALESCE(data,'{}'), '$._tries', ?), updated_at=? WHERE media_id=?",
                            (status, status, tries, db.now(), row["media_id"]))
            await c.commit()
        return None
    async with db.connect() as c:
        await c.execute("UPDATE archive_ig SET status=?, updated_at=? WHERE media_id=?", (res, db.now(), row["media_id"]))
        await c.commit()
    st["ig"][{"imported": "imported", "notfound": "notfound", "dup_site": "dups"}[res]] += 1
    return True


# ======================= выкладка на сайт =======================

async def commit(st: dict, force: bool = False) -> int:
    """Накопленное — на сайт одной выкладкой: обновлённые фото, новые записи, скрытые записи.
    Только в паузе между слотами (или force). Скрытия — только когда предохранитель разрешил. → сколько ушло."""
    c = ctl()
    hides_ok = (c["guard_ok"] or c["guard_user"]) and not (c["paused"] and c["why"] == "guard")
    put = st["pending"]["put"]
    hide = st["pending"]["hide"] if hides_ok else {}
    if not put and not hide:
        return 0
    if not force and not sitepub._calm(15):
        return 0
    if not await sitepub.enabled():
        return 0
    async with sitepub._lock:
        D = await sitepub.load()
        files: list[tuple[str, bytes]] = []
        applied = 0
        for sid, x in put.items():
            folder = WORK / "up" / sid
            idx = next((i for i, o in enumerate(D["objects"]) if str(o["id"]) == sid), None)
            if x.get("replace"):
                if idx is None:
                    continue                          # запись убрали с сайта, пока мы искали
                D["objects"][idx]["img"] = x["img"]
                D["objects"][idx]["ik"] = x["ik"]
            else:
                rec = json.loads(json.dumps(x["rec"]))
                if idx is not None and D["objects"][idx].get("ig") != rec.get("ig"):
                    log.warning("Архив: номер %s уже занят другой записью — пропускаю", sid)
                    continue
                new = rec.pop("_new", None) or {}
                for k, v in (new.get("people") or {}).items():
                    D["people"].setdefault(k, v)
                for k, v in (new.get("countries") or {}).items():
                    D["countries"].setdefault(k, v)
                if idx is not None:
                    D["objects"].pop(idx)
                D["objects"].append(rec)
            files += [(rel, (folder / rel).read_bytes()) for rel in x.get("files") or [] if (folder / rel).exists()]
            applied += 1
        hidden = _load_hidden()
        for sid, reason in hide.items():
            idx = next((i for i, o in enumerate(D["objects"]) if str(o["id"]) == sid), None)
            if idx is None:
                continue
            rec = D["objects"].pop(idx)
            hidden[sid] = {"rec": rec, "reason": reason, "at": db.now(),
                           "people": {p["id"]: D["people"][p["id"]] for p in rec.get("p") or [] if p["id"] in D["people"]},
                           "countries": {c: D["countries"][c] for c in rec.get("co") or [] if c in D["countries"]}}
            applied += 1
        _save_hidden(hidden)                          # скрытые — на диск до выкладки: запись не потеряется
        sitepub.recount(D)
        sitepub.order(D)
        batch, manifest = await asyncio.to_thread(sitepub.build_diff, D)
        old = json.loads(sitepub.MANIFEST.read_text("utf-8")) if sitepub.MANIFEST.exists() else {}
        gone = [r for r in old if r not in manifest and r.endswith("/index.html")]
        await asyncio.to_thread(sitepub.upload, files + batch, gone)
        sitepub._save(D, manifest)
    for sid in put:
        shutil.rmtree(WORK / "up" / sid, ignore_errors=True)
    st["pending"] = {"put": {}, "hide": {k: v for k, v in st["pending"]["hide"].items() if k not in hide}}
    save_state(st)
    log.info("Архив: на сайт ушло изменений: %s", applied)
    return applied


async def restore(rid: int) -> str:
    """Скрытая запись — обратно на сайт."""
    async with sitepub._lock:
        hidden = _load_hidden()
        x = hidden.get(str(rid))
        if not x:
            return f"Записи {rid} среди скрытых нет."
        D = await sitepub.load()
        if any(o["id"] == rid for o in D["objects"]):
            hidden.pop(str(rid))
            _save_hidden(hidden)
            return f"Запись {rid} и так на сайте."
        for k, v in (x.get("people") or {}).items():
            if v:
                D["people"].setdefault(k, {**v, "objs": []})
        for k, v in (x.get("countries") or {}).items():
            if v:
                D["countries"].setdefault(k, {**v, "n": 0})
        D["objects"].append(x["rec"])
        sitepub.recount(D)
        sitepub.order(D)
        batch, manifest = await asyncio.to_thread(sitepub.build_diff, D)
        await asyncio.to_thread(sitepub.upload, batch)
        sitepub._save(D, manifest)
        hidden.pop(str(rid))
        _save_hidden(hidden)
    title = (x["rec"].get("t") or {}).get("ru", "")
    return f"Запись {rid} снова на сайте: {title}"


# ======================= ход работы =======================

PAUSE_TEXT = {
    "budget": "кончился бюджет архива",
    "guard": "оригиналы находятся слишком редко — скрытия не выложены, посмотри «🙈 Скрытые» и реши",
    "noig": "Instagram не настроен",
    "igerr": "Instagram не отдал список постов",
    "credits": "на счёте Anthropic нет денег",
    "user": "пауза",
}


async def _safe_commit(st: dict, force: bool = False) -> None:
    try:
        await commit(st, force)
    except Exception:
        log.exception("Архив: выкладка на сайт")


async def tick(bot: Bot) -> None:
    """Раз в 10 минут: работать до TICK_SECONDS, потом выложить накопленное."""
    global _running
    if _running or not ENABLED or not await sitepub.enabled():
        return
    st = load_state()
    if st is None:
        st = _new_state()
        save_state(st)
        await _say(bot, "🗄 <b>Архив сайта: начинаю</b>\nСначала старые записи сайта: ищу оригиналы фото, без них "
                        "запись скрываю (первые скрытия — только после проверки на 20 записях). Потом посты Instagram: "
                        f"отбор, оригиналы, перенос. Бюджет — ${ctl()['cap']:.0f}. Ход — /archive.")
    c = ctl()
    if st["phase"] == "done" or c["paused"]:
        await _safe_commit(st)            # накопленное — всё равно на сайт (скрытия — по предохранителю)
        await _tell_pause(bot, st)
        return
    _running = True
    _acc[0] = 0.0
    token = curator.cost_sink(_acc)
    moves: list[tuple[str, str]] = []
    try:
        deadline = time.monotonic() + TICK_SECONDS
        D = await sitepub.load()
        async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}, follow_redirects=True) as client:
            while time.monotonic() < deadline and not ctl()["paused"] and st["phase"] != "done":
                ph = before = st["phase"]
                r: bool | None = True
                if ph == "site":
                    r = await _site_step(st, client, D)
                    if r is False and not ctl()["paused"]:
                        st["phase"] = "ig_fetch"
                elif ph == "ig_fetch":
                    try:
                        r = await _ig_fetch(st, client)
                    except instagram.IGError as exc:     # токен, права — само не пройдёт: пауза, автору — один раз
                        _pause("igerr", str(exc)[:300])
                        r = None
                    if r is False and not ctl()["paused"]:
                        st["phase"] = "ig_curate"
                elif ph == "ig_curate":
                    r = await _ig_curate(st)
                    if r is False and not ctl()["paused"]:
                        st["phase"] = "ig_import"
                elif ph == "ig_import":
                    r = await _ig_import(st, client, D)
                    if r is False and not ctl()["paused"]:
                        st["phase"] = "done"
                        st["finished"] = db.now()
                if st["phase"] != before:
                    moves.append((before, st["phase"]))
                st["spent"] += _acc[0]
                _acc[0] = 0.0
                save_state(st)
                if len(st["pending"]["put"]) + len(st["pending"]["hide"]) >= COMMIT_EVERY:
                    if await commit(st):
                        D = await sitepub.load()
                if r is None:
                    break                                # сбой или ждём пакет — до следующего захода
    except Stop as exc:
        if str(exc) == "credits":
            _pause("credits")
        else:
            log.info("Архив: API Anthropic недоступен — продолжу в следующий заход")
    except Exception as exc:
        log.exception("Архив: сбой в заходе")
        text = curator.explain(exc)[:300]
        last = st.get("last_error") or {}
        if last.get("text") != text or time.time() - last.get("at", 0) > 6 * 3600:   # одно и то же — не чаще раза в 6 часов
            st["last_error"] = {"text": text, "at": time.time()}
            await _say(bot, f"⚠️ Архив сайта: сбой — {html.escape(text)}. Продолжу через 10 минут.")
    finally:
        curator._sink.reset(token)
        st["spent"] += _acc[0]
        _acc[0] = 0.0
        save_state(st)
        _running = False
    await _safe_commit(st)
    for before, after in moves:
        note = _phase_note(st, before, after)
        if note:
            await _say(bot, note)
    await _tell_pause(bot, st)


async def _tell_pause(bot: Bot, st: dict) -> None:
    c = ctl()
    if c["paused"] and c["why"] and not c["told"]:
        set_ctl(told=True)
        detail = f" ({html.escape(c['detail'])})" if c.get("detail") else ""
        await _say(bot, f"⏸ Архив сайта остановлен: {PAUSE_TEXT.get(c['why'], c['why'])}{detail}.\n\n"
                        + await status_text(st), kb=_kb(st))


def _phase_note(st: dict, before: str, after: str) -> str | None:
    s, ig = st["site"], st["ig"]
    if after == "done":
        return "🗄 <b>Архив сайта готов</b>\n" + _summary(st)
    if before == "site":
        return (f"🗄 <b>Старые записи сайта разобраны</b>\nФото в полном размере: {s['fixed']} · скрыто: {s['hidden']} · "
                f"оставлено как есть: {s['kept']}.\nСкрытые можно вернуть: /archive. Дальше — Instagram.")
    if before == "ig_curate":
        return (f"🗄 <b>Instagram отобран</b>\nВсего постов: {ig['fetched']} · дубли канала и сайта: {ig['dups']} · "
                f"в архив по вкусу: {ig['selected']} · не прошли: {ig['low']}.\nИщу оригиналы фото для отобранных.")
    return None


def _summary(st: dict) -> str:
    s, ig = st["site"], st["ig"]
    return (f"Старые записи: фото в полном размере {s['fixed']}, скрыто {s['hidden']}, как есть {s['kept']}.\n"
            f"Instagram: перенесено {ig['imported']}, без оригиналов {ig['notfound']}, дубли {ig['dups']}.\n"
            f"Потрачено ${spent(st):.2f} из ${ctl()['cap']:.0f}.")


async def _say(bot: Bot, text: str, kb=None) -> None:
    try:
        await bot.send_message(config.ADMIN_ID, text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        log.warning("Архив: сообщение не ушло", exc_info=True)


# ======================= /archive =======================

PHASES = {"site": "старые записи сайта", "ig_fetch": "забираю посты Instagram", "ig_curate": "отбор Instagram",
          "ig_import": "перенос Instagram", "done": "готово"}


async def status_text(st: dict | None = None) -> str:
    st = st or load_state()
    if not st:
        return "🗄 Архив сайта ещё не запускался."
    c = ctl()
    try:
        D = await sitepub.load()
        left = sum(1 for o in D["objects"] if _old(o) and o["id"] not in set(st["site"]["done"]))
    except Exception:
        left = "?"
    s, ig = st["site"], st["ig"]
    held = len(st["pending"]["hide"])
    lines = [f"🗄 <b>Архив сайта</b> · {PHASES.get(st['phase'], st['phase'])}"
             + (f" · ⏸ {PAUSE_TEXT.get(c['why'], c['why'])}" if c["paused"] else ""),
             f"Старые записи: фото в полном размере {s['fixed']} · скрыто {s['hidden']} · как есть {s['kept']}"
             + (f" · осталось {left}" if st["phase"] == "site" else ""),
             f"Instagram: забрано {ig['fetched']} · дубли {ig['dups']} · отобрано {ig['selected']} · "
             f"перенесено {ig['imported']} · без оригиналов {ig['notfound']}",
             f"Ждут выкладки на сайт: {len(st['pending']['put']) + held}"
             + (f" (из них скрытий {held} — ждут предохранителя)" if held and not (c["guard_ok"] or c["guard_user"]) else ""),
             f"Потрачено ${spent(st):.2f} из ${c['cap']:.0f}"]
    hidden = _load_hidden()
    if hidden:
        lines.append(f"\nСкрыто записей: {len(hidden)}. Вернуть — /archive restore &lt;номер&gt;, список — «🙈 Скрытые».")
    return "\n".join(lines)


def _kb(st: dict | None) -> InlineKeyboardMarkup:
    c = ctl()
    rows = []
    if st and st["phase"] != "done":
        rows.append([InlineKeyboardButton(text="▶️ Продолжить" if c["paused"] else "⏸ Пауза",
                                          callback_data="ar:go" if c["paused"] else "ar:pause")])
        if c["paused"] and c["why"] == "budget":
            rows.append([InlineKeyboardButton(text="➕ Ещё $10 бюджета", callback_data="ar:more")])
    rows.append([InlineKeyboardButton(text="🙈 Скрытые", callback_data="ar:hidden"),
                 InlineKeyboardButton(text="🔄 Обновить", callback_data="ar:st")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hidden_list(st: dict | None) -> str:
    hidden = _load_hidden()
    held = (st or {}).get("pending", {}).get("hide", {}) if st else {}
    items = [(k, ((v.get("rec") or {}).get("t") or {}).get("ru", "")) for k, v in hidden.items()]
    lines = [f"{k} · {html.escape(t[:60])}" for k, t in sorted(items, key=lambda x: -int(x[0]))]
    out = "🙈 <b>Скрытые записи</b> (номер · заголовок)\n" + ("\n".join(lines[:80]) if lines else "—")
    if len(lines) > 80:
        out += f"\n… и ещё {len(lines) - 80}"
    if held:
        out += f"\n\nЖдут скрытия (ещё на сайте): {', '.join(sorted(held, key=int))}"
    return out[:4000] + "\n\nВернуть: /archive restore &lt;номер&gt;"


@router.message(Command("archive"))
async def cmd_archive(msg: Message):
    arg = (msg.text or "").split()[1:]
    if len(arg) >= 2 and arg[0] == "restore" and arg[1].isdigit():
        rid = arg[1]
        st = load_state()
        if st and rid in st["pending"]["hide"]:          # ещё не выложено — просто не скрывать
            st["pending"]["hide"].pop(rid)
            save_state(st)
            return await msg.answer(f"Запись {rid} останется на сайте.")
        return await msg.answer(await restore(int(rid)))
    st = load_state()
    await msg.answer(await status_text(st), reply_markup=_kb(st))


@router.callback_query(F.data.startswith("ar:"))
async def on_archive(cb: CallbackQuery, bot: Bot):
    """Кнопки архива. На экране бота (сообщение с картинкой) — перерисовка экрана, в сообщении /archive — его правка."""
    from app import screen      # здесь: экран сам пользуется этим модулем
    act = cb.data.split(":", 1)[1]
    on_screen = bool(getattr(cb.message, "photo", None))
    if on_screen:
        await screen.adopt(cb.message)
    if act == "show":
        await cb.answer()
        return await (screen.show(bot, "arch") if on_screen else screen.move_down(bot, "arch"))
    st = load_state()
    c = ctl()
    if act == "pause":
        _pause("user")
        set_ctl(told=True)
        await cb.answer("На паузе")
    elif act == "more":
        set_ctl(cap=c["cap"] + 10, paused=False, why="", detail="", told=False)
        await cb.answer("Бюджет +$10")
    elif act == "go":
        upd = {"paused": False, "why": "", "detail": "", "told": False}
        if c["why"] == "guard":                       # автор посмотрел и разрешил скрывать
            upd.update(guard_ok=True, guard_user=True)
        set_ctl(**upd)
        await cb.answer("Продолжаю")
    elif act == "hidden":
        await cb.answer()
        m = await bot.send_message(config.ADMIN_ID, _hidden_list(st))
        if on_screen:                  # список уберётся, когда экран сменится
            await screen.add_temp([m.message_id])
        return
    else:
        await cb.answer()
    if on_screen:
        return await screen.show(bot, "arch")
    try:
        await cb.message.edit_text(await status_text(st), reply_markup=_kb(st))
    except Exception:
        pass


def schedule(sched, bot: Bot, guarded) -> None:
    sched.add_job(guarded(bot, "архив сайта", tick, bot), "interval", minutes=10, id="archive", max_instances=1)
