"""🎬 Рилсы — только для Instagram. Бот собирает видео, автор выкладывает его сам и кладёт музыку.

Форматы (app/reelkinds.py):
  подборка        — тематическая, отдельная от ленты: тема и 6–8 работ разных авторов; с голосом — вступление
                    на титуле и строка на каждую работу.
  детали картины  — одна картина: камера по очереди подходит к деталям, голос рассказывает историю.
  одна фотография — то же на архивной фотографии.
  масштаб         — от крошечного человека к огромному зданию: камера отъезжает, кольцо держит человека.
  разбор здания   — поверх фасада прорисовываются ось, сетка, уровни, пропорции.
  пары            — две картинки и переход: чертёж → здание, тогда / сейчас, картина и место, кадр ← картина,
                    что под слоем, какая из двух.
Факты Claude проверяет веб-поиском.

Картинки — из Wikimedia Commons, The Met и Cleveland Museum of Art (открытый доступ) в высоком разрешении,
кадры из кино — TMDB. Какая из найденных — та самая работа, Claude проверяет глазами по превью.

Язык (5.6): каждый рилс — на английском или на русском, выбор кнопкой RU / EN при создании. Русский рилс:
рассказ, подписи на экране, названия работ, подпись в Instagram и хэштеги — по-русски, читает русский голос
(🎙 Голоса → RU), антиква с кириллицей. «🌐 На другом языке» в «Переделать» — тот же объект, новый рассказ.

Расписание: REELS_DAYS (пн, ср, пт) в REELS_TIME (18:30). Накануне в REELS_ASK_TIME (10:00) бот спрашивает,
какой формат и язык собрать на завтра; выбрал — рилс собирается сразу. Не ответил до REELS_BUILD_TIME (13:00) —
бот собирает сам: формат по кругу, язык — как в прошлый раз. Потом присылает карточку: видео, работы, музыка.
✅ Беру / 🔁 Переделать / ❌ Не надо.
В день выхода в REELS_TIME приходит «Пора выкладывать»: видео файлом без сжатия, подпись одним блоком
(нажать — скопируется), треки. Выложил — «✅ Выложил». Рилс по запросу — экран «🎬 Рилсы» на пульте.

Рилсы в Telegram-канал не идут. Голос в видео уже есть, музыку автор кладёт в Instagram.
Статистика выложенных рилсов (пролистывания, время просмотра, пересылки) — «📊 Что заходит» на экране рилсов."""
import asyncio
import base64
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message
from PIL import Image, ImageDraw, ImageOps

from app import config, db, reelkinds as rk, reelplan, reelrender, screen, slots, tts, ui
from app.screen import btn

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)


def _hm(raw: str) -> tuple[int, int]:
    h, _, m = raw.strip().partition(":")
    return int(h), int(m or 0)


ENABLED = os.getenv("REELS", "1").strip().lower() not in ("0", "off", "false", "no")
DOW = [d.strip().lower() for d in os.getenv("REELS_DAYS", "mon,wed,fri").split(",") if d.strip()]
TIME = _hm(os.getenv("REELS_TIME", "18:30"))
BUILD_TIME = _hm(os.getenv("REELS_BUILD_TIME", "13:00"))
ASK_TIME = _hm(os.getenv("REELS_ASK_TIME", "10:00"))     # вопрос «какой формат завтра»; должен быть раньше сборки
TOPICS = [t.strip() for t in os.getenv("REEL_TOPICS", "art,architecture,photography,art,architecture,archive").split(",")
          if t.strip()]
MAX_ITEMS = int(os.getenv("REEL_MAX_ITEMS", "8"))
MIN_ITEMS = 5
REMIND_HOURS = 2
DOW_NUM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DOW_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
KIND_RU = {k: v["ru"] for k, v in rk.KINDS.items()}
KIND_ACC = {k: v["acc"] for k, v in rk.KINDS.items()}
MUSEUMS = os.getenv("REEL_MUSEUMS", "1").strip().lower() not in ("0", "off", "false", "no")
MAX_SIDE = int(os.getenv("REEL_MAX_SIDE", "4800"))     # картинки крупнее ужимаются: память рендера
VOICED_COLLECTIONS = os.getenv("REEL_COLLECTION_VOICE", "1").strip().lower() not in ("0", "off", "false", "no")
UA = {"User-Agent": f"AHMAG-bot/{config.VERSION} (+https://t.me/ahmag; curation bot)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
DIR = config.DATA_DIR / "reels"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,         -- collection | details | photo | scale | read | plan | thennow | place | film | layer | which
    day TEXT NOT NULL,          -- YYYY-MM-DD, когда выкладывать
    status TEXT NOT NULL,       -- building | ready | approved | sent | posted | rejected | expired | failed
    title TEXT,
    data TEXT NOT NULL DEFAULT '{}',
    video TEXT,
    card_msg INTEGER,
    pkg_msgs TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

ICON = {"building": "⏳", "ready": "📥", "approved": "🟡", "sent": "📤", "posted": "✅", "rejected": "✕",
        "expired": "⌛️", "failed": "⚠️"}
ACTIVE = ("building", "ready", "approved", "sent")


class ReelError(RuntimeError):
    """Понятная автору причина, почему рилс не собрался."""


class ReelEdit(StatesGroup):
    caption = State()
    theme = State()


# ======================= база =======================

async def init() -> None:
    async with db.connect() as c:
        await c.executescript(SCHEMA)
        await c.commit()
    DIR.mkdir(parents=True, exist_ok=True)


async def get(rid: int):
    async with db.connect() as c:
        return await (await c.execute("SELECT * FROM reels WHERE id=?", (rid,))).fetchone()


async def items(*statuses: str, limit: int = 50) -> list:
    q = "SELECT * FROM reels" + (f" WHERE status IN ({','.join('?' * len(statuses))})" if statuses else "")
    async with db.connect() as c:
        return await (await c.execute(q + " ORDER BY day DESC, id DESC LIMIT ?", (*statuses, limit))).fetchall()


async def _set(rid: int, **f) -> None:
    if "data" in f and not isinstance(f["data"], str):
        f["data"] = json.dumps(f["data"], ensure_ascii=False)
    f["updated_at"] = db.now()
    async with db.connect() as c:
        await c.execute(f"UPDATE reels SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), rid))
        await c.commit()


async def _add(kind: str, day: str, data: dict) -> int:
    async with db.connect() as c:
        cur = await c.execute("INSERT INTO reels(kind, day, status, data, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                              (kind, day, "building", json.dumps(data, ensure_ascii=False), db.now(), db.now()))
        await c.commit()
        return cur.lastrowid


def _data(r) -> dict:
    return json.loads(r["data"] or "{}")


# ======================= даты =======================

def _now() -> datetime:
    return slots._now()


def _post_dt(day: str) -> datetime:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, TIME[0], TIME[1], tzinfo=_now().tzinfo)


def human(day: str) -> str:
    d = date.fromisoformat(day)
    today = _now().date()
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    return f"{DOW_RU[d.weekday()]} {d:%d.%m}"


def is_reel_day(d: date) -> bool:
    return any(DOW_NUM.get(x) == d.weekday() for x in DOW)


async def _taken_days() -> set[str]:
    return {r["day"] for r in await items(*ACTIVE, "posted")}


async def free_day() -> str:
    """Ближайший день без рилса: сегодня, если время ещё не прошло, иначе завтра и дальше."""
    taken = await _taken_days()
    d = _now().date()
    if _now() >= _post_dt(d.isoformat()) - timedelta(minutes=10):
        d += timedelta(days=1)
    while d.isoformat() in taken:
        d += timedelta(days=1)
    return d.isoformat()


# ======================= Wikimedia Commons =======================

async def _commons_get(client: httpx.AsyncClient, params: dict) -> dict:
    """Запрос к API Commons. Commons иногда отвечает «слишком часто» (429 или не-JSON) — ждём и повторяем."""
    for n in range(4):
        r = await client.get(COMMONS, params=params, timeout=30)
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                pass
        elif r.status_code not in (429, 502, 503, 504):
            r.raise_for_status()
        await asyncio.sleep(2 + 4 * n)
    raise ReelError("Wikimedia Commons не отвечает — попробуй через пару минут")


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(s or ""))).strip()


async def commons_candidates(client: httpx.AsyncClient, query: str, n: int = 3) -> list[dict]:
    """Крупные картинки по запросу: [{title, w, h, thumb, artist, license}] — превью 500 px для проверки."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
              "gsrnamespace": "6", "gsrlimit": "10", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
              "iiurlwidth": "500", "iiextmetadatafilter": "Artist|LicenseShortName"}
    try:
        pages = ((await _commons_get(client, params)).get("query") or {}).get("pages") or {}
    except Exception as exc:
        log.warning("Commons «%s»: %s", query, exc)
        return []
    out = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        i = (p.get("imageinfo") or [{}])[0]
        if i.get("mime") not in ("image/jpeg", "image/png", "image/tiff"):
            continue
        if max(i.get("width") or 0, i.get("height") or 0) < 1500 or not i.get("thumburl"):
            continue
        meta = i.get("extmetadata") or {}
        out.append({"title": p["title"], "w": i["width"], "h": i["height"], "thumb": i["thumburl"],
                    "artist": _strip((meta.get("Artist") or {}).get("value", ""))[:80],
                    "license": _strip((meta.get("LicenseShortName") or {}).get("value", ""))[:40]})
        if len(out) >= n:
            break
    return out


async def commons_download(client: httpx.AsyncClient, title: str, dest: Path) -> Path:
    """Файл Commons в крупном размере (до 3840 px по ширине) → dest (JPEG)."""
    data = await _commons_get(client, {"action": "query", "format": "json", "titles": title, "prop": "imageinfo",
                                       "iiprop": "url|size|mime", "iiurlwidth": "3840"})
    page = next(iter(((data.get("query") or {}).get("pages") or {}).values()), {})
    i = (page.get("imageinfo") or [{}])[0]
    url = i.get("url") if i.get("mime") == "image/jpeg" and (i.get("width") or 0) <= 3840 else i.get("thumburl")
    if not url:
        raise ReelError(f"Commons не отдал файл {title}")
    return await _save_url(client, url, dest)


async def _save_url(client: httpx.AsyncClient, url: str, dest: Path) -> Path:
    """Скачать картинку, срезать поля скана, ужать до MAX_SIDE по длинной стороне → dest (JPEG)."""
    r = await client.get(url, timeout=180)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)

    def save():
        with Image.open(io.BytesIO(r.content)) as im:
            if im.format == "JPEG" and max(im.size) > MAX_SIDE * 2:
                im.draft("RGB", (MAX_SIDE, MAX_SIDE))     # огромный музейный файл — декодируем сразу уменьшенным
            im = trim_borders(ImageOps.exif_transpose(im).convert("RGB"))
            if max(im.size) > MAX_SIDE:
                im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            im.save(dest, "JPEG", quality=94)
    await asyncio.to_thread(save)
    return dest


async def fetch_image(client: httpx.AsyncClient, it: dict, dest: Path) -> Path:
    """Картинка работы: музей или кадр из кино — по прямой ссылке url, Commons — по имени файла."""
    if it.get("url"):
        return await _save_url(client, it["url"], dest)
    return await commons_download(client, it["file"], dest)


# ======================= музеи открытого доступа =======================

MET = "https://collectionapi.metmuseum.org/public/collection/v1"
CMA = "https://openaccess-api.clevelandart.org/api/artworks/"


def _same(a: str, b: str) -> bool:
    """Название и автор музейной записи похожи на то, что нужно (хотя бы половина значимых слов)."""
    wa = {w for w in re.findall(r"[a-zà-ÿ]{3,}", (a or "").lower()) if w not in ("the", "and", "with", "von", "van", "der")}
    wb = set(re.findall(r"[a-zà-ÿ]{3,}", (b or "").lower()))
    return bool(wa) and len(wa & wb) >= max(1, len(wa) // 2)


async def museum_candidates(client: httpx.AsyncClient, w: dict) -> list[dict]:
    """The Met и Cleveland Museum of Art: картинки в открытом доступе (CC0) в полном размере — чище сканов Commons.
    → [{title, w, h, thumb, url, artist, license}] (0–2 штуки). Картины и графика, не архитектура."""
    if not MUSEUMS or not w.get("title"):
        return []
    q = f"{w.get('title')} {w.get('author') or ''}".strip()
    out = []
    try:
        r = await client.get(f"{MET}/search", params={"hasImages": "true", "q": q}, timeout=20)
        for oid in (r.json().get("objectIDs") or [])[:3]:
            o = (await client.get(f"{MET}/objects/{oid}", timeout=20)).json()
            if o.get("isPublicDomain") and o.get("primaryImage") and _same(w["title"], o.get("title")) \
                    and (not w.get("author") or _same(w["author"], o.get("artistDisplayName"))):
                out.append({"title": f"The Met: {o.get('title')}", "w": 0, "h": 0, "thumb": o.get("primaryImageSmall") or o["primaryImage"],
                            "url": o["primaryImage"], "artist": o.get("artistDisplayName") or "",
                            "license": "Public domain (The Met Open Access)"})
                break
    except Exception as exc:
        log.info("The Met «%s»: %s", q, exc)
    try:
        r = await client.get(CMA, params={"q": q, "has_image": 1, "cc0": 1, "limit": 3}, timeout=20)
        for o in r.json().get("data") or []:
            im = (o.get("images") or {})
            big = im.get("print") or im.get("full") or {}      # print — 3400 px JPEG; full бывает TIFF на 15 000 px
            who = ", ".join(c.get("description") or "" for c in o.get("creators") or [])
            if big.get("url") and _same(w["title"], o.get("title")) and (not w.get("author") or _same(w["author"], who)):
                out.append({"title": f"Cleveland: {o.get('title')}", "w": int(big.get("width") or 0), "h": int(big.get("height") or 0),
                            "thumb": (im.get("web") or big)["url"], "url": big["url"], "artist": who[:80],
                            "license": "CC0 (Cleveland Museum of Art)"})
                break
    except Exception as exc:
        log.info("Cleveland «%s»: %s", q, exc)
    return out


async def tmdb_candidates(client: httpx.AsyncClient, film: dict, n: int = 4) -> list[dict]:
    """Кадры фильма из TMDB (если задан ключ): [{title, thumb, url, …}]."""
    from app import tmdb
    if not tmdb.configured() or not (film or {}).get("title"):
        return []
    try:
        year = int(str(film.get("year") or "")[:4]) if str(film.get("year") or "")[:4].isdigit() else None
        item = await tmdb.find(client, film["title"], year)
        urls = await tmdb.images(client, item, 8) if item else []
    except Exception as exc:
        log.info("TMDB «%s»: %s", film, exc)
        return []
    return [{"title": f"Film still: {film['title']}", "w": 0, "h": 0, "thumb": u.replace("/original/", "/w500/"),
             "url": u, "artist": "", "license": "film still (TMDB)"} for u in urls[:n]]


def trim_borders(im: Image.Image, limit: float = 0.12) -> Image.Image:
    """Срезать ровные поля скана (белые, серые, чёрные — паспарту, фон фотостудии), не больше limit с каждой стороны.
    Поле — полоса, где цвет почти не меняется; живопись так ровно не бывает даже у тёмного фона."""
    import numpy as np
    small = im.copy()
    small.thumbnail((800, 800))
    a = np.asarray(small.convert("L"), dtype=np.float32)
    h, w = a.shape

    def edge(lines, ref):
        n = 0
        for line in lines:
            if line.std() < 4.0 and abs(float(line.mean()) - ref) < 10:
                n += 1
            else:
                break
        return n
    cut = [edge(a, float(a[0].mean())), edge(a[::-1], float(a[-1].mean())),
           edge(a.T, float(a[:, 0].mean())), edge(a.T[::-1], float(a[:, -1].mean()))]
    lim = [int(h * limit), int(h * limit), int(w * limit), int(w * limit)]
    if any(c >= l for c, l in zip(cut, lim)):          # поле шире предела — это часть картины, не трогаем
        cut = [c if c < l else 0 for c, l in zip(cut, lim)]
    top, bottom, left, right = cut
    if not any(cut):
        return im
    k = im.width / w
    pad = 2                                             # на пару пикселей глубже, чтобы не осталась кромка
    box = (int((left + pad) * k) if left else 0, int((top + pad) * k) if top else 0,
           im.width - (int((right + pad) * k) if right else 0), im.height - (int((bottom + pad) * k) if bottom else 0))
    log.info("Рилс: срезаю поля скана %s", box)
    return im.crop(box)


async def _thumbs(client: httpx.AsyncClient, cands: list[dict]) -> list[Image.Image | None]:
    async def one(c):
        try:
            r = await client.get(c["thumb"], timeout=30)
            r.raise_for_status()
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            return None
    return await asyncio.gather(*(one(c) for c in cands))


def _sheet(thumbs: list[Image.Image | None]) -> Image.Image:
    """Превью кандидатов в ряд, с номерами 1, 2, 3 — чтобы Claude выбрал нужный."""
    h = 320
    tiles = []
    for n, t in enumerate(thumbs, 1):
        if t is None:
            t = Image.new("RGB", (h, h), (60, 60, 60))
        t = t.copy()
        t.thumbnail((h * 2, h))
        tile = Image.new("RGB", (t.width, h + 40), (255, 255, 255))
        tile.paste(t, (0, 40))
        ImageDraw.Draw(tile).text((8, 6), str(n), fill=(0, 0, 0), font=reelrender.font(28))
        tiles.append(tile)
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 16 * (len(tiles) - 1), h + 40), (255, 255, 255))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width + 16
    return sheet


def _img_block(im: Image.Image, max_side: int = 1568, quality: int = 85) -> dict:
    im = im.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


PICK_SYSTEM = """You check pictures for an Instagram reel. For each numbered sheet you get the work that should be shown and 1–3 candidate images from Wikimedia Commons, numbered on the sheet.

