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
    status TEXT NOT NULL,      -- ready | sent | approved | announced | published | rejected | auto_rejected
    reject_reason TEXT,
    card_chat_id INTEGER,
    card_msg_id INTEGER,
    album_msg_id INTEGER,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    decided_at TEXT,
    format TEXT NOT NULL DEFAULT 'std',   -- std | mini | notes
    slot_key TEXT,             -- 'YYYY-MM-DD HH:MM' — к какому слоту предложен / анонсирован
    offers INTEGER NOT NULL DEFAULT 0,    -- сколько раз предлагался
    channel_msg_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL        -- json
);

CREATE TABLE IF NOT EXISTS usage (
    day TEXT PRIMARY KEY,
    calls INTEGER NOT NULL DEFAULT 0,
    in_tok INTEGER NOT NULL DEFAULT 0,
    out_tok INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    brief TEXT,                -- json: тезис, план, факты, источники
    status TEXT NOT NULL,      -- research | plan | written | cancelled
    post_id INTEGER,
    created_at TEXT NOT NULL
);
"""

# колонки, которых нет в базах прошлых версий
MIGRATIONS = {
    "album_msg_id": "ALTER TABLE posts ADD COLUMN album_msg_id INTEGER",
    "format": "ALTER TABLE posts ADD COLUMN format TEXT NOT NULL DEFAULT 'std'",
    "slot_key": "ALTER TABLE posts ADD COLUMN slot_key TEXT",
    "offers": "ALTER TABLE posts ADD COLUMN offers INTEGER NOT NULL DEFAULT 0",
    "channel_msg_id": "ALTER TABLE posts ADD COLUMN channel_msg_id INTEGER",
}


def _now_dt() -> datetime:
    return datetime.now(ZoneInfo(config.TZ_NAME))


def now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def today_start() -> str:
    return _now_dt().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def days_ago(days: int) -> str:
    return (_now_dt() - timedelta(days=days)).isoformat(timespec="seconds")


@asynccontextmanager
async def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def init() -> None:
    async with connect() as db:
        # таблица posts могла быть создана старой версией — сначала добавляем колонки
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='posts'")
        if await cur.fetchone():
            cur = await db.execute("PRAGMA table_info(posts)")
            cols = {r["name"] for r in await cur.fetchall()}
            for col, ddl in MIGRATIONS.items():
                if col not in cols:
                    await db.execute(ddl)
        await db.executescript(SCHEMA)
        await db.commit()


# ---------- settings ----------

async def get_setting(key: str, default=None):
    async with connect() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
    return json.loads(row["value"]) if row else default


async def set_setting(key: str, value) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        await db.commit()


# ---------- usage ----------

async def add_usage(in_tok: int, out_tok: int) -> None:
    day = _now_dt().date().isoformat()
    async with connect() as db:
        await db.execute(
            "INSERT INTO usage(day, calls, in_tok, out_tok) VALUES (?,1,?,?) "
            "ON CONFLICT(day) DO UPDATE SET calls=calls+1, in_tok=in_tok+excluded.in_tok, "
            "out_tok=out_tok+excluded.out_tok",
            (day, in_tok, out_tok),
        )
        await db.commit()


async def calls_today() -> int:
    day = _now_dt().date().isoformat()
    async with connect() as db:
        cur = await db.execute("SELECT calls FROM usage WHERE day=?", (day,))
        row = await cur.fetchone()
    return row["calls"] if row else 0


# ---------- candidates ----------

async def add_candidate(url: str, source: str, title: str, payload: dict | None = None) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO candidates(url, source, title, payload, created_at) VALUES (?,?,?,?,?)",
            (url, source, title, json.dumps(payload or {}, ensure_ascii=False), now()),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_candidate_by_url(url: str) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM candidates WHERE url=?", (url,))
        return await cur.fetchone()


async def new_candidates(limit: int, per_source: dict[str, int], skip: set[str] | None = None) -> list[aiosqlite.Row]:
    """Свежие кандидаты вперемешку по источникам, с лимитом на каждый источник."""
    async with connect() as db:
        cur = await db.execute("SELECT * FROM candidates WHERE status='new' ORDER BY id DESC")
        rows = await cur.fetchall()
    buckets: dict[str, list] = {}
    for r in rows:
        if skip and r["source"] in skip:
            continue
        buckets.setdefault(r["source"], []).append(r)
    for src, cap in per_source.items():
        if src in buckets:
            buckets[src] = buckets[src][:cap]
    out = []
    while len(out) < limit and any(buckets.values()):
        for src in list(buckets):
            if buckets[src] and len(out) < limit:
                out.append(buckets[src].pop(0))
    return out


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


async def post_by_candidate(cid: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM posts WHERE candidate_id=? ORDER BY id DESC LIMIT 1", (cid,))
        return await cur.fetchone()


async def update_post(pid: int, **f) -> None:
    for k in ("data", "images", "file_ids"):
        if k in f and f[k] is not None and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    sets = ",".join(f"{k}=?" for k in f)
    async with connect() as db:
        await db.execute(f"UPDATE posts SET {sets} WHERE id=?", (*f.values(), pid))
        await db.commit()


async def ready_posts(fmt: str | None = None) -> list[aiosqlite.Row]:
    q = "SELECT * FROM posts WHERE status='ready'"
    args: tuple = ()
    if fmt:
        q += " AND format=?"
        args = (fmt,)
    async with connect() as db:
        cur = await db.execute(q + " ORDER BY score DESC, id DESC", args)
        return await cur.fetchall()


async def count_ready(fmt: str | None = None) -> int:
    return len(await ready_posts(fmt))


async def approved_posts(fmt: str | None = None) -> list[aiosqlite.Row]:
    """Одобренные и ждущие слота — в порядке одобрения."""
    q = "SELECT * FROM posts WHERE status='approved'"
    args: tuple = ()
    if fmt:
        q += " AND format=?"
        args = (fmt,)
    async with connect() as db:
        cur = await db.execute(q + " ORDER BY decided_at ASC, id ASC", args)
        return await cur.fetchall()


async def announced_posts(slot_key: str | None = None) -> list[aiosqlite.Row]:
    q = "SELECT * FROM posts WHERE status='announced'"
    args: tuple = ()
    if slot_key:
        q += " AND slot_key=?"
        args = (slot_key,)
    async with connect() as db:
        cur = await db.execute(q + " ORDER BY id ASC", args)
        return await cur.fetchall()


async def slot_leftovers(slot_key: str) -> list[aiosqlite.Row]:
    """Предложенные к этому (или более раннему) слоту и так и не выбранные."""
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM posts WHERE status IN ('sent','announced') AND slot_key IS NOT NULL AND slot_key<=?",
            (slot_key,),
        )
        return await cur.fetchall()


async def sent_today() -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT category, source FROM posts WHERE sent_at >= ?", (today_start(),)
        )
        return await cur.fetchall()


async def recent_rejections(limit: int = 15) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT data, reject_reason FROM posts WHERE status='rejected' ORDER BY decided_at DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def published_posts(limit: int = 30, fmt: str | None = None) -> list[aiosqlite.Row]:
    q = "SELECT caption, data, category, format FROM posts WHERE status='published'"
    args: tuple = ()
    if fmt:
        q += " AND format=?"
        args = (fmt,)
    async with connect() as db:
        cur = await db.execute(q + " ORDER BY decided_at DESC LIMIT ?", (*args, limit))
        return await cur.fetchall()


async def recent_headlines(days: int = 60) -> list[str]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT data FROM posts WHERE status IN ('ready','sent','approved','announced','published') "
            "AND created_at >= ?",
            (days_ago(days),),
        )
        rows = await cur.fetchall()
    return [json.loads(r["data"]).get("headline", "") for r in rows]


async def purge_ready(source: str) -> int:
    async with connect() as db:
        cur = await db.execute(
            "UPDATE posts SET status='auto_rejected', reject_reason='очищено вручную' "
            "WHERE status='ready' AND source=?", (source,))
        await db.commit()
        return cur.rowcount


async def finished_before(days: int) -> list[aiosqlite.Row]:
    """Решённые посты старше N дней — их файлы можно удалять."""
    async with connect() as db:
        cur = await db.execute(
            "SELECT id, images FROM posts WHERE status IN ('published','rejected','auto_rejected') "
            "AND COALESCE(decided_at, created_at) < ?", (days_ago(days),))
        return await cur.fetchall()


# ---------- статистика ----------

async def stats() -> dict:
    async with connect() as db:
        cur = await db.execute("SELECT source, COUNT(*) c FROM posts WHERE status='ready' GROUP BY source")
        ready_by_source = {r["source"]: r["c"] for r in await cur.fetchall()}
        cur = await db.execute("SELECT format, COUNT(*) c FROM posts WHERE status='ready' GROUP BY format")
        ready_by_format = {r["format"]: r["c"] for r in await cur.fetchall()}
        cur = await db.execute("SELECT source, COUNT(*) c FROM candidates WHERE status='new' GROUP BY source")
        new_by_source = {r["source"]: r["c"] for r in await cur.fetchall()}
        cur = await db.execute("SELECT status, COUNT(*) c FROM posts GROUP BY status")
        posts = {r["status"]: r["c"] for r in await cur.fetchall()}
        cur = await db.execute("SELECT status, COUNT(*) c FROM candidates GROUP BY status")
        cands = {r["status"]: r["c"] for r in await cur.fetchall()}
    return {"posts": posts, "candidates": cands, "ready_by_source": ready_by_source,
            "ready_by_format": ready_by_format, "new_by_source": new_by_source}


async def source_stats(days: int | None = None) -> dict[str, dict[str, int]]:
    """{источник: {'published': n, 'rejected': m}} — только решения автора."""
    q = ("SELECT source, status, COUNT(*) c FROM posts "
         "WHERE status IN ('published','rejected','approved')")
    args: tuple = ()
    if days:
        q += " AND decided_at >= ?"
        args = (days_ago(days),)
    async with connect() as db:
        cur = await db.execute(q + " GROUP BY source, status", args)
        rows = await cur.fetchall()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        key = "rejected" if r["status"] == "rejected" else "published"
        d = out.setdefault(r["source"] or "?", {"published": 0, "rejected": 0})
        d[key] += r["c"]
    return out


async def digest(days: int = 7) -> dict:
    since = days_ago(days)
    async with connect() as db:
        cur = await db.execute(
            "SELECT format, COUNT(*) c FROM posts WHERE status='published' AND decided_at>=? GROUP BY format",
            (since,))
        by_format = {r["format"]: r["c"] for r in await cur.fetchall()}
        cur = await db.execute(
            "SELECT category, COUNT(*) c FROM posts WHERE status='published' AND decided_at>=? GROUP BY category",
            (since,))
        by_category = {r["category"]: r["c"] for r in await cur.fetchall()}
        cur = await db.execute(
            "SELECT reject_reason, COUNT(*) c FROM posts WHERE status='rejected' AND decided_at>=? "
            "GROUP BY reject_reason", (since,))
        reasons = {r["reject_reason"] or "?": r["c"] for r in await cur.fetchall()}
        cur = await db.execute(
            "SELECT COALESCE(SUM(calls),0) calls, COALESCE(SUM(in_tok),0) i, COALESCE(SUM(out_tok),0) o "
            "FROM usage WHERE day>=?", (since[:10],))
        u = await cur.fetchone()
    return {"by_format": by_format, "by_category": by_category, "reasons": reasons,
            "sources": await source_stats(days),
            "usage": {"calls": u["calls"], "in_tok": u["i"], "out_tok": u["o"]}}


# ---------- notes ----------

async def add_note(topic: str, brief: dict | None = None, status: str = "research") -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO notes(topic, brief, status, created_at) VALUES (?,?,?,?)",
            (topic, json.dumps(brief or {}, ensure_ascii=False), status, now()))
        await db.commit()
        return cur.lastrowid


async def get_note(nid: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM notes WHERE id=?", (nid,))
        return await cur.fetchone()


async def update_note(nid: int, **f) -> None:
    if "brief" in f and not isinstance(f["brief"], str):
        f["brief"] = json.dumps(f["brief"], ensure_ascii=False)
    sets = ",".join(f"{k}=?" for k in f)
    async with connect() as db:
        await db.execute(f"UPDATE notes SET {sets} WHERE id=?", (*f.values(), nid))
        await db.commit()


async def note_topics(limit: int = 30) -> list[str]:
    async with connect() as db:
        cur = await db.execute("SELECT topic FROM notes ORDER BY id DESC LIMIT ?", (limit,))
        return [r["topic"] for r in await cur.fetchall()]
