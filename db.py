"""NUMBUS Branding — хранилище (SQLite).

Один файл numbus.db на Railway Volume. Соединение открывается на каждый
вызов — так безопасно при рендере в потоках и достаточно быстро для бота.
"""
import os
import json
import secrets
import sqlite3
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))


def _resolve_data_dir():
    """Railway сам выставляет RAILWAY_VOLUME_MOUNT_PATH при подключённом
    Volume. Иначе /data, иначе папка рядом с ботом (эфемерно)."""
    vol = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    candidates = ([(vol, True)] if vol else []) + [("/data", True), (os.path.join(BASE, "data"), False)]
    for d, persistent in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d, persistent
        except Exception:
            pass
    return BASE, False


DATA_DIR, STORAGE_PERSISTENT = _resolve_data_dir()
DB_PATH = os.environ.get("NUMBUS_DB") or os.path.join(DATA_DIR, "numbus.db")

# ============ Тарифы ============
# photos — лимит обработанных фото в календарный месяц (UTC)
# members — сколько человек в команде бренда (включая владельца)
PLANS = {
    "pilot":  {"photos": 500,  "members": 5},
    "solo":   {"photos": 150,  "members": 1},
    "media":  {"photos": 1000, "members": 5},
    "studio": {"photos": 5000, "members": 20},
}