Pick the candidate that shows exactly this work, whole and clean: for a painting or print — the full picture, straight, without a frame or museum wall around it if another candidate has that, not a detail, sketch, copy or engraving after it (unless the work itself is a print); for a building — a clear, well-composed photo of this building; for a photograph — the photograph itself. Prefer the sharper, better-coloured one. If none fits, pick 0.

Also give the centre of the main subject in the picked image (a face, a figure, the building) as fractions of its width and height: fx, fy from 0 to 1 — the video slowly pushes in toward it.

Return ONLY JSON: {"picks": [{"i": 1, "pick": 2, "fx": 0.3, "fy": 0.5}]}"""


async def verify(client: httpx.AsyncClient, works: list[dict], background: bool, per: int = 3,
                 max_aspect: float | None = None, museums: bool = True) -> list[dict]:
    """works: [{title, author, year, commons, need?, cands?}] → те, для которых нашлась верная картинка, с полем
    file (Commons) или url (музей, кадр). need — что должно быть на картинке; cands — свои кандидаты (кадры кино).
    max_aspect — только картинки не шире этого (ширина / высота): для подборки вертикальных работ."""
    from app import curator
    sem = asyncio.Semaphore(4)

    async def cands(w):
        async with sem:
            own = list(w.get("cands") or [])
            mus = await museum_candidates(client, w) if museums and not own else []
            c = await commons_candidates(client, w.get("commons") or f"{w.get('title')} {w.get('author')}", per + 2) \
                if w.get("commons") or not own else []
            c = own + mus + c
            if max_aspect:
                c = [x for x in c if not x["w"] or x["w"] / max(1, x["h"]) <= max_aspect]
            return c[:per + len(mus)]
    found = await asyncio.gather(*(cands(w) for w in works))
    pairs = [(w, c) for w, c in zip(works, found) if c]
    if not pairs:
        return []
    thumbs = await asyncio.gather(*(_thumbs(client, c) for _, c in pairs))
    content: list = [{"type": "text", "text": "Sheets follow. Each: the expected work, then the candidates."}]
    for n, ((w, c), t) in enumerate(zip(pairs, thumbs), 1):
        content.append({"type": "text", "text": f"Sheet {n}: {w.get('title')} — {w.get('author')}, {w.get('year')}"
                                                + (f". Must show: {w['need']}" if w.get("need") else "")})
        content.append(_img_block(_sheet(t), 1400, 80))
    data = await _ask(content, system=PICK_SYSTEM, max_tokens=1500, background=background)
    picks = {}
    for p in data.get("picks") or []:
        try:
            picks[int(p.get("i"))] = (int(p.get("pick") or 0),
                                      [min(1.0, max(0.0, float(p.get("fx", 0.5)))), min(1.0, max(0.0, float(p.get("fy", 0.5))))])
        except (TypeError, ValueError):
            continue
    out = []
    for n, (w, c) in enumerate(pairs, 1):
        k, focus = picks.get(n, (0, [0.5, 0.5]))
        if 1 <= k <= len(c):
            got = c[k - 1]
            row = {**{a: b for a, b in w.items() if a != "cands"}, "file": got["title"], "artist": got["artist"],
                   "license": got["license"], "focus": focus}
            if got.get("url"):
                row["url"] = got["url"]
            out.append(row)
    return out


# ======================= тексты =======================

EN_RULES = """Style for every line of text: plain English, concrete, conversational; uneven sentence rhythm is fine. The account is run by an architect for people with taste, so no hype. Never use: stunning, breathtaking, masterpiece, iconic, timeless, captivating, haunting, testament to, nestled, boasts, seamlessly, harmonious, "a dialogue between", "not X but Y" constructions, rhetorical questions, aphoristic closing lines, exclamation marks, emoji."""

TASTE = """AHMAG taste: built architecture (modernism and post-war classics, private houses, sacred buildings, ruins, memorials, brick, concrete, stone, wood, light, landscape); art without kitsch (quiet metaphysics and surrealism, land art, prints, manuscripts, old visual culture, warm humour); documentary, street and archival photography, ethnography; historical series. Never: renders, parametric architecture, commercial towers, developer projects."""

MUSIC = """music — 3 tracks that fit the mood and are likely in Instagram's music library: real, well-known recordings (film scores, classical, ambient, jazz, indie). Artist and track exactly as published; mood — 2–4 words in Russian."""

TOPIC_HINT = {
    "art": "painting, prints, drawings, sculpture",
    "architecture": "buildings: houses, churches, ruins, modernist and older architecture, interiors",
    "photography": "historical and documentary photography (photographers who died before ~1955, archives)",
    "archive": "archive material: old prints, maps, manuscripts, posters, scientific illustration, early photos",
}

COLLECTION_SYSTEM = f"""You make Instagram reels for AHMAG, an account about architecture, art, photography and archives. This reel is a thematic compilation: a title card, then 6–8 works by different authors, each on screen for a few seconds with its title and author. Example of the genre: "The Art of Melancholy" — paintings where sadness is felt rather than shown.

A good theme is specific and has a mood or an idea that ties works from different countries and periods: "Rooms without people", "Windows at night", "Concrete that looks soft", "Painters and their mothers". Not a whole category ("Modern architecture"), not a list of famous things ("Greatest paintings").

{TASTE}

Every work must have a large image on Wikimedia Commons: paintings and prints by artists who died before about 1955, historical photographs, buildings with free-licensed photos. Pick specific works, one per author; mix famous and lesser-known.

The reel is narrated. intro — the line spoken over the title card, up to 14 words: it opens the question the collection answers, about what ties these works, without naming the title again ("Every one of these rooms was painted by someone who lived alone."). Each work gets a line — up to 14 words, spoken while it is on screen: one specific fact the viewer cannot see, which ties it to the theme (who, when, what happened, why it belongs here). Lines are joined like a story, not a list; the last work's line lands the idea. Wrap the one word per line the narrator leans on in *asterisks*. delivery for each line and intro_delivery — 6–12 words of direction for the narrator (tone, pace).

format — all works in the reel are shown the same way. "vertical": every work fills the whole tall screen, so EVERY work must be a tall portrait-format image (height at least 1.4 times the width): full-length portraits, standing figures, towers, doorways, narrow streets, vertical photographs. Choose it only when the theme suits tall works. "framed": each work is shown whole on a dark wall, any proportions. When unsure — "framed". Give 10 works in viewing order (some may not be found, the reel uses up to 8); the first one opens the reel under the title, so it should be strong.

{EN_RULES}

{MUSIC}

Return ONLY JSON:
{{"format": "vertical|framed", "title": "on-screen title, up to 28 characters, sentence case; wrap the one key word in *asterisks* — it is set in italics (\"The art of *melancholy*\")", "theme_ru": "тема по-русски, коротко", "intro": "...", "intro_delivery": "...", "works": [{{"title": "title of the work in English or original", "author": "...", "year": "...", "commons": "query for Wikimedia Commons search: title and author, no year", "line": "...", "delivery": "..."}}], "caption": "1–3 short sentences for the Instagram caption: what ties these works together", "hashtags": ["5–8 lowercase words without #"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}"""

MORE_SYSTEM = f"""You add works to an AHMAG Instagram reel compilation. Same rules: a specific work by an author not yet in the reel, with a large image on Wikimedia Commons (artists who died before about 1955, historical photographs, buildings with free photos), fitting the theme.

{TASTE}

Each work also gets a line — up to 14 words spoken while it is on screen: one specific fact that ties it to the theme; wrap the one stressed word in *asterisks*.

Return ONLY JSON: {{"works": [{{"title": "...", "author": "...", "year": "...", "commons": "query for Wikimedia Commons", "line": "...", "delivery": "..."}}]}}"""

PAINTING_SYSTEM = f"""You pick a painting for an AHMAG Instagram reel that walks through its details: the camera starts on the whole picture, then moves to 4–6 details one by one, with one short line on each, and the lines tell the painting's story. Example: Matejko's "Stańczyk" — a jester sits alone while the ball goes on next door; the letter on the table; the comet in the window; Poland has lost Smolensk.

Pick a painting (or a fresco, altarpiece, large print) that is in the public domain and has a large image on Wikimedia Commons; has several visible details that carry the story; has a documented story with tension, contrast or a twist that a stranger would care about in three seconds — someone is losing something, hiding something, doesn't know something, or the obvious reading is wrong. The story matters more than fame: a famous painting is fine if its real story is not the one everybody knows. Check the facts with web search. Prefer works that are not the most overexposed. Not from the avoid list.

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "common English title", "author": "...", "year": "...", "museum": "museum, city", "medium": "e.g. Oil on canvas, if known", "size": "e.g. 88 × 120 cm, if known", "commons": "query for Wikimedia Commons search: title and author", "facts": ["6–10 specific verified facts in English, about what is shown, details, context, what happened"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}"""

HOOK_TYPES = {"contradiction": "противоречие", "hidden": "скрытая деталь", "stakes": "ставки",
              "challenge": "вызов", "question": "вопрос"}

STORY_SYSTEM = """You write the narration for an AHMAG Instagram reel about the painting in the image. A narrator reads it aloud, the words appear on screen as they are spoken, and the camera moves between details. Coordinates are fractions of the image width and height from its top-left corner (0 to 1).

THE JOB: the viewer must not scroll away. They decide in the first two seconds, and they stay only while a question is open. So this is a story with tension, told by one person to a friend standing next to them in the museum — never a list of facts.

The engine of retention:
- The hook opens ONE main question. The story answers it only at the climax. Until then every line gives part of the answer and opens a smaller new question.
- Lines are joined by cause and contrast — "but", "so", "which is why", "and that's the problem" — never by "and also", "now look at", "there's more". If a line could be swapped with the next one without breaking anything, the story is a list: rewrite it.
- Order the details by the logic of the story (cause → contrast → consequence), not by where they sit in the picture.
- Stakes in human terms: who loses what, who knows and who doesn't, what someone is hiding or afraid of. No art-history vocabulary, no technique talk unless it IS the story.
- Specific beats general: a name, a number, an object the viewer can see.
- The final line turns the hook around: after it, the first frame reads differently, so the loop back to the start feels natural.

How it sounds:
- Present tense for the scene: "The queen is throwing a ball." Talk to the viewer and steer their eye: "Look at his hands."
- Short sentences, varied rhythm, some fragments. Plain spoken English.
- Let the people in the painting think and feel through what we see: where they look, what they hold, who is missing.

Example of the voice and structure (Matejko's "Stańczyk"; do not reuse its lines):
  hook: "The only man not laughing at this party is the jester."
  context: "Everyone else is at the queen's ball. He's sitting alone, in the dark."
  reveals: "Because of the ^letter on this table. Smolensk has fallen to Moscow. The war is *lost*." / "But next ^door, the court is still dancing. Nobody has read it." / "And in the window, a ^comet. Back then, that meant worse was *coming*."
  climax: "He's the fool. He's the only one who *sees* it."
  final: "Matejko painted this in 1862, when Poland no longer existed. He already knew how it *ended*."

Structure — 25 to 35 seconds, 70–90 words in total:

1. hooks — 4 alternative opening lines, each of a different type:
   contradiction: "The only man not laughing at this party is the jester."
   hidden: "There's a comet in this painting. Almost nobody sees it."
   stakes: "The letter on this table just cost a kingdom a city."
   challenge: "You've seen this jester before. You probably thought he was bored."
   question: "Why is the jester the saddest man at the party?"
   The first sentence is at most 8 words and lands in under two seconds; the whole hook at most 14 words. No names, dates or titles in the hook. It talks about what is on screen in the first frame, so its box is the close-up the reel opens on. True, specific, visual. No generic hooks ("This painting hides a dark secret", "You won't believe what's in this painting").
   Check each hook before you give it: understood in two seconds without sound? about what is on screen? opens a question? concrete? Give only hooks that pass.
   hook_pick — the index of the strongest: the one you would stop scrolling for and the one the story pays off best.
2. context — up to 14 words, shown over the whole painting (the viewer sees the whole scene for the first time). It RAISES the stakes of the hook, it does not explain. Who/when only as a half-clause if needed; never "X painted this in Y" here.
3. reveals — 3 details (4 only if the story truly needs it), each up to 18 words: one step of the story each (see the engine above). label — 1–3 words naming the detail for the on-screen caption ("The letter", "The comet"). Mark with ^ the one word at which the camera should arrive at this detail — the word that names or points at it ("the ^letter", "next ^door"); the ^ is not read aloud.
4. climax — up to 12 words: the answer to the hook's question, what it all means for the person in the painting. Short sentences. Here, and only here, one brief human note. Do not name the emotion with an adjective. box — the detail to hold on (often the face), or null for the whole painting.
5. final — up to 20 words: the last turn — what happened next, or why the painter told this story. It lands with weight and echoes the hook. Not a moral, not a slogan, not "and that's why it's a masterpiece".

Boxes — [x0, y0, x1, y1], tight around the thing itself, not around the area it is in (the letter, not the table). target — 2–6 words naming exactly what is inside the box ("the folded letter on the table"); a second pass uses it to refine the box, so be precise. Only things clearly visible in THIS image.

Every word of emphasis — the one word per sentence the narrator leans on — is wrapped in asterisks: "Nobody has *noticed* he's gone." At most one per sentence; the screen sets it in italics.

Use only the facts given; if the story needs a fact you don't have, change the angle instead of inventing one.
Never: insane, mind-blowing, crazy, iconic, masterpiece, stunning, breathtaking, haunting, heartbreaking, chilling, fascinating, intricate, secret, "dive in", "wait for the end", "follow for more", "let that sink in", "not X but Y" constructions, exclamation marks, emoji, parentheses, abbreviations, lists.

delivery — every hook, reveal, the climax, and the context and final lines (context_delivery, final_delivery) get a short direction for the narrator, in English, 6–15 words: tone, emotion, pace, where to pause. Follow the arc: hook — no warm-up, first word straight away, quiet intrigue, a little quicker; context — lower, the stakes sinking in; reveals — curiosity that builds; climax — slower, softer, heavier, a real pause between sentences; final — calm and weighty. Directions are alive and specific, like notes from a director to an actor ("lean on 'only'", "a wry smile here", "let it hang"), but never theatrical: no shouting, no whispering, no trailer voice.

voice_direction — one or two sentences in English for the narrator about this particular story: its mood, where it turns, what to savour.

caption — the Instagram caption WITHOUT the hook (the hook is put above it automatically): first line "Title (year), Author"; then 2–3 short paragraphs with the story and one or two facts that did not fit the video; last line — museum and city.
hashtags — 5–8 lowercase words without #.

Return ONLY JSON:
{"hooks": [{"type": "contradiction|hidden|stakes|challenge|question", "text": "...", "box": [0.1, 0.2, 0.3, 0.5], "target": "...", "delivery": "..."}], "hook_pick": 0, "context": "...", "context_delivery": "...", "reveals": [{"box": [0.1, 0.2, 0.3, 0.5], "target": "...", "label": "...", "text": "...", "delivery": "..."}], "climax": {"text": "...", "box": [0.1, 0.2, 0.3, 0.5], "target": "...", "delivery": "..."}, "final": "...", "final_delivery": "...", "voice_direction": "...", "caption": "...", "hashtags": ["..."]}"""

