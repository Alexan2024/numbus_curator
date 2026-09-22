"""Пост по запросу, выбор любого слота, отложенные запросы, «ещё кадры».

Запрос. Пишешь боту название (фильм, здание, художник…) или присылаешь фото с подписью. Карточка запроса:
куда поставить (входящие / ближайший слот / любой слот, даже занятый), формат (большой / мини / #ahmagnotes),
«🕓 Потом». Дальше короткий поиск (Sonnet + веб-поиск) → черновик: что нашёл и по каким источникам →
«✅ Собирать» → фото: свои, кадры TMDB для кино, фото со страниц, Wikimedia Commons → оценка и текст, как у
поста по ссылке → входящие или слот.

Занятый слот. Стоявший там одобренный пост переезжает в следующий свободный слот своего формата,
автопост к этому слоту — во входящие.

Отложенные («🕓 Потом», «потом: …», /later …) копятся в списке. После каждого сбора, если запас меньше нормы,
бот собирает в запас один отложенный пост сам (без черновика) и присылает уведомление.

«🔄 Ещё кадры» в разделе фото карточки: докачивает новые фото (TMDB, страницы, Commons), без повторов
того, что уже есть, и даёт выбрать, какие добавить.

Подключается из app/__init__.py после загрузки app.bot: хэндлеры регистрируются на его router,
кнопки добавляются к экрану обёртками. Сами bot.py, screen.py, pipeline.py не менялись."""
import asyncio
import html
import io
import itertools
import json
import logging
import re
import shutil
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from aiogram import Bot, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from PIL import Image

from app import commons, config, curator, db, formatter, media, pipeline, screen, slots, tmdb

log = logging.getLogger(__name__)

BUMP_MIN_LEAD = 2          # минут до слота: ближе — берём следующий
PHOTO_WAIT = 1.5           # секунд ждём остальные фото альбома
PENDING_TTL = 50           # сколько последних запросов помнить
MORE_MAX = 9               # сколько новых кадров показывать за раз

FORMATS = {"std": "большой", "mini": "мини", "notes": "#ahmagnotes"}
NEXT_FMT = {"std": "mini", "mini": "notes", "notes": "std"}
LATER_RE = re.compile(r"^\s*(?:/later|/потом|потом)\s*[:\-—]?\s+(.+)$", re.I | re.S)

_bot_mod = None            # app.bot — берём оттуда _list, _drop, _ask
_bot: Bot | None = None    # для уведомлений из фоновых задач
_pending: dict[str, dict] = {}
_albums: dict[str, dict] = {}
_more: dict[int, dict] = {}
_ids = itertools.count(1)
_wl_lock = asyncio.Lock()
_attached = False


class Req(StatesGroup):
    query = State()
    fix = State()


def btn(text: str, data: str):
    return screen.btn(text, data)


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================= ближайший слот, даже занятый =======================

async def nearest_key() -> str | None:
    """Ближайший будущий слот любого формата, кроме пропущенных и тех, до которых меньше пары минут."""
    sk = await slots.skipped()
    edge = slots._now() + timedelta(minutes=BUMP_MIN_LEAD)
    for dt, _ in slots.upcoming(None, 20):
        key = slots.key_of(dt)
        if dt > edge and key not in sk:
            return key
    return None


async def slot_choices(limit_today_min: int = 3) -> list[dict]:
    """Оставшиеся слоты сегодня (если их меньше трёх — плюс завтрашние): ключ, формат и кто там стоит."""
    sk = await slots.skipped()
    edge = slots._now() + timedelta(minutes=BUMP_MIN_LEAD)
    today = slots._now().date()
    out = [{"key": slots.key_of(dt), "fmt": f, "dt": dt} for dt, f in slots.upcoming(None, 20)
           if dt > edge and slots.key_of(dt) not in sk]
    todays = [c for c in out if c["dt"].date() == today]
    if len(todays) >= limit_today_min:
        out = todays
    else:
        out = todays + [c for c in out if c["dt"].date() == today + timedelta(days=1)]
    rows = await db.posts_in_slots([c["key"] for c in out])
    for c in out:
        here = [r for r in rows if r["slot_key"] == c["key"] and r["status"] in ("approved", "announced")]
        c["post"] = here[0] if here else None
    return out


def slot_label(c: dict, short: bool = False) -> str:
    t = f"{c['dt']:%H:%M}" if c["dt"].date() == slots._now().date() else f"завтра {c['dt']:%H:%M}"
    fmt = "мини" if c["fmt"] == "mini" else "большой"
    if c["post"]:
        return f"⤵ {t} · вместо «{slots.headline(c['post'], 18 if short else 24)}»"
    return f"⚪️ {t} · свободен · {fmt}"


async def place(pid: int, key: str) -> str:
    """Ставит пост в слот key. Занявший его пост сдвигается. → строка для экрана."""
    if slots.key_dt(key) <= slots._now():
        raise RuntimeError("этот слот уже прошёл — выбери другой")
    moved = []
    for p in await db.posts_in_slots([key]):
        if p["id"] == pid:
            continue
        if p["status"] == "announced":          # автопост к этому слоту — во входящие
            await db.update_post(p["id"], status="sent", slot_key=None, sent_at=db.now())
            moved.append(f"«{slots.headline(p, 30)}» → во входящие")
        elif p["status"] == "approved":
            fmt = p["format"] if p["format"] in ("std", "mini") else "std"
            nk = await slots.next_free(fmt)     # сам key ещё занят этим постом, поэтому next_free его не вернёт
            await db.update_post(p["id"], slot_key=nk)
            moved.append(f"«{slots.headline(p, 30)}» → {slots.human_key(nk) if nk else 'первый свободный слот'}")
    post = await db.get_post(pid)
    if post["format"] == "std" and not formatter.has_body(json.loads(post["data"])):
        await pipeline.ensure_text(pid)
    await db.update_post(pid, status="approved", decided_at=db.now(), slot_key=key)
    note = f"Выйдет {slots.human_key(key)}"
    if await slots.paused():
        note += " (сейчас пауза)"
    if moved:
        note += ". Сдвинут: " + "; ".join(moved)
    return note


