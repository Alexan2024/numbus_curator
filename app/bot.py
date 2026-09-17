import html
import json
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaPhoto, Message)

from app import config, curator, db, formatter, pipeline, sources

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)

REJECT_REASONS = {
    "taste": "не мой вкус",
    "photo": "слабые фото",
    "dup": "уже было",
    "topic": "не та тема",
    "text": "плохой текст",
}


class Edit(StatesGroup):
    text = State()
    rewrite = State()


# ---------- карточка ----------

def kb_main(pid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{pid}")],
        [InlineKeyboardButton(text="✏️ Свой текст", callback_data=f"edit:{pid}"),
         InlineKeyboardButton(text="🔁 Переписать", callback_data=f"rw:{pid}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rej:{pid}")],
    ])


def kb_reject(pid: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=v, callback_data=f"rr:{pid}:{k}")] for k, v in REJECT_REASONS.items()]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"back:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fits(caption: str) -> bool:
    return formatter.visible_len(caption) <= config.CAPTION_LIMIT


def build_album(files: list, caption: str | None) -> list[InputMediaPhoto]:
    """Подпись ставится на первое фото — так Telegram показывает её под альбомом."""
    return [
        InputMediaPhoto(media=f, caption=caption, parse_mode="HTML") if i == 0 and caption
        else InputMediaPhoto(media=f)
        for i, f in enumerate(files)
    ]


def control_text(post, suffix: str = "") -> str:
    """Служебное сообщение под альбомом: оценка, источник, кнопки.
    Если подпись не влезает в альбом (>1024), текст поста идёт сюда же."""
    data = json.loads(post["data"])
    flags = data.get("flags") or []
    meta = (
        f"<b>{post['score']}/10</b> · {post['category']} · {post['source']} · "
        f'<a href="{html.escape(post["url"] or "")}">источник</a>\n<i>{html.escape(post["reason"] or "")}</i>'
    )
    if flags:
        meta += "\n⚠️ " + html.escape("; ".join(map(str, flags)))
    if not _fits(post["caption"]):
        meta = post["caption"] + "\n\n┈┈┈┈┈┈┈┈\n⚠️ Текст длиннее 1024 знаков — в канал уйдёт отдельным сообщением под альбомом.\n" + meta
    return meta + suffix


async def send_card(bot: Bot, post) -> None:
    """Альбом с подписью — ровно как будет в канале. Кнопки — ответом на альбом
    (Telegram не разрешает кнопки у альбома)."""
    images = json.loads(post["images"])
    media = build_album([FSInputFile(p) for p in images],
                        post["caption"] if _fits(post["caption"]) else None)
    msgs = await bot.send_media_group(config.ADMIN_ID, media)
    file_ids = [m.photo[-1].file_id for m in msgs]
    card = await bot.send_message(
        config.ADMIN_ID, control_text(post), reply_markup=kb_main(post["id"]),
        disable_web_page_preview=True, reply_to_message_id=msgs[0].message_id,
    )
    await db.update_post(post["id"], status="sent", sent_at=db.now(), file_ids=file_ids,
                         card_chat_id=card.chat.id, card_msg_id=card.message_id,
                         album_msg_id=msgs[0].message_id)


async def deliver(bot: Bot, n: int) -> int:
    """Отправить до n карточек, не превышая дневной лимит."""
    left = config.DAILY_MAX - len(await db.sent_today())
    sent = 0
    for _ in range(max(0, min(n, left))):
        post = await pipeline.pick_next()
        if not post:
            break
        try:
            await send_card(bot, post)
            sent += 1
        except Exception:
            log.exception("Не удалось отправить карточку %s", post["id"])
            await db.update_post(post["id"], status="auto_rejected", reject_reason="ошибка отправки")
    return sent


async def refresh_card(bot: Bot, pid: int, suffix: str = "", keyboard: bool = True,
                       caption_changed: bool = False) -> None:
    post = await db.get_post(pid)
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
            reply_markup=kb_main(pid) if keyboard else None,
        )
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


# ---------- кнопки ----------

@router.callback_query(F.data.startswith("pub:"))
async def on_publish(cb: CallbackQuery, bot: Bot):
    pid = int(cb.data.split(":")[1])
    post = await db.get_post(pid)
    if post["status"] == "published":
        return await cb.answer("Уже опубликовано")
    caption = post["caption"]
    long = not _fits(caption)
    media = build_album(json.loads(post["file_ids"]), None if long else caption)
    await bot.send_media_group(config.CHANNEL_ID, media)
    if long:
        await bot.send_message(config.CHANNEL_ID, caption, disable_web_page_preview=True)
    await db.update_post(pid, status="published", decided_at=db.now())
    await refresh_card(bot, pid, "\n\n✅ <b>Опубликовано</b>", keyboard=False)
    await cb.answer("Опубликовано")


@router.callback_query(F.data.startswith("rej:"))
async def on_reject(cb: CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=kb_reject(int(cb.data.split(":")[1])))
    await cb.answer("Почему?")


