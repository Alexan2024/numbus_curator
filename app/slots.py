"""Слоты публикации и режимы.
manual — бот только присылает карточки; в слоты уходят лишь посты, которые автор сам поставил «⏱ В слот».
semi   — перед каждым слотом бот присылает несколько вариантов, автор выбирает.
auto   — бот сам анонсирует пост к слоту и публикует, если автор не отменил."""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from app import cards, config, db, pipeline

log = logging.getLogger(__name__)

MODES = {"manual": "✋ ручной", "semi": "🤝 полуавтомат", "auto": "🤖 автомат"}


async def mode() -> str:
    m = await db.get_setting("mode", "manual")
    return m if m in MODES else "manual"


async def paused() -> bool:
    return bool(await db.get_setting("paused", False))


def _now() -> datetime:
    return datetime.now(ZoneInfo(config.TZ_NAME))


def _key(dt: datetime, h: int, m: int) -> str:
    return f"{dt.date().isoformat()} {h:02d}:{m:02d}"


def upcoming(fmt: str | None, n: int) -> list[tuple[datetime, str]]:
    """Ближайшие n слотов (нужного формата или любых)."""
    now, out = _now(), []
    for d in range(0, n + 2):
        day = now + timedelta(days=d)
        for h, m, f in config.SLOTS:
            dt = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if dt > now and (fmt is None or f == fmt):
                out.append((dt, f))
    return sorted(out)[:n]


def human(dt: datetime) -> str:
    days = (dt.date() - _now().date()).days
    day = {0: "сегодня", 1: "завтра"}.get(days, dt.strftime("%d.%m"))
    return f"{day} в {dt:%H:%M}"


async def eta(fmt: str) -> str:
    """Когда выйдет пост, который только что поставили в очередь."""
    k = len(await db.approved_posts(fmt))
    slots = upcoming(fmt, max(k, 1))
    if len(slots) < max(k, 1):
        return "нет слотов этого формата"
    return human(slots[max(k, 1) - 1][0])


# ---------- за SLOT_LEAD_MIN минут до слота ----------

async def prepare(bot: Bot, h: int, m: int, fmt: str) -> None:
    md = await mode()
    if md == "manual" or await paused():
        return
    if await db.approved_posts(fmt):
        return  # слот уже закрыт постом из очереди
    key = _key(_now() + timedelta(minutes=config.SLOT_LEAD_MIN), h, m)
    label = cards.FORMAT_LABEL[fmt]

    if md == "auto":
        post = await pipeline.pick_auto(fmt)
        if post:
            await cards.send_card(bot, post, slot_key=key, status="announced")
            return
        head = (f"🤖 К слоту {h:02d}:{m:02d} ({label}) нет материала с оценкой ≥ {config.AUTO_MIN_SCORE} "
                f"без замечаний — выберите сами: «⏱ В слот» или «✅ Опубликовать».")
    else:
        head = f"🕑 Слот {h:02d}:{m:02d} · {label}. Выберите пост: «⏱ В слот» — выйдет по расписанию."

    sent = 0
    for _ in range(config.SEMI_OPTIONS):
        post = await pipeline.pick_next(fmt)
        if not post:
            break
        if sent == 0:
            await bot.send_message(config.ADMIN_ID, head)
        try:
            await cards.send_card(bot, post, slot_key=key)
            sent += 1
        except Exception:
            log.exception("Не удалось отправить карточку %s", post["id"])
            await db.update_post(post["id"], status="auto_rejected", reject_reason="ошибка отправки")
    if not sent:
        await bot.send_message(
            config.ADMIN_ID, f"К слоту {h:02d}:{m:02d} ({label}) в очереди пусто. Пришлите ссылку или нажмите «Собрать».")


# ---------- время слота ----------

async def fire(bot: Bot, h: int, m: int, fmt: str) -> None:
    if await paused():
        return
    key = _key(_now(), h, m)
    queue = await db.approved_posts(fmt)
    post = queue[0] if queue else None
    how = f"по слоту {h:02d}:{m:02d}"
    if not post and await mode() == "auto":
        announced = [p for p in await db.announced_posts(key) if p["format"] == fmt]
        post, how = (announced[0], f"автомат, {h:02d}:{m:02d}") if announced else (None, how)
    if post:
        try:
            await cards.publish_post(bot, post["id"], how)
        except Exception as exc:
            log.exception("Слот %s: публикация %s", key, post["id"])
            await bot.send_message(config.ADMIN_ID, f"⚠️ Слот {h:02d}:{m:02d}: пост не опубликован — {exc!r}")

    # невыбранные варианты этого и прошлых слотов возвращаются в очередь
    for p in await db.slot_leftovers(key):
        if (p["offers"] or 0) >= config.MAX_OFFERS:
            await db.update_post(p["id"], status="auto_rejected", slot_key=None,
                                 reject_reason=f"не выбран {config.MAX_OFFERS} раза")
            note = "\n\n↩️ <i>Не выбран — снят с предложения</i>"
        else:
            await db.update_post(p["id"], status="ready", slot_key=None)
            note = "\n\n↩️ <i>Слот прошёл — вернулся в очередь</i>"
        await cards.refresh_card(bot, p["id"], note)
