"""Сайт theahmag.com: всё, что выходит в канале, появляется и на сайте.

Как это устроено
• Большой пост. Перед отправкой в канал бот собирает запись для сайта (тексты на двух языках, фото со знаком
  AHMAG) и выкладывает её временную страницу /a/<номер поста в боте>/. В подпись уходит тихая строчка
  «в архиве →» с этой ссылкой. Когда канал дал посту номер, запись получает постоянный адрес /o/<номер>/,
  временный адрес начинает вести на него, сайт пересобирается.
• Мини-пост. В канале без ссылки; на сайт попадает сразу после выхода.
• #ahmagnotes. Полный текст сначала выходит на сайте (/n/<номер>/), в канал уходит короткий анонс со ссылкой
  и большим превью страницы (через Instant View, если задан IV_RHASH). Фон для сторис приходит со ссылкой
  для стикера.
• Подборки на сайт не идут.
Если сайт недоступен или не настроен, посты выходят как раньше: без ссылки, заметка целиком в канале.

Сайт собирается целиком (site/sitebuild.py и site/prerender.js, около секунды) и сверяется со списком файлов,
которые уже лежат на хостинге (assets/manifest.json): по FTP уходят только изменённые файлы.

Переменные Railway
  SITE_FTP_HOST, SITE_FTP_USER, SITE_FTP_PASSWORD — FTP на Beget (пароль хранится только в Railway);
  SITE_FTP_DIR — папка сайта на FTP, по умолчанию theahmag.com/public_html (для отдельного FTP-аккаунта,
  который смотрит прямо в папку сайта, — пустая строка или «/»);
  SITE_URL — адрес сайта, по умолчанию https://theahmag.com;  SITE=0 — выключить публикацию на сайт;
  IV_RHASH — номер шаблона Instant View: ссылки на заметки пойдут через t.me/iv.
"""
import asyncio
import ftplib
import html
import io
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from PIL import Image, ImageOps

from app import brand, cards, config, curator, db, formatter

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))
    import sitebuild  # noqa: E402
except Exception:          # папки site/ нет в репозитории — бот работает как раньше, сайт выключен
    sitebuild = None

log = logging.getLogger(__name__)
router = Router(name="site")
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)

SITE_URL = os.getenv("SITE_URL", "https://theahmag.com").strip().rstrip("/")
FTP_HOST = os.getenv("SITE_FTP_HOST", "").strip()
FTP_PORT = int(os.getenv("SITE_FTP_PORT", "21") or 21)
FTP_USER = os.getenv("SITE_FTP_USER", "").strip()
FTP_PASS = os.getenv("SITE_FTP_PASSWORD", "")
FTP_DIR = os.getenv("SITE_FTP_DIR", "theahmag.com/public_html").strip().rstrip("/")
FTP_TLS = os.getenv("SITE_FTP_TLS", "auto").strip().lower()     # auto | 1 | 0
IV_RHASH = os.getenv("IV_RHASH", "").strip()
LINK_STD = os.getenv("SITE_LINK_TEXT", "в архиве →")
LINK_NOTE = os.getenv("SITE_NOTE_LINK_TEXT", "читать →")
PREPARE_TIMEOUT = int(os.getenv("SITE_TIMEOUT", "120"))        # сек.: дольше пост не ждёт сайт

ROOT = config.DATA_DIR / "site"
DATA = ROOT / "data.json"
MANIFEST = ROOT / "manifest.json"
PENDING = ROOT / "pending"            # записи больших постов, которые ждут номера из канала
TEMP_BASE = 9_000_000                 # временный номер записи до выхода поста: TEMP_BASE + номер поста в боте
CATS = ("architecture", "art", "photography", "cinema", "archive")
ROLES = ("architect", "studio", "artist", "photographer", "director", "designer", "writer")

_lock = asyncio.Lock()


class SiteError(Exception):
    pass


# ======================= включено ли =======================

def configured() -> bool:
    return sitebuild is not None and bool(FTP_HOST and FTP_USER and FTP_PASS)


