"""NUMBUS Branding — Telegram-бот.

Один бот, много брендов. Клиент активирует инвайт-код, загружает логотип и
собирает собственный стиль в редакторе (Mini App, web.py + webapp.html).
Посты делаются в чате: шаблон → фото → текст → формат → хештег → готово.
"""
import io
import os
import re
import html
import time
import signal
import asyncio
import logging
import warnings
from datetime import datetime

from aiohttp import web as aioweb
from telegram import (Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB,
                      InputMediaPhoto, WebAppInfo)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, BaseUpdateProcessor, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)
from telegram.warnings import PTBUserWarning
from PIL import Image

import db
import render as R
import web
from texts import t as _t

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logger = logging.getLogger("numbus")
warnings.filterwarnings("ignore", category=PTBUserWarning)

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.environ.get("ADMIN_IDS", "")) if x.strip().isdigit()}
SUPPORT = os.environ.get("SUPPORT_CONTACT", "администратору")
PORT = int(os.environ.get("PORT", "8080"))
_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
WEBAPP_URL = (os.environ.get("WEBAPP_URL") or (f"https://{_domain}" if _domain else "")).rstrip("/")
MAX_BATCH = 30
RENDER_SEM = asyncio.Semaphore(int(os.environ.get("RENDER_WORKERS", "2")))
HTML = ParseMode.HTML
esc = html.escape

MENU, CODE, K_NAME, K_LOGO = range(4)

FORMATS = ["4:5", "3:4", "1:1", "3:2", "9:16", "orig"]
CODE_RE = r"(?i)^\s*NB-[A-Z0-9]{4}-[A-Z0-9]{4}\s*$"


# ============ Параллельно между юзерами, по очереди внутри юзера ============
class PerUserProcessor(BaseUpdateProcessor):
    def __init__(self, max_concurrent_updates=64):
        super().__init__(max_concurrent_updates)
        self._locks = {}

    async def do_process_update(self, update, coroutine):
        uid = update.effective_user.id if isinstance(update, Update) and update.effective_user else None
        if uid is None:
            await coroutine
            return
        async with self._locks.setdefault(uid, asyncio.Lock()):
            await coroutine

    async def initialize(self):
        pass

    async def shutdown(self):
        pass


# ============ Утилиты ============
def L(ctx, update) -> str:
    lang = ctx.user_data.get("lang")
    if not lang:
        u = update.effective_user
        lang = db.ensure_user(u.id, u.language_code).get("lang") or "ru"
        ctx.user_data["lang"] = lang
    return lang


def tx(ctx, key, **kw):
    return _t(ctx.user_data.get("lang", "ru"), key, **kw)


def reset_session(ctx):
    for k in ("wiz", "kit_bid", "await"):
        ctx.user_data.pop(k, None)


def fmt_date(iso) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except Exception:
        return "—"


def plan_limits(b):
    return db.PLANS.get(b["plan"], db.PLANS["pilot"])


def current_brand(uid):
    brands = db.user_brands(uid)
    if not brands:
        return None, brands
    u = db.get_user(uid) or {}
    b = next((x for x in brands if x["id"] == u.get("active_brand")), None) or brands[0]
    if b["id"] != u.get("active_brand"):
        db.set_active_brand(uid, b["id"])
    return b, brands


def editor_btn(ctx, bid):
    if not WEBAPP_URL:
        return None
    return Btn(tx(ctx, "b_editor"), web_app=WebAppInfo(url=f"{WEBAPP_URL}/?b={bid}"))


def safe_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())[:30].strip("_")
    return s or "numbus"


async def run(fn, *a):
    async with RENDER_SEM:
        return await asyncio.to_thread(fn, *a)


async def answer(update):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except TelegramError:
            pass


async def say(update, text, kb=None):
    return await update.effective_chat.send_message(text, parse_mode=HTML, reply_markup=kb,
                                                    disable_web_page_preview=True)


async def strip_kb(update):
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass


async def edit_or_say(update, text, kb=None):
    q = update.callback_query
    if q and q.message and not q.message.photo:
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
            return
        except BadRequest:
            pass
    await strip_kb(update)
    await say(update, text, kb)


async def put_photo(update, data: bytes, caption, kb):
    q = update.callback_query
    if q and q.message and q.message.photo:
        try:
            await q.edit_message_media(InputMediaPhoto(io.BytesIO(data), caption=caption, parse_mode=HTML),
                                       reply_markup=kb)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            logger.warning("edit_message_media: %s", e)
    await strip_kb(update)
    await update.effective_chat.send_photo(io.BytesIO(data), caption=caption, parse_mode=HTML, reply_markup=kb)


