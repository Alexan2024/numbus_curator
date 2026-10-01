"""NUMBUS Branding — движок рендера v2 (слои).

Шаблон клиента — это JSON со списком слоёв: логотип, текст, фигура, градиент,
затемнение. Движок ничего не знает о конкретном стиле — стиль целиком задаёт
клиент в редакторе (Mini App). Редактор рисует превью на <canvas> по тем же
правилам (webapp.html → renderSurface), поэтому превью совпадает с итогом.

ЕДИНИЦЫ. Все координаты и размеры — доли ШИРИНЫ канваса (W). Так шаблон
одинаково ложится на 4:5, 1:1 и любой другой формат.

ЯКОРЬ. Двухбуквенный: вертикаль t/m/b + горизонталь l/c/r ("bl", "mc"…).
  l: левый край слоя = x·W      r: правый край = W − x·W     c: центр = W/2 + x·W
  t: верх = y·W                 b: низ = H − y·W             m: центр = H/2 + y·W

ТЕКСТ. Блок строк: высота = capH + (n−1)·leading·size. Верх блока — линия
высоты прописных первой строки, низ — базовая линия последней. То есть
«снизу» текст стоит на базовой линии, как в вёрстке.

ЦВЕТ. {"mode":"fixed","value":"#RRGGBB"|"p0".."p4"} — фиксированный или из палитры;
{"mode":"adaptive"} — тон фона ±45% (светлее на тёмном, темнее на светлом);
{"mode":"contrast","light":..,"dark":..} — светлый на тёмном фоне, тёмный на светлом;
{"mode":"original"} — только для логотипа: цвета файла.
"""
import io
import os
import hashlib
import logging

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

try:
    import pillow_avif  # noqa: F401
except Exception:
    pass
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

logger = logging.getLogger(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE, "fonts")

# ============ Каталог шрифтов (все с кириллицей и осью веса) ============
FONTS = {
    "inter":      dict(label="Inter",              file="Inter.ttf",              min=100, max=900, group="sans"),
    "onest":      dict(label="Onest",              file="Onest.ttf",              min=100, max=900, group="sans"),
    "golos":      dict(label="Golos",              file="GolosText.ttf",          min=400, max=900, group="sans"),
    "manrope":    dict(label="Manrope",            file="Manrope.ttf",            min=200, max=800, group="sans"),
    "geologica":  dict(label="Geologica",          file="Geologica.ttf",          min=100, max=900, group="sans"),
    "montserrat": dict(label="Montserrat",         file="Montserrat.ttf",         min=100, max=900, group="sans"),
    "jost":       dict(label="Jost",               file="Jost.ttf",               min=100, max=900, group="sans"),
    "rubik":      dict(label="Rubik",              file="Rubik.ttf",              min=300, max=900, group="sans"),
    "nunito":     dict(label="Nunito",             file="Nunito.ttf",             min=200, max=1000, group="round"),
    "comfortaa":  dict(label="Comfortaa",          file="Comfortaa.ttf",          min=300, max=700, group="round"),
    "unbounded":  dict(label="Unbounded",          file="Unbounded.ttf",          min=200, max=900, group="display"),
    "oswald":     dict(label="Oswald",             file="Oswald.ttf",             min=200, max=700, group="display"),
    "playfair":   dict(label="Playfair Display",   file="PlayfairDisplay.ttf",    min=400, max=900, group="serif"),
    "cormorant":  dict(label="Cormorant Garamond", file="CormorantGaramond.ttf",  min=300, max=700, group="serif"),
    "lora":       dict(label="Lora",               file="Lora.ttf",               min=400, max=700, group="serif"),
    "jetbrains":  dict(label="JetBrains Mono",     file="JetBrainsMono.ttf",      min=100, max=800, group="mono"),
}
CUSTOM_FONT_SLOTS = ("font1", "font2", "font3")
DEFAULT_FONT = "inter"
_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_font_cache = {}


def _apply_axes(font, weight, size):
    try:
        axes = font.get_variation_axes()
    except Exception:
        return
    vals = []
    for a in axes:
        name = a.get("name", b"")
        name = name.decode("latin-1", "ignore") if isinstance(name, bytes) else str(name)
        lo, hi, df = a.get("minimum", 0), a.get("maximum", 0), a.get("default", a.get("minimum", 0))
        if "eight" in name:
            vals.append(max(lo, min(hi, weight)))
        elif "ptical" in name:
            vals.append(max(lo, min(hi, size)))  # как font-optical-sizing:auto в браузере
        else:
            vals.append(df)
    try:
        font.set_variation_by_axes(vals)
    except Exception as e:
        logger.warning("variation: %s", e)


