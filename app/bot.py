import json
import logging
import re
from pathlib import Path

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

async def kb_menu(day: int = 0) -> InlineKeyboardMarkup:
    md, is_paused = await slots.mode(), await slots.paused()

    def mode_btn(key: str, label: str) -> InlineKeyboardButton:
        return _btn(("● " if md == key else "") + label, f"m:mode:{key}")

    state = await slots.day_state(day)
    slot_btns = [_btn(f"{x['dt']:%H:%M} {slots.ICON[x['state']]}", f"sl:{slots.enc(x['key'])}") for x in state]
    slot_rows = [slot_btns[i:i + 4] for i in range(0, len(slot_btns), 4)]
    return InlineKeyboardMarkup(inline_keyboard=[
        *slot_rows,
        [_btn("Завтра ▸" if day == 0 else "◂ Сегодня", f"m:day:{1 - day}"), _btn("🔄 Обновить", f"m:day:{day}")],
        [mode_btn("manual", "✋ Ручной"), mode_btn("semi", "🤝 Полуавто"), mode_btn("auto", "🤖 Авто")],
        [_btn("▶️ Следующий пост", "m:next:std"), _btn("▫️ Мини-пост", "m:next:mini")],
        [_btn("📝 #ahmagnotes", "m:notes"), _btn("🔄 Собрать сейчас", "m:collect")],
        [_btn("📊 Статистика", "m:stats"), _btn("📈 Итоги недели", "m:digest")],
        [_btn("📡 Источники", "m:src"), _btn("▶️ Снять с паузы" if is_paused else "⏸ Пауза", "m:pause")],
    ])


KB_HOME = InlineKeyboardMarkup(inline_keyboard=[[_btn("← Меню", "m:home")]])


async def show_menu(msg: Message, edit: bool = False, day: int = 0) -> None:
    text, kb = await reports.menu_text(day), await kb_menu(day)
    if edit:
        try:
            return await msg.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return
    await msg.answer(text, reply_markup=kb)


async def send_next(msg: Message, bot: Bot, fmt: str) -> None:
    post = await pipeline.pick_next(fmt)
    if post:
        return await cards.send_card(bot, post)
    other = "mini" if fmt == "std" else "std"
    n = await db.count_ready(other)
    text = f"Готовых постов ({cards.FORMAT_LABEL[fmt]}) нет."
    if n:
        text += (f"\n\nНо в очереди есть {n} — {cards.FORMAT_LABEL[other]}. "
                 f"Возьмите такой пост и нажмите на карточке «↔️», чтобы сменить формат.")
    else:
        text += "\n\nНажмите «🔄 Собрать сейчас» или пришлите ссылку — соберу пост из неё."
    err = await db.get_setting("api_error")
    if err:
        text += f"\n\n⚠️ {err['text']}"
    await msg.answer(text)


async def run_collect(msg: Message) -> None:
    wait = await msg.answer("Собираю…")
    try:
        added = await sources.collect_all()
        processed, note = await pipeline.process_new()
    except Exception as exc:
        log.exception("collect")
        return await wait.edit_text(curator.explain(exc))
    s = (await db.stats())["ready_by_format"]
    text = (f"Новых материалов: {added}\nОценено: {processed}\n"
            f"Готово: стандарт {s.get('std', 0)} · мини {s.get('mini', 0)}")
    if note:
        text += f"\n\n⚠️ {note}"
    elif processed and not s.get("std", 0) and not s.get("mini", 0):
        text += "\n\nНичего не набрало проходной балл — обычное дело при строгом отборе."
    await wait.edit_text(text)


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
    if action == "day":
        await cb.answer("Обновлено")
        return await show_menu(cb.message, edit=True, day=int(parts[2]))
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
        if not now_paused:
            await slots.reschedule(bot)   # посты, чей слот прошёл за время паузы, — в ближайшие свободные
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
    if action == "src":
        text, kb = await sources_view()
        return await cb.message.edit_text(text, reply_markup=kb)


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


@router.message(Command("diag"))
async def cmd_diag(msg: Message):
    """Короткая самопроверка: диск, база, доступ к Claude."""
    wait = await msg.answer("Проверяю…")
    lines = []
    try:
        config.IMG_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.DATA_DIR / ".probe"
        probe.write_text("ok")
        probe.unlink()
        size = config.DB_PATH.stat().st_size // 1024 if config.DB_PATH.exists() else 0
        folders = len(list(config.IMG_DIR.glob("*"))) if config.IMG_DIR.exists() else 0
        lines.append(f"💾 Диск {config.DATA_DIR}: доступен · база {size} КБ · папок с фото {folders}")
    except Exception as exc:
        lines.append(f"💾 Диск {config.DATA_DIR}: ❌ {exc!r}\nПроверьте volume в настройках Railway.")
    missing = 0
    for p in await db.ready_posts():
        imgs = json.loads(p["images"] or "[]")
        if imgs and not all(Path(x).exists() for x in imgs):
            missing += 1
    lines.append(f"🖼 Постов в очереди с пропавшими фото: {missing}")
    try:
        await curator.ping()
        lines.append("🤖 Claude: отвечает")
    except Exception as exc:
        lines.append(f"🤖 Claude: ❌ {curator.explain(exc)}")
    lines.append(f"📊 Вызовов сегодня: {await db.calls_today()}/{config.DAILY_API_CALLS_MAX}")
    await wait.edit_text("\n\n".join(lines))


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