async def get_file_bytes(update, ctx):
    msg = update.message
    try:
        if msg.document:
            f = await msg.document.get_file()
            return bytes(await f.download_as_bytearray()), False
        if msg.photo:
            f = await msg.photo[-1].get_file()
            return bytes(await f.download_as_bytearray()), True
    except BadRequest as e:
        if "too big" in str(e).lower():
            await say(update, tx(ctx, "file_big"))
            return None, False
        raise
    return None, False


def is_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data))
        return True
    except Exception:
        return False


# ============ Главное меню ============
def menu_text(ctx, b):
    lines = [tx(ctx, "menu_head", brand=esc(b["kit"].get("name") or "—"))]
    until = fmt_date(b["plan_until"])
    if db.plan_active(b):
        lines.append(tx(ctx, "menu_plan", plan=b["plan"].capitalize(), until=until,
                        used=db.photos_used(b["id"]), limit=plan_limits(b)["photos"]))
    else:
        lines.append(tx(ctx, "menu_expired", until=until, support=esc(SUPPORT)))
    if not db.has_asset(b["id"], "logo"):
        lines.append("\n" + tx(ctx, "menu_no_kit"))
    else:
        lines.append("\n" + tx(ctx, "menu_hint"))
    return "\n".join(lines)


def menu_kb(ctx, b, brands):
    rows = []
    if b["role"] == "owner":
        eb = editor_btn(ctx, b["id"])
        if eb and db.has_asset(b["id"], "logo"):
            rows.append([eb, Btn(tx(ctx, "b_desktop"), callback_data="menu:desktop")])
        elif not db.has_asset(b["id"], "logo"):
            rows.append([Btn(tx(ctx, "k_setup"), callback_data="menu:setup")])
        rows.append([Btn(tx(ctx, "b_team"), callback_data="menu:team")])
    row = []
    if len(brands) > 1:
        row.append(Btn(tx(ctx, "b_switch"), callback_data="menu:switch"))
    row.append(Btn(tx(ctx, "b_code"), callback_data="menu:code"))
    rows.append(row)
    rows.append([Btn(tx(ctx, "b_lang"), callback_data="menu:lang")])
    return KB(rows)


async def show_menu(update, ctx, edit=False):
    b, brands = current_brand(update.effective_user.id)
    if not b:
        await say(update, tx(ctx, "welcome_new"))
        return CODE
    text, kb = menu_text(ctx, b), menu_kb(ctx, b, brands)
    if edit:
        await edit_or_say(update, text, kb)
    else:
        await say(update, text, kb)
    return MENU


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    reset_session(ctx)
    args = ctx.args or []
    if args and args[0].startswith("j_"):
        return await do_join(update, ctx, args[0][2:])
    return await show_menu(update, ctx)


async def cmd_cancel(update, ctx):
    L(ctx, update)
    reset_session(ctx)
    await say(update, tx(ctx, "cancelled"))
    return await show_menu(update, ctx)


