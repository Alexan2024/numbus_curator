"""🎬 Рилсы — только для Instagram. Бот собирает видео, автор выкладывает его сам и кладёт музыку.

Два вида:
  подборка — тематическая, отдельная от ленты: Claude придумывает тему («The Art of Melancholy»,
             «Windows at night») и 6–8 работ разных авторов; каждая по несколько секунд с названием и автором.
  детали   — одна картина: общий план, затем камера по очереди подходит к деталям, на каждой — фраза;
             вместе фразы рассказывают историю картины. Факты Claude проверяет веб-поиском.

Картинки — из Wikimedia Commons в высоком разрешении. Какая из найденных — та самая работа, а не деталь,
копия или фото музейной стены, Claude проверяет глазами по превью.

Расписание: REELS_DAYS (пн, ср, пт) в REELS_TIME (18:30). Накануне в REELS_BUILD_TIME (13:00) бот собирает
рилс на завтра и присылает карточку: видео, работы, музыка. ✅ Беру / 🔁 Переделать / ❌ Не надо.
В день выхода в REELS_TIME приходит «Пора выкладывать»: видео файлом без сжатия, подпись одним блоком
(нажать — скопируется), треки. Выложил — «✅ Выложил». Рилс по запросу — экран «🎬 Рилсы» на пульте.

Рилсы в Telegram-канал не идут. Звука в видео нет."""
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

from app import config, db, reelrender, screen, slots, ui
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
TOPICS = [t.strip() for t in os.getenv("REEL_TOPICS", "art,architecture,photography,art,architecture,archive").split(",")
          if t.strip()]
MAX_ITEMS = int(os.getenv("REEL_MAX_ITEMS", "8"))
MIN_ITEMS = 5
REMIND_HOURS = 2
DOW_NUM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DOW_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
KIND_RU = {"collection": "подборка", "details": "детали картины"}
KIND_ACC = {"collection": "подборку", "details": "детали картины"}
UA = {"User-Agent": f"AHMAG-bot/{config.VERSION} (+https://t.me/ahmag; curation bot)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
DIR = config.DATA_DIR / "reels"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,         -- collection | details
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

def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(s or ""))).strip()


async def commons_candidates(client: httpx.AsyncClient, query: str, n: int = 3) -> list[dict]:
    """Крупные картинки по запросу: [{title, w, h, thumb, artist, license}] — превью 500 px для проверки."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
              "gsrnamespace": "6", "gsrlimit": "10", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
              "iiurlwidth": "500", "iiextmetadatafilter": "Artist|LicenseShortName"}
    try:
        r = await client.get(COMMONS, params=params, timeout=30)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
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
    r = await client.get(COMMONS, params={"action": "query", "format": "json", "titles": title, "prop": "imageinfo",
                                          "iiprop": "url|size|mime", "iiurlwidth": "3840"}, timeout=30)
    r.raise_for_status()
    page = next(iter(((r.json().get("query") or {}).get("pages") or {}).values()), {})
    i = (page.get("imageinfo") or [{}])[0]
    url = i.get("url") if i.get("mime") == "image/jpeg" and (i.get("width") or 0) <= 3840 else i.get("thumburl")
    if not url:
        raise ReelError(f"Commons не отдал файл {title}")
    r = await client.get(url, timeout=120)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)

    def save():
        with Image.open(io.BytesIO(r.content)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.save(dest, "JPEG", quality=94)
    await asyncio.to_thread(save)
    return dest


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

Also give the centre of the main subject in the picked image (a face, a figure, the building) as fractions of its width and height: fx, fy from 0 to 1 — the video crops the image to a tall frame around it.

Return ONLY JSON: {"picks": [{"i": 1, "pick": 2, "fx": 0.3, "fy": 0.5}]}"""


async def verify(client: httpx.AsyncClient, works: list[dict], background: bool, per: int = 3) -> list[dict]:
    """works: [{title, author, year, commons}] → те, для которых нашлась верная картинка, с полем file."""
    from app import curator
    sem = asyncio.Semaphore(4)

    async def cands(w):
        async with sem:
            return await commons_candidates(client, w.get("commons") or f"{w.get('title')} {w.get('author')}", per)
    found = await asyncio.gather(*(cands(w) for w in works))
    pairs = [(w, c) for w, c in zip(works, found) if c]
    if not pairs:
        return []
    thumbs = await asyncio.gather(*(_thumbs(client, c) for _, c in pairs))
    content: list = [{"type": "text", "text": "Sheets follow. Each: the expected work, then the candidates."}]
    for n, ((w, c), t) in enumerate(zip(pairs, thumbs), 1):
        content.append({"type": "text", "text": f"Sheet {n}: {w.get('title')} — {w.get('author')}, {w.get('year')}"})
        content.append(_img_block(_sheet(t), 1400, 80))
    data = await curator._call(content, system=PICK_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=400,
                               background=background)
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
            out.append({**w, "file": c[k - 1]["title"], "artist": c[k - 1]["artist"], "license": c[k - 1]["license"],
                        "focus": focus})
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