async def enabled() -> bool:
    if os.getenv("SITE", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return configured() and bool(await db.get_setting("site_enabled", True))


def wants(post) -> str | None:
    """Что этот пост на сайте: 'object' (большой и мини), 'note' (#ahmagnotes) или None (подборки и прочее)."""
    if not post:
        return None
    src = post["source"] or ""
    if src == "digest":
        return None
    if post["format"] in ("std", "mini"):
        return "object"
    if post["format"] == "notes" and src == "notes":
        return "note"
    return None


_reach = {"at": 0.0, "ok": False}


async def reachable() -> bool:
    """Открывается ли сайт у читателей по адресу из ссылок (с https). Ссылки на нерабочий адрес в канал не идут.
    Ответ помним 10 минут."""
    if time.time() - _reach["at"] < 600:
        return _reach["ok"]
    ok = False
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": config.USER_AGENT}) as c:
            ok = (await c.get(SITE_URL + "/robots.txt")).status_code == 200
    except Exception as exc:
        log.warning("Сайт не открывается по %s: %r", SITE_URL, exc)
    _reach.update(at=time.time(), ok=ok)
    return ok


async def _status(ok: bool, text: str) -> None:
    try:
        await db.set_setting("site_status", {"ok": ok, "text": text[:300], "at": db.now()})
    except Exception:
        log.warning("Сайт: статус не записался", exc_info=True)


async def _live(path: str) -> bool:
    """Открывается ли только что выложенная страница по адресу, который увидят читатели."""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": config.USER_AGENT}) as c:
                if (await c.get(SITE_URL + path)).status_code == 200:
                    return True
        except Exception as exc:
            log.info("Сайт: %s пока не открывается: %r", path, exc)
        await asyncio.sleep(2 * (attempt + 1))
    return False


# ======================= данные сайта =======================

def _today() -> str:
    return datetime.now(ZoneInfo(config.TZ_NAME)).date().isoformat()


async def _fetch(path: str) -> httpx.Response:
    """Файл с сайта. Пока https не настроен, пробует и http."""
    urls = [SITE_URL + path]
    if SITE_URL.startswith("https://"):
        urls.append("http://" + SITE_URL[8:] + path)
    last = None
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": config.USER_AGENT}) as c:
        for u in urls:
            try:
                r = await c.get(u)
                if r.status_code == 200:
                    return r
                last = SiteError(f"{u}: {r.status_code}")
            except httpx.HTTPError as exc:
                last = exc
    raise SiteError(f"сайт не отдал {path}: {last}")


async def load() -> dict:
    """Данные сайта. В первый раз — с самого сайта, дальше бот ведёт их сам (на диске Railway)."""
    if DATA.exists():
        return json.loads(DATA.read_text("utf-8"))
    D = (await _fetch("/data.json")).json()
    if int(D.get("v") or 1) < 2:
        raise SiteError("на сайте ещё старая версия — сначала залей новую сборку сайта (архив из чата)")
    try:
        manifest = (await _fetch("/" + sitebuild.MANIFEST)).json()
    except Exception:
        manifest = {}
    ROOT.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(D, ensure_ascii=False), "utf-8")
    MANIFEST.write_text(json.dumps(manifest), "utf-8")
    log.info("Сайт: данные взяты с сайта, записей %s", len(D.get("objects", [])))
    return D


def _save(D: dict, manifest: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(D, ensure_ascii=False), "utf-8")
    tmp.replace(DATA)
    MANIFEST.write_text(json.dumps(manifest), "utf-8")


def recount(D: dict) -> dict:
    """Счётчики стран и десятилетий, списки записей у имён — заново по самим записям."""
    objs = D["objects"]
    for p in D["people"].values():
        p["objs"] = []
    for o in objs:
        for x in o["p"]:
            if x["id"] in D["people"]:
                D["people"][x["id"]]["objs"].append(o["id"])
    D["people"] = {k: v for k, v in D["people"].items() if v["objs"]}
    for c in D["countries"].values():
        c["n"] = 0
    for o in objs:
        for c in o["co"]:
            if c in D["countries"]:
                D["countries"][c]["n"] += 1
    D["countries"] = {k: v for k, v in D["countries"].items() if v["n"]}
    per: dict[str, int] = {}
    for o in objs:
        if o.get("per"):
            per[o["per"]] = per.get(o["per"], 0) + 1
    D["periods"] = per
    return D


def period_of(year: int | None) -> str | None:
    if year is None:
        return None
    if year < 1800:
        return "pre1800"
    if year < 1900:
        return "1800s"
    return f"{min(year, 2029) // 10 * 10}s"