async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    await answer(update)
    action = update.callback_query.data.split(":", 1)[1]
    uid = update.effective_user.id
    reset_session(ctx)

    if action == "home":
        return await show_menu(update, ctx, edit=True)
    if action == "lang":
        new = "en" if ctx.user_data.get("lang") == "ru" else "ru"
        db.set_lang(uid, new)
        ctx.user_data["lang"] = new
        return await show_menu(update, ctx, edit=True)
    if action == "code":
        await edit_or_say(update, tx(ctx, "ask_code"), KB([[Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]))
        return CODE

    b, brands = current_brand(uid)
    if not b:
        return await show_menu(update, ctx)
    if action == "switch":
        rows = [[Btn(("✓ " if x["id"] == b["id"] else "") + (x["kit"].get("name") or f"#{x['id']}"),
                     callback_data=f"sw:{x['id']}")] for x in brands]
        rows.append([Btn(tx(ctx, "b_menu"), callback_data="menu:home")])
        await edit_or_say(update, tx(ctx, "switch_head"), KB(rows))
        return MENU
    if action == "setup":
        if b["role"] != "owner":
            await say(update, tx(ctx, "kit_owner_only"))
            return MENU
        ctx.user_data["kit_bid"] = b["id"]
        ctx.user_data["wiz"] = True
        return await ask_name(update, ctx)
    if action == "team":
        return await show_team(update, ctx, b, edit=True)
    if action == "desktop":
        await send_desktop_link(update, ctx, b)
        return MENU
    if action == "new":   # кнопка из старых сообщений
        await say(update, tx(ctx, "q_hint"))
        return MENU
    return await show_menu(update, ctx, edit=True)


async def on_switch(update, ctx):
    L(ctx, update)
    await answer(update)
    bid = int(update.callback_query.data.split(":")[1])
    if db.member_role(bid, update.effective_user.id):
        db.set_active_brand(update.effective_user.id, bid)
    return await show_menu(update, ctx, edit=True)


async def send_desktop_link(update, ctx, b):
    """Одноразовая ссылка на редактор для компьютера (15 минут)."""
    if b["role"] != "owner":
        await say(update, tx(ctx, "kit_owner_only"))
        return
    if not WEBAPP_URL:
        await say(update, tx(ctx, "editor_off"))
        return
    tok = db.create_login_link(update.effective_user.id, b["id"])
    url = f"{WEBAPP_URL}/?b={b['id']}&k={tok}"
    await say(update, tx(ctx, "desktop_link", min=db.LINK_TTL_MIN),
              KB([[Btn(tx(ctx, "b_open_desktop"), url=url)]]))


async def cmd_desktop(update, ctx):
    L(ctx, update)
    b, _ = current_brand(update.effective_user.id)
    if not b:
        await say(update, tx(ctx, "welcome_new"))
        return
    await send_desktop_link(update, ctx, b)


# ============ Доступ ============
async def on_code(update, ctx):
    L(ctx, update)
    res = db.redeem_invite(update.message.text or "")
    if not res:
        await say(update, tx(ctx, "code_bad"))
        return CODE
    plan, days = res
    bid = db.create_brand(update.effective_user.id, plan, days)
    b = db.get_brand(bid)
    await say(update, tx(ctx, "code_ok", plan=plan.capitalize(), until=fmt_date(b["plan_until"])))
    ctx.user_data["kit_bid"] = bid
    ctx.user_data["wiz"] = True
    return await ask_name(update, ctx)


async def do_join(update, ctx, token):
    u = update.effective_user
    b = db.brand_by_token(token)
    if not b:
        await say(update, tx(ctx, "join_bad"))
        return await show_menu(update, ctx)
    name = esc(b["kit"].get("name") or "—")
    if db.member_role(b["id"], u.id):
        db.set_active_brand(u.id, b["id"])
        await say(update, tx(ctx, "join_ok", brand=name))
        return await show_menu(update, ctx)
    if db.member_count(b["id"]) >= plan_limits(b)["members"]:
        await say(update, tx(ctx, "join_full", brand=name))
        return await show_menu(update, ctx)
    db.add_member(b["id"], u.id, "editor")
    db.set_active_brand(u.id, b["id"])
    await say(update, tx(ctx, "join_ok", brand=name))
    owner = db.get_user(b["owner_id"]) or {}
    who = esc(u.full_name) + (f" (@{u.username})" if u.username else "")
    try:
        await ctx.bot.send_message(b["owner_id"], _t(owner.get("lang", "ru"), "join_notify", who=who, brand=name),
                                   parse_mode=HTML)
    except TelegramError:
        pass
    return await show_menu(update, ctx)


async def show_team(update, ctx, b, edit=False):
    if b["role"] != "owner":
        await say(update, tx(ctx, "team_owner_only"))
        return MENU
    link = f"https://t.me/{ctx.bot.username}?start=j_{b['join_token']}"
    text = tx(ctx, "team_head", brand=esc(b["kit"].get("name") or "—"), n=db.member_count(b["id"]),
              limit=plan_limits(b)["members"], link=link)
    kb = KB([[Btn(tx(ctx, "b_team_new"), callback_data="team:new")],
             [Btn(tx(ctx, "b_menu"), callback_data="menu:home")]])
    await (edit_or_say(update, text, kb) if edit else say(update, text, kb))
    return MENU


async def on_team(update, ctx):
    L(ctx, update)
    await answer(update)
    b, _ = current_brand(update.effective_user.id)
    if not b or b["role"] != "owner":
        return await show_menu(update, ctx, edit=True)
    db.regen_token(b["id"])
    b, _ = current_brand(update.effective_user.id)
    return await show_team(update, ctx, b, edit=True)


# ============ Онбординг: название → логотип → редактор ============
def step(ctx, n):
    return tx(ctx, "step", n=n) if ctx.user_data.get("wiz") else ""


def kit_bid(ctx):
    return ctx.user_data.get("kit_bid")


async def ask_name(update, ctx):
    await edit_or_say(update, step(ctx, 1) + tx(ctx, "ask_name"))
    return K_NAME


async def on_name(update, ctx):
    name = (update.message.text or "").strip()
    if not 1 <= len(name) <= 40:
        await say(update, tx(ctx, "name_bad"))
        return K_NAME
    db.update_kit(kit_bid(ctx), name=name)
    await say(update, step(ctx, 2) + tx(ctx, "ask_logo"))
    return K_LOGO


async def on_logo(update, ctx):
    data, as_photo = await get_file_bytes(update, ctx)
    if not data:
        return K_LOGO
    try:
        png, had_alpha = await run(R.prepare_logo, data)
    except ValueError:
        await say(update, tx(ctx, "logo_empty"))
        return K_LOGO
    except Exception as e:
        logger.info("logo open failed: %s", e)
        await say(update, tx(ctx, "logo_bad"))
        return K_LOGO
    bid = kit_bid(ctx)
    db.set_asset(bid, "logo", png)
    if as_photo:
        await say(update, tx(ctx, "logo_photo"))
    elif not had_alpha:
        await say(update, tx(ctx, "logo_bg"))
    eb = editor_btn(ctx, bid)
    await say(update, tx(ctx, "wiz_done") if eb else tx(ctx, "editor_off"), KB([[eb]]) if eb else None)
    reset_session(ctx)
    return await show_menu(update, ctx)


# ============ Быстрый пост: фото → превью с пультом ============
# Человек присылает фото (одно или альбомом) с подписью. Бот сразу отвечает
# превью по последнему шаблону и формату этого человека; под превью — пульт.
# Любая правка перерисовывает превью на месте. «Файлы» отдают готовые JPG,
# а пульт появляется заново под ними — можно сделать ещё вариант.
DRAFT_TTL = 180          # сек: фото без подписи в этот срок дополняют текущий пост
REFRESH_DELAY = 1.2      # сек: ждём остальные фото альбома
TAG_RE = re.compile(r"#[\w\-]+", re.U)


def parse_text(text, fields):
    """Подпись → (заголовок, подзаголовок, хештег|None).
    Пустая строка отделяет подзаголовок. Если в шаблоне есть подзаголовок,
    а пустой строки нет — первая строка заголовок, остальное подзаголовок."""
    text = text or ""
    tags = TAG_RE.findall(text)
    body = TAG_RE.sub("", text)
    body = "\n".join(ln.strip() for ln in body.split("\n")).strip()
    title = subtitle = ""
    if body:
        paras = re.split(r"\n\s*\n", body, maxsplit=1)
        if len(paras) == 2:
            title, subtitle = paras[0].strip(), paras[1].strip()
        elif "subtitle" in fields and "\n" in body:
            title, subtitle = body.split("\n", 1)
        else:
            title = body
    return title.strip()[:300], subtitle.strip()[:300], (tags[0][:31] if tags else None)


def draft(ctx):
    return ctx.user_data.get("q")


def _tpl(d):
    return db.get_template(d["bid"], d["tid"])


def _ctx_for(base, d, i, n, dark):
    return R.Ctx(base.palette, base.logos, base.customs,
                 dict(title=d.get("title", ""), subtitle=d.get("subtitle", ""), hashtag=d.get("tag") or "",
                      i=i, n=n), dark, base.images)


def _render_job(data, spec, fmt, ctx):
    return [(suf, R.to_jpeg(im)) for suf, im in R.render_template(R.open_photo(data), spec, fmt, ctx)]


def _preview_job(data, spec, fmt, ctx):
    photo = R.open_photo(data)
    W, H = R.feed_size(photo, fmt)
    return R.to_preview(R.render_surface(photo, W, H, spec["feed"]["layers"], ctx))


def fmt_label(ctx, fmt):
    return tx(ctx, "fmt_orig") if fmt == "orig" else fmt


def pult_caption(ctx, d, tp):
    fields = R.spec_fields(tp["spec"])
    bits = [f"<b>{esc(tp['name'])}</b>", fmt_label(ctx, d["fmt"])]
    if "hashtag" in fields:
        bits.append(esc(d.get("tag") or tx(ctx, "q_no_tag")))
    lines = [" · ".join(bits), tx(ctx, "q_photos", n=len(d["photos"]))
             + (tx(ctx, "tpl_story") if tp["spec"]["story"].get("enabled") else "")]
    if "title" in fields and not d.get("title"):
        lines.append(tx(ctx, "q_no_title"))
    return "\n".join(lines)


def pult_kb(ctx, d, tp):
    fields = R.spec_fields(tp["spec"])
    name = tp["name"] if len(tp["name"]) <= 14 else tp["name"][:13] + "…"
    row = [Btn("🧩 " + name, callback_data="q:tpl"), Btn("📐 " + fmt_label(ctx, d["fmt"]), callback_data="q:fmt")]
    if "hashtag" in fields:
        row.append(Btn(d.get("tag") or "#", callback_data="q:tag"))
    rows = [row]
    if R.spec_has_shade(tp["spec"]):
        last = len(R.DARK_STEPS) - 1
        rows.append([Btn(tx(ctx, "b_lighter") if d["dark"] > 0 else "· · ·", callback_data="q:lighter"),
                     Btn(tx(ctx, "b_darker") if d["dark"] < last else "· · ·", callback_data="q:darker")])
    rows.append([Btn(tx(ctx, "b_q_text"), callback_data="q:text")])
    rows.append([Btn(tx(ctx, "b_q_send", n=len(d["photos"])), callback_data="q:send")])
    return KB(rows)


async def show_pult(bot, chat_id, ctx, new=False):
    """Рисует превью первого фото и показывает/обновляет пульт."""
    d = draft(ctx)
    if not d or not d["photos"]:
        return
    tp = _tpl(d)
    if not tp:
        tpls = db.list_templates(d["bid"])
        if not tpls:
            return
        tp = tpls[0]
        d["tid"] = tp["id"]
    d["base"] = d.get("base") or await run(web.brand_ctx, d["bid"])
    c = _ctx_for(d["base"], d, 1, len(d["photos"]), R.DARK_STEPS[d["dark"]])
    data = await run(_preview_job, d["photos"][0], tp["spec"], d["fmt"], c)
    caption, kb = pult_caption(ctx, d, tp), pult_kb(ctx, d, tp)
    if d.get("msg") and not new:
        try:
            await bot.edit_message_media(chat_id=chat_id, message_id=d["msg"],
                                         media=InputMediaPhoto(io.BytesIO(data), caption=caption, parse_mode=HTML),
                                         reply_markup=kb)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            logger.info("pult edit failed, sending new: %s", e)
    if d.get("msg") and new:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=d["msg"], reply_markup=None)
        except TelegramError:
            pass
    m = await bot.send_photo(chat_id, io.BytesIO(data), caption=caption, parse_mode=HTML, reply_markup=kb)
    d["msg"] = m.message_id