async def bump(pid: int) -> str:
    """В ближайший слот, даже занятый."""
    key = await nearest_key()
    if not key:
        raise RuntimeError("впереди нет ни одного слота — проверь SLOTS и пропуски")
    return "⚡️ " + await place(pid, key)


REQUEST_SYSTEM = """Ты помогаешь редактору Telegram-канала AHMAG (архитектура, интерьеры, искусство, фотография, архивы, кино) собрать материал для поста по запросу автора. Автор пишет коротко: название фильма, здания, имя художника или фотографа, иногда с уточнением. Найди в интернете, о чём именно речь, и собери проверенные факты для поста в один-два абзаца.
Только то, что нашёл в источниках: даты, имена и цифры — лишь подтверждённые. Факты формулируй по-русски, своими словами."""

REQUEST_FORMAT = """{
  "status": "ok|ambiguous|not_found",
  "options": [{"label": "коротко по-русски: что это", "query": "уточнённый запрос"}],
  "title": "название объекта в оригинале",
  "subject": "одной фразой: что это (например: фильм Джузеппе Торнаторе, Италия, 1988)",
  "category": "architecture|art|photography|archive|cinema",
  "kind": "film|tv|other",
  "tmdb": {"title": "оригинальное или английское название", "year": 1988},
  "facts": [{"text": "проверенный факт", "url": "откуда"}],
  "sources": [{"title": "название страницы", "url": "https://..."}],
  "page_urls": ["страницы с крупными фото именно этого объекта, до 3"],
  "image_queries": ["запросы на английском для Wikimedia Commons, 2–4"]
}"""


async def research(query: str, has_photos: bool) -> dict:
    prompt = (
        f"Запрос автора: {query}\n"
        + ("Автор приложил свои фото — искать фото не нужно, page_urls и image_queries оставь пустыми.\n" if has_photos else "")
        + "\n1. Определи, о чём запрос. Если подходят несколько разных объектов и из запроса не понять, какой нужен, "
        "верни status «ambiguous» и 2–4 варианта в options. Если ничего не нашлось — status «not_found».\n"
        "2. Собери 5–10 фактов для поста: кто, когда, где, как сделано, чем интересно. К каждому — url страницы.\n"
        "3. page_urls — до 3 страниц, где есть крупные качественные фото именно этого объекта "
        "(профильные издания, сайты бюро, музеи, киноархивы, Criterion, MUBI и т. п.).\n"
        "4. Для фильма или сериала заполни tmdb: название и год выхода. Для остального tmdb — null.\n"
        "5. image_queries — 2–4 запроса для Wikimedia Commons (для кино можно оставить пустым).\n\n"
        f"Верни ТОЛЬКО JSON:\n{REQUEST_FORMAT}"
    )
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    try:
        brief = await curator._call(prompt, system=REQUEST_SYSTEM, model=config.CLAUDE_MODEL,
                                    max_tokens=3000, tools=tools)
    except (curator.NoCredits, curator.ApiDown):
        raise
    except Exception as exc:
        log.warning("Запрос: веб-поиск не сработал (%r), собираю без него", exc)
        brief = await curator._call(prompt + "\n\nВеб-поиск недоступен: опирайся только на то, в чём уверен, "
                                    "url оставляй пустыми.", system=REQUEST_SYSTEM, model=config.CLAUDE_MODEL,
                                    max_tokens=2500)
        brief["_no_search"] = True
    brief["query"] = query
    return brief


def _brief_text(brief: dict) -> str:
    facts = "\n".join(f"- {f.get('text', '')}" + (f" ({f['url']})" if f.get("url") else "")
                      for f in brief.get("facts") or [] if f.get("text"))
    srcs = "\n".join(f"- {s.get('title') or ''} {s.get('url') or ''}".strip()
                     for s in (brief.get("sources") or [])[:8] if s.get("url"))
    return (f"Пост по запросу автора канала: «{brief.get('query', '')}».\n"
            f"Объект: {brief.get('title', '')} — {brief.get('subject', '')}\n\n"
            f"Факты (собраны по источникам):\n{facts or '—'}\n\nИсточники:\n{srcs or '—'}")


async def _old_post_note(cid: int) -> tuple[int | None, str | None]:
    """Уже был пост из этого материала? → (id, пояснение) или (None, None)."""
    old = await db.post_by_candidate(cid)
    if not old:
        return None, None
    st = old["status"]
    if st in ("ready", "sent", "announced"):
        return old["id"], "уже был в запасе или во входящих — открываю его"
    if st == "approved":
        return old["id"], "этот материал уже стоит в слоте"
    if st == "published":
        return None, "этот материал уже выходил в канале"
    return None, None


# ======================= сборка поста из найденного =======================

def _year(t: dict) -> int | None:
    try:
        return int(t.get("year")) if t.get("year") else None
    except (TypeError, ValueError):
        return None


async def _film(client: httpx.AsyncClient, brief: dict) -> dict | None:
    if brief.get("kind") in ("film", "tv") and brief.get("tmdb") and tmdb.configured():
        t = brief["tmdb"] or {}
        return await tmdb.find(client, t.get("title") or brief.get("title", ""), _year(t), brief.get("kind"))
    return None


