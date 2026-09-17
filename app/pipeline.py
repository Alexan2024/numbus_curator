"""Кандидат → статья → фото → оценка Claude → готовый пост в очереди.
Плюс: пост по ссылке, сборка заметки #ahmagnotes, выбор следующего поста."""
import json
import logging
import shutil
from pathlib import Path

import httpx

from app import commons, config, curator, db, formatter, media, sources

log = logging.getLogger(__name__)


def _met_text(meta: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in meta.items() if v)


def _order(data: dict, images: list, limit: int) -> list[str]:
    order = [i for i in (data.get("photo_order") or []) if isinstance(i, int) and 0 <= i < len(images)]
    return [str(images[i]) for i in dict.fromkeys(order)][:limit] or [str(p) for p in images[:limit]]


async def process_candidate(client: httpx.AsyncClient, cand, force: bool = False) -> tuple[int | None, str]:
    """→ (id поста или None, пояснение). force — пост по ссылке автора: порог оценки не применяется."""
    cid, url, source = cand["id"], cand["url"], cand["source"]
    payload = json.loads(cand["payload"] or "{}")
    folder = config.IMG_DIR / f"c{cid}"

    async def skip(status: str, note: str):
        await db.mark_candidate(cid, status, note)
        shutil.rmtree(folder, ignore_errors=True)
        return None, note

    try:
        if source == "met":
            title = payload["meta"].get("title", "")
            text = "Объект из открытой коллекции The Metropolitan Museum of Art.\n" + _met_text(payload["meta"])
            image_urls, min_photos = payload["images"], 1
        else:
            art = await media.extract_article(client, url, payload.get("content_html", ""))
            title, text, image_urls = art["title"] or cand["title"], art["text"], art["image_urls"]
            min_photos = 1 if force else config.MIN_PHOTOS_MINI
            if len(text) < (80 if force else 300):
                return await skip("skipped", "мало текста")

        images = await media.download_images(client, image_urls, folder)
        if len(images) < min_photos:
            return await skip("skipped", f"мало качественных фото: {len(images)}")
        allow_std = source == "met" or len(images) >= config.MIN_PHOTOS_ARTICLE or force

        data = await curator.evaluate(source, url, title, text, images, allow_std=allow_std, forced=force)
        data["_source_text"] = text[:6000]
        data["flags"] = data.get("flags") or []
        score = int(data.get("score") or 0)
        passed = score >= config.SCORE_THRESHOLD and not data.get("stoplist") and not data.get("already_posted")

        if not passed and not force:
            return await skip("processed", f"авто-отказ {score}: {data.get('score_reason', '')}")
        if force:
            if data.get("stoplist"):
                data["flags"].append("совпадает со стоп-листом")
            if data.get("already_posted"):
                data["flags"].append("похоже, уже было в канале")

        fmt = "mini" if (data.get("format") == "mini" or not allow_std) else "std"
        if fmt == "std" and not (data.get("body") or "").strip():
            fmt = "mini"
        chosen = _order(data, images, config.MINI_MAX_PHOTOS if fmt == "mini" else config.MAX_PHOTOS)
        caption = formatter.build_caption(data, fmt)
        if formatter.visible_len(caption) > config.CAPTION_LIMIT:
            data["flags"].append("длинная подпись — уйдёт отдельным сообщением")

        pid = await db.add_post(
            candidate_id=cid, source=source, url=url,
            category=(data.get("category") or "architecture").lower(),
            data=data, caption=caption, score=score, format=fmt,
            reason=data.get("score_reason", ""), images=chosen, status="ready",
        )
        await db.mark_candidate(cid, "processed", f"в очереди {score} ({fmt})")
        return pid, "ok"
    except (curator.BudgetExceeded, curator.NoCredits, curator.ApiDown):
        shutil.rmtree(folder, ignore_errors=True)
        raise                             # кандидат остаётся new — вернёмся к нему позже
    except Exception as exc:
        log.exception("Кандидат %s упал", cid)
        await db.mark_candidate(cid, "error", repr(exc))
        shutil.rmtree(folder, ignore_errors=True)
        return None, f"ошибка: {exc!r}"


async def process_new() -> tuple[int, str]:
    """→ (сколько обработано, пояснение для чата)."""
    ready_now = await db.count_ready()
    if ready_now >= config.MAX_READY_QUEUE:
        return 0, f"очередь полна ({ready_now}) — обработку пропустил"
    cands = await db.new_candidates(config.MAX_PER_RUN, {"met": config.MET_PER_RUN}, skip=await sources.disabled())
    if not cands:
        return 0, "новых материалов для оценки нет"
    done = 0
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}) as client:
        for c in cands:
            if await db.calls_today() >= config.DAILY_API_CALLS_MAX:
                await db.set_setting("api_error", {"at": db.now(), "text": curator.explain(curator.BudgetExceeded())})
                return done, f"дневной лимит Claude исчерпан на {done}-м материале"
            try:
                await process_candidate(client, c)
            except (curator.NoCredits, curator.ApiDown) as exc:
                await db.set_setting("api_error", {"at": db.now(), "text": curator.explain(exc)})
                return done, curator.explain(exc)
            done += 1
    await db.set_setting("api_error", None)   # дошли до конца — прошлая ошибка неактуальна
    return done, ""


