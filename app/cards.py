"""Карточка поста: альбом + служебное сообщение с кнопками, публикация в канал, пакет для Instagram."""
import html
import json
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaDocument, InputMediaPhoto)

from app import config, db, formatter

log = logging.getLogger(__name__)

FORMAT_LABEL = {"std": "стандарт", "mini": "мини", "notes": "#ahmagnotes"}


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _fits(caption: str) -> bool:
    return formatter.visible_len(caption) <= config.CAPTION_LIMIT


# ---------- фото ----------

def photo_plan(post) -> list[int]:
    """Индексы фото, которые уйдут в канал: без исключённых, обложка первой, с лимитом формата."""
    data = json.loads(post["data"])
    n = len(json.loads(post["images"]))
    excluded = set(data.get("_excluded") or [])
    idx = [i for i in range(n) if i not in excluded]
    cover = data.get("_cover")
    if cover in idx:
        idx.remove(cover)
        idx.insert(0, cover)
    return idx[: config.MINI_MAX_PHOTOS if post["format"] == "mini" else config.MAX_PHOTOS]


def build_album(files: list, caption: str | None) -> list[InputMediaPhoto]:
    """Подпись ставится на первое фото — так Telegram показывает её под альбомом."""
    return [
        InputMediaPhoto(media=f, caption=caption, parse_mode="HTML") if i == 0 and caption
        else InputMediaPhoto(media=f)
        for i, f in enumerate(files)
    ]


async def _send_photos(bot: Bot, chat_id, files: list, caption: str | None) -> list:
    if not files:
        return []
    if len(files) == 1:   # альбом в Telegram — от двух фото
        return [await bot.send_photo(chat_id, files[0], caption=caption)]
    return await bot.send_media_group(chat_id, build_album(files, caption))


# ---------- клавиатуры ----------