Every work must have a large image on Wikimedia Commons: paintings and prints by artists who died before about 1955, historical photographs, buildings with free-licensed photos. Pick specific works, one per author; mix famous and lesser-known. Give 10 works in viewing order (some may not be found, the reel uses up to 8); the first one opens the reel under the title, so it should be strong.

{EN_RULES}

{MUSIC}

Return ONLY JSON:
{{"title": "on-screen title, up to 28 characters", "theme_ru": "тема по-русски, коротко", "works": [{{"title": "title of the work in English or original", "author": "...", "year": "...", "commons": "query for Wikimedia Commons search: title and author, no year"}}], "caption": "1–3 short sentences for the Instagram caption: what ties these works together", "hashtags": ["5–8 lowercase words without #"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}"""

MORE_SYSTEM = f"""You add works to an AHMAG Instagram reel compilation. Same rules: a specific work by an author not yet in the reel, with a large image on Wikimedia Commons (artists who died before about 1955, historical photographs, buildings with free photos), fitting the theme.

{TASTE}

Return ONLY JSON: {{"works": [{{"title": "...", "author": "...", "year": "...", "commons": "query for Wikimedia Commons"}}]}}"""

PAINTING_SYSTEM = f"""You pick a painting for an AHMAG Instagram reel that walks through its details: the camera starts on the whole picture, then moves to 4–6 details one by one, with one short line on each, and the lines tell the painting's story. Example: Matejko's "Stańczyk" — a jester sits alone while the ball goes on next door; the letter on the table; the comet in the window; Poland has lost Smolensk.

Pick a painting (or a fresco, altarpiece, large print) that is in the public domain and has a large image on Wikimedia Commons; has several visible details that carry the story; has a documented story. Check the facts with web search. Prefer works that are not the most overexposed. Not from the avoid list.

{TASTE}

{MUSIC}

Return ONLY JSON:
{{"title": "common English title", "author": "...", "year": "...", "museum": "museum, city", "commons": "query for Wikimedia Commons search: title and author", "facts": ["6–10 specific verified facts in English, about what is shown, details, context, what happened"], "music": [{{"artist": "...", "track": "...", "mood": "..."}}]}}"""

FRAMES_SYSTEM = f"""You write the on-screen text for an AHMAG Instagram reel that walks through the details of the painting in the image. Coordinates are fractions of the image width and height from its top-left corner (0 to 1).

Write:
- intro: the line shown over the whole painting at the start, up to 70 characters. It opens the story, e.g. "At first, this looks like a tired clown taking a break."
- frames: 4–6 details in viewing order. box [x0, y0, x1, y1] — tight around a detail that is clearly visible in this image (a face, a hand, an object, a figure, a window). text — one line, up to 80 characters: what we see there and why it matters. Together the lines tell one story and end on a fact, not a moral.
- caption: the Instagram caption. First line: "Title (year), Author". Then 2–4 short paragraphs telling the story plainly. Last line: museum and city.
- hashtags: 5–8 lowercase words without #.

Use only the facts given; add nothing you are not sure of.

{EN_RULES}

Return ONLY JSON: {{"intro": "...", "frames": [{{"box": [0.1, 0.2, 0.3, 0.5], "text": "..."}}], "caption": "...", "hashtags": ["..."]}}"""


async def _avoid(kind: str) -> list[str]:
    rows = [r for r in await items(limit=200) if r["kind"] == kind and r["title"]]
    return [r["title"] for r in rows][:60]


async def _next_topic() -> str:
    i = int(await db.get_setting("reel_topic_i", 0) or 0)
    await db.set_setting("reel_topic_i", i + 1)
    return TOPICS[i % len(TOPICS)] if TOPICS else "art"


def _credits(items: list[dict]) -> str:
    """Авторы фото из Commons, если лицензия требует указать автора (не общественное достояние)."""
    need = []
    for it in items:
        lic = (it.get("license") or "").lower()
        if lic and "public domain" not in lic and "pd" not in lic.split() and "cc0" not in lic and it.get("artist"):
            need.append(f"{it['artist']} ({it['license']})")
    base = "Images: Wikimedia Commons"
    return base + (". Photos: " + "; ".join(dict.fromkeys(need)) if need else "")


def _tags(tags: list, extra: str) -> str:
    clean = [re.sub(r"[^a-z0-9_]", "", str(t).lower()) for t in tags or []]
    out = ["ahmag", extra] + [t for t in clean if t and t not in ("ahmag", extra)]
    return " ".join(f"#{t}" for t in list(dict.fromkeys(out))[:10])


def build_caption(r_kind: str, d: dict) -> str:
    if d.get("caption_override"):
        return d["caption_override"]
    if r_kind == "collection":
        lines = [d.get("title", ""), "", d.get("caption", "").strip(), ""]
        for n, it in enumerate(d.get("items") or [], 1):
            lines.append(f"{n}. {it.get('title')} — {it.get('author')}" + (f", {it['year']}" if it.get("year") else ""))
        lines += ["", _credits(d.get("items") or []), "", _tags(d.get("hashtags"), "ahmagreels")]
    else:
        lines = [d.get("caption", "").strip(), "", _credits([d.get("painting") or {}]), "",
                 _tags(d.get("hashtags"), "ahmagreels")]
    return "\n".join(lines).strip()[:2150]


# ======================= сборка =======================

_gen_lock = asyncio.Lock()


async def _collection(rid: int, d: dict, background: bool, client: httpx.AsyncClient) -> dict:
    from app import curator
    folder = DIR / str(rid)
    if not d.get("items"):
        topic = d.get("topic") or await _next_topic()
        req = d.get("request")
        prompt = ((f"Theme requested by the author: {req}\n" if req else
                   f"Area for this reel: {topic} — {TOPIC_HINT.get(topic, topic)}.\n")
                  + "Avoid these earlier reel titles:\n" + ("\n".join(await _avoid("collection")) or "—"))
        plan = await curator._call(prompt, system=COLLECTION_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=2500,
                                   background=background)
        works = [w for w in plan.get("works") or [] if w.get("title")]
        if not works:
            raise ReelError("Claude не предложил работ")
        good = await verify(client, works[:12], background)
        if len(good) < MIN_ITEMS:
            more = await curator._call(
                f"Theme: {plan.get('title')}\nAlready in the reel: " + "; ".join(f"{w['title']} — {w['author']}" for w in good)
                + "\nNot found on Commons: " + "; ".join(w["title"] for w in works
                                                           if w["title"] not in {g["title"] for g in good})
                + f"\nGive {MAX_ITEMS} more works.", system=MORE_SYSTEM, model=config.CLAUDE_MODEL,
                max_tokens=1200, background=background)
            good += await verify(client, (more.get("works") or [])[:MAX_ITEMS], background)
        if len(good) < MIN_ITEMS:
            raise ReelError(f"хороших картинок нашлось только {len(good)} из {MIN_ITEMS} нужных — попробуй другую тему")
        d.update(title=str(plan.get("title") or "")[:40], theme_ru=plan.get("theme_ru") or "",
                 caption=plan.get("caption") or "", hashtags=plan.get("hashtags") or [],
                 music=(plan.get("music") or [])[:3], topic=topic, items=good[:MAX_ITEMS], spare=good[MAX_ITEMS:])
    for n, it in enumerate(d["items"]):
        path = folder / f"{n:02d}_{hashlib.md5(it['file'].encode()).hexdigest()[:8]}.jpg"
        if not path.exists():
            await commons_download(client, it["file"], path)
        it["path"] = str(path)
    render_items = [{"path": it["path"], "label": it["title"], "sub": f"By {it['author']}",
                     "focus": it.get("focus") or [0.5, 0.5]} for it in d["items"]]
    video = folder / f"reel_{int(datetime.now().timestamp())}.mp4"
    d["duration"] = await asyncio.to_thread(reelrender.collection, d["title"], render_items, video)
    d["video"] = str(video)
    return d


async def _details(rid: int, d: dict, background: bool, client: httpx.AsyncClient) -> dict:
    from app import curator
    folder = DIR / str(rid)
    if not d.get("painting"):
        req = d.get("request")
        prompt = ((f"The author asks for: {req}\n" if req else "")
                  + "Avoid these paintings (already done):\n" + ("\n".join(await _avoid("details")) or "—"))
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
        p = await curator._call(prompt, system=PAINTING_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=2500,
                                tools=tools, background=background)
        if not p.get("title"):
            raise ReelError("Claude не выбрал картину")
        good = await verify(client, [p], background, per=4)
        if not good:
            raise ReelError(f"на Commons не нашлось хорошей картинки «{p['title']}» — попробуй другую картину")
        d.update(painting=good[0], title=f"{p['title']}", music=(p.get("music") or [])[:3],
                 facts=p.get("facts") or [])
        d.pop("frames", None)
    pt = d["painting"]
    path = Path(pt.get("path") or folder / "painting.jpg")
    if not path.exists():
        await commons_download(client, pt["file"], path)
    pt["path"] = str(path)
    if not d.get("frames"):
        with Image.open(path) as im:
            block = _img_block(im.convert("RGB"))
        avoid = d.get("avoid_frames") or []
        text = (f"Painting: {pt['title']} — {pt['author']}, {pt.get('year')}. {pt.get('museum') or ''}\n\nFacts:\n"
                + "\n".join(f"- {f}" for f in d.get("facts") or [])
                + ("\n\nThe previous version used these details, choose others where possible:\n"
                   + "\n".join(avoid) if avoid else ""))
        fr = await curator._call([block, {"type": "text", "text": text}], system=FRAMES_SYSTEM,
                                 model=config.CLAUDE_MODEL, max_tokens=2000, background=background)
        frames = [f for f in fr.get("frames") or [] if isinstance(f.get("box"), list) and len(f["box"]) == 4
                  and f.get("text")][:6]
        if len(frames) < 3:
            raise ReelError("Claude не нашёл деталей на картине")
        d.update(intro=fr.get("intro") or "", frames=frames, caption=fr.get("caption") or "",
                 hashtags=fr.get("hashtags") or [])
    end = [f"{pt['title']}" + (f", {pt['year']}" if pt.get("year") else ""), pt.get("author") or "",
           pt.get("museum") or ""]
    video = folder / f"reel_{int(datetime.now().timestamp())}.mp4"
    d["duration"] = await asyncio.to_thread(reelrender.details, path, d.get("intro") or "", d["frames"], end, video)
    d["video"] = str(video)
    return d


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
            async with httpx.AsyncClient(headers=UA, follow_redirects=True) as client:
                d = await (_collection if r["kind"] == "collection" else _details)(rid, d, background, client)
        except Exception as exc:
            log.exception("Рилс %s", rid)
            why = str(exc) if isinstance(exc, ReelError) else curator.explain(exc)
            await _set(rid, status="failed", note=why[:300], data=d)
            await screen.notify(bot, f"🎬 Рилс ({KIND_RU[r['kind']]}) не собрался: {why}"[:900],
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
              background: bool = False) -> int:
    rid = await _add(kind, day or await free_day(), {"request": request} if request else {})
    start(bot, rid, background)
    return rid


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
    lines = [f"🎬 <b>Рилс · {KIND_RU[r['kind']]}</b> · {when} · {st}"]
    if note:
        lines.append(f"<b>{html.escape(note)}</b>")
    if r["kind"] == "collection":
        lines.append(f"\n<b>{html.escape(d.get('title') or '')}</b>" + (f" — {html.escape(d['theme_ru'])}"
                                                                        if d.get("theme_ru") else ""))
        for n, it in enumerate(d.get("items") or [], 1):
            lines.append(f"{n}. {html.escape(it['title'])} — {html.escape(it['author'])}"
                         + (f", {html.escape(str(it['year']))}" if it.get("year") else ""))
    else:
        pt = d.get("painting") or {}
        lines.append(f"\n<b>{html.escape(pt.get('title') or '')}</b> — {html.escape(pt.get('author') or '')}"
                     + (f", {html.escape(str(pt['year']))}" if pt.get("year") else ""))
        lines.append(f"<i>{html.escape(d.get('intro') or '')}</i>")
        for n, fr in enumerate(d.get("frames") or [], 1):
            lines.append(f"{n}. {html.escape(fr['text'])}")
    mus = _music_lines(d)
    if mus:
        lines += ["", "🎵 Музыка:"] + mus
    lines.append(f"\n⏱ {d.get('duration', 0):.0f} с · подпись на английском придёт в день выхода")
    text = "\n".join(lines)
    return text if len(text) <= 1024 else text[:1020] + "…"


def _card_kb(r, sub: str | None = None) -> InlineKeyboardMarkup:
    rid, d = r["id"], _data(r)
    if sub == "redo":
        rows = []
        if r["kind"] == "collection":
            rows.append([btn("🔀 Другая тема", f"rl:theme:{rid}"), btn("🔄 Сделать «детали»", f"rl:kind:{rid}")])
            nums = [btn(f"🖼 {n}", f"rl:rep:{rid}:{n}") for n in range(1, len(d.get("items") or []) + 1)]
            if nums:
                rows.append([btn("Заменить работу:", "rl:noop")])
                rows += [nums[i:i + 4] for i in range(0, len(nums), 4)]
        else:
            rows.append([btn("🔀 Другая картина", f"rl:theme:{rid}"), btn("🎯 Другие детали", f"rl:det:{rid}")])
            rows.append([btn("🔄 Сделать подборку", f"rl:kind:{rid}")])
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
    cap = build_caption(r["kind"], d)
    mus = _music_lines(d)
    text = ("<b>Подпись</b> — нажми на блок, чтобы скопировать:\n"
            f"<pre>{html.escape(cap)}</pre>"
            + ("\n\n🎵 " + "\n".join(mus) if mus else "")
            + "\n\nInstagram → Reels → это видео → музыка → подпись. Обложку выбери сам.")
    if len(text) > 4000:
        text = text[:3990] + "…</pre>"
    msg = await bot.send_message(config.ADMIN_ID, text, disable_web_page_preview=True,
                                 reply_markup=screen._kb([[btn("✅ Выложил", f"rl:done:{rid}"),
                                                           btn("⏭ Завтра", f"rl:later:{rid}")]]))
    d["sent_at"] = db.now()
    d.pop("reminded", None)
    await _set(rid, status="sent", pkg_msgs=json.dumps([doc.message_id, msg.message_id]), data=d)
    await ui.drop(bot, r["card_msg"])
    screen.refresh_soon(bot)


# ======================= расписание =======================

async def plan_next(bot: Bot) -> None:
    """Накануне дня рилса — собрать его. Если сегодня день рилса, а его нет и время не прошло, — и на сегодня."""
    if not ENABLED:
        return
    taken = await _taken_days()
    today = _now().date()
    targets = [today + timedelta(days=1)]
    if _now() < _post_dt(today.isoformat()) - timedelta(hours=1):
        targets.insert(0, today)
    for d in targets:
        if is_reel_day(d) and d.isoformat() not in taken:
            last = await db.get_setting("reel_last_kind", "details")
            kind = "collection" if last == "details" else "details"
            await db.set_setting("reel_last_kind", kind)
            rid = await _add(kind, d.isoformat(), {})
            await generate(bot, rid, background=True)


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
    sched.add_job(guarded(bot, "рилс: сборка", plan_next, bot), "cron", hour=BUILD_TIME[0], minute=BUILD_TIME[1],
                  id="reels_build", max_instances=1)
    sched.add_job(guarded(bot, "рилс: выкладывать", due, bot), "cron", hour=TIME[0], minute=TIME[1], id="reels_due")
    sched.add_job(guarded(bot, "рилс: напоминание", remind, bot), "interval", minutes=30, id="reels_remind")
    sched.add_job(guarded(bot, "рилс: очистка", cleanup), "cron", hour=4, minute=40, id="reels_cleanup")


def install(bot: Bot) -> None:
    screen.VIEWS["reels"] = _v_reels


async def home_label() -> str:
    n = len(await items("ready"))
    return "🎬 Рилсы" + (f" · {n} ждёт" if n else "")


# ======================= экран =======================

async def _v_reels(arg: dict):
    days = ", ".join(DOW_RU[DOW_NUM[x]] for x in DOW if x in DOW_NUM)
    lines = [f"<b>🎬 Рилсы</b> — только Instagram · {days} в {TIME[0]:02d}:{TIME[1]:02d}",
             f"<i>Накануне в {BUILD_TIME[0]:02d}:{BUILD_TIME[1]:02d} бот собирает рилс и присылает карточку. "
             "Музыку кладёшь ты при публикации.</i>"]
    if not ENABLED:
        lines.append("⚠️ Выключены переменной REELS=0")
    if not await asyncio.to_thread(reelrender.ffmpeg_ok):
        lines.append("⚠️ Нет ffmpeg — видео не соберётся")
    if arg.get("note"):
        lines.append(f"<b>{html.escape(arg['note'])}</b>")
    rows_r = await items(limit=12)
    lines.append("")
    rows = []
    for r in rows_r[:10]:
        lines.append(f"{ICON.get(r['status'], '•')} {human(r['day'])} · {KIND_RU[r['kind']]} · "
                     f"{html.escape((r['title'] or '…')[:40])}"
                     + (f" — <i>{html.escape((r['note'] or '')[:60])}</i>" if r["status"] == "failed" else ""))
        if r["status"] in ("ready", "approved", "sent") and r["video"]:
            rows.append([btn(f"{ICON[r['status']]} {human(r['day'])} · {(r['title'] or '')[:22]}", f"rl:card:{r['id']}")])
        elif r["status"] == "failed":
            rows.append([btn(f"🔁 Ещё раз · {human(r['day'])}", f"rl:retry:{r['id']}")])
    if not rows_r:
        lines.append("Пока рилсов не было.")
    lines.append("\n<i>📥 ждёт решения · 🟡 одобрен · 📤 ждёт публикации · ✅ выложен · ⏳ собирается</i>")
    rows.append([btn("➕ Подборка", "rl:new:collection"), btn("➕ Детали картины", "rl:new:details")])
    rows.append([btn("✍️ Своя тема или картина", "rl:ask"), btn("← Пульт", "h:home")])
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
        kind = p[2]
        rid = await new(bot, kind)
        r = await get(rid)
        await cb.answer(f"Собираю {KIND_ACC[kind]} на {human(r['day'])} — пара минут")
        await screen.adopt(cb.message)
        return await screen.show(bot, "reels", note=f"⏳ Собираю {KIND_ACC[kind]}, карточка придёт сообщением")
    if a == "ask":
        await cb.answer()
        await state.set_state(ReelEdit.theme)
        m = await bot.send_message(config.ADMIN_ID, "Напиши тему подборки («окна ночью», «бетон, похожий на ткань») "
                                                    "или картину («Stańczyk, Матейко»). /cancel — отмена.")
        await state.update_data(prompt=m.message_id)
        return await screen.add_temp([m.message_id])
    if a == "mk":
        data = await state.get_data()
        req = data.get("reel_request")
        await state.clear()
        await ui.drop(bot, cb.message.message_id)
        if not req:
            return await cb.answer("Запрос потерялся — напиши заново", show_alert=True)
        rid = await new(bot, p[2], req)
        r = await get(rid)
        await cb.answer(f"Собираю на {human(r['day'])} — пара минут")
        return await screen.move_down(bot, "reels", note=f"⏳ {KIND_RU[p[2]]}: «{req[:50]}»")

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
            d = {k: v for k, v in d.items() if k == "request"}     # тема не удалась — берём новую
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
    if a in ("redo", "back"):
        await cb.answer()
        return await _refresh_card(cb, rid, sub="redo" if a == "redo" else None)
    if a == "now":
        await cb.answer("Присылаю")
        return await send_package(bot, rid)
    if a == "done":
        await _set(rid, status="posted")
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
    if a == "theme":
        d = {}
        note = "Беру другую тему" if r["kind"] == "collection" else "Беру другую картину"
    elif a == "kind":
        kind = "details" if r["kind"] == "collection" else "collection"
        async with db.connect() as c:
            await c.execute("UPDATE reels SET kind=? WHERE id=?", (kind, rid))
            await c.commit()
        d = {}
        note = f"Делаю {KIND_ACC[kind]}"
    elif a == "det":
        d["avoid_frames"] = [f"{fr['text']} (box {fr['box']})" for fr in d.get("frames") or []]
        d.pop("frames", None)
        note = "Выбираю другие детали"
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
    more = await curator._call(f"Theme: {d.get('title')}\nAlready in the reel: {have}\nGive 4 more works.",
                               system=MORE_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=800, background=background)
    async with httpx.AsyncClient(headers=UA, follow_redirects=True) as client:
        good = await verify(client, (more.get("works") or [])[:4], background)
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
    m = await bot.send_message(config.ADMIN_ID, f"«{html.escape(req)}» — что собрать?",
                               reply_markup=screen._kb([[btn("🖼 Подборку", "rl:mk:collection"),
                                                         btn("🔍 Детали картины", "rl:mk:details")]]))
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
