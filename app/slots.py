"""Слоты публикации и режимы.
manual — бот только присылает карточки; в слоты уходят лишь посты, которые автор сам туда поставил.
semi   — перед каждым слотом бот присылает несколько вариантов, автор выбирает.
auto   — бот сам анонсирует пост к слоту и публикует, если автор не отменил.

Пост привязывается к конкретному слоту: posts.slot_key = 'YYYY-MM-DD HH:MM'.
После публикации по слоту ключ остаётся — расписание помнит, чем слот был занят."""
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from app import cards, config, db, pipeline

log = logging.getLogger(__name__)

MODES = {"manual": "✋ ручной", "semi": "🤝 полуавтомат", "auto": "🤖 автомат"}
ICON = {"published": "✅", "approved": "🟡", "announced": "🤖", "offered": "🕑",
        "skipped": "⏭", "empty": "⚪️", "missed": "✖️"}
SHORT = {"std": "ст", "mini": "мини"}


async def mode() -> str:
    m = await db.get_setting("mode", "manual")
    return m if m in MODES else "manual"


async def paused() -> bool:
    return bool(await db.get_setting("paused", False))


# ---------- ключи и время ----------

def _now() -> datetime:
    return datetime.now(ZoneInfo(config.TZ_NAME))


def key_of(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def key_dt(key: str) -> datetime:
    return datetime.strptime(key, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(config.TZ_NAME))


def enc(key: str) -> str:
    """Ключ для callback_data: только цифры, без двоеточий."""
    return "".join(ch for ch in key if ch.isdigit())


def dec(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"


def slot_fmt(key: str) -> str | None:
    hm = key[-5:]
    return next((f for h, m, f in config.SLOTS if f"{h:02d}:{m:02d}" == hm), None)


def human(dt: datetime) -> str:
    days = (dt.date() - _now().date()).days
    day = {0: "сегодня", 1: "завтра"}.get(days, dt.strftime("%d.%m"))
    return f"{day} в {dt:%H:%M}"


def human_key(key: str) -> str:
    return human(key_dt(key))


def upcoming(fmt: str | None, n: int) -> list[tuple[datetime, str]]:
    """Ближайшие n слотов (нужного формата или любых)."""
    now, out, d = _now(), [], 0
    while len(out) < n and d < 60:
        day = now + timedelta(days=d)
        for h, m, f in config.SLOTS:
            dt = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if dt > now and (fmt is None or f == fmt):
                out.append((dt, f))
        d += 1
    return sorted(out)[:n]


# ---------- пропуски ----------

async def skipped() -> set[str]:
    return set(await db.get_setting("skipped_slots", []))


async def toggle_skip(key: str) -> bool:
    """→ True, если слот теперь пропускается."""
    sk = {k for k in await skipped() if k >= key_of(_now() - timedelta(days=1))}
    now_skipped = key not in sk
    sk.symmetric_difference_update({key})
    await db.set_setting("skipped_slots", sorted(sk))
    return now_skipped


# ---------- свободные слоты ----------

async def free_slots(fmt: str | None = None, limit: int = 10, horizon: int = 40) -> list[tuple[str, str]]:
    """[(ключ, формат)] — будущие слоты, где нет ни поста автора, ни автопоста, и которые не пропущены."""
    taken = await db.taken_keys(key_of(_now())) | await skipped()
    out = [(key_of(dt), f) for dt, f in upcoming(fmt, horizon) if key_of(dt) not in taken]
    return out[:limit]


async def next_free(fmt: str) -> str | None:
    free = await free_slots(fmt, limit=1)
    return free[0][0] if free else None


async def is_free(key: str) -> bool:
    return key_dt(key) > _now() and key not in await db.taken_keys(key)


async def reschedule(bot: Bot | None = None) -> int:
    """Одобренные посты, чей слот прошёл (пауза, перезапуск) или не назначен, — в ближайшие свободные."""
    moved = 0
    for p in await db.overdue_approved(key_of(_now())):
        key = await next_free(p["format"])
        if not key:
            continue
        await db.update_post(p["id"], slot_key=key)
        moved += 1
        if bot:
            await cards.refresh_card(bot, p["id"], f"\n↪️ <i>Перенесён: {human_key(key)}</i>")
    return moved


# ---------- состояние дня (для меню) ----------

async def day_state(offset: int = 0) -> list[dict]:
    now = _now()
    day = now + timedelta(days=offset)
    slots_ = []
    for h, m, f in config.SLOTS:
        dt = day.replace(hour=h, minute=m, second=0, microsecond=0)
        slots_.append({"key": key_of(dt), "dt": dt, "fmt": f, "state": "empty", "post": None, "n": 0})
    rows = await db.posts_in_slots([s["key"] for s in slots_])
    sk = await skipped()
    rank = {"published": 4, "approved": 3, "announced": 2, "sent": 1}
    for s in slots_:
        here = [r for r in rows if r["slot_key"] == s["key"]]
        if here:
            best = max(here, key=lambda r: rank[r["status"]])
            s["post"], s["n"] = best, len(here)
            s["state"] = "offered" if best["status"] == "sent" else best["status"]
        elif s["key"] in sk:
            s["state"] = "skipped"
        elif s["dt"] <= now:
            s["state"] = "missed"
    return slots_


def headline(post, limit: int = 44) -> str:
    h = json.loads(post["data"]).get("headline") or "без заголовка"
    return h if len(h) <= limit else h[: limit - 1] + "…"


# ---------- варианты к слоту ----------

async def offer(bot: Bot, key: str, fmt: str, head: str) -> int:
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
    return sent


# ---------- за SLOT_LEAD_MIN минут до слота ----------

async def prepare(bot: Bot, h: int, m: int, fmt: str) -> None:
    md = await mode()
    if md == "manual" or await paused():
        return
    key = key_of((_now() + timedelta(minutes=config.SLOT_LEAD_MIN)).replace(hour=h, minute=m))
    if key in await skipped() or await db.approved_in_slot(key):
        return
    if await db.overdue_approved(key_of(_now()), fmt):
        return  # слот закроет пост, чей собственный слот уже прошёл
    label = cards.FORMAT_LABEL[fmt]

    if md == "auto":
        post = await pipeline.pick_auto(fmt)
        if post:
            await cards.send_card(bot, post, slot_key=key, status="announced")
            return
        head = (f"🤖 К слоту {h:02d}:{m:02d} ({label}) нет материала с оценкой ≥ {config.AUTO_MIN_SCORE} "
                f"без замечаний — выберите сами: «⏱ Ближайший слот» или «✅ Опубликовать».")
    else:
        head = f"🕑 Слот {h:02d}:{m:02d} · {label}. Выберите пост: «⏱ Ближайший слот» поставит его сюда."

    if not await offer(bot, key, fmt, head):
        await bot.send_message(
            config.ADMIN_ID, f"К слоту {h:02d}:{m:02d} ({label}) в очереди пусто. Пришлите ссылку или нажмите «Собрать».")


# ---------- время слота ----------

async def fire(bot: Bot, h: int, m: int, fmt: str) -> None:
    if await paused():
        return
    key = key_of(_now().replace(hour=h, minute=m))
    how = f"по слоту {h:02d}:{m:02d}"
    post = await db.approved_in_slot(key)
    if not post:
        overdue = await db.overdue_approved(key, fmt)   # ждал прошедшего слота — выходит в первом же свободном
        post = overdue[0] if overdue else None
    if not post and await mode() == "auto" and key not in await skipped():
        announced = await db.announced_posts(key)
        post, how = (announced[0], f"автомат, {h:02d}:{m:02d}") if announced else (None, how)
    if post:
        try:
            await cards.publish_post(bot, post["id"], how, slot_key=key)
        except Exception as exc:
            log.exception("Слот %s: публикация %s", key, post["id"])
            await bot.send_message(config.ADMIN_ID, f"⚠️ Слот {h:02d}:{m:02d}: пост не опубликован — {exc!r}")

    await reschedule(bot)

    # невыбранные варианты и несостоявшиеся анонсы этого и прошлых слотов возвращаются в очередь
    for p in await db.slot_leftovers(key):
        if (p["offers"] or 0) >= config.MAX_OFFERS:
            await db.update_post(p["id"], status="auto_rejected", slot_key=None,
                                 reject_reason=f"не выбран {config.MAX_OFFERS} раза")
            note = "\n\n↩️ <i>Не выбран — снят с предложения</i>"
        else:
            await db.update_post(p["id"], status="ready", slot_key=None)
            note = "\n\n↩️ <i>Слот прошёл — вернулся в очередь</i>"
        await cards.refresh_card(bot, p["id"], note)
