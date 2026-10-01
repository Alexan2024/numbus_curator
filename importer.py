"""NUMBUS Branding — импорт готовых макетов в шаблон.

Любой формат раскладывается одинаково:
  • ФОТО — место под фото клиента. Слой с именем «фото»/«photo» (или нижний слой
    на весь кадр, или картинка на весь лист в PDF). Выбрасывается.
  • ЖИВОЙ ТЕКСТ — меняется от поста к посту. Слой с именем title/subtitle/hashtag/
    counter (или «заголовок», «подзаголовок», «хештег», «счётчик»), либо текст
    {title}, {subtitle}, {hashtag}, {i}/{n}. Становится текстовым слоем движка:
    шрифт, кегль, цвет, трекинг, выключка берутся из файла.
  • ЛОГОТИП — слой с именем «logo»/«логотип»: подставляется логотип бренда.
  • ГРАФИКА — всё остальное. Отрисовывается на прозрачном фоне и делится на
    отдельные элементы; каждый привязывается к ближайшему краю кадра, чтобы
    макет правильно ложился на другие форматы.

Итог — шаблон в формате spec.py + набор PNG для слоёв «image».
"""
import io
import re
import math
import ctypes
import hashlib
import logging
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

import render as R

logger = logging.getLogger("numbus.import")

WORK_W = 1920          # ширина, к которой приводится макет
MAX_PIECES = 24


class ImportFail(Exception):
    """Ошибка импорта с кодом для интерфейса."""

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


# ============ Роли слоёв ============
_ROLE_WORDS = {
    "title": "title", "заголовок": "title", "headline": "title",
    "subtitle": "subtitle", "подзаголовок": "subtitle", "subheadline": "subtitle",
    "hashtag": "hashtag", "хештег": "hashtag", "хэштег": "hashtag", "tag": "hashtag",
    "counter": "counter", "счетчик": "counter", "счётчик": "counter",
    "logo": "logo", "логотип": "logo", "лого": "logo",
    "logo_alt": "logo_alt", "logo2": "logo_alt", "логотип2": "logo_alt",
    "photo": "photo", "фото": "photo", "image": "photo", "picture": "photo",
}
_PLACEHOLDER = re.compile(r"^\{\s*([a-zа-яё_0-9]+)\s*\}$", re.I)
_COUNTER = re.compile(r"\{\s*i\s*\}.*\{\s*n\s*\}", re.I)


def role_by_name(name):
    key = re.sub(r"[\s\-{}#]+", "", (name or "").strip().lower())
    return _ROLE_WORDS.get(key)


def role_by_text(text):
    t = (text or "").strip()
    if _COUNTER.search(t):
        return "counter"
    m = _PLACEHOLDER.match(t)
    if m:
        r = _ROLE_WORDS.get(m.group(1).lower())
        return r if r in ("title", "subtitle", "hashtag", "counter") else None
    return None


TEXT_ROLES = ("title", "subtitle", "hashtag", "counter")


# ============ Промежуточное представление ============
@dataclass
class Slot:
    role: str
    align: str                 # left | center | right
    x_ref: float               # px: левый край / центр / правый край (по align)
    baseline: float            # px: базовая линия первой строки
    size: float                # px
    font_name: str = ""
    weight: int = 0            # 0 — определить по имени
    color: tuple = (255, 255, 255)
    tracking: float = 0.0      # доля кегля
    leading: float = 1.15
    case: str = "none"
    maxw: float = 0.0          # px, 0 — определить автоматически
    text: str = ""             # для счётчика — формат


@dataclass
class Piece:
    png: bytes
    box: tuple                 # (x, y, w, h) px
    anchor: str = ""           # подсказка якоря (Figma constraints); "" — определить
    fit: str = "box"


@dataclass
class LogoSlot:
    asset: str
    box: tuple


@dataclass
class Layout:
    W: float
    H: float
    name: str = ""
    pieces: list = field(default_factory=list)
    slots: list = field(default_factory=list)
    logos: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ============ Шрифты ============