def slug(s: str) -> str:
    s = (s or "").lower()
    table = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                          "abvgdeezziiklmnoprstufhccss_y_eua")
    s = s.translate(table)
    s = re.sub(r"[^a-z0-9]+", "-", s.encode("ascii", "ignore").decode())
    return s.strip("-")[:60] or "x"


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def paragraphs(text: str) -> list[str]:
    return [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", plain(text)) if p.strip()]


def first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?…])(\s+[«\"A-ZА-ЯЁ0-9]|\s*$)", text or "", re.S)
    s = (m.group(1) if m else text or "").strip()
    return s if len(s) <= 260 else s[:259].rsplit(" ", 1)[0] + "…"


# ======================= запись для сайта =======================

SITE_SYSTEM = """You prepare entries for theahmag.com, the website archive of AHMAG, a Russian-language Telegram channel about architecture, interiors, art, photography, cinema and archival finds. Every entry exists in Russian and English. You get one published post and return its structured fields.

Rules
- Keep every fact, name, date and number exactly; add nothing that is not in the post.
- Russian fields: take the wording from the post as is.
- English fields: plain, concrete, conversational English, as if the same person wrote it. Names of buildings, works and people keep their original Latin spelling if they have one; Russian names are transliterated the standard way; places are translated (Токио, Япония → Tokyo, Japan). Never use: stunning, breathtaking, masterpiece, testament to, nestled, boasts, seamlessly, harmonious, "a dialogue between", "not X but Y" constructions, aphoristic closing lines, exclamation marks.
- The headline is usually "Title // Author // Place, Year". People are the authors only (architect, studio, artist, photographer, director, designer, writer), not people merely mentioned in the text.
- People: if a person or studio is already in the index list, reuse its id exactly. Otherwise make a new id: lowercase Latin slug of the English name with hyphens (e.g. "tadao-ando").
- Countries: reuse ids from the country list; a new country gets a lowercase English slug and its Russian and English names.
- Year: "ru"/"en" as written (e.g. "1932", "1913–1915", "ок. 1820" / "c. 1820", "1950-е" / "1950s"); "sort" is the first year as an integer (a century → its first year, e.g. XV век → 1400). No year in the post → null.
- Place: the place without the year ("Гранд-Каньон, Аризона, США"). No place → null.

Return ONLY JSON:
{"title": {"ru": "...", "en": "..."},
 "people": [{"id": "...", "ru": "...", "en": "...", "role": "architect"}],
 "place": {"ru": "...", "en": "..."},
 "year": {"ru": "...", "en": "...", "sort": 1932},
 "countries": [{"id": "japan", "ru": "Япония", "en": "Japan"}],
 "text_en": ["English paragraph", "..."],
 "credits_en": {"pr": "...", "ph": "...", "via": "..."}}"""

NOTE_SYSTEM = """You prepare an essay from #ahmagnotes (the long-form column of AHMAG, a Russian-language channel about architecture, art, photography and cinema) for the bilingual website theahmag.com. Translate it into English: plain, concrete, conversational, as if the same author wrote it; keep every fact, name, date and number; add nothing. Names keep their original Latin spelling; Russian names are transliterated the standard way. Never use: stunning, breathtaking, masterpiece, testament to, seamlessly, harmonious, "a dialogue between", "not X but Y", aphoristic closing lines, exclamation marks.
Also list the people the essay is about (architects, artists, photographers, directors, writers): reuse ids from the index list when the person is there, otherwise a lowercase Latin slug of the English name. And pick one section: architecture, art, photography, cinema or archive.

Return ONLY JSON:
{"title_en": "...", "subtitle_en": "... or null", "paragraphs_en": ["...", "..."],
 "people": [{"id": "...", "ru": "...", "en": "..."}], "section": "architecture"}"""


def _people_list(D: dict) -> str:
    return "\n".join(f"{k} | {p['ru']} | {p['en']}" for k, p in sorted(D["people"].items()))


def _countries_list(D: dict) -> str:
    return "\n".join(f"{k} | {c['ru']} | {c['en']}" for k, c in sorted(D["countries"].items()))


def _bi(v, fallback: str = "") -> dict | None:
    if not isinstance(v, dict):
        return None
    ru = str(v.get("ru") or fallback or "").strip()
    en = str(v.get("en") or ru).strip()
    return {"ru": ru, "en": en} if ru else None


def _photos(post) -> list:
    """Фото, которые ушли в канал (в том же порядке), — со знаком AHMAG, как в канале."""
    images = json.loads(post["images"] or "[]")
    out = []
    for i in cards.photo_plan(post):
        if i < len(images) and Path(images[i]).exists():
            im = ImageOps.exif_transpose(Image.open(images[i])).convert("RGB")
            out.append(brand.stamp(im) if brand.enabled() else im)
    if not out:
        raise SiteError("фото поста уже нет на диске")
    return out


async def object_record(post, D: dict, key) -> tuple[dict, dict[str, bytes]]:
    """Пост → запись архива сайта и её картинки (имена файлов — по key)."""
    data = json.loads(post["data"])
    fmt = post["format"]
    parts = formatter.headline_parts(data)
    text_ru = paragraphs(data.get("body", "")) if fmt == "std" else (
        [plain(data.get("mini_line"))] if plain(data.get("mini_line")) else [])
    credits = {k: v for k, v in (data.get("credits") or {}).items() if v and str(v).strip().lower() != "null"}
    photos = await asyncio.to_thread(_photos, post)          # раньше Claude: без фото запись не нужна
    content = (
        f"# Headline parts\n{json.dumps(parts, ensure_ascii=False)}\n\n"
        f"# Section\n{config.CATEGORY_ALIASES.get(post['category'] or '', post['category'] or '')}\n\n"
        f"# Text ({'full post' if fmt == 'std' else 'short post: one line'})\n" + ("\n\n".join(text_ru) or "(none)") + "\n\n"
        f"# Credits\n{json.dumps(credits, ensure_ascii=False)}\n\n"
        f"# Hashtags\n{' '.join(formatter.normalize_tags(data.get('tags', []), fmt))}\n\n"
        f"# Index of people already on the site (id | Russian | English)\n{_people_list(D)}\n\n"
        f"# Countries already on the site (id | Russian | English)\n{_countries_list(D)}")
    out = await curator._call(content, system=SITE_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=3000)
    img, files = await asyncio.to_thread(sitebuild.make_images, photos, key)

    title = _bi(out.get("title"), parts[0] if parts else "") or {"ru": parts[0] if parts else "AHMAG", "en": parts[0] if parts else "AHMAG"}
    people, new_people = [], {}
    for p in out.get("people") or []:
        pid = slug(str(p.get("id") or p.get("en") or p.get("ru") or ""))
        if pid in (x["id"] for x in people) or pid == "x":
            continue
        people.append({"id": pid})
        if pid not in D["people"] and (p.get("ru") or p.get("en")):
            role = p.get("role") if p.get("role") in ROLES else "architect"
            new_people[pid] = {"ru": str(p.get("ru") or p.get("en")), "en": str(p.get("en") or p.get("ru")),
                               "roles": [role], "objs": []}
    countries, new_countries = [], {}
    for c in out.get("countries") or []:
        cid = slug(str((c.get("id") if isinstance(c, dict) else c) or ""))
        if cid == "x" or cid in countries:
            continue
        if cid not in D["countries"]:
            if not (isinstance(c, dict) and c.get("ru") and c.get("en")):
                continue
            new_countries[cid] = {"ru": str(c["ru"]), "en": str(c["en"]), "n": 0}
        countries.append(cid)
    year = out.get("year") if isinstance(out.get("year"), dict) else None
    y = None
    if year and (year.get("ru") or year.get("en")):
        sort = year.get("sort")
        try:
            sort = int(sort) if sort is not None else None
        except (TypeError, ValueError):
            sort = None
        y = {"ru": str(year.get("ru") or year.get("en")), "en": str(year.get("en") or year.get("ru")), "s": sort}
    raw_en = out.get("text_en") or []
    text_en = [str(p).strip() for p in ([raw_en] if isinstance(raw_en, str) else raw_en) if str(p).strip()]
    if len(text_en) != len(text_ru):
        text_en = [" ".join(text_en)] if text_en and len(text_ru) == 1 else (text_en or [])
    cr_en = out.get("credits_en") or {}
    cr = []
    for k, role in (("pr", "project"), ("ph", "photo"), ("via", "source")):
        name = credits.get(k)
        if not name:
            continue
        url = credits.get(k + "_url") if str(credits.get(k + "_url") or "").startswith("http") else None
        entry = [role, str(name), url]
        en = str(cr_en.get(k) or "").strip()
        if en and en != str(name):
            entry.append(en)
        cr.append(entry)
    cat = config.CATEGORY_ALIASES.get(post["category"] or "", post["category"] or "")
    rec = {
        "d": _today(), "cats": [cat if cat in CATS else "architecture"], "co": countries,
        "t": title, "p": people, "pl": _bi(out.get("place")), "y": y,
        "per": period_of(y["s"]) if y else None,
        "b": {"ru": text_ru, "en": text_en if len(text_en) == len(text_ru) else []},
        "cr": cr, "img": img,
    }
    if fmt == "std" and text_ru:
        rec["s"] = {"ru": first_sentence(text_ru[0]), "en": first_sentence(text_en[0]) if text_en else first_sentence(text_ru[0])}
    if not rec["pl"]:
        rec.pop("pl")
    if not rec["y"]:
        rec.pop("y"); rec.pop("per")
    rec["_new"] = {"people": new_people, "countries": new_countries}
    return rec, files


def with_object(D: dict, rec: dict) -> dict:
    D = json.loads(json.dumps(D))
    new = rec.pop("_new", None) or {}
    for k, v in (new.get("people") or {}).items():
        D["people"].setdefault(k, v)
    for k, v in (new.get("countries") or {}).items():
        D["countries"].setdefault(k, v)
    D["objects"] = [o for o in D["objects"] if o["id"] != rec["id"]]
    D["objects"].insert(0, rec)
    D["objects"].sort(key=lambda o: o["id"], reverse=True)   # номера постов растут со временем; временный — самый большой
    return recount(D)


async def note_record(post, D: dict, nid: int) -> tuple[dict, dict[str, bytes]]:
    data = json.loads(post["data"])
    parts = formatter.headline_parts(data)
    paras = paragraphs(data.get("body", ""))
    if not paras:
        raise SiteError("у заметки нет текста")
    photos = await asyncio.to_thread(_photos, post)
    content = (f"# Title\n{parts[0] if parts else ''}\n\n# Subtitle\n{parts[1] if len(parts) > 1 else '(none)'}\n\n"
               "# Paragraphs\n" + "\n\n".join(paras) +
               f"\n\n# Hashtags\n{' '.join(formatter.normalize_tags(data.get('tags', []), 'notes'))}\n\n"
               f"# Index of people already on the site (id | Russian | English)\n{_people_list(D)}")
    out = await curator._call(content, system=NOTE_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=6000)
    raw_en = out.get("paragraphs_en") or []
    en = [str(p).strip() for p in ([raw_en] if isinstance(raw_en, str) else raw_en) if str(p).strip()]
    if len(en) != len(paras):
        raise SiteError("перевод заметки разошёлся по абзацам")
    key = f"n{nid}"                   # номера заметок пересекаются с номерами постов, картинки — нет
    img, files = await asyncio.to_thread(sitebuild.make_images, photos, key, True)   # и фото по отдельности — для Instant View
    blocks = [{"type": "p", "ru": r, "en": e} for r, e in zip(paras, en)]
    # фото после абзацев, равномерно; обложка — первая
    extra = min(len(img["segs"]) - 1, max(0, len(blocks) - 1))
    slots = sorted({round((i + 1) * (len(blocks) - 1) / (extra + 1)) - 1 for i in range(extra)}) if extra else []
    slots = [s for s in slots if 0 <= s < len(blocks) - 1]
    section = out.get("section") if out.get("section") in CATS else "architecture"
    title_ru = parts[0] if parts else paras[0][:80]
    rec = {"id": nid, "ik": key, "d": _today(), "t": {"ru": title_ru, "en": str(out.get("title_en") or title_ru)},
           "sf": {"ru": paras[0], "en": en[0]}, "bl": blocks,
           "ppl": [{"id": slug(str(p.get("id") or p.get("en") or "")), "ru": str(p.get("ru") or ""), "en": str(p.get("en") or "")}
                   for p in (out.get("people") or []) if isinstance(p, dict)],
           "slots": slots, "img": img, "cats": [section]}
    if len(parts) > 1:
        rec["sub"] = {"ru": parts[1], "en": str(out.get("subtitle_en") or parts[1])}
    return rec, files


# ======================= сборка и FTP =======================

def _order(rel: str) -> int:
    if rel.startswith("img/"):
        return 0
    if rel.startswith("assets/fonts/") or rel.endswith(".js"):
        return 1
    if rel in ("assets/data.html", "data.json"):
        return 2
    if rel.endswith("index.html") or rel == "404.html":
        return 3
    if rel == sitebuild.MANIFEST:
        return 9
    return 4


def build_diff(D: dict) -> tuple[list[tuple[str, bytes]], dict]:
    """Собирает сайт во временную папку и отдаёт только то, что изменилось против хостинга."""
    out = ROOT / "build"
    shutil.rmtree(out, ignore_errors=True)
    sitebuild.build(D, out, site=SITE_URL)
    manifest = json.loads((out / sitebuild.MANIFEST).read_text("utf-8"))
    old = json.loads(MANIFEST.read_text("utf-8")) if MANIFEST.exists() else {}
    changed = [r for r, h in manifest.items() if old.get(r) != h]
    files = [(r, (out / r).read_bytes()) for r in sorted(changed, key=_order)]
    files.append((sitebuild.MANIFEST, (out / sitebuild.MANIFEST).read_bytes()))
    return files, manifest


class _TLS(ftplib.FTP_TLS):
    """FTPS, где канал данных продолжает TLS-сессию канала команд. Без этого многие серверы (vsftpd, proftpd)
    отказывают в передаче файла: «522 session reuse required»."""
    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


class _NoTLS(Exception):
    """Сервер не знает AUTH TLS — шифрования на нём нет вообще."""


_ftp_state = {"mode": None}


def _connect(mode: str) -> ftplib.FTP:
    if mode == "plain":
        ftp = ftplib.FTP(timeout=60, encoding="utf-8")
        ftp.connect(FTP_HOST, FTP_PORT)
        ftp.login(FTP_USER, FTP_PASS)
    else:
        ctx = ssl.create_default_context()
        if mode == "tls-noverify":     # шифрование без проверки сертификата FTP-сервера
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        ftp = _TLS(timeout=60, context=ctx, encoding="utf-8")
        ftp.connect(FTP_HOST, FTP_PORT)
        try:
            ftp.auth()
        except (ftplib.error_perm, ftplib.error_reply) as exc:
            ftp.close()
            raise _NoTLS(str(exc)) from exc
        ftp.login(FTP_USER, FTP_PASS)
        ftp.prot_p()
    ftp.set_pasv(True)
    if FTP_DIR:
        ftp.cwd(FTP_DIR)          # от папки, в которую FTP пускает после входа
    return ftp


def _modes() -> list[str]:
    if FTP_TLS in ("0", "no", "off", "false", "plain"):
        return ["plain"]
    tls = ["tls", "tls-noverify"]
    if _ftp_state["mode"] == "tls-noverify":
        tls.reverse()          # сертификат сервера уже не прошёл проверку: не стучаться зря каждый раз
    if FTP_TLS in ("1", "yes", "on", "true", "tls"):
        return tls
    return tls + ["plain"]


def _open_ftp() -> tuple[ftplib.FTP, str]:
    """Соединение с хостингом. Пароль без шифрования уходит, только если так велено (SITE_FTP_TLS=0)
    или сервер не умеет TLS вовсе; любая другая ошибка TLS — это ошибка, а не повод открыть пароль."""
    errors, no_tls = [], False
    for mode in _modes():
        if mode == "plain" and FTP_TLS not in ("0", "no", "off", "false", "plain") and not no_tls:
            break
        if mode != "plain" and no_tls:
            continue
        try:
            ftp = _connect(mode)
            ftp.storbinary("STOR .ahmag-ping", io.BytesIO(b"ok"))    # проверка канала данных
            try:
                ftp.delete(".ahmag-ping")
            except ftplib.Error:
                pass
            _ftp_state["mode"] = mode
            return ftp, mode
        except _NoTLS as exc:
            no_tls = True
            errors.append(f"{mode}: сервер без TLS ({exc})")
        except Exception as exc:
            errors.append(f"{mode}: {exc}")
    raise SiteError("FTP не пускает — " + "; ".join(errors)[:400])


def upload(files: list[tuple[str, bytes]]) -> str:
    """Файлы на хостинг. Каждый пишется рядом под временным именем и подменяет старый, чтобы посетитель
    не застал половину файла. → режим соединения (tls / tls-noverify / plain)."""
    if not files:
        return "—"
    ftp, mode = _open_ftp()
    made: set[str] = set()
    try:
        for rel, data in files:
            d = posixpath.dirname(rel)
            if d and d not in made:
                path = ""
                for part in d.split("/"):
                    path = f"{path}/{part}" if path else part
                    if path in made:
                        continue
                    try:
                        ftp.mkd(path)
                    except ftplib.error_perm:
                        pass       # уже есть
                    made.add(path)
            tmp = rel + ".part"
            ftp.storbinary("STOR " + tmp, io.BytesIO(data))
            try:
                ftp.rename(tmp, rel)
            except ftplib.error_perm:
                # сервер не переименовывает поверх файла: пишем прямо на место, живой файл не удаляем
                ftp.storbinary("STOR " + rel, io.BytesIO(data))
                try:
                    ftp.delete(tmp)
                except ftplib.error_perm:
                    pass
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    return mode


# ======================= публикация =======================

_tasks: set = set()


def _spawn(coro) -> asyncio.Task:
    """Задача, которая доживает до конца, даже если пост её не дождался (таймаут): замок не отпускается
    посреди выкладки, а ошибка пишется в лог."""
    task = asyncio.create_task(coro)
    _tasks.add(task)

    def done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not t.cancelled() and t.exception():
            log.warning("Сайт: фоновая задача завершилась ошибкой: %r", t.exception())
    task.add_done_callback(done)
    return task


def _pending_path(pid: int) -> Path:
    return PENDING / f"{pid}.json"


def _stash(pid: int, rec: dict, files: dict[str, bytes] | None = None) -> None:
    """Запись, которая ждёт выкладки, — на диск: повтор после сбоя не зовёт Claude ещё раз."""
    PENDING.mkdir(parents=True, exist_ok=True)
    _pending_path(pid).write_text(json.dumps(rec, ensure_ascii=False), "utf-8")
    for rel, b in (files or {}).items():
        f = PENDING / str(pid) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)


