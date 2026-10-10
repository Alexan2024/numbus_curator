"""Редакция сайта (7.0): админка theahmag.com в браузере.

Адрес — ADMIN_URL (admin.theahmag.com: свой домен в Railway и запись CNAME в Beget), без него — PUBLIC_URL/admin/.
Работает на том же веб-сервере бота, что раздаёт фото для Instagram (app/instagram.py), и с теми же данными
сайта, что и бот (sitepub: data.json под sitepub._lock).

Вход без пароля: «Войти» → бот присылает в Telegram «✅ Войти» → браузер помнит тебя 30 дней. Подтвердить вход
может только ADMIN_ID. /admin в боте: ссылка на редакцию, «🚪 Выйти везде», «💾 Копия базы».

Что правится
• Архив. Запись целиком: тексты RU/EN, имена, место, год, страна, рубрика, кредиты, дата. Фото: порядок
  (обложка — первое), удалить, добавить свои. Запись можно скрыть и вернуть. Новые записи получают номера
  от 200000 (sitepub.MAN_BASE): без номера AH- и ссылки на Telegram, пока пост о них не выйдет в канале.
  Запись, поправленная руками, получает man: 1 — чистка архива (app/archive.py) её больше не трогает.
• Notes. Редактор в вёрстке сайта: абзацы, подзаголовки, цитаты, примечания; фото на всю ширину, в колонку и
  парой; жирный, курсив, ссылки. Перевод на английский (Claude), черновики, предпросмотр по закрытой ссылке,
  публикация сразу или по расписанию, «Снять с сайта». Старые заметки открываются в том же редакторе.
• Главная (закреплённая обложка), «О журнале», «Партнёрство», имена и страны указателя.
• «Отправить в бот». Запись → пост для канала или Instagram во входящих: текст пишет бот, как для поста по
  ссылке. Заметка → анонс #ahmagnotes во входящих.
• История: у каждой правки сохраняется прежняя версия, любую можно вернуть.

Публикация. Правки встают в очередь (queue.json) и уходят на сайт одной выкладкой: под тем же замком, что и
посты; сайт собирается целиком, по FTP уходят только изменённые файлы. Сбой — повтор с растущей паузой,
очередь на диске переживает перезапуск. Раз в неделю бот присылает копию базы: data.json сайта, скрытые
записи, заметки редакции и базу бота.

Разметка текста заметок: **жирный**, *курсив*, [ссылка](https://…); \\*, \\[, \\] и \\\\ — сами знаки. Такие
заметки помечены fmt: 2 (app.js рисует разметку только у них).

Диск Railway, /data/admin: sessions.json, queue.json, notes/ (заметки редакции), history/, assets/ (фото,
загруженные в редакции), orig/ (их оригиналы без знака AHMAG — для поста в бот), files/img/… (файлы сайта,
которые сделала редакция), cache/ (фото, скачанные с сайта)."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiohttp import web
from PIL import Image, ImageOps

from app import brand, config, curator, db, sitepub, ui

Image.MAX_IMAGE_PIXELS = 80_000_000    # больше — отказ ещё до распаковки (защита от «бомб»)
try:                                  # фото с iPhone (HEIC), если пакет есть
    import pillow_heif                # noqa: E402
    pillow_heif.register_heif_opener()
except Exception:
    pass

log = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)

ROOT = config.DATA_DIR / "admin"
FILES = ROOT / "files"
ORIG = ROOT / "orig"
ASSETS = ROOT / "assets"
NOTES = ROOT / "notes"
HIST = ROOT / "history"
CACHE = ROOT / "cache"
SESSIONS = ROOT / "sessions.json"
QUEUE = ROOT / "queue.json"
ORIGMAP = ROOT / "origmap.json"
WEB = Path(__file__).resolve().parent.parent / "admin"
SITE_WEB = sitepub.SITE_DIR / "web"

MAN_BASE = sitepub.MAN_BASE
SESSION_DAYS = 30
LOGIN_TTL = 300
HIST_KEEP = 40
MAX_UPLOAD = 60 * 1024 * 1024
ORIG_MAX = 3200                       # оригинал без знака: длинная сторона, px
SMALL = 1600                          # фото меньше — предупреждение (редакционная политика: от 1600 px)
TZ = ZoneInfo(config.TZ_NAME)
CATS = sitepub.CATS
ROLES = sitepub.ROLES
CR_ROLES = ("project", "photo", "source", "courtesy", "other")
BLOCKS = ("p", "h", "quote", "example", "img")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IMG_RE = re.compile(r"^(?:c|t|f|m|p)/[A-Za-z0-9_-]{1,48}(?:-\d{1,3})?\.jpg$")


class AdminError(Exception):
    """Понятная ошибка для редакции: текст уходит в браузер как есть."""


# ======================= мелочи =======================

def admin_url() -> str | None:
    """Адрес редакции: ADMIN_URL (свой домен) или адрес бота в Railway."""
    u = os.getenv("ADMIN_URL", "").strip().rstrip("/")
    if not u:
        from app import instagram
        u = instagram.public_url() or ""
    if not u:
        return None
    u = u if u.startswith("http") else "https://" + u
    return u if u.endswith("/admin") else u + "/admin/"


def _read(p: Path, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        log.warning("Редакция: файл %s не читается", p, exc_info=True)
        return default


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tmp.replace(p)


def _now() -> datetime:
    return datetime.now(TZ)


def _iso() -> str:
    return _now().isoformat(timespec="seconds")


def _today() -> str:
    return _now().date().isoformat()


def _deep(x):
    return copy.deepcopy(x)


def _s(v, limit: int = 20000) -> str:
    """Строка без лишних пробелов по краям и без невидимых управляющих знаков."""
    s = str(v if v is not None else "")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s).replace("\r\n", "\n").replace("\r", "\n").strip()
    return s[:limit]


def _bi(v, limit: int = 4000) -> dict | None:
    """{ru, en} или None. Пустой английский — пусть будет пустым: сайт тогда покажет русский."""
    if not isinstance(v, dict):
        return None
    ru, en = _s(v.get("ru"), limit), _s(v.get("en"), limit)
    if not ru and not en:
        return None
    return {"ru": ru or en, "en": en}


_MD_SPECIAL = re.compile(r"([\\*\[\]])")


def md_escape(s) -> str:
    """Обычный текст → текст разметки: звёздочки и скобки остаются знаками."""
    return _MD_SPECIAL.sub(r"\\\1", str(s or ""))


def md_plain(s) -> str:
    """Текст разметки → простой текст (описание страницы, текст поста для бота)."""
    s = str(s or "")
    s = re.sub(r"(?<!\\)\[([^\]\n]+?)(?<!\\)\]\(([^)\s]+)\)", r"\1", s)
    s = re.sub(r"(?<!\\)\*\*(.+?)(?<!\\)\*\*", r"\1", s)
    s = re.sub(r"(?<!\\)\*(.+?)(?<!\\)\*", r"\1", s)
    return re.sub(r"\\([\\*\[\]])", r"\1", s).strip()


def _ua(request: web.Request) -> str:
    ua = request.headers.get("User-Agent", "")
    br = next((n for n, rx in (("Edge", r"Edg/"), ("Opera", r"OPR/"), ("Яндекс Браузер", r"YaBrowser"),
                               ("Chrome", r"Chrome/"), ("Firefox", r"Firefox/"), ("Safari", r"Safari/"))
               if re.search(rx, ua)), "браузер")
    os_ = next((n for n, rx in (("iPhone", r"iPhone"), ("iPad", r"iPad"), ("Android", r"Android"),
                                ("Mac", r"Macintosh"), ("Windows", r"Windows"), ("Linux", r"Linux"))
                if re.search(rx, ua)), "")
    return f"{br}{', ' + os_ if os_ else ''}"


def _ip(request: web.Request) -> str:
    """Адрес браузера: последний в X-Forwarded-For (его дописывает прокси Railway, подделать нельзя)."""
    xff = [x.strip() for x in request.headers.get("X-Forwarded-For", "").split(",") if x.strip()]
    return (xff[-1] if xff else request.remote or "")[:60]


def _secure(request: web.Request) -> bool:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    return proto == "https" or request.host.split(":")[0] not in ("localhost", "127.0.0.1")


def _err(text: str, status: int = 400) -> web.Response:
    return web.json_response({"error": text}, status=status)


async def _body(request: web.Request) -> dict:
    try:
        d = await request.json()
    except Exception:
        raise AdminError("Запрос не прочитался") from None
    if not isinstance(d, dict):
        raise AdminError("Запрос не прочитался")
    return d


def _ok(**kw) -> web.Response:
    return web.json_response({"ok": True, **kw})


# ======================= вход =======================

def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _sessions() -> dict:
    now = time.time()
    return {k: v for k, v in _read(SESSIONS, {}).items() if isinstance(v, dict) and v.get("exp", 0) > now}


def _new_session(ua: str, ip: str) -> str:
    tok = secrets.token_urlsafe(32)
    s = _sessions()
    s[_hash(tok)] = {"exp": time.time() + SESSION_DAYS * 86400, "at": _iso(), "ua": ua, "ip": ip, "seen": _iso()}
    _write(SESSIONS, s)
    return tok


def _session(request: web.Request) -> dict | None:
    tok = request.cookies.get("ahs")
    if not tok:
        return None
    s = _sessions()
    x = s.get(_hash(tok))
    if not x:
        return None
    if time.time() - x.get("_t", 0) > 600:           # «последний раз» — не чаще раза в 10 минут
        x["_t"] = time.time()
        x["seen"] = _iso()
        _write(SESSIONS, s)
    return x


_logins: dict[str, dict] = {}
_login_times: list[tuple[float, str]] = []


async def api_login_start(request: web.Request) -> web.Response:
    now = time.time()
    ip = _ip(request)
    _login_times[:] = [t for t in _login_times if now - t[0] < 3600]
    mine = [t for t, a in _login_times if a == ip]
    if mine and now - mine[-1] < 12:
        return _err("Подожди несколько секунд и нажми ещё раз", 429)
    if len(mine) >= 8 or len(_login_times) >= 40:
        return _err("Слишком много попыток входа за час — попробуй позже", 429)
    bot = ui.BOT
    if bot is None:
        return _err("Бот ещё запускается — попробуй через минуту", 503)
    for k in [k for k, v in _logins.items() if now - v["t"] > LOGIN_TTL]:
        _logins.pop(k, None)
    lid, secret = secrets.token_hex(8), secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(10000):04d}"         # тот же код в браузере и в Telegram: чужой запрос не спутать
    who = _ua(request)
    _logins[lid] = {"h": _hash(secret), "t": now, "state": "pending", "ua": who, "ip": ip, "code": code}
    _login_times.append((now, ip))
    text = (f"🔐 <b>Вход в редакцию сайта</b>\nКод: <b>{code}</b> — должен совпасть с кодом в браузере.\n"
            f"{html.escape(who)} · IP {html.escape(ip)} · {_now():%H:%M}\n\n"
            "Если код другой или входишь не ты — нажми «Это не я».")
    try:
        await bot.send_message(config.ADMIN_ID, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Войти · {code}", callback_data=f"adm:ok:{lid}"),
            InlineKeyboardButton(text="✕ Это не я", callback_data=f"adm:no:{lid}")]]))
    except Exception:
        log.warning("Редакция: запрос на вход не ушёл в Telegram", exc_info=True)
        _logins.pop(lid, None)
        return _err("Не получилось отправить подтверждение в Telegram", 502)
    resp = web.json_response({"id": lid, "code": code})
    resp.set_cookie("ahl", secret, max_age=LOGIN_TTL, httponly=True, secure=_secure(request), samesite="Lax",
                    path="/admin")
    return resp


async def api_login_poll(request: web.Request) -> web.Response:
    try:
        body = await _body(request)
    except AdminError as exc:
        return _err(str(exc))
    lid = str(body.get("id") or "")
    x = _logins.get(lid)
    if not x or time.time() - x["t"] > LOGIN_TTL:
        return web.json_response({"state": "expired"})
    if not secrets.compare_digest(_hash(request.cookies.get("ahl", "")), x["h"]):
        return web.json_response({"state": "expired"})
    if x["state"] != "ok":
        return web.json_response({"state": x["state"]})
    _logins.pop(lid, None)
    tok = _new_session(x["ua"], x["ip"])
    resp = web.json_response({"state": "ok"})
    resp.set_cookie("ahs", tok, max_age=SESSION_DAYS * 86400, httponly=True, secure=_secure(request),
                    samesite="Lax", path="/admin")
    resp.del_cookie("ahl", path="/admin")
    return resp


async def api_logout(request: web.Request) -> web.Response:
    tok = request.cookies.get("ahs")
    if tok:
        s = _sessions()
        s.pop(_hash(tok), None)
        _write(SESSIONS, s)
    resp = _ok()
    resp.del_cookie("ahs", path="/admin")
    return resp


def auth(fn):
    """Обработчик API только для вошедшего; меняющие запросы — только со своим заголовком (защита от чужих форм)."""
    async def handler(request: web.Request) -> web.StreamResponse:
        if not _session(request):
            return _err("Нужно войти", 401)
        if request.method != "GET" and request.headers.get("X-AH") != "1":
            return _err("Запрос без заголовка редакции", 403)
        try:
            return await fn(request)
        except web.HTTPException:
            raise
        except AdminError as exc:
            return _err(str(exc))
        except Exception as exc:
            log.exception("Редакция: %s", request.path)
            return _err(f"Сбой: {curator.explain(exc)}", 500)
    return handler


# ======================= данные: сайт + очередь правок =======================

_qlock = asyncio.Lock()
_wake: asyncio.Event | None = None
_status = {"state": "idle", "ok_at": None, "error": None, "try_at": None}


def queue() -> list:
    return _read(QUEUE, [])


async def effective() -> tuple[dict, dict]:
    """Данные сайта такими, какими они станут после очереди правок: это видит редакция. → (D, скрытые)"""
    from app import archive
    D = await sitepub.load()
    H = archive._load_hidden()
    D, H, _, _ = apply_changes(D, H, queue())
    return D, H


def _find(lst: list, rid) -> int | None:
    return next((i for i, r in enumerate(lst) if r.get("id") == rid), None)


def _hidden_entry(D: dict, rec: dict, reason: str) -> dict:
    """Скрытая запись в том же виде, что у чистки архива (app/archive.py): её возвращает и /archive restore."""
    return {"rec": rec, "reason": reason, "at": db.now(),
            "people": {p["id"]: D["people"][p["id"]] for p in rec.get("p") or [] if p.get("id") in D["people"]},
            "countries": {c: D["countries"][c] for c in rec.get("co") or [] if c in D["countries"]}}


def apply_changes(D: dict, H: dict, changes: list) -> tuple[dict, dict, list[str], list[str]]:
    """Правки редакции поверх данных сайта. Меняет и возвращает D и H (скрытые). → (D, H, файлы для выкладки, отчёт)"""
    files: list[str] = []
    report: list[str] = []
    for ch in changes:
        t = ch.get("t")
        try:
            if t == "obj":
                rid, rec = ch["id"], _deep(ch["rec"])
                for k, v in (ch.get("people") or {}).items():
                    D["people"].setdefault(k, _deep(v))
                for k, v in (ch.get("countries") or {}).items():
                    D["countries"].setdefault(k, _deep(v))
                idx = _find(D["objects"], rid)
                if idx is not None:
                    cur = D["objects"][idx]
                    if cur.get("tmp"):
                        report.append(f"запись {rid}: временная — пропущена")
                        continue
                    if cur.get("tg") and not rec.get("tg"):
                        rec["tg"] = cur["tg"]                 # пост в канале вышел, пока правка ждала выкладки
                    D["objects"][idx] = rec
                elif str(rid) in H:
                    H[str(rid)]["rec"] = rec                  # запись скрыта — правка ляжет в скрытую копию
                    for k, v in (ch.get("people") or {}).items():
                        H[str(rid)].setdefault("people", {}).setdefault(k, _deep(v))
                    for k, v in (ch.get("countries") or {}).items():
                        H[str(rid)].setdefault("countries", {}).setdefault(k, _deep(v))
                elif ch.get("new") or rid >= MAN_BASE:
                    D["objects"].append(rec)
                else:
                    report.append(f"запись {rid}: на сайте её уже нет — правка пропущена")
                    continue
                files += ch.get("files") or []
                report.append(f"запись {rid}")
            elif t == "hide":
                rid = ch["id"]
                idx = _find(D["objects"], rid)
                if idx is None or D["objects"][idx].get("tmp"):
                    continue
                rec = D["objects"].pop(idx)
                H[str(rid)] = _hidden_entry(D, rec, "скрыта в редакции")
                report.append(f"запись {rid} скрыта")
            elif t == "unhide":
                rid = ch["id"]
                x = H.pop(str(rid), None)
                if not x or _find(D["objects"], rid) is not None:
                    continue
                for k, v in (x.get("people") or {}).items():
                    if v:
                        D["people"].setdefault(k, {**v, "objs": []})
                for k, v in (x.get("countries") or {}).items():
                    if v:
                        D["countries"].setdefault(k, {**v, "n": 0})
                D["objects"].append(x["rec"])
                report.append(f"запись {rid} снова на сайте")
            elif t == "note":
                rec = _deep(ch["rec"])
                idx = _find(D["notes"], rec["id"])
                if idx is None:
                    D["notes"].append(rec)
                else:
                    D["notes"][idx] = rec
                D["notes"].sort(key=lambda n: (str(n.get("d") or ""), n["id"]), reverse=True)
                files += ch.get("files") or []
                report.append(f"заметка {rec['id']}")
            elif t == "unnote":
                idx = _find(D["notes"], ch["id"])
                if idx is not None:
                    D["notes"].pop(idx)
                    report.append(f"заметка {ch['id']} снята с сайта")
            elif t == "pages":
                D["pages"] = _deep(ch["pages"])
                report.append("страницы")
            elif t == "home":
                if ch.get("pin"):
                    D["home"] = {"pin": ch["pin"]}
                else:
                    D.pop("home", None)
                report.append("главная")
            elif t == "person":
                cur = D["people"].get(ch["id"])
                if cur:
                    cur.update({k: v for k, v in ch["rec"].items() if k in ("ru", "en", "roles", "life")})
                    if not cur.get("life"):
                        cur.pop("life", None)
                for x in H.values():          # и в копиях скрытых записей
                    p = (x.get("people") or {}).get(ch["id"])
                    if p:
                        p.update({k: v for k, v in ch["rec"].items() if k in ("ru", "en", "roles", "life")})
                report.append(f"имя {ch['id']}")
            elif t == "country":
                cur = D["countries"].get(ch["id"])
                if cur:
                    cur.update({k: v for k, v in ch["rec"].items() if k in ("ru", "en")})
                report.append(f"страна {ch['id']}")
        except Exception:
            log.warning("Редакция: правка %s не применилась", t, exc_info=True)
            report.append(f"{t}: не применилась")
    # ссылки на имена и страны, которых нет, — убрать: иначе сайт не соберётся
    for o in D["objects"]:
        o["p"] = [p for p in o.get("p") or [] if p.get("id") in D["people"]]
        o["co"] = [c for c in o.get("co") or [] if c in D["countries"]]
    home = (D.get("home") or {}).get("pin")
    if home and _find(D["objects"], home) is None:
        D.pop("home", None)
    sitepub.recount(D)
    sitepub.order(D)
    return D, H, list(dict.fromkeys(files)), report


async def enqueue(ch: dict) -> None:
    """Правка — в очередь на сайт. Новая правка того же предмета заменяет старую (файлы обеих уйдут)."""
    async with _qlock:
        q = queue()
        ch = {**ch, "cid": secrets.token_hex(4), "at": _iso()}
        key = (ch["t"], ch.get("id"))
        pos = None
        if ch["t"] in ("obj", "note", "pages", "home", "person", "country"):
            for old in [c for c in q if (c["t"], c.get("id")) == key]:
                ch["files"] = list(dict.fromkeys((old.get("files") or []) + (ch.get("files") or [])))
                if old.get("new"):
                    ch["new"] = True
                if pos is None:
                    pos = q.index(old)               # на место старой правки: порядок с «скрыть» и др. не меняется
                q.remove(old)
        if pos is None:
            q.append(ch)
        else:
            q.insert(pos, ch)
        _write(QUEUE, q)
    if _status["state"] in ("idle", "error"):
        _status["state"] = "pending"
    if _wake:
        _wake.set()


def status() -> dict:
    n = len(queue())
    st = dict(_status)
    if n and st["state"] == "idle":
        st["state"] = "pending"
    if not n and st["state"] == "pending":
        st["state"] = "idle"
    st["n"] = n
    return st


async def publish_once() -> int:
    """Очередь правок → на сайт одной выкладкой. → сколько правок ушло. Ошибка — исключение (очередь остаётся)."""
    from app import archive
    q = queue()
    if not q:
        _status.update(state="idle", error=None)
        return 0
    if not await sitepub.enabled():
        raise AdminError("публикация на сайт выключена или не настроена — /site в боте")
    if not sitepub._calm(6):
        _status.update(state="pending", error=None)
        raise _Later()                      # слот публикации через минуты: замок сайта нужен посту
    _status.update(state="publishing")
    async with sitepub._lock:
        D = await sitepub.load()
        H = archive._load_hidden()
        before = set(H)
        D2, H2, rels, report = apply_changes(D, H, q)
        files = []
        for rel in rels:
            p = FILES / rel
            if p.exists():
                files.append((rel, p.read_bytes()))
            else:
                log.warning("Редакция: файла %s нет на диске", rel)
        pre = archive._load_hidden()                 # до выкладки: скрытые не потеряются, даже если она оборвётся
        pre.update({k: v for k, v in H2.items() if k not in before})
        archive._save_hidden(pre)
        batch, manifest = await asyncio.to_thread(sitepub.build_diff, D2)
        old = json.loads(sitepub.MANIFEST.read_text("utf-8")) if sitepub.MANIFEST.exists() else {}
        gone = [r for r in old if r not in manifest and r.endswith("/index.html")]
        await asyncio.to_thread(sitepub.upload, files + batch, gone)
        sitepub._save(D2, manifest)
        archive._save_hidden(H2)
    async with _qlock:
        done = {c["cid"] for c in q}
        rest = [c for c in queue() if c["cid"] not in done]
        _write(QUEUE, rest)
    for rel in rels:                                 # фото редакции уже на сайте — второй раз не выкладывать
        m = re.match(r"img/[cftm]/(u[0-9a-f]+)(?:-\d+)?\.jpg$", rel)
        if m:
            meta = _asset(m.group(1))
            if meta and not meta.get("up"):
                meta["up"] = True
                _write(ASSETS / f"{meta['uid']}.json", meta)
    _status.update(state="pending" if rest else "idle", ok_at=_iso(), error=None, try_at=None)
    await sitepub._status(True, "правки редакции на сайте: " + ", ".join(report[:4]))
    log.info("Редакция: на сайт ушло правок %s (%s)", len(q), "; ".join(report[:8]))
    return len(q)


class _Later(Exception):
    """Выкладку лучше отложить на минуту."""


_last_alert = 0.0


async def _loop(bot: Bot | None) -> None:
    """Выкладка правок: ждёт правку, собирает соседние (2.5 с), выкладывает; сбой — повтор с паузой."""
    global _last_alert
    delay = 0
    while True:
        try:
            if not queue():
                _wake.clear()
                await _wake.wait()
            _wake.clear()
            await asyncio.sleep(2.5)
            await publish_once()
            delay = 0
        except asyncio.CancelledError:
            raise
        except _Later:
            await asyncio.sleep(60)
            continue
        except Exception as exc:
            delay = min(600, max(30, delay * 2))
            text = str(exc) if isinstance(exc, (AdminError, sitepub.SiteError)) else curator.explain(exc)
            _status.update(state="error", error=text[:300],
                           try_at=(_now() + timedelta(seconds=delay)).isoformat(timespec="seconds"))
            log.warning("Редакция: выкладка не прошла (%r), повтор через %s с", exc, delay)
            if bot and time.time() - _last_alert > 3600:
                _last_alert = time.time()
                try:
                    await bot.send_message(config.ADMIN_ID, f"🛠 Правки редакции не дошли до сайта: "
                                           f"{html.escape(text[:300])}\nПовторяю сам.")
                except Exception:
                    pass
            try:
                await asyncio.wait_for(_wake.wait(), delay)
            except asyncio.TimeoutError:
                pass


def start(bot: Bot | None) -> None:
    """Запуск выкладки правок (после веб-сервера)."""
    global _wake
    if _wake is not None:
        return
    _wake = asyncio.Event()
    if queue():
        _wake.set()
    task = asyncio.create_task(_loop(bot))
    sitepub._tasks.add(task)


# ======================= история =======================

def _hist_path(kind: str, iid) -> Path:
    return HIST / f"{kind}-{iid}.json"


def hist_add(kind: str, iid, payload: dict, label: str) -> None:
    lst = _read(_hist_path(kind, iid), [])
    lst.append({"at": _iso(), "label": label, **payload})
    _write(_hist_path(kind, iid), lst[-HIST_KEEP:])


def _obj_payload(D: dict, rec: dict) -> dict:
    return {"rec": _deep(rec),
            "people": {p["id"]: _deep(D["people"][p["id"]]) for p in rec.get("p") or [] if p.get("id") in D["people"]},
            "countries": {c: _deep(D["countries"][c]) for c in rec.get("co") or [] if c in D["countries"]}}


# ======================= фото =======================

def _asset(uid: str) -> dict | None:
    if not KEY_RE.match(uid or ""):
        return None
    return _read(ASSETS / f"{uid}.json", None)


def _put(rel: str, b: bytes) -> None:
    p = FILES / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b)


async def _site_bytes(client: httpx.AsyncClient, rel: str, missing_ok: bool = False) -> bytes | None:
    """Файл сайта: свой (редакции), из кэша или с самого сайта."""
    for base in (FILES, CACHE):
        p = base / rel
        if p.exists():
            return p.read_bytes()
    try:
        r = await client.get(f"{sitepub.SITE_URL}/{rel}", timeout=40)
    except Exception as exc:
        raise AdminError(f"фото {rel} не скачалось с сайта ({exc.__class__.__name__})") from exc
    if r.status_code == 404 and missing_ok:
        return None
    if r.status_code != 200 or not r.content:
        raise AdminError(f"фото {rel} не скачалось с сайта ({r.status_code})")
    p = CACHE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(r.content)
    return r.content


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}, follow_redirects=True)


def _mid(f: bytes) -> bytes:
    sb = sitepub.sitebuild
    return sb._jpeg(sb._fit(sb._open(Image.open(io.BytesIO(f))), sb.MID), 82)


def _cover_files(key: str, f: bytes) -> tuple[int, int]:
    """Обложка img/c и квадрат img/t по первому фото. → размер обложки"""
    sb = sitepub.sitebuild
    cover = sb._fit(sb._open(Image.open(io.BytesIO(f))), sb.BOX)
    _put(f"img/c/{key}.jpg", sb._jpeg(cover, 86))
    _put(f"img/t/{key}.jpg", sb._jpeg(sb._tile(cover), 80))
    return cover.width, cover.height


def _strip_cut(strip: bytes, g: list) -> bytes:
    im = Image.open(io.BytesIO(strip)).convert("RGB")
    y0, w, h = int(g[0]), int(g[1]), int(g[2])
    return sitepub.sitebuild._jpeg(im.crop((0, y0, min(w, im.width), min(y0 + h, im.height))), 90)


def _save_asset(im: Image.Image, name: str = "", orig: bool = True, src: str = "") -> dict:
    """Фото → файлы сайта со знаком AHMAG (img/f, m, c, t под новым ключом) и оригинал без знака. → описание"""
    sb = sitepub.sitebuild
    uid = "u" + secrets.token_hex(5)
    im = sb._open(im)
    if orig:
        o = sb._fit(im, ORIG_MAX)
        ORIG.mkdir(parents=True, exist_ok=True)
        o.save(ORIG / f"{uid}.jpg", "JPEG", quality=94)
    stamped = brand.stamp(im) if (brand.enabled() and not src) else im      # фото с сайта уже со знаком
    img, files = sb.make_images([stamped], uid)
    for rel, b in files.items():
        _put(rel, b)
    g = img["segs"][0]
    meta = {"uid": uid, "w": g[1], "h": g[2], "cw": img["cw"], "ch": img["ch"], "sw": im.width, "sh": im.height,
            "small": max(im.size) < SMALL, "name": name[:120], "at": _iso(), "src": src}
    if orig:
        meta["orig"] = f"{uid}.jpg"
    _write(ASSETS / f"{uid}.json", meta)
    return meta


def _asset_from_bytes(f: bytes, m: bytes | None, src: str, orig_path: str | None) -> dict:
    """Фото, которое уже есть на сайте, — отдельным фото редакции (для обложки заметки): те же байты, без пересжатия."""
    uid = "u" + secrets.token_hex(5)
    im = Image.open(io.BytesIO(f))
    _put(f"img/f/{uid}-0.jpg", f)
    _put(f"img/m/{uid}-0.jpg", m or _mid(f))
    cw, ch = _cover_files(uid, f)
    meta = {"uid": uid, "w": im.width, "h": im.height, "cw": cw, "ch": ch, "sw": im.width, "sh": im.height,
            "small": max(im.size) < SMALL, "name": "", "at": _iso(), "src": src}
    if orig_path:
        meta["orig_abs"] = orig_path
    _write(ASSETS / f"{uid}.json", meta)
    return meta


def _public(meta: dict) -> dict:
    return {k: meta[k] for k in ("uid", "w", "h", "cw", "ch", "sw", "sh", "small", "name") if k in meta}


async def api_upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "file":
        field = await reader.next()
    if field is None:
        raise AdminError("Файл не пришёл")
    buf = io.BytesIO()
    while True:
        chunk = await field.read_chunk(1 << 20)
        if not chunk:
            break
        buf.write(chunk)
        if buf.tell() > MAX_UPLOAD:
            raise AdminError("Файл больше 60 МБ")
    name = field.filename or ""

    def work() -> dict:
        try:
            im = Image.open(io.BytesIO(buf.getvalue()))
        except Image.DecompressionBombError:
            raise AdminError("Фото больше 80 мегапикселей — уменьши его") from None
        except Exception:
            raise AdminError(f"«{name}» — не картинка или формат не читается (нужен JPEG, PNG, WebP или HEIC)") from None
        if im.width * im.height > 80_000_000:
            raise AdminError("Фото больше 80 мегапикселей — уменьши его")
        try:
            im.load()
        except Exception:
            raise AdminError(f"«{name}» не читается — файл повреждён?") from None
        return _save_asset(ImageOps.exif_transpose(im), name)

    meta = await asyncio.to_thread(work)
    return _ok(asset=_public(meta))


async def materialize(k: str, i: int) -> dict:
    """Фото сайта (ключ k, номер i) → отдельное фото редакции. → описание"""
    if not KEY_RE.match(k) or not (0 <= i < 200):
        raise AdminError("Не то фото")
    a = _asset(k)
    if a and i == 0:
        return a
    async with _client() as client:
        f = await _site_bytes(client, f"img/f/{k}-{i}.jpg")
        m = await _site_bytes(client, f"img/m/{k}-{i}.jpg", missing_ok=True)
    orig = _read(ORIGMAP, {}).get(f"img/f/{k}-{i}.jpg")
    return await asyncio.to_thread(_asset_from_bytes, f, m, f"{k}-{i}", orig)


async def api_asset_copy(request: web.Request) -> web.Response:
    b = await _body(request)
    meta = await materialize(str(b.get("k") or ""), int(b.get("i") or 0))
    return _ok(asset=_public(meta))


async def compose_photos(rid: int, old: dict | None, plan: list) -> tuple[dict, str, list[str]] | None:
    """Новый порядок и состав фото записи → (img, новый ключ, файлы). None — фото не менялись.
    Свои фото записи копируются байт в байт (без пересжатия), новые — фото редакции со знаком."""
    if not isinstance(plan, list) or not plan:
        raise AdminError("Нужно хотя бы одно фото")
    if len(plan) > 40:
        raise AdminError("Больше 40 фото в одной записи — многовато")
    old = old or {}
    okey = str(old.get("ik") or old.get("id") or "")
    oimg = old.get("img") or {}
    osegs = oimg.get("segs") or []
    if (old and len(plan) == len(osegs)
            and all(isinstance(r, dict) and str(r.get("k")) == okey and r.get("i") == n for n, r in enumerate(plan))):
        return None
    omap = _read(ORIGMAP, {})
    parts: list[tuple[bytes, bytes | None, int, str | None]] = []
    strip = None
    async with _client() as client:
        for r in plan:
            if not isinstance(r, dict):
                raise AdminError("Не то фото")
            if r.get("u"):
                a = _asset(str(r["u"]))
                if not a:
                    raise AdminError("Загруженное фото потерялось — загрузи его ещё раз")
                uid = a["uid"]
                parts.append(((FILES / f"img/f/{uid}-0.jpg").read_bytes(), (FILES / f"img/m/{uid}-0.jpg").read_bytes(),
                              0, str(ORIG / a["orig"]) if a.get("orig") else a.get("orig_abs")))
                continue
            k, i = str(r.get("k")), r.get("i")
            if k != okey or not isinstance(i, int) or not (0 <= i < len(osegs)):
                raise AdminError("В списке фото — фото не из этой записи")
            g = osegs[i]
            vid = int(g[3]) if len(g) > 3 and g[3] else 0
            if oimg.get("v", 0) >= 2:
                f = await _site_bytes(client, f"img/f/{k}-{i}.jpg")
                m = await _site_bytes(client, f"img/m/{k}-{i}.jpg", missing_ok=True)
            else:                                  # старая запись: все фото одной лентой img/p по 800 px
                if strip is None:
                    strip = await _site_bytes(client, f"img/p/{k}.jpg", missing_ok=True) or b""
                if strip:
                    f = await asyncio.to_thread(_strip_cut, strip, g)
                elif i == 0:
                    f = await _site_bytes(client, f"img/c/{k}.jpg")
                else:
                    raise AdminError("Ленты фото этой записи нет на сайте")
                m = None
            parts.append((f, m, vid, omap.get(f"img/f/{k}-{i}.jpg")))
    nk = f"{rid}e{secrets.token_hex(2)}"

    def build() -> tuple[dict, list[str]]:
        segs, files = [], []
        cw = ch = 0
        for j, (f, m, vid, orig) in enumerate(parts):
            w, h = Image.open(io.BytesIO(f)).size
            relf, relm = f"img/f/{nk}-{j}.jpg", f"img/m/{nk}-{j}.jpg"
            _put(relf, f)
            _put(relm, m or _mid(f))
            files += [relf, relm]
            if orig:
                omap[relf] = orig
            segs.append([0, w, h, vid])
            if j == 0:
                cw, ch = _cover_files(nk, f)
                files += [f"img/c/{nk}.jpg", f"img/t/{nk}.jpg"]
        _write(ORIGMAP, omap)
        return {"v": 2, "W": max(g[1] for g in segs), "H": sum(g[2] for g in segs), "segs": segs,
                "cw": cw, "ch": ch}, files

    img, files = await asyncio.to_thread(build)
    return img, nk, files


# ======================= записи архива =======================

def _next_man_id(D: dict, H: dict) -> int:
    ids = [o["id"] for o in D["objects"] if o["id"] >= MAN_BASE]
    ids += [int(k) for k in H if k.isdigit() and int(k) >= MAN_BASE]
    return max([MAN_BASE] + ids) + 1


def _clean_obj(rid: int, x: dict, old: dict | None, D: dict) -> tuple[dict, dict, dict]:
    """Запись из браузера → запись сайта. → (запись, новые имена, новые страны)"""
    old = old or {}
    rec = {k: _deep(old[k]) for k in ("src", "ig", "tg", "ik", "img") if k in old}
    rec["id"] = rid
    d = _s(x.get("d") or old.get("d") or _today(), 10)
    if not DATE_RE.match(d):
        raise AdminError("Дата — в виде 2026-10-10")
    rec["d"] = d
    cats = [c for c in (x.get("cats") or []) if c in CATS]
    if not cats:
        raise AdminError("Нужна рубрика")
    rec["cats"] = list(dict.fromkeys(cats))[:3]
    t = _bi(x.get("t"), 400)
    if not t or not t["ru"]:
        raise AdminError("Нужен заголовок")
    rec["t"] = t
    new_people, new_countries = {}, {}
    people = []
    for p in x.get("p") or []:
        if not isinstance(p, dict):
            continue
        if isinstance(p.get("new"), dict):
            n = p["new"]
            ru, en = _s(n.get("ru"), 160), _s(n.get("en"), 160)
            if not (ru or en):
                continue
            pid = sitepub.slug(en or ru)
            base, k = pid, 2
            while pid in D["people"] and (D["people"][pid].get("ru"), D["people"][pid].get("en")) != (ru or en, en or ru):
                pid, k = f"{base}-{k}", k + 1
            role = n.get("role") if n.get("role") in ROLES else "architect"
            if pid not in D["people"]:
                new_people[pid] = {"ru": ru or en, "en": en or ru, "roles": [role], "objs": []}
        else:
            pid = _s(p.get("id"), 80)
            if pid not in D["people"]:
                continue
        e = {"id": pid}
        note = _bi(p.get("note"), 120)
        if note:
            e["note"] = note
        if pid not in (q["id"] for q in people):
            people.append(e)
    rec["p"] = people
    co = []
    for c in x.get("co") or []:
        if isinstance(c, dict) and isinstance(c.get("new"), dict):
            ru, en = _s(c["new"].get("ru"), 80), _s(c["new"].get("en"), 80)
            if not (ru and en):
                raise AdminError("У новой страны нужны оба названия, RU и EN")
            cid = sitepub.slug(en)
            if cid not in D["countries"]:
                new_countries[cid] = {"ru": ru, "en": en, "n": 0}
        else:
            cid = _s(c, 80)
            if cid not in D["countries"]:
                continue
        if cid not in co:
            co.append(cid)
    rec["co"] = co
    pl = _bi(x.get("pl"), 200)
    if pl:
        rec["pl"] = pl
    y = x.get("y") if isinstance(x.get("y"), dict) else None
    if y and (_s(y.get("ru")) or _s(y.get("en"))):
        try:
            sort = int(y.get("s")) if str(y.get("s") or "").strip() else None
        except (TypeError, ValueError):
            sort = None
        if sort is None:
            m = re.search(r"(1\d{3}|20\d{2})", _s(y.get("ru")) or _s(y.get("en")))
            sort = int(m.group(1)) if m else None
        rec["y"] = {"ru": _s(y.get("ru"), 80) or _s(y.get("en"), 80), "en": _s(y.get("en"), 80) or _s(y.get("ru"), 80),
                    "s": sort}
        if sort is not None:
            rec["per"] = sitepub.period_of(sort)
            if not rec["per"]:
                rec.pop("per")
    b = x.get("b") if isinstance(x.get("b"), dict) else {}
    ru = [_s(p, 6000) for p in (b.get("ru") or []) if _s(p)][:30]
    en = [_s(p, 6000) for p in (b.get("en") or []) if _s(p)][:30]
    rec["b"] = {"ru": ru, "en": en}
    s = _bi(x.get("s"), 600)
    if s:
        rec["s"] = s
    cr = []
    for c in x.get("cr") or []:
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            continue
        role = c[0] if c[0] in CR_ROLES else "other"
        name = _s(c[1], 200)
        if not name:
            continue
        url = _s(c[2], 600) if len(c) > 2 and c[2] else None
        if url and not re.match(r"^https?://[^\s]+$", url):
            raise AdminError(f"Ссылка в кредитах «{name}» должна начинаться с https://")
        e = [role, name, url or None]
        en_name = _s(c[3], 200) if len(c) > 3 and c[3] else ""
        if en_name and en_name != name:
            e.append(en_name)
        cr.append(e)
    rec["cr"] = cr
    if rid >= MAN_BASE:
        rec["src"] = "man"
    rec["man"] = 1
    return rec, new_people, new_countries


def _obj_of(D: dict, H: dict, rid: int) -> tuple[dict | None, bool]:
    idx = _find(D["objects"], rid)
    if idx is not None:
        return D["objects"][idx], False
    x = H.get(str(rid))
    return (x["rec"], True) if x else (None, False)


async def api_site(request: web.Request) -> web.Response:
    D, H = await effective()
    hidden = [{"id": int(k), "rec": v.get("rec"), "reason": v.get("reason", ""), "at": v.get("at", "")}
              for k, v in H.items() if k.isdigit() and isinstance(v, dict) and v.get("rec")]
    return web.json_response({"D": D, "hidden": hidden, "status": status(), "site": sitepub.SITE_URL,
                              "version": config.VERSION, "busy": sorted(_busy)})


async def api_status(request: web.Request) -> web.Response:
    return web.json_response({"status": status(), "busy": sorted(_busy)})


_new_lock = asyncio.Lock()


async def api_obj_save(request: web.Request) -> web.Response:
    body = await _body(request)
    if body.get("new"):
        async with _new_lock:              # номер новой записи занят до её постановки в очередь
            return await _obj_save(body)
    return await _obj_save(body)


async def _obj_save(body: dict) -> web.Response:
    D, H = await effective()
    x = body.get("rec") or {}
    new = bool(body.get("new"))
    if new:
        rid, old, hidden = _next_man_id(D, H), None, False
    else:
        try:
            rid = int(x.get("id"))
        except (TypeError, ValueError):
            raise AdminError("Нет номера записи") from None
        old, hidden = _obj_of(D, H, rid)
        if not old:
            raise AdminError("Такой записи на сайте нет")
        if old.get("tmp"):
            raise AdminError("Запись ещё временная: пост о ней выходит в канале. Поправь её через пару минут")
    rec, pn, cn = _clean_obj(rid, x, old, D)
    # и имена, и страны, на которые запись ссылается, — с правкой: пока она ждёт выкладки, другая запись с тем же
    # именем могла уйти в скрытые, и имя пропало бы из указателя вместе с ней
    pn = {**{p["id"]: _deep(D["people"][p["id"]]) for p in rec["p"] if p["id"] in D["people"]}, **pn}
    cn = {**{c: _deep(D["countries"][c]) for c in rec["co"] if c in D["countries"]}, **cn}
    if (old or {}).get("man_img"):
        rec["man_img"] = 1
    files: list[str] = []
    if "photos" in body:
        res = await compose_photos(rid, old, body.get("photos"))
        if res:
            rec["img"], rec["ik"], files = res
            rec["man_img"] = 1                 # фото выбраны в редакции — чистка архива их не заменит
    if not rec.get("img"):
        raise AdminError("Нужно хотя бы одно фото")
    if rec.get("ik") == rid:
        rec.pop("ik")
    if old:
        hist_add("obj", rid, _obj_payload(D, old), "до правки")
    await enqueue({"t": "obj", "id": rid, "rec": rec, "people": pn, "countries": cn, "files": files, "new": new})
    return _ok(id=rid, rec=rec, hidden=hidden)


async def api_obj_hide(request: web.Request) -> web.Response:
    body = await _body(request)
    rid = int(body.get("id") or 0)
    D, H = await effective()
    if body.get("hide", True):
        idx = _find(D["objects"], rid)
        if idx is None:
            raise AdminError("Записи на сайте нет")
        if D["objects"][idx].get("tmp"):
            raise AdminError("Запись ещё временная — подожди, пока пост выйдет")
        await enqueue({"t": "hide", "id": rid})
    else:
        if str(rid) not in H:
            raise AdminError("Среди скрытых такой записи нет")
        await enqueue({"t": "unhide", "id": rid})
    return _ok()


# ======================= история =======================

async def api_history(request: web.Request) -> web.Response:
    kind, iid = request.query.get("kind", ""), request.query.get("id", "")
    if kind not in ("obj", "note", "pages", "home") or not re.match(r"^[\w-]{1,20}$", iid):
        raise AdminError("Не тот запрос")
    out = []
    for n, h in enumerate(_read(_hist_path(kind, iid), [])):
        r = h.get("rec") or h.get("doc") or h.get("pages") or {}
        title = ((r.get("t") or {}).get("ru") if isinstance(r, dict) else "") or ""
        out.append({"n": n, "at": h.get("at"), "label": h.get("label"), "title": title})
    return web.json_response({"items": out[::-1]})


async def api_history_get(request: web.Request) -> web.Response:
    kind, iid = request.query.get("kind", ""), request.query.get("id", "")
    n = int(request.query.get("n", "-1"))
    ok = kind in ("obj", "note", "pages", "home") and re.match(r"^[\w-]{1,20}$", iid)
    lst = _read(_hist_path(kind, iid), []) if ok else []
    if not (0 <= n < len(lst)):
        raise AdminError("Такой версии нет")
    return web.json_response({"item": lst[n]})


async def api_history_restore(request: web.Request) -> web.Response:
    b = await _body(request)
    kind, iid, n = str(b.get("kind")), str(b.get("id")), int(b.get("n", -1))
    ok = kind in ("obj", "note", "pages", "home") and re.match(r"^[\w-]{1,20}$", iid)
    lst = _read(_hist_path(kind, iid), []) if ok else []
    if not (0 <= n < len(lst)):
        raise AdminError("Такой версии нет")
    h = lst[n]
    D, H = await effective()
    if kind == "obj":
        rid = int(iid)
        cur, _ = _obj_of(D, H, rid)
        if not cur:
            raise AdminError("Записи нет ни на сайте, ни среди скрытых")
        rec = _deep(h["rec"])
        rec["man"] = 1
        hist_add("obj", rid, _obj_payload(D, cur), "до возврата версии")
        await enqueue({"t": "obj", "id": rid, "rec": rec, "people": h.get("people") or {},
                       "countries": h.get("countries") or {}, "files": []})
        return _ok()
    if kind == "note":
        x = _doc(int(iid))
        if not x:
            raise AdminError("Заметки нет")
        hist_add("note", int(iid), {"doc": _deep(x["doc"])}, "до возврата версии")
        x["doc"] = _deep(h.get("doc") or x["doc"])
        x["updated"] = _iso()
        _save_doc(x)
        return _ok(doc=x["doc"])
    if kind == "pages":
        await enqueue({"t": "pages", "pages": _deep(h["pages"])})
        return _ok()
    raise AdminError("Эту версию так не вернуть")


# ======================= Notes =======================

def _doc_path(nid: int) -> Path:
    return NOTES / f"{int(nid)}.json"


def _doc(nid: int) -> dict | None:
    return _read(_doc_path(nid), None)


def _save_doc(x: dict) -> None:
    _write(_doc_path(x["id"]), x)


def reserved_note_ids() -> list[int]:
    """Номера заметок редакции (и черновиков): бот не займёт их своими #ahmagnotes. И наоборот: номера, которые
    бот уже взял для заметки, пока Claude её переводит (sitepub._note_ids), заняты для редакции."""
    out = list(sitepub._note_ids)
    for p in NOTES.glob("*.json") if NOTES.exists() else []:
        try:
            out.append(int(p.stem))
        except ValueError:
            pass
    return out


def _ref(r, cover: bool = False) -> dict | None:
    if not isinstance(r, dict):
        return None
    k = str(r.get("k") or "")
    try:
        i, w, h = int(r.get("i") or 0), int(r.get("w") or 0), int(r.get("h") or 0)
    except (TypeError, ValueError):
        return None
    if not KEY_RE.match(k) or not (0 <= i < 200) or w <= 0 or h <= 0:
        return None
    out = {"k": k, "i": i, "w": w, "h": h}
    a = _asset(k)
    if a:
        out.update(w=a["w"], h=a["h"])
        if cover:
            out.update(cw=a["cw"], ch=a["ch"])
    elif r.get("m") is False:
        out["m"] = False
    if cover and not a:
        try:
            out["cw"], out["ch"] = int(r.get("cw") or w), int(r.get("ch") or h)
        except (TypeError, ValueError):
            out["cw"], out["ch"] = w, h
    return out


def note_to_doc(n: dict) -> dict:
    """Заметка сайта → документ редактора: фото по слотам становятся блоками фото, текст — разметкой."""
    key = str(n.get("ik") or n["id"])
    img = n.get("img") or {}
    segs = img.get("segs") or []
    hi = img.get("v", 0) >= 2
    cover = None
    if segs:
        cover = {"k": key, "i": 0, "w": segs[0][1], "h": segs[0][2], "cw": img.get("cw") or segs[0][1],
                 "ch": img.get("ch") or segs[0][2]}
        if not hi:
            cover["m"] = False
    if n.get("fmt") == 2:
        bl = _deep(n.get("bl") or [])
    else:
        slots, ph = {}, 1
        for s in n.get("slots") or []:
            if ph < len(segs):
                slots[s] = ph
                ph += 1
        bl = []
        for i, b in enumerate(n.get("bl") or []):
            nb = {"type": b.get("type") if b.get("type") in BLOCKS else "p",
                  "ru": md_escape(b.get("ru")), "en": md_escape(b.get("en"))}
            if b.get("kicker"):
                nb["kicker"] = str(b["kicker"])
            if isinstance(b.get("by"), dict):
                nb["by"] = {"ru": str(b["by"].get("ru") or ""), "en": str(b["by"].get("en") or "")}
            bl.append(nb)
            if i in slots:
                g = segs[slots[i]]
                r = {"k": key, "i": slots[i], "w": g[1], "h": g[2]}
                if not hi:
                    r["m"] = False
                bl.append({"type": "img", "ph": [r], "sz": "wide"})
    return {"t": _deep(n.get("t") or {"ru": "", "en": ""}), "sub": _deep(n.get("sub")), "cats": list(n.get("cats") or ["architecture"]),
            "d": n.get("d") or _today(), "cover": cover, "bl": bl, "ppl": _deep(n.get("ppl") or [])}


def _clean_doc(x: dict, strict: bool) -> dict:
    t = _bi(x.get("t"), 400) or {"ru": "", "en": ""}
    d = _s(x.get("d") or _today(), 10)
    if not DATE_RE.match(d):
        raise AdminError("Дата — в виде 2026-10-10")
    cats = [c for c in (x.get("cats") or []) if c in CATS][:3] or ["architecture"]
    bl = []
    for b in (x.get("bl") or [])[:600]:
        if not isinstance(b, dict):
            continue
        typ = b.get("type")
        if typ == "img":
            ph = [r for r in (_ref(r) for r in (b.get("ph") or [])[:2]) if r]
            if not ph:
                continue
            nb = {"type": "img", "ph": ph, "sz": "col" if b.get("sz") == "col" and len(ph) == 1 else "wide"}
            cap = _bi(b.get("cap"), 600)
            if cap:
                nb["cap"] = cap
            bl.append(nb)
            continue
        if typ not in BLOCKS:
            typ = "p"
        nb = {"type": typ, "ru": _s(b.get("ru"), 12000), "en": _s(b.get("en"), 12000)}
        if typ == "h" and _s(b.get("kicker"), 120):
            nb["kicker"] = _s(b.get("kicker"), 120)
        if typ == "quote":
            by = _bi(b.get("by"), 300)
            if by:
                nb["by"] = by
        if strict and not (nb["ru"] or nb["en"]):
            continue                                  # пустые блоки на сайт не идут
        bl.append(nb)
    ppl = []
    for p in (x.get("ppl") or [])[:60]:
        if isinstance(p, dict) and SLUG_RE.match(str(p.get("id") or "")):
            ppl.append({"id": str(p["id"]), "ru": _s(p.get("ru"), 160), "en": _s(p.get("en"), 160)})
    doc = {"t": t, "sub": _bi(x.get("sub"), 400), "cats": cats, "d": d, "cover": _ref(x.get("cover"), cover=True),
           "bl": bl, "ppl": ppl}
    if strict:
        if not t["ru"]:
            raise AdminError("Нужен заголовок")
        if not doc["cover"]:
            raise AdminError("Нужна обложка — выбери фото в «Настройках» заметки")
        if not any(b["type"] == "p" and b["ru"] for b in bl):
            raise AdminError("В заметке нет ни одного абзаца")
    return doc


def _next_note_id(D: dict) -> int:
    return max([n["id"] for n in D["notes"]] + reserved_note_ids() + [1000]) + 1


async def doc_to_rec(nid: int, doc: dict, D: dict) -> tuple[dict, list[str], dict]:
    """Документ → заметка сайта. → (запись, файлы для выкладки, документ с обложкой)"""
    cover = doc["cover"]
    # у обложки должны быть свои img/c и img/t: они есть у фото редакции и у первой фотографии заметки сайта
    has_c = cover["i"] == 0 and (_asset(cover["k"]) is not None
                                 or any(str(n.get("ik") or n["id"]) == cover["k"] for n in D["notes"]))
    if not has_c:
        a = await materialize(cover["k"], cover["i"])
        cover = {"k": a["uid"], "i": 0, "w": a["w"], "h": a["h"], "cw": a["cw"], "ch": a["ch"]}
        doc = {**doc, "cover": cover}
    key = cover["k"]
    first_ru = next((b["ru"] for b in doc["bl"] if b["type"] == "p" and b["ru"]), "")
    first_en = next((b["en"] for b in doc["bl"] if b["type"] == "p" and b["en"]), "")
    bl = []
    for b in doc["bl"]:
        nb = _deep(b)
        if nb["type"] != "img" and not nb.get("en"):
            nb["en"] = nb.get("ru", "")
        bl.append(nb)
    hi = cover.get("m") is not False
    rec = {"id": nid, "ik": key, "d": doc["d"], "t": {"ru": doc["t"]["ru"], "en": doc["t"]["en"] or doc["t"]["ru"]},
           "sf": {"ru": md_plain(first_ru), "en": md_plain(first_en or first_ru)}, "bl": bl, "ppl": doc["ppl"],
           "slots": [], "cats": doc["cats"], "fmt": 2,
           "img": {"v": 2 if hi else 1, "W": cover["w"], "H": cover["h"], "segs": [[0, cover["w"], cover["h"], 0]],
                   "cw": cover["cw"], "ch": cover["ch"]}}
    if doc.get("sub"):
        rec["sub"] = {"ru": doc["sub"]["ru"], "en": doc["sub"]["en"] or doc["sub"]["ru"]}
    keys = {cover["k"]} | {r["k"] for b in doc["bl"] if b["type"] == "img" for r in b["ph"]}
    files = []
    for k in sorted(keys):
        a = _asset(k)
        if a and not a.get("up"):
            files += [f"img/f/{k}-0.jpg", f"img/m/{k}-0.jpg", f"img/c/{k}.jpg", f"img/t/{k}.jpg"]
    return rec, files, doc


def _doc_status(x: dict | None, live: dict | None) -> dict:
    if not x:
        return {"status": "live", "dirty": False}
    st = x.get("status") or "draft"
    if st == "live" and not live:
        st = "draft"
    return {"status": st, "dirty": st == "live" and x.get("pub") != x.get("doc"), "at": x.get("at")}


async def api_notes(request: web.Request) -> web.Response:
    D, _ = await effective()
    live = {n["id"]: n for n in D["notes"] if not n.get("tmp")}
    docs = {}
    for p in NOTES.glob("*.json") if NOTES.exists() else []:
        x = _read(p, None)
        if x and isinstance(x.get("id"), int):
            docs[x["id"]] = x
    out = []
    for nid in set(live) | set(docs):
        x, n = docs.get(nid), live.get(nid)
        doc = x["doc"] if x else note_to_doc(n)
        c = doc.get("cover")
        out.append({"id": nid, "t": doc.get("t"), "d": doc.get("d"), "cover": c, "updated": (x or {}).get("updated"),
                    **_doc_status(x, n)})
    out.sort(key=lambda r: (r.get("updated") or "", r.get("d") or "", r["id"]), reverse=True)
    return web.json_response({"items": out})


async def api_note_get(request: web.Request) -> web.Response:
    nid = int(request.query.get("id", "0"))
    D, _ = await effective()
    live = next((n for n in D["notes"] if n["id"] == nid), None)
    x = _doc(nid)
    if not x:
        if not live:
            raise AdminError("Такой заметки нет")
        # заметка сайта впервые в редакции: документ заводится сразу (у него появляется ссылка предпросмотра)
        x = {"id": nid, "status": "live", "doc": _clean_doc(note_to_doc(live), strict=False), "pub": None,
             "preview": secrets.token_urlsafe(18), "updated": _iso()}
        x["pub"] = _deep(x["doc"])
        _save_doc(x)
    return web.json_response({"note": x, **_doc_status(x, live), "live": bool(live)})


async def api_note_new(request: web.Request) -> web.Response:
    D, _ = await effective()
    nid = _next_note_id(D)
    doc = {"t": {"ru": "", "en": ""}, "sub": None, "cats": ["architecture"], "d": _today(), "cover": None,
           "bl": [{"type": "p", "ru": "", "en": ""}], "ppl": []}
    x = {"id": nid, "status": "draft", "doc": doc, "pub": None, "preview": secrets.token_urlsafe(18), "updated": _iso()}
    _save_doc(x)
    return _ok(note=x)


async def api_note_save(request: web.Request) -> web.Response:
    b = await _body(request)
    nid = int(b.get("id") or 0)
    D, _ = await effective()
    live = next((n for n in D["notes"] if n["id"] == nid), None)
    x = _doc(nid)
    if not x:
        if not live:
            raise AdminError("Такой заметки нет")
        x = {"id": nid, "status": "live", "doc": _clean_doc(note_to_doc(live), strict=False),
             "preview": secrets.token_urlsafe(18)}
        x["pub"] = _deep(x["doc"])
    doc = _clean_doc(b.get("doc") or {}, strict=False)
    lst = _read(_hist_path("note", nid), [])
    if x.get("doc") and x["doc"] != doc and (not lst or _age(lst[-1].get("at")) > 900):
        hist_add("note", nid, {"doc": _deep(x["doc"])}, "черновик")      # версия черновика — не чаще раза в 15 минут
    x["doc"] = doc
    x["updated"] = _iso()
    _save_doc(x)
    return _ok(updated=x["updated"], **_doc_status(x, live))


def _age(iso: str | None) -> float:
    try:
        return (_now() - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return 1e9


async def publish_doc(nid: int) -> dict:
    x = _doc(nid)
    if not x:
        raise AdminError("Такой заметки нет")
    D, _ = await effective()
    doc = _clean_doc(x["doc"], strict=True)
    rec, files, doc = await doc_to_rec(nid, doc, D)
    prev = next((n for n in D["notes"] if n["id"] == nid), None)
    if prev:
        hist_add("note", nid, {"doc": note_to_doc(prev)}, "до публикации")
    await enqueue({"t": "note", "id": nid, "rec": rec, "files": files})
    cur = _doc(nid) or x                        # пока публиковали, черновик могли поправить — правки не теряем
    if cur.get("doc") == x.get("doc"):
        cur["doc"] = doc
    else:
        cur["doc"]["cover"] = doc["cover"]
    cur.update(pub=_deep(doc), status="live", at=None, live_at=_iso(), updated=_iso())
    _save_doc(cur)
    return cur


async def api_note_publish(request: web.Request) -> web.Response:
    b = await _body(request)
    nid = int(b.get("id") or 0)
    at = _s(b.get("at"), 40)
    if at:
        try:
            when = datetime.fromisoformat(at)
            when = when if when.tzinfo else when.replace(tzinfo=TZ)
        except ValueError:
            raise AdminError("Не понял время публикации") from None
        if when <= _now() + timedelta(minutes=1):
            raise AdminError("Время публикации уже прошло")
        x = _doc(nid)
        if not x:
            raise AdminError("Такой заметки нет")
        _clean_doc(x["doc"], strict=True)              # проверка сейчас, а не в момент выхода
        x.update(status="scheduled", at=when.isoformat(timespec="minutes"), announce=bool(b.get("announce")))
        _save_doc(x)
        return _ok(status="scheduled", at=x["at"])
    x = await publish_doc(nid)
    if b.get("announce"):
        try:
            _spawn_bot(f"n{nid}", _note_to_bot(nid))
        except AdminError:
            pass                                # анонс уже собирается
    return _ok(status="live", doc=x["doc"], dirty=x["doc"] != x["pub"])


async def api_note_unpublish(request: web.Request) -> web.Response:
    b = await _body(request)
    nid = int(b.get("id") or 0)
    D, _ = await effective()
    prev = next((n for n in D["notes"] if n["id"] == nid), None)
    x = _doc(nid)
    if x and x.get("status") == "scheduled":
        x.update(status="draft", at=None)
        _save_doc(x)
        return _ok(status="draft")
    if not prev:
        raise AdminError("Заметки и так нет на сайте")
    if not x:
        x = {"id": nid, "doc": _clean_doc(note_to_doc(prev), strict=False), "preview": secrets.token_urlsafe(18)}
    hist_add("note", nid, {"doc": note_to_doc(prev)}, "снята с сайта")
    x.update(status="draft", at=None, pub=None, updated=_iso())
    _save_doc(x)
    await enqueue({"t": "unnote", "id": nid})
    return _ok(status="draft")


async def api_note_delete(request: web.Request) -> web.Response:
    b = await _body(request)
    nid = int(b.get("id") or 0)
    D, _ = await effective()
    if any(n["id"] == nid for n in D["notes"]):
        raise AdminError("Заметка на сайте — сначала сними её с сайта")
    p = _doc_path(nid)
    if p.exists():
        p.unlink()
    return _ok()


async def scheduled_tick(bot: Bot) -> None:
    """Раз в минуту: заметки, чьё время пришло, — на сайт (и анонс во входящие, если просили)."""
    for p in NOTES.glob("*.json") if NOTES.exists() else []:
        x = _read(p, None)
        if not x or x.get("status") != "scheduled" or not x.get("at"):
            continue
        try:
            when = datetime.fromisoformat(x["at"])
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=TZ)
        if when > _now():
            continue
        title = html.escape(x["doc"]["t"]["ru"] or str(x["id"]))
        try:
            await publish_doc(x["id"])
        except Exception as exc:
            log.warning("Редакция: заметка %s по расписанию не вышла: %r", x["id"], exc)
            cur = _doc(x["id"]) or x
            cur.update(status="draft", at=None)
            _save_doc(cur)
            try:
                await bot.send_message(config.ADMIN_ID, f"⚠️ Заметка «{title}» по расписанию не вышла: "
                                       f"{html.escape(str(exc)[:300])}. Она осталась черновиком.")
            except Exception:
                pass
            continue
        if x.get("announce"):
            try:
                _spawn_bot(f"n{x['id']}", _note_to_bot(x["id"]))
            except Exception:
                log.warning("Редакция: анонс заметки %s не собрался", x["id"], exc_info=True)
        try:
            await bot.send_message(config.ADMIN_ID, f"🛠 По расписанию: заметка «{title}» уходит на сайт.")
        except Exception:
            pass


# ======================= предпросмотр =======================

def _doc_by_token(tok: str) -> dict | None:
    if not re.match(r"^[A-Za-z0-9_-]{16,40}$", tok or ""):
        return None
    for p in NOTES.glob("*.json") if NOTES.exists() else []:
        x = _read(p, None)
        if x and secrets.compare_digest(str(x.get("preview") or ""), tok):
            return x
    return None


async def preview_page(request: web.Request) -> web.StreamResponse:
    x = _doc_by_token(request.match_info["tok"])
    if not x:
        raise web.HTTPNotFound()
    return _static_file(WEB / "preview.html", html_page=True, extra={"X-Robots-Tag": "noindex"})


async def preview_data(request: web.Request) -> web.Response:
    x = _doc_by_token(request.match_info["tok"])
    if not x:
        raise web.HTTPNotFound()
    D, _ = await effective()
    doc = _clean_doc(x["doc"], strict=False)
    if not doc["cover"]:
        raise web.HTTPNotFound(text="no cover")
    cover = doc["cover"]
    first_ru = next((b["ru"] for b in doc["bl"] if b["type"] == "p" and b["ru"]), "")
    rec = {"id": x["id"], "ik": cover["k"], "d": doc["d"], "t": {"ru": doc["t"]["ru"] or "Без заголовка", "en": doc["t"]["en"]},
           "sf": {"ru": md_plain(first_ru), "en": ""}, "bl": doc["bl"], "ppl": doc["ppl"], "slots": [], "cats": doc["cats"],
           "fmt": 2, "img": {"v": 2 if cover.get("m") is not False else 1, "W": cover["w"], "H": cover["h"],
                             "segs": [[0, cover["w"], cover["h"], 0]], "cw": cover["cw"], "ch": cover["ch"]}}
    if doc.get("sub"):
        rec["sub"] = doc["sub"]
    idx = _find(D["notes"], x["id"])
    if idx is None:
        D["notes"].insert(0, rec)
    else:
        D["notes"][idx] = rec
    return web.json_response({"D": D, "id": x["id"]}, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


# ======================= страницы, главная, указатель =======================

def _clean_pages(x: dict) -> dict:
    out = {}
    for page, fields in (("about", ("title", "lead", "p", "contactH", "contact")),
                         ("partners", ("title", "lead", "fmtH", "fmt", "howH", "how", "audH", "aud", "contactH", "contact"))):
        pg = x.get(page) if isinstance(x.get(page), dict) else {}
        out[page] = {}
        for L in ("ru", "en"):
            src = pg.get(L) if isinstance(pg.get(L), dict) else {}
            o = {}
            for f in fields:
                v = src.get(f)
                if f == "p":
                    o[f] = [_s(s, 3000) for s in (v or [])[:20] if _s(s)]
                elif f == "fmt":
                    o[f] = [[_s(a[0], 200), _s(a[1], 1000)] for a in (v or [])[:20]
                            if isinstance(a, (list, tuple)) and len(a) >= 2 and _s(a[0])]
                elif f == "aud":
                    o[f] = _s(v, 2000) or None
                else:
                    o[f] = _s(v, 2000)
            out[page][L] = o
    return out


async def api_pages(request: web.Request) -> web.Response:
    b = await _body(request)
    pages = _clean_pages(b.get("pages") or {})
    D, _ = await effective()
    if D.get("pages"):
        hist_add("pages", "all", {"pages": _deep(D["pages"])}, "до правки")
    await enqueue({"t": "pages", "pages": pages})
    return _ok(pages=pages)


async def api_home(request: web.Request) -> web.Response:
    b = await _body(request)
    pin = b.get("pin")
    D, _ = await effective()
    if pin:
        pin = int(pin)
        idx = _find(D["objects"], pin)
        if idx is None or D["objects"][idx].get("tmp"):
            raise AdminError("Такой записи на сайте нет")
    await enqueue({"t": "home", "pin": pin or None})
    return _ok()


async def api_person(request: web.Request) -> web.Response:
    b = await _body(request)
    pid = _s(b.get("id"), 80)
    D, _ = await effective()
    if pid not in D["people"]:
        raise AdminError("Такого имени в указателе нет")
    ru, en = _s(b.get("ru"), 160), _s(b.get("en"), 160)
    if not ru or not en:
        raise AdminError("Нужны оба написания, RU и EN")
    roles = [r for r in (b.get("roles") or []) if r in ROLES] or D["people"][pid].get("roles") or ["architect"]
    await enqueue({"t": "person", "id": pid, "rec": {"ru": ru, "en": en, "roles": roles, "life": _s(b.get("life"), 60)}})
    return _ok()


async def api_country(request: web.Request) -> web.Response:
    b = await _body(request)
    cid = _s(b.get("id"), 80)
    D, _ = await effective()
    if cid not in D["countries"]:
        raise AdminError("Такой страны нет")
    ru, en = _s(b.get("ru"), 80), _s(b.get("en"), 80)
    if not ru or not en:
        raise AdminError("Нужны оба названия, RU и EN")
    await enqueue({"t": "country", "id": cid, "rec": {"ru": ru, "en": en}})
    return _ok()


# ======================= перевод =======================

TR_SYSTEM = """You translate Russian texts of AH Magazine (theahmag.com: a visual archive of architecture, interiors, art, photography, cinema and archival finds) into English for the English version of the site. You get a JSON list of Russian strings; return {"en": [...]} with exactly the same number of strings in the same order, each the translation of the string at that position. An empty string stays empty.

