"""Тексты для меню: состояние, статистика, дайджест, источники."""
import html

from app import cards, config, db, slots, sources


def _fmt(d: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda x: -x[1])) or "—"


def _slot_line(s: dict) -> str:
    line = f"{slots.ICON[s['state']]} {s['dt']:%H:%M} {slots.SHORT[s['fmt']]}"
    post, state = s["post"], s["state"]
    if state in ("published", "approved"):
        return f"{line} · {html.escape(slots.headline(post))}"
    if state == "announced":
        return f"{line} · автопост: {html.escape(slots.headline(post, 34))}"
    if state == "offered":
        return f"{line} · вариантов на выбор: {s['n']}"
    return f"{line} · " + {"skipped": "пропуск", "missed": "прошёл пустым"}.get(state, "пусто")


async def menu_text(day: int = 0) -> str:
    s = await db.stats()
    rf = s["ready_by_format"]
    md, is_paused = await slots.mode(), await slots.paused()
    today, other = await slots.day_state(day), await slots.day_state(1 - day)
    title = "Сегодня" if day == 0 else "Завтра"
    lines = [
        f"<b>AHMAG</b> · {slots.MODES[md]}" + (" · ⏸ <b>пауза</b>" if is_paused else ""),
        "",
        f"<b>{title}, {today[0]['dt']:%d.%m}</b>" if today else f"<b>{title}</b>",
        *[_slot_line(x) for x in today],
        "",
        ("Завтра: " if day == 0 else "Сегодня: ") + "".join(slots.ICON[x["state"]] for x in other),
        "",
        f"Готово к выдаче: стандарт {rf.get('std', 0)} · мини {rf.get('mini', 0)}",
        f"Claude сегодня: {await db.calls_today()}/{config.DAILY_API_CALLS_MAX}",
        "",
        "<i>Нажмите на слот, чтобы управлять им. Ссылка в чат — пост из неё.</i>",
    ]
    return "\n".join(lines)


async def stats_text() -> str:
    s = await db.stats()
    p, c = s["posts"], s["candidates"]
    return (
        f"<b>Посты</b>\nв очереди: {p.get('ready', 0)} ({_fmt(s['ready_by_format'])})\n"
        f"на модерации: {p.get('sent', 0)} · ждут слота: {p.get('approved', 0)}\n"
        f"опубликовано: {p.get('published', 0)} · отклонено: {p.get('rejected', 0)}\n\n"
        f"<b>Кандидаты</b>\nновые: {c.get('new', 0)} · обработаны: {c.get('processed', 0)} · "
        f"пропущены: {c.get('skipped', 0)} · ошибки: {c.get('error', 0)}\n\n"
        f"<b>В очереди по источникам:</b> {_fmt(s['ready_by_source'])}\n"
        f"<b>Ждут обработки:</b> {_fmt(s['new_by_source'])}\n\n"
        f"Карточек сегодня: {len(await db.sent_today())}\n"
        f"Claude сегодня: {await db.calls_today()}/{config.DAILY_API_CALLS_MAX}"
    )


def _rate(d: dict) -> str:
    total = d["published"] + d["rejected"]
    return f"{round(100 * d['published'] / total)}% из {total}" if total else "нет решений"


async def digest_text(days: int = 7) -> str:
    d = await db.digest(days)
    total = sum(d["by_format"].values())
    src = "\n".join(f"• {k}: {_rate(v)}" for k, v in sorted(
        d["sources"].items(), key=lambda x: -(x[1]["published"] + x[1]["rejected"]))) or "—"
    u = d["usage"]
    return (
        f"<b>AHMAG · итоги за {days} дн.</b>\n\n"
        f"Опубликовано: <b>{total}</b> ({_fmt({cards.FORMAT_LABEL.get(k, k): v for k, v in d['by_format'].items()})})\n"
        f"Рубрики: {_fmt(d['by_category'])}\n\n"
        f"<b>Одобрено по источникам</b>\n{src}\n\n"
        f"<b>Причины отказов:</b> {_fmt(d['reasons'])}\n\n"
        f"Claude: {u['calls']} вызовов · {u['in_tok'] // 1000}k вход / {u['out_tok'] // 1000}k выход токенов"
    )


async def sources_rows() -> list[tuple[str, bool, str]]:
    """[(имя, включён ли, рейтинг)]"""
    stats, off = await db.source_stats(), await sources.disabled()
    return [(name, name not in off, _rate(stats.get(name, {"published": 0, "rejected": 0})))
            for name in await sources.source_names()]