def schedule_pult(update, ctx):
    old = ctx.user_data.get("q_task")
    if old and not old.done():
        old.cancel()
    bot, chat_id = ctx.bot, update.effective_chat.id

    async def later():
        try:
            await asyncio.sleep(REFRESH_DELAY)
            await show_pult(bot, chat_id, ctx)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("pult: %s", e)
    ctx.user_data["q_task"] = asyncio.create_task(later())


async def on_quick_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    uid = update.effective_user.id
    b, _ = current_brand(uid)
    if not b:
        await say(update, tx(ctx, "welcome_new"))
        return
    if not db.plan_active(b):
        await say(update, tx(ctx, "no_access", support=esc(SUPPORT)))
        return
    if not db.has_asset(b["id"], "logo"):
        await say(update, tx(ctx, "no_logo"))
        return
    tpls = db.list_templates(b["id"])
    if not tpls:
        eb = editor_btn(ctx, b["id"])
        await say(update, tx(ctx, "no_tpl"), KB([[eb]]) if eb else None)
        return
    data, _ = await get_file_bytes(update, ctx)
    if not data:
        return
    if not is_image(data):
        await say(update, tx(ctx, "photo_bad"))
        return
    msg = update.message
    gid, cap = msg.media_group_id, msg.caption
    d = draft(ctx)
    now = time.time()
    same_album = d and gid and d.get("group") == gid
    add_more = (d and not gid and not cap and not d.get("sent") and d["bid"] == b["id"]
                and now - d["ts"] < DRAFT_TTL)
    if (same_album or add_more) and d["bid"] == b["id"]:
        if len(d["photos"]) >= MAX_BATCH:
            if not d.get("warned_max"):
                d["warned_max"] = True
                await say(update, tx(ctx, "photos_max", n=MAX_BATCH))
            return
        d["photos"].append(data)
        d["ts"] = now
        if cap:
            fields = R.spec_fields(_tpl(d)["spec"]) if _tpl(d) else set()
            d["title"], d["subtitle"], tag = parse_text(cap, fields)
            d["tag"] = tag or d.get("tag")
    else:
        prefs = db.get_prefs(uid, b["id"])
        tp = next((x for x in tpls if x["id"] == prefs.get("tid")), tpls[0])
        fmt = prefs.get("fmt") if prefs.get("fmt") in FORMATS else "4:5"
        if fmt == "9:16" and tp["spec"]["story"].get("enabled"):
            fmt = "4:5"
        title, subtitle, tag = parse_text(cap, R.spec_fields(tp["spec"]))
        ctx.user_data["q"] = d = {"bid": b["id"], "photos": [data], "group": gid, "ts": now, "tid": tp["id"],
                                  "fmt": fmt, "dark": R.DARK_DEFAULT_IDX, "title": title, "subtitle": subtitle,
                                  "tag": tag, "msg": None, "sent": False}
        ctx.user_data.pop("await", None)
    schedule_pult(update, ctx)