def _stashed_files(pid: int) -> dict[str, bytes]:
    d = PENDING / str(pid)
    return {f.relative_to(d).as_posix(): f.read_bytes() for f in sorted(d.rglob("*")) if f.is_file()} if d.exists() else {}


def _drop(pid: int) -> None:
    _pending_path(pid).unlink(missing_ok=True)
    shutil.rmtree(PENDING / str(pid), ignore_errors=True)


async def prepare(post) -> str | None:
    """Большой пост перед отправкой: запись и временная страница на сайте. → ссылка для подписи."""
    if wants(post) != "object" or post["format"] != "std" or not await enabled():
        return None
    pid = post["id"]
    akey = f"{pid}-{secrets.token_hex(2)}"     # адрес /a/<akey>/ не повторится, даже если базу бота начнут заново
    async with _lock:
        D = await load()
        rec, files = await object_record(post, D, f"b{akey}")
        rec.update(id=TEMP_BASE + pid, ik=f"b{akey}", tmp=True)
        new = rec.get("_new")
        D2 = with_object(D, dict(rec))
        out = ROOT / "prov"
        shutil.rmtree(out, ignore_errors=True)
        pages = await asyncio.to_thread(sitebuild.build_provisional, D2, "object", rec["id"], akey, out, SITE_URL)
        rec["_new"] = new
        _stash(pid, rec, files)                 # до выкладки: если FTP подведёт, после выхода поста Claude не нужен
        batch = list(files.items()) + [(p, (out / p).read_bytes()) for p in pages]
        await asyncio.to_thread(upload, batch)
        shutil.rmtree(PENDING / str(pid), ignore_errors=True)   # картинки уже на сайте — копия не нужна
    if not await _live(f"/a/{akey}/"):
        raise SiteError(f"страница выложена, но не открывается по {SITE_URL}/a/{akey}/ — проверь SITE_FTP_DIR")
    url = f"{SITE_URL}/a/{akey}/"
    data = json.loads(post["data"])
    data["_site_url"] = url
    await db.update_post(pid, data=data)
    return url