REFINE_SYSTEM = """You refine bounding boxes for a video camera. Each image is a crop from a painting, photograph or drawing with a grid drawn over it: lines every 1/10 of the crop, numbered 0–10 along the top (x) and the left side (y). For each crop you get the thing the camera must frame. Give its tight box in grid units: x0, y0 (top-left) and x1, y1 (bottom-right), decimals allowed, 0 to 10. Tight around the thing itself, not its surroundings. If the thing is not in the crop, or you are not sure which one it is, return null for that crop.

Return ONLY JSON: {"boxes": [{"i": 1, "box": [x0, y0, x1, y1]}, {"i": 2, "box": null}]}"""


def _box(b) -> list | None:
    try:
        b = [float(x) for x in b]
        return b if len(b) == 4 and b[2] > b[0] and b[3] > b[1] else None
    except (TypeError, ValueError):
        return None


def beats(d: dict) -> list[dict]:
    """Сюжет рилса по порядку: [{kind, text, box}], box None — вся картина.
    Рилсы, собранные до v4.7 (intro + frames), превращаются в тот же вид."""
    st = d.get("story")
    if not st:
        out = [{"kind": "context", "text": d.get("intro") or "", "box": None}]
        return out + [{"kind": "reveal", "text": f.get("text") or "", "box": _box(f.get("box"))}
                      for f in d.get("frames") or []]
    if isinstance(st.get("context"), dict):
        return pair_beats(d)
    kind = d.get("kind") or "details"
    hooks = st.get("hooks") or []
    h = hooks[st.get("hook_i", 0) % len(hooks)] if hooks else None
    out = [{"kind": "hook", "text": h["text"], "box": _box(h.get("box")), "how": h.get("delivery")}] if h else []
    if h and kind == "scale" and _box(h.get("box")):
        # «Масштаб»: кольцо вокруг человека держится, пока камера отъезжает
        out[0]["mark"] = {"type": "figure", "box": _box(h.get("box")), "label": (st.get("figure_label") or "")[:18],
                          "persist": True}
    out.append({"kind": "context", "text": st.get("context") or "", "box": _box(st.get("context_box")),
                "how": st.get("context_delivery")})
    for r in st.get("reveals") or []:
        b = {"kind": "reveal", "text": r.get("text") or "", "box": _box(r.get("box")), "how": r.get("delivery"),
             "label": (r.get("label") or "").strip()[:28]}
        if kind == "read" and r.get("mark") and b["box"]:
            b["mark"] = {"type": str(r["mark"]).lower(), "box": b["box"], "cols": r.get("cols"), "rows": r.get("rows")}
        out.append(b)
    cl = st.get("climax") or {}
    if cl.get("text"):
        out.append({"kind": "climax", "text": cl["text"], "box": _box(cl.get("box")), "how": cl.get("delivery")})
    if st.get("final"):
        out.append({"kind": "final", "text": st["final"], "box": None, "how": st.get("final_delivery")})
    # *слово* — акцент (курсив), ^слово — на нём камера приезжает к детали. Оба знака остаются в "raw" для Remotion,
    # голос, карточка и старый рендер получают чистый текст
    for b in out:
        b["raw"] = b["text"]
        b["text"] = b["text"].replace("*", "").replace("^", "")
    return [b for b in out if b["text"].strip()]


def pair_beats(d: dict) -> list[dict]:
    """Сюжет пары: [{kind, text, raw, show (a|b|both), box, how, label, pick}]."""
    st = d.get("story") or {}
    hooks = st.get("hooks") or []
    h = hooks[st.get("hook_i", 0) % len(hooks)] if hooks else None
    parts = ([("hook", h)] if h else []) + [("context", st.get("context") or {})] + \
        [("reveal", r) for r in st.get("reveals") or []] + [("climax", st.get("climax") or {}), ("final", st.get("final") or {})]
    out = []
    for kind, x in parts:
        if not x or not (x.get("text") or "").strip():
            continue
        show = str(x.get("show") or "a").lower()
        show = show if show in ("a", "b", "both") else "a"
        b = {"kind": kind, "raw": x["text"], "text": x["text"].replace("*", "").replace("^", ""), "show": show,
             "box": _box(x.get("box")) if show != "both" else None, "how": x.get("delivery")}
        if kind == "reveal":
            b["label"] = (x.get("label") or "").strip()[:28]
        if kind == "climax" and str(x.get("pick") or "").lower() in ("a", "b"):
            b["pick"] = str(x["pick"]).lower()
            b["show"], b["box"] = "both", None
        out.append(b)
    return out


def hook_text(d: dict) -> str:
    b = beats(d)
    return b[0]["text"] if b and b[0]["kind"] == "hook" else ""


async def _ask(content, *, system: str, max_tokens: int, background: bool, tools: list | None = None) -> dict:
    """Вызов Claude для рилса. Пустой ответ (весь запас токенов ушёл на размышления или поиск) — ещё раз
    с запасом втрое больше: так было с «Claude вернул не JSON: ''»."""
    from app import curator
    for n in range(2):
        try:
            return await curator._call(content, system=system, model=config.CLAUDE_MODEL,
                                       max_tokens=max_tokens * (3 if n else 1), tools=tools, background=background)
        except ValueError as exc:
            if n or "не JSON" not in str(exc):
                raise ReelError("Claude ответил не по формату — попробуй ещё раз") from exc
            log.warning("Рилс: пустой или битый ответ Claude, повторяю с большим запасом: %s", exc)
    return {}


def _grid_crop(im: Image.Image, box: list[float]) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """Участок вокруг грубой рамки (с запасом) с сеткой 10×10 и подписями → (картинка, участок в долях)."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # участок примерно квадратный на картине: в 2,2 раза больше рамки, но не меньше 22% короткой стороны
    px = max((x1 - x0) * 2.2 * im.width, (y1 - y0) * 2.2 * im.height, 0.22 * min(im.width, im.height))
    side_w, side_h = min(1.0, px / im.width), min(1.0, px / im.height)
    cx = min(max(cx, side_w / 2), 1 - side_w / 2)
    cy = min(max(cy, side_h / 2), 1 - side_h / 2)
    area = (cx - side_w / 2, cy - side_h / 2, cx + side_w / 2, cy + side_h / 2)
    crop = im.crop((int(area[0] * im.width), int(area[1] * im.height), int(area[2] * im.width), int(area[3] * im.height)))
    crop.thumbnail((900, 900))
    pad = 34
    out = Image.new("RGB", (crop.width + pad + 22, crop.height + pad + 22), (255, 255, 255))
    out.paste(crop, (pad, pad))
    dr = ImageDraw.Draw(out)
    f = reelrender.font(18)
    for k in range(11):
        x = pad + crop.width * k / 10
        y = pad + crop.height * k / 10
        for dx, col in ((1, (0, 0, 0)), (0, (255, 235, 0))):
            dr.line([(x + dx, pad), (x + dx, pad + crop.height)], fill=col, width=1)
            dr.line([(pad, y + dx), (pad + crop.width, y + dx)], fill=col, width=1)
        dr.text((x - (10 if k == 10 else 5), 6), str(k), fill=(0, 0, 0), font=f)
        dr.text((4, y - 10), str(k), fill=(0, 0, 0), font=f)
    return out, area


async def refine_boxes(path: Path, st: dict, background: bool) -> int:
    """Второй проход по рамкам: Claude смотрит на каждую деталь крупно, с сеткой, и уточняет рамку.
    Первый проход (по всей картине в 1568 px) ошибается на 5–10% размера картины — для мелкой детали это мимо.
    Меняет рамки в st на месте. → сколько рамок уточнено."""
    items = [h for h in st.get("hooks") or []] + list(st.get("reveals") or []) + \
        ([st["climax"]] if (st.get("climax") or {}).get("box") else [])
    items = [x for x in items if _box(x.get("box"))]
    if not items:
        return 0
    with Image.open(path) as im:
        im = im.convert("RGB")
        crops = [_grid_crop(im, _box(x["box"])) for x in items]
    content: list = [{"type": "text", "text": f"{len(items)} crops follow."}]
    for n, (x, (img, _)) in enumerate(zip(items, crops), 1):
        what = x.get("target") or x.get("label") or x.get("text") or ""
        content.append({"type": "text", "text": f"Crop {n}: frame {what}"})
        content.append(_img_block(img, 900, 85))
    try:
        data = await _ask(content, system=REFINE_SYSTEM, max_tokens=1500, background=background)
    except ReelError:
        log.warning("Рилс: уточнение рамок не удалось, оставляю первые", exc_info=True)
        return 0
    done = 0
    for r in data.get("boxes") or []:
        try:
            n = int(r.get("i")) - 1
            b = [min(10.0, max(0.0, float(v))) / 10 for v in r["box"]]
        except (TypeError, ValueError, KeyError):
            continue
        if not 0 <= n < len(items) or b[2] - b[0] < 0.02 or b[3] - b[1] < 0.02:
            continue
        ax0, ay0, ax1, ay1 = crops[n][1]
        aw, ah = ax1 - ax0, ay1 - ay0
        items[n]["box"] = [round(ax0 + b[0] * aw, 4), round(ay0 + b[1] * ah, 4),
                           round(ax0 + b[2] * aw, 4), round(ay0 + b[3] * ah, 4)]
        done += 1
    return done


async def sfx_on() -> bool:
    return bool(await db.get_setting("reel_sfx", True))


async def _posted_kinds() -> set[str]:
    return {r["kind"] for r in await items("posted", limit=300)}


async def _avoid(kind: str) -> list[str]:
    rows = [r for r in await items(limit=200) if r["kind"] == kind and r["title"]]
    return [r["title"] for r in rows][:60]


async def _next_topic() -> str:
    i = int(await db.get_setting("reel_topic_i", 0) or 0)
    await db.set_setting("reel_topic_i", i + 1)
    return TOPICS[i % len(TOPICS)] if TOPICS else "art"


def _disp(it: dict | None, lang: str) -> dict:
    """Работа так, как её показать на экране и в подписи: в русском рилсе — русские название, автор, музей,
    техника и размер, если Claude их дал (поле ru; у работ подборки — title_ru и author_ru)."""
    it = dict(it or {})
    if lang != "ru":
        return it
    ru = it.get("ru") if isinstance(it.get("ru"), dict) else {}
    ru = {**ru, **{k: it[f"{k}_ru"] for k in ("title", "author") if it.get(f"{k}_ru")}}
    for k in ("title", "author", "museum", "medium", "size"):
        if str(ru.get(k) or "").strip():
            it[k] = str(ru[k]).strip()
    return it


NAMES_SYSTEM = """You give Russian display names for an Instagram reel in Russian. For each numbered line give: title — the established Russian title of the work (Russian Wikipedia or Russian museum practice; otherwise a plain Russian translation), author — the name in the standard Russian form, museum — the museum or place in Russian ("Национальный музей, Варшава"), medium and size in Russian ("Холст, масло", "88 × 120 см"). Leave a field empty if the line does not have it. Line 0, if present, is the short name of the reel itself: give only its title.