_WEIGHTS = [("hairline", 100), ("thin", 100), ("extralight", 200), ("ultralight", 200), ("light", 300),
            ("book", 400), ("regular", 400), ("normal", 400), ("roman", 400), ("medium", 500),
            ("semibold", 600), ("demibold", 600), ("demi", 600), ("extrabold", 800), ("ultrabold", 800),
            ("heavy", 800), ("black", 900), ("bold", 700)]


def _norm(s):
    return re.sub(r"[^a-z0-9а-яё]", "", (s or "").lower())


def weight_from_name(name):
    n = _norm(name)
    for word, w in _WEIGHTS:
        if word in n:
            return w
    return 400


def match_font(name, customs_names):
    """Имя шрифта из файла → ключ каталога / своего шрифта. (key, найден ли)."""
    n = _norm(re.sub(r"^[A-Z]{6}\+", "", name or ""))
    if not n:
        return R.DEFAULT_FONT, False
    for slot, label in (customs_names or {}).items():
        ln = _norm(label)
        if ln and (ln in n or n.startswith(ln[:max(4, len(ln) - 2)])):
            return slot, True
    for key, f in sorted(R.FONTS.items(), key=lambda kv: -len(kv[1]["label"])):
        ln = _norm(f["label"])
        if ln in n or (key in n and len(key) >= 4):
            return key, True
    return R.DEFAULT_FONT, False


# ============ Разбиение графики на элементы ============
def segment(overlay, min_area=0.00015):
    """RGBA-оверлей → список Piece. Элементы, стоящие рядом, склеиваются."""
    from scipy import ndimage
    W, H = overlay.size
    alpha = np.asarray(overlay.split()[3])
    mask = alpha > 8
    if not mask.any():
        return []
    sw = 480
    sh = max(1, round(H * sw / W))
    small = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((sw, sh), Image.BILINEAR)) > 0
    lab, n = ndimage.label(ndimage.binary_dilation(small, iterations=2))
    if n == 0:
        return []
    if n > MAX_PIECES:
        return [_piece_from(overlay, mask, (0, 0, W, H))]
    lab_full = np.asarray(Image.fromarray(lab.astype(np.int32)).resize((W, H), Image.NEAREST))
    out = []
    for k, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        m = mask & (lab_full == k)
        ys, xs = np.where(m)
        if len(xs) == 0 or len(xs) < min_area * W * H:
            continue
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        out.append(_piece_from(overlay, m, (x0, y0, x1 - x0, y1 - y0)))
    return out


def _piece_from(overlay, m, box):
    x, y, w, h = (int(v) for v in box)
    W, H = overlay.size
    fit = "box"
    if w >= 0.85 * W and h >= 0.85 * H:
        filled = m[y:y + h, x:x + w].mean()
        fit = "stretch" if filled < 0.25 else "cover"   # рамка тянется, заливка кадрируется
        x, y, w, h = 0, 0, W, H                         # храним во весь кадр — с полями
    arr = np.array(overlay.crop((x, y, x + w, y + h)))
    arr[..., 3] = np.where(m[y:y + h, x:x + w], arr[..., 3], 0)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, "PNG", optimize=True)
    return Piece(buf.getvalue(), (x, y, w, h), "", fit)


# ============ Сборка шаблона ============
def _anchor_for(box, W, H, hint=""):
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    hz = hint[1] if len(hint) == 2 else ("l" if cx < W / 3 else ("r" if cx > 2 * W / 3 else "c"))
    v = hint[0] if len(hint) == 2 else ("t" if cy < H / 3 else ("b" if cy > 2 * H / 3 else "m"))
    ax = x / W if hz == "l" else ((W - x - w) / W if hz == "r" else (cx - W / 2) / W)
    ay = y / W if v == "t" else ((H - y - h) / W if v == "b" else (cy - H / 2) / W)
    return v + hz, round(ax, 4), round(ay, 4)


def _hex(c):
    return "#%02X%02X%02X" % tuple(int(max(0, min(255, round(v)))) for v in c[:3])