async def prepare_safe(post) -> str | None:
    """prepare с потолком по времени: сайт не задерживает пост дольше SITE_TIMEOUT и не роняет его."""
    try:
        if not await enabled():
            return None
        if not await reachable():
            await _status(False, f"{SITE_URL} не открывается — пост {post['id']} ушёл без ссылки, на сайт попадёт после выхода")
            return None
        return await asyncio.wait_for(asyncio.shield(_spawn(prepare(post))), PREPARE_TIMEOUT)
    except Exception as exc:
        log.warning("Сайт: страница для поста %s не готова к выходу: %r", post["id"], exc)
        await _status(False, f"пост {post['id']} ушёл без ссылки на сайт: {curator.explain(exc) if not isinstance(exc, SiteError) else exc}")
        return None


async def finalize(pid: int, msg_id: int) -> str:
    """После выхода в канал: запись получает номер поста, сайт пересобирается. → постоянный адрес."""
    post = await db.get_post(pid)
    if wants(post) != "object":
        return ""
    async with _lock:
        D = await load()
        if any(o["id"] == msg_id and not o.get("tmp") for o in D["objects"]):
            return f"{SITE_URL}/o/{msg_id}/"
        pend = _pending_path(pid)
        if pend.exists():
            rec = json.loads(pend.read_text("utf-8"))
            files = _stashed_files(pid)
        else:
            rec, files = await object_record(post, D, msg_id)
            _stash(pid, rec, files)
        rec.pop("tmp", None)                    # временная страница /a/<ключ>/ станет переадресацией (её делает сборка)
        rec["id"] = msg_id
        D2 = with_object(D, rec)
        batch, manifest = await asyncio.to_thread(build_diff, D2)
        await asyncio.to_thread(upload, list(files.items()) + batch)
        if not await _live(f"/o/{msg_id}/"):
            raise SiteError(f"файлы залиты, но {SITE_URL}/o/{msg_id}/ не открывается — проверь SITE_FTP_DIR")
        _save(D2, manifest)
        _drop(pid)
    url = f"{SITE_URL}/o/{msg_id}/"
    post = await db.get_post(pid)
    data = json.loads(post["data"])
    data["_site_url"] = url
    await db.update_post(pid, data=data)
    log.info("Сайт: пост %s → %s", pid, url)
    return url