Return ONLY JSON: {"items": [{"i": 1, "title": "...", "author": "...", "museum": "...", "medium": "...", "size": "..."}]}"""


async def _ensure_ru(its: list[dict], background: bool, reel_name: dict | None = None) -> None:
    """Русские названия для экрана, если их нет (рилс переделывают на русский — объект выбирался для английского).
    reel_name — пара: её короткое имя (title → title_ru). Не вышло — останутся английские."""
    need = [it for it in its if not (it.get("ru") or {}).get("title")]
    ask_name = reel_name is not None and not reel_name.get("title_ru") and reel_name.get("title")
    if not need and not ask_name:
        return
    lines = ([f"0. {reel_name['title']}"] if ask_name else []) + [
        f"{n}. {it.get('title')} — {it.get('author') or ''}; museum: {it.get('museum') or ''}; "
        f"medium: {it.get('medium') or ''}; size: {it.get('size') or ''}" for n, it in enumerate(need, 1)]
    try:
        res = await _ask("\n".join(lines), system=NAMES_SYSTEM, max_tokens=1200, background=background)
    except Exception:
        log.warning("Русские названия не получены — останутся английские", exc_info=True)
        return
    for x in res.get("items") or []:
        try:
            i = int(x.get("i"))
        except (TypeError, ValueError):
            continue
        if i == 0 and ask_name and x.get("title"):
            reel_name["title_ru"] = str(x["title"]).strip()
        elif 1 <= i <= len(need):
            need[i - 1]["ru"] = {k: str(x.get(k) or "").strip() for k in ("title", "author", "museum", "medium", "size")}


def _credits(items: list[dict], lang: str = "en") -> str:
    """Авторы фото из Commons, если лицензия требует указать автора (не общественное достояние)."""
    need = []
    for it in items:
        lic = (it.get("license") or "").lower()
        if lic and "public domain" not in lic and "pd" not in lic.split() and "cc0" not in lic and it.get("artist"):
            need.append(f"{it['artist']} ({it['license']})")
    src = []
    for it in items:
        lic = it.get("license") or ""
        src.append("The Met" if "The Met" in lic else "Cleveland Museum of Art" if "Cleveland" in lic
                   else "film still" if "film" in lic else "Wikimedia Commons")
    head, photos = ("Изображения: ", ". Фото: ") if lang == "ru" else ("Images: ", ". Photos: ")
    base = head + (", ".join(dict.fromkeys(x for x in src if x != "film still")) if src else "Wikimedia Commons")
    return base + (photos + "; ".join(dict.fromkeys(need)) if need else "")


def _tags(tags: list, extra: str) -> str:
    # \w — и латиница, и кириллица: русские хэштеги не теряются
    clean = [re.sub(r"[^\w]", "", str(t).lower()) for t in tags or []]
    out = ["ahmag", extra] + [t for t in clean if t and t not in ("ahmag", extra)]
    return " ".join(f"#{t}" for t in list(dict.fromkeys(out))[:10])


def build_caption(r_kind: str, d: dict) -> str:
    if d.get("caption_override"):
        return d["caption_override"]
    lang = rk.lang_of(d)
    if r_kind == "collection":
        lines = [d.get("title", ""), "", d.get("caption", "").strip(), ""]
        for n, it in enumerate(d.get("items") or [], 1):
            w = _disp(it, lang)
            lines.append(f"{n}. {w.get('title')} — {w.get('author')}" + (f", {w['year']}" if w.get("year") else ""))
        lines += ["", _credits(d.get("items") or [], lang), "", _tags(d.get("hashtags"), "ahmagreels")]
    else:
        hook = hook_text(d)
        its = [d["pair"]["a"], d["pair"]["b"]] if d.get("pair") else [d.get("painting") or {}]
        lines = ([hook, ""] if hook else []) + [d.get("caption", "").strip(), "", _credits(its, lang), "",
                 _tags(d.get("hashtags"), "ahmagreels")]
    return "\n".join(lines).strip()[:2150]


# ======================= сборка =======================

_gen_lock = asyncio.Lock()


async def _collection(rid: int, d: dict, background: bool, client: httpx.AsyncClient) -> dict:
    from app import curator
    folder = DIR / str(rid)
    lang = rk.lang_of(d)
    if not d.get("items"):
        topic = d.get("topic") or await _next_topic()
        req = d.get("request")
        prompt = ((f"Theme requested by the author: {req}\n" if req else
                   f"Area for this reel: {topic} — {TOPIC_HINT.get(topic, topic)}.\n")
                  + "Avoid these earlier reel titles:\n" + ("\n".join(await _avoid("collection")) or "—"))
        plan = await _ask(prompt, system=rk.localize(COLLECTION_SYSTEM, lang, rk.RU_NAMES_WORKS), max_tokens=4000,
                          background=background)
        works = [w for w in plan.get("works") or [] if w.get("title")]
        if not works:
            raise ReelError("Claude не предложил работ")
        vertical = str(plan.get("format") or "").lower() == "vertical"
        tall = reelplan.BLEED + 0.03 if vertical else None
        good = await verify(client, works[:12], background, max_aspect=tall)
        if len(good) < MIN_ITEMS:
            more = await _ask(
                f"Theme: {plan.get('title')}\nFormat: {'vertical — only tall portrait-format works' if vertical else 'framed'}"
                + "\nAlready in the reel: " + "; ".join(f"{w['title']} — {w['author']}" for w in good)
                + "\nNot found on Commons: " + "; ".join(w["title"] for w in works
                                                           if w["title"] not in {g["title"] for g in good})
                + f"\nGive {MAX_ITEMS} more works.", system=rk.localize(MORE_SYSTEM, lang, rk.RU_NAMES_WORKS),
                max_tokens=2500, background=background)
            good += await verify(client, (more.get("works") or [])[:MAX_ITEMS], background, max_aspect=tall)
        if vertical and len(good) < MIN_ITEMS:
            # вертикальных не хватило — подборка станет «целиком», добираем любые
            log.info("Рилс: вертикальных работ %s — собираю подборку целиком", len(good))
            have = {g["title"] for g in good}
            good += await verify(client, [w for w in works[:12] if w["title"] not in have], background)
        if len(good) < MIN_ITEMS:
            raise ReelError(f"хороших картинок нашлось только {len(good)} из {MIN_ITEMS} нужных — попробуй другую тему")
        title_em = str(plan.get("title") or "")[:44]
        d.update(intro=plan.get("intro") or "", intro_delivery=plan.get("intro_delivery") or "")
        d.update(title=title_em.replace("*", ""), title_em=title_em, theme_ru=plan.get("theme_ru") or "",
                 caption=plan.get("caption") or "", hashtags=plan.get("hashtags") or [],
                 music=(plan.get("music") or [])[:3], topic=topic, items=good[:MAX_ITEMS], spare=good[MAX_ITEMS:])
    for n, it in enumerate(d["items"]):
        path = folder / f"{n:02d}_{hashlib.md5((it.get('url') or it['file']).encode()).hexdigest()[:8]}.jpg"
        if not path.exists():
            await fetch_image(client, it, path)
        it["path"] = str(path)
    render_items = [{"path": it["path"], "label": _disp(it, lang)["title"],
                     "sub": _disp(it, lang)["author"] if lang == "ru" else f"By {it['author']}",
                     "focus": it.get("focus") or [0.5, 0.5]} for it in d["items"]]
    video = folder / f"reel_{int(datetime.now().timestamp())}.mp4"
    d.pop("render_note", None)
    if reelplan.available():
        try:
            vb = reelplan.collection_beats(d) if VOICED_COLLECTIONS else []
            voice = await _voice(folder, d, vb) if vb else None
            props = await asyncio.to_thread(reelplan.collection_props, folder, d, await sfx_on(), voice)
            d["duration"] = await asyncio.to_thread(reelplan.render, "Collection", props, folder, video)
            d["video"], d["cover_t"] = str(video), props.get("coverT")
            return d
        except Exception as exc:
            log.warning("Рилс: Remotion не собрал подборку, беру запасную вёрстку", exc_info=True)
            d["render_note"] = f"новая вёрстка не собралась ({str(exc)[:80]}) — запасная"
    else:
        d["render_note"] = f"новая вёрстка недоступна: {reelplan.why_not()} — запасная"
    d["duration"] = await asyncio.to_thread(reelrender.collection, d["title"], render_items, video)
    d["video"] = str(video)
    return d


async def _details(rid: int, d: dict, background: bool, client: httpx.AsyncClient) -> dict:
    """Одна картинка, камера по деталям: детали картины, одна фотография, масштаб, разбор здания."""
    from app import curator
    kind = d.get("kind") or "details"
    folder = DIR / str(rid)
    lang = rk.lang_of(d)
    if not d.get("painting"):
        req = d.get("request")
        prompt = ((f"The author asks for: {req}\n" if req else "")
                  + "Avoid (already done):\n" + (("\n".join(await _avoid(kind))) or "—"))
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
        system = rk.localize_pick(PAINTING_SYSTEM if kind == "details" else rk.PICK[kind], lang)
        p = await _ask(prompt, system=system, max_tokens=4000, tools=tools, background=background)
        if not p.get("title"):
            raise ReelError(f"Claude не выбрал: {rk.KINDS[kind]['obj']}")
        p["need"] = rk.NEED.get(kind, "")
        good = await verify(client, [p], background, per=4, museums=kind in ("details", "photo"))
        if not good:
            raise ReelError(f"не нашлось хорошей картинки «{p['title']}» — попробуй другую")
        d.update(painting=good[0], title=f"{p['title']}", music=(p.get("music") or [])[:3],
                 facts=p.get("facts") or [])
        d.pop("frames", None)
        d.pop("story", None)
    pt = d["painting"]
    path = Path(pt.get("path") or folder / "painting.jpg")
    if not path.exists():
        await fetch_image(client, pt, path)
    pt["path"] = str(path)
    if not d.get("story") and not d.get("frames"):
        with Image.open(path) as im:
            block = _img_block(im.convert("RGB"))
        avoid = d.get("avoid_frames") or []
        noun = {"details": "Painting", "photo": "Photograph"}.get(kind, "Building")
        text = (f"{noun}: {pt['title']} — {pt['author']}, {pt.get('year')}. {pt.get('museum') or ''}\n\nFacts:\n"
                + "\n".join(f"- {f}" for f in d.get("facts") or [])
                + ("\n\nThe previous version used these details and lines, choose others where possible:\n"
                   + "\n".join(avoid) if avoid else ""))
        system = rk.localize(STORY_SYSTEM if kind == "details" else rk.story_system(kind), lang)
        st = await _ask([block, {"type": "text", "text": text}], system=system, max_tokens=5000,
                        background=background)
        hooks = [h for h in st.get("hooks") or [] if h.get("text")]
        reveals = [r for r in st.get("reveals") or [] if r.get("text") and _box(r.get("box"))][:4]
        if not hooks or len(reveals) < 2:
            raise ReelError("Claude не собрал сюжет — попробуй другой объект")
        try:
            pick = int(st.get("hook_pick") or 0)
        except (TypeError, ValueError):
            pick = 0
        # выбранный хук — первым, остальные — для кнопки «Другой хук»
        pick = pick if 0 <= pick < len(hooks) else 0
        hooks = [hooks[pick]] + [h for i, h in enumerate(hooks) if i != pick]
        d["story"] = {"hooks": hooks, "hook_i": 0, "context": st.get("context") or "", "reveals": reveals,
                      "climax": st.get("climax") or {}, "final": st.get("final") or "",
                      "context_delivery": st.get("context_delivery") or "", "final_delivery": st.get("final_delivery") or "",
                      "voice_direction": st.get("voice_direction") or ""}
        for k in ("figure_label", "context_box"):
            if st.get(k):
                d["story"][k] = st[k]
        d.update(caption=st.get("caption") or "", hashtags=st.get("hashtags") or [])
        n = await refine_boxes(path, d["story"], background)
        log.info("Рилс: уточнил рамок %s", n)
    if lang == "ru":
        await _ensure_ru([pt], background)
    show = _disp(pt, lang)
    end = [f"{show['title']}" + (f", {show['year']}" if show.get("year") else ""), show.get("author") or "",
           show.get("museum") or ""]
    bs = beats(d)
    voice = await _voice(folder, d, bs)
    video = folder / f"reel_{int(datetime.now().timestamp())}.mp4"
    d.pop("render_note", None)
    if reelplan.available():
        try:
            info = {**show, "rubric": rk.rubric(kind, lang), "meta_names": rk.meta_names(kind, lang), "lang": lang}
            props = await asyncio.to_thread(reelplan.story_props, folder, path, bs, voice, info, await sfx_on())
            d["duration"] = await asyncio.to_thread(reelplan.render, "Story", props, folder, video)
            d["video"], d["cover_t"] = str(video), props.get("coverT")
            return d
        except Exception as exc:
            log.warning("Рилс: Remotion не собрал %s, беру запасную вёрстку", kind, exc_info=True)
            d["render_note"] = f"новая вёрстка не собралась ({str(exc)[:80]}) — запасная"
    else:
        d["render_note"] = f"новая вёрстка недоступна: {reelplan.why_not()} — запасная"
    d["duration"] = await asyncio.to_thread(reelrender.story, path, bs, end, video, voice)
    d["video"] = str(video)
    return d


async def _pair(rid: int, d: dict, background: bool, client: httpx.AsyncClient) -> dict:
    """Две картинки и переход: чертёж → здание, тогда / сейчас, картина и место, кадр ← картина, что под слоем,
    какая из двух."""
    kind = d.get("kind")
    spec = rk.KINDS[kind]
    folder = DIR / str(rid)
    lang = rk.lang_of(d)
    if not d.get("pair"):
        req = d.get("request")
        prompt = ((f"The author asks for: {req}\n" if req else "")
                  + "Avoid (already done):\n" + (("\n".join(await _avoid(kind))) or "—"))
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]
        p = await _ask(prompt, system=rk.localize_pick(rk.pair_pick_system(kind), lang, rk.RU_NAMES_PAIR), max_tokens=4000,
                       tools=tools, background=background)
        a, b = p.get("a") or {}, p.get("b") or {}
        if not a.get("title") or not b.get("title"):
            raise ReelError("Claude не подобрал пару")
        got = []
        for side, role in ((a, spec["roles"][0]), (b, spec["roles"][1])):
            w = {k: side.get(k) for k in ("title", "author", "year", "museum", "commons", "role", "ru")}
            w["need"] = rk.PAIR_NEED.get(side.get("role") or "", "") or rk.PAIR_NEED.get(role, "")
            if kind == "film" and side is b:
                w["cands"] = await tmdb_candidates(client, b.get("film") or {"title": b.get("title"), "year": b.get("year")})
                if not w["cands"] and not w.get("commons"):
                    raise ReelError("кадров фильма нет: задай TMDB_API_KEY или возьми фильм в общественном достоянии")
            museums = (side.get("role") or "").lower() in ("drawing", "painted", "painting", "visible", "a", "b")
            good = await verify(client, [w], background, per=4, museums=museums)
            if not good:
                raise ReelError(f"не нашлось хорошей картинки «{w['title']}» — попробуй другую пару")
            got.append(good[0])
        d.update(pair={"a": got[0], "b": got[1], "title": p.get("title") or a["title"], "answer": p.get("answer"),
                       "title_ru": p.get("title_ru") or ""},
                 title=p.get("title") or a["title"], music=(p.get("music") or [])[:3], facts=p.get("facts") or [])
        d.pop("story", None)
    pr = d["pair"]
    paths = []
    for n, side in enumerate(("a", "b")):
        it = pr[side]
        path = Path(it.get("path") or folder / f"pair_{side}.jpg")
        if not path.exists():
            await fetch_image(client, it, path)
        it["path"] = str(path)
        paths.append(path)
    if not d.get("story"):
        blocks = []
        for side, path in zip(("A", "B"), paths):
            it = pr[side.lower()]
            blocks.append({"type": "text", "text": f"Image {side}: {it.get('title')} — {it.get('author') or ''}, {it.get('year') or ''}"})
            with Image.open(path) as im:
                blocks.append(_img_block(im.convert("RGB"), 1400))
        avoid = d.get("avoid_frames") or []
        text = ("Facts:\n" + "\n".join(f"- {f}" for f in d.get("facts") or [])
                + (f"\nThe answer to the question: image {str(pr.get('answer')).upper()}" if kind == "which" and pr.get("answer") else "")
                + ("\n\nThe previous version used these lines, write a different story:\n" + "\n".join(avoid) if avoid else ""))
        st = await _ask(blocks + [{"type": "text", "text": text}], system=rk.localize(rk.pair_story_system(kind), lang),
                        max_tokens=5000,
                        background=background)
        hooks = [h for h in st.get("hooks") or [] if h.get("text")]
        reveals = [r for r in st.get("reveals") or [] if r.get("text")][:4]
        if not hooks or len(reveals) < 2:
            raise ReelError("Claude не собрал сюжет — попробуй другую пару")
        try:
            pick = int(st.get("hook_pick") or 0)
        except (TypeError, ValueError):
            pick = 0
        pick = pick if 0 <= pick < len(hooks) else 0
        hooks = [hooks[pick]] + [h for i, h in enumerate(hooks) if i != pick]
        fin = st.get("final")
        d["story"] = {"hooks": hooks, "hook_i": 0, "context": st.get("context") if isinstance(st.get("context"), dict)
                      else {"text": st.get("context") or "", "show": "a"}, "reveals": reveals,
                      "climax": st.get("climax") or {}, "final": fin if isinstance(fin, dict) else {"text": fin or "", "show": "b"},
                      "voice_direction": st.get("voice_direction") or ""}
        d.update(caption=st.get("caption") or "", hashtags=st.get("hashtags") or [])
        # рамки деталей — вторым проходом, отдельно по каждой картинке
        parts = [d["story"]["hooks"], d["story"]["reveals"], [d["story"]["climax"]]]
        for side, path in zip(("a", "b"), paths):
            sub = {"hooks": [x for x in parts[0] if str(x.get("show")).lower() == side and _box(x.get("box"))],
                   "reveals": [x for x in parts[1] if str(x.get("show")).lower() == side and _box(x.get("box"))]}
            cl = d["story"]["climax"]
            if str(cl.get("show")).lower() == side and _box(cl.get("box")):
                sub["climax"] = cl
            if sub["hooks"] or sub["reveals"] or sub.get("climax"):
                await refine_boxes(path, sub, background)
    bs = beats(d)
    voice = await _voice(folder, d, bs)
    video = folder / f"reel_{int(datetime.now().timestamp())}.mp4"
    d.pop("render_note", None)
    if not reelplan.available():
        raise ReelError(f"этот формат собирается только новой вёрсткой: {reelplan.why_not()}")
    layout = spec["layout"]
    if kind == "layer":
        # шторка работает, только если слои совпадают: совмещаем по общим точкам, не вышло — рядом
        al = await asyncio.to_thread(reelplan.align, paths[0], paths[1], folder / "aligned.jpg")
        if al:
            paths = list(al)
        else:
            layout = "split"
    sizes = [reelplan._size(x) for x in paths]
    ra, rb = sizes[0][0] / sizes[0][1], sizes[1][0] / sizes[1][1]
    if layout in ("wipe", "dissolve") and max(ra, rb) / min(ra, rb) > 1.6:
        layout = "split"            # слишком разные пропорции: общее окно срежет полкартинки — показываем каждую целиком
    roles = rk.roles(kind, lang)
    if lang == "ru":
        await _ensure_ru([pr["a"], pr["b"]], background, reel_name=pr)
    sa, sb = _disp(pr["a"], lang), _disp(pr["b"], lang)
    name = (pr.get("title_ru") if lang == "ru" else None) or pr.get("title") or d.get("title") or ""

    def who(it):
        return ", ".join(x for x in (it.get("author"), str(it.get("year") or "")) if x and x != "None")
    tags = [f"{roles[0]}" + (f" · {sa['year']}" if sa.get("year") and kind != "which" else ""),
            f"{roles[1]}" + (f" · {sb['year']}" if sb.get("year") and kind != "which" else "")]
    meta = [[roles[0], f"{sa.get('title')}" + (f", {who(sa)}" if who(sa) else "")],
            [roles[1], f"{sb.get('title')}" + (f", {who(sb)}" if who(sb) else "")]]
    info = {"title": name, "rubric": rk.rubric(kind, lang), "roles": roles, "tags": tags,
            "meta": meta, "focus": [pr["a"].get("focus") or [0.5, 0.5], pr["b"].get("focus") or [0.5, 0.5]],
            "series": name, "lang": lang}
    if kind == "plan":
        info.update(ink=True, paper=reelplan.paper_color(paths[0]))
    props = await asyncio.to_thread(reelplan.pair_props, folder, paths, bs, voice, info, layout, await sfx_on())
    d["duration"] = await asyncio.to_thread(reelplan.render, "Pair", props, folder, video)
    d["video"], d["cover_t"] = str(video), props.get("coverT")
    return d


async def _voice(folder: Path, d: dict, bs: list[dict]) -> list | dict | None:
    """Озвучить вступление и фразы деталей. Уже озвученное (тот же текст, тот же голос) не синтезируется заново.
    Не вышло — рилс собирается без звука, а в d["voice_note"] — почему."""
    d.pop("voice_note", None)
    lang = rk.lang_of(d)
    if await tts.provider(lang) == "off":
        return None
    vkey = await tts.current_key(lang)
    spare = tts.FALLBACK[lang]
    if tts.is_one_take(vkey):
        parts = [(b["kind"], b["text"], b.get("how") or "") for b in bs]
        extra = (d.get("story") or {}).get("voice_direction") or ""
        # запасной дубль: ElevenLabs не вышел — OpenAI (если есть ключ)
        chain = [vkey] + (["openai:cedar"] if tts.OA_KEY and not vkey.startswith("openai:") else [])
        fails = []
        for v in chain:
            # «v2» — время слов считается по-новому (5.1): старые дубли с ошибочным временем не берём из кэша
            key = hashlib.md5(f"v2|{v}|{json.dumps(parts, ensure_ascii=False)}|{extra}".encode()).hexdigest()[:12]
            hit = (d.get("voice") or {}).get(key)
            if hit and Path(hit["audio"]).exists():
                d["voice_label"] = _voice_label(v, hit)
                return hit
            try:
                res = await tts.speak_story(parts, folder / "voice" / f"take_{key}", extra, v, lang)
                if res.get("approx"):
                    d["voice_note"] = "время слов не сошлось с текстом — слова идут по голосу приблизительно"
                if fails:
                    d["voice_note"] = f"{fails[0]} — прочитал {tts.VOICES.get(v, (v,))[0]}"
                d["voice"] = {key: res}
                d["voice_label"] = _voice_label(v, res)
                return res
            except Exception as exc:
                log.warning("Рилс: дубль %s не получился", v, exc_info=True)
                fails.append(str(exc)[:120] or type(exc).__name__)
        d["voice_note"] = f"{fails[0]} — прочитал запасной голос"
        vkey = spare
    cache = d.get("voice") or {}
    texts = [(b["text"], b.get("how") or "") for b in bs]
    out = []
    try:
        for text, how in texts:
            key = hashlib.md5(f"{vkey}|{tts.SPEED}|{text}|{how if vkey.startswith('openai:') else ''}".encode()).hexdigest()[:12]
            hit = cache.get(key)
            if hit and Path(hit["audio"]).exists():
                out.append(hit)
                continue
            res = (await tts.speak(text, folder / "voice" / key, how or None, lang) if vkey != spare
                   or not d.get("voice_note") else await tts._speak_with(vkey, text, folder / "voice" / key, lang=lang))
            if res and res.get("fallback"):
                d["voice_note"] = res["fallback"]
            if res and not res.get("fallback"):     # запасной голос не запоминаем — в следующий раз попробуем выбранный
                cache[key] = res
            out.append(res)
    except Exception as exc:
        log.warning("Рилс: озвучка не получилась", exc_info=True)
        d["voice_note"] = f"голос не получился ({str(exc)[:80]}) — видео без озвучки"
        return None
    d["voice"] = {k: v for k, v in cache.items() if v in out}
    d["voice_label"] = _voice_label(vkey)
    return out


def _voice_label(v: str, res: dict | None = None) -> str:
    name = tts.VOICES.get(v, (v.split(":")[-1],))[0]
    by = (res or {}).get("by") or {"openai": "OpenAI", "elevenlabs": "ElevenLabs", "kokoro": "Kokoro",
                                    "edge": "Edge"}.get(v.split(":")[0], "")
    return f"голос {name}" + (f" ({by})" if by else "")


async def generate(bot: Bot, rid: int, background: bool = True) -> None:
    """Собрать (или пересобрать) рилс и прислать карточку. Ошибка — уведомление с кнопкой «ещё раз»."""
    from app import curator
    async with _gen_lock:
        r = await get(rid)
        if not r:
            return
        await _set(rid, status="building", note=None)
        d = _data(r)
        old_video = d.get("video")
        try:
            if not reelrender.ffmpeg_ok():
                raise ReelError("на сервере нет ffmpeg — проверь, что в requirements.txt есть imageio-ffmpeg")
            d["kind"] = r["kind"]
            eng = rk.engine(r["kind"])
            async with httpx.AsyncClient(headers=UA, follow_redirects=True) as client:
                d = await {"collection": _collection, "pair": _pair}.get(eng, _details)(rid, d, background, client)
        except Exception as exc:
            log.exception("Рилс %s", rid)
            why = str(exc) if isinstance(exc, ReelError) else curator.explain(exc)
            await _set(rid, status="failed", note=why[:300], data=d)
            await screen.notify(bot, f"🎬 Рилс ({KIND_RU.get(r['kind'], r['kind'])}) не собрался: {why}"[:900],
                                [("🔁 Ещё раз", f"rl:retry:{rid}"), ("🎬 Рилсы", "rl:go")])
            return
        if old_video and old_video != d["video"]:
            Path(old_video).unlink(missing_ok=True)
        d.pop("request_note", None)
        await _set(rid, status="ready", title=d.get("title"), data=d, video=d["video"])
    await send_card(bot, rid)
    screen.refresh_soon(bot)


def start(bot: Bot, rid: int, background: bool = False) -> None:
    asyncio.create_task(generate(bot, rid, background))


async def new(bot: Bot, kind: str, request: str | None = None, day: str | None = None,
              background: bool = False, lang: str = "en") -> int:
    lang = lang if lang in rk.LANGS else "en"
    day = day or await free_day()
    rid = await _add(kind, day, {"lang": lang, **({"request": request} if request else {})})
    await db.set_setting("reel_last_lang", lang)
    await _drop_ask(bot, day)                   # на этот день рилс уже есть — вопрос о формате не нужен
    start(bot, rid, background)
    return rid


def lang_rows(prefix: str) -> list:
    """Кнопки языка: callback = prefix + en | ru."""
    return [[btn("RU · по-русски", f"{prefix}ru"), btn("EN · in English", f"{prefix}en")]]


# ======================= карточка =======================

def _music_lines(d: dict) -> list[str]:
    out = []
    for m in d.get("music") or []:
        if m.get("artist") and m.get("track"):
            out.append(f"• {html.escape(m['artist'])} — {html.escape(m['track'])}"
                       + (f" <i>({html.escape(m['mood'])})</i>" if m.get("mood") else ""))
    return out


def _card_text(r, d: dict, note: str | None = None) -> str:
    when = f"{human(r['day'])} в {TIME[0]:02d}:{TIME[1]:02d}"
    st = {"ready": "ждёт решения", "approved": "одобрен", "sent": "ждёт публикации", "posted": "выложен"}.get(
        r["status"], r["status"])
    lang = rk.lang_of(d)
    lines = [f"🎬 <b>Рилс · {KIND_RU.get(r['kind'], r['kind'])} · {rk.LANGS[lang]}</b> · {when} · {st}"]
    if d.get("auto_pick") and r["status"] == "ready":
        lines.append("<i>Формат не выбрал — собрал сам: формат по кругу, язык как в прошлый раз.</i>")
    if note:
        lines.append(f"<b>{html.escape(note)}</b>")
    if r["kind"] == "collection":
        lines.append(f"\n<b>{html.escape(d.get('title') or '')}</b>" + (f" — {html.escape(d['theme_ru'])}"
                                                                        if d.get("theme_ru") else ""))
        if d.get("intro") and d.get("voice"):
            lines.append(f"🪝 <b>{html.escape(d['intro'].replace('*', ''))}</b>")
        for n, it in enumerate(d.get("items") or [], 1):
            w = _disp(it, lang)
            lines.append(f"{n}. {html.escape(w['title'])} — {html.escape(w['author'])}"
                         + (f", {html.escape(str(w['year']))}" if w.get("year") else ""))
    else:
        def line(it):
            it = _disp(it, lang)
            return (f"<b>{html.escape(it.get('title') or '')}</b> — {html.escape(it.get('author') or '')}"
                    + (f", {html.escape(str(it['year']))}" if it.get("year") else ""))
        if d.get("pair"):
            roles = rk.roles(r["kind"], lang)
            lines.append(f"\n{roles[0]}: {line(d['pair']['a'])}\n{roles[1]}: {line(d['pair']['b'])}")
        else:
            lines.append("\n" + line(d.get("painting") or {}))
        cut = lambda x: x if len(x) <= 95 else x[:92].rsplit(" ", 1)[0] + "…"
        mark = {"hook": "🪝", "context": "·", "reveal": "·", "climax": "❗️", "final": "↩️"}
        side = {"a": "A", "b": "B", "both": "A+B"}
        st = d.get("story") or {}
        for b in beats(d):
            tag = f"<i>[{side[b['show']]}]</i> " if b.get("show") else ""
            if b["kind"] == "hook":
                hooks = st.get("hooks") or []
                h = hooks[st.get("hook_i", 0) % len(hooks)]
                lines.append(f"🪝 {tag}<b>{html.escape(b['text'])}</b> <i>({HOOK_TYPES.get(h.get('type'), 'хук')}, "
                             f"{st.get('hook_i', 0) % len(hooks) + 1} из {len(hooks)})</i>")
            else:
                lines.append(f"{mark[b['kind']]} {tag}{html.escape(cut(b['text']))}")
    mus = _music_lines(d)
    if mus:
        lines += ["", "🎵 Музыка:"] + mus
    if d.get("render_note"):
        lines.append(f"\n⚠️ {html.escape(d['render_note'])}")
    if r["kind"] != "collection" or d.get("voice"):
        lines.append("\n" + (f"⚠️ {html.escape(d['voice_note'])}" if d.get("voice_note") else f"🎙 {html.escape(d.get('voice_label') or '')}"))
    lines.append(f"\n⏱ {d.get('duration', 0):.0f} с · подпись {rk.LANG_RU[lang]} придёт в день выхода")
    text = "\n".join(lines)
    return text if len(text) <= 1024 else text[:1020] + "…"


def kinds_rows(prefix: str, skip: str | None = None) -> list:
    """Кнопки форматов по два в ряд: callback = prefix + вид."""
    b = [btn(f"{rk.KINDS[k]['icon']} {rk.KINDS[k]['ru'].capitalize()}", f"{prefix}{k}") for k in rk.ORDER if k != skip]
    return [b[i:i + 2] for i in range(0, len(b), 2)]


def _card_kb(r, sub: str | None = None) -> InlineKeyboardMarkup:
    rid, d = r["id"], _data(r)
    if sub == "kinds":
        return screen._kb([[btn("Собрать заново в другом формате:", "rl:noop")]] + kinds_rows(f"rl:kind:{rid}:", r["kind"])
                          + [[btn("← Назад", f"rl:redo:{rid}")]])
    if sub == "redo":
        rows = []
        other_lang = btn("🌐 Переделать в EN" if rk.lang_of(d) == "ru" else "🌐 Переделать в RU", f"rl:lang:{rid}")
        if r["kind"] == "collection":
            rows.append([btn("🔀 Другая тема", f"rl:theme:{rid}"), btn("🔄 Другой формат", f"rl:kinds:{rid}")])
            rows.append([other_lang])
            nums = [btn(f"🖼 {n}", f"rl:rep:{rid}:{n}") for n in range(1, len(d.get("items") or []) + 1)]
            if nums:
                rows.append([btn("Заменить работу:", "rl:noop")])
                rows += [nums[i:i + 4] for i in range(0, len(nums), 4)]
        else:
            if len((d.get("story") or {}).get("hooks") or []) > 1:
                rows.append([btn("🪝 Другой хук", f"rl:hook:{rid}")])
            obj = rk.KINDS.get(r["kind"], {}).get("obj", "картина")
            other = {"картина": "Другая картина", "фотография": "Другая фотография", "здание": "Другое здание",
                     "пара": "Другая пара", "место": "Другое место"}.get(obj, "Другой объект")
            rows.append([btn(f"🔀 {other}", f"rl:theme:{rid}"), btn("🎯 Другой сюжет", f"rl:det:{rid}")])
            rows.append([btn("🔄 Другой формат", f"rl:kinds:{rid}"), other_lang])
        rows.append([btn("✏️ Подпись", f"rl:cap:{rid}"), btn("← Назад", f"rl:back:{rid}")])
        return screen._kb(rows)
    if r["status"] == "ready":
        return screen._kb([[btn("✅ Беру", f"rl:ok:{rid}"), btn("🔁 Переделать", f"rl:redo:{rid}"),
                            btn("❌ Не надо", f"rl:no:{rid}")]])
    if r["status"] == "approved":
        return screen._kb([[btn("📤 Выложить сейчас", f"rl:now:{rid}"), btn("↩️ Снять", f"rl:undo:{rid}")],
                           [btn("🔁 Переделать", f"rl:redo:{rid}")]])
    return screen._kb([[btn("🎬 Рилсы", "rl:go")]])


async def send_card(bot: Bot, rid: int, note: str | None = None) -> None:
    r = await get(rid)
    if not r or not r["video"] or not Path(r["video"]).exists():
        return
    d = _data(r)
    await ui.drop(bot, r["card_msg"])
    m = await bot.send_video(config.ADMIN_ID, FSInputFile(r["video"]), caption=_card_text(r, d, note),
                             reply_markup=_card_kb(r), width=reelrender.W, height=reelrender.H,
                             duration=int(d.get("duration") or 0), supports_streaming=True)
    await _set(rid, card_msg=m.message_id)


async def _refresh_card(cb: CallbackQuery, rid: int, note: str | None = None, sub: str | None = None) -> None:
    r = await get(rid)
    if not r:
        return
    try:
        await cb.message.edit_caption(caption=_card_text(r, _data(r), note), reply_markup=_card_kb(r, sub))
    except Exception:
        pass


# ======================= публикация (руками автора) =======================

async def send_package(bot: Bot, rid: int) -> None:
    """«Пора выкладывать»: видео файлом (без сжатия), подпись одним блоком, треки."""
    r = await get(rid)
    if not r or not r["video"] or not Path(r["video"]).exists():
        return
    d = _data(r)
    old = json.loads(r["pkg_msgs"] or "[]")
    await ui.drop(bot, *old)
    doc = await bot.send_document(config.ADMIN_ID, FSInputFile(r["video"], filename=f"ahmag_reel_{rid}.mp4"),
                                  caption=f"🎬 Пора выкладывать: <b>{html.escape(r['title'] or '')}</b>",
                                  disable_content_type_detection=True)
    ids = [doc.message_id]
    cov = await asyncio.to_thread(reelplan.cover, Path(r["video"]), float(d.get("cover_t") or 1.2),
                                  Path(r["video"]).with_name(f"cover_{rid}.jpg"))
    if cov:
        ph = await bot.send_document(config.ADMIN_ID, FSInputFile(cov, filename=f"ahmag_cover_{rid}.jpg"),
                                     caption="🖼 Обложка: кадр с хуком. Хук стоит в середине — сетка профиля (3:4) его не срежет.",
                                     disable_content_type_detection=True)
        ids.append(ph.message_id)
    cap = build_caption(r["kind"], d)
    mus = _music_lines(d)
    fresh = r["kind"] not in await _posted_kinds()
    text = ("<b>Подпись</b> — нажми на блок, чтобы скопировать:\n"
            f"<pre>{html.escape(cap)}</pre>"
            + ("\n\n🎵 " + "\n".join(mus) if mus else "")
            + ("\n\nВ видео уже есть голос: музыку в Instagram ставь потише, около 15–25%."
               if d.get("voice") and not (d.get("voice_note") or "").startswith("голос не получился") else "")
            + (f"\n\n🧪 Первый рилс в формате «{KIND_RU[r['kind']]}» — можно выложить пробным (Trial): его увидят "
               "только не подписчики, и по цифрам будет видно, заходит ли формат." if fresh else "")
            + "\n\nInstagram → Reels → это видео → музыка → подпись → обложка из файла выше.")
    if len(text) > 4000:
        text = text[:3990] + "…</pre>"
    msg = await bot.send_message(config.ADMIN_ID, text, disable_web_page_preview=True,
                                 reply_markup=screen._kb([[btn("✅ Выложил", f"rl:done:{rid}"),
                                                           btn("⏭ Завтра", f"rl:later:{rid}")]]))
    ids.append(msg.message_id)
    d["sent_at"] = db.now()
    d.pop("reminded", None)
    await _set(rid, status="sent", pkg_msgs=json.dumps(ids), data=d)
    await ui.drop(bot, r["card_msg"])
    screen.refresh_soon(bot)


# ======================= расписание =======================

async def plan_next(bot: Bot) -> None:
    """Накануне дня рилса — собрать его, если автор не выбрал формат сам (вопрос в REELS_ASK_TIME): формат по
    кругу, язык — как в прошлый раз. Если сегодня день рилса, а его нет и время не прошло, — и на сегодня."""
    if not ENABLED:
        return
    taken = await _taken_days()
    today = _now().date()
    targets = [today + timedelta(days=1)]
    if _now() < _post_dt(today.isoformat()) - timedelta(hours=1):
        targets.insert(0, today)
    asked = await db.get_setting("reel_ask") or {}
    for d in targets:
        if is_reel_day(d) and d.isoformat() not in taken:
            kind = await next_kind()
            lang = await last_lang()
            data = {"lang": lang, **({"auto_pick": True} if asked.get("day") == d.isoformat() else {})}
            await _drop_ask(bot, d.isoformat())
            rid = await _add(kind, d.isoformat(), data)
            await generate(bot, rid, background=True)


async def last_lang() -> str:
    lang = await db.get_setting("reel_last_lang", "en")
    return lang if lang in rk.LANGS else "en"


# ---------- вопрос «какой формат завтра» ----------

def _ask_text(day: str, kind: str | None = None) -> str:
    when = human(day)
    if kind:
        what = "на выбор бота (по кругу)" if kind == "auto" else f"{rk.KINDS[kind]['icon']} {KIND_RU[kind]}"
        return f"🎬 Рилс на {when}: {what}.\nНа каком языке?"
    return (f"🎬 {when.capitalize()} день рилса. Какой формат собрать?\n"
            f"<i>Не выберешь до {BUILD_TIME[0]:02d}:{BUILD_TIME[1]:02d} — соберу сам: формат по кругу, "
            "язык как в прошлый раз.</i>")


def _ask_kb(day: str) -> InlineKeyboardMarkup:
    return screen._kb(kinds_rows(f"rl:ak:{day}:") + [[btn("🎲 На выбор бота", f"rl:ak:{day}:auto")]])


async def send_ask(bot: Bot, day: str) -> None:
    """Спросить, какой формат и язык собрать на день day. Старый вопрос убирается."""
    old = await db.get_setting("reel_ask") or {}
    await ui.drop(bot, old.get("msg"))
    m = await bot.send_message(config.ADMIN_ID, _ask_text(day), reply_markup=_ask_kb(day))
    await db.set_setting("reel_ask", {"day": day, "msg": m.message_id})


async def _drop_ask(bot: Bot, day: str | None = None) -> None:
    """Убрать вопрос о формате (на день day или любой)."""
    old = await db.get_setting("reel_ask") or {}
    if old and (day is None or old.get("day") == day):
        await ui.drop(bot, old.get("msg"))
        await db.set_setting("reel_ask", {})


async def pending_ask() -> str | None:
    """День, на который бот спросил формат и ещё ждёт ответа."""
    old = await db.get_setting("reel_ask") or {}
    day = old.get("day")
    if not day or day < _now().date().isoformat() or day in await _taken_days():
        return None
    return day


async def ask_next(bot: Bot) -> None:
    """Накануне дня рилса, до сборки, — спросить формат и язык. Если рилс на завтра уже есть — не спрашивать."""
    if not ENABLED:
        return
    day = _now().date() + timedelta(days=1)
    if is_reel_day(day) and day.isoformat() not in await _taken_days():
        await send_ask(bot, day.isoformat())


async def next_kind() -> str:
    """Следующий формат для автоплана: по кругу REEL_ROTATION, с весами из «📊 Что заходит» (если приняты).
    Взвешенный круг: у каждого формата копится «очередь» = вес, берётся тот, у кого она больше."""
    rot = rk.ROTATION or ["details", "collection"]
    weights = await db.get_setting("reel_weights", {}) or {}
    if not weights:
        i = int(await db.get_setting("reel_rot_i", 0) or 0)
        await db.set_setting("reel_rot_i", i + 1)
        return rot[i % len(rot)]
    kinds = list(dict.fromkeys(rot))
    share = {k: rot.count(k) * float(weights.get(k, 1.0)) for k in kinds}
    credit = await db.get_setting("reel_credit", {}) or {}
    total = sum(share.values()) or 1.0
    for k in kinds:
        credit[k] = float(credit.get(k, 0.0)) + share[k] / total
    kind = max(kinds, key=lambda k: credit[k])
    credit[kind] -= 1.0
    await db.set_setting("reel_credit", credit)
    return kind


async def due(bot: Bot) -> None:
    """Время выкладывать: одобренные на сегодня (и просроченные одобренные) — пакетом. Не решённый — напомнить."""
    today = _now().date().isoformat()
    for r in await items("approved"):
        if r["day"] <= today:
            await send_package(bot, r["id"])
    for r in await items("ready"):
        if r["day"] == today:
            await screen.notify(bot, f"🎬 Рилс на сегодня ещё ждёт решения: {html.escape(r['title'] or '')}",
                                [("👁 Показать", f"rl:card:{r['id']}")])
        elif r["day"] < (_now().date() - timedelta(days=1)).isoformat():
            await _set(r["id"], status="expired")
            await ui.drop(bot, r["card_msg"])
    screen.refresh_soon(bot)


async def remind(bot: Bot) -> None:
    for r in await items("sent"):
        d = _data(r)
        if d.get("reminded") or not d.get("sent_at"):
            continue
        if datetime.fromisoformat(d["sent_at"]) < _now() - timedelta(hours=REMIND_HOURS):
            d["reminded"] = True
            await _set(r["id"], data=d)
            await screen.notify(bot, f"🎬 Рилс «{html.escape(r['title'] or '')}» ещё не отмечен как выложенный. "
                                     "Видео и подпись — выше в чате.", [("🎬 Рилсы", "rl:go")])


async def cleanup() -> None:
    """Файлы рилсов, которые уже выложены или сняты больше недели назад."""
    edge = db.days_ago(7)
    for r in await items("posted", "rejected", "expired", "failed", limit=500):
        if r["updated_at"] < edge:
            shutil.rmtree(DIR / str(r["id"]), ignore_errors=True)


def schedule(sched, bot: Bot, guarded) -> None:
    if not ENABLED:
        return
    if ASK_TIME < BUILD_TIME:
        sched.add_job(guarded(bot, "рилс: какой формат", ask_next, bot), "cron", hour=ASK_TIME[0], minute=ASK_TIME[1],
                      id="reels_ask", max_instances=1)
    else:
        log.warning("REELS_ASK_TIME не раньше REELS_BUILD_TIME — бот не будет спрашивать формат, соберёт сам")
    sched.add_job(guarded(bot, "рилс: сборка", plan_next, bot), "cron", hour=BUILD_TIME[0], minute=BUILD_TIME[1],
                  id="reels_build", max_instances=1)
    sched.add_job(guarded(bot, "рилс: выкладывать", due, bot), "cron", hour=TIME[0], minute=TIME[1], id="reels_due")
    sched.add_job(guarded(bot, "рилс: напоминание", remind, bot), "interval", minutes=30, id="reels_remind")
    sched.add_job(guarded(bot, "рилс: очистка", cleanup), "cron", hour=4, minute=40, id="reels_cleanup")
    sched.add_job(guarded(bot, "рилс: статистика", refresh_stats, bot), "interval", hours=6, id="reels_stats",
                  max_instances=1)


def install(bot: Bot) -> None:
    screen.VIEWS["reels"] = _v_reels
    screen.VIEWS["reelvoice"] = _v_voices
    screen.VIEWS["reelnew"] = _v_new
    screen.VIEWS["reelstats"] = _v_stats


async def home_label() -> str:
    n = len(await items("ready"))
    return "🎬 Рилсы" + (f" · {n} ждёт" if n else "")


# ======================= статистика рилсов =======================
# Рилсы выкладываются руками, поэтому бот сам находит их в Instagram: берёт последние публикации аккаунта
# (Graph API, IG_ACCESS_TOKEN + IG_USER_ID) и сопоставляет по первой строке подписи или по времени выкладки.
# Цифры — раз в 6 часов неделю после выхода, потом замораживаются: форматы сравниваются по одной неделе жизни.
# Нужно право instagram_business_manage_insights (то же, что для статистики постов).

REEL_METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
                "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate"]


def _norm(x: str) -> str:
    return re.sub(r"\W+", " ", (x or "").lower()).strip()


def _ts(x: str) -> datetime | None:
    try:
        return datetime.strptime(x, "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(x)
        except (TypeError, ValueError):
            return None


async def _insights(client: httpx.AsyncClient, mid: str) -> dict:
    """Цифры рилса. Какие-то метрики Instagram может не отдать (старый токен, новая метрика) — берём те, что есть."""
    from app import instagram

    def parse(data):
        out = {}
        for m in data:
            val = (m.get("values") or [{}])[0].get("value") if m.get("values") else (m.get("total_value") or {}).get("value")
            if m.get("name") and val is not None:
                out[m["name"]] = val
        return out
    try:
        return parse((await instagram._get(client, f"{mid}/insights", metric=",".join(REEL_METRICS))).get("data") or [])
    except Exception:
        out = {}
        for name in REEL_METRICS:
            try:
                out.update(parse((await instagram._get(client, f"{mid}/insights", metric=name)).get("data") or []))
            except Exception:
                continue
        return out


async def refresh_stats(bot: Bot | None = None) -> int:
    """Найти выложенные рилсы в Instagram и снять цифры. → сколько рилсов обновлено."""
    from app import instagram
    if not instagram.configured():
        return 0
    rows = [r for r in await items("posted", limit=60) if not _data(r).get("stats_final")]
    if not rows:
        return 0
    done = 0
    async with httpx.AsyncClient(headers=UA) as client:
        try:
            media = (await instagram._get(client, f"{instagram.ENV_USER}/media", limit=40,
                                          fields="id,caption,media_product_type,timestamp,permalink")).get("data") or []
        except Exception as exc:
            await db.set_setting("reel_stats_error", str(exc)[:200])
            return 0
        await db.set_setting("reel_stats_error", None)
        reels_ig = [m for m in media if m.get("media_product_type") == "REELS"]
        taken = {_data(r).get("ig_media") for r in await items("posted", limit=300)} - {None}
        for r in rows:
            d = _data(r)
            if not d.get("ig_media"):
                since = _ts(d.get("sent_at") or d.get("posted_at") or r["updated_at"])
                first = _norm(build_caption(r["kind"], d).split("\n")[0])[:50]
                cands = [m for m in reels_ig if m["id"] not in taken and since and _ts(m.get("timestamp"))
                         and since - timedelta(hours=3) <= _ts(m["timestamp"]) <= since + timedelta(days=3)]
                hit = next((m for m in cands if first and first[:40] in _norm(m.get("caption"))), None)
                if not hit and len(cands) == 1:
                    hit = cands[0]
                if not hit:
                    continue
                d.update(ig_media=hit["id"], ig_link=hit.get("permalink"), ig_time=hit.get("timestamp"))
                taken.add(hit["id"])
            st = await _insights(client, d["ig_media"])
            if st:
                d["stats"] = {**st, "at": db.now()}
                age = datetime.now(_ts(d["ig_time"]).tzinfo) - _ts(d["ig_time"]) if _ts(d.get("ig_time")) else timedelta(0)
                if age >= timedelta(days=7):
                    d["stats_final"] = True
                done += 1
            await _set(r["id"], data=d)
    return done


def _score(st: dict, dur: float) -> dict:
    """Показатели одного рилса: доля досмотра, пролистывания, пересылки и сохранения на 1000 охвата."""
    reach = float(st.get("reach") or st.get("views") or 0) or 1.0
    watch = float(st.get("ig_reels_avg_watch_time") or 0) / 1000
    skip = st.get("reels_skip_rate")
    return {"watch": watch, "ratio": min(1.5, watch / dur) if dur else 0.0,
            "skip": float(skip) / (100 if float(skip or 0) > 1 else 1) if skip is not None else None,
            "shares": 1000 * float(st.get("shares") or 0) / reach, "saves": 1000 * float(st.get("saved") or 0) / reach,
            "views": int(st.get("views") or 0)}


async def _stat_rows(days: int = 120) -> list[tuple]:
    out = []
    for r in await items("posted", limit=300):
        d = _data(r)
        if d.get("stats") and r["day"] >= (_now().date() - timedelta(days=days)).isoformat():
            st = d.get("story") or {}
            hooks = st.get("hooks") or []
            htype = hooks[st.get("hook_i", 0) % len(hooks)].get("type") if hooks else None
            out.append((r, d, _score(d["stats"], float(d.get("duration") or 30)), htype))
    return out


def _agg(xs: list[dict]) -> dict:
    def avg(k):
        v = [x[k] for x in xs if x.get(k) is not None]
        return sum(v) / len(v) if v else None
    return {k: avg(k) for k in ("watch", "ratio", "skip", "shares", "saves", "views")} | {"n": len(xs)}


def _kind_value(a: dict) -> float:
    """Одна цифра для сравнения форматов: удержание × досмотр × (1 + пересылки). Пересылки — главный сигнал охвата."""
    keep = 1 - (a["skip"] if a["skip"] is not None else 0.5)
    return keep * max(0.05, a["ratio"] or 0) * (1 + (a["shares"] or 0) / 10)


async def suggest_weights() -> dict:
    """Веса форматов для автоплана по статистике: формат лучше среднего — чаще (до ×2), хуже — реже (до ×0,5).
    Форматы без двух выложенных рилсов с цифрами — вес 1."""
    by: dict[str, list] = {}
    for r, d, sc, _ in await _stat_rows():
        by.setdefault(r["kind"], []).append(sc)
    vals = {k: _kind_value(_agg(v)) for k, v in by.items() if len(v) >= 2}
    if not vals:
        return {}
    mean = sum(vals.values()) / len(vals)
    return {k: round(max(0.5, min(2.0, v / mean)), 2) for k, v in vals.items()}


def _fmt(a: dict) -> str:
    parts = [f"👁 {a['views']:.0f}" if a.get("views") else None,
             f"⏭ {a['skip'] * 100:.0f}%" if a.get("skip") is not None else None,
             f"⏱ {a['watch']:.1f} с" if a.get("watch") else None,
             f"↗ {a['shares']:.1f}‰" if a.get("shares") is not None else None,
             f"🔖 {a['saves']:.1f}‰" if a.get("saves") is not None else None]
    return " · ".join(x for x in parts if x)


async def _v_stats(arg: dict):
    rows = await _stat_rows()
    lines = ["<b>📊 Рилсы: что заходит</b>",
             "<i>⏭ пролистнули в первые секунды · ⏱ смотрят в среднем · ↗ пересылки и 🔖 сохранения на 1000 охвата. "
             "Цифры — за неделю после выхода.</i>"]
    err = await db.get_setting("reel_stats_error")
    if err:
        lines.append(f"⚠️ Instagram: {html.escape(err)}")
    if not rows:
        lines.append("\nЦифр пока нет: бот находит выложенные рилсы в Instagram сам, раз в 6 часов после «✅ Выложил».")
    else:
        by: dict[str, list] = {}
        hooks: dict[str, list] = {}
        for r, d, sc, ht in rows:
            by.setdefault(r["kind"], []).append(sc)
            if ht:
                hooks.setdefault(ht, []).append(sc)
        lines.append("\n<b>Форматы</b>")
        for k, xs in sorted(by.items(), key=lambda kv: -_kind_value(_agg(kv[1]))):
            lines.append(f"{rk.KINDS.get(k, {}).get('icon', '•')} {KIND_RU.get(k, k)} ({len(xs)}): {_fmt(_agg(xs))}")
        if len(hooks) > 1:
            lines.append("\n<b>Хуки</b>")
            for k, xs in sorted(hooks.items(), key=lambda kv: (_agg(kv[1])["skip"] or 1)):
                lines.append(f"🪝 {HOOK_TYPES.get(k, k)} ({len(xs)}): {_fmt(_agg(xs))}")
        lines.append("\n<b>Последние</b>")
        for r, d, sc, _ in rows[:5]:
            lines.append(f"• {human(r['day'])} · {html.escape((r['title'] or '')[:30])}: {_fmt(sc)}")
    w = await db.get_setting("reel_weights", {}) or {}
    sug = await suggest_weights()
    if w:
        lines.append("\n⚖️ Автоплан с весами: " + ", ".join(f"{KIND_RU.get(k, k)} ×{v}" for k, v in w.items()))
    elif sug:
        lines.append("\n⚖️ Можно чаще ставить то, что заходит: "
                     + ", ".join(f"{KIND_RU.get(k, k)} ×{v}" for k, v in sorted(sug.items(), key=lambda kv: -kv[1])))
    rows_kb = []
    if sug and sug != w:
        rows_kb.append([btn("⚖️ Чаще то, что заходит", "rl:wt:auto")])
    if w:
        rows_kb.append([btn("↺ Все форматы поровну", "rl:wt:reset")])
    rows_kb.append([btn("← Рилсы", "rl:home")])
    return screen.banner(), "\n".join(lines)[:1020], screen._kb(rows_kb), arg


async def _v_new(arg: dict):
    kind = arg.get("kind")
    if kind in rk.KINDS:
        # второй шаг: язык
        lines = ["<b>➕ Собрать рилс</b>", f"Формат: {rk.KINDS[kind]['icon']} {KIND_RU[kind]}.", "", "На каком языке?",
                 "<i>RU — рассказ, надписи, названия работ, подпись и хэштеги по-русски, читает русский голос.</i>"]
        kb = lang_rows(f"rl:nl:{kind}:") + [[btn("← Формат", "rl:newmenu"), btn("← Рилсы", "rl:home")]]
        return screen.banner(), "\n".join(lines)[:1020], screen._kb(kb), arg
    lines = ["<b>➕ Собрать рилс</b>", "Выбери формат, потом язык — рилс соберётся на ближайший свободный день, "
             "карточка придёт сообщением.", "",
             "🔍 детали картины · 📷 одна фотография — камера по деталям под рассказ",
             "🧍 масштаб — от человека к огромному зданию",
             "📐 разбор здания — ось, сетка, пропорции поверх фасада",
             "🖼 подборка — тема и 6–8 работ, с голосом",
             "✏️ 🕰 📍 🎞 🩻 ⚖️ пары — чертёж и здание, тогда и сейчас, картина и место, кадр и картина, "
             "что под слоем, какая из двух"]
    return screen.banner(), "\n".join(lines)[:1020], screen._kb(kinds_rows("rl:new:") + [[btn("← Рилсы", "rl:home")]]), arg


# ======================= экран =======================

async def _v_reels(arg: dict):
    days = ", ".join(DOW_RU[DOW_NUM[x]] for x in DOW if x in DOW_NUM)
    lines = [f"<b>🎬 Рилсы</b> — только Instagram · {days} в {TIME[0]:02d}:{TIME[1]:02d}",
             f"<i>Накануне в {ASK_TIME[0]:02d}:{ASK_TIME[1]:02d} спрошу формат и язык; не ответишь — в "
             f"{BUILD_TIME[0]:02d}:{BUILD_TIME[1]:02d} соберу сам. Музыку кладёшь ты при публикации.</i>"]
    if not ENABLED:
        lines.append("⚠️ Выключены переменной REELS=0")
    if not reelplan.available():
        lines.append(f"⚠️ Новая вёрстка недоступна ({reelplan.why_not()}) — видео соберётся запасной")
    if not await asyncio.to_thread(reelrender.ffmpeg_ok):
        lines.append("⚠️ Нет ffmpeg — видео не соберётся")
    if arg.get("note"):
        lines.append(f"<b>{html.escape(arg['note'])}</b>")
    rows_r = await items(limit=12)
    lines.append("")
    rows = []
    for r in rows_r[:10]:
        st = _data(r).get("stats") if r["status"] == "posted" else None
        ru_mark = " · RU" if rk.lang_of(_data(r)) == "ru" else ""
        lines.append(f"{ICON.get(r['status'], '•')} {human(r['day'])} · {KIND_RU.get(r['kind'], r['kind'])}{ru_mark} · "
                     f"{html.escape((r['title'] or '…')[:40])}"
                     + (f" — <i>{html.escape((r['note'] or '')[:60])}</i>" if r["status"] == "failed" else "")
                     + (f" — {_fmt(_score(st, float(_data(r).get('duration') or 30)))}" if st else ""))
        if r["status"] in ("ready", "approved", "sent") and r["video"]:
            rows.append([btn(f"{ICON[r['status']]} {human(r['day'])} · {(r['title'] or '')[:22]}", f"rl:card:{r['id']}")])
        elif r["status"] == "failed":
            rows.append([btn(f"🔁 Ещё раз · {human(r['day'])}", f"rl:retry:{r['id']}")])
    if not rows_r:
        lines.append("Пока рилсов не было.")
    ask = await pending_ask()
    if ask:
        lines.append(f"❓ {human(ask)} · жду, какой формат собрать (до {BUILD_TIME[0]:02d}:{BUILD_TIME[1]:02d})")
        rows.insert(0, [btn(f"🎯 Выбрать формат на {human(ask)}", "rl:askday")])
    lines.append("\n<i>📥 ждёт решения · 🟡 одобрен · 📤 ждёт публикации · ✅ выложен · ⏳ собирается</i>")
    rows.append([btn("➕ Собрать рилс", "rl:newmenu"), btn("✍️ Своя тема", "rl:ask")])
    rows.append([btn("📊 Что заходит", "rl:stats")])
    en, ru = await tts.voice("en"), await tts.voice("ru")
    vname = lambda v: tts.VOICES.get(v, (v.split(":")[-1],))[0]
    rows.append([btn(f"🎙 EN {vname(en)} · RU {vname(ru)}" if tts.ENABLED else "🎙 без озвучки", "rl:voices"),
                 btn("🔈 Звуки: вкл" if await sfx_on() else "🔇 Звуки: выкл", "rl:sfx")])
    rows.append([btn("← Пульт", "h:home")])
    return screen.banner(), "\n".join(lines)[:1020], screen._kb(rows), arg


VOICE_KEYS = {lang: tts.available(lang) for lang in tts.LANGS}


async def _v_voices(arg: dict):
    """Выбор голоса: отдельно для английских и для русских рилсов. Нажал — голос выбран, и приходит образец."""
    lang = arg.get("lang") if arg.get("lang") in tts.LANGS else "en"
    cur = await tts.voice(lang)
    lines = [f"<b>🎙 Голос рилсов · {rk.LANGS[lang]}</b>",
             "Нажми на голос — он станет основным для рилсов "
             + ("на русском" if lang == "ru" else "на английском") + ", и я пришлю образец послушать."]
    if lang == "ru":
        lines.append("<i>По-русски читают ElevenLabs, OpenAI и Edge. Kokoro русского не знает.</i>")
    if not tts.el_ready():
        lines.append("<i>Голоса ElevenLabs появятся, когда в Railway будет ELEVENLABS_API_KEY или FAL_KEY.</i>")
    elif not tts.EL_KEY:
        lines.append("<i>ElevenLabs — через fal.ai (FAL_KEY).</i>")
    if not tts.ENABLED:
        lines.append("⚠️ Озвучка выключена переменной REEL_TTS=0.")
    if arg.get("note"):
        lines.append(f"<b>{html.escape(arg['note'])}</b>")
    lines.append("")
    keys = VOICE_KEYS[lang]
    for k in keys:
        name, desc = tts.VOICES[k]
        lines.append(f"{'●' if k == cur else '○'} <b>{name}</b> — {desc}")
    rows = [[btn(("● " if lg == lang else "") + f"Голоса {rk.LANGS[lg]}", f"rl:voices:{lg}") for lg in tts.LANGS]]
    li = tts.LANGS.index(lang)
    b = [btn(("● " if k == cur else "") + tts.VOICES[k][0], f"rl:vs:{li}:{i}") for i, k in enumerate(keys)]
    rows += [b[i:i + 3] for i in range(0, len(b), 3)]
    rows.append([btn("← Рилсы", "rl:home")])
    return screen.banner(), "\n".join(lines)[:1020], screen._kb(rows), arg


# ======================= кнопки =======================

@router.callback_query(F.data.startswith("rl:"))
async def on_cb(cb: CallbackQuery, bot: Bot, state: FSMContext):
    p = cb.data.split(":")
    a = p[1]
    if a == "noop":
        return await cb.answer()
    if a == "go":
        await cb.answer()
        await ui.drop(bot, cb.message.message_id)
        return await screen.move_down(bot, "reels")
    if a == "home":
        await cb.answer()
        await screen.adopt(cb.message)
        return await screen.show(bot, "reels")
    if a == "new":
        # формат выбран — теперь язык
        kind = p[2]
        if kind not in rk.KINDS:
            return await cb.answer()
        await cb.answer()
        await screen.adopt(cb.message)
        return await screen.show(bot, "reelnew", kind=kind)
    if a == "nl":
        kind, lang = p[2], p[3] if len(p) > 3 else "en"
        if kind not in rk.KINDS or lang not in rk.LANGS:
            return await cb.answer()
        rid = await new(bot, kind, lang=lang)
        r = await get(rid)
        await cb.answer(f"Собираю {KIND_ACC[kind]} {rk.LANG_RU[lang]} на {human(r['day'])} — пара минут")
        await screen.adopt(cb.message)
        return await screen.show(bot, "reels", note=f"⏳ Собираю {KIND_ACC[kind]} {rk.LANG_RU[lang]}, "
                                                    "карточка придёт сообщением")
    if a == "ak":
        # вопрос «какой формат завтра»: выбран формат (или «на выбор бота») — спросить язык
        day, kind = p[2], p[3] if len(p) > 3 else ""
        if kind != "auto" and kind not in rk.KINDS:
            return await cb.answer()
        await cb.answer()
        try:
            return await cb.message.edit_text(_ask_text(day, kind), reply_markup=screen._kb(
                lang_rows(f"rl:al:{day}:{kind}:") + [[btn("← Формат", f"rl:ab:{day}")]]))
        except Exception:
            return
    if a == "ab":
        await cb.answer()
        try:
            return await cb.message.edit_text(_ask_text(p[2]), reply_markup=_ask_kb(p[2]))
        except Exception:
            return
    if a == "al":
        day, kind, lang = p[2], p[3], p[4] if len(p) > 4 else "en"
        if lang not in rk.LANGS or (kind != "auto" and kind not in rk.KINDS):
            return await cb.answer()
        await ui.drop(bot, cb.message.message_id)
        if day < _now().date().isoformat() or day in await _taken_days():
            await _drop_ask(bot, day)
            return await cb.answer("На этот день рилс уже есть — он в «🎬 Рилсы»", show_alert=True)
        if _now() >= _post_dt(day) - timedelta(minutes=10):
            await _drop_ask(bot, day)
            return await cb.answer("Время этого рилса уже прошло", show_alert=True)
        kind = await next_kind() if kind == "auto" else kind
        await new(bot, kind, day=day, lang=lang)
        await cb.answer(f"Собираю {KIND_ACC[kind]} {rk.LANG_RU[lang]} на {human(day)} — пара минут")
        return screen.refresh_soon(bot)
    if a == "askday":
        day = await pending_ask()
        if not day:
            await cb.answer("Уже не нужно — рилс на этот день есть")
            await screen.adopt(cb.message)
            return await screen.show(bot, "reels")
        await cb.answer()
        return await send_ask(bot, day)
    if a == "newmenu":
        await cb.answer()
        await screen.adopt(cb.message)
        return await screen.show(bot, "reelnew")
    if a == "stats":
        await cb.answer()
        await screen.adopt(cb.message)
        return await screen.show(bot, "reelstats")
    if a == "wt":
        # веса форматов в автоплане: по статистике или поровну
        if p[2] == "auto":
            w = await suggest_weights()
            await db.set_setting("reel_weights", w)
            await cb.answer("Автоплан будет чаще ставить то, что заходит")
        else:
            await db.set_setting("reel_weights", {})
            await cb.answer("Форматы снова по кругу, поровну")
        await screen.adopt(cb.message)
        return await screen.show(bot, "reelstats")
    if a == "sfx":
        on = not await sfx_on()
        await db.set_setting("reel_sfx", on)
        await cb.answer("Звуки в рилсах включены" if on else "Звуки в рилсах выключены")
        await screen.adopt(cb.message)
        return await screen.show(bot, "reels")
    if a == "voices":
        await cb.answer()
        await screen.adopt(cb.message)
        return await screen.show(bot, "reelvoice", lang=p[2] if len(p) > 2 and p[2] in tts.LANGS else "en")
    if a == "vs":
        li = int(p[2]) if p[2].isdigit() and int(p[2]) < len(tts.LANGS) else 0
        lang = tts.LANGS[li]
        keys = VOICE_KEYS[lang]
        k = keys[int(p[3])] if 0 <= int(p[3]) < len(keys) else None
        if not k:
            return await cb.answer()
        await db.set_setting("reel_voice_ru" if lang == "ru" else "reel_voice", k)
        name = tts.VOICES[k][0]
        await cb.answer(f"Голос {rk.LANGS[lang]}: {name}. Готовлю образец — до минуты в первый раз")
        await screen.adopt(cb.message)
        await screen.show(bot, "reelvoice", lang=lang, note=f"Выбран {name}")
        try:
            path = await tts.sample(k, lang)
            m = await bot.send_audio(config.ADMIN_ID, FSInputFile(path, filename=f"{name}.mp3"),
                                     title=f"AHMAG · {name}", performer="образец голоса",
                                     caption=f"🎙 {name} — {tts.VOICES[k][1]}")
            await screen.add_temp([m.message_id])
        except Exception as exc:
            log.warning("Образец голоса %s", k, exc_info=True)
            from app import curator
            await screen.notify(bot, f"🎙 Образец {name} не получился: {str(exc)[:200] or curator.explain(exc)}")
        return
    if a == "ask":
        await cb.answer()
        await state.set_state(ReelEdit.theme)
        m = await bot.send_message(config.ADMIN_ID, "Напиши тему или объект: «окна ночью», «Stańczyk, Матейко», "
                                                    "«Вилла Ротонда», «Пенсильванский вокзал». Потом выберешь формат. "
                                                    "/cancel — отмена.")
        await state.update_data(prompt=m.message_id)
        return await screen.add_temp([m.message_id])
    if a == "mk":
        # своя тема: формат выбран — спросить язык
        data = await state.get_data()
        req = data.get("reel_request")
        if not req or p[2] not in rk.KINDS:
            await ui.drop(bot, cb.message.message_id)
            return await cb.answer("Запрос потерялся — напиши заново", show_alert=True)
        await cb.answer()
        try:
            return await cb.message.edit_text(
                f"«{html.escape(req)}» · {rk.KINDS[p[2]]['icon']} {KIND_RU[p[2]]}. На каком языке?",
                reply_markup=screen._kb(lang_rows(f"rl:ml:{p[2]}:") + [[btn("← Формат", "rl:mkb")]]))
        except Exception:
            return
    if a == "mkb":
        data = await state.get_data()
        req = data.get("reel_request")
        await cb.answer()
        if not req:
            return await ui.drop(bot, cb.message.message_id)
        try:
            return await cb.message.edit_text(f"«{html.escape(req)}» — в каком формате?",
                                              reply_markup=screen._kb(kinds_rows("rl:mk:")))
        except Exception:
            return
    if a == "ml":
        data = await state.get_data()
        req = data.get("reel_request")
        await state.clear()
        await ui.drop(bot, cb.message.message_id)
        kind, lang = p[2], p[3] if len(p) > 3 else "en"
        if not req or kind not in rk.KINDS or lang not in rk.LANGS:
            return await cb.answer("Запрос потерялся — напиши заново", show_alert=True)
        rid = await new(bot, kind, req, lang=lang)
        r = await get(rid)
        await cb.answer(f"Собираю {rk.LANG_RU[lang]} на {human(r['day'])} — пара минут")
        return await screen.move_down(bot, "reels", note=f"⏳ {KIND_RU[kind]} · {rk.LANGS[lang]}: «{req[:50]}»")

    rid = int(p[2])
    r = await get(rid)
    if not r:
        return await cb.answer("Рилс не найден", show_alert=True)
    if a == "card":
        await cb.answer()
        if cb.message.text:                         # уведомление — оно больше не нужно
            await ui.drop(bot, cb.message.message_id)
        if r["status"] == "sent":
            return await send_package(bot, rid)
        return await send_card(bot, rid)
    if a == "retry":
        await cb.answer("Собираю заново — пара минут")
        if r["status"] != "failed":
            return
        d = _data(r)
        if "хороших картинок" in (r["note"] or "") or "не нашлось" in (r["note"] or ""):
            d = {k: v for k, v in d.items() if k in ("request", "lang")}     # тема не удалась — берём новую
        await _set(rid, data=d)
        start(bot, rid)
        if cb.message.photo:
            await screen.adopt(cb.message)
            return await screen.show(bot, "reels", note="⏳ Собираю заново")
        return await ui.drop(bot, cb.message.message_id)
    if a == "ok":
        if r["status"] != "ready":
            return await cb.answer("Уже решено")
        now = _now()
        day = r["day"]
        if _post_dt(day) <= now:                    # время уже прошло — выкладывать сейчас
            await _set(rid, status="approved", day=now.date().isoformat())
            await cb.answer("Время уже пришло — присылаю видео и подпись")
            return await send_package(bot, rid)
        await _set(rid, status="approved")
        await cb.answer(f"Беру. Пришлю видео и подпись {human(day)} в {TIME[0]:02d}:{TIME[1]:02d}")
        await _refresh_card(cb, rid)
        return screen.refresh_soon(bot)
    if a == "no":
        await _set(rid, status="rejected")
        await cb.answer("Не надо — убрал")
        await ui.drop(bot, cb.message.message_id)
        return screen.refresh_soon(bot)
    if a == "undo":
        await _set(rid, status="ready")
        await cb.answer("Снял — рилс снова ждёт решения")
        return await _refresh_card(cb, rid)
    if a in ("redo", "back", "kinds"):
        await cb.answer()
        return await _refresh_card(cb, rid, sub={"redo": "redo", "kinds": "kinds"}.get(a))
    if a == "now":
        await cb.answer("Присылаю")
        return await send_package(bot, rid)
    if a == "done":
        d = _data(r)
        d["posted_at"] = db.now()
        await _set(rid, status="posted", data=d)
        await cb.answer("Отмечено: рилс выложен")
        await ui.drop(bot, *json.loads(r["pkg_msgs"] or "[]"), cb.message.message_id)
        return screen.refresh_soon(bot)
    if a == "later":
        nd = (_now().date() + timedelta(days=1)).isoformat()
        await _set(rid, status="approved", day=nd)
        await cb.answer(f"Пришлю завтра в {TIME[0]:02d}:{TIME[1]:02d}")
        await ui.drop(bot, *json.loads(r["pkg_msgs"] or "[]"), cb.message.message_id)
        return screen.refresh_soon(bot)
    if a == "cap":
        await cb.answer()
        await state.set_state(ReelEdit.caption)
        cap = build_caption(r["kind"], _data(r))
        m = await bot.send_message(config.ADMIN_ID, "Сейчас подпись такая (нажми, чтобы скопировать), пришли новую "
                                                    f"целиком. /cancel — отмена.\n\n<pre>{html.escape(cap)[:3500]}</pre>")
        await state.update_data(prompt=m.message_id, reel=rid)
        return await screen.add_temp([m.message_id])

    # дальше — пересборка
    if r["status"] in ("building",):
        return await cb.answer("Уже собирается")
    d = _data(r)
    lang = rk.lang_of(d)
    if a == "theme":
        d = {"lang": lang}
        note = "Беру другую тему" if r["kind"] == "collection" else "Беру другую картину"
    elif a == "kind":
        kind = p[3] if len(p) > 3 and p[3] in rk.KINDS else ("details" if r["kind"] == "collection" else "collection")
        async with db.connect() as c:
            await c.execute("UPDATE reels SET kind=? WHERE id=?", (kind, rid))
            await c.commit()
        d = {"lang": lang}
        note = f"Делаю {KIND_ACC[kind]}"
    elif a == "lang":
        # тот же объект, рассказ заново на другом языке; подборку — заново целиком (строки работ привязаны к языку)
        lang = "en" if lang == "ru" else "ru"
        if r["kind"] == "collection":
            d = {k: v for k, v in d.items() if k == "request"}
        else:
            for k in ("story", "frames", "caption", "hashtags", "avoid_frames", "caption_override", "auto_pick"):
                d.pop(k, None)
        d["lang"] = lang
        await db.set_setting("reel_last_lang", lang)
        note = f"Переделываю {rk.LANG_RU[lang]}"
    elif a == "det":
        if r["kind"] == "collection":
            return await cb.answer()
        d["avoid_frames"] = [f"{b['text']} (box {b.get('box')})" for b in beats(d)]
        d.pop("frames", None)
        d.pop("story", None)
        note = "Пишу другой сюжет"
    elif a == "hook":
        st = d.get("story") or {}
        if len(st.get("hooks") or []) < 2:
            return await cb.answer("Других хуков нет")
        st["hook_i"] = (st.get("hook_i", 0) + 1) % len(st["hooks"])
        note = f"Беру хук {st['hook_i'] + 1} из {len(st['hooks'])}"
    elif a == "rep":
        n = int(p[3]) - 1
        its = d.get("items") or []
        if not 0 <= n < len(its):
            return await cb.answer("Такой работы нет")
        repl = await _replacement(d, background=False)
        if not repl:
            return await cb.answer("Замену не нашёл — попробуй «Другая тема»", show_alert=True)
        old = its[n]
        if old.get("path"):
            Path(old["path"]).unlink(missing_ok=True)
        its[n] = repl
        d["items"] = its
        note = f"Меняю работу {n + 1}: {repl['title']}"
    else:
        return await cb.answer()
    await _set(rid, data=d)
    await cb.answer(note + " — пара минут")
    await ui.drop(bot, cb.message.message_id)
    start(bot, rid)


async def _replacement(d: dict, background: bool) -> dict | None:
    """Запасная работа из уже проверенных, иначе Claude предложит новые, и одна пройдёт проверку."""
    from app import curator
    spare = d.get("spare") or []
    if spare:
        d["spare"] = spare[1:]
        return spare[0]
    have = "; ".join(f"{w['title']} — {w['author']}" for w in d.get("items") or [])
    more = await _ask(f"Theme: {d.get('title')}\nAlready in the reel: {have}\nGive 4 more works.",
                      system=rk.localize(MORE_SYSTEM, rk.lang_of(d), rk.RU_NAMES_WORKS), max_tokens=2500,
                      background=background)
    tall = None
    paths = [Path(w["path"]) for w in d.get("items") or [] if w.get("path") and Path(w["path"]).exists()]
    if paths and reelplan.collection_mode([reelplan._size(x) for x in paths]) == "bleed":
        tall = reelplan.BLEED + 0.03              # подборка на весь кадр — замена тоже вертикальная
    async with httpx.AsyncClient(headers=UA, follow_redirects=True) as client:
        good = await verify(client, (more.get("works") or [])[:4], background, max_aspect=tall)
    authors = {w["author"] for w in d.get("items") or []}
    good = [g for g in good if g.get("author") not in authors]
    if not good:
        return None
    d["spare"] = good[1:]
    return good[0]


@router.message(ReelEdit.theme, F.text, ~F.text.startswith("/"))
async def on_theme(msg: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await ui.drop(bot, data.get("prompt"), msg.message_id)
    req = msg.text.strip()[:300]
    await state.set_state(None)                     # данные остаются до выбора вида
    await state.update_data(reel_request=req)
    m = await bot.send_message(config.ADMIN_ID, f"«{html.escape(req)}» — в каком формате?",
                               reply_markup=screen._kb(kinds_rows("rl:mk:")))
    await screen.add_temp([m.message_id])


@router.message(ReelEdit.caption, F.text, ~F.text.startswith("/"))
async def on_caption(msg: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await ui.drop(bot, data.get("prompt"), msg.message_id)
    rid = data.get("reel")
    r = await get(rid) if rid else None
    if not r:
        return
    d = _data(r)
    d["caption_override"] = msg.text.strip()[:2200]
    await _set(rid, data=d)
    await screen.notify(bot, "✏️ Подпись рилса сохранена.", [("👁 Рилс", f"rl:card:{rid}")])