async def _put_in_slot(bot: Bot, pid: int, key: str) -> None:
    await db.update_post(pid, status="approved", decided_at=db.now(), slot_key=key)
    await cards.refresh_card(bot, pid)


@router.callback_query(F.data.startswith("slot:"))
async def on_slot(cb: CallbackQuery, bot: Bot):
    """Одно нажатие: слот, к которому пост предложен, иначе ближайший свободный его формата."""
    pid = _pid(cb)
    post = await db.get_post(pid)
    if post["status"] not in ("sent", "announced"):
        return await cb.answer("Этот пост уже не на модерации")
    offered = post["slot_key"]
    if post["status"] == "announced" and offered and slots.key_dt(offered) > slots._now():
        key = offered                      # автопост → подтверждённый пост в том же слоте
    elif offered and await slots.is_free(offered):
        key = offered
    else:
        key = await slots.next_free(post["format"])
    if not key:
        return await cb.answer("Свободных слотов этого формата нет. Выберите слот вручную.", show_alert=True)
    await _put_in_slot(bot, pid, key)
    note = " (сейчас пауза)" if await slots.paused() else ""
    await cb.answer(f"Выйдет {slots.human_key(key)}{note}", show_alert=bool(note))


@router.callback_query(F.data.startswith("pick:"))
async def on_pick(cb: CallbackQuery):
    """Список слотов: свободные — поставить; занятые (для уже стоящего поста) — поменяться местами."""
    pid = _pid(cb)
    post = await db.get_post(pid)
    if post["status"] not in ("sent", "approved", "announced"):
        return await cb.answer("Этот пост уже не на модерации")
    rows = []
    for key, fmt in await slots.free_slots(None, limit=8):
        mark = "" if fmt == post["format"] else " ≠"
        rows.append([_btn(f"{slots.human_key(key)} · {slots.SHORT[fmt]}{mark}", f"ps:{pid}:{slots.enc(key)}")])
    if post["status"] == "approved":
        for other in await db.approved_posts():
            if other["id"] != pid and other["slot_key"] and slots.key_dt(other["slot_key"]) > slots._now():
                rows.append([_btn(f"⇄ {slots.human_key(other['slot_key'])} · {slots.headline(other, 24)}",
                                  f"ps:{pid}:{slots.enc(other['slot_key'])}")])
    rows.append([_btn("← Назад", f"back:{pid}")])
    await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer("≠ — слот другого формата" if any("≠" in r[0].text for r in rows) else None)


@router.callback_query(F.data.startswith("ps:"))
async def on_pick_slot(cb: CallbackQuery, bot: Bot):
    _, pid, code = cb.data.split(":")
    pid, key = int(pid), slots.dec(code)
    post = await db.get_post(pid)
    if post["status"] not in ("sent", "approved", "announced"):
        return await cb.answer("Этот пост уже не на модерации")
    if slots.key_dt(key) <= slots._now():
        return await cb.answer("Этот слот уже прошёл", show_alert=True)
    other = await db.approved_in_slot(key)
    if other and other["id"] != pid:
        if post["status"] != "approved" or not post["slot_key"]:
            return await cb.answer("Слот уже занят", show_alert=True)
        await db.update_post(other["id"], slot_key=post["slot_key"])      # меняемся местами
        await cards.refresh_card(bot, other["id"], f"\n⇄ <i>Перенесён: {slots.human_key(post['slot_key'])}</i>")
    elif not other and post["slot_key"] != key and not await slots.is_free(key):
        return await cb.answer("Слот занят автопостом — сначала отмените его", show_alert=True)
    await _put_in_slot(bot, pid, key)
    await cb.answer(f"Выйдет {slots.human_key(key)}")


@router.callback_query(F.data.startswith("unslot:"))
async def on_unslot(cb: CallbackQuery, bot: Bot):
    pid = _pid(cb)
    post = await db.get_post(pid)
    if post["status"] not in ("approved", "announced"):
        return await cb.answer("Уже не в слоте")
    await db.update_post(pid, status="sent", slot_key=None)
    await cards.refresh_card(bot, pid, "\n\n↩️ <i>Снят с публикации — решение за вами</i>")
    await cb.answer("Снял, слот свободен")