async def finalize_safe(bot: Bot, pid: int, msg_id: int) -> None:
    # сначала в очередь: если бот перезапустится посреди сборки, повтор по расписанию доведёт пост до сайта
    await _enqueue(pid, msg_id, SiteError("выкладывается"), quiet=True)
    try:
        url = await finalize(pid, msg_id)
        if url:
            await _status(True, f"пост {pid} на сайте: {url}")
            await _unqueue(pid)
    except Exception as exc:
        log.exception("Сайт: пост %s не выложен", pid)
        await _enqueue(pid, msg_id, exc)
        await _alert(bot, f"🌐 Пост не попал на сайт: {html.escape(str(exc)[:300])}\nПовторю сам через 20 минут.")


async def publish_note(post) -> str | None:
    """#ahmagnotes: полный текст — на сайт до анонса в канале. → адрес заметки."""
    if wants(post) != "note" or not await enabled():
        return None
    data = json.loads(post["data"])
    if data.get("_site_url"):
        return data["_site_url"]
    async with _lock:
        D = await load()
        nid = max([n["id"] for n in D["notes"]] + [1000]) + 1
        rec, files = await note_record(post, D, nid)
        D2 = json.loads(json.dumps(D))
        D2["notes"].insert(0, rec)
        batch, manifest = await asyncio.to_thread(build_diff, D2)
        await asyncio.to_thread(upload, list(files.items()) + batch)
        if not await _live(f"/n/{nid}/"):
            raise SiteError(f"заметка залита, но {SITE_URL}/n/{nid}/ не открывается — проверь SITE_FTP_DIR")
        _save(D2, manifest)
    url = f"{SITE_URL}/n/{nid}/"
    data["_site_url"] = url
    data["_site_url_en"] = f"{SITE_URL}/en/n/{nid}/"
    await db.update_post(post["id"], data=data)
    log.info("Сайт: заметка %s → %s", post["id"], url)
    return url