DEFAULT_KIT = {
    "name": "",
    # p0 — светлый, p1 — тёмный, p2 — акцент, p3–p4 — дополнительные
    "palette": ["#FFFFFF", "#141414", "#D9D9D9", "#7A7A7A", "#FFFFFF"],
    "hashtags": [],
    "custom_fonts": {},    # {"font1": "Название шрифта"}
}
MAX_TEMPLATES = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    lang TEXT,
    active_brand INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    kit TEXT NOT NULL,
    plan TEXT NOT NULL,
    plan_until TEXT,
    join_token TEXT UNIQUE,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS members (
    brand_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (brand_id, tg_id)
);
CREATE TABLE IF NOT EXISTS assets (
    brand_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (brand_id, kind)
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    days INTEGER NOT NULL,
    max_uses INTEGER NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    template TEXT NOT NULL,
    photos INTEGER NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_brand_ts ON events (brand_id, ts);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_templates_brand ON templates (brand_id, sort);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- link (одноразовая, 15 мин) | session (7 дней)
    tg_id INTEGER NOT NULL,
    brand_id INTEGER NOT NULL,
    expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prefs (
    tg_id INTEGER NOT NULL,
    brand_id INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (tg_id, brand_id)
);
"""


def _now():
    return datetime.now(timezone.utc)


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
    logger.info("БД: %s (%s)", DB_PATH, "постоянная" if STORAGE_PERSISTENT else "ВРЕМЕННАЯ")


# ============ Пользователи ============
def ensure_user(tg_id: int, lang_hint: str = None) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if row:
            return dict(row)
        lang = "ru" if (lang_hint or "").lower().startswith(("ru", "uk", "be", "kk")) else "en"
        c.execute("INSERT INTO users (tg_id, lang, active_brand, created_at) VALUES (?,?,?,?)",
                  (tg_id, lang, None, _now().isoformat()))
        return {"tg_id": tg_id, "lang": lang, "active_brand": None}


def get_user(tg_id: int):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(row) if row else None


def set_lang(tg_id: int, lang: str):
    with _conn() as c:
        c.execute("UPDATE users SET lang=? WHERE tg_id=?", (lang, tg_id))


def set_active_brand(tg_id: int, brand_id):
    with _conn() as c:
        c.execute("UPDATE users SET active_brand=? WHERE tg_id=?", (brand_id, tg_id))


# ============ Бренды ============
def _brand_row(row) -> dict:
    b = dict(row)
    kit = dict(DEFAULT_KIT)
    try:
        kit.update(json.loads(b["kit"]))
    except Exception:
        pass
    b["kit"] = kit
    return b


def create_brand(owner_id: int, plan: str, days: int) -> int:
    until = (_now() + timedelta(days=days)).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO brands (owner_id, kit, plan, plan_until, join_token, created_at) VALUES (?,?,?,?,?,?)",
            (owner_id, json.dumps(DEFAULT_KIT, ensure_ascii=False), plan, until,
             secrets.token_urlsafe(9), _now().isoformat()))
        bid = cur.lastrowid
        c.execute("INSERT INTO members (brand_id, tg_id, role) VALUES (?,?,?)", (bid, owner_id, "owner"))
        c.execute("UPDATE users SET active_brand=? WHERE tg_id=?", (bid, owner_id))
    seed_templates(bid)
    return bid


def get_brand(brand_id):
    if not brand_id:
        return None
    with _conn() as c:
        row = c.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
        return _brand_row(row) if row else None


def update_kit(brand_id: int, **changes):
    b = get_brand(brand_id)
    if not b:
        return
    kit = b["kit"]
    kit.update(changes)
    with _conn() as c:
        c.execute("UPDATE brands SET kit=? WHERE id=?", (json.dumps(kit, ensure_ascii=False), brand_id))


def user_brands(tg_id: int) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT b.*, m.role FROM brands b JOIN members m ON m.brand_id=b.id "
            "WHERE m.tg_id=? ORDER BY b.id", (tg_id,)).fetchall()
        out = []
        for r in rows:
            b = _brand_row(r)
            out.append(b)
        return out


def member_role(brand_id: int, tg_id: int):
    with _conn() as c:
        row = c.execute("SELECT role FROM members WHERE brand_id=? AND tg_id=?",
                        (brand_id, tg_id)).fetchone()
        return row["role"] if row else None


def member_count(brand_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM members WHERE brand_id=?", (brand_id,)).fetchone()[0]


def brand_by_token(token: str):
    with _conn() as c:
        row = c.execute("SELECT * FROM brands WHERE join_token=?", (token,)).fetchone()
        return _brand_row(row) if row else None


def add_member(brand_id: int, tg_id: int, role: str = "editor"):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO members (brand_id, tg_id, role) VALUES (?,?,?)",
                  (brand_id, tg_id, role))


def list_brands() -> list:
    with _conn() as c:
        rows = c.execute("SELECT * FROM brands ORDER BY id").fetchall()
        return [_brand_row(r) for r in rows]


def extend_brand(brand_id: int, days: int, plan: str = None) -> bool:
    b = get_brand(brand_id)
    if not b:
        return False
    try:
        cur = datetime.fromisoformat(b["plan_until"])
    except Exception:
        cur = _now()
    base = max(cur, _now())
    until = (base + timedelta(days=days)).isoformat()
    with _conn() as c:
        c.execute("UPDATE brands SET plan_until=?, plan=? WHERE id=?",
                  (until, plan or b["plan"], brand_id))
    return True


def plan_active(brand: dict) -> bool:
    try:
        return datetime.fromisoformat(brand["plan_until"]) > _now()
    except Exception:
        return False


# ============ Ассеты (логотипы, шрифт, фото-образец) ============
def set_asset(brand_id: int, kind: str, data: bytes):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO assets (brand_id, kind, data) VALUES (?,?,?)",
                  (brand_id, kind, sqlite3.Binary(data)))


def get_asset(brand_id: int, kind: str):
    with _conn() as c:
        row = c.execute("SELECT data FROM assets WHERE brand_id=? AND kind=?",
                        (brand_id, kind)).fetchone()
        return bytes(row["data"]) if row else None


def del_asset(brand_id: int, kind: str):
    with _conn() as c:
        c.execute("DELETE FROM assets WHERE brand_id=? AND kind=?", (brand_id, kind))


# ============ Инвайт-коды ============
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без 0/O/1/I — не путаются


def create_invite(plan: str, days: int, max_uses: int = 1) -> str:
    code = "NB-" + "".join(secrets.choice(_ALPHABET) for _ in range(4)) + "-" + \
           "".join(secrets.choice(_ALPHABET) for _ in range(4))
    with _conn() as c:
        c.execute("INSERT INTO invites (code, plan, days, max_uses, uses, created_at) VALUES (?,?,?,?,0,?)",
                  (code, plan, days, max_uses, _now().isoformat()))
    return code


def redeem_invite(code: str):
    """Атомарно тратит одно использование кода. Возвращает (plan, days) или None."""
    code = (code or "").strip().upper()
    with _conn() as c:
        row = c.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
        if not row or row["uses"] >= row["max_uses"]:
            return None
        cur = c.execute("UPDATE invites SET uses=uses+1 WHERE code=? AND uses<max_uses", (code,))
        if cur.rowcount != 1:
            return None
        return row["plan"], row["days"]


def list_invites() -> list:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM invites ORDER BY created_at DESC LIMIT 30")]


# ============ Использование ============
def month_start():
    n = _now()
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def photos_used(brand_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COALESCE(SUM(photos),0) FROM events WHERE brand_id=? AND ts>=?",
                         (brand_id, month_start().isoformat())).fetchone()[0]


def record_event(brand_id: int, tg_id: int, template: str, photos: int):
    if photos <= 0:
        return
    with _conn() as c:
        c.execute("INSERT INTO events (brand_id, tg_id, template, photos, ts) VALUES (?,?,?,?,?)",
                  (brand_id, tg_id, template, photos, _now().isoformat()))


def has_asset(brand_id: int, kind: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM assets WHERE brand_id=? AND kind=?", (brand_id, kind)).fetchone() is not None


def regen_token(brand_id: int) -> str:
    tok = secrets.token_urlsafe(9)
    with _conn() as c:
        c.execute("UPDATE brands SET join_token=? WHERE id=?", (tok, brand_id))
    return tok


# ============ Шаблоны ============
def seed_templates(brand_id: int, lang: str = "ru"):
    import spec as S
    for key in S.SEED_PRESETS:
        p = S.preset(key)
        create_template(brand_id, p["name"].get(lang) or p["name"]["ru"], S.preset_spec(key))


def _tpl_row(r):
    t = dict(r)
    try:
        t["spec"] = json.loads(t["spec"])
    except Exception:
        t["spec"] = {"v": 1, "feed": {"layers": []}, "story": {"enabled": False, "layers": []}}
    return t


def list_templates(brand_id: int) -> list:
    with _conn() as c:
        return [_tpl_row(r) for r in c.execute(
            "SELECT * FROM templates WHERE brand_id=? ORDER BY sort, id", (brand_id,))]


def get_template(brand_id: int, tid: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM templates WHERE id=? AND brand_id=?", (tid, brand_id)).fetchone()
        return _tpl_row(r) if r else None


def template_count(brand_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM templates WHERE brand_id=?", (brand_id,)).fetchone()[0]


def create_template(brand_id: int, name: str, spec: dict):
    if template_count(brand_id) >= MAX_TEMPLATES:
        return None
    with _conn() as c:
        sort = c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM templates WHERE brand_id=?", (brand_id,)).fetchone()[0]
        cur = c.execute("INSERT INTO templates (brand_id, name, spec, sort, updated_at) VALUES (?,?,?,?,?)",
                        (brand_id, name[:40], json.dumps(spec, ensure_ascii=False), sort, _now().isoformat()))
        return cur.lastrowid


def update_template(brand_id: int, tid: int, name: str, spec: dict) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE templates SET name=?, spec=?, updated_at=? WHERE id=? AND brand_id=?",
                        (name[:40], json.dumps(spec, ensure_ascii=False), _now().isoformat(), tid, brand_id))
        return cur.rowcount == 1


def delete_template(brand_id: int, tid: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM templates WHERE id=? AND brand_id=?", (tid, brand_id)).rowcount == 1


# ============ Последние настройки поста (для быстрого режима) ============
def get_prefs(tg_id: int, brand_id: int) -> dict:
    with _conn() as c:
        r = c.execute("SELECT data FROM prefs WHERE tg_id=? AND brand_id=?", (tg_id, brand_id)).fetchone()
    try:
        return json.loads(r["data"]) if r else {}
    except Exception:
        return {}


def set_prefs(tg_id: int, brand_id: int, **changes):
    data = get_prefs(tg_id, brand_id)
    data.update(changes)
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO prefs (tg_id, brand_id, data) VALUES (?,?,?)",
                  (tg_id, brand_id, json.dumps(data, ensure_ascii=False)))


# ============ Графика из импортированных макетов ============
def image_assets(brand_id: int) -> list:
    with _conn() as c:
        return [r["kind"] for r in c.execute(
            "SELECT kind FROM assets WHERE brand_id=? AND kind LIKE 'img\\_%' ESCAPE '\\'", (brand_id,))]


def gc_images(brand_id: int) -> int:
    """Удаляет картинки, на которые не ссылается ни один шаблон бренда."""
    used = set()
    for t in list_templates(brand_id):
        for surf in ("feed", "story"):
            for L in t["spec"].get(surf, {}).get("layers", []):
                if L.get("type") == "image":
                    used.add(L.get("asset"))
    dead = [k for k in image_assets(brand_id) if k not in used]
    for k in dead:
        del_asset(brand_id, k)
    return len(dead)


# ============ Вход на компьютере: одноразовая ссылка → сессия ============
LINK_TTL_MIN = 15
SESSION_TTL_DAYS = 7


def _token():
    import secrets
    return secrets.token_urlsafe(24)


def create_login_link(tg_id: int, brand_id: int) -> str:
    tok = _token()
    exp = _now() + timedelta(minutes=LINK_TTL_MIN)
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (_now().isoformat(),))
        c.execute("INSERT INTO sessions (token, kind, tg_id, brand_id, expires) VALUES (?,?,?,?,?)",
                  (tok, "link", tg_id, brand_id, exp.isoformat()))
    return tok


def redeem_login_link(tok: str):
    """Одноразовая ссылка → (session_token, tg_id, brand_id) или None."""
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE token=? AND kind='link'", (tok or "",)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM sessions WHERE token=?", (tok,))
        if r["expires"] < _now().isoformat():
            return None
        sess = _token()
        c.execute("INSERT INTO sessions (token, kind, tg_id, brand_id, expires) VALUES (?,?,?,?,?)",
                  (sess, "session", r["tg_id"], r["brand_id"],
                   (_now() + timedelta(days=SESSION_TTL_DAYS)).isoformat()))
        return sess, r["tg_id"], r["brand_id"]


def check_session(tok: str):
    if not tok:
        return None
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE token=? AND kind='session'", (tok,)).fetchone()
    if not r or r["expires"] < _now().isoformat():
        return None
    return r["tg_id"], r["brand_id"]


def drop_session(tok: str):
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (tok or "",))