def get_font(key, weight, size, customs=None):
    """key — ключ каталога или слот своего шрифта (font1..font3)."""
    size = max(1.0, round(float(size) * 2) / 2)
    weight = int(weight or 400)
    if key in CUSTOM_FONT_SLOTS and customs and customs.get(key):
        data = customs[key]
        ck = ("c", hashlib.md5(data).hexdigest(), weight, size)
        if ck not in _font_cache:
            try:
                f = ImageFont.truetype(io.BytesIO(data), size)
                _apply_axes(f, weight, size)
                _font_cache[ck] = f
            except Exception as e:
                logger.error("Свой шрифт не открылся: %s", e)
                return get_font(DEFAULT_FONT, weight, size)
        return _font_cache[ck]
    if key not in FONTS:
        key = DEFAULT_FONT
    ck = (key, weight, size)
    if ck not in _font_cache:
        try:
            f = ImageFont.truetype(os.path.join(FONT_DIR, FONTS[key]["file"]), size)
            _apply_axes(f, weight, size)
        except Exception as e:
            logger.error("Шрифт %s: %s — фолбэк", key, e)
            f = ImageFont.truetype(_FALLBACK, size) if os.path.exists(_FALLBACK) else ImageFont.load_default()
        _font_cache[ck] = f
    return _font_cache[ck]


def validate_font(data: bytes) -> bool:
    try:
        f = ImageFont.truetype(io.BytesIO(data), 40)
        f.getbbox("Aa Жж #")
        return True
    except Exception:
        return False


def font_name(data: bytes, fallback: str) -> str:
    try:
        fam, style = ImageFont.truetype(io.BytesIO(data), 20).getname()
        return (fam or fallback)[:40]
    except Exception:
        return fallback


# ============ Картинки ============
def open_image(data: bytes) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data)))


def open_photo(data: bytes) -> Image.Image:
    return open_image(data).convert("RGB")


def prepare_logo(data: bytes):
    """RGBA-логотип, обрезанный по видимой части. Если прозрачности нет —
    фон определяется по углам, альфа строится из контраста с ним.
    Возвращает (png_bytes, had_alpha)."""
    img = open_image(data).convert("RGBA")
    if max(img.size) > 3000:
        img.thumbnail((3000, 3000), Image.LANCZOS)
    arr = np.array(img)
    alpha = arr[:, :, 3]
    had_alpha = bool((alpha < 10).mean() > 0.01)
    if not had_alpha:
        gray = arr[:, :, :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        h, w = gray.shape
        k = max(2, int(min(h, w) * 0.05))
        corners = np.concatenate([gray[:k, :k].ravel(), gray[:k, -k:].ravel(),
                                  gray[-k:, :k].ravel(), gray[-k:, -k:].ravel()])
        diff = np.abs(gray - float(np.median(corners)))
        top = float(np.percentile(diff, 99.5)) or 1.0
        a = np.clip(diff / top * 255.0, 0, 255)
        a[a < 18] = 0
        alpha = a.astype(np.uint8)
        arr[:, :, 3] = alpha
    mask = alpha > 8
    if not mask.any():
        raise ValueError("empty logo")
    ys, xs = np.where(mask)
    out = Image.fromarray(arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1], "RGBA")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue(), had_alpha


def sample_image(w=1600, h=2000) -> Image.Image:
    """Нейтральный фон для превью: светлый верх, тёмный низ."""
    y = np.linspace(0, 1, h).reshape(-1, 1)
    x = np.linspace(0, 1, w).reshape(1, -1)
    top, mid, low = (np.array(c, dtype=np.float32) for c in ([214, 222, 230], [198, 170, 150], [40, 42, 48]))
    t = np.clip(y * 1.4, 0, 1)[..., None]
    col = top * (1 - t) + mid * t
    t2 = np.clip((y - 0.62) * 3.2, 0, 1)[..., None]
    col = col * (1 - t2) + low * t2 + (x[..., None] - 0.5) * 18
    col = col + np.random.default_rng(7).normal(0, 3.5, (h, w, 1))
    img = Image.fromarray(np.clip(np.broadcast_to(col, (h, w, 3)), 0, 255).astype(np.uint8), "RGB")
    sun = Image.new("L", (w, h), 0)
    ImageDraw.Draw(sun).ellipse((w * 0.58, h * 0.18, w * 0.80, h * 0.36), fill=200)
    return Image.composite(Image.new("RGB", (w, h), (255, 240, 220)), img,
                           sun.filter(ImageFilter.GaussianBlur(w * 0.03)))


