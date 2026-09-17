import json
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import cards, config, curator, db, formatter, notes, pipeline, reports, slots, sources

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
URL_RE = re.compile(r"https?://\S+")
NOT_COMMAND = ~F.text.startswith("/")


class Edit(StatesGroup):
    text = State()
    rewrite = State()


class Src(StatesGroup):
    add = State()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _pid(cb: CallbackQuery) -> int:
    return int(cb.data.split(":")[1])


# ======================= меню =======================

async def kb_menu() -> InlineKeyboardMarkup:
    md, is_paused = await slots.mode(), await slots.paused()

    def mode_btn(key: str, label: str) -> InlineKeyboardButton:
        return _btn(("● " if md == key else "") + label, f"m:mode:{key}")

    return InlineKeyboardMarkup(inline_keyboard=[
        [mode_btn("manual", "✋ Ручной"), mode_btn("semi", "🤝 Полуавто"), mode_btn("auto", "🤖 Авто")],
        [_btn("▶️ Следующий пост", "m:next:std"), _btn("▫️ Мини-пост", "m:next:mini")],
        [_btn("📝 #ahmagnotes", "m:notes"), _btn("🗂 Очередь слотов", "m:queue")],
        [_btn("📊 Статистика", "m:stats"), _btn("📈 Итоги недели", "m:digest")],
        [_btn("📡 Источники", "m:src"), _btn("🔄 Собрать сейчас", "m:collect")],
        [_btn("▶️ Снять с паузы" if is_paused else "⏸ Пауза", "m:pause")],
    ])


KB_HOME = InlineKeyboardMarkup(inline_keyboard=[[_btn("← Меню", "m:home")]])


async def show_menu(msg: Message, edit: bool = False) -> None:
    text, kb = await reports.menu_text(), await kb_menu()
    if edit:
        try:
            return await msg.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return
    await msg.answer(text, reply_markup=kb)


async def send_next(msg: Message, bot: Bot, fmt: str) -> None:
    post = await pipeline.pick_next(fmt)
    if not post:
        return await msg.answer(f"Готовых постов ({cards.FORMAT_LABEL[fmt]}) нет. Нажмите «Собрать сейчас» или пришлите ссылку.")
    await cards.send_card(bot, post)


async def run_collect(msg: Message) -> None:
    wait = await msg.answer("Собираю…")
    added = await sources.collect_all()
    processed = await pipeline.process_new()
    s = (await db.stats())["ready_by_format"]
    await wait.edit_text(f"Новых материалов: {added}\nОбработано: {processed}\n"
                         f"Готово: стандарт {s.get('std', 0)} · мини {s.get('mini', 0)}")


