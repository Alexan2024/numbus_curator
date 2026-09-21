"""Пост по запросу и «⚡️ ближайший слот, даже занятый».

Пишешь боту название (фильм, здание, художник…) или присылаешь фото с подписью → бот спрашивает, собрать ли пост →
короткий поиск в интернете (Sonnet + веб-поиск) → фото: свои, кадры TMDB для кино, фото со страниц, Wikimedia Commons →
обычная оценка и текст, как у поста по ссылке → входящие или сразу в ближайший слот.

«⚡️ Ближайший слот»: пост встаёт в ближайший слот любого формата, даже если там уже стоит одобренный пост.
Тот сдвигается в следующий свободный слот своего формата; автопост из этого слота уходит во входящие.

Подключается из app/__init__.py после загрузки app.bot: хэндлеры регистрируются на его router,
кнопки добавляются к экрану (screen) обёртками. Сами bot.py и screen.py не менялись."""
import asyncio
import html
import itertools
import json
import logging
import shutil
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from aiogram import Bot, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app import commons, config, curator, db, formatter, media, pipeline, screen, slots, tmdb

log = logging.getLogger(__name__)

BUMP_MIN_LEAD = 2          # минут до слота: ближе — берём следующий
PHOTO_WAIT = 1.5           # секунд ждём остальные фото альбома
PENDING_TTL = 50           # сколько последних запросов помнить

_bot_mod = None            # app.bot — берём оттуда _list, _drop
_pending: dict[str, dict] = {}
_albums: dict[str, dict] = {}
_ids = itertools.count(1)
_attached = False


class Req(StatesGroup):
    query = State()


def btn(text: str, data: str):
    return screen.btn(text, data)


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


async def bump(pid: int) -> str:
    """Ставит пост в ближайший слот. Занявший его пост сдвигается. → строка для экрана."""
    key = await nearest_key()
    if not key:
        raise RuntimeError("впереди нет ни одного слота — проверь SLOTS и пропуски")
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
    note = f"⚡️ Выйдет {slots.human_key(key)}"
    if await slots.paused():
        note += " (сейчас пауза)"
    if moved:
        note += ". Сдвинут: " + "; ".join(moved)
    return note


# ======================= сбор поста по запросу =======================

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


async def build(bot: Bot, query: str, photo_ids: list[str], status_cb) -> tuple[int | None, str, dict | None]:
    """→ (id поста, пояснение, brief при неоднозначности)."""
    await status_cb(f"🔎 Ищу: <b>{html.escape(query)}</b>\nОбычно это минута-две.")
    brief = await research(query, bool(photo_ids))
    if brief.get("status") == "ambiguous" and brief.get("options"):
        return None, "ambiguous", brief
    if brief.get("status") == "not_found" and not photo_ids:
        return None, "по запросу ничего не нашлось — уточни название, год или автора", None

    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}, follow_redirects=True) as client:
        film = None
        if brief.get("kind") in ("film", "tv") and brief.get("tmdb") and tmdb.configured():
            t = brief["tmdb"] or {}
            try:
                year = int(t.get("year")) if t.get("year") else None
            except (TypeError, ValueError):
                year = None
            film = await tmdb.find(client, t.get("title") or brief.get("title", ""), year, brief.get("kind"))

        srcs = [s.get("url") for s in brief.get("sources") or [] if s.get("url")]
        url = (film or {}).get("url") or (srcs[0] if srcs else
               f"https://en.wikipedia.org/wiki/Special:Search?search={quote(brief.get('title') or query)}")
        title = brief.get("title") or query

        await db.add_candidate(url, "request", title, {"query": query})
        cand = await db.get_candidate_by_url(url)
        cid = cand["id"]
        old_id, why = await _old_post_note(cid)
        if old_id or why:
            return old_id, why, None
        await db.update_candidate(cid, source="request", title=title, status="request", note="собирается по запросу")

        folder = config.IMG_DIR / f"c{cid}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        await status_cb(f"🖼 Собираю фото: <b>{html.escape(title)}</b>")
        images: list[Path] = []
        if photo_ids:
            for n, fid in enumerate(photo_ids[:config.EVAL_PHOTOS]):
                dst = folder / f"own{n:02d}.jpg"
                try:
                    await bot.download(fid, destination=dst)
                    images.append(dst)
                except Exception:
                    log.warning("Запрос: своё фото %s не скачалось", n + 1, exc_info=True)
        else:
            urls: list[str] = []
            if film:
                urls += await tmdb.images(client, film)
            if len(urls) < 6:
                for page in (brief.get("page_urls") or [])[:3]:
                    try:
                        urls += (await media.extract_article(client, page))["image_urls"][:12]
                    except Exception as exc:
                        log.info("Запрос: страница без фото (%s): %s", exc, page)
            if len(urls) < 6 and brief.get("kind") not in ("film", "tv"):
                for q in (brief.get("image_queries") or [])[:4]:
                    urls += await commons.search(client, q)
            urls = list(dict.fromkeys(urls))[:40]
            if urls:
                images = await media.download_images(client, urls, folder)
        for extra in images[config.EVAL_PHOTOS:]:
            Path(extra).unlink(missing_ok=True)
        images = images[:config.EVAL_PHOTOS]

    if not images:
        shutil.rmtree(folder, ignore_errors=True)
        await db.mark_candidate(cid, "skipped", "по запросу: фото не нашлись")
        tip = ("Кадров не нашлось" if brief.get("kind") in ("film", "tv") else "Качественных фото не нашлось")
        return None, f"{tip}. Пришли свои фото с подписью «{query}» — соберу пост на них", None

    text = _brief_text(brief)
    prep = {"title": title, "text": text[:6000], "images": [str(p) for p in images], "allow_std": True}
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
        return None, "не получилось собрать пост", None
    post = await db.get_post(pid)
    if post["format"] != "std":
        await pipeline.set_format(pid, "std", write=False)
    await pipeline.ensure_text(pid)
    return pid, "ok", None


