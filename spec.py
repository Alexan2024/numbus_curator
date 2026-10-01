"""NUMBUS Branding — схема шаблона: проверка и стартовые стили.

Шаблон приходит из Mini App как JSON — всё, что пишется в БД, проходит через
sanitize_spec: неизвестные поля выбрасываются, числа зажимаются в диапазоны.
"""
import re
import copy
import secrets

ANCHORS = {"tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br"}
SOURCES = {"title", "subtitle", "hashtag", "counter", "static"}
MAX_LAYERS = 30
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PAL = re.compile(r"^p[0-4]$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_IMG = re.compile(r"^img_[0-9a-f]{12}$")


def _num(v, lo, hi, d):
    try:
        v = float(v)
        if v != v:  # NaN
            return d
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return d


def _cval(v, d):
    return v if isinstance(v, str) and (_HEX.match(v) or _PAL.match(v)) else d


def _color(c, allow_original=False, d=None):
    d = d or {"mode": "fixed", "value": "p0"}
    if not isinstance(c, dict):
        return dict(d)
    mode = c.get("mode")
    if mode == "fixed":
        return {"mode": "fixed", "value": _cval(c.get("value"), "p0")}
    if mode == "adaptive":
        return {"mode": "adaptive"}
    if mode == "contrast":
        return {"mode": "contrast", "light": _cval(c.get("light"), "p0"), "dark": _cval(c.get("dark"), "p1")}
    if mode == "original" and allow_original:
        return {"mode": "original"}
    return dict(d)


def _font(v):
    from render import FONTS, CUSTOM_FONT_SLOTS, DEFAULT_FONT
    return v if v in FONTS or v in CUSTOM_FONT_SLOTS else DEFAULT_FONT


def _base(L):
    lid = L.get("id") if isinstance(L.get("id"), str) and _ID.match(L.get("id")) else "l" + secrets.token_hex(3)
    out = {"id": lid, "type": L.get("type")}
    if L.get("hidden"):
        out["hidden"] = True
    name = L.get("name")
    if isinstance(name, str) and name.strip():
        out["name"] = name.strip()[:40]
    return out


def _pos(L, out, d_anchor="bl"):
    out["anchor"] = L.get("anchor") if L.get("anchor") in ANCHORS else d_anchor
    out["x"] = _num(L.get("x"), -1, 1, 0.05)
    out["y"] = _num(L.get("y"), -1, 2, 0.05)


def sanitize_layer(L):
    if not isinstance(L, dict):
        return None
    t = L.get("type")
    out = _base(L)
    if t == "logo":
        _pos(L, out)
        out["asset"] = "logo_alt" if L.get("asset") == "logo_alt" else "logo"
        out["w"] = _num(L.get("w"), 0.01, 1, 0.08)
        out["color"] = _color(L.get("color"), allow_original=True, d={"mode": "adaptive"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
    elif t == "text":
        _pos(L, out)
        out["source"] = L.get("source") if L.get("source") in SOURCES else "static"
        out["text"] = str(L.get("text") or "")[:300]
        out["font"] = _font(L.get("font"))
        out["weight"] = int(_num(L.get("weight"), 100, 1000, 500))
        out["size"] = _num(L.get("size"), 0.005, 0.4, 0.04)
        out["tracking"] = _num(L.get("tracking"), -0.2, 0.6, 0)
        out["leading"] = _num(L.get("leading"), 0.6, 3, 1.1)
        out["case"] = L.get("case") if L.get("case") in ("none", "upper", "lower") else "none"
        out["align"] = L.get("align") if L.get("align") in ("left", "center", "right") else "left"
        out["maxw"] = _num(L.get("maxw"), 0, 1, 0)
        out["color"] = _color(L.get("color"))
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
        p = L.get("plate")
        if isinstance(p, dict):
            out["plate"] = {
                "color": _color(p.get("color"), d={"mode": "fixed", "value": "p1"}),
                "opacity": _num(p.get("opacity"), 0, 1, 1),
                "radius": _num(p.get("radius"), 0, 0.5, 0),
                "padx": _num(p.get("padx"), 0, 4, 0.8),
                "pady": _num(p.get("pady"), 0, 4, 0.5),
            }
    elif t == "rect":
        out["fit"] = "inset" if L.get("fit") == "inset" else "box"
        _pos(L, out, "mc")
        out["m"] = _num(L.get("m"), 0, 0.4, 0.04)
        out["w"] = _num(L.get("w"), 0.001, 2, 0.2)
        out["h"] = _num(L.get("h"), 0.001, 4, 0.1)
        out["radius"] = _num(L.get("radius"), 0, 1, 0)
        out["stroke"] = _num(L.get("stroke"), 0, 0.1, 0)
        out["color"] = _color(L.get("color"), d={"mode": "fixed", "value": "p1"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
    elif t == "gradient":
        out["side"] = L.get("side") if L.get("side") in ("bottom", "top", "left", "right") else "bottom"
        out["extent"] = _num(L.get("extent"), 0.05, 1, 0.45)
        out["color"] = _color(L.get("color"), d={"mode": "fixed", "value": "p1"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 0.7)
        out["adaptive"] = bool(L.get("adaptive"))
    elif t == "image":
        if not isinstance(L.get("asset"), str) or not _IMG.match(L["asset"]):
            return None
        out["asset"] = L["asset"]
        out["fit"] = L.get("fit") if L.get("fit") in ("box", "cover", "stretch") else "box"
        _pos(L, out, "mc")
        out["w"] = _num(L.get("w"), 0.005, 2, 0.2)
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
    elif t == "overlay":
        out["color"] = _color(L.get("color"), d={"mode": "fixed", "value": "p1"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 0.25)
    else:
        return None
    return out


def _layers(v):
    out = []
    for L in (v if isinstance(v, list) else [])[:MAX_LAYERS]:
        c = sanitize_layer(L)
        if c:
            out.append(c)
    return out


def sanitize_spec(spec):
    spec = spec if isinstance(spec, dict) else {}
    feed = spec.get("feed") if isinstance(spec.get("feed"), dict) else {}
    story = spec.get("story") if isinstance(spec.get("story"), dict) else {}
    return {
        "v": 1,
        "feed": {"layers": _layers(feed.get("layers"))},
        "story": {"enabled": bool(story.get("enabled")), "layers": _layers(story.get("layers"))},
    }


def sanitize_palette(v):
    base = ["#FFFFFF", "#141414", "#D9D9D9", "#7A7A7A", "#FFFFFF"]
    v = (v if isinstance(v, list) else [])[:5]
    v = v + base[len(v):]
    return [(c.upper() if isinstance(c, str) and _HEX.match(c) else base[i]) for i, c in enumerate(v)]


# ============ Стартовые стили ============
# Палитра по умолчанию: p0 — светлый, p1 — тёмный, p2 — акцент.
# Стили намеренно разные по характеру: это отправная точка, а не готовый бренд.
CONTRAST = {"mode": "contrast", "light": "p0", "dark": "p1"}


def _t(**kw):
    base = dict(type="text", anchor="bl", x=0.05, y=0.05, source="static", text="", font="inter",
                weight=500, size=0.03, tracking=0, leading=1.1, case="none", align="left", maxw=0,
                color=dict(CONTRAST), opacity=1)
    base.update(kw)
    return base


def _logo(**kw):
    base = dict(type="logo", anchor="bl", x=0.05, y=0.05, asset="logo", w=0.08, color=dict(CONTRAST), opacity=1)
    base.update(kw)
    return base


PRESETS = [
    {"key": "mark", "name": {"ru": "Знак", "en": "Mark"}, "spec": {
        "feed": {"layers": [_logo(anchor="tr", w=0.085)]},
        "story": {"enabled": False, "layers": []}}},

    {"key": "chip", "name": {"ru": "Метка", "en": "Chip"}, "spec": {
        "feed": {"layers": [
            _t(anchor="tl", source="hashtag", font="onest", weight=600, size=0.028,
               color={"mode": "fixed", "value": "p1"},
               plate={"color": {"mode": "fixed", "value": "p2"}, "opacity": 1, "radius": 0.5, "padx": 0.75, "pady": 0.55}),
            _logo(anchor="br", w=0.075)]},
        "story": {"enabled": False, "layers": []}}},

    {"key": "editorial", "name": {"ru": "Редакция", "en": "Editorial"}, "spec": {
        "feed": {"layers": [
            {"type": "gradient", "side": "bottom", "extent": 0.55, "color": {"mode": "fixed", "value": "p1"},
             "opacity": 0.8, "adaptive": True},
            _t(anchor="tl", x=0.06, y=0.06, source="hashtag", font="inter", weight=600, size=0.02,
               tracking=0.14, case="upper"),
            _logo(anchor="tr", x=0.06, y=0.06, w=0.09),
            _t(anchor="bl", x=0.06, y=0.075, source="title", font="playfair", weight=700, size=0.078,
               leading=1.04, maxw=0.8, color={"mode": "fixed", "value": "p0"})]},
        "story": {"enabled": True, "layers": [
            {"type": "gradient", "side": "bottom", "extent": 0.5, "color": {"mode": "fixed", "value": "p1"},
             "opacity": 0.85, "adaptive": True},
            _t(anchor="tl", x=0.08, y=0.16, source="hashtag", font="inter", weight=600, size=0.028,
               tracking=0.14, case="upper"),
            _logo(anchor="tr", x=0.08, y=0.15, w=0.13),
            _t(anchor="bl", x=0.08, y=0.3, source="title", font="playfair", weight=700, size=0.1,
               leading=1.04, maxw=0.84, color={"mode": "fixed", "value": "p0"})]}}},

    {"key": "block", "name": {"ru": "Блок", "en": "Block"}, "spec": {
        "feed": {"layers": [
            _logo(anchor="tl", w=0.09),
            _t(anchor="bl", x=0, y=0.07, source="title", font="unbounded", weight=700, size=0.046,
               leading=1.12, maxw=0.72, color={"mode": "fixed", "value": "p1"},
               plate={"color": {"mode": "fixed", "value": "p2"}, "opacity": 1, "radius": 0, "padx": 1.1, "pady": 0.8})]},
        "story": {"enabled": False, "layers": []}}},

    {"key": "frame", "name": {"ru": "Рамка", "en": "Frame"}, "spec": {
        "feed": {"layers": [
            {"type": "rect", "fit": "inset", "m": 0.035, "stroke": 0.0025, "radius": 0,
             "color": dict(CONTRAST), "opacity": 0.9},
            _t(anchor="tc", x=0, y=0.07, source="hashtag", font="jost", weight=500, size=0.021,
               tracking=0.18, case="upper", align="center"),
            _logo(anchor="bc", x=0, y=0.07, w=0.11)]},
        "story": {"enabled": False, "layers": []}}},

    {"key": "carousel", "name": {"ru": "Карусель", "en": "Carousel"}, "spec": {
        "feed": {"layers": [
            _t(anchor="tr", source="counter", text="{i} / {n}", font="jetbrains", weight=500, size=0.024),
            _logo(anchor="bl", w=0.07)]},
        "story": {"enabled": False, "layers": []}}},

    {"key": "center", "name": {"ru": "Центр", "en": "Center"}, "spec": {
        "feed": {"layers": [
            {"type": "overlay", "color": {"mode": "fixed", "value": "p1"}, "opacity": 0.35},
            _t(anchor="mc", x=0, y=0, source="title", font="cormorant", weight=600, size=0.1,
               leading=1.0, maxw=0.8, align="center", color={"mode": "fixed", "value": "p0"}),
            _logo(anchor="bc", x=0, y=0.06, w=0.12, color={"mode": "fixed", "value": "p0"})]},
        "story": {"enabled": True, "layers": [
            {"type": "overlay", "color": {"mode": "fixed", "value": "p1"}, "opacity": 0.35},
            _t(anchor="mc", x=0, y=0, source="title", font="cormorant", weight=600, size=0.13,
               leading=1.0, maxw=0.82, align="center", color={"mode": "fixed", "value": "p0"}),
            _logo(anchor="tc", x=0, y=0.16, w=0.18, color={"mode": "fixed", "value": "p0"})]}}},

    {"key": "blank", "name": {"ru": "С нуля", "en": "Blank"}, "spec": {
        "feed": {"layers": []}, "story": {"enabled": False, "layers": []}}},
]
SEED_PRESETS = ("mark", "editorial")


def preset(key):
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None


def preset_spec(key):
    p = preset(key)
    return sanitize_spec(copy.deepcopy(p["spec"])) if p else sanitize_spec({})


def presets_public(lang="ru"):
    return [{"key": p["key"], "name": p["name"].get(lang) or p["name"]["ru"],
             "spec": sanitize_spec(copy.deepcopy(p["spec"]))} for p in PRESETS]
