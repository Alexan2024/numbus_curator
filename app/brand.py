"""Логотип AHMAG на фото, которые уходят в канал и в Instagram.

Размеры заданы для кадра шириной 1080 px и масштабируются по ширине фото:
знак 69×62, отступ 68 от левого и 68 от нижнего края, непрозрачность 65%.
Цвет знака выбирается по фону под ним: на светлом — чёрный, на тёмном — белый.

Сами cards.py и instagram.py не менялись: app/__init__.py пристёгивает сюда
отправку фото в канал (cards._send_photos) и подготовку фото для Instagram (instagram._fit).
Выключить — переменная Railway BRAND=0."""
import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

log = logging.getLogger(__name__)

BASE_W = 1080
LOGO_W, LOGO_H = 39, 35
MARGIN_LEFT, MARGIN_BOTTOM = 38, 38
OPACITY = float(os.getenv("BRAND_OPACITY", "0.65"))
LIGHT_BG = 140          # средняя яркость фона под знаком (0–255), выше — знак чёрный

ASSETS = Path(__file__).resolve().parent.parent / "data" / "brand"
LOGOS = {"black": ASSETS / "logo_black.png", "white": ASSETS / "logo_white.png"}

_cache: dict[tuple[str, int, int], Image.Image] = {}
_attached: set[str] = set()


def enabled() -> bool:
    return os.getenv("BRAND", "1").strip().lower() not in ("0", "false", "no", "off")


def _logo(color: str, w: int, h: int) -> Image.Image:
    """Логотип нужного цвета и размера (RGBA), обрезанный по видимым границам знака."""
    key = (color, w, h)
    if key not in _cache:
        with Image.open(LOGOS[color]) as src:
            src = src.convert("RGBA")
            src = src.crop(src.getchannel("A").getbbox())
            _cache[key] = src.resize((w, h), Image.LANCZOS)
    return _cache[key]


def stamp(im: Image.Image, box: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Фото с логотипом в левом нижнем углу. → новое RGB-изображение.
    box — где внутри кадра лежит сам снимок (x, y, ширина, высота), если вокруг него поля:
    знак встаёт в угол снимка, а не полей. Размер знака всегда считается от ширины всего кадра."""
    im = im.convert("RGB")
    W, H = im.size
    bx, by, bw, bh = box or (0, 0, W, H)
    s = W / BASE_W
    w, h = max(1, round(LOGO_W * s)), max(1, round(LOGO_H * s))
    x, y = bx + round(MARGIN_LEFT * s), by + bh - round(MARGIN_BOTTOM * s) - h
    if y < by or x + w > bx + bw:
        return im
    color = "black" if ImageStat.Stat(im.crop((x, y, x + w, y + h)).convert("L")).mean[0] > LIGHT_BG else "white"
    logo = _logo(color, w, h)
    alpha = logo.getchannel("A").point(lambda a: round(a * OPACITY))
    out = im.copy()
    out.paste(logo.convert("RGB"), (x, y), alpha)
    return out


def stamp_file(src: Path, dst: Path) -> Path:
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        stamp(im).save(dst, "JPEG", quality=95)
    return dst


# ======================= подключение к модулям бота =======================

def _tmp_dir() -> Path:
    from app import config
    d = config.DATA_DIR / "brand_tmp" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    return d


def _wrap_send(orig):
    """Фото, которые уходят в канал, — с логотипом. Предпросмотр и всё, что приходит тебе в личку, — без."""
    from aiogram.types import FSInputFile

    async def _send_photos(bot, chat_id, files, caption):
        from app import config
        if not enabled() or not files or str(chat_id) != str(config.CHANNEL_ID):
            return await orig(bot, chat_id, files, caption)
        tmp = _tmp_dir()
        try:
            out = []
            for n, f in enumerate(files):
                try:
                    if isinstance(f, FSInputFile):
                        src = Path(f.path)
                    else:                           # file_id — фото уже в Telegram, без логотипа
                        src = tmp / f"src{n:02d}"
                        await bot.download(f, destination=src)
                    dst = await asyncio.to_thread(stamp_file, src, tmp / f"{n:02d}.jpg")
                    out.append(FSInputFile(dst))
                except Exception:
                    log.warning("Логотип: фото %s ушло без знака", n + 1, exc_info=True)
                    out.append(f)
            return await orig(bot, chat_id, out, caption)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    _send_photos.__wrapped__ = orig
    return _send_photos


def _photo_box(im, w: int, h: int) -> tuple[int, int, int, int]:
    """Где снимок лежит в кадре Instagram — по тому же правилу, что в instagram._fit:
    почти та же пропорция — обрезка на весь кадр, иначе снимок по центру с полями."""
    iw, ih = ImageOps.exif_transpose(im).size
    if abs(iw / ih - w / h) / (w / h) < 0.03:
        return 0, 0, w, h
    k = min(w / iw, h / ih)
    pw, ph = max(1, round(iw * k)), max(1, round(ih * k))
    return (w - pw) // 2, (h - ph) // 2, pw, ph


def _wrap_fit(orig):
    """Фото для Instagram: сначала приводится к пропорции карусели (кадр 1080 px), потом получает знак.
    Если вокруг снимка поля, знак стоит в углу снимка; размер одинаковый на всех фото карусели."""
    def _fit(im, w, h):
        out = orig(im, w, h)
        if not enabled():
            return out
        try:
            return stamp(out, _photo_box(im, w, h))
        except Exception:
            log.warning("Логотип: фото для Instagram ушло без знака", exc_info=True)
            return out

    _fit.__wrapped__ = orig
    return _fit


def attach(module) -> None:
    name = module.__name__
    if name in _attached:
        return
    target = {"app.cards": ("_send_photos", _wrap_send), "app.instagram": ("_fit", _wrap_fit)}.get(name)
    if not target:
        return
    attr, wrap = target
    fn = getattr(module, attr, None)
    if fn is None:
        log.error("Логотип: в %s нет %s — фото там пойдут без знака", name, attr)
        return
    setattr(module, attr, wrap(fn))
    _attached.add(name)
    log.info("Логотип подключён: %s (%s)", name, "вкл" if enabled() else "выкл, BRAND=0")


def status() -> str:
    return ", ".join(sorted(_attached)) or "не подключён"