# ======================= диалог =======================

def _remember(query: str, photos: list[str], user_msgs: list[int]) -> str:
    tok = str(next(_ids))
    _pending[tok] = {"q": query.strip()[:300], "photos": photos, "msgs": user_msgs}
    for old in list(_pending)[:-PENDING_TTL]:
        _pending.pop(old, None)
    return tok


async def _card(bot: Bot, tok: str, reply_to: Message | None = None, edit: Message | None = None) -> None:
    it = _pending[tok]
    key = await nearest_key()
    when = f" · {slots.human_key(key)}" if key else ""
    text = (f"<b>Пост по запросу</b>\n«{html.escape(it['q'])}»"
            + (f"\nСвоих фото: {len(it['photos'])}" if it["photos"] else ""))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("🔎 Собрать во входящие", f"rq:go:{tok}")],
        [btn(f"⚡️ Собрать и в ближайший слот{when}", f"rq:now:{tok}")],
        [btn("✕ Не надо", f"rq:x:{tok}")]])
    if edit:
        await edit.edit_text(text, reply_markup=kb)
    else:
        await bot.send_message(config.ADMIN_ID, text, reply_markup=kb)


async def _run(bot: Bot, msg: Message, tok: str, urgent: bool) -> None:
    it = _pending.get(tok)
    if not it:
        return

    async def status(text: str):
        try:
            await msg.edit_text(text, reply_markup=None)
        except Exception:
            pass

    try:
        pid, note, brief = await build(bot, it["q"], it["photos"], status)
    except Exception as exc:
        log.exception("пост по запросу")
        return await status(f"Не получилось. {curator.explain(exc)}")

    if brief:   # неоднозначно — варианты кнопками
        it["options"] = [o for o in brief["options"] if o.get("query")][:4]
        rows = [[btn(str(o.get("label") or o["query"])[:60], f"rq:opt:{tok}:{i}:{int(urgent)}")]
                for i, o in enumerate(it["options"])]
        rows.append([btn("✕ Не надо", f"rq:x:{tok}")])
        try:
            return await msg.edit_text(f"«{html.escape(it['q'])}» — что именно?",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except Exception:
            return
    if not pid:
        return await status(f"Пост не собрался: {html.escape(note)}")

    await _bot_mod._drop(bot, msg.message_id, *it.get("msgs", []))
    _pending.pop(tok, None)
    post = await db.get_post(pid)
    if urgent and post["status"] in ("ready", "sent", "approved"):
        try:
            done = await bump(pid)
        except Exception as exc:
            log.exception("ближайший слот")
            done = f"В слот не встал: {curator.explain(exc)}"[:200]
            await slots.propose(pid, None)
            return await screen.move_down(bot, "list", mode="inbox", pid=pid, note=done)
        return await screen.move_down(bot, "list", mode="sched", pid=pid, note=done)
    if post["status"] == "ready":
        await slots.propose(pid, None)
    mode = "sched" if post["status"] == "approved" else "inbox"
    await screen.move_down(bot, "list", mode=mode, pid=pid, note=None if note == "ok" else note)


async def on_query_text(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await _card(bot, _remember(msg.text, [], [msg.message_id]))


async def on_photo(msg: Message, state: FSMContext, bot: Bot):
    """Фото с подписью (или альбом) → пост на этих фото. Альбом приходит пачкой сообщений — ждём остальные."""
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


async def on_cb(cb: CallbackQuery, state: FSMContext, bot: Bot):
    p = cb.data.split(":")
    action = p[1]
    if action == "ask":
        await cb.answer()
        return await _bot_mod._ask(bot, state, Req.query,
                                   "О чём пост? Название фильма, здания, имя художника — можно с уточнением. "
                                   "Или пришли фото с подписью. /cancel — отмена.")
    if action == "bump":
        pid = int(p[2])
        post = await db.get_post(pid)
        if not post or post["status"] not in ("ready", "sent", "approved"):
            return await cb.answer("Этот пост уже не ждёт решения", show_alert=True)
        await cb.answer("Ставлю в ближайший слот")
        try:
            note = await bump(pid)
        except Exception as exc:
            log.exception("ближайший слот")
            return await _bot_mod._list(bot, pid=pid, note=curator.explain(exc)[:200])
        screen.refresh_soon(bot)
        return await _bot_mod._list(bot, mode="sched", pid=pid, note=note[:200])

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
    if action == "opt":
        i, urgent = int(p[3]), p[4] == "1"
        opts = it.get("options") or []
        if i >= len(opts):
            return await cb.answer("Варианты устарели", show_alert=True)
        it["q"] = opts[i]["query"][:300]
    else:
        urgent = action == "now"
    await cb.answer("Собираю")
    asyncio.create_task(_run(bot, cb.message, tok, urgent))


# ======================= подключение к боту и экрану =======================

def _wrap_post_kb(orig):
    """В выборе слота («🗓 Другой слот», «🗓 Выбрать слот», «🔀 Перенести») первой строкой — ⚡️ ближайший, даже занятый."""
    async def _post_kb(post, mode, idx, n, clipped, sub):
        kb = await orig(post, mode, idx, n, clipped, sub)
        try:
            if sub == "pick" and post["status"] in ("ready", "sent", "approved"):
                key = await nearest_key()
                if key and not (post["status"] == "approved" and post["slot_key"] == key):
                    kb.inline_keyboard.insert(
                        0, [btn(f"⚡️ {slots.human_key(key)} · даже если занят", f"rq:bump:{post['id']}")])
        except Exception:
            log.warning("Кнопка ⚡️ не добавилась", exc_info=True)
        return kb

    _post_kb.__wrapped__ = orig
    return _post_kb


def _wrap_home(orig):
    async def home(arg: dict):
        photo, text, kb, arg = await orig(arg)
        try:
            rows = [list(r) for r in kb.inline_keyboard]
            rows.insert(max(len(rows) - 1, 0), [btn("✍️ Пост по запросу", "rq:ask")])
            kb = InlineKeyboardMarkup(inline_keyboard=rows)
        except Exception:
            log.warning("Кнопка «Пост по запросу» не добавилась", exc_info=True)
        return photo, text, kb, arg

    home.__wrapped__ = orig
    return home


def attach(bot_module) -> None:
    global _bot_mod, _attached
    if _attached:
        return
    _bot_mod = bot_module
    r = bot_module.router
    not_cmd = bot_module.NOT_COMMAND
    not_fwd = F.func(lambda m: getattr(m, "forward_origin", None) is None)
    r.callback_query.register(on_cb, F.data.startswith("rq:"))
    r.message.register(on_query_text, Req.query, F.text, not_cmd)
    r.message.register(on_photo, Req.query, F.photo)
    # обычные сообщения без ссылки: ссылки раньше забирает «пост по ссылке», ввод в диалогах — свои обработчики
    r.message.register(on_query_text, StateFilter(None), F.text, not_cmd, not_fwd)
    r.message.register(on_photo, StateFilter(None), F.photo, not_fwd)
    screen._post_kb = _wrap_post_kb(screen._post_kb)
    screen.VIEWS["home"] = _wrap_home(screen.VIEWS["home"])
    _attached = True
    log.info("Пост по запросу подключён (TMDB %s)", "есть" if tmdb.configured() else "нет ключа")
