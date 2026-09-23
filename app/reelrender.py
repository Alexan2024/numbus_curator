"""Сборка рилса: кадры рисует Pillow, склеивает ffmpeg (бинарник приезжает пакетом imageio-ffmpeg).

Кадр 1080×1920, 30 к/с, без звука — музыку автор кладёт сам в Instagram.
Камера — прямоугольник 9:16 внутри «сцены» (картинки): плавно едет и приближается, координаты дробные,
поэтому движение без дрожи. Для скорости у сцены есть пирамида уменьшенных копий.

Два вида:
  подборка — по кадру на работу, у каждой свой медленный наезд или проезд, сверху название и автор;
             первый кадр ещё и с названием подборки;
  детали   — одна картина: общий план, затем камера по очереди переезжает к деталям, под каждой фраза,
             в конце снова общий план и подпись «название, автор, музей».
"""
import logging
import math
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

log = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = int(os.getenv("REEL_FPS", "30"))
ASPECT = H / W
FONTS = Path(__file__).resolve().parent.parent / "data" / "fonts"
FADE = 0.35                     # появление и исчезновение текста, с
LOGO_BOTTOM = int(os.getenv("REEL_LOGO_BOTTOM", "380"))   # знак выше подписи Instagram, иначе её плашка его закроет
MAX_SIDE = 4200                 # больше не нужно даже для крупных деталей


# ======================= ffmpeg =======================

def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def ffmpeg_ok() -> bool:
    exe = ffmpeg_exe()
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-version"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


# ======================= шрифты и текст =======================

_fonts: dict = {}


def font(size: int, medium: bool = True):
    key = (size, medium)
    if key not in _fonts:
        name = "IBMPlexSans-Medium.ttf" if medium else "IBMPlexSans-Regular.ttf"
        try:
            _fonts[key] = ImageFont.truetype(str(FONTS / name), size)
        except OSError:
            _fonts[key] = ImageFont.load_default(size=size)
    return _fonts[key]


def _wrap(text: str, f, width: int, max_lines: int = 4) -> list[str]:
    d = ImageDraw.Draw(Image.new("L", (10, 10)))
    out: list[str] = []
    for para in str(text).split("\n"):
        line = ""
        for w in para.split():
            test = f"{line} {w}".strip()
            if d.textlength(test, font=f) <= width or not line:
                line = test
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
    if len(out) > max_lines:
        out = out[:max_lines]
        out[-1] = out[-1].rstrip(".,;: ") + "…"
    return out


def _balanced(text: str, f, width: int) -> list[str]:
    """Перенос без «висящего» слова: столько же строк, но самая узкая ширина, при которой их не больше."""
    best = _wrap(text, f, width)
    if len(best) < 2:
        return best
    lo, hi = width // 2, width
    while hi - lo > 8:
        mid = (lo + hi) // 2
        if len(_wrap(text, f, mid)) <= len(best):
            hi = mid
        else:
            lo = mid
    return _wrap(text, f, hi)


