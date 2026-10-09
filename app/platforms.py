"""Три площадки на пульте: ✈️ Канал, 📸 Инста, 🌐 Сайт — что на каждую выйдет по плану.

Куда что идёт (с 6.0, MINI_TO=ig):
  канал (Telegram)  — посты с текстом (std), новости, #ahmagnotes и подборки;
  Instagram         — свои фото-посты (mini) и копии постов канала с английской подписью (кроме подборок);
  сайт              — всё из канала, кроме подборок; пост для канала — с продолжением текста (site_more).

Экран площадки: слоты на две недели вперёд с постами этой площадки (номер — кнопка, пост открывается карточкой
со всеми действиями), свободные слоты на сегодня и завтра, внизу — что ждёт решения, запас и состояние площадки.
Вид "pf" живёт в screen.VIEWS, кнопки — h:pf:<площадка>, h:pl:<площадка>:<пост> (bot.on_home)."""
import html
import json
import logging
from datetime import timedelta

from aiogram.types import InlineKeyboardButton

from app import cards, config, db, formatter, screen, sitepub, slots

log = logging.getLogger(__name__)

PF = {"tg": ("✈️", "Канал"), "ig": ("📸", "Инста"), "web": ("🌐", "Сайт")}
HORIZON = 14                      # дней вперёд
RANK = {"published": 4, "approved": 3, "announced": 2, "sent": 1}
ACTIVE = ("approved", "announced", "sent")
ICON = {"approved": "🟡", "announced": "🤖", "sent": "📥", "published": "✅"}
IG_ICON = {"done": "✅", "queued": "⏳", "publishing": "⏳", "failed": "⚠️", "skipped": "—"}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
BUDGET = 1000                     # подпись к фото — до 1024 знаков
HEAD_LIMIT = 30


def title(pf: str) -> str:
    icon, name = PF.get(pf, ("", pf))
    return f"{icon} {name}"


def fmts(pf: str) -> set[str]:
    """Форматы постов, которые выходят на площадке сами (для входящих и запаса)."""
    if pf == "ig":
        return {"mini"} if config.MINI_IG else set()
    if pf == "tg":
        return {"std", "notes"} | (set() if config.MINI_IG else {"mini"})
    return {"std", "notes"}


def slot_owner(fmt: str) -> str:
    """Чей слот: мини-слоты с 6.0 — Instagram, остальные — канал."""
    return "ig" if fmt == "mini" and config.MINI_IG else "tg"


def role(post, pf: str, ig_on: bool) -> str | None:
    """'own' — пост выходит на площадке сам; 'copy' — копия поста канала в Instagram; None — сюда не идёт."""
    if not post:
        return None
    if pf == "tg":
        return None if cards.ig_only(post) else "own"
    if pf == "ig":
        if cards.ig_only(post):
            return "own"
        return "copy" if ig_on and (post["source"] or "") != "digest" else None
    return "own" if sitepub.wants(post) else None


def own(post, pf: str) -> bool:
    return role(post, pf, False) == "own"


async def ig_on() -> bool:
    """Копии постов канала уходят в Instagram: он настроен, включён и у бота есть адрес для фото."""
    from app import instagram
    try:
        return instagram.configured() and bool(instagram.public_url()) and await instagram.enabled()
    except Exception:
        return False


# ======================= данные =======================

async def slot_rows(days: int = HORIZON) -> list[dict]:
    """Слоты с сегодня на days дней вперёд: {key, dt, fmt, post|None, skipped, past}. Один запрос в базу."""
    now = slots._now()
    out = []
    for d in range(days + 1):
        day = now + timedelta(days=d)
        for h, m, f in config.SLOTS:
            dt = day.replace(hour=h, minute=m, second=0, microsecond=0)
            out.append({"key": slots.key_of(dt), "dt": dt, "fmt": f, "post": None})
    by: dict[str, list] = {}
    for r in await db.posts_in_slots([s["key"] for s in out]):
        by.setdefault(r["slot_key"], []).append(r)
    sk = await slots.skipped()
    for s in out:
        here = by.get(s["key"])
        if here:
            s["post"] = max(here, key=lambda r: RANK[r["status"]])
        s["skipped"] = s["key"] in sk
        s["past"] = s["dt"] <= now
    return sorted(out, key=lambda s: s["dt"])


def _planned(s: dict, pf: str, ig: bool) -> bool:
    p = s["post"]
    return bool(p) and not s["past"] and p["status"] in ACTIVE and bool(role(p, pf, ig))