async def edit_kb(update, kb):
    try:
        await update.callback_query.edit_message_reply_markup(reply_markup=kb)
    except BadRequest:
        pass


async def on_quick_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    q = update.callback_query
    d = draft(ctx)
    if not d or d.get("msg") != q.message.message_id:
        await on_stale(update, ctx)
        return
    head, _, arg = q.data.partition(":")      # «qf:1:1» → формат «1:1» целиком
    tp = _tpl(d)
    if not tp:
        await on_stale(update, ctx)
        return
    uid = update.effective_user.id
    back = [Btn(tx(ctx, "b_back"), callback_data="q:back")]

    if head == "q" and arg == "tpl":
        await answer(update)
        rows = [[Btn(("✓ " if x["id"] == d["tid"] else "") + x["name"]
                     + (tx(ctx, "tpl_story") if x["spec"]["story"].get("enabled") else ""),
                     callback_data=f"qt:{x['id']}")] for x in db.list_templates(d["bid"])]
        await edit_kb(update, KB(rows + [back]))
        return
    if head == "q" and arg == "fmt":
        await answer(update)
        keys = [k for k in FORMATS if not (k == "9:16" and tp["spec"]["story"].get("enabled"))]
        main = [k for k in keys if k != "orig"]
        rows = [[Btn(("✓ " if k == d["fmt"] else "") + k, callback_data=f"qf:{k}") for k in main[i:i + 3]]
                for i in range(0, len(main), 3)]
        rows.append([Btn(("✓ " if d["fmt"] == "orig" else "") + tx(ctx, "fmt_orig"), callback_data="qf:orig")])
        await edit_kb(update, KB(rows + [back]))
        return
    if head == "q" and arg == "tag":
        await answer(update)
        tags = db.get_brand(d["bid"])["kit"].get("hashtags") or []
        rows, row = [], []
        for i, tag in enumerate(tags):
            row.append(Btn(("✓ " if tag == d.get("tag") else "") + tag, callback_data=f"qh:i:{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Btn(tx(ctx, "tag_none"), callback_data="qh:none")])
        rows.append([Btn(tx(ctx, "tag_custom"), callback_data="qh:custom")])
        await edit_kb(update, KB(rows + [back]))
        return
    if head == "q" and arg == "back":
        await answer(update)
        await edit_kb(update, pult_kb(ctx, d, tp))
        return
    if head == "q" and arg == "text":
        await answer(update)
        ctx.user_data["await"] = "text"
        await say(update, tx(ctx, "q_ask_text"))
        return
    if head == "q" and arg in ("lighter", "darker"):
        new = max(0, min(len(R.DARK_STEPS) - 1, d["dark"] + (1 if arg == "darker" else -1)))
        if new == d["dark"]:
            await q.answer(tx(ctx, "edge"))
            return
        await answer(update)
        d["dark"] = new
        await show_pult(ctx.bot, q.message.chat_id, ctx)
        return
    if head == "qt":
        await answer(update)
        ntp = db.get_template(d["bid"], int(arg)) if arg.isdigit() else None
        if ntp:
            d["tid"] = ntp["id"]
            if d["fmt"] == "9:16" and ntp["spec"]["story"].get("enabled"):
                d["fmt"] = "4:5"
            db.set_prefs(uid, d["bid"], tid=ntp["id"], fmt=d["fmt"])
        await show_pult(ctx.bot, q.message.chat_id, ctx)
        return
    if head == "qf":
        await answer(update)
        if arg in FORMATS:
            d["fmt"] = arg
            db.set_prefs(uid, d["bid"], fmt=arg)
        await show_pult(ctx.bot, q.message.chat_id, ctx)
        return
    if head == "qh":
        await answer(update)
        if arg == "custom":
            ctx.user_data["await"] = "tag"
            await say(update, tx(ctx, "ask_custom_tag"))
            return
        if arg == "none":
            d["tag"] = None
        elif arg.startswith("i:"):
            tags = db.get_brand(d["bid"])["kit"].get("hashtags") or []
            idx = int(arg[2:]) if arg[2:].isdigit() else -1
            if 0 <= idx < len(tags):
                d["tag"] = tags[idx]
        await show_pult(ctx.bot, q.message.chat_id, ctx)
        return
    if head == "q" and arg == "send":
        await send_files(update, ctx, d, tp)
        return
    await answer(update)


async def send_files(update, ctx, d, tp):
    b = db.get_brand(d["bid"])
    n = len(d["photos"])
    if not db.plan_active(b):
        await update.callback_query.answer(tx(ctx, "no_access_short"), show_alert=True)
        return
    left = plan_limits(b)["photos"] - db.photos_used(b["id"])
    if n > left:
        await update.callback_query.answer()
        await say(update, tx(ctx, "limit_hit", left=max(0, left), n=n, support=esc(SUPPORT)))
        return
    await update.callback_query.answer(tx(ctx, "q_sending"))
    d["base"] = d.get("base") or await run(web.brand_ctx, d["bid"])
    dark = R.DARK_STEPS[d["dark"]]
    base = safe_name(b["kit"].get("name")) + "_" + safe_name(tp["name"])
    chat = update.effective_chat
    ok = 0
    for i, data in enumerate(d["photos"], 1):
        try:
            outs = await run(_render_job, data, tp["spec"], d["fmt"], _ctx_for(d["base"], d, i, n, dark))
            for suf, blob in outs:
                name = f"{base}_{i}.jpg" if suf == "feed" else f"{base}_{i}_story.jpg"
                await chat.send_document(io.BytesIO(blob), filename=name)
            ok += 1
        except Exception as e:
            logger.exception("photo %s: %s", i, e)
            await say(update, tx(ctx, "photo_err", i=i))
    db.record_event(d["bid"], update.effective_user.id, "tpl", ok)
    db.set_prefs(update.effective_user.id, d["bid"], tid=d["tid"], fmt=d["fmt"])
    d["sent"] = True
    await show_pult(ctx.bot, chat.id, ctx, new=True)   # пульт снова под файлами


async def on_quick_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    aw = ctx.user_data.pop("await", None)
    d = draft(ctx)
    if not aw or not d:
        b, _ = current_brand(update.effective_user.id)
        await say(update, tx(ctx, "q_hint") if b else tx(ctx, "welcome_new"))
        return
    text = update.message.text or ""
    if aw == "tag":
        words = text.split()
        token = words[0].lstrip("#").strip() if words else ""
        if not token:
            ctx.user_data["await"] = "tag"
            await say(update, tx(ctx, "custom_tag_bad"))
            return
        d["tag"] = "#" + token[:30]
    else:
        tp = _tpl(d)
        d["title"], d["subtitle"], tag = parse_text(text, R.spec_fields(tp["spec"]) if tp else set())
        if tag:
            d["tag"] = tag
    await show_pult(ctx.bot, update.effective_chat.id, ctx, new=True)


# ============ Админ ============
def is_admin(update):
    return update.effective_user and update.effective_user.id in ADMIN_IDS


async def cmd_myid(update, ctx):
    if ADMIN_IDS and not is_admin(update):
        return
    await update.message.reply_text(f"Telegram ID: <code>{update.effective_user.id}</code>", parse_mode=HTML)


async def cmd_newcode(update, ctx):
    """/newcode [тариф=pilot] [дней=30] [использований=1]"""
    if not is_admin(update):
        return
    a = ctx.args or []
    plan = a[0].lower() if a else "pilot"
    if plan not in db.PLANS:
        await update.message.reply_text("Тарифы: " + ", ".join(db.PLANS))
        return
    try:
        days = int(a[1]) if len(a) > 1 else 30
        uses = int(a[2]) if len(a) > 2 else 1
    except ValueError:
        await update.message.reply_text("Формат: /newcode pilot 30 1")
        return
    code = db.create_invite(plan, days, uses)
    await update.message.reply_text(
        f"<code>{code}</code> — {plan}, {days} дн., использований: {uses}\n\n"
        f"Для клиента:\nВаш код доступа к NUMBUS Branding: <code>{code}</code>\n"
        f"Откройте @{ctx.bot.username} и отправьте код.", parse_mode=HTML)


async def cmd_codes(update, ctx):
    if not is_admin(update):
        return
    rows = db.list_invites()
    if not rows:
        await update.message.reply_text("Кодов пока нет. /newcode")
        return
    lines = [f"<code>{r['code']}</code> · {r['plan']} · {r['days']}д · {r['uses']}/{r['max_uses']}" for r in rows]
    await update.message.reply_text("\n".join(lines), parse_mode=HTML)


async def cmd_brands(update, ctx):
    if not is_admin(update):
        return
    rows = db.list_brands()
    if not rows:
        await update.message.reply_text("Брендов пока нет.")
        return
    lines = []
    for b in rows:
        state = "✅" if db.plan_active(b) else "⛔"
        lines.append(f"{state} <b>#{b['id']}</b> {esc(b['kit'].get('name') or '—')} · {b['plan']} до "
                     f"{fmt_date(b['plan_until'])} · 👥{db.member_count(b['id'])} · "
                     f"🧩{db.template_count(b['id'])} · 📷{db.photos_used(b['id'])}/{plan_limits(b)['photos']}")
    storage = "постоянное" if db.STORAGE_PERSISTENT else "⚠️ ВРЕМЕННОЕ — подключи Volume"
    editor = WEBAPP_URL or "⚠️ не задан WEBAPP_URL"
    await update.message.reply_text("\n".join(lines) + f"\n\nХранилище: {storage}\nРедактор: {editor}",
                                    parse_mode=HTML, disable_web_page_preview=True)


async def cmd_extend(update, ctx):
    """/extend <id бренда> <дней> [тариф]"""
    if not is_admin(update):
        return
    a = ctx.args or []
    try:
        bid, days = int(a[0].lstrip("#")), int(a[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Формат: /extend 3 30 [media]")
        return
    plan = a[2].lower() if len(a) > 2 else None
    if plan and plan not in db.PLANS:
        await update.message.reply_text("Тарифы: " + ", ".join(db.PLANS))
        return
    if not db.extend_brand(bid, days, plan):
        await update.message.reply_text("Бренд не найден.")
        return
    b = db.get_brand(bid)
    await update.message.reply_text(f"#{bid}: {b['plan']} до {fmt_date(b['plan_until'])}")


async def on_stale(update, ctx):
    L(ctx, update)
    try:
        await update.callback_query.answer(tx(ctx, "stale"), show_alert=True)
    except TelegramError:
        pass


async def on_orphan(update, ctx):
    L(ctx, update)
    await say(update, tx(ctx, "stale"))


async def on_error(update, ctx):
    logger.error("Ошибка при обработке апдейта", exc_info=ctx.error)


# ============ Сборка ============
def build_app(token=None):
    app = (Application.builder().token(token or TOKEN)
           .concurrent_updates(PerUserProcessor(64))
           .read_timeout(120).write_timeout(120).connect_timeout(30)
           .build())
    IMG = filters.PHOTO | filters.Document.ALL
    TXT = filters.TEXT & ~filters.COMMAND
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("menu", cmd_start),
            CallbackQueryHandler(on_menu, pattern="^menu:"),
            MessageHandler(filters.Regex(CODE_RE), on_code),
        ],
        states={
            MENU: [CallbackQueryHandler(on_switch, pattern=r"^sw:\d+$"),
                   CallbackQueryHandler(on_team, pattern="^team:new$")],
            CODE: [MessageHandler(TXT, on_code)],
            K_NAME: [MessageHandler(TXT, on_name)],
            K_LOGO: [MessageHandler(IMG, on_logo)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel), CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("desktop", cmd_desktop))
    app.add_handler(CommandHandler("newcode", cmd_newcode))
    app.add_handler(CommandHandler("codes", cmd_codes))
    app.add_handler(CommandHandler("brands", cmd_brands))
    app.add_handler(CommandHandler("extend", cmd_extend))
    app.add_handler(conv)
    # Быстрый пост: срабатывает, когда диалог онбординга не ждёт это сообщение
    app.add_handler(CallbackQueryHandler(on_quick_cb, pattern=r"^q[tfh]?:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & IMG, on_quick_photo))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & TXT, on_quick_text))
    app.add_handler(CallbackQueryHandler(on_stale))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_orphan))
    app.add_error_handler(on_error)
    return app


async def amain():
    db.init_db()
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS не задан — админ-команды недоступны. Узнай свой ID через /myid.")
    if not WEBAPP_URL:
        logger.warning("WEBAPP_URL не задан и RAILWAY_PUBLIC_DOMAIN нет — кнопка редактора скрыта.")
    runner = aioweb.AppRunner(web.build_web(TOKEN))
    await runner.setup()
    await aioweb.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info("Редактор: порт %s, адрес %s", PORT, WEBAPP_URL or "—")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    app = build_app()
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await stop.wait()
        await app.updater.stop()
        await app.stop()
    await runner.cleanup()


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    asyncio.run(amain())


if __name__ == "__main__":
    main()
