import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    title TEXT,
    payload TEXT,              -- json: доп. данные источника (Met и т.п.)
    status TEXT NOT NULL DEFAULT 'new',   -- new | processed | skipped | error
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER REFERENCES candidates(id),
    source TEXT,
    url TEXT,
    category TEXT,
    data TEXT NOT NULL,        -- json-ответ Claude (заголовок, текст, кредиты, теги)
    caption TEXT NOT NULL,     -- готовый HTML
    score INTEGER,
    reason TEXT,
    images TEXT NOT NULL,      -- json: пути к файлам
    file_ids TEXT,             -- json: file_id после отправки на модерацию
    status TEXT NOT NULL,      -- ready | sent | published | rejected | auto_rejected
    reject_reason TEXT,
    card_chat_id INTEGER,
    card_msg_id INTEGER,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
"""


def now() -> str:
    return datetime.now(ZoneInfo(config.TZ_NAME)).isoformat(timespec="seconds")


def today_start() -> str:
    d = datetime.now(ZoneInfo(config.TZ_NAME)).replace(hour=0, minute=0, second=0, microsecond=0)
    return d.isoformat(timespec="seconds")


@asynccontextmanager
async def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def init() -> None:
    async with connect() as db:
        await db.executescript(SCHEMA)
        await db.commit()


# ---------- candidates ----------

async def add_candidate(url: str, source: str, title: str, payload: dict | None = None) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO candidates(url, source, title, payload, created_at) VALUES (?,?,?,?,?)",
            (url, source, title, json.dumps(payload or {}, ensure_ascii=False), now()),
        )
        await db.commit()
        return cur.rowcount > 0


async def new_candidates(limit: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM candidates WHERE status='new' ORDER BY id DESC LIMIT ?", (limit,)
        )
        return await cur.fetchall()


async def mark_candidate(cid: int, status: str, note: str = "") -> None:
    async with connect() as db:
        await db.execute("UPDATE candidates SET status=?, note=? WHERE id=?", (status, note[:500], cid))
        await db.commit()


# ---------- posts ----------

async def add_post(**f) -> int:
    f.setdefault("created_at", now())
    for k in ("data", "images"):
        if not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    cols = ",".join(f)
    q = ",".join("?" * len(f))
    async with connect() as db:
        cur = await db.execute(f"INSERT INTO posts({cols}) VALUES ({q})", tuple(f.values()))
        await db.commit()
        return cur.lastrowid


async def get_post(pid: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM posts WHERE id=?", (pid,))
        return await cur.fetchone()


async def update_post(pid: int, **f) -> None:
    for k in ("data", "images", "file_ids"):
        if k in f and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    sets = ",".join(f"{k}=?" for k in f)
    async with connect() as db:
        await db.execute(f"UPDATE posts SET {sets} WHERE id=?", (*f.values(), pid))
        await db.commit()


async def ready_posts() -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM posts WHERE status='ready' ORDER BY score DESC, id DESC")
        return await cur.fetchall()


async def count_ready() -> int:
    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM posts WHERE status='ready'")
        return (await cur.fetchone())[0]


async def sent_today() -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT category FROM posts WHERE sent_at >= ?", (today_start(),)
        )
        return await cur.fetchall()


async def recent_rejections(limit: int = 15) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT data, reject_reason FROM posts WHERE status='rejected' ORDER BY decided_at DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def published_posts(limit: int = 30) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT caption, data, category FROM posts WHERE status='published' ORDER BY decided_at DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def recent_headlines(days: int = 60) -> list[str]:
    since = (datetime.now(ZoneInfo(config.TZ_NAME)) - timedelta(days=days)).isoformat()
    async with connect() as db:
        cur = await db.execute(
            "SELECT data FROM posts WHERE status IN ('ready','sent','published') AND created_at >= ?",
            (since,),
        )
        rows = await cur.fetchall()
    return [json.loads(r["data"]).get("headline", "") for r in rows]


async def stats() -> dict:
    async with connect() as db:
        cur = await db.execute("SELECT status, COUNT(*) c FROM posts GROUP BY status")
        posts = {r["status"]: r["c"] for r in await cur.fetchall()}
        cur = await db.execute("SELECT status, COUNT(*) c FROM candidates GROUP BY status")
        cands = {r["status"]: r["c"] for r in await cur.fetchall()}
    return {"posts": posts, "candidates": cands}
