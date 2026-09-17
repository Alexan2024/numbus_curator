"""Кандидат → статья → фото → оценка Claude → готовый пост в очереди."""
import json
import logging
import shutil

import httpx

from app import config, curator, db, formatter, media

log = logging.getLogger(__name__)


def _met_text(meta: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in meta.items() if v)


async def process_candidate(client: httpx.AsyncClient, cand) -> None:
    cid, url, source = cand["id"], cand["url"], cand["source"]
    payload = json.loads(cand["payload"] or "{}")
    folder = config.IMG_DIR / f"c{cid}"
    try:
        if source == "met":
            title = payload["meta"].get("title", "")
            text = "Объект из открытой коллекции The Metropolitan Museum of Art.\n" + _met_text(payload["meta"])
            image_urls, min_photos = payload["images"], 1
        else:
            art = await media.extract_article(client, url, payload.get("content_html", ""))
            title, text, image_urls = art["title"] or cand["title"], art["text"], art["image_urls"]
            min_photos = config.MIN_PHOTOS_ARTICLE
            if len(text) < 300:
                await db.mark_candidate(cid, "skipped", "мало текста")
                return

        images = await media.download_images(client, image_urls, folder)
        if len(images) < min_photos:
            await db.mark_candidate(cid, "skipped", f"мало качественных фото: {len(images)}")
            shutil.rmtree(folder, ignore_errors=True)
            return

        data = await curator.evaluate(source, url, title, text, images)
        data["_source_text"] = text[:6000]
        score = int(data.get("score") or 0)
        passed = score >= config.SCORE_THRESHOLD and not data.get("stoplist") and not data.get("already_posted")

        if not passed:
            await db.mark_candidate(cid, "processed", f"авто-отказ {score}: {data.get('score_reason', '')}")
            shutil.rmtree(folder, ignore_errors=True)
            return

        order = [i for i in (data.get("photo_order") or []) if isinstance(i, int) and 0 <= i < len(images)]
        chosen = [str(images[i]) for i in dict.fromkeys(order)][: config.MAX_PHOTOS] or [str(p) for p in images[:config.MAX_PHOTOS]]
        caption = formatter.build_caption(data)
        if formatter.visible_len(caption) > config.CAPTION_LIMIT:
            data.setdefault("flags", []).append("длинная подпись — уйдёт отдельным сообщением")

        await db.add_post(
            candidate_id=cid, source=source, url=url,
            category=(data.get("category") or "architecture").lower(),
            data=data, caption=caption, score=score,
            reason=data.get("score_reason", ""), images=chosen, status="ready",
        )
        await db.mark_candidate(cid, "processed", f"в очереди {score}")
    except Exception as exc:
        log.exception("Кандидат %s упал", cid)
        await db.mark_candidate(cid, "error", repr(exc))
        shutil.rmtree(folder, ignore_errors=True)


async def process_new() -> int:
    if await db.count_ready() >= config.MAX_READY_QUEUE:
        log.info("Очередь полна, обработку пропускаем")
        return 0
    cands = await db.new_candidates(config.MAX_PER_RUN)
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}) as client:
        for c in cands:
            await process_candidate(client, c)
    return len(cands)


async def pick_next():
    """Лучший готовый пост с поправкой на баланс рубрик за сегодня."""
    ready = await db.ready_posts()
    if not ready:
        return None
    sent = [r["category"] for r in await db.sent_today()]
    total = len(sent) + 1

    def weight(p):
        share = sent.count(p["category"]) / total
        target = config.TARGET_MIX.get(p["category"], 0.03)
        return p["score"] + 3 * (target - share)

    return max(ready, key=weight)