def fit_cover(img, cw, ch):
    ir, cr = img.width / img.height, cw / ch
    if ir > cr:
        dh, dw = ch, int(round(ch * ir))
    else:
        dw, dh = cw, int(round(cw / ir))
    x0, y0 = (dw - cw) // 2, (dh - ch) // 2
    return img.resize((dw, dh), Image.LANCZOS).crop((x0, y0, x0 + cw, y0 + ch))


# ============ Цвет ============
def hex_rgb(s, default=(255, 255, 255)):
    try:
        s = s.lstrip("#")
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except Exception:
        return default


def avg_color(img, box):
    x, y, w, h = box
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(int(x + w), img.width), min(int(y + h), img.height)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0, 0.0
    a = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(axis=0)
    return float(a[0]), float(a[1]), float(a[2])


def luma(r, g, b):
    return (r * 299 + g * 587 + b * 114) / 1000


def shift_tone(r, g, b, pct):
    if pct > 0:
        r, g, b = (c + (255 - c) * pct / 100 for c in (r, g, b))
    else:
        r, g, b = (c - c * (-pct) / 100 for c in (r, g, b))
    return tuple(int(min(255, max(0, round(c)))) for c in (r, g, b))


class Ctx:
    """Всё, что нужно слоям: палитра, логотипы, свои шрифты, значения полей."""

    def __init__(self, palette=None, logos=None, customs=None, fields=None, dark=0.0, images=None):
        self.palette = palette or []
        self.logos = logos or {}          # {"logo": RGBA Image, "logo_alt": ...}
        self.customs = customs or {}      # {"font1": bytes, ...}
        self.fields = fields or {}        # title, subtitle, hashtag, i, n
        self.dark = float(dark)
        self.images = images              # callable(asset_id) -> RGBA Image | None (графика из макетов)

    def image(self, asset):
        if not self.images:
            return None
        try:
            return self.images(asset)
        except Exception as e:
            logger.warning("картинка %s: %s", asset, e)
            return None

    def ref(self, v, default="#FFFFFF"):
        v = v or default
        if isinstance(v, str) and len(v) == 2 and v[0] == "p" and v[1].isdigit():
            idx = int(v[1])
            v = self.palette[idx] if idx < len(self.palette) else default
        return hex_rgb(v)


def resolve_color(spec, ctx, canvas, box):
    """→ (r,g,b) или None (оригинальные цвета логотипа)."""
    spec = spec or {"mode": "fixed", "value": "#FFFFFF"}
    mode = spec.get("mode", "fixed")
    if mode == "original":
        return None
    if mode == "fixed":
        return ctx.ref(spec.get("value"))
    r, g, b = avg_color(canvas, box)
    dark_bg = luma(r, g, b) < 128
    if mode == "contrast":
        return ctx.ref(spec.get("light"), "#FFFFFF") if dark_bg else ctx.ref(spec.get("dark"), "#000000")
    return shift_tone(r, g, b, 45 if dark_bg else -45)  # adaptive


# ============ Геометрия ============
def place(anchor, x, y, w, h, W, H):
    v, hz = (anchor or "bl")[0], (anchor or "bl")[1]
    left = x * W if hz == "l" else (W - x * W - w if hz == "r" else W / 2 + x * W - w / 2)
    top = y * W if v == "t" else (H - y * W - h if v == "b" else H / 2 + y * W - h / 2)
    return left, top


def _rounded_layer(size, box, radius, fill, stroke=0):
    lay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    x0, y0, x1, y1 = box
    if x1 - x0 < 1 or y1 - y0 < 1:
        return lay
    r = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    if stroke > 0:
        d.rounded_rectangle(box, radius=r, outline=fill, width=max(1, int(round(stroke))))
    else:
        d.rounded_rectangle(box, radius=r, fill=fill)
    return lay


# ============ Текст ============
def apply_case(s, case):
    return s.upper() if case == "upper" else (s.lower() if case == "lower" else s)