Keep the inline markup exactly as it is, translating only the words inside: **bold**, *italic*, [link text](url) — the url is never changed. Backslash escapes (\\*, \\[, \\], \\\\) stay as they are. Line breaks stay.

Write plain, concrete, conversational English, as if the same author wrote it; keep every fact, name, date and number; add nothing, explain nothing. Names keep their original Latin spelling; Russian names are transliterated the standard way; titles of works in their established English form. Never use: stunning, breathtaking, masterpiece, testament to, seamlessly, harmonious, "a dialogue between", "not X but Y", exclamation marks."""


async def translate(items: list[str], ctx: str) -> list[str]:
    items = [str(x or "")[:12000] for x in items][:400]
    out: list[str] = []
    chunk: list[str] = []
    size = 0

    async def run(part: list[str]) -> list[str]:
        if not any(p.strip() for p in part):
            return [""] * len(part)
        content = f"# What this is\n{ctx}\n\n# Russian strings ({len(part)})\n" + json.dumps(part, ensure_ascii=False)
        res = await curator._call(content, system=TR_SYSTEM, model=config.CLAUDE_MODEL,
                                  max_tokens=min(16000, 400 + sum(len(p) for p in part) * 2))
        en = res.get("en") if isinstance(res, dict) else None
        if not isinstance(en, list) or len(en) != len(part):
            raise AdminError("Перевод разошёлся по абзацам — попробуй ещё раз")
        return [str(e or "") for e in en]

    for s in items:
        if chunk and size + len(s) > 9000:
            out += await run(chunk)
            chunk, size = [], 0
        chunk.append(s)
        size += len(s)
    if chunk:
        out += await run(chunk)
    return out


async def api_translate(request: web.Request) -> web.Response:
    b = await _body(request)
    items = b.get("items")
    if not isinstance(items, list) or not items:
        raise AdminError("Переводить нечего")
    ctx = {"note": "An essay from the Notes column (#ahmagnotes).",
           "obj": "An archive entry: title, place, year, text paragraphs, lead sentence.",
           "pages": "Texts of the site's About and Partnerships pages."}.get(str(b.get("ctx")), "Texts for the site.")
    return _ok(items=await translate(items, ctx))


# ======================= в бот =======================

_busy: set[str] = set()


def _spawn_bot(key: str, coro) -> None:
    if key in _busy:
        coro.close()
        raise AdminError("Уже собирается — бот пришлёт сообщение")
    _busy.add(key)

    async def run():
        try:
            await coro
        finally:
            _busy.discard(key)
    task = asyncio.create_task(run())
    sitepub._tasks.add(task)
    task.add_done_callback(sitepub._tasks.discard)


def _unstamped(f: bytes, dest: Path) -> Path:
    """Фото с сайта (со знаком AHMAG) для бота: снизу срезается полоса со знаком — бот поставит свой."""
    im = Image.open(io.BytesIO(f)).convert("RGB")
    s = im.width / brand.BASE_W
    cut = round((brand.MARGIN_BOTTOM + brand.LOGO_H) * s) + 3
    if brand.enabled() and im.height - cut > im.height * 0.6:
        im = im.crop((0, 0, im.width, im.height - cut))
    im.save(dest, "JPEG", quality=94)
    return dest


async def _bot_photos(rec: dict, folder: Path) -> list[Path]:
    """Фото записи для поста: оригиналы без знака, где они есть (загрузки редакции, фото поста бота), иначе — с сайта."""
    from app import cards
    folder.mkdir(parents=True, exist_ok=True)
    key = str(rec.get("ik") or rec["id"])
    img = rec.get("img") or {}
    segs = img.get("segs") or []
    omap = _read(ORIGMAP, {})
    post_imgs, plan = [], []
    if not str(rec.get("ik") or "").startswith(f"{rec['id']}e") and rec["id"] < 100_000:
        async with db.connect() as c:
            cur = await c.execute("SELECT * FROM posts WHERE channel_msg_id=? AND status='published' ORDER BY id DESC LIMIT 1",
                                  (rec["id"],))
            post = await cur.fetchone()
        if post:
            post_imgs, plan = json.loads(post["images"] or "[]"), cards.photo_plan(post)
    out = []
    async with _client() as client:
        for j, g in enumerate(segs[:12]):
            dest = folder / f"own{j:02d}.jpg"
            orig = omap.get(f"img/f/{key}-{j}.jpg")
            if orig and Path(orig).exists():
                await asyncio.to_thread(shutil.copyfile, orig, dest)
            elif j < len(plan) and plan[j] < len(post_imgs) and Path(post_imgs[plan[j]]).exists():
                await asyncio.to_thread(shutil.copyfile, post_imgs[plan[j]], dest)
            else:
                if img.get("v", 0) >= 2:
                    f = await _site_bytes(client, f"img/f/{key}-{j}.jpg")
                else:
                    strip = await _site_bytes(client, f"img/p/{key}.jpg", missing_ok=True)
                    f = await asyncio.to_thread(_strip_cut, strip, g) if strip else (
                        await _site_bytes(client, f"img/c/{key}.jpg") if j == 0 else None)
                if not f:
                    continue
                await asyncio.to_thread(_unstamped, f, dest)
            out.append(dest)
    return out


async def _obj_to_bot(rid: int, fmt: str) -> None:
    from app import pipeline, screen, slots
    bot = ui.BOT
    D, H = await effective()
    rec, _ = _obj_of(D, H, rid)
    title = md_plain((rec or {}).get("t", {}).get("ru", "")) or str(rid)
    try:
        if not rec:
            raise AdminError("записи нет")
        url = f"{sitepub.SITE_URL}/o/{rid}/"
        old = await db.get_candidate_by_url(url)
        if old:
            post = await db.post_by_candidate(old["id"])
            if post and post["status"] in ("ready", "sent", "announced", "approved"):
                await screen.notify(bot, f"🛠 Пост по записи «{html.escape(title)}» уже в боте.", [("📥 Разобрать", "n:inbox")])
                return
            url += f"#r{int(time.time())}"
        await db.add_candidate(url, "site", title, {"site_rec": rid})
        cand = await db.get_candidate_by_url(url)
        cid = cand["id"]
        folder = config.IMG_DIR / f"c{cid}"
        shutil.rmtree(folder, ignore_errors=True)
        images = await _bot_photos(rec, folder)
        if not images:
            raise AdminError("фото записи не скачались")
        credits = []
        for c in rec.get("cr") or []:
            role = {"project": "Проект", "photo": "Фото", "source": "Источник", "courtesy": "Предоставлено"}.get(c[0], "Также")
            credits.append(f"{role}: {c[1]}" + (f" ({c[2]})" if len(c) > 2 and c[2] else ""))
        people = ", ".join(D["people"].get(p["id"], {}).get("ru", "") for p in rec.get("p") or [])
        text = "\n\n".join(x for x in [
            title, people, md_plain((rec.get("pl") or {}).get("ru")), (rec.get("y") or {}).get("ru", ""),
            *[md_plain(p) for p in (rec.get("b") or {}).get("ru") or []], "\n".join(credits)] if x)
        prep = {"title": title, "text": text[:6000], "images": [str(p) for p in images], "allow_std": True,
                "sheet": await pipeline.make_sheet(images, folder)}
        await db.update_candidate(cid, source="site", title=title, status="request", note="из редакции сайта", prep=prep)
        cand = await db.get_candidate(cid)
        data = await curator.evaluate(pipeline._params(await curator.eval_context(), cand, prep, forced=True),
                                      background=False)
        data["_site_rec"] = rid
        data["flags"] = list(data.get("flags") or []) + ["из редакции сайта: фото и факты — из записи архива"]
        pid = await pipeline.finish(cid, data, forced=True)
        if not pid:
            raise AdminError("не получилось собрать пост")
        post = await db.get_post(pid)
        if post["format"] != fmt:
            await pipeline.set_format(pid, fmt, write=False)
        if fmt == "std":
            await pipeline.ensure_text(pid)
        await slots.propose(pid, None)
        what = "пост для канала" if fmt == "std" else "фото-пост для Instagram"
        await screen.notify(bot, f"🛠 Из редакции сайта: {what} «{html.escape(title)}» во входящих.",
                            [("📥 Разобрать", "n:inbox")])
    except Exception as exc:
        log.exception("Редакция: запись %s в бот", rid)
        if bot:
            await bot.send_message(config.ADMIN_ID, f"⚠️ Пост по записи «{html.escape(title)}» не собрался: "
                                   f"{html.escape(str(exc) if isinstance(exc, AdminError) else curator.explain(exc))[:300]}")


async def _note_to_bot(nid: int) -> None:
    """Опубликованная заметка → анонс #ahmagnotes во входящих: в канал он уйдёт как заметки бота
    (со ссылкой на сайт или целиком, смотря по «🔗 Ссылки» в /site)."""
    from app import formatter, screen, slots
    bot = ui.BOT
    x = _doc(nid)
    doc = (x or {}).get("pub") or (x or {}).get("doc")
    title = (doc or {}).get("t", {}).get("ru") or str(nid)
    try:
        if not doc:
            raise AdminError("заметки нет")
        url, url_en = f"{sitepub.SITE_URL}/n/{nid}/", f"{sitepub.SITE_URL}/en/n/{nid}/"
        paras = []
        for b in doc["bl"]:
            if b["type"] in ("p", "h", "example") and b.get("ru"):
                paras.append(md_plain(b["ru"]))
            elif b["type"] == "quote" and b.get("ru"):
                paras.append(f"«{md_plain(b['ru']).strip('«»')}»")
        refs, seen = [], set()
        for r in [doc["cover"]] + [r for b in doc["bl"] if b["type"] == "img" for r in b["ph"]]:
            if r and (r["k"], r["i"]) not in seen:
                seen.add((r["k"], r["i"]))
                refs.append(r)
        folder = config.IMG_DIR / f"site-n{nid}-{secrets.token_hex(2)}"
        folder.mkdir(parents=True, exist_ok=True)
        images = []
        async with _client() as client:
            for j, r in enumerate(refs[: config.MAX_PHOTOS]):
                a = _asset(r["k"])
                dest = folder / f"{j:02d}.jpg"
                orig = (str(ORIG / a["orig"]) if a and a.get("orig") else (a or {}).get("orig_abs")) if a else None
                if orig and Path(orig).exists():
                    await asyncio.to_thread(shutil.copyfile, orig, dest)
                else:
                    f = await _site_bytes(client, f"img/f/{r['k']}-{r['i']}.jpg", missing_ok=True)
                    if not f:
                        continue
                    await asyncio.to_thread(_unstamped, f, dest)
                images.append(str(dest))
        parts = [doc["t"]["ru"]] + ([doc["sub"]["ru"]] if doc.get("sub") else [])
        data = {"headline_parts": parts, "body": "\n\n".join(paras), "tags": ["ahmagnotes"],
                "flags": ["анонс заметки из редакции сайта"], "_site_url": url, "_site_url_en": url_en,
                "_source_text": "", "photo_order": list(range(len(images)))}
        caption = formatter.build_caption(data, "notes")
        pid = await db.add_post(candidate_id=None, source="notes", url=url, category="notes", format="notes",
                                data=data, caption=caption, score=0, reason="заметка из редакции сайта",
                                images=images, status="ready")
        await slots.propose(pid, None)
        await screen.notify(bot, f"🛠 Анонс заметки «{html.escape(title)}» во входящих.", [("📥 Разобрать", "n:inbox")])
    except Exception as exc:
        log.exception("Редакция: заметка %s в бот", nid)
        if bot:
            await bot.send_message(config.ADMIN_ID, f"⚠️ Анонс заметки «{html.escape(title)}» не собрался: "
                                   f"{html.escape(str(exc) if isinstance(exc, AdminError) else curator.explain(exc))[:300]}")


async def api_obj_tobot(request: web.Request) -> web.Response:
    b = await _body(request)
    rid = int(b.get("id") or 0)
    fmt = "mini" if b.get("fmt") == "mini" else "std"
    D, H = await effective()
    rec, hidden = _obj_of(D, H, rid)
    if not rec or hidden:
        raise AdminError("Запись должна быть на сайте")
    if any(c["t"] == "obj" and c.get("id") == rid for c in queue()):
        raise AdminError("Правка записи ещё выкладывается — отправь через минуту")
    _spawn_bot(f"o{rid}", _obj_to_bot(rid, fmt))
    return _ok()


async def api_note_tobot(request: web.Request) -> web.Response:
    b = await _body(request)
    nid = int(b.get("id") or 0)
    x = _doc(nid)
    if not x or x.get("status") != "live":
        raise AdminError("Анонс — только у опубликованной заметки")
    _spawn_bot(f"n{nid}", _note_to_bot(nid))
    return _ok()


# ======================= копия базы =======================

async def backup(bot: Bot, manual: bool = False) -> None:
    """Копия в Telegram: данные сайта, скрытые записи, заметки и очередь редакции, база бота (если влезает)."""
    def build() -> tuple[Path, bool]:
        tmp = Path(tempfile.mkdtemp(prefix="ahmag-backup-", dir=str(config.DATA_DIR)))
        zpath = tmp / f"ahmag-backup-{_now():%Y-%m-%d}.zip"
        dbcopy = tmp / "ahmag.db"
        src = sqlite3.connect(str(config.DB_PATH))
        dst = sqlite3.connect(str(dbcopy))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        def pack(with_db: bool) -> None:
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p, arc in ((sitepub.DATA, "site/data.json"), (sitepub.ROOT / "hidden.json", "site/hidden.json"),
                               (QUEUE, "admin/queue.json")):
                    if p.exists():
                        z.write(p, arc)
                for folder, arc in ((NOTES, "admin/notes"), (HIST, "admin/history")):
                    for p in sorted(folder.glob("*.json")) if folder.exists() else []:
                        z.write(p, f"{arc}/{p.name}")
                if with_db:
                    z.write(dbcopy, "bot/ahmag.db")

        pack(True)
        with_db = zpath.stat().st_size < 48 * 1024 * 1024
        if not with_db:
            pack(False)
        return zpath, with_db

    zpath, with_db = await asyncio.to_thread(build)
    try:
        await bot.send_document(config.ADMIN_ID, FSInputFile(zpath), caption=(
            "💾 Копия базы" + (" (по кнопке)" if manual else " — раз в неделю") + ": данные сайта, скрытые записи, "
            "заметки редакции" + (", база бота." if with_db else ". База бота больше 48 МБ — в копию не влезла.")))
    finally:
        shutil.rmtree(zpath.parent, ignore_errors=True)


# ======================= бот: вход, /admin =======================

def _admin_kb() -> InlineKeyboardMarkup:
    rows = []
    url = admin_url()
    if url and url.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🛠 Открыть редакцию", url=url)])
    rows.append([InlineKeyboardButton(text="🚪 Выйти везде", callback_data="adm:out"),
                 InlineKeyboardButton(text="💾 Копия базы", callback_data="adm:bak")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_text() -> str:
    url = admin_url()
    s = _sessions()
    st = status()
    lines = ["🛠 <b>Редакция сайта</b>", html.escape(url) if url else "Адрес не задан: переменная ADMIN_URL в Railway"]
    lines.append(f"Входов активно: {len(s)}" if s else "Сейчас никто не вошёл")
    if st["n"]:
        lines.append(f"Правок ждут выкладки: {st['n']}" + (f" · ⚠️ {html.escape(st['error'] or '')}" if st["state"] == "error" else ""))
    return "\n".join(lines)


@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    await msg.answer(admin_text(), reply_markup=_admin_kb(), disable_web_page_preview=True)


@router.callback_query(F.data.startswith("adm:"))
async def on_admin(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")
    act = parts[1] if len(parts) > 1 else ""
    if act in ("ok", "no"):
        x = _logins.get(parts[2] if len(parts) > 2 else "")
        if not x or time.time() - x["t"] > LOGIN_TTL or x["state"] != "pending":
            await cb.answer("Запрос устарел")
            try:
                await cb.message.edit_text("Запрос на вход устарел.")
            except Exception:
                pass
            return
        x["state"] = "ok" if act == "ok" else "denied"
        await cb.answer("Готово")
        try:
            await cb.message.edit_text("✅ Вход подтверждён: браузер помнит тебя 30 дней." if act == "ok"
                                       else "✕ Вход отклонён.")
        except Exception:
            pass
        return
    if act == "out":
        _write(SESSIONS, {})
        await cb.answer("Все входы закрыты")
        try:
            await cb.message.edit_text(admin_text(), reply_markup=_admin_kb(), disable_web_page_preview=True)
        except Exception:
            pass
        return
    if act == "bak":
        await cb.answer("Собираю копию…")
        try:
            await backup(bot, manual=True)
        except Exception as exc:
            log.exception("Редакция: копия базы")
            await bot.send_message(config.ADMIN_ID, f"⚠️ Копия не собралась: {html.escape(str(exc)[:300])}")
        return
    await cb.answer()


def schedule(sched, bot: Bot, guarded) -> None:
    sched.add_job(guarded(bot, "редакция: заметки по расписанию", scheduled_tick, bot), "interval", minutes=1,
                  id="admin_sched", max_instances=1)
    sched.add_job(guarded(bot, "копия базы", backup, bot), "cron", day_of_week="sun", hour=5, minute=20,
                  id="admin_backup", max_instances=1)


# ======================= веб: страницы и файлы =======================

CSP = ("default-src 'self'; img-src 'self' data: blob: https:; style-src 'self' 'unsafe-inline'; font-src 'self'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
_ver: dict[str, str] = {}


def _code_ver() -> str:
    if "v" not in _ver:
        h = hashlib.sha1()
        for p in sorted(WEB.glob("*")) + [SITE_WEB / "app.js", SITE_WEB / "app.css"]:
            if p.is_file():
                h.update(p.read_bytes())
        _ver["v"] = h.hexdigest()[:10]
    return _ver["v"]


def _static_file(p: Path, html_page: bool = False, extra: dict | None = None) -> web.StreamResponse:
    if not p.is_file():
        raise web.HTTPNotFound()
    headers = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", **(extra or {})}
    if html_page:
        body = p.read_text("utf-8").replace("{{V}}", _code_ver())
        headers.update({"Content-Security-Policy": CSP, "Cache-Control": "no-store", "X-Frame-Options": "DENY"})
        return web.Response(text=body, content_type="text/html", charset="utf-8", headers=headers)
    headers["Cache-Control"] = "public, max-age=31536000, immutable" if p.suffix == ".woff2" else "no-cache"
    return web.FileResponse(p, headers=headers)


async def page_index(request: web.Request) -> web.StreamResponse:
    return _static_file(WEB / "index.html", html_page=True, extra={"X-Robots-Tag": "noindex, nofollow"})


async def page_redirect(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/admin/")


async def static_admin(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if not re.match(r"^[a-z0-9_-]+\.(?:js|css|svg|png)$", name):
        raise web.HTTPNotFound()
    return _static_file(WEB / name)


async def static_site(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if name not in ("app.js", "app.css", "fonts.css"):
        raise web.HTTPNotFound()
    return _static_file(SITE_WEB / name)


async def static_font(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if not re.match(r"^[a-z0-9-]+\.woff2$", name):
        raise web.HTTPNotFound()
    return _static_file(SITE_WEB / "fonts" / name)


async def img(request: web.Request) -> web.StreamResponse:
    """Фото для редакции и предпросмотра: свои файлы (ещё не на сайте) — отсюда, остальное — с сайта."""
    tail = request.match_info["tail"]
    if not IMG_RE.match(tail):
        raise web.HTTPNotFound()
    p = FILES / "img" / tail
    if p.is_file():
        return web.FileResponse(p, headers={"Cache-Control": "private, max-age=86400", "Content-Type": "image/jpeg"})
    raise web.HTTPFound(f"{sitepub.SITE_URL}/img/{tail}")


def setup(app: web.Application) -> None:
    """Пути редакции на веб-сервере бота."""
    r = app.router
    r.add_get("/admin", page_redirect)
    r.add_get("/admin/", page_index)
    r.add_get("/admin/static/{name}", static_admin)
    r.add_get("/admin/site/{name}", static_site)
    r.add_get("/admin/fonts/{name}", static_font)
    r.add_get("/assets/fonts/{name}", static_font)
    r.add_get("/img/{tail:.+}", img)
    r.add_get("/admin/preview/{tok}", preview_page)
    r.add_get("/admin/preview/{tok}/data", preview_data)
    r.add_post("/admin/api/login/start", api_login_start)
    r.add_post("/admin/api/login/poll", api_login_poll)
    r.add_post("/admin/api/logout", api_logout)
    for method, path, fn in (
            ("GET", "site", api_site), ("GET", "status", api_status),
            ("POST", "obj/save", api_obj_save), ("POST", "obj/hide", api_obj_hide), ("POST", "obj/tobot", api_obj_tobot),
            ("POST", "upload", api_upload), ("POST", "asset/copy", api_asset_copy),
            ("POST", "translate", api_translate),
            ("GET", "history", api_history), ("GET", "history/get", api_history_get),
            ("POST", "history/restore", api_history_restore),
            ("GET", "notes", api_notes), ("GET", "note", api_note_get), ("POST", "note/new", api_note_new),
            ("POST", "note/save", api_note_save), ("POST", "note/publish", api_note_publish),
            ("POST", "note/unpublish", api_note_unpublish), ("POST", "note/delete", api_note_delete),
            ("POST", "note/tobot", api_note_tobot),
            ("POST", "pages", api_pages), ("POST", "home", api_home),
            ("POST", "person", api_person), ("POST", "country", api_country)):
        r.add_route(method, f"/admin/api/{path}", auth(fn))
