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
DELIVERY_HOURS = [int(h) for h in os.getenv("DELIVERY_HOURS", "10,14,19").split(",")]

# Фото
MIN_LONG_SIDE = int(os.getenv("MIN_LONG_SIDE", "1200"))
MIN_SHORT_SIDE = int(os.getenv("MIN_SHORT_SIDE", "700"))
MIN_PHOTOS_ARTICLE = int(os.getenv("MIN_PHOTOS_ARTICLE", "4"))
MAX_PHOTOS = 10
CAPTION_LIMIT = 1024  # лимит подписи к альбому в Telegram

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