def kb_for(post) -> InlineKeyboardMarkup | None:
    pid, st, fmt = post["id"], post["status"], post["format"]
    if st == "published":
        return InlineKeyboardMarkup(inline_keyboard=[[_btn("📸 Пакет для Instagram", f"ig:{pid}")]])
    if st not in ("sent", "approved", "announced"):
        return None
    if st == "sent":
        first = [_btn("✅ Опубликовать", f"pub:{pid}")]
        if fmt != "notes":
            first.append(_btn("⏱ В слот", f"slot:{pid}"))
    elif st == "approved":
        first = [_btn("✅ Сейчас", f"pub:{pid}"), _btn("↩️ Убрать из слота", f"unslot:{pid}")]
    else:
        first = [_btn("✅ Сейчас", f"pub:{pid}"), _btn("🚫 Отменить автопост", f"unslot:{pid}")]
    rows = [first, [_btn("✏️ Свой текст", f"edit:{pid}"), _btn("🔁 Переписать", f"rw:{pid}")]]
    third = []
    if len(json.loads(post["images"])) > 1:
        third.append(_btn("🖼 Фото", f"ph:{pid}"))
    if fmt != "notes":
        third.append(_btn("↔️ В стандарт" if fmt == "mini" else "↔️ В мини", f"fm:{pid}"))
    if third:
        rows.append(third)
    rows.append([_btn("❌ Отклонить", f"rej:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_photos(post, cover_mode: bool = False) -> InlineKeyboardMarkup:
    pid = post["id"]
    data = json.loads(post["data"])
    n = len(json.loads(post["images"]))
    excluded, cover = set(data.get("_excluded") or []), data.get("_cover")
    nums = []
    for i in range(n):
        label = f"{i + 1}" + ("★" if i == cover else "") + (" ✕" if i in excluded else "")
        nums.append(_btn(label, f"{'pcs' if cover_mode else 'px'}:{pid}:{i}"))
    rows = [nums[i:i + 5] for i in range(0, n, 5)]
    if cover_mode:
        rows.append([_btn("← К фото", f"ph:{pid}")])
    else:
        rows.append([_btn("⭐ Выбрать обложку", f"pc:{pid}"), _btn("Готово", f"back:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- текст карточки ----------

def control_text(post, suffix: str = "") -> str:
    """Служебное сообщение под альбомом: оценка, формат, источник, статус.
    Если подпись не влезает в альбом (>1024), текст поста показывается здесь же."""
    data = json.loads(post["data"])
    fmt = post["format"]
    flags = data.get("flags") or []
    meta = "" if fmt == "notes" else f"<b>{post['score']}/10</b> · "
    meta += f"{FORMAT_LABEL.get(fmt, fmt)} · {post['category']} · {post['source']}"
    if post["url"]:
        meta += f' · <a href="{html.escape(post["url"])}">источник</a>'
    if post["reason"]:
        meta += f"\n<i>{html.escape(post['reason'])}</i>"

    n = len(json.loads(post["images"]))
    plan = photo_plan(post)
    if n and (len(plan) != n or data.get("_cover") is not None):
        meta += f"\n🖼 В канал уйдёт фото: {len(plan)} из {n}"
        if data.get("_cover") is not None:
            meta += f" · обложка №{data['_cover'] + 1}"
    if flags:
        meta += "\n⚠️ " + html.escape("; ".join(map(str, flags)))
    srcs = data.get("_sources") or []
    if srcs:
        meta += "\n\n<b>Источники:</b>\n" + "\n".join(
            f'• <a href="{html.escape(s.get("url") or "")}">{html.escape((s.get("title") or s.get("url") or "?")[:70])}</a>'
            for s in srcs if s.get("url"))
    if post["status"] == "approved":
        meta += "\n\n⏱ <b>В очереди на слот</b>"
    elif post["status"] == "announced" and post["slot_key"]:
        meta += f"\n\n🤖 <b>Автопост в {post['slot_key'][-5:]}</b> — выйдет сам, если не отменить"
    meta += suffix

    if not _fits(post["caption"]) or not n:
        budget = config.MESSAGE_LIMIT - formatter.visible_len(meta) - 200
        body, clipped = formatter.clip_blocks(post["caption"], budget)
        note = "⚠️ Текст длиннее 1024 знаков — в канал уйдёт отдельным сообщением под альбомом." if n else \
               "Пост без фото — в канал уйдёт текстом."
        if clipped:
            note += "\n<i>Здесь показано начало; в канал уйдёт целиком.</i>"
        meta = f"{body}\n\n┈┈┈┈┈┈┈┈\n{note}\n{meta}"
    return meta


# ---------- отправка и обновление карточки ----------

async def send_card(bot: Bot, post, slot_key: str | None = None, status: str = "sent") -> None:
    """Альбом с подписью — ровно как будет в канале. Кнопки — ответом на альбом
    (Telegram не разрешает кнопки у альбома)."""
    images = json.loads(post["images"])
    caption = post["caption"] if _fits(post["caption"]) else None
    msgs = await _send_photos(bot, config.ADMIN_ID, [FSInputFile(p) for p in images], caption)
    file_ids = [m.photo[-1].file_id for m in msgs]
    album_id = msgs[0].message_id if msgs else None
    await db.update_post(post["id"], status=status, sent_at=db.now(), file_ids=file_ids,
                         slot_key=slot_key, offers=(post["offers"] or 0) + 1, album_msg_id=album_id)
    post = await db.get_post(post["id"])
    card = await bot.send_message(
        config.ADMIN_ID, control_text(post), reply_markup=kb_for(post),
        disable_web_page_preview=True, reply_to_message_id=album_id,
    )
    await db.update_post(post["id"], card_chat_id=card.chat.id, card_msg_id=card.message_id)


async def refresh_card(bot: Bot, pid: int, suffix: str = "", caption_changed: bool = False,
                       markup: InlineKeyboardMarkup | None = None) -> None:
    post = await db.get_post(pid)
    if not post or not post["card_msg_id"]:
        return
    if caption_changed and post["album_msg_id"]:
        try:
            await bot.edit_message_caption(
                chat_id=post["card_chat_id"], message_id=post["album_msg_id"],
                caption=post["caption"] if _fits(post["caption"]) else "",
                parse_mode="HTML",
            )
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc):
                log.warning("Подпись альбома не обновилась: %s", exc)
    try:
        await bot.edit_message_text(
            chat_id=post["card_chat_id"], message_id=post["card_msg_id"],
            text=control_text(post, suffix), disable_web_page_preview=True,
            reply_markup=markup or kb_for(post),
        )
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            log.warning("Карточка %s не обновилась: %s", pid, exc)


# ---------- публикация ----------

async def publish_post(bot: Bot, pid: int, how: str = "") -> bool:
    """Отправляет пост в канал. how — пометка в карточке («по слоту 14:00», «автомат»)."""
    post = await db.get_post(pid)
    if not post or post["status"] == "published":
        return False
    images = json.loads(post["images"])
    files = json.loads(post["file_ids"] or "[]")
    if len(files) != len(images):
        files = [FSInputFile(p) for p in images]
    chosen = [files[i] for i in photo_plan(post)]
    caption = post["caption"]
    inline = _fits(caption) and bool(chosen)

    msgs = await _send_photos(bot, config.CHANNEL_ID, chosen, caption if inline else None)
    first_id = msgs[0].message_id if msgs else None
    if not inline:
        for chunk in formatter.split_blocks(caption, config.MESSAGE_LIMIT - 100):
            m = await bot.send_message(config.CHANNEL_ID, chunk, disable_web_page_preview=True)
            first_id = first_id or m.message_id
    await db.update_post(pid, status="published", decided_at=db.now(), slot_key=None, channel_msg_id=first_id)
    await refresh_card(bot, pid, f"\n\n✅ <b>Опубликовано</b>{' · ' + how if how else ''}")
    return True


# ---------- Instagram ----------

async def instagram_pack(bot: Bot, pid: int) -> None:
    """Оригиналы фото файлами (без сжатия Telegram) и подпись простым текстом."""
    post = await db.get_post(pid)
    images = json.loads(post["images"])
    plan = photo_plan(post)
    paths = [Path(images[i]) for i in plan]
    if paths and all(p.exists() for p in paths):
        docs = [FSInputFile(p, filename=f"ahmag_{pid}_{n + 1:02d}.jpg") for n, p in enumerate(paths)]
        if len(docs) == 1:
            await bot.send_document(config.ADMIN_ID, docs[0])
        else:
            await bot.send_media_group(config.ADMIN_ID, [InputMediaDocument(media=d) for d in docs])
    else:
        ids = json.loads(post["file_ids"] or "[]")
        if ids:
            await _send_photos(bot, config.ADMIN_ID, [ids[i] for i in plan if i < len(ids)], None)
        await bot.send_message(config.ADMIN_ID, "Оригиналы уже удалены с диска — прислал фото в сжатии Telegram.")
    text = formatter.plain_text(post["caption"])
    await bot.send_message(config.ADMIN_ID, f"<code>{html.escape(text[:3900])}</code>")