async def publish_note_safe(post) -> str | None:
    try:
        if not await enabled():
            return None
        if not await reachable():
            await _status(False, f"{SITE_URL} не открывается — заметка {post['id']} ушла в канал целиком")
            return None
        return await asyncio.wait_for(asyncio.shield(_spawn(publish_note(post))), PREPARE_TIMEOUT + 60)
    except Exception as exc:
        log.warning("Сайт: заметка %s не вышла на сайте: %r", post["id"], exc)
        await _status(False, f"заметка {post['id']} ушла в канал целиком: {exc}")
        return None


# ======================= подпись в канал =======================

def iv_link(url: str) -> str:
    """Ссылка с Instant View: Telegram покажет страницу по шаблону, пока шаблон не одобрен для всех."""
    if not IV_RHASH:
        return url
    return f"https://t.me/iv?url={quote(url, safe='')}&rhash={IV_RHASH}"


def with_archive_link(caption: str, url: str, inline: bool) -> str:
    """Тихая строчка «в архиве →» в конце подписи. Если с ней подпись перестанет влезать под фото,
    строчку не ставим: пост не должен разваливаться на фото и отдельный текст."""
    line = f'<a href="{html.escape(url)}">{html.escape(LINK_STD)}</a>'
    out = f"{caption.rstrip()}\n\n{line}"
    if inline and formatter.visible_len(out) > config.CAPTION_LIMIT:
        return caption
    return out


def teaser(data: dict, limit: int = 420) -> str:
    """Анонс заметки: первые фразы самой заметки — то, что автор уже одобрил."""
    paras = paragraphs(data.get("body", ""))
    if not paras:
        return ""
    sents = re.findall(r".+?(?:[.!?…](?=\s|$)|$)", paras[0], re.S)
    out = ""
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if out and len(out) + 1 + len(s) > limit:
            break
        out = f"{out} {s}".strip()
        if len(out) >= 160:
            break
    return out if len(out) <= limit else out[: limit - 1].rsplit(" ", 1)[0] + "…"


def note_announcement(post, url: str) -> tuple[str, str]:
    """Анонс заметки в канал → (HTML-текст, ссылка для превью)."""
    data = json.loads(post["data"])
    parts = formatter.headline_parts(data)
    link = iv_link(url)
    blocks = []
    if parts:
        blocks.append("<b>" + html.escape(" // ".join(parts), quote=False) + "</b>")
    t = teaser(data)
    if t:
        blocks.append(html.escape(t, quote=False))
    blocks.append(f'<a href="{html.escape(link)}">{html.escape(LINK_NOTE)}</a>')
    tags = formatter.normalize_tags(data.get("tags", []), "notes")
    if tags:
        blocks.append(" ".join("#" + x for x in tags))
    return "\n\n".join(blocks), link


def story_link(post) -> str | None:
    """Ссылка для стикера в сторис к заметке — английская страница (Instagram читают по-английски)."""
    data = json.loads(post["data"])
    return data.get("_site_url_en") or data.get("_site_url")


# ======================= очередь повторов и пульт =======================

_qlock = asyncio.Lock()