async def planned_ids(pf: str, rows: list[dict] | None = None, ig: bool | None = None) -> list[int]:
    """Посты площадки в будущих слотах, по времени. Тот же порядок, что номера на экране площадки."""
    rows = rows if rows is not None else await slot_rows()
    ig = await ig_on() if ig is None else ig
    return [s["post"]["id"] for s in rows if _planned(s, pf, ig)]


async def inbox(pf: str) -> list:
    return [p for p in await db.inbox_posts() if own(p, pf)]


async def stock(pf: str, cat: str | None = None) -> list:
    fm = fmts(pf)
    return [p for p in await db.ready_posts(None, cat) if p["format"] in fm]


def stock_total(counts: dict, pf: str) -> int:
    fm = fmts(pf)
    return sum(n for v in counts.values() for f, n in v.items() if f in fm)


async def ig_queue() -> dict[str, int]:
    try:
        async with db.connect() as c:
            cur = await c.execute("SELECT status, COUNT(*) n FROM ig_posts GROUP BY status")
            return {r["status"]: r["n"] for r in await cur.fetchall()}
    except Exception:
        return {}


async def ig_states(ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    try:
        async with db.connect() as c:
            cur = await c.execute(f"SELECT post_id, status FROM ig_posts WHERE post_id IN ({','.join('?' * len(ids))})",
                                  tuple(ids))
            return {r["post_id"]: r["status"] for r in await cur.fetchall()}
    except Exception:
        return {}


async def summary() -> dict[str, dict]:
    """Для пульта: {площадка: {plan, inbox, stock}}."""
    rows, ig = await slot_rows(), await ig_on()
    box, counts = await db.inbox_posts(), await db.stock_counts()
    out = {}
    for pf in PF:
        out[pf] = {"plan": len(await planned_ids(pf, rows, ig)),
                   "inbox": sum(1 for p in box if own(p, pf)),
                   "stock": stock_total(counts, pf)}
    return out


async def site_line() -> str:
    """Короткое состояние сайта для пульта: выключен, ждут повтора, архив."""
    parts = []
    try:
        if not await sitepub.enabled():
            parts.append("⏸ выключен" if sitepub.configured() else "⚙️ не настроен")
        q = await db.get_setting("site_queue", []) or []
        if q:
            parts.append(f"⚠️ ждут повтора {len(q)}")
        a = _archive_short()
        if a:
            parts.append(a)
    except Exception:
        log.warning("Пульт: состояние сайта", exc_info=True)
    return " · ".join(parts)


def _archive_short() -> str:
    from app import archive
    st = archive.load_state()
    if not st or st.get("phase") == "done":
        return ""
    c = archive.ctl()
    return "архив: " + ("⏸ пауза" if c.get("paused") else archive.PHASES.get(st["phase"], st["phase"]))


# ======================= экран площадки =======================

def day_title(d) -> str:
    word = {0: "Сегодня", 1: "Завтра"}.get((d - slots._now().date()).days)
    base = f"{WEEKDAYS[d.weekday()]} {d:%d.%m}"
    return f"{word} · {base}" if word else base.capitalize()


def _tags(post, pf: str) -> list[str]:
    data = json.loads(post["data"])
    t = []
    if post["format"] == "notes":
        t.append("подборка" if (post["source"] or "") == "digest" else "заметка")
    if pf in ("tg", "web") and post["format"] == "std" and not formatter.has_body(data):
        t.append("✍️ без текста")
    if pf == "web" and post["format"] == "std" and data.get("site_more"):
        t.append("+ продолжение")
    if data.get("flags") and post["status"] != "published":
        t.append("⚠️")
    return t


def _line(s: dict, pf: str, n: int | None, mark: str | None = None) -> str:
    p = s["post"]
    data = json.loads(p["data"])
    head = html.escape(slots.headline(p, HEAD_LIMIT))
    if config.NEWS and data.get("kind") == "news":
        head = "📰 " + head
    if pf == "ig" and not cards.ig_only(p):
        head = "↪ " + head
    icon = mark or ICON.get(p["status"], "")
    text = (f"<b>{n}.</b> " if n else "") + f"{icon} {s['dt']:%H:%M} · {head}"
    tags = _tags(p, pf)
    return text + (" · " + " · ".join(tags) if tags else "")


async def _body(pf: str, rows: list[dict], ig: bool) -> tuple[list[tuple[object, str, bool]], list[int]]:
    """[(день, строка, с номером ли)] и номера постов по порядку. Вышедшие сегодня — без номера, для полноты дня;
    свободные и пропущенные слоты — только свои и только на сегодня и завтра."""
    today = slots._now().date()
    shown_pub = [s["post"]["id"] for s in rows if s["post"] and s["post"]["status"] == "published"
                 and s["dt"].date() == today and role(s["post"], pf, ig)]
    igs = await ig_states(shown_pub) if pf == "ig" else {}
    queue = {x["pid"] for x in (await db.get_setting("site_queue", []) or [])} if pf == "web" else set()
    lines, ids = [], []
    for s in rows:
        d, p = s["dt"].date(), s["post"]
        near = (d - today).days <= 1
        if _planned(s, pf, ig):
            ids.append(p["id"])
            lines.append((d, _line(s, pf, len(ids)), True))
        elif p and p["status"] == "published" and d == today and role(p, pf, ig):
            mark = "✅"
            if pf == "ig":
                mark = IG_ICON.get(igs.get(p["id"], ""), "⏳" if ig else "—")
            elif pf == "web" and p["id"] in queue:
                mark = "⚠️"
            lines.append((d, _line(s, pf, None, mark), False))
        elif not p and not s["past"] and near and slot_owner(s["fmt"]) == pf:
            lines.append((d, f"⏭ {s['dt']:%H:%M} · пропуск" if s["skipped"] else f"⚪️ {s['dt']:%H:%M} · свободно",
                          False))
    return lines, ids


def _fit(head: list[str], body: list[tuple[object, str, bool]], foot: list[str], total: int) -> tuple[str, int]:
    """Собирает подпись в пределах BUDGET построчно; что не влезло — строкой «и ещё N»."""
    def size(xs):
        return formatter.visible_len("\n".join(xs))
    fixed = size(head + [""] + foot) + 2
    out, cur_day, shown = [], None, 0
    for d, text, numbered in body:
        add = (([""] if out else []) + [f"<b>{day_title(d)}</b>"]) if d != cur_day else []
        if fixed + size(out + add + [text]) + 60 > BUDGET:
            break
        out += add + [text]
        cur_day = d
        shown += numbered
    left = total - shown
    if left > 0:
        out += ["", f"<i>… и ещё {left} в плане — листай ◀ ▶ в карточке поста</i>"]
    return "\n".join(head + ([""] + out if out else []) + [""] + foot), shown


async def view(arg: dict):
    pf = arg.get("pf") if arg.get("pf") in PF else "tg"
    rows, ig = await slot_rows(), await ig_on()
    body, ids = await _body(pf, rows, ig)
    head, foot, extra = await {"tg": _tg, "ig": _ig, "web": _web}[pf](ig)
    if arg.get("note"):
        head.append(f"<b>{html.escape(arg['note'])}</b>")
    if not body:
        body_note = ["", "<i>В плане пусто.</i>"]
        text, shown = "\n".join(head + body_note + [""] + foot), 0
    else:
        text, shown = _fit(head, body, foot, len(ids))
    nums = [screen.btn(str(i + 1), f"h:pl:{pf}:{pid}") for i, pid in enumerate(ids[:shown])]
    kb = [nums[i:i + 6] for i in range(0, len(nums), 6)] + extra + [[screen.btn("🏠 Пульт", "h:home")]]
    return screen.banner(), text, screen._kb(kb), arg


def _slot_times(pf: str) -> str:
    return ", ".join(f"{h:02d}:{m:02d}" for h, m, f in config.SLOTS if slot_owner(f) == pf) or "нет"


async def _tg(ig: bool):
    box, st = await inbox("tg"), await stock("tg")
    with_text = sum(1 for p in st if p["format"] != "std" or formatter.has_body(json.loads(p["data"])))
    news = sum(1 for p in st if json.loads(p["data"]).get("kind") == "news")
    head = [f"<b>✈️ Канал</b> — что выйдет в Telegram",
            f"Посты с текстом, новости и #ahmagnotes. Слоты: {_slot_times('tg')}."]
    foot = [f"📥 Ждут решения: {len(box)}",
            f"📦 В запасе: {len(st)} · с готовым текстом {with_text}" + (f" · новостей {news}" if news else "")]
    extra = [[screen.btn(f"📥 Ждут · {len(box)}", "h:inbox:tg"), screen.btn(f"📦 Запас · {len(st)}", "h:stock:tg")],
             [screen.btn("▶️ Предложить пост", "h:next:std:tg"), screen.btn("🗓 План на день", "h:plan")]]
    return head, foot, extra


async def _ig(ig: bool):
    from app import instagram
    box, st = await inbox("ig"), await stock("ig")
    try:
        icon = await instagram.status_icon()
    except Exception:
        icon = "…"
    word = {"✅": "работает", "⚠️": "есть проблема", "⏸": "автопостинг выключен",
            "⚙️": "не настроен", "…": "ещё не проверялся"}.get(icon, "")
    head = [f"<b>📸 Инста</b> — что выйдет в Instagram · {icon} {word}"]
    if config.MINI_IG:
        head.append(f"Свои фото-посты в слоты {_slot_times('ig')}; ↪ — копия поста канала с английской подписью.")
    else:
        head.append("Сюда уходят копии постов канала (↪) с английской подписью.")
    if not ig:
        head.append("<b>Пока Instagram не работает, сюда ничего не уйдёт.</b>")
    q = await ig_queue()
    foot = []
    sending, failed = q.get("queued", 0) + q.get("publishing", 0), q.get("failed", 0)
    if sending or failed:
        foot.append(f"⏳ Отправляются: {sending}" + (f" · ⚠️ не ушли: {failed}" if failed else ""))
    reels_n = 0
    try:
        from app import reels
        reels_n = len(await reels.items("ready"))
    except Exception:
        log.warning("Инста: рилсы", exc_info=True)
    if reels_n:
        foot.append(f"🎬 Рилсы готовы: {reels_n} — выкладываешь сам")
    if config.MINI_IG:
        foot.append(f"📥 Ждут решения: {len(box)} · 📦 Фото-постов в запасе: {len(st)}")
    extra = []
    if config.MINI_IG:
        extra.append([screen.btn(f"📥 Ждут · {len(box)}", "h:inbox:ig"), screen.btn(f"📦 Запас · {len(st)}", "h:stock:ig")])
        extra.append([screen.btn("▶️ Предложить фото-пост", "h:next:mini:ig"),
                      screen.btn("🎬 Рилсы" + (f" · {reels_n}" if reels_n else ""), "rl:home")])
    else:
        extra.append([screen.btn("🎬 Рилсы" + (f" · {reels_n}" if reels_n else ""), "rl:home")])
    if failed:
        extra.append([screen.btn(f"🔁 Повторить неудачные · {failed}", "ig:retryall")])
    extra.append([screen.btn("⚙️ Настройки Instagram" + (f" {icon}" if icon != "⚙️" else ""), "ig:home")])
    return head, foot, extra


async def _web(ig: bool):
    on = await sitepub.enabled()
    state = "✅ включён" if on else ("⏸ выключен" if sitepub.configured() else "⚙️ не настроен")
    host = sitepub.SITE_URL.replace("https://", "").rstrip("/")
    head = [f"<b>🌐 Сайт</b> · {html.escape(host)} · {state}",
            "Всё из канала, кроме подборок; пост для канала — с продолжением текста."]
    if not on:
        head.append("<b>Пока сайт выключен, сюда ничего не уйдёт.</b>")
    q = await db.get_setting("site_queue", []) or []
    foot = []
    try:
        waiting = len(sitepub.pending_updates(sitepub._state())) if sitepub.sitebuild is not None else 0
    except Exception:
        waiting = 0
    if q or waiting:
        foot.append(f"⚠️ Ждут повтора: {len(q)}" + (f" · правки ждут выкладки: {waiting}" if waiting else ""))
    try:
        from app import archive
        st = archive.load_state()
        if st:
            c, s, igs = archive.ctl(), st["site"], st["ig"]
            if st["phase"] == "done":
                foot.append(f"🗄 Архив разобран: из Instagram +{igs['imported']} · скрыто {s['hidden']}")
            else:
                foot.append(f"🗄 Архив: {archive.PHASES.get(st['phase'], st['phase'])}"
                            + (" · ⏸ пауза" if c.get("paused") else "")
                            + f" · ${archive.spent(st):.2f} из ${c['cap']:.0f}")
    except Exception:
        log.warning("Сайт: архив для экрана", exc_info=True)
    extra = []
    if q:
        extra.append([screen.btn(f"🔁 Повторить · {len(q)}", "site:retry")])
    extra.append([screen.btn("🗄 Архив", "ar:show"), screen.btn("⚙️ Настройки сайта", "site:home")])
    extra.append([InlineKeyboardButton(text=f"🔗 {host}", url=sitepub.SITE_URL)])
    return head, foot, extra
