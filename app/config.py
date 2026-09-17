import os
from pathlib import Path


def _req(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


BOT_TOKEN = _req("BOT_TOKEN")
ADMIN_ID = int(_req("ADMIN_ID"))
CHANNEL_ID = _req("CHANNEL_ID")  # @username или -100...
ANTHROPIC_API_KEY = _req("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

TZ_NAME = os.getenv("TZ_NAME", "Europe/Moscow")
DAILY_MAX = int(os.getenv("DAILY_MAX", "10"))
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "7"))
COLLECT_EVERY_HOURS = int(os.getenv("COLLECT_EVERY_HOURS", "4"))
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "12"))
MAX_READY_QUEUE = int(os.getenv("MAX_READY_QUEUE", "40"))
# Ручной режим: когда присылать карточки на модерацию
DELIVERY_HOURS = [int(h) for h in os.getenv("DELIVERY_HOURS", "10,14,19").split(",")]


# ---------- слоты публикации ----------
def _parse_slots(raw: str) -> list[tuple[int, int, str]]:
    """'10:00=std,16:00=mini' → [(10, 0, 'std'), (16, 0, 'mini')]"""
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        t, _, fmt = part.partition("=")
        h, _, m = t.partition(":")
        fmt = (fmt or "std").strip().lower()
        if fmt not in ("std", "mini"):
            raise RuntimeError(f"SLOTS: неизвестный формат «{fmt}» (нужно std или mini)")
        out.append((int(h), int(m or 0), fmt))
    return sorted(out)


# 4 стандартных поста + 1 мини. Мини можно добавлять сколько угодно: «…,12:00=mini,17:30=mini»
SLOTS = _parse_slots(os.getenv("SLOTS", "10:00=std,13:00=std,16:00=mini,19:00=std,21:30=std"))
SLOT_LEAD_MIN = int(os.getenv("SLOT_LEAD_MIN", "30"))     # за сколько минут до слота бот готовит варианты / анонс
SEMI_OPTIONS = int(os.getenv("SEMI_OPTIONS", "3"))        # вариантов на слот в полуавтомате
AUTO_MIN_SCORE = int(os.getenv("AUTO_MIN_SCORE", "8"))    # автомат публикует сам только от этой оценки
MAX_OFFERS = int(os.getenv("MAX_OFFERS", "2"))            # сколько раз предлагать пост, прежде чем снять

# Расход API: фоновая обработка останавливается на этом числе вызовов Claude в сутки.
# Действия по кнопкам (переписать, ссылка, notes) лимитом не режутся.
DAILY_API_CALLS_MAX = int(os.getenv("DAILY_API_CALLS_MAX", "150"))
NOTES_WEB_SEARCHES = int(os.getenv("NOTES_WEB_SEARCHES", "6"))

# Музейный open access — приправа, а не основа ленты
MET_PER_RUN = int(os.getenv("MET_PER_RUN", "2"))       # сколько объектов Met оценивать за проход
MET_DAILY_MAX = int(os.getenv("MET_DAILY_MAX", "2"))   # сколько карточек Met в день

# Фото
MIN_LONG_SIDE = int(os.getenv("MIN_LONG_SIDE", "1200"))
MIN_SHORT_SIDE = int(os.getenv("MIN_SHORT_SIDE", "700"))
MIN_PHOTOS_ARTICLE = int(os.getenv("MIN_PHOTOS_ARTICLE", "3"))   # для стандартного поста
MIN_PHOTOS_MINI = int(os.getenv("MIN_PHOTOS_MINI", "2"))         # для мини-поста
MAX_PHOTOS = 10
MINI_MAX_PHOTOS = int(os.getenv("MINI_MAX_PHOTOS", "4"))
CAPTION_LIMIT = 1024  # лимит подписи к альбому в Telegram
MESSAGE_LIMIT = 4096
IMAGE_TTL_DAYS = int(os.getenv("IMAGE_TTL_DAYS", "14"))  # сколько хранить файлы решённых постов

# Недельный дайджест
DIGEST_DOW = os.getenv("DIGEST_DOW", "sun")
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "20"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "ahmag.db"
IMG_DIR = DATA_DIR / "images"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILE_PATH = ASSETS_DIR / "ahmag_taste_profile.md"
ARCHIVE_PATH = ASSETS_DIR / "ahmag_posts.json"

# Целевой микс рубрик в выдаче
TARGET_MIX = {
    "architecture": 0.60,
    "art": 0.20,
    "photography": 0.08,
    "archive": 0.07,
    "cinema": 0.03,
    "interiors": 0.02,
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
