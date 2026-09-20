"""Карусель Instagram без полей.

Instagram держит всю карусель в одной пропорции, поэтому раньше кадры другой формы
вписывались целиком и получали поля — чаще всего тонкую рамку в пару десятков пикселей.

Теперь иначе:
  1. Пропорция поста считается по медиане пропорций его фотографий (зажата в 4:5…1.91:1),
     а не по первому кадру: набор из горизонталей 3:2 даёт ровно 3:2 и ни одной обрезки.
  2. Кадр, теряющий при обрезке до этой пропорции не больше 15% площади, просто режется.
  3. Кадр, теряющий больше, в карусель не идёт. После отсева медиана считается заново
     по оставшимся — обычно это убирает и часть обрезок.
  4. Обложка (первый кадр) не выбрасывается никогда: если из общей пропорции выпадает она,
     пропорция считается по ней, а не вписывающиеся кадры уходят.
  5. Если после отсева осталось меньше трёх кадров, пропорция берётся по обложке и режутся все —
     но только пока самый тяжёлый кроп не превышает 30%. Если превышает, карусель просто
     становится короче: пост из одного-двух кадров лучше, чем кадр, разрезанный пополам.

Полей не остаётся ни в одном случае, IG_PAD_COLOR больше ни на что не влияет.
В Telegram уходит полный набор фото в своих пропорциях — этот модуль трогает только Instagram.

Сам instagram.py не менялся: app/__init__.py пристёгивает сюда подготовку фото (instagram._photos).
Обрезка делается до вызова instagram._fit, поэтому логотип по-прежнему встаёт в угол кадра."""
import json
import logging
import os
import secrets
import shutil
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

MAX_CROP = float(os.getenv("IG_MAX_CROP", "0.15"))      # доля площади, которую можно срезать
MIN_PHOTOS = int(os.getenv("IG_MIN_PHOTOS", "3"))       # меньше — пропорция по обложке, режем всех
HARD_CROP = float(os.getenv("IG_HARD_CROP", "0.30"))    # потолок для такой вынужденной обрезки
TOP_BIAS = float(os.getenv("IG_TOP_BIAS", "0.35"))      # вертикальный срез: 0.5 по центру, меньше — ближе к верху
IG_MIN, IG_MAX = 0.8, 1.91                              # пределы Instagram: 4:5 … 1.91:1

_attached: set[str] = set()


# ======================= расчёт =======================

def _clamp(r: float) -> float:
    return min(max(r, IG_MIN), IG_MAX)


def _read_ratio(path: Path) -> float:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return im.width / im.height


def _loss(r: float, target: float) -> float:
    """Доля площади, теряемая при обрезке кадра пропорции r до target."""
    return 1 - min(r, target) / max(r, target)


def _median(values: list[float]) -> float:
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def plan(ratios: list[float]) -> tuple[float, list[int]]:
    """Пропорции кадров → (пропорция карусели, номера кадров, которые в неё идут)."""
    n = len(ratios)
    if n == 1:
        return _clamp(ratios[0]), [0]

    cover = _clamp(ratios[0])
    target = _clamp(_median(ratios))
    if _loss(ratios[0], target) > MAX_CROP:              # обложка выпадает — пропорция по ней
        target = cover
    else:
        first = [i for i in range(n) if _loss(ratios[i], target) <= MAX_CROP]
        if len(first) < n:                               # пересчёт медианы по оставшимся
            again = _clamp(_median([ratios[i] for i in first]))
            if _loss(ratios[0], again) <= MAX_CROP:
                target = again

    keep = [i for i in range(n) if _loss(ratios[i], target) <= MAX_CROP]
    if 0 not in keep:                                    # кадр вне пределов Instagram, но это обложка
        keep.insert(0, 0)

    if n >= MIN_PHOTOS and len(keep) < MIN_PHOTOS:       # карусель обмелела
        worst = max(_loss(r, cover) for r in ratios)
        if worst <= HARD_CROP:                           # дотянуть всех до обложки — терпимо
            return cover, list(range(n))
    return target, keep


def crop(im: Image.Image, target: float) -> Image.Image:
    """Кадр строго под пропорцию: по горизонтали — по центру, по вертикали — со смещением к верху."""
    im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    r = w / h
    if abs(r - target) / target < 0.002:
        return im
    if r > target:                                       # шире цели — режем бока
        nw, nh = max(1, round(h * target)), h
        x, y = (w - nw) // 2, 0
    else:                                                # выше цели — режем верх и низ
        nw, nh = w, max(1, round(w / target))
        x, y = 0, round((h - nh) * TOP_BIAS)
    return im.crop((x, y, x + nw, y + nh))


# ======================= подготовка фото поста =======================

async def _photos(bot, post) -> tuple[str, list[str]]:
    """Фото поста → JPEG одной пропорции в публичной папке, без полей. → (токен папки, ссылки)"""
    from app import cards, instagram

    images = json.loads(post["images"] or "[]")
    fids = cards._fids(post)
    token = secrets.token_urlsafe(18)
    folder = instagram.PUBLIC_DIR / token
    folder.mkdir(parents=True, exist_ok=True)

    raw: list[Path] = []
    for n, i in enumerate(cards.photo_plan(post)[:10]):
        src = Path(images[i]) if i < len(images) else None
        tmp = folder / f"src{n:02d}"
        if src and src.exists():
            shutil.copyfile(src, tmp)
        elif fids.get(str(i)):
            await bot.download(fids[str(i)], destination=tmp)
        else:
            continue
        raw.append(tmp)

    files, ratios = [], []
    for p in raw:
        try:
            ratios.append(_read_ratio(p))
            files.append(p)
        except Exception:
            log.warning("Instagram: файл %s не открылся, кадр пропущен", p.name, exc_info=True)
            p.unlink(missing_ok=True)
    if not files:
        shutil.rmtree(folder, ignore_errors=True)
        raise RuntimeError("у поста не нашлось фото — ни на диске, ни в Telegram")

    target, keep = plan(ratios)
    w = instagram.WIDTH
    h = max(1, round(w / target))
    exact = w / h                                        # пропорция готового кадра, с учётом округления
    if len(keep) < len(files):
        log.info("Instagram: пропорция %.3f, в карусель идут %d из %d кадров (остальные не влезают без полей)",
                 exact, len(keep), len(files))

    urls, base, out = [], instagram.public_url(), 0
    for n, p in enumerate(files):
        if n in keep:
            with Image.open(p) as im:
                # обрезка до точной пропорции кадра: дальше instagram._fit только масштабирует
                # и ставит логотип, полей не появляется
                instagram._fit(crop(im, exact), w, h).save(folder / f"{out:02d}.jpg", "JPEG", quality=92)
            urls.append(f"{base}/ig/{token}/{out:02d}.jpg")
            out += 1
        p.unlink(missing_ok=True)
    return token, urls


# ======================= подключение =======================

def attach(module) -> None:
    name = module.__name__
    if name != "app.instagram" or name in _attached:
        return
    orig = getattr(module, "_photos", None)
    if orig is None:
        log.error("Карусель: в app.instagram нет _photos — фото пойдут по-старому, с полями")
        return
    _photos.__wrapped__ = orig
    module._photos = _photos
    _attached.add(name)
    log.info("Карусель Instagram: без полей (обрезка до %d%% площади, кадры сверх того не идут)",
             round(MAX_CROP * 100))


def status() -> str:
    return ", ".join(sorted(_attached)) or "не подключена"
