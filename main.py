import asyncio
import json
import logging
import math
import shutil
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import cards, config, db, pipeline, reports, slots, sources
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


async def collect():
    await sources.collect_all()
    await pipeline.process_new()


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
    sched.add_job(guarded(bot, "сбор", collect), "interval", hours=config.COLLECT_EVERY_HOURS,
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

    await bot.set_my_commands([
        BotCommand(command="menu", description="Меню"),
        BotCommand(command="next", description="Следующий пост"),
        BotCommand(command="mini", description="Мини-пост"),
        BotCommand(command="cancel", description="Отменить ввод"),
    ])
    asyncio.create_task(guarded(bot, "сбор", collect)())  # первый сбор сразу после старта
    log.info("AHMAG curator запущен · режим %s · слоты %s", await slots.mode(), config.SLOTS)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