def text_block(lines: list[tuple[str, object, int]], width: int = 900, gap: int = 14,
               shadow: bool = True) -> Image.Image:
    """Блок строк по центру: [(текст, шрифт, отступ сверху)] → RGBA с мягкой тенью."""
    d = ImageDraw.Draw(Image.new("L", (10, 10)))
    rows, h = [], 0
    for text, f, top in lines:
        for i, ln in enumerate(_balanced(text, f, width)):
            asc, desc = f.getmetrics()
            rows.append((ln, f, h + (top if i == 0 else 0)))
            h += (top if i == 0 else 0) + asc + desc + gap
    h = max(h - gap, 1)
    pad = 40
    im = Image.new("RGBA", (width + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    ink = Image.new("L", im.size, 0)
    di = ImageDraw.Draw(ink)
    for ln, f, y in rows:
        x = pad + (width - d.textlength(ln, font=f)) / 2
        di.text((x, pad + y), ln, font=f, fill=255)
    if shadow:
        sh = ink.filter(ImageFilter.GaussianBlur(10)).point(lambda a: min(255, int(a * 1.5)))
        im.paste((0, 0, 0, 150), (0, 0), sh.point(lambda a: int(a * 0.55)))
    im.paste((255, 255, 255, 255), (0, 0), ink)
    return im


def gradient(top: bool, height: int = 760, strength: float = 0.62) -> Image.Image:
    """Затемнение сверху или снизу, чтобы белый текст читался на любом фоне."""
    g = Image.new("L", (1, height))
    for y in range(height):
        t = 1 - y / height if top else y / height
        g.putpixel((0, y), int(255 * strength * (t ** 1.6)))
    g = g.resize((W, height))
    im = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    im.putalpha(g)
    return im


# ======================= сцена и камера =======================

class Stage:
    """Картинка сцены с пирамидой уменьшенных копий."""

    def __init__(self, im: Image.Image):
        self.levels = [im.convert("RGB")]
        while self.levels[-1].width > 2 * W:
            last = self.levels[-1]
            self.levels.append(last.reduce(2))
        self.w, self.h = im.size

    def view(self, cx: float, cy: float, w: float) -> Image.Image:
        h = w * ASPECT
        k = 0
        while k + 1 < len(self.levels) and w / (2 ** (k + 1)) >= W:
            k += 1
        s = 2 ** k
        lv = self.levels[k]
        x0, y0 = max(0.0, (cx - w / 2) / s), max(0.0, (cy - h / 2) / s)
        x1, y1 = min(float(lv.width), (cx + w / 2) / s), min(float(lv.height), (cy + h / 2) / s)
        return lv.resize((W, H), Image.BILINEAR, box=(x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)))


def clamp_rect(stage: Stage, cx: float, cy: float, w: float) -> tuple[float, float, float]:
    """Прямоугольник камеры целиком внутри сцены."""
    w = min(w, stage.w, stage.h / ASPECT)
    h = w * ASPECT
    cx = min(max(cx, w / 2), stage.w - w / 2)
    cy = min(max(cy, h / 2), stage.h - h / 2)
    return cx, cy, w


def full_rect(stage: Stage) -> tuple[float, float, float]:
    return clamp_rect(stage, stage.w / 2, stage.h / 2, 1e9)


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def _at(keys: list, t: float, eased: bool = True) -> tuple[float, float, float]:
    """Камера в момент t по опорным точкам [(время, (cx, cy, w))]: между ними плавно, ширина — по логарифму."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, a), (t1, b) in zip(keys, keys[1:]):
        if t <= t1:
            u = (t - t0) / max(t1 - t0, 1e-6)
            u = _ease(u) if eased else u
            return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u,
                    math.exp(math.log(a[2]) + (math.log(b[2]) - math.log(a[2])) * u))
    return keys[-1][1]


# ======================= знак =======================

_logo = None


def _logo_layer():
    global _logo
    if _logo is None:
        from app import brand
        lg = brand._logo("white", brand.LOGO_W, brand.LOGO_H)
        alpha = lg.getchannel("A").point(lambda a: round(a * brand.OPACITY))
        _logo = (lg.convert("RGB"), alpha, (brand.MARGIN_LEFT, H - LOGO_BOTTOM - brand.LOGO_H))
    return _logo


# ======================= кадры и склейка =======================

def _alpha_cache(im: Image.Image, steps: int = 10) -> list:
    a = im.getchannel("A")
    rgb = im.convert("RGB")
    return [(rgb, a.point(lambda v, k=k: int(v * k / steps))) for k in range(steps + 1)]


def _paste(frame: Image.Image, cache: list, xy: tuple[int, int], k: float) -> None:
    i = round(max(0.0, min(1.0, k)) * (len(cache) - 1))
    if i:
        rgb, a = cache[i]
        frame.paste(rgb, xy, a)


def encode(shots: list[dict], out: Path, logo: bool = True) -> float:
    """shots: [{stage, keys: [(t, rect)], dur, eased, layers: [(RGBA, (x, y), t0, t1, fade_in, fade_out)]}].
    → длительность ролика, с."""
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("не найден ffmpeg")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", os.getenv("REEL_PRESET", "veryfast"),
           "-crf", os.getenv("REEL_CRF", "22"), "-maxrate", "9M", "-bufsize", "18M", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    lg = _logo_layer() if logo else None
    total = 0.0
    try:
        for shot in shots:
            layers = [(_alpha_cache(im), xy, t0, t1, fi, fo) for im, xy, t0, t1, fi, fo in shot.get("layers", [])]
            n = max(1, round(shot["dur"] * FPS))
            for i in range(n):
                t = i / FPS
                frame = shot["stage"].view(*_at(shot["keys"], t, shot.get("eased", True)))
                for cache, xy, t0, t1, fi, fo in layers:
                    if t0 <= t < t1:
                        k = min(1.0, (t - t0) / FADE if fi else 1.0, (t1 - t) / FADE if fo else 1.0)
                        _paste(frame, cache, xy, k)
                if lg:
                    frame.paste(lg[0], lg[2], lg[1])
                proc.stdin.write(frame.tobytes())
            total += n / FPS
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait(timeout=600) != 0:
            raise RuntimeError(f"ffmpeg: {err[-300:]}")
    except BrokenPipeError:
        err = proc.stderr.read().decode(errors="ignore")
        raise RuntimeError(f"ffmpeg оборвался: {err[-300:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return total


def load(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return im


def padded_stage(im: Image.Image) -> tuple[Stage, tuple[int, int]]:
    """Картина целиком в кадре 9:16: вокруг — она же, размытая и затемнённая. → (сцена, смещение картины)."""
    w, h = im.size
    if h / w >= ASPECT:
        sw, sh = w, h
    else:
        sw, sh = w, round(w * ASPECT)
    # фон — растянутая, сильно размытая и тёмная копия
    small = ImageOps.fit(im, (max(1, sw // 12), max(1, sh // 12)))
    bg = small.filter(ImageFilter.GaussianBlur(6)).resize((sw, sh), Image.BILINEAR)
    bg = ImageEnhance.Brightness(bg).enhance(0.32)
    ox, oy = (sw - w) // 2, (sh - h) // 2
    bg.paste(im, (ox, oy))
    return Stage(bg), (ox, oy)


# ======================= подборка =======================

SEG = float(os.getenv("REEL_SEG", "3.6"))        # секунд на работу
TITLE_HOLD = 2.6                                 # сколько на первом кадре держится название подборки


def collection(title: str, items: list[dict], out: Path) -> float:
    """items: [{path, label, sub}] — label крупно (название работы), sub мельче (автор)."""
    shots = []
    top = gradient(True)
    for n, it in enumerate(items):
        im = load(Path(it["path"]))
        st = Stage(im)
        full = full_rect(st)
        fx, fy = (it.get("focus") or [0.5, 0.5])[:2]
        landscape = im.width / im.height > W / H * 1.25
        if landscape:   # широкая картина — камера медленно проезжает мимо главного, туда или обратно
            w = full[2]
            span = min(st.w * 0.16, (st.w - w) / 2)
            a = clamp_rect(st, fx * st.w - span / 2, st.h / 2, w)
            b = clamp_rect(st, fx * st.w + span / 2, st.h / 2, w)
            if n % 2:
                a, b = b, a
        else:           # вертикальная — медленный наезд к главному
            a = full
            b = clamp_rect(st, full[0] + (fx * st.w - full[0]) * 0.35, full[1] + (fy * st.h - full[1]) * 0.35,
                           full[2] / 1.1)
        dur = SEG + (TITLE_HOLD if n == 0 else 0)
        layers = [(top, (0, 0), 0, dur, n == 0, False)]
        lab = text_block([(it["label"].upper(), font(52), 0), (it.get("sub") or "", font(30, False), 14)])
        lx = (W - lab.width) // 2
        if n == 0:
            head = text_block([(title.upper(), font(92), 0)], width=920)
            layers.append((head, ((W - head.width) // 2, int(H * 0.40) - head.height // 2), 0, TITLE_HOLD, False, True))
            layers.append((lab, (lx, 150), TITLE_HOLD, dur, True, False))
        else:
            layers.append((lab, (lx, 150), 0, dur, False, False))
        shots.append({"stage": st, "keys": [(0, a), (dur, b)], "dur": dur, "eased": False, "layers": layers})
    # последний кадр чуть дольше, с плавным уходом в чёрный не заморачиваемся: Instagram зацикливает ролик
    return encode(shots, out)


# ======================= детали =======================

HOLD = float(os.getenv("REEL_HOLD", "3.8"))      # сколько держим деталь
MOVE = 1.3                                       # переезд между деталями


def _detail_rect(stage: Stage, off: tuple[int, int], size: tuple[int, int], box: list[float]):
    """box — доли картины [x0, y0, x1, y1] → прямоугольник камеры 9:16 вокруг детали с полями."""
    x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in box]
    if x1 <= x0 or y1 <= y0:
        x0, y0, x1, y1 = 0.3, 0.3, 0.7, 0.7
    pw, ph = size
    cx, cy = off[0] + (x0 + x1) / 2 * pw, off[1] + (y0 + y1) / 2 * ph
    bw, bh = (x1 - x0) * pw * 1.25, (y1 - y0) * ph * 1.25
    w = max(bw, bh / ASPECT, pw * 0.16)          # не ближе, чем шестая часть ширины картины: иначе мыло
    if w <= pw and w * ASPECT <= ph:             # помещается в картину — не заезжаем на тёмные поля
        h = w * ASPECT
        cx = min(max(cx, off[0] + w / 2), off[0] + pw - w / 2)
        cy = min(max(cy, off[1] + h / 2), off[1] + ph - h / 2)
    return clamp_rect(stage, cx, cy, w)


def details(image: Path, intro: str, frames: list[dict], end_lines: list[str], out: Path) -> float:
    """frames: [{box: [x0, y0, x1, y1] (доли), text}]. intro — фраза на общем плане."""
    im = load(image)
    st, off = padded_stage(im)
    full = full_rect(st)
    txt_y = int(H * 0.60)

    def caption(text: str) -> tuple[Image.Image, tuple[int, int]]:
        b = text_block([(text, font(48), 0)], width=860)
        return b, ((W - b.width) // 2, txt_y - b.height // 2)

    keys, layers, t = [], [], 0.0
    # общий план с лёгким наездом
    near_full = (full[0], full[1], full[2] / 1.05)
    keys += [(0.0, full), (HOLD + 0.6, near_full)]
    b, xy = caption(intro)
    layers.append((b, xy, 0.25, HOLD + 0.4, True, True))
    t = HOLD + 0.6
    for fr in frames:
        r = _detail_rect(st, off, im.size, fr.get("box") or [])
        drift = clamp_rect(st, r[0], r[1] - r[2] * 0.03, r[2] / 1.04)
        keys += [(t + MOVE, r), (t + MOVE + HOLD, drift)]
        b, xy = caption(fr.get("text") or "")
        layers.append((b, xy, t + MOVE + 0.15, t + MOVE + HOLD - 0.1, True, True))
        t += MOVE + HOLD
    # финал: снова вся картина и подпись
    keys += [(t + MOVE + 0.2, full), (t + MOVE + 0.2 + HOLD, full)]
    end = text_block([(end_lines[0], font(54), 0)] + [(ln, font(34, False), 16) for ln in end_lines[1:] if ln],
                     width=900)
    layers.append((gradient(False, 900, 0.7), (0, H - 900), t + MOVE, t + MOVE + 0.2 + HOLD, True, False))
    layers.append((end, ((W - end.width) // 2, int(H * 0.70) - end.height // 2), t + MOVE + 0.3,
                   t + MOVE + 0.2 + HOLD, True, False))
    dur = t + MOVE + 0.2 + HOLD
    return encode([{"stage": st, "keys": keys, "dur": dur, "layers": layers}], out)


def cover(video_frame_src: Path, dest: Path) -> Path:
    """Обложка для карточки в боте — первый кадр по центру картинки (превью, без текста)."""
    im = load(video_frame_src)
    ImageOps.fit(im, (540, 960)).save(dest, "JPEG", quality=88)
    return dest