async def _gather_urls(client: httpx.AsyncClient, req: dict, film: dict | None, want: int = 6,
                       tmdb_limit: int = 14) -> list[str]:
    urls: list[str] = []
    if film:
        urls += await tmdb.images(client, film, limit=tmdb_limit)
    if len(urls) < want:
        for page in (req.get("page_urls") or [])[:3]:
            try:
                urls += (await media.extract_article(client, page))["image_urls"][:12]
            except Exception as exc:
                log.info("Запрос: страница без фото (%s): %s", exc, page)
    if len(urls) < want and req.get("kind") not in ("film", "tv"):
        for q in (req.get("image_queries") or [])[:4]:
            urls += await commons.search(client, q)
    return list(dict.fromkeys(urls))


async def assemble(bot: Bot | None, query: str, brief: dict, photo_ids: list[str], fmt: str,
                   status_cb) -> tuple[int | None, str]:
    """Найденное → пост в запасе. → (id поста, пояснение)."""
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}, follow_redirects=True) as client:
        film = await _film(client, brief)
        srcs = [s.get("url") for s in brief.get("sources") or [] if s.get("url")]
        url = (film or {}).get("url") or (srcs[0] if srcs else
               f"https://en.wikipedia.org/wiki/Special:Search?search={quote(brief.get('title') or query)}")
        title = brief.get("title") or query

        await db.add_candidate(url, "request", title, {"query": query})
        cand = await db.get_candidate_by_url(url)
        cid = cand["id"]
        old_id, why = await _old_post_note(cid)
        if old_id or why:
            return old_id, why
        await db.update_candidate(cid, source="request", title=title, status="request", note="собирается по запросу")

        folder = config.IMG_DIR / f"c{cid}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        await status_cb(f"🖼 Собираю фото: <b>{html.escape(title)}</b>")
        images: list[Path] = []
        tried: list[str] = []
        if photo_ids and bot:
            for n, fid in enumerate(photo_ids[:config.EVAL_PHOTOS]):
                dst = folder / f"own{n:02d}.jpg"
                try:
                    await bot.download(fid, destination=dst)
                    images.append(dst)
                except Exception:
                    log.warning("Запрос: своё фото %s не скачалось", n + 1, exc_info=True)
        else:
            tried = (await _gather_urls(client, brief, film))[:40]
            if tried:
                images = await media.download_images(client, tried, folder)
        for extra in images[config.EVAL_PHOTOS:]:
            Path(extra).unlink(missing_ok=True)
        images = images[:config.EVAL_PHOTOS]

    if not images:
        shutil.rmtree(folder, ignore_errors=True)
        await db.mark_candidate(cid, "skipped", "по запросу: фото не нашлись")
        tip = "Кадров не нашлось" if brief.get("kind") in ("film", "tv") else "Качественных фото не нашлось"
        return None, f"{tip}. Пришли свои фото с подписью «{query}» — соберу пост на них"

    prep = {"title": title, "text": _brief_text(brief)[:6000], "images": [str(p) for p in images], "allow_std": True}
    await db.update_candidate(cid, prep=prep)
    cand = await db.get_candidate(cid)
    await status_cb(f"✍️ Пишу пост: <b>{html.escape(title)}</b>")
    data = await curator.evaluate(pipeline._params(await curator.eval_context(), cand, prep, forced=True),
                                  background=False)
    flags = list(data.get("flags") or [])
    if brief.get("_no_search"):
        flags.append("собрано без веб-поиска — проверь факты")
    if brief.get("kind") in ("film", "tv") and not photo_ids and not film:
        flags.append("кадры со страниц" + ("" if tmdb.configured() else ", TMDB не подключён"))
    data["flags"] = flags
    pid = await pipeline.finish(cid, data, forced=True)
    if not pid:
        return None, "не получилось собрать пост"
    # откуда брать «ещё кадры»
    post = await db.get_post(pid)
    pdata = json.loads(post["data"])
    pdata["_req"] = {"kind": brief.get("kind"), "tmdb": film, "page_urls": (brief.get("page_urls") or [])[:3],
                     "image_queries": (brief.get("image_queries") or [])[:4]}
    pdata["_seen_urls"] = tried
    await db.update_post(pid, data=pdata)
    want = "mini" if fmt == "mini" else "std"
    if post["format"] != want:
        await pipeline.set_format(pid, want, write=False)
    if want == "std":
        await pipeline.ensure_text(pid)
    return pid, "ok"


# ======================= карточка запроса =======================

def _remember(query: str, photos: list[str], user_msgs: list[int], fmt: str = "std", wl: str | None = None) -> str:
    tok = str(next(_ids))
    _pending[tok] = {"q": query.strip()[:300], "photos": photos, "msgs": user_msgs, "fmt": fmt,
                     "target": None, "brief": None, "wl": wl}
    for old in list(_pending)[:-PENDING_TTL]:
        _pending.pop(old, None)
    return tok


def _target_human(target: str | None) -> str:
    if not target:
        return "во входящие"
    if target == "nearest":
        return "в ближайший слот"
    return f"в слот {slots.human_key(target)}"