@router.callback_query(F.data.startswith("back:"))
async def on_back(cb: CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=kb_main(int(cb.data.split(":")[1])))
    await cb.answer()


@router.callback_query(F.data.startswith("rr:"))
async def on_reject_reason(cb: CallbackQuery, bot: Bot):
    _, pid, code = cb.data.split(":")
    reason = REJECT_REASONS.get(code, code)
    await db.update_post(int(pid), status="rejected", reject_reason=reason, decided_at=db.now())
    await refresh_card(bot, int(pid), f"\n\n❌ <b>Отклонено:</b> {reason}", keyboard=False)
    await cb.answer("Учту")


@router.callback_query(F.data.startswith("edit:"))
async def on_edit(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Edit.text)
    await state.update_data(pid=int(cb.data.split(":")[1]))
    await cb.message.answer(
        "Пришлите текст поста целиком, с форматированием, как он должен выйти в канале. /cancel — отмена."
    )
    await cb.answer()


@router.message(Edit.text, F.text)
async def on_edit_text(msg: Message, state: FSMContext, bot: Bot):
    pid = (await state.get_data())["pid"]
    await state.clear()
    await db.update_post(pid, caption=msg.html_text)
    await refresh_card(bot, pid, "\n\n✏️ <i>Текст заменён</i>", caption_changed=True)
    await msg.answer("Готово, подпись в альбоме обновлена ↑")


@router.callback_query(F.data.startswith("rw:"))
async def on_rewrite(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Edit.rewrite)
    await state.update_data(pid=int(cb.data.split(":")[1]))
    await cb.message.answer("Что поправить? Напишите комментарий или «-», чтобы просто переписать.")
    await cb.answer()


@router.message(Edit.rewrite, F.text)
async def on_rewrite_comment(msg: Message, state: FSMContext, bot: Bot):
    pid = (await state.get_data())["pid"]
    await state.clear()
    wait = await msg.answer("Переписываю…")
    post = await db.get_post(pid)
    data = json.loads(post["data"])
    comment = "" if msg.text.strip() == "-" else msg.text
    try:
        new = await curator.rewrite(data, data.get("_source_text", ""), comment)
        new["_source_text"] = data.get("_source_text", "")
        new["flags"] = new.get("flags") or []
        caption = formatter.build_caption(new)
        await db.update_post(pid, data=new, caption=caption)
        await refresh_card(bot, pid, "\n\n🔁 <i>Переписано</i>", caption_changed=True)
        await wait.edit_text("Готово, подпись в альбоме обновлена ↑")
    except Exception as exc:
        log.exception("rewrite")
        await wait.edit_text(f"Не получилось: {exc!r}")


# ---------- команды ----------

@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменено.")


@router.message(Command("start", "help"))
async def cmd_start(msg: Message):
    await msg.answer(
        "<b>AHMAG curator</b>\n\n"
        "/next — прислать следующий пост сейчас\n"
        "/collect — собрать и обработать материалы сейчас\n"
        "/stats — состояние очереди\n"
        "/purge met — убрать из очереди все посты источника\n"
        "/cancel — отменить ввод"
    )


@router.message(Command("next"))
async def cmd_next(msg: Message, bot: Bot):
    post = await pipeline.pick_next()
    if not post:
        return await msg.answer("Очередь пуста. /collect — собрать материалы.")
    await send_card(bot, post)


@router.message(Command("collect"))
async def cmd_collect(msg: Message):
    wait = await msg.answer("Собираю…")
    added = await sources.collect_all()
    processed = await pipeline.process_new()
    ready = await db.count_ready()
    await wait.edit_text(f"Новых материалов: {added}\nОбработано: {processed}\nГотово в очереди: {ready}")


@router.message(Command("stats"))
async def cmd_stats(msg: Message):
    s = await db.stats()
    p, c = s["posts"], s["candidates"]

    def fmt(d):
        return ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda x: -x[1])) or "—"
    await msg.answer(
        f"<b>Посты</b>\nв очереди: {p.get('ready', 0)}\nна модерации: {p.get('sent', 0)}\n"
        f"опубликовано: {p.get('published', 0)}\nотклонено: {p.get('rejected', 0)}\n\n"
        f"<b>Кандидаты</b>\nновые: {c.get('new', 0)} · обработаны: {c.get('processed', 0)} · "
        f"пропущены: {c.get('skipped', 0)} · ошибки: {c.get('error', 0)}\n\n"
        f"<b>В очереди по источникам:</b> {fmt(s['ready_by_source'])}\n"
        f"<b>Ждут обработки:</b> {fmt(s['new_by_source'])}\n\n"
        f"Отправлено сегодня: {len(await db.sent_today())}/{config.DAILY_MAX}"
    )


@router.message(Command("purge"))
async def cmd_purge(msg: Message, command: CommandObject):
    source = (command.args or "").strip().lower()
    if not source:
        return await msg.answer("Укажите источник: /purge met")
    n = await db.purge_ready(source)
    await msg.answer(f"Убрано из очереди: {n} ({source})")
