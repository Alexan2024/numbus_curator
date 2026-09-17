import html
import json
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
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


def card_text(post) -> str:
    data = json.loads(post["data"])
    flags = data.get("flags") or []
    footer = (
        f"\n\n┈┈┈┈┈┈┈┈\n<b>{post['score']}/10</b> · {post['category']} · {post['source']} · "
        f'<a href="{html.escape(post["url"] or "")}">источник</a>\n<i>{html.escape(post["reason"] or "")}</i>'
    )
    if flags:
        footer += "\n⚠️ " + html.escape("; ".join(map(str, flags)))
    return post["caption"] + footer


async def send_card(bot: Bot, post) -> None:
    images = json.loads(post["images"])
    msgs = await bot.send_media_group(
        config.ADMIN_ID, [InputMediaPhoto(media=FSInputFile(p)) for p in images]
    )
    file_ids = [m.photo[-1].file_id for m in msgs]
    card = await bot.send_message(
        config.ADMIN_ID, card_text(post), reply_markup=kb_main(post["id"]),
        disable_web_page_preview=True,
    )
    await db.update_post(post["id"], status="sent", sent_at=db.now(), file_ids=file_ids,
                         card_chat_id=card.chat.id, card_msg_id=card.message_id)


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


async def refresh_card(bot: Bot, pid: int, suffix: str = "", keyboard: bool = True) -> None:
    post = await db.get_post(pid)
    await bot.edit_message_text(
        chat_id=post["card_chat_id"], message_id=post["card_msg_id"],
        text=card_text(post) + suffix, disable_web_page_preview=True,
        reply_markup=kb_main(pid) if keyboard else None,
    )


# ---------- кнопки ----------

@router.callback_query(F.data.startswith("pub:"))
async def on_publish(cb: CallbackQuery, bot: Bot):
    pid = int(cb.data.split(":")[1])
    post = await db.get_post(pid)
    if post["status"] == "published":
        return await cb.answer("Уже опубликовано")
    caption = post["caption"]
    media = [InputMediaPhoto(media=fid) for fid in json.loads(post["file_ids"])]
    long = formatter.visible_len(caption) > config.CAPTION_LIMIT
    if not long:
        media[0].caption, media[0].parse_mode = caption, "HTML"
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
    await refresh_card(bot, pid, "\n\n✏️ <i>Текст заменён</i>")
    await msg.answer("Готово, карточка обновлена ↑")


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
        await refresh_card(bot, pid, "\n\n🔁 <i>Переписано</i>")
        await wait.edit_text("Готово, карточка обновлена ↑")
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
    await msg.answer(
        f"<b>Посты</b>\nв очереди: {p.get('ready', 0)}\nна модерации: {p.get('sent', 0)}\n"
        f"опубликовано: {p.get('published', 0)}\nотклонено: {p.get('rejected', 0)}\n\n"
        f"<b>Кандидаты</b>\nновые: {c.get('new', 0)} · обработаны: {c.get('processed', 0)} · "
        f"пропущены: {c.get('skipped', 0)} · ошибки: {c.get('error', 0)}\n\n"
        f"Отправлено сегодня: {len(await db.sent_today())}/{config.DAILY_MAX}"
    )
