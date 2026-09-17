import asyncio
import json
import logging
import math
import shutil
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, ErrorEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import cards, config, curator, db, pipeline, reports, slots, sources
from app.bot import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ahmag")

_last_alert: dict[str, float] = {}


def guarded(bot: Bot, name: str, fn, *args):
    """Задача расписания: ошибка не роняет бота, а приходит автору (не чаще раза в час на задачу)."""
    async def job():
        try:
            await fn(*args)
        except Exception as exc:
            log.exception(name)
            if time.time() - _last_alert.get(name, 0) > 3600:
                _last_alert[name] = time.time()
                try:
                    await bot.send_message(config.ADMIN_ID, f"⚠️ Сбой в задаче «{name}»: {exc!r}"[:1000])
                except Exception:
                    log.exception("alert")
    return job


async def collect(bot: Bot | None = None):
    await sources.collect_all()
    before = await db.get_setting("api_error")
    _, note = await pipeline.process_new()
    after = await db.get_setting("api_error")
    # об ошибке доступа к Claude сообщаем один раз, а не при каждом сборе
    if bot and after and (not before or before["text"] != after["text"]):
        await bot.send_message(config.ADMIN_ID, f"⚠️ {after['text']}")


async def deliver(bot: Bot, n: int):
    """Ручной режим: карточки на модерацию по часам, не превышая дневной лимит."""
    if await slots.mode() != "manual" or await slots.paused():
        return
    left = config.DAILY_MAX - len(await db.sent_today())
    for _ in range(max(0, min(n, left))):
        post = await pipeline.pick_next("std") or await pipeline.pick_next("mini")
        if not post:
            break
        try:
            await cards.send_card(bot, post)
        except Exception:
            log.exception("Не удалось отправить карточку %s", post["id"])
            await db.update_post(post["id"], status="auto_rejected", reject_reason="ошибка отправки")


async def digest(bot: Bot):
    await bot.send_message(config.ADMIN_ID, await reports.digest_text(7))


async def cleanup():
    """Удаляет файлы давно решённых постов, чтобы диск не рос бесконечно."""
    root = config.IMG_DIR.resolve()
    for r in await db.finished_before(config.IMAGE_TTL_DAYS):
        images = json.loads(r["images"] or "[]")
        if images:
            folder = Path(images[0]).parent.resolve()
            if folder != root and root in folder.parents:
                shutil.rmtree(folder, ignore_errors=True)


async def main():
    await db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)

    sched = AsyncIOScheduler(timezone=config.TZ_NAME, job_defaults={"misfire_grace_time": 300, "coalesce": True})
    sched.add_job(guarded(bot, "сбор", collect, bot), "interval", hours=config.COLLECT_EVERY_HOURS,
                  id="collect", max_instances=1)
    per_slot = math.ceil(config.DAILY_MAX / len(config.DELIVERY_HOURS))
    for h in config.DELIVERY_HOURS:
        sched.add_job(guarded(bot, "выдача", deliver, bot, per_slot), "cron", hour=h, minute=0, id=f"deliver_{h}")
    for h, m, fmt in config.SLOTS:
        pre = (h * 60 + m - config.SLOT_LEAD_MIN) % 1440
        sched.add_job(guarded(bot, f"подготовка слота {h:02d}:{m:02d}", slots.prepare, bot, h, m, fmt),
                      "cron", hour=pre // 60, minute=pre % 60, id=f"prep_{h}_{m}")
        sched.add_job(guarded(bot, f"слот {h:02d}:{m:02d}", slots.fire, bot, h, m, fmt),
                      "cron", hour=h, minute=m, id=f"fire_{h}_{m}")
    sched.add_job(guarded(bot, "дайджест", digest, bot), "cron",
                  day_of_week=config.DIGEST_DOW, hour=config.DIGEST_HOUR, id="digest")
    sched.add_job(guarded(bot, "очистка", cleanup), "cron", hour=4, minute=30, id="cleanup")
    sched.start()

    await slots.reschedule(bot)   # посты, чей слот прошёл, пока бот не работал
    await bot.set_my_commands([
        BotCommand(command="menu", description="Меню"),
        BotCommand(command="next", description="Следующий пост"),
        BotCommand(command="mini", description="Мини-пост"),
        BotCommand(command="diag", description="Проверить, всё ли работает"),
        BotCommand(command="cancel", description="Отменить ввод"),
    ])
    asyncio.create_task(guarded(bot, "сбор", collect, bot)())  # первый сбор сразу после старта
    log.info("AHMAG curator запущен · режим %s · слоты %s", await slots.mode(), config.SLOTS)
    @dp.error()
    async def on_error(event: ErrorEvent) -> None:
        """Ошибка в кнопке или команде: раньше она уходила в лог, и бот молчал."""
        exc = event.exception
        log.exception("Ошибка обработчика", exc_info=exc)
        text = f"⚠️ {curator.explain(exc)}"
        cb = event.update.callback_query
        try:
            if cb:
                await cb.answer("Не получилось", show_alert=False)
                await bot.send_message(cb.from_user.id, text)
            elif event.update.message:
                await event.update.message.answer(text)
        except Exception:
            log.exception("Не смог сообщить об ошибке")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