def slot_layer(sl, W, H, customs, customs_names):
    key, found = match_font(sl.font_name, customs_names)
    weight = sl.weight or weight_from_name(sl.font_name)
    info = R.FONTS.get(key)
    if info:
        weight = max(info["min"], min(info["max"], weight))
    font = R.get_font(key, weight, sl.size, customs)
    cap = -font.getbbox("H", anchor="ls")[1]
    top = sl.baseline - cap
    # ширина блока по плейсхолдеру неизвестна — берём ширину строки как ориентир
    v = "t" if sl.baseline < H / 3 else ("b" if top > H / 2 else "m")
    hz = "l" if sl.align == "left" else ("r" if sl.align == "right" else "c")
    x = sl.x_ref / W if hz == "l" else ((W - sl.x_ref) / W if hz == "r" else (sl.x_ref - W / 2) / W)
    y = top / W if v == "t" else ((H - sl.baseline) / W if v == "b" else (top + cap / 2 - H / 2) / W)
    if sl.role in ("hashtag", "counter"):
        maxw = 0.0
    elif sl.maxw > 0:
        maxw = sl.maxw / W
    elif hz == "l":
        maxw = (W - 2 * sl.x_ref) / W
    elif hz == "r":
        maxw = (W - 2 * (W - sl.x_ref)) / W
    else:
        maxw = 0.84
    layer = {
        "type": "text", "source": sl.role, "anchor": v + hz, "x": round(x, 4), "y": round(y, 4),
        "font": key, "weight": int(weight), "size": round(sl.size / W, 5),
        "tracking": round(sl.tracking, 4), "leading": round(sl.leading, 3), "case": sl.case,
        "align": sl.align, "maxw": round(max(0.0, min(1.0, maxw)), 3) if maxw else 0,
        "color": {"mode": "fixed", "value": _hex(sl.color)}, "opacity": 1,
    }
    if sl.role == "counter":
        layer["text"] = re.sub(r"\{\s*i\s*\}", "{i}", re.sub(r"\{\s*n\s*\}", "{n}", sl.text)) or "{i} / {n}"
    return layer, (None if found or not sl.font_name else re.sub(r"^[A-Z]{6}\+", "", sl.font_name))


def build(layout, customs=None, customs_names=None):
    """Layout → (layers, assets {id: png}, report)."""
    W, H = layout.W, layout.H
    layers, assets = [], {}
    for p in layout.pieces:
        aid = "img_" + hashlib.sha1(p.png).hexdigest()[:12]
        assets[aid] = p.png
        if p.fit in ("cover", "stretch"):
            layers.append({"type": "image", "asset": aid, "fit": p.fit, "anchor": "mc", "x": 0, "y": 0,
                           "w": 1, "opacity": 1, "name": "Графика"})
            continue
        anchor, x, y = _anchor_for(p.box, W, H, p.anchor)
        layers.append({"type": "image", "asset": aid, "fit": "box", "anchor": anchor, "x": x, "y": y,
                       "w": round(p.box[2] / W, 5), "opacity": 1, "name": "Графика"})
    for lg in layout.logos:
        anchor, x, y = _anchor_for(lg.box, W, H)
        layers.append({"type": "logo", "asset": lg.asset, "anchor": anchor, "x": x, "y": y,
                       "w": round(lg.box[2] / W, 5), "color": {"mode": "contrast", "light": "p0", "dark": "p1"},
                       "opacity": 1})
    missing = []
    roles = []
    for sl in layout.slots:
        layer, miss = slot_layer(sl, W, H, customs, customs_names)
        layers.append(layer)
        roles.append(sl.role)
        if miss and miss not in missing:
            missing.append(miss)
    report = {"pieces": len(layout.pieces), "roles": roles, "logos": len(layout.logos),
              "missing_fonts": missing, "warnings": layout.warnings, "name": layout.name,
              "ratio": round(H / W, 3)}
    return layers, assets, report


def _to_work(img):
    """Приводит оверлей к рабочей ширине. Возвращает (картинка, множитель)."""
    if img.width == WORK_W:
        return img, 1.0
    k = WORK_W / img.width
    return img.resize((WORK_W, max(1, round(img.height * k))), Image.LANCZOS), k