async def _enqueue(pid: int, msg_id: int, exc: Exception, quiet: bool = False) -> None:
    async with _qlock:
        q = await db.get_setting("site_queue", [])
        tries = next((x["tries"] for x in q if x["pid"] == pid), 0) + (0 if quiet else 1)
        q = [x for x in q if x["pid"] != pid] + [{"pid": pid, "msg": msg_id, "tries": tries, "error": str(exc)[:200], "at": db.now()}]
        await db.set_setting("site_queue", q)
    if not quiet:
        await _status(False, f"пост {pid} ждёт повтора: {exc}")


async def _unqueue(pid: int) -> None:
    async with _qlock:
        q = await db.get_setting("site_queue", [])
        if any(x["pid"] == pid for x in q):
            await db.set_setting("site_queue", [x for x in q if x["pid"] != pid])


_last_alert = 0.0


async def _alert(bot: Bot | None, text: str) -> None:
    global _last_alert
    if not bot or time.time() - _last_alert < 3600:
        return
    _last_alert = time.time()
    try:
        await bot.send_message(config.ADMIN_ID, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🌐 Сайт", callback_data="site:go")]]))
    except Exception:
        log.warning("Сайт: уведомление не ушло", exc_info=True)


async def retry_queue(bot: Bot | None = None, force: bool = False) -> int:
    """Посты, которые вышли в канал, но не попали на сайт: ещё раз. Сам — до 12 попыток на пост,
    кнопкой «🔁 Повторить» — все."""
    if not await enabled():
        return 0
    done = 0
    for x in list(await db.get_setting("site_queue", [])):
        if x["tries"] > 12 and not force:
            continue
        try:
            await finalize(x["pid"], x["msg"])
            await _unqueue(x["pid"])
            done += 1
        except Exception as exc:
            log.warning("Сайт: повтор для поста %s не вышел: %r", x["pid"], exc)
            await _enqueue(x["pid"], x["msg"], exc)
    if done:
        await _status(True, f"догнал сайт: {done}")
    return done


async def check() -> str:
    """Проверка связи: данные сайта и FTP."""
    lines = []
    try:
        D = await load()
        lines.append(f"✅ Данные сайта: записей {len([o for o in D['objects'] if not o.get('tmp')])}, заметок {len(D['notes'])}")
    except Exception as exc:
        lines.append(f"❌ Данные сайта: {exc}")
    if not configured():
        lines.append("❌ FTP: не заданы SITE_FTP_HOST, SITE_FTP_USER, SITE_FTP_PASSWORD в Railway")
    else:
        try:
            ftp, mode = await asyncio.to_thread(_open_ftp)
            ftp.quit()
            lines.append(f"✅ FTP: вход есть ({'с шифрованием' if mode.startswith('tls') else 'без шифрования'})")
        except Exception as exc:
            lines.append(f"❌ FTP: {exc}")
    return "\n".join(lines)


async def view_text() -> str:
    st = await db.get_setting("site_status")
    q = await db.get_setting("site_queue", [])
    on = await enabled()
    why = ("нет папки site/ в репозитории" if sitebuild is None else "нет FTP в Railway")
    lines = [f"<b>🌐 Сайт</b> · {SITE_URL.replace('https://', '')}",
             "Публикация на сайт: " + ("включена" if on else ("выключена" if configured() else f"не настроена ({why})"))]
    if _ftp_state["mode"]:
        lines.append("FTP: " + ("с шифрованием" if _ftp_state["mode"].startswith("tls") else "без шифрования (сервер не умеет TLS)"))
    if st:
        lines.append(("✅ " if st["ok"] else "⚠️ ") + html.escape(st["text"]) + f" · {st['at'][5:16].replace('T', ' ')}")
    if q:
        lines.append(f"Ждут повтора: {len(q)}")
    lines.append("Instant View для заметок: " + ("включён" if IV_RHASH else "нет (переменная IV_RHASH)"))
    return "\n".join(lines)


def _kb(on: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проверить связь", callback_data="site:check"),
         InlineKeyboardButton(text="🔁 Повторить", callback_data="site:retry")],
        [InlineKeyboardButton(text="⏸ Выключить" if on else "▶️ Включить", callback_data="site:toggle")]])


@router.message(Command("site"))
async def cmd_site(msg: Message):
    await msg.answer(await view_text(), reply_markup=_kb(bool(await db.get_setting("site_enabled", True))),
                     disable_web_page_preview=True)


@router.callback_query(F.data.startswith("site:"))
async def on_site(cb: CallbackQuery, bot: Bot):
    act = cb.data.split(":", 1)[1]
    if act == "check":
        await cb.answer("Проверяю…")
        return await cb.message.answer(await check())
    if act == "retry":
        await cb.answer("Повторяю…")
        n = await retry_queue(bot, force=True)
        return await cb.message.answer(f"Готово: на сайт ушло {n}.")
    if act == "toggle":
        await db.set_setting("site_enabled", not bool(await db.get_setting("site_enabled", True)))
        await cb.answer("Готово")
    else:
        await cb.answer()
    await cb.message.answer(await view_text(), reply_markup=_kb(bool(await db.get_setting("site_enabled", True))),
                            disable_web_page_preview=True)


def schedule(sched, bot: Bot, guarded) -> None:
    sched.add_job(guarded(bot, "сайт: повтор", retry_queue, bot), "interval", minutes=20, id="site_retry",
                  max_instances=1)