# ======================= слот из меню =======================

async def slot_view(key: str) -> tuple[str, InlineKeyboardMarkup]:
    offset = (slots.key_dt(key).date() - slots._now().date()).days
    state = next((x for x in await slots.day_state(offset) if x["key"] == key), None)
    code, rows = slots.enc(key), []
    if not state:
        return "Такого слота в расписании нет.", KB_HOME
    post, st = state["post"], state["state"]
    text = f"<b>Слот {slots.human_key(key)}</b> · {cards.FORMAT_LABEL[state['fmt']]}\n{slots.ICON[st]} "
    if st == "published":
        text += f"Вышел: {slots.headline(post, 80)}"
        link = cards.post_link(post)
        if link:
            rows.append([InlineKeyboardButton(text="Открыть в канале", url=link)])
    elif st == "approved":
        text += f"Стоит: {slots.headline(post, 80)}\n\nПеренести или поменять местами — кнопкой «🔀 Перенести» на карточке."
        rows.append([_btn("👁 К карточке", f"sc:{post['id']}"), _btn("↩️ Освободить слот", f"su:{post['id']}:{code}")])
    elif st == "announced":
        text += f"Автопост: {slots.headline(post, 80)}\nВыйдет сам, если не отменить."
        rows.append([_btn("👁 К карточке", f"sc:{post['id']}"), _btn("🚫 Отменить", f"su:{post['id']}:{code}")])
    elif st == "offered":
        text += f"Прислано вариантов: {state['n']}. Выберите на карточке «⏱ Ближайший слот» — пост встанет сюда."
        rows.append([_btn("👁 К вариантам", f"sc:{post['id']}")])
    elif st == "missed":
        text += "Прошёл пустым."
    else:
        text += "Пропуск: бот не будет его заполнять." if st == "skipped" else "Пусто."
        if st == "empty":
            rows.append([_btn("🎯 Подобрать варианты сейчас", f"so:{code}")])
        rows.append([_btn("↩️ Вернуть слот" if st == "skipped" else "⏭ Пропустить этот слот", f"sk:{code}")])
    rows.append([_btn("← Меню", f"m:day:{min(max(offset, 0), 1)}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("sl:"))
async def on_slot_open(cb: CallbackQuery):
    text, kb = await slot_view(slots.dec(cb.data.split(":")[1]))
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("sk:"))
async def on_slot_skip(cb: CallbackQuery):
    key = slots.dec(cb.data.split(":")[1])
    now_skipped = await slots.toggle_skip(key)
    await cb.answer("Слот пропускается" if now_skipped else "Слот снова в работе")
    text, kb = await slot_view(key)
    await cb.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("so:"))
async def on_slot_offer(cb: CallbackQuery, bot: Bot):
    key = slots.dec(cb.data.split(":")[1])
    fmt = slots.slot_fmt(key)
    if not fmt or not await slots.is_free(key):
        return await cb.answer("Слот уже занят или прошёл", show_alert=True)
    await cb.answer("Подбираю…")
    head = f"🎯 Варианты для слота {slots.human_key(key)} · {cards.FORMAT_LABEL[fmt]}. «⏱ Ближайший слот» поставит пост сюда."
    if not await slots.offer(bot, key, fmt, head):
        await cb.message.answer("Готовых постов этого формата нет. Нажмите «Собрать сейчас» или пришлите ссылку.")


@router.callback_query(F.data.startswith("sc:"))
async def on_slot_card(cb: CallbackQuery, bot: Bot):
    post = await db.get_post(_pid(cb))
    await cb.answer()
    try:
        await bot.send_message(config.ADMIN_ID, "Карточка ↑", reply_to_message_id=post["card_msg_id"])
    except TelegramBadRequest:
        await cards.send_card(bot, post, slot_key=post["slot_key"], status=post["status"])  # сообщение удалено — шлём заново


@router.callback_query(F.data.startswith("su:"))
async def on_slot_free(cb: CallbackQuery, bot: Bot):
    _, pid, code = cb.data.split(":")
    post = await db.get_post(int(pid))
    if post and post["status"] in ("approved", "announced"):
        await db.update_post(int(pid), status="sent", slot_key=None)
        await cards.refresh_card(bot, int(pid), "\n\n↩️ <i>Снят со слота — решение за вами</i>")
    await cb.answer("Слот свободен")
    text, kb = await slot_view(slots.dec(code))
    await cb.message.edit_text(text, reply_markup=kb)


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
        await wait.edit_text(curator.explain(exc))


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
        return await wait.edit_text(curator.explain(exc))
    if not pid:
        return await wait.edit_text(f"Пост не собрался: {note}")
    await cards.send_card(bot, await db.get_post(pid))
    await wait.delete()


router.include_router(notes.router)