async def process_link(url: str) -> tuple[int | None, str]:
    """Пост из ссылки, которую прислал автор."""
    await db.add_candidate(url, "link", "", {})
    cand = await db.get_candidate_by_url(url)
    old = await db.post_by_candidate(cand["id"])
    if old and old["status"] == "ready":
        return old["id"], "уже был в очереди"
    if old and old["status"] in ("sent", "approved", "announced"):
        return None, "карточка этого материала уже у вас выше"
    if old and old["status"] == "published":
        return None, "этот материал уже опубликован"
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}) as client:
        return await process_candidate(client, cand, force=True)


async def build_notes_post(nid: int) -> int:
    """Заметка по утверждённому плану: фото со страниц-источников и из Wikimedia Commons, текст, пост."""
    note = await db.get_note(nid)
    brief = json.loads(note["brief"])
    folder = config.IMG_DIR / f"n{nid}"
    urls: list[str] = []
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}) as client:
        for page in (brief.get("page_urls") or [])[:3]:
            try:
                art = await media.extract_article(client, page)
                urls += art["image_urls"][:12]
            except Exception as exc:
                log.info("notes: страница без фото (%s): %s", exc, page)
        for q in (brief.get("image_queries") or [])[:5]:
            urls += await commons.search(client, q)
        images = await media.download_images(client, list(dict.fromkeys(urls))[:40], folder) if urls else []

    data = await curator.notes_write(brief, images)
    data["_source_text"] = curator.brief_text(brief)
    data["_sources"] = (brief.get("sources") or [])[:8]
    data["flags"] = data.get("flags") or []
    if brief.get("_no_search"):
        data["flags"].append("собрано без веб-поиска — проверьте факты")
    if not images:
        data["flags"].append("фото не нашлись — заметка выйдет текстом")
    chosen = _order(data, images, config.MAX_PHOTOS) if images else []
    caption = formatter.build_caption(data, "notes")
    src = data["_sources"][0].get("url", "") if data["_sources"] else ""
    pid = await db.add_post(
        candidate_id=None, source="notes", url=src, category="notes", format="notes",
        data=data, caption=caption, score=0, reason=brief.get("thesis", ""),
        images=chosen, status="ready",
    )
    await db.update_note(nid, status="written", post_id=pid)
    return pid


# ---------- выбор следующего ----------

async def _to_mini(post) -> None:
    data = json.loads(post["data"])
    await db.update_post(post["id"], format="mini", data=data, caption=formatter.build_caption(data, "mini"))


async def _files_ok(post) -> bool:
    """Файлы фото на месте? Если volume отвалился, пост снимаем, а не падаем при отправке."""
    images = json.loads(post["images"] or "[]")
    if not images or all(Path(p).exists() for p in images):
        return True
    await db.update_post(post["id"], status="auto_rejected", reject_reason="файлы фото пропали с диска")
    log.warning("Пост %s снят: файлов нет на диске", post["id"])
    return False


async def pick_next(fmt: str = "std", min_score: int = 0, clean_only: bool = False,
                    skip_sources: set[str] | None = None):
    """Лучший готовый пост нужного формата с поправкой на баланс рубрик за сегодня.
    Если мини-постов нет, а стандартных с запасом — сжимает один из них до мини."""
    ready = await db.ready_posts(fmt)
    converted = False
    if fmt == "mini" and not ready:
        std = await db.ready_posts("std")
        ready = std[4:] if len(std) > 6 else []   # лучшие четыре не трогаем
        converted = True
    ready = [p for p in ready if p["score"] >= min_score and p["source"] not in (skip_sources or set())]
    ready = [p for p in ready if await _files_ok(p)]
    if clean_only:
        ready = [p for p in ready if not (json.loads(p["data"]).get("flags") or [])]
    if not ready:
        return None
    today = await db.sent_today()
    sent = [r["category"] for r in today]
    total = len(sent) + 1
    met_sent = sum(1 for r in today if r["source"] == "met")
    if met_sent >= config.MET_DAILY_MAX:
        ready = [p for p in ready if p["source"] != "met"]
        if not ready:
            return None

    def weight(p):
        share = sent.count(p["category"]) / total
        target = config.TARGET_MIX.get(p["category"], 0.03)
        return p["score"] + 3 * (target - share)

    best = max(ready, key=weight)
    if converted:
        await _to_mini(best)
        best = await db.get_post(best["id"])
    return best


async def pick_auto(fmt: str):
    """Для автомата: только высокая оценка, без флагов, из источников, которым автор доверяет
    (источник с 5+ решениями и долей одобрений ниже половины в автомат не попадает)."""
    untrusted = set()
    for src, s in (await db.source_stats()).items():
        decided = s["published"] + s["rejected"]
        if decided >= 5 and s["published"] / decided < 0.5:
            untrusted.add(src)
    return await pick_next(fmt, min_score=config.AUTO_MIN_SCORE, clean_only=True, skip_sources=untrusted)