# ============ PNG ============
def parse_png(data, name=""):
    img = R.open_image(data).convert("RGBA")
    a = np.asarray(img.split()[3])
    if (a < 250).mean() < 0.05:
        raise ImportFail("png_opaque")
    img, _ = _to_work(img)
    lay = Layout(img.width, img.height, name)
    lay.pieces = segment(img)
    return lay


# ============ PSD ============
def _psd_text_slot(L, role, k):
    ed, rd = L.engine_dict, L.resource_dict
    style = ed["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]
    para = ed["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"]
    xx, xy, yx, yy, tx, ty = (float(v) for v in L.transform)
    scale = math.hypot(yx, yy) or 1.0
    size = float(style.get("FontSize", 24)) * scale
    fonts = rd.get("FontSet", [])
    fi = int(style.get("Font", 0))
    fname = str(fonts[fi]["Name"]).strip("'\"") if fi < len(fonts) else ""
    vals = (style.get("FillColor") or {}).get("Values", [1, 1, 1, 1])
    color = tuple(float(v) * 255 for v in vals[1:4])
    just = int(para.get("Justification", 0))
    align = {0: "left", 1: "right", 2: "center"}.get(just, "left")
    leading = 1.2 if style.get("AutoLeading", True) else float(style.get("Leading", size)) * scale / size
    tracking = float(style.get("Tracking", 0)) / 1000.0
    case = "upper" if int(style.get("FontCaps", 0)) == 2 else "none"
    shape = 0
    box = None
    try:
        ch = ed["Rendered"]["Shapes"]["Children"][0]
        shape = int(ch.get("ShapeType", 0))
        if shape == 1:
            bb = ch["Cookie"]["Photoshop"]["BoxBounds"]   # [top, left, bottom, right]
            box = (tx + float(bb[1]) * scale, ty + float(bb[0]) * scale,
                   tx + float(bb[3]) * scale, ty + float(bb[2]) * scale)
    except Exception:
        pass
    if box:  # абзацный текст: считаем от рамки
        left, top, right, _ = box
        x_ref = left if align == "left" else (right if align == "right" else (left + right) / 2)
        baseline = top + size * 0.9
        maxw = right - left
    else:    # строчный текст: transform — начало базовой линии
        x_ref, baseline, maxw = tx, ty, 0.0
    text = str(L.text or "").replace("\r", "\n")
    return Slot(role, align, x_ref * k, baseline * k, size * k, fname, 0, color, tracking, leading, case,
                maxw * k, text)


def parse_psd(data, name=""):
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(io.BytesIO(data))
    except Exception as e:
        raise ImportFail("psd_bad", str(e))
    W, H = psd.size
    k = WORK_W / W
    lay = Layout(WORK_W, round(H * k), name)
    exclude = set()
    explicit_photo = False
    for L in psd.descendants():
        if not L.is_visible():
            continue
        r = role_by_name(L.name)
        if L.kind == "type":
            r = r if r in TEXT_ROLES else role_by_text(L.text)
            if r in TEXT_ROLES:
                try:
                    lay.slots.append(_psd_text_slot(L, r, k))
                    exclude.add(id(L))
                except Exception as e:
                    lay.warnings.append(f"text:{L.name}")
                    logger.warning("psd text %s: %s", L.name, e)
                continue
        if r == "photo":
            exclude.add(id(L))
            explicit_photo = True
        elif r in ("logo", "logo_alt"):
            l0, t0, r0, b0 = L.bbox
            if r0 > l0 and b0 > t0:
                lay.logos.append(LogoSlot(r, (l0 * k, t0 * k, (r0 - l0) * k, (b0 - t0) * k)))
                exclude.add(id(L))
    if not explicit_photo:  # нижний слой на весь кадр — это фото
        for L in psd:
            if L.is_visible() and L.kind in ("pixel", "smartobject"):
                l0, t0, r0, b0 = L.bbox
                if (r0 - l0) * (b0 - t0) >= 0.9 * W * H:
                    exclude.add(id(L))
                break

    def keep(L):
        if id(L) in exclude:
            return False
        p = L.parent
        while p is not None and hasattr(p, "parent"):
            if id(p) in exclude:
                return False
            p = p.parent
        return L.is_visible()
    try:
        overlay = psd.composite(layer_filter=keep, force=True, color=1.0, alpha=0.0)
    except Exception as e:
        raise ImportFail("psd_render", str(e))
    if overlay is None:
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    overlay, _ = _to_work(overlay.convert("RGBA"))
    lay.pieces = segment(overlay)
    return lay


# ============ AI / PDF ============
def _pdf_text(obj, tp):
    import pypdfium2.raw as P
    n = P.FPDFTextObj_GetText(obj.raw, tp.raw, None, 0)
    if n <= 0:
        return ""
    buf = (ctypes.c_ushort * n)()
    P.FPDFTextObj_GetText(obj.raw, tp.raw, buf, n)
    return bytes(buf).decode("utf-16-le", "ignore").rstrip("\x00")


def parse_pdf(data, name=""):
    """AI (сохранённый с PDF-совместимостью — так по умолчанию) и PDF. Первая монтажная область."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as P
        pdf = pdfium.PdfDocument(data)
    except Exception as e:
        raise ImportFail("ai_bad", str(e))
    if len(pdf) == 0:
        raise ImportFail("ai_bad")
    page = pdf[0]
    Wp, Hp = page.get_size()
    k = WORK_W / Wp
    lay = Layout(WORK_W, round(Hp * k), name)
    if len(pdf) > 1:
        lay.warnings.append("first_artboard")
    tp = page.get_textpage()
    kill = []
    for obj in page.get_objects(max_depth=1):
        if obj.type == P.FPDF_PAGEOBJ_TEXT:
            text = _pdf_text(obj, tp)
            role = role_by_text(text)
            if role not in TEXT_ROLES:
                continue
            fs = ctypes.c_float()
            P.FPDFTextObj_GetFontSize(obj.raw, ctypes.byref(fs))
            a, b, c, d, e, f = obj.get_matrix().get()
            size = fs.value * (math.hypot(c, d) or 1.0)
            font = P.FPDFTextObj_GetFont(obj.raw)
            fb = ctypes.create_string_buffer(256)
            P.FPDFFont_GetFontName(font, fb, 256)
            fname = fb.value.decode("latin-1", "ignore")
            try:
                weight = int(P.FPDFFont_GetWeight(font))
            except Exception:
                weight = -1
            weight = weight if 100 <= weight <= 1000 and weight != 400 else 0
            rgba = [ctypes.c_uint() for _ in range(4)]
            P.FPDFPageObj_GetFillColor(obj.raw, *(ctypes.byref(v) for v in rgba))
            l, bt, r, t = obj.get_pos()
            cx = (l + r) / 2
            if abs(cx - Wp / 2) < 0.03 * Wp:
                align, x_ref = "center", cx
            elif l > Wp / 2:
                align, x_ref = "right", r
            else:
                align, x_ref = "left", e
            lay.slots.append(Slot(role, align, x_ref * k, (Hp - f) * k, size * k, fname, weight,
                                  tuple(v.value for v in rgba[:3]), 0.0, 1.15, "none", 0.0, text))
            kill.append(obj)
        elif obj.type == P.FPDF_PAGEOBJ_IMAGE:
            l, bt, r, t = obj.get_pos()
            if (r - l) * (t - bt) >= 0.6 * Wp * Hp:
                kill.append(obj)
    tp.close()
    for o in kill:
        try:
            page.remove_obj(o)
        except Exception:
            lay.warnings.append("nested")
    page.gen_content()
    bmp = page.render(scale=k, fill_color=(0, 0, 0, 0), may_draw_forms=True, rev_byteorder=True)
    overlay = bmp.to_pil().convert("RGBA")
    lay.pieces = segment(overlay)
    pdf.close()
    return lay


# ============ Figma ============
FIGMA_URL = re.compile(r"figma\.com/(?:file|design|proto|board)/([A-Za-z0-9]+)[^?#]*(?:\?[^#]*?node-id=([0-9]+[-:%3Aa]+[0-9]+))?")


def figma_ref(url):
    m = FIGMA_URL.search(url or "")
    if not m:
        raise ImportFail("figma_url")
    key, node = m.group(1), m.group(2)
    if not node:
        raise ImportFail("figma_node")
    node = node.replace("%3A", ":").replace("%3a", ":").replace("-", ":")
    return key, node


def _fig_visible(n):
    return n.get("visible", True) is not False


def _fig_role(n):
    r = role_by_name(n.get("name"))
    if n.get("type") == "TEXT":
        if r not in TEXT_ROLES:
            r = role_by_text(n.get("characters"))
    return r


def _fig_has_dynamic(n):
    if not _fig_visible(n):
        return False
    if _fig_role(n) in TEXT_ROLES + ("logo", "logo_alt", "photo"):
        return True
    return any(_fig_has_dynamic(c) for c in n.get("children", []))


def _fig_box(n, x0, y0, render=False):
    b = (n.get("absoluteRenderBounds") if render else None) or n.get("absoluteBoundingBox") or {}
    return (b.get("x", 0) - x0, b.get("y", 0) - y0, b.get("width", 0), b.get("height", 0))


def _fig_anchor(n):
    c = n.get("constraints") or {}
    hz = {"LEFT": "l", "RIGHT": "r", "CENTER": "c"}.get(c.get("horizontal"), "")
    v = {"TOP": "t", "BOTTOM": "b", "CENTER": "m"}.get(c.get("vertical"), "")
    return v + hz if hz and v else ""


def _fig_image_fill(n):
    return any(f.get("type") == "IMAGE" and f.get("visible", True) is not False for f in n.get("fills", []) or [])


def figma_plan(frame):
    """Разбор фрейма: что отрисовать графикой, какие слоты создать. Без сети."""
    if frame.get("type") not in ("FRAME", "COMPONENT", "INSTANCE", "SECTION", "GROUP"):
        raise ImportFail("figma_not_frame")
    fb = frame.get("absoluteBoundingBox") or {}
    x0, y0, Wf, Hf = fb.get("x", 0), fb.get("y", 0), fb.get("width", 0), fb.get("height", 0)
    if Wf <= 0 or Hf <= 0:
        raise ImportFail("figma_not_frame")
    k = WORK_W / Wf
    plan = {"W": WORK_W, "H": round(Hf * k), "k": k, "name": frame.get("name", ""),
            "render": [], "slots": [], "logos": [], "warnings": []}
    kids = [c for c in frame.get("children", []) if _fig_visible(c)]
    explicit_photo = any(_fig_role(c) == "photo" for c in frame.get("children", []))
    if not explicit_photo and kids:
        b = kids[0].get("absoluteBoundingBox") or {}
        if _fig_image_fill(kids[0]) and b.get("width", 0) * b.get("height", 0) >= 0.9 * Wf * Hf:
            kids = kids[1:]   # нижний слой-картинка на весь фрейм — место под фото

    def walk(nodes):
        for n in nodes:
            if not _fig_visible(n):
                continue
            role = _fig_role(n)
            if role == "photo":
                continue
            if n.get("type") == "TEXT" and role in TEXT_ROLES:
                plan["slots"].append(_fig_slot(n, role, x0, y0, k))
                continue
            if role in ("logo", "logo_alt"):
                bx = _fig_box(n, x0, y0)
                plan["logos"].append({"asset": role, "box": tuple(v * k for v in bx)})
                continue
            if _fig_has_dynamic(n) and n.get("children"):
                if (n.get("fills") and any(f.get("visible", True) is not False for f in n["fills"])) or n.get("effects"):
                    plan["warnings"].append(f"group_bg:{n.get('name', '')}")
                walk(n["children"])
                continue
            bx = _fig_box(n, x0, y0, render=True)
            if bx[2] <= 0 or bx[3] <= 0:
                continue
            plan["render"].append({"id": n["id"], "box": tuple(v * k for v in bx), "anchor": _fig_anchor(n)})
    walk(kids)
    return plan


def _fig_slot(n, role, x0, y0, k):
    st = n.get("style") or {}
    size = float(st.get("fontSize", 24))
    lh = float(st.get("lineHeightPx", size * 1.2))
    fam = st.get("fontPostScriptName") or st.get("fontFamily") or ""
    weight = int(st.get("fontWeight", 0) or 0)
    fill = next((f for f in n.get("fills", []) if f.get("type") == "SOLID" and f.get("visible", True) is not False), None)
    col = fill.get("color", {}) if fill else {"r": 1, "g": 1, "b": 1}
    color = (col.get("r", 1) * 255, col.get("g", 1) * 255, col.get("b", 1) * 255)
    align = {"LEFT": "left", "CENTER": "center", "RIGHT": "right", "JUSTIFIED": "left"}.get(
        st.get("textAlignHorizontal"), "left")
    bx, by, bw, bh = _fig_box(n, x0, y0)
    x_ref = bx if align == "left" else (bx + bw if align == "right" else bx + bw / 2)
    key, _ = match_font(st.get("fontFamily") or fam, {})
    font = R.get_font(key, weight or 400, size)
    asc, desc = font.getmetrics()
    baseline = by + (lh - (asc + desc)) / 2 + asc      # модель межстрочного интервала Figma/CSS
    case = {"UPPER": "upper", "LOWER": "lower"}.get(st.get("textCase"), "none")
    fixed_w = n.get("style", {}).get("textAutoResize") in ("HEIGHT", "NONE", "TRUNCATE")
    return {"role": role, "align": align, "x_ref": x_ref * k, "baseline": baseline * k, "size": size * k,
            "font_name": st.get("fontFamily") or fam, "weight": weight,
            "color": color, "tracking": float(st.get("letterSpacing", 0)) / size if size else 0,
            "leading": lh / size if size else 1.15, "case": case,
            "maxw": bw * k if fixed_w else 0.0, "text": n.get("characters", "")}


def figma_assemble(plan, images):
    """plan + {node_id: png bytes} → Layout."""
    lay = Layout(plan["W"], plan["H"], plan["name"])
    lay.warnings = list(plan["warnings"])
    for r in plan["render"]:
        data = images.get(r["id"])
        if not data:
            lay.warnings.append("render_missing")
            continue
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        x, y, w, h = r["box"]
        if w >= 1 and h >= 1 and (abs(img.width - w) > 1 or abs(img.height - h) > 1):
            img = img.resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)
        fit = "box"
        if w >= 0.85 * plan["W"] and h >= 0.85 * plan["H"]:
            fit = "stretch" if (np.asarray(img.split()[3]) > 8).mean() < 0.25 else "cover"
            full = Image.new("RGBA", (plan["W"], plan["H"]), (0, 0, 0, 0))
            full.alpha_composite(img, (max(0, round(x)), max(0, round(y))))
            img, (x, y, w, h) = full, (0, 0, plan["W"], plan["H"])
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        lay.pieces.append(Piece(buf.getvalue(), (x, y, w, h), r["anchor"], fit))
    lay.slots = [Slot(**s) for s in plan["slots"]]
    lay.logos = [LogoSlot(lg["asset"], lg["box"]) for lg in plan["logos"]]
    return lay


# ============ Определение формата ============
def detect(data, filename=""):
    fn = (filename or "").lower()
    if data[:4] == b"8BPS" or fn.endswith((".psd", ".psb")):
        return "psd"
    if data[:5] == b"%PDF-" or fn.endswith((".ai", ".pdf")):
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or fn.endswith(".png"):
        return "png"
    if fn.endswith(".fig"):
        raise ImportFail("fig_file")
    raise ImportFail("format")


def parse_file(data, filename=""):
    kind = detect(data, filename)
    name = re.sub(r"\.[a-z0-9]+$", "", filename or "", flags=re.I)[:40]
    if kind == "psd":
        return parse_psd(data, name)
    if kind == "pdf":
        try:
            return parse_pdf(data, name)
        except ImportFail as e:
            if (filename or "").lower().endswith(".ai"):
                raise ImportFail("ai_nopdf") from e
            raise
    return parse_png(data, name)
