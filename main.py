import asyncio
import logging
import math

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import config, db, pipeline, sources
from app.bot import deliver, router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ahmag")


async def collect_job():
    try:
        await sources.collect_all()
        await pipeline.process_new()
    except Exception:
        log.exception("collect_job")


def make_deliver_job(bot: Bot):
    per_slot = math.ceil(config.DAILY_MAX / len(config.DELIVERY_HOURS))

    async def job():
        try:
            n = await deliver(bot, per_slot)
            log.info("Отправлено карточек: %s", n)
        except Exception:
            log.exception("deliver_job")
    return job


async def main():
    await db.init()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)

    sched = AsyncIOScheduler(timezone=config.TZ_NAME)
    sched.add_job(collect_job, "interval", hours=config.COLLECT_EVERY_HOURS,
                  id="collect", max_instances=1, coalesce=True)
    for h in config.DELIVERY_HOURS:
        sched.add_job(make_deliver_job(bot), "cron", hour=h, minute=0, id=f"deliver_{h}")
    sched.start()

    asyncio.create_task(collect_job())  # первый сбор сразу после старта
    log.info("AHMAG curator запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