async def _card(bot: Bot, tok: str, edit: Message | None = None) -> None:
    it = _pending[tok]
    text = (f"<b>Пост по запросу</b>\n«{html.escape(it['q'])}»"
            + (f"\nСвоих фото: {len(it['photos'])}" if it["photos"] else "")
            + "\n\nКуда поставить? Сначала бот найдёт материал и покажет, что нашёл.")
    rows = []
    if it["fmt"] == "notes":
        text = (f"<b>Заметка #ahmagnotes по запросу</b>\n«{html.escape(it['q'])}»\n\n"
                "Бот соберёт материал и пришлёт план на утверждение, как обычно у заметок.")
        rows.append([btn("📝 Собрать материал", f"rq:go:{tok}")])
    else:
        key = await nearest_key()
        rows.append([btn("📥 Во входящие", f"rq:go:{tok}")])
        rows.append([btn(f"⚡️ Ближайший слот{' · ' + slots.human_key(key) if key else ''}", f"rq:now:{tok}")])
        rows.append([btn("🗓 Выбрать слот, даже занятый", f"rq:slots:{tok}")])
    rows.append([btn(f"Формат: {FORMATS[it['fmt']]} ▸", f"rq:fmt:{tok}")])
    rows.append([btn("🕓 Потом", f"rq:later:{tok}"), btn("✕ Не надо", f"rq:x:{tok}")])
    if edit:
        await edit.edit_text(text, reply_markup=kb(rows))
    else:
        await bot.send_message(config.ADMIN_ID, text, reply_markup=kb(rows))


async def _slots_card(msg: Message, tok: str) -> None:
    it = _pending[tok]
    rows = [[btn(slot_label(c), f"rq:at:{tok}:{slots.enc(c['key'])}")] for c in await slot_choices()]
    rows.append([btn("← Назад", f"rq:back:{tok}")])
    await msg.edit_text(f"<b>Пост по запросу</b>\n«{html.escape(it['q'])}»\n\n"
                        "Куда поставить? Если слот занят, стоявший там пост переедет в следующий свободный слот "
                        "своего формата, автопост — во входящие.", reply_markup=kb(rows))


def _draft_text(it: dict) -> str:
    b = it["brief"]
    facts = [f for f in b.get("facts") or [] if f.get("text")]
    srcs = [s for s in b.get("sources") or [] if s.get("url")][:4]
    lines = [f"<b>Нашёл:</b> {html.escape(b.get('title') or it['q'])}"]
    if b.get("subject"):
        lines.append(f"<i>{html.escape(b['subject'])}</i>")
    lines.append("")
    lines += [f"• {html.escape(f['text'][:140])}" for f in facts[:3]]
    if len(facts) > 3:
        lines.append(f"…и ещё фактов: {len(facts) - 3}")
    if srcs:
        lines.append("\n<b>Источники</b>")
        lines += [f'• <a href="{html.escape(s["url"])}">{html.escape((s.get("title") or s["url"])[:60])}</a>'
                  for s in srcs]
    photos = ("свои: " + str(len(it["photos"]))) if it["photos"] else (
        "кадры TMDB" if b.get("kind") in ("film", "tv") and tmdb.configured() else "со страниц и Commons")
    lines.append(f"\nФото: {photos} · формат: {FORMATS[it['fmt']]} · {_target_human(it['target'])}")
    if b.get("_no_search"):
        lines.append("⚠️ Веб-поиск не сработал — факты по памяти модели, их нужно проверить.")
    return "\n".join(lines)[:4000]


async def _status(msg: Message, text: str, rows=None) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb(rows) if rows else None, disable_web_page_preview=True)
    except Exception:
        pass


async def _find(bot: Bot, msg: Message, tok: str) -> None:
    """Поиск → варианты, «ничего не нашлось» или черновик на подтверждение."""
    it = _pending.get(tok)
    if not it:
        return
    await _status(msg, f"🔎 Ищу: <b>{html.escape(it['q'])}</b>\nОбычно это минута.")
    try:
        brief = await research(it["q"], bool(it["photos"]))
    except Exception as exc:
        log.exception("поиск по запросу")
        return await _status(msg, f"Не получилось. {curator.explain(exc)}",
                             [[btn("🔁 Ещё раз", f"rq:retry:{tok}"), btn("✕", f"rq:x:{tok}")]])
    it["brief"] = brief
    if brief.get("status") == "ambiguous" and brief.get("options"):
        it["options"] = [o for o in brief["options"] if o.get("query")][:4]
        rows = [[btn(str(o.get("label") or o["query"])[:60], f"rq:opt:{tok}:{i}")] for i, o in enumerate(it["options"])]
        rows.append([btn("✏️ Уточнить сам", f"rq:fix:{tok}"), btn("✕", f"rq:x:{tok}")])
        return await _status(msg, f"«{html.escape(it['q'])}» — что именно?", rows)
    if brief.get("status") == "not_found" and not it["photos"]:
        return await _status(msg, f"По запросу «{html.escape(it['q'])}» ничего не нашлось. "
                                  "Уточни: год, автор, город.",
                             [[btn("✏️ Уточнить", f"rq:fix:{tok}"), btn("✕", f"rq:x:{tok}")]])
    await _status(msg, _draft_text(it), [
        [btn("✅ Собирать", f"rq:ok:{tok}")],
        [btn("✏️ Не то — уточнить", f"rq:fix:{tok}"), btn("✕", f"rq:x:{tok}")]])