def text_content(L, ctx):
    src = L.get("source", "static")
    f = ctx.fields
    if src == "title":
        s = f.get("title") or ""
    elif src == "subtitle":
        s = f.get("subtitle") or ""
    elif src == "hashtag":
        s = f.get("hashtag") or ""
    elif src == "counter":
        n = f.get("n") or 0
        s = (L.get("text") or "{i} / {n}").replace("{i}", str(f.get("i", 1))).replace("{n}", str(n)) if n else ""
    else:
        s = L.get("text") or ""
    return apply_case(s, L.get("case", "none"))


def tracked_w(font, s, ls):
    return font.getlength(s) + ls * (len(s) - 1) if s else 0.0


def wrap(text, font, ls, maxw):
    out = []
    for para in text.split("\n"):
        line = ""
        for w in para.split(" "):
            cand = w if line == "" else line + " " + w
            if line != "" and maxw > 0 and tracked_w(font, cand, ls) > maxw:
                out.append(line)
                line = w
            else:
                line = cand
        out.append(line)
    return out


def layout_text(L, ctx, content, W):
    """Строки, шрифт и габариты блока. Общая логика с webapp.html → layoutText."""
    size = float(L.get("size", 0.04)) * W
    key, weight = L.get("font", DEFAULT_FONT), L.get("weight", 400)
    font = get_font(key, weight, size, ctx.customs)
    ls = float(L.get("tracking", 0)) * size
    maxw = float(L.get("maxw", 0)) * W
    lines = wrap(content, font, ls, maxw)
    widest = max((tracked_w(font, ln, ls) for ln in lines), default=0)
    if maxw > 0 and widest > maxw:  # одно слово шире рамки — уменьшаем кегль
        size = size * maxw / widest
        font = get_font(key, weight, size, ctx.customs)
        ls = float(L.get("tracking", 0)) * size
        widest = max((tracked_w(font, ln, ls) for ln in lines), default=0)
    cap = -font.getbbox("H", anchor="ls")[1]
    adv = float(L.get("leading", 1.1)) * size
    return dict(lines=lines, font=font, ls=ls, size=size, cap=cap, adv=adv,
                w=widest, h=cap + (len(lines) - 1) * adv)


