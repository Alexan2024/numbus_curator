"""Сбор кандидатов из источников. Каждый источник возвращает список словарей
{url, title, source, payload}. Добавить источник = добавить строку в FEEDS
или функцию в COLLECTORS."""
import asyncio
import logging
import random

import feedparser
import httpx

from app import config, db

log = logging.getLogger(__name__)

FEEDS = {
    "archdaily": "https://feeds.feedburner.com/Archdaily",
    "dezeen": "https://www.dezeen.com/architecture/feed/",
    "designboom": "https://www.designboom.com/architecture/feed/",
    "colossal": "https://www.thisiscolossal.com/feed/",
}

# Темы для музейного open access — под профиль канала
MET_QUERIES = [
    "architecture photograph", "architectural drawing", "Japanese woodblock print",
    "ukiyo-e landscape", "surrealism", "street photograph", "Walker Evans",
    "Eugène Atget", "Berenice Abbott", "illuminated manuscript", "book of hours",
    "Hiroshige", "Hokusai", "Giorgio de Chirico", "Bauhaus", "modernist design",
    "cat", "Egyptian temple", "Persian tile", "Roman ruins photograph",
]


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    r = await client.get(url, timeout=30, follow_redirects=True, **kw)
    r.raise_for_status()
    return r


async def collect_rss(client: httpx.AsyncClient) -> list[dict]:
    items = []
    for name, url in FEEDS.items():
        try:
            r = await _get(client, url)
            feed = feedparser.parse(r.content)
            for e in feed.entries[:25]:
                link = e.get("link")
                if link:
                    content = e.get("content") or []
                    body = content[0].get("value", "") if content else e.get("summary", "")
                    items.append({"url": link, "title": e.get("title", ""), "source": name,
                                  "payload": {"content_html": body[:200_000]}})
        except Exception as exc:  # источник упал — не роняем весь сбор
            log.warning("RSS %s: %s", name, exc)
    return items


async def collect_met(client: httpx.AsyncClient, n_queries: int = 3, per_query: int = 4) -> list[dict]:
    base = "https://collectionapi.metmuseum.org/public/collection/v1"
    items = []
    for q in random.sample(MET_QUERIES, n_queries):
        try:
            r = await _get(client, f"{base}/search", params={"q": q, "hasImages": "true"})
            ids = (r.json().get("objectIDs") or [])[:300]
            for oid in random.sample(ids, min(per_query * 3, len(ids))):
                o = (await _get(client, f"{base}/objects/{oid}")).json()
                if not (o.get("isPublicDomain") and o.get("primaryImage")):
                    continue
                images = [o["primaryImage"], *o.get("additionalImages", [])][: config.MAX_PHOTOS]
                items.append({
                    "url": o.get("objectURL") or f"https://www.metmuseum.org/art/collection/search/{oid}",
                    "title": o.get("title", ""),
                    "source": "met",
                    "payload": {
                        "images": images,
                        "meta": {k: o.get(k) for k in (
                            "title", "artistDisplayName", "artistDisplayBio", "objectDate",
                            "medium", "culture", "country", "city", "department",
                            "classification", "creditLine", "objectName", "tags",
                        )},
                    },
                })
                if sum(1 for i in items if i["payload"].get("meta", {}).get("title")) >= per_query * n_queries:
                    break
                await asyncio.sleep(0.3)
        except Exception as exc:
            log.warning("Met %s: %s", q, exc)
    return items


COLLECTORS = [collect_rss, collect_met]


async def collect_all() -> int:
    added = 0
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}) as client:
        for fn in COLLECTORS:
            for it in await fn(client):
                if await db.add_candidate(it["url"], it["source"], it["title"], it["payload"]):
                    added += 1
    log.info("Новых кандидатов: %s", added)
    return added