async def _build(bot: Bot, msg: Message, tok: str) -> None:
    it = _pending.get(tok)
    if not it or not it.get("brief"):
        return

    async def status(text: str):
        await _status(msg, text)

    try:
        pid, note = await assemble(bot, it["q"], it["brief"], it["photos"], it["fmt"], status)
    except Exception as exc:
        log.exception("пост по запросу")
        return await _status(msg, f"Не получилось. {curator.explain(exc)}",
                             [[btn("🔁 Ещё раз", f"rq:ok:{tok}"), btn("✕", f"rq:x:{tok}")]])
    if not pid:
        return await _status(msg, f"Пост не собрался: {html.escape(note)}")

    await _bot_mod._drop(bot, msg.message_id, *it.get("msgs", []))
    _pending.pop(tok, None)
    if it.get("wl"):
        await _wl_remove(it["wl"])
    target = it["target"]
    post = await db.get_post(pid)
    if target and post["status"] in ("ready", "sent", "approved"):
        try:
            done = await (bump(pid) if target == "nearest" else place(pid, target))
        except Exception as exc:
            log.exception("слот для поста по запросу")
            try:     # слот успел пройти, пока собирался пост, — тогда в ближайший
                done = await bump(pid)
            except Exception:
                done = f"В слот не встал: {curator.explain(exc)}"[:200]
                await slots.propose(pid, None)
                return await screen.move_down(bot, "list", mode="inbox", pid=pid, note=done)
        return await screen.move_down(bot, "list", mode="sched", pid=pid, note=done[:200])
    if post["status"] == "ready":
        await slots.propose(pid, None)
    mode = "sched" if post["status"] == "approved" else "inbox"
    await screen.move_down(bot, "list", mode=mode, pid=pid, note=None if note == "ok" else note)


# ======================= отложенные запросы =======================

async def wishlist() -> list[dict]:
    return list(await db.get_setting("wishlist", []))


async def _wl_save(items: list[dict]) -> None:
    await db.set_setting("wishlist", items[-60:])


async def wl_add(query: str, fmt: str = "std") -> int:
    items = await wishlist()
    q = query.strip()[:300]
    if not any(i["q"].lower() == q.lower() for i in items):
        items.append({"id": f"w{int(time.time() * 1000)}", "q": q, "fmt": fmt, "added": db.now(), "note": ""})
        await _wl_save(items)
    return len(items)


async def _wl_remove(wid: str) -> None:
    await _wl_save([i for i in await wishlist() if i["id"] != wid])


async def _wl_view(bot: Bot, msg: Message | None = None, note: str = "") -> None:
    items = await wishlist()
    lines = ["<b>🕓 Отложенные запросы</b>"]
    if note:
        lines.append(f"<b>{html.escape(note)}</b>")
    if not items:
        lines.append("\nПусто. Отложить можно кнопкой «🕓 Потом» в карточке запроса или сообщением "
                     "«потом: название».")
    else:
        lines.append("Когда запас меньше нормы, бот после сбора сам собирает в запас один пост отсюда.\n")
        for n, i in enumerate(items[:15]):
            extra = f" · {FORMATS.get(i.get('fmt'), '')}" if i.get("fmt") != "std" else ""
            warn = f" — ⚠️ {html.escape(i['note'])}" if i.get("note") else ""
            lines.append(f"{n + 1}. {html.escape(i['q'])}{extra}{warn}")
    rows = [[btn(f"▶ {n + 1}", f"rq:wlgo:{i['id']}"), btn(f"✕ {n + 1}", f"rq:wldel:{i['id']}")]
            for n, i in enumerate(items[:15])]
    rows = [sum(rows[k:k + 3], []) for k in range(0, len(rows), 3)]
    rows.append([btn("Закрыть", "rq:wlclose")])
    text = "\n".join(lines)[:4000]
    if msg:
        await _status(msg, text, rows)
    else:
        await bot.send_message(config.ADMIN_ID, text, reply_markup=kb(rows))


async def wishlist_tick() -> None:
    """После сбора: запас меньше нормы → один отложенный пост в запас, без черновика."""
    if _wl_lock.locked():
        return
    async with _wl_lock:
        try:
            if await db.count_ready() >= pipeline.target_stock():
                return
        except Exception:
            return
        items = [i for i in await wishlist() if i.get("fmt") != "notes" and not i.get("note")]
        if not items:
            return
        item = items[0]
        try:
            brief = await research(item["q"], False)
            if brief.get("status") != "ok" or not brief.get("facts"):
                why = "неоднозначно — выбери вариант" if brief.get("status") == "ambiguous" else "ничего не нашлось"
                raise LookupError(why)

            async def quiet(_t):
                return None

            pid, note = await assemble(None, item["q"], brief, [], item.get("fmt", "std"), quiet)
            if not pid:
                raise LookupError(note)
        except (curator.NoCredits, curator.ApiDown, curator.BudgetExceeded):
            return
        except Exception as exc:
            reason = str(exc) if isinstance(exc, LookupError) else "не собрался"
            log.info("Отложенный «%s»: %s", item["q"], exc)
            items_all = await wishlist()
            for i in items_all:
                if i["id"] == item["id"]:
                    i["note"] = reason[:80]
            await _wl_save(items_all)
            if _bot:
                await screen.notify(_bot, f"🕓 Отложенный «{item['q']}» сам не собрался: {reason}. "
                                          "Запусти его вручную.", [("🕓 Отложенные", "rq:wl")])
            return
        await _wl_remove(item["id"])
        if _bot:
            post = await db.get_post(pid)
            await screen.notify(_bot, f"🕓 Из отложенного собран пост в запас: {slots.headline(post, 60)}",
                                [("👁 Открыть", f"n:open:{pid}")])
            screen.refresh_soon(_bot)


# ======================= ещё кадры =======================

def _hash(path) -> int | None:
    try:
        with Image.open(path) as im:
            return media._ahash(im)
    except Exception:
        return None