async def queue_view() -> tuple[str, InlineKeyboardMarkup]:
    lines, rows = ["<b>Очередь слотов</b>"], []
    for fmt in ("std", "mini"):
        queue = await db.approved_posts(fmt)
        times = slots.upcoming(fmt, len(queue))
        for i, p in enumerate(queue):
            head = json.loads(p["data"]).get("headline", "?")
            when = slots.human(times[i][0]) if i < len(times) else "—"
            lines.append(f"• {when} · {cards.FORMAT_LABEL[fmt]} · {head[:60]}")
            rows.append([_btn(f"✕ {head[:40]}", f"q:rm:{p['id']}")])
    for p in await db.announced_posts():
        head = json.loads(p["data"]).get("headline", "?")
        lines.append(f"• 🤖 {p['slot_key'][-5:]} · автопост · {head[:60]}")
    if len(lines) == 1:
        lines.append("Пусто. Посты попадают сюда кнопкой «⏱ В слот» на карточке.")
    if await slots.paused():
        lines.append("\n⏸ Пауза: слоты сейчас не публикуют.")
    rows.append([_btn("← Меню", "m:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def sources_view() -> tuple[str, InlineKeyboardMarkup]:
    rows_data = await reports.sources_rows()
    lines = ["<b>Источники</b> — доля одобренных вами постов"]
    rows = []
    for name, on, rate in rows_data:
        lines.append(f"{'🟢' if on else '⚪️'} {name}: {rate}")
        rows.append([_btn(f"{'Выключить' if on else 'Включить'} {name}", f"src:t:{name}")])
    rows.append([_btn("➕ Добавить RSS", "src:add"), _btn("← Меню", "m:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("m:"))
async def on_menu(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")
    action = parts[1]
    if action == "home":
        await cb.answer()
        return await show_menu(cb.message, edit=True)
    if action == "mode":
        await db.set_setting("mode", parts[2])
        hints = {
            "manual": "Ручной: присылаю карточки, решаете вы",
            "semi": f"Полуавтомат: за {config.SLOT_LEAD_MIN} мин до слота пришлю варианты",
            "auto": f"Автомат: анонс за {config.SLOT_LEAD_MIN} мин, публикую сам от {config.AUTO_MIN_SCORE}/10",
        }
        await cb.answer(hints[parts[2]], show_alert=True)
        return await show_menu(cb.message, edit=True)
    if action == "pause":
        now_paused = not await slots.paused()
        await db.set_setting("paused", now_paused)
        await cb.answer("Пауза: ничего не публикую и не присылаю" if now_paused else "Работаю", show_alert=now_paused)
        return await show_menu(cb.message, edit=True)
    await cb.answer()
    if action == "next":
        return await send_next(cb.message, bot, parts[2])
    if action == "notes":
        return await notes.propose(cb.message)
    if action == "collect":
        return await run_collect(cb.message)
    if action == "stats":
        return await cb.message.edit_text(await reports.stats_text(), reply_markup=KB_HOME)
    if action == "digest":
        return await cb.message.edit_text(await reports.digest_text(), reply_markup=KB_HOME)
    if action == "queue":
        text, kb = await queue_view()
        return await cb.message.edit_text(text, reply_markup=kb)
    if action == "src":
        text, kb = await sources_view()
        return await cb.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("q:rm:"))
async def on_queue_remove(cb: CallbackQuery, bot: Bot):
    pid = int(cb.data.split(":")[2])
    post = await db.get_post(pid)
    if post and post["status"] == "approved":
        await db.update_post(pid, status="sent")
        await cards.refresh_card(bot, pid, "\n\n↩️ <i>Убран из очереди слотов</i>")
    await cb.answer("Убрал — карточка снова активна")
    text, kb = await queue_view()
    await cb.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("src:"))
async def on_sources(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":", 2)
    if parts[1] == "add":
        await state.set_state(Src.add)
        await cb.answer()
        return await cb.message.answer(
            "Пришлите строкой: <code>имя https://адрес-rss</code>\nИмя — латиницей, до 20 знаков. /cancel — отмена.")
    name = parts[2]
    off = await sources.disabled()
    if name in off:
        off.discard(name)
        await cb.answer(f"{name} включён")
    else:
        off.add(name)
        purged = await db.purge_ready(name)
        await cb.answer(f"{name} выключен" + (f", из очереди убрано {purged}" if purged else ""), show_alert=bool(purged))
    await db.set_setting("disabled_sources", sorted(off))
    text, kb = await sources_view()
    await cb.message.edit_text(text, reply_markup=kb)


# ======================= команды =======================

@router.message(Command("start", "help", "menu"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await show_menu(msg)


@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменено.")


@router.message(Command("next"))
async def cmd_next(msg: Message, bot: Bot):
    await send_next(msg, bot, "std")


@router.message(Command("mini"))
async def cmd_mini(msg: Message, bot: Bot):
    await send_next(msg, bot, "mini")


@router.message(Command("collect"))
async def cmd_collect(msg: Message):
    await run_collect(msg)


@router.message(Command("stats"))
async def cmd_stats(msg: Message):
    await msg.answer(await reports.stats_text())


@router.message(Command("purge"))
async def cmd_purge(msg: Message, command: CommandObject):
    source = (command.args or "").strip().lower()
    if not source:
        return await msg.answer("Укажите источник: /purge met")
    n = await db.purge_ready(source)
    await msg.answer(f"Убрано из очереди: {n} ({source})")


# ======================= кнопки карточки =======================

@router.callback_query(F.data.startswith("pub:"))
async def on_publish(cb: CallbackQuery, bot: Bot):
    try:
        ok = await cards.publish_post(bot, _pid(cb))
    except Exception as exc:
        log.exception("publish")
        return await cb.answer(f"Не вышло: {exc!r}"[:190], show_alert=True)
    await cb.answer("Опубликовано" if ok else "Уже опубликовано")


@router.callback_query(F.data.startswith("slot:"))
async def on_slot(cb: CallbackQuery, bot: Bot):
    pid = _pid(cb)
    post = await db.get_post(pid)
    if post["status"] not in ("sent", "announced"):
        return await cb.answer("Этот пост уже не на модерации")
    await db.update_post(pid, status="approved", decided_at=db.now(), slot_key=None)
    when = await slots.eta(post["format"])
    await cards.refresh_card(bot, pid, f"\nВыйдет: {when}")
    note = " (сейчас пауза)" if await slots.paused() else ""
    await cb.answer(f"В очереди. Выйдет {when}{note}", show_alert=bool(note))


@router.callback_query(F.data.startswith("unslot:"))
async def on_unslot(cb: CallbackQuery, bot: Bot):
    pid = _pid(cb)
    post = await db.get_post(pid)
    if post["status"] not in ("approved", "announced"):
        return await cb.answer("Уже не в очереди")
    await db.update_post(pid, status="sent", slot_key=None)
    await cards.refresh_card(bot, pid, "\n\n↩️ <i>Снят с публикации — решение за вами</i>")
    await cb.answer("Снял")


@router.callback_query(F.data.startswith("rej:"))
async def on_reject(cb: CallbackQuery):
    pid = _pid(cb)
    rows = [[_btn(v, f"rr:{pid}:{k}")] for k, v in REJECT_REASONS.items()]
    rows.append([_btn("← Назад", f"back:{pid}")])
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer("Почему?")


@router.callback_query(F.data.startswith("back:"))
async def on_back(cb: CallbackQuery, bot: Bot):
    await cards.refresh_card(bot, _pid(cb))
    await cb.answer()


@router.callback_query(F.data.startswith("rr:"))
async def on_reject_reason(cb: CallbackQuery, bot: Bot):
    _, pid, code = cb.data.split(":")
    reason = REJECT_REASONS.get(code, code)
    await db.update_post(int(pid), status="rejected", reject_reason=reason, decided_at=db.now(), slot_key=None)
    await cards.refresh_card(bot, int(pid), f"\n\n❌ <b>Отклонено:</b> {reason}")
    await cb.answer("Учту")


@router.callback_query(F.data.startswith("fm:"))
async def on_format(cb: CallbackQuery, bot: Bot):
    pid = _pid(cb)
    post = await db.get_post(pid)
    fmt = "std" if post["format"] == "mini" else "mini"
    data = json.loads(post["data"])
    if fmt == "std" and not (data.get("body") or "").strip():
        return await cb.answer("У этого поста нет длинного текста. Нажмите «Переписать» после смены или пришлите свой.",
                               show_alert=True)
    await db.update_post(pid, format=fmt, data=data, caption=formatter.build_caption(data, fmt))
    await cards.refresh_card(bot, pid, caption_changed=True)
    await cb.answer(f"Формат: {cards.FORMAT_LABEL[fmt]}")


# ---------- фото ----------

@router.callback_query(F.data.startswith("ph:"))
async def on_photos(cb: CallbackQuery):
    post = await db.get_post(_pid(cb))
    await cb.message.edit_reply_markup(reply_markup=cards.kb_photos(post))
    await cb.answer("Нажмите номер, чтобы убрать или вернуть фото")


@router.callback_query(F.data.startswith("pc:"))
async def on_cover_mode(cb: CallbackQuery):
    post = await db.get_post(_pid(cb))
    await cb.message.edit_reply_markup(reply_markup=cards.kb_photos(post, cover_mode=True))
    await cb.answer("Какое фото поставить первым?")


@router.callback_query(F.data.startswith("px:") | F.data.startswith("pcs:"))
async def on_photo_toggle(cb: CallbackQuery, bot: Bot):
    kind, pid, i = cb.data.split(":")
    pid, i = int(pid), int(i)
    post = await db.get_post(pid)
    data = json.loads(post["data"])
    n = len(json.loads(post["images"]))
    excluded = set(data.get("_excluded") or [])
    if kind == "pcs":
        data["_cover"] = None if data.get("_cover") == i else i
        excluded.discard(i)
    elif i in excluded:
        excluded.discard(i)
    else:
        if len(excluded) >= n - 1:
            return await cb.answer("Должно остаться хотя бы одно фото", show_alert=True)
        excluded.add(i)
        if data.get("_cover") == i:
            data["_cover"] = None
    data["_excluded"] = sorted(excluded)
    await db.update_post(pid, data=data)
    await cards.refresh_card(bot, pid, markup=cards.kb_photos(await db.get_post(pid)))
    await cb.answer()


@router.callback_query(F.data.startswith("ig:"))
async def on_instagram(cb: CallbackQuery, bot: Bot):
    await cb.answer("Собираю пакет…")
    try:
        await cards.instagram_pack(bot, _pid(cb))
    except Exception as exc:
        log.exception("instagram_pack")
        await cb.message.answer(f"Не получилось: {exc!r}")


# ---------- текст ----------

@router.callback_query(F.data.startswith("edit:"))
async def on_edit(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Edit.text)
    await state.update_data(pid=_pid(cb))
    await cb.message.answer(
        "Пришлите текст поста целиком, с форматированием, как он должен выйти в канале. /cancel — отмена."
    )
    await cb.answer()


@router.message(Edit.text, F.text, NOT_COMMAND)
async def on_edit_text(msg: Message, state: FSMContext, bot: Bot):
    pid = (await state.get_data())["pid"]
    await state.clear()
    await db.update_post(pid, caption=msg.html_text)
    await cards.refresh_card(bot, pid, "\n\n✏️ <i>Текст заменён</i>", caption_changed=True)
    await msg.answer("Готово, карточка обновлена ↑")


@router.callback_query(F.data.startswith("rw:"))
async def on_rewrite(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Edit.rewrite)
    await state.update_data(pid=_pid(cb))
    await cb.message.answer("Что поправить? Напишите комментарий или «-», чтобы просто переписать. /cancel — отмена.")
    await cb.answer()


@router.message(Edit.rewrite, F.text, NOT_COMMAND)
async def on_rewrite_comment(msg: Message, state: FSMContext, bot: Bot):
    pid = (await state.get_data())["pid"]
    await state.clear()
    wait = await msg.answer("Переписываю…")
    post = await db.get_post(pid)
    data = json.loads(post["data"])
    comment = "" if msg.text.strip() == "-" else msg.text
    try:
        new = await curator.rewrite(data, data.get("_source_text", ""), comment, post["format"])
        for k, v in data.items():          # служебные поля: исходник, источники, выбор фото
            if k.startswith("_"):
                new.setdefault(k, v)
        new["flags"] = new.get("flags") or []
        caption = formatter.build_caption(new, post["format"])
        await db.update_post(pid, data=new, caption=caption)
        await cards.refresh_card(bot, pid, "\n\n🔁 <i>Переписано</i>", caption_changed=True)
        await wait.edit_text("Готово, карточка обновлена ↑")
    except Exception as exc:
        log.exception("rewrite")
        await wait.edit_text(f"Не получилось: {exc!r}")


# ======================= источники: добавить RSS =======================

@router.message(Src.add, F.text, NOT_COMMAND)
async def on_add_feed(msg: Message, state: FSMContext):
    m = re.fullmatch(r"\s*([a-z0-9_]{2,20})\s+(https?://\S+)\s*", msg.text, re.I)
    if not m:
        return await msg.answer("Формат: <code>имя https://адрес-rss</code>. Попробуйте ещё раз или /cancel.")
    name, url = m.group(1).lower(), m.group(2)
    await state.clear()
    try:
        n = await sources.check_feed(url)
    except Exception as exc:
        return await msg.answer(f"Лента не открылась: {exc!r}")
    if not n:
        return await msg.answer("По этому адресу нет записей RSS — не добавляю.")
    feeds = await db.get_setting("custom_feeds", {})
    feeds[name] = url
    await db.set_setting("custom_feeds", feeds)
    await msg.answer(f"Добавил «{name}»: в ленте {n} записей. Подтянутся при следующем сборе.")


# ======================= пост по ссылке =======================

@router.message(StateFilter(None), F.text.regexp(URL_RE, search=True))
async def on_link(msg: Message, bot: Bot):
    url = URL_RE.search(msg.text).group(0).rstrip(").,")
    wait = await msg.answer("Собираю пост из ссылки…")
    try:
        pid, note = await pipeline.process_link(url)
    except Exception as exc:
        log.exception("link")
        return await wait.edit_text(f"Не получилось: {exc!r}")
    if not pid:
        return await wait.edit_text(f"Пост не собрался: {note}")
    await cards.send_card(bot, await db.get_post(pid))
    await wait.delete()


router.include_router(notes.router)