def draw_text_layer(canvas, L, ctx):
    W, H = canvas.size
    content = text_content(L, ctx)
    if not content.strip():
        return None
    m = layout_text(L, ctx, content, W)
    plate = L.get("plate")
    padx = float(plate.get("padx", 0.8)) * m["size"] if plate else 0
    pady = float(plate.get("pady", 0.5)) * m["size"] if plate else 0
    bw, bh = m["w"] + 2 * padx, m["h"] + 2 * pady
    left, top = place(L.get("anchor", "bl"), float(L.get("x", 0)), float(L.get("y", 0)), bw, bh, W, H)
    opacity = float(L.get("opacity", 1))

    if plate:
        pc = resolve_color(plate.get("color"), ctx, canvas, (left, top, bw, bh)) or (0, 0, 0)
        pa = int(round(255 * float(plate.get("opacity", 1)) * opacity))
        radius = float(plate.get("radius", 0)) * bh
        canvas.alpha_composite(_rounded_layer(canvas.size, (left, top, left + bw, top + bh), radius, pc + (pa,)))

    tx, ty = left + padx, top + pady
    col = resolve_color(L.get("color"), ctx, canvas, (tx, ty, m["w"], m["h"])) or (255, 255, 255)
    fill = col + (int(round(255 * opacity)),)
    # Отдельный слой → корректное наложение полупрозрачного текста
    pad = int(m["size"])
    lx0, ly0 = int(tx) - pad, int(ty) - pad
    lay = Image.new("RGBA", (int(m["w"]) + 2 * pad + 2, int(m["h"]) + 2 * pad + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    align = L.get("align", "left")
    font, ls = m["font"], m["ls"]
    for i, ln in enumerate(m["lines"]):
        lw = tracked_w(font, ln, ls)
        off = 0 if align == "left" else ((m["w"] - lw) / 2 if align == "center" else m["w"] - lw)
        bx = tx + off - lx0
        by = ty + m["cap"] + i * m["adv"] - ly0
        if ls == 0:
            d.text((bx, by), ln, font=font, fill=fill, anchor="ls")
        else:
            for k, ch in enumerate(ln):
                d.text((bx + font.getlength(ln[:k]) + ls * k, by), ch, font=font, fill=fill, anchor="ls")
    over(canvas, lay, lx0, ly0)
    return (left, top, bw, bh)


def over(canvas, lay, x, y):
    """alpha_composite с обрезкой по краям канваса (слой может выходить за край)."""
    x, y = int(x), int(y)
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(lay.width - sx0, canvas.width - dx0)
    h = min(lay.height - sy0, canvas.height - dy0)
    if w <= 0 or h <= 0:
        return
    canvas.alpha_composite(lay.crop((sx0, sy0, sx0 + w, sy0 + h)), (dx0, dy0))


# ============ Остальные слои ============
def tint(logo, color, alpha):
    solid = Image.new("RGBA", logo.size, color + (0,))
    solid.putalpha(logo.split()[3].point(lambda p: int(round(p * alpha))))
    return solid


def draw_logo_layer(canvas, L, ctx):
    W, H = canvas.size
    logo = ctx.logos.get(L.get("asset", "logo")) or ctx.logos.get("logo")
    if logo is None:
        return None
    w = max(1, int(round(float(L.get("w", 0.08)) * W)))
    h = max(1, int(round(w * logo.height / logo.width)))
    left, top = place(L.get("anchor", "bl"), float(L.get("x", 0)), float(L.get("y", 0)), w, h, W, H)
    left, top = int(round(left)), int(round(top))
    rs = logo.resize((w, h), Image.LANCZOS)
    opacity = float(L.get("opacity", 1))
    col = resolve_color(L.get("color"), ctx, canvas, (left, top, w, h))
    if col is None:
        a = rs.split()[3].point(lambda p: int(round(p * opacity)))
        rs.putalpha(a)
        piece = rs
    else:
        piece = tint(rs, col, opacity)
    over(canvas, piece, left, top)
    return (left, top, w, h)


def rect_box(L, W, H):
    if L.get("fit") == "inset":
        m = float(L.get("m", 0.04)) * W
        return m, m, W - 2 * m, H - 2 * m
    w, h = float(L.get("w", 0.2)) * W, float(L.get("h", 0.1)) * W
    left, top = place(L.get("anchor", "mc"), float(L.get("x", 0)), float(L.get("y", 0)), w, h, W, H)
    return left, top, w, h


def draw_rect_layer(canvas, L, ctx):
    W, H = canvas.size
    left, top, w, h = rect_box(L, W, H)
    col = resolve_color(L.get("color"), ctx, canvas, (left, top, w, h)) or (0, 0, 0)
    a = int(round(255 * float(L.get("opacity", 1))))
    radius = float(L.get("radius", 0)) * min(w, h) / 2
    stroke = float(L.get("stroke", 0)) * W
    canvas.alpha_composite(_rounded_layer(canvas.size, (left, top, left + w, top + h), radius, col + (a,), stroke))
    return (left, top, w, h)


def draw_gradient_layer(canvas, L, ctx):
    W, H = canvas.size
    side = L.get("side", "bottom")
    vertical = side in ("bottom", "top")
    ext = int(round(float(L.get("extent", 0.4)) * (H if vertical else W)))
    ext = max(1, min(ext, H if vertical else W))
    zone = {"bottom": (0, H - ext, W, ext), "top": (0, 0, W, ext),
            "left": (0, 0, ext, H), "right": (W - ext, 0, ext, H)}[side]
    alpha = float(L.get("opacity", 0.6))
    if L.get("adaptive"):
        alpha *= 0.4 + 0.6 * luma(*avg_color(canvas, zone)) / 255
    alpha = max(0.0, min(0.99, alpha + ctx.dark))
    ramp = np.linspace(0, 255 * alpha, ext, dtype=np.float32)
    if side in ("top", "left"):
        ramp = ramp[::-1]
    mask = np.zeros((H, W), dtype=np.float32)
    if side == "bottom":
        mask[H - ext:, :] = ramp[:, None]
    elif side == "top":
        mask[:ext, :] = ramp[:, None]
    elif side == "left":
        mask[:, :ext] = ramp[None, :]
    else:
        mask[:, W - ext:] = ramp[None, :]
    col = resolve_color(L.get("color"), ctx, canvas, zone) or (0, 0, 0)
    solid = Image.new("RGBA", (W, H), col + (0,))
    solid.putalpha(Image.fromarray(np.round(mask).astype(np.uint8), "L"))
    canvas.alpha_composite(solid)
    return zone


def draw_overlay_layer(canvas, L, ctx):
    W, H = canvas.size
    col = resolve_color(L.get("color"), ctx, canvas, (0, 0, W, H)) or (0, 0, 0)
    a = max(0.0, min(0.99, float(L.get("opacity", 0.2)) + ctx.dark * 0.5))
    canvas.alpha_composite(Image.new("RGBA", (W, H), col + (int(round(255 * a)),)))
    return (0, 0, W, H)


def draw_image_layer(canvas, L, ctx):
    """Графика из макета. fit: box — свой размер и якорь; cover — заполнить кадр
    с обрезкой; stretch — растянуть точно по кадру (рамки, обводки)."""
    W, H = canvas.size
    img = ctx.image(L.get("asset"))
    if img is None:
        return None
    fit = L.get("fit", "box")
    if fit == "stretch":
        piece, left, top = img.resize((W, H), Image.LANCZOS), 0, 0
    elif fit == "cover":
        piece, left, top = fit_cover(img, W, H), 0, 0
    else:
        w = max(1, int(round(float(L.get("w", 0.2)) * W)))
        h = max(1, int(round(w * img.height / img.width)))
        left, top = place(L.get("anchor", "mc"), float(L.get("x", 0)), float(L.get("y", 0)), w, h, W, H)
        left, top = int(round(left)), int(round(top))
        piece = img.resize((w, h), Image.LANCZOS)
    opacity = float(L.get("opacity", 1))
    if opacity < 1:
        piece = piece.copy()
        piece.putalpha(piece.split()[3].point(lambda p: int(round(p * opacity))))
    over(canvas, piece, left, top)
    return (left, top, piece.width, piece.height)


DRAW = {"text": draw_text_layer, "logo": draw_logo_layer, "rect": draw_rect_layer,
        "gradient": draw_gradient_layer, "overlay": draw_overlay_layer, "image": draw_image_layer}


def render_surface(photo, W, H, layers, ctx) -> Image.Image:
    canvas = fit_cover(photo, W, H).convert("RGBA")
    for L in layers or []:
        if L.get("hidden"):
            continue
        fn = DRAW.get(L.get("type"))
        if fn:
            try:
                fn(canvas, L, ctx)
            except Exception as e:
                logger.exception("слой %s: %s", L.get("type"), e)
    return canvas.convert("RGB")


# ============ Форматы и шаблоны ============
FEED_SIZES = {
    "4:5": (1920, 2400), "3:4": (1920, 2560), "1:1": (1920, 1920),
    "3:2": (1920, 1280), "9:16": (1080, 1920),
}
STORY_SIZE = (1080, 1920)
DARK_STEPS = [-0.4, -0.2, 0.0, 0.2, 0.4]
DARK_DEFAULT_IDX = 2


def feed_size(img, fmt):
    if fmt in FEED_SIZES:
        return FEED_SIZES[fmt]
    w, h = img.size
    tw = min(max(w, 1920), 2560)
    return tw, int(round(h * tw / w))


def spec_fields(spec) -> set:
    """Какие данные шаблон спросит у пользователя при создании поста."""
    out = set()
    surfaces = [spec.get("feed", {})]
    if spec.get("story", {}).get("enabled"):
        surfaces.append(spec["story"])
    for s in surfaces:
        for L in s.get("layers", []):
            if L.get("type") == "text" and not L.get("hidden") and L.get("source") in ("title", "subtitle", "hashtag"):
                out.add(L["source"])
    return out


def spec_has_shade(spec) -> bool:
    surfaces = [spec.get("feed", {})] + ([spec["story"]] if spec.get("story", {}).get("enabled") else [])
    return any(L.get("type") in ("gradient", "overlay") and not L.get("hidden")
               for s in surfaces for L in s.get("layers", []))


def render_template(photo, spec, fmt, ctx):
    """→ [(suffix, Image)]: лента в выбранном формате + сторис, если включены."""
    W, H = feed_size(photo, fmt)
    out = [("feed", render_surface(photo, W, H, spec.get("feed", {}).get("layers"), ctx))]
    st = spec.get("story", {})
    if st.get("enabled"):
        out.append(("story", render_surface(photo, *STORY_SIZE, st.get("layers"), ctx)))
    return out


# ============ Вывод ============
def to_jpeg(img, quality=92) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def to_preview(img, max_side=1200) -> bytes:
    im = img.copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    return to_jpeg(im, 85)