async def more_photos(pid: int) -> list[Path]:
    """Новые кадры для поста: без уже виденных адресов и без повторов того, что уже есть в посте."""
    post = await db.get_post(pid)
    data = json.loads(post["data"])
    images = [p for p in json.loads(post["images"] or "[]") if Path(p).exists()]
    if not images:
        raise RuntimeError("у поста нет фото на диске")
    folder = Path(images[0]).parent
    req = dict(data.get("_req") or {})
    seen = set(data.get("_seen_urls") or [])
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}, follow_redirects=True) as client:
        film = req.get("tmdb")
        if not req:
            hp = [x for x in (data.get("headline_parts") or []) if x and x != "null"]
            if post["category"] == "cinema" and hp and tmdb.configured():
                film = await tmdb.find(client, str(hp[0]), None, "film")
            if (post["url"] or "").startswith("http"):
                req["page_urls"] = [post["url"]]
            req["kind"] = "film" if film else "other"
        urls = await _gather_urls(client, req, film, want=40, tmdb_limit=40)
        fresh = [u for u in urls if u not in seen][:30]
        data["_seen_urls"] = list(seen | set(fresh))
        await db.update_post(pid, data=data)
        if not fresh:
            return []
        tmp = folder / f"more_{int(time.time())}"
        got = await media.download_images(client, fresh, tmp)
    have = [h for h in (await asyncio.to_thread(lambda: [_hash(p) for p in images])) if h is not None]
    out = []
    for p in got:
        h = _hash(p)
        if h is None or any(bin(h ^ x).count("1") <= 5 for x in have):
            p.unlink(missing_ok=True)
            continue
        have.append(h)
        out.append(p)
        if len(out) >= MORE_MAX:
            break
    for p in got:
        if p not in out:
            Path(p).unlink(missing_ok=True)
    return out


def _more_kb(pid: int) -> InlineKeyboardMarkup:
    m = _more[pid]
    nums = [btn(("✅ " if i in m["sel"] else "") + f"+{i + 1}", f"rq:mt:{pid}:{i}") for i in range(len(m["files"]))]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    rows.append([btn(f"Добавить выбранные · {len(m['sel'])}", f"rq:ma:{pid}"), btn("Все", f"rq:mall:{pid}")])
    rows.append([btn("🔄 Ещё", f"rq:more:{pid}"), btn("✕ Не надо", f"rq:mx:{pid}")])
    return kb(rows)


async def _more_clear(bot: Bot, pid: int, keep_files: set[str] = frozenset()) -> None:
    m = _more.pop(pid, None)
    if not m:
        return
    await _bot_mod._drop(bot, *m["msgs"])
    for f in m["files"]:
        if str(f) not in keep_files:
            Path(f).unlink(missing_ok=True)


async def _more_show(bot: Bot, pid: int) -> None:
    await _more_clear(bot, pid)
    wait = await bot.send_message(config.ADMIN_ID, "🔄 Ищу ещё кадры…")
    try:
        files = await more_photos(pid)
    except Exception as exc:
        log.exception("ещё кадры")
        return await _status(wait, f"Не получилось: {curator.explain(exc)}"[:500], [[btn("OK", f"rq:mx:{pid}")]])
    if not files:
        _more[pid] = {"files": [], "sel": set(), "msgs": [wait.message_id]}
        return await _status(wait, "Новых кадров не нашлось — всё, что было в источниках, уже показано. "
                                   "Можно прислать свои фото с подписью.", [[btn("OK", f"rq:mx:{pid}")]])
    await _bot_mod._drop(bot, wait.message_id)
    if len(files) == 1:
        album = [await bot.send_photo(config.ADMIN_ID, FSInputFile(files[0]), caption="+1")]
    else:
        album = await bot.send_media_group(config.ADMIN_ID, [InputMediaPhoto(media=FSInputFile(f), caption=f"+{i + 1}")
                                                             for i, f in enumerate(files)])
    _more[pid] = {"files": files, "sel": set(), "msgs": [m.message_id for m in album]}
    ctl = await bot.send_message(config.ADMIN_ID, f"Новые кадры: {len(files)}. Отметь, какие добавить в пост.",
                                 reply_markup=_more_kb(pid))
    _more[pid]["msgs"].append(ctl.message_id)
    await screen.add_temp(_more[pid]["msgs"])


async def _more_add(bot: Bot, pid: int, idx: list[int]) -> None:
    m = _more.get(pid)
    post = await db.get_post(pid)
    if not m or not post:
        return
    chosen = [str(m["files"][i]) for i in sorted(idx) if i < len(m["files"])]
    images = json.loads(post["images"] or "[]") + chosen
    await db.update_post(pid, images=images)
    await _more_clear(bot, pid, keep_files=set(chosen))
    await _bot_mod._list(bot, pid=pid, kb="photos", note=f"Добавлено кадров: {len(chosen)} — они в конце и включены")


# ======================= сообщения =======================

async def on_query_text(msg: Message, state: FSMContext, bot: Bot):
    global _bot
    _bot = bot
    cur = await state.get_state()
    data = await state.get_data()
    await state.clear()
    if cur == Req.fix.state:           # уточнение к запросу
        tok = data.get("tok")
        await _bot_mod._drop(bot, msg.message_id, data.get("prompt"))
        it = _pending.get(tok)
        if not it:
            return await bot.send_message(config.ADMIN_ID, "Запрос устарел — напиши его заново.")
        it["q"] = f"{it['q']} — {msg.text.strip()}"[:300]
        wait = await bot.send_message(config.ADMIN_ID, "🔎 Ищу…")
        _pending[tok]["msgs"] = it.get("msgs", []) + ([data["card"]] if data.get("card") else [])
        return asyncio.create_task(_find(bot, wait, tok))
    m = LATER_RE.match(msg.text or "")
    if m:
        n = await wl_add(m.group(1))
        await _bot_mod._drop(bot, msg.message_id)
        return await screen.notify(bot, f"🕓 Отложил: «{m.group(1).strip()[:100]}». В списке: {n}",
                                   [("🕓 Отложенные", "rq:wl")])
    await _card(bot, _remember(msg.text, [], [msg.message_id]))


async def on_photo(msg: Message, state: FSMContext, bot: Bot):
    """Фото с подписью (или альбом) → пост на этих фото. Альбом приходит пачкой сообщений — ждём остальные."""
    global _bot
    _bot = bot
    await state.clear()
    gid = msg.media_group_id or f"single{msg.message_id}"
    g = _albums.setdefault(gid, {"photos": [], "caption": "", "msgs": [], "task": None})
    g["photos"].append(msg.photo[-1].file_id)
    g["msgs"].append(msg.message_id)
    if msg.caption:
        g["caption"] = msg.caption
    if g["task"]:
        g["task"].cancel()

    async def later():
        await asyncio.sleep(PHOTO_WAIT)
        _albums.pop(gid, None)
        if not g["caption"].strip():
            return await bot.send_message(config.ADMIN_ID, "Добавь к фото подпись — о чём пост, "
                                                           "например «Nuovo Cinema Paradiso». Пришли ещё раз с подписью.")
        await _card(bot, _remember(g["caption"], g["photos"], g["msgs"]))

    g["task"] = asyncio.create_task(later())


# ======================= кнопки =======================

async def on_cb(cb: CallbackQuery, state: FSMContext, bot: Bot):
    global _bot
    _bot = bot
    p = cb.data.split(":")
    action = p[1]

    # --- пульт и отложенные ---
    if action == "ask":
        await cb.answer()
        return await _bot_mod._ask(bot, state, Req.query,
                                   "О чём пост? Название фильма, здания, имя художника — можно с уточнением. "
                                   "Или пришли фото с подписью. /cancel — отмена.")
    if action == "wl":
        await cb.answer()
        return await _wl_view(bot, cb.message if cb.message and cb.message.text and "Отложенные" in cb.message.text else None)
    if action == "wlclose":
        await cb.answer()
        return await _bot_mod._drop(bot, cb.message.message_id)
    if action in ("wlgo", "wldel"):
        item = next((i for i in await wishlist() if i["id"] == p[2]), None)
        if not item:
            await cb.answer("Уже нет в списке")
            return await _wl_view(bot, cb.message)
        await cb.answer()
        if action == "wldel":
            await _wl_remove(item["id"])
            return await _wl_view(bot, cb.message, "Убрал")
        await _bot_mod._drop(bot, cb.message.message_id)
        return await _card(bot, _remember(item["q"], [], [], item.get("fmt", "std"), wl=item["id"]))

    # --- слоты на карточке поста ---
    if action in ("bump", "put"):
        pid = int(p[2])
        post = await db.get_post(pid)
        if not post or post["status"] not in ("ready", "sent", "approved"):
            return await cb.answer("Этот пост уже не ждёт решения", show_alert=True)
        await cb.answer("Ставлю в слот")
        try:
            note = await (bump(pid) if action == "bump" else place(pid, slots.dec(p[3])))
        except Exception as exc:
            log.exception("слот")
            return await _bot_mod._list(bot, pid=pid, note=curator.explain(exc)[:200])
        screen.refresh_soon(bot)
        return await _bot_mod._list(bot, mode="sched", pid=pid, note=note[:200])

    # --- ещё кадры ---
    if action in ("more", "mt", "ma", "mall", "mx"):
        pid = int(p[2])
        if action == "more":
            await cb.answer("Ищу кадры")
            return asyncio.create_task(_more_show(bot, pid))
        if action == "mx":
            await cb.answer()
            return await _more_clear(bot, pid)
        m = _more.get(pid)
        if not m:
            await cb.answer("Это устарело — нажми «🔄 Ещё кадры» снова", show_alert=True)
            return await _bot_mod._drop(bot, cb.message.message_id)
        if action == "mt":
            m["sel"].symmetric_difference_update({int(p[3])})
            await cb.answer()
            try:
                return await cb.message.edit_reply_markup(reply_markup=_more_kb(pid))
            except Exception:
                return
        idx = list(range(len(m["files"]))) if action == "mall" else sorted(m["sel"])
        if not idx:
            return await cb.answer("Отметь хотя бы один кадр", show_alert=True)
        await cb.answer("Добавляю")
        return await _more_add(bot, pid, idx)

    # --- карточка запроса ---
    tok = p[2]
    it = _pending.get(tok)
    if not it:
        await cb.answer("Запрос устарел — напиши его ещё раз", show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    if action == "x":
        await cb.answer()
        _pending.pop(tok, None)
        return await _bot_mod._drop(bot, cb.message.message_id, *it.get("msgs", []))
    if action == "fmt":
        it["fmt"] = NEXT_FMT[it["fmt"]]
        await cb.answer(f"Формат: {FORMATS[it['fmt']]}")
        return await _card(bot, tok, edit=cb.message)
    if action == "later":
        n = await wl_add(it["q"], it["fmt"])
        _pending.pop(tok, None)
        await cb.answer(f"Отложил. В списке: {n}")
        return await _bot_mod._drop(bot, cb.message.message_id, *it.get("msgs", []))
    if action == "slots":
        await cb.answer()
        return await _slots_card(cb.message, tok)
    if action == "back":
        await cb.answer()
        return await _card(bot, tok, edit=cb.message)
    if action == "fix":
        await cb.answer()
        await state.set_state(Req.fix)
        prompt = await bot.send_message(config.ADMIN_ID, "Уточни: год, автор, город или что именно нужно. "
                                                         "/cancel — отмена.")
        await state.update_data(tok=tok, prompt=prompt.message_id, card=cb.message.message_id)
        return
    if action == "opt":
        i = int(p[3])
        opts = it.get("options") or []
        if i >= len(opts):
            return await cb.answer("Варианты устарели", show_alert=True)
        it["q"] = opts[i]["query"][:300]
        await cb.answer("Ищу")
        return asyncio.create_task(_find(bot, cb.message, tok))
    if action == "retry":
        await cb.answer("Ищу")
        return asyncio.create_task(_find(bot, cb.message, tok))
    if action == "ok":
        await cb.answer("Собираю")
        return asyncio.create_task(_build(bot, cb.message, tok))

    # выбор места → поиск
    if it["fmt"] == "notes":
        await cb.answer("Собираю материал")
        from app import notes
        _pending.pop(tok, None)
        await _bot_mod._drop(bot, *it.get("msgs", []))
        if it.get("wl"):
            await _wl_remove(it["wl"])
        await _status(cb.message, f"📝 Заметка по запросу: «{html.escape(it['q'])}»")
        return asyncio.create_task(notes.research(cb.message, it["q"]))
    if action == "at":
        target = slots.dec(p[3])
        if slots.key_dt(target) <= slots._now():
            await cb.answer("Этот слот уже прошёл", show_alert=True)
            return await _slots_card(cb.message, tok)
        it["target"] = target
    else:
        it["target"] = "nearest" if action == "now" else None
    await cb.answer("Ищу")
    asyncio.create_task(_find(bot, cb.message, tok))


# ======================= подключение к боту и экрану =======================

def _wrap_post_kb(orig):
    """Выбор слота: первой строкой ⚡️ ближайший, даже занятый; дальше — занятые слоты сегодня.
    Раздел фото: «🔄 Ещё кадры»."""
    async def _post_kb(post, mode, idx, n, clipped, sub):
        kbd = await orig(post, mode, idx, n, clipped, sub)
        try:
            active = post["status"] in ("ready", "sent", "approved", "announced")
            if sub == "pick" and post["status"] in ("ready", "sent", "approved"):
                key = await nearest_key()
                if key and not (post["status"] == "approved" and post["slot_key"] == key):
                    kbd.inline_keyboard.insert(
                        0, [btn(f"⚡️ {slots.human_key(key)} · даже если занят", f"rq:bump:{post['id']}")])
                if post["status"] in ("ready", "sent"):   # у одобренного занятые слоты уже есть — «⇄ поменять»
                    busy = [c for c in await slot_choices() if c["post"] and c["key"] != key]
                    for k, c in enumerate(busy[:6]):
                        kbd.inline_keyboard.insert(
                            1 + k, [btn(slot_label(c, short=True), f"rq:put:{post['id']}:{slots.enc(c['key'])}")])
            if sub == "photos" and active:
                kbd.inline_keyboard.insert(max(len(kbd.inline_keyboard) - 1, 0),
                                           [btn("🔄 Ещё кадры", f"rq:more:{post['id']}")])
        except Exception:
            log.warning("Кнопки запроса не добавились", exc_info=True)
        return kbd

    _post_kb.__wrapped__ = orig
    return _post_kb


def _wrap_home(orig):
    async def home(arg: dict):
        photo, text, kbd, arg = await orig(arg)
        try:
            n = len(await wishlist())
            rows = [list(r) for r in kbd.inline_keyboard]
            rows.insert(max(len(rows) - 1, 0), [btn("✍️ Пост по запросу", "rq:ask"),
                                                 btn(f"🕓 Отложенные · {n}" if n else "🕓 Отложенные", "rq:wl")])
            kbd = kb(rows)
        except Exception:
            log.warning("Кнопка «Пост по запросу» не добавилась", exc_info=True)
        return photo, text, kbd, arg

    home.__wrapped__ = orig
    return home


def _wrap_collection(orig):
    """После каждого сбора — отложенный запрос в запас, если запас меньше нормы."""
    async def run_collection(*a, **k):
        res = await orig(*a, **k)
        try:
            asyncio.get_running_loop().create_task(wishlist_tick())
        except Exception:
            log.warning("Отложенные не запустились", exc_info=True)
        return res

    run_collection.__wrapped__ = orig
    return run_collection


def _wrap_refresh(orig):
    """Запоминаем бота для уведомлений из фоновых задач (refresh_soon вызывается после каждого сбора)."""
    def refresh_soon(bot, *a, **k):
        global _bot
        _bot = bot
        return orig(bot, *a, **k)

    refresh_soon.__wrapped__ = orig
    return refresh_soon


def attach(bot_module) -> None:
    global _bot_mod, _attached
    if _attached:
        return
    _bot_mod = bot_module
    r = bot_module.router
    not_cmd = ~F.text.regexp(r"^/(?!later\b|потом\b)")
    not_fwd = F.func(lambda m: getattr(m, "forward_origin", None) is None)
    r.callback_query.register(on_cb, F.data.startswith("rq:"))
    r.message.register(on_query_text, StateFilter(Req.query, Req.fix), F.text, not_cmd)
    r.message.register(on_photo, Req.query, F.photo)
    # обычные сообщения без ссылки: ссылки раньше забирает «пост по ссылке», ввод в диалогах — свои обработчики
    r.message.register(on_query_text, StateFilter(None), F.text, not_cmd, not_fwd)
    r.message.register(on_photo, StateFilter(None), F.photo, not_fwd)
    screen._post_kb = _wrap_post_kb(screen._post_kb)
    screen.VIEWS["home"] = _wrap_home(screen.VIEWS["home"])
    screen.refresh_soon = _wrap_refresh(screen.refresh_soon)
    pipeline.run_collection = _wrap_collection(pipeline.run_collection)
    _attached = True
    log.info("Пост по запросу подключён (TMDB %s)", "есть" if tmdb.configured() else "нет ключа")
