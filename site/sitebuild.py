"""AH Magazine · сборка сайта.

Сайт — статичные файлы на Beget. Всё, что на нём есть, собирается отсюда из одного файла данных (data.json):
страницы на двух языках (их рисует prerender.js тем же кодом, что работает в браузере), файл данных для
браузера, иконки, robots.txt, sitemap.xml, RSS, .htaccess. Картинки записи (img/c, img/t, img/f, img/m) делаются
один раз, когда запись появляется (make_images), и дальше не пересобираются.

Запуск руками:  python3 site/sitebuild.py build data.json out/
Бот пользуется этим модулем из app/sitepub.py.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
SITE_URL = "https://theahmag.com"
BOX = 1200       # обложка img/c: лента, превью ссылок, Instant View (до 6.1 — 800, и для всех фото)
HI = 2000        # каждое фото img/f: страница записи и заметки на больших и плотных экранах
MID = 1000       # каждое фото img/m: телефоны (srcset выбирает сам браузер)
IMG_V = 2        # формат картинок записи: 2 — отдельные файлы f/m; без v — старая лента img/p по 800 px
GAP = 6          # просвет между фото в ленте img/p (старые записи)
TILE = 240       # квадрат для сетки архива
MANIFEST = "assets/manifest.json"
STATIC = {       # файл в web/static → путь на сайте
    "favicon.ico": "favicon.ico", "icon.svg": "icon.svg", "apple-touch-icon.png": "apple-touch-icon.png",
    "icon-192.png": "icon-192.png", "icon-512.png": "icon-512.png", "site.webmanifest": "site.webmanifest",
    "share.jpg": "share.jpg", "robots.txt": "robots.txt", "htaccess.txt": ".htaccess",
}


def short_hash(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:10]


def minify_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    css = re.sub(r"\s*:\s*(?=[^{}]*[;}])", ":", css)       # «color : x» → «color:x» только внутри правил
    return css.replace(";}", "}").strip()


# ---------- картинки записи ----------

def _open(src) -> Image.Image:
    """Путь к файлу или уже открытое изображение (например, со знаком AHMAG) → RGB."""
    im = src if isinstance(src, Image.Image) else ImageOps.exif_transpose(Image.open(src))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _fit(im: Image.Image, box: int = BOX) -> Image.Image:
    if max(im.size) <= box:
        return im
    k = box / max(im.size)
    return im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)


def _tile(cover: Image.Image) -> Image.Image:
    """Квадрат для сетки: окно на месте, где больше всего деталей, а не всегда по центру."""
    w, h = cover.size
    m = min(w, h)
    if w == h:
        return cover.resize((TILE, TILE), Image.LANCZOS)
    small = cover.convert("L").resize((max(1, w // 4), max(1, h // 4)))
    edges = small.filter(ImageFilter.FIND_EDGES)
    best, best_e = 0, -1.0
    steps = 12
    for i in range(steps + 1):
        off = round((max(w, h) - m) * i / steps)
        box = (off // 4, 0, (off + m) // 4, h // 4) if w > h else (0, off // 4, w // 4, (off + m) // 4)
        e = ImageStat.Stat(edges.crop(box)).mean[0]
        e *= 1 - 0.15 * abs(i / steps - 0.5) * 2       # при равенстве — ближе к центру
        if e > best_e:
            best, best_e = off, e
    box = (best, 0, best + m, h) if w > h else (0, best, w, best + m)
    return cover.crop(box).resize((TILE, TILE), Image.LANCZOS)


def _jpeg(im: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def photo_files(photos: list, key: str | int) -> tuple[list, dict[str, bytes]]:
    """Каждое фото записи отдельными файлами: img/f/<key>-<n>.jpg до 2000 px и img/m/<key>-<n>.jpg до 1000 px.
    → (segs: [[0, ширина f, высота f, 0], …], {путь на сайте: байты})"""
    segs, files = [], {}
    for n, src in enumerate(photos):
        im = _fit(_open(src), HI)
        files[f"img/f/{key}-{n}.jpg"] = _jpeg(im, 84)
        files[f"img/m/{key}-{n}.jpg"] = _jpeg(_fit(im, MID), 82)
        segs.append([0, im.width, im.height, 0])
    return segs, files


def make_images(photos: list, key: str | int, separate: bool = True) -> tuple[dict, dict[str, bytes]]:
    """Фото записи → (описание для data.json, {путь на сайте: байты}). Первое фото — обложка.
    Каждое фото — отдельным файлом в двух размерах (img/f, img/m), обложка — img/c, квадрат для сетки — img/t.
    Общей ленты img/p больше нет: она ограничивала фото 800 px. separate оставлен для совместимости."""
    ims = [_open(p) for p in photos]
    if not ims:
        raise ValueError("нет фото")
    segs, files = photo_files(ims, key)
    cover = _fit(ims[0], BOX)
    files.update({
        f"img/c/{key}.jpg": _jpeg(cover, 86),
        f"img/t/{key}.jpg": _jpeg(_tile(cover), 80),
    })
    W = max(g[1] for g in segs)
    H = sum(g[2] for g in segs)
    return {"v": IMG_V, "W": W, "H": H, "segs": segs, "cw": cover.width, "ch": cover.height}, files


def photos_from_strip(rec: dict, strip_path: Path, out_dir: Path) -> int:
    """Разовая миграция: отдельные фото заметки, вырезанные из её ленты img/p."""
    strip = Image.open(strip_path).convert("RGB")
    key = rec.get("ik") or rec["id"]
    for n, (y0, w, h, _v) in enumerate(rec["img"]["segs"]):
        dest = out_dir / "img" / "f" / f"{key}-{n}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_jpeg(strip.crop((0, y0, w, y0 + h)), 86))
    return len(rec["img"]["segs"])


def thumbs_from_sprites(data: dict, sprite_dir: Path, out_dir: Path) -> int:
    """Разовая миграция: квадраты сетки для записей, у которых они жили в общих листах img/s/N.jpg."""
    sheets: dict[int, Image.Image] = {}
    n = 0
    for o in data["objects"]:
        sp = o.get("sp")
        if not sp:
            continue
        if sp[0] not in sheets:
            sheets[sp[0]] = Image.open(sprite_dir / f"{sp[0]}.jpg").convert("RGB")
        t = sheets[sp[0]].crop((sp[1] * TILE, sp[2] * TILE, sp[1] * TILE + TILE, sp[2] * TILE + TILE))
        dest = out_dir / "img" / "t" / f"{o.get('ik') or o['id']}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_jpeg(t, 82))
        n += 1
    return n


# ---------- сборка ----------

def _assets(D: dict) -> tuple[dict[str, bytes], dict]:
    """Файлы, которые не зависят от страниц, и имена с хешем для страниц."""
    # «<» как \u003c: файл данных отдаётся как страница, и разметка из текстов не должна в нём оживать
    compact = json.dumps(D, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").encode()
    js = (WEB / "app.js").read_bytes()
    files: dict[str, bytes] = {}
    # Данные лежат в .html: страницы nginx на Beget сжимает всегда, а .json — нет (480 КБ против 140).
    # Имя постоянное, чтобы новая запись не меняла все страницы сайта; браузер сверяет файл при загрузке.
    data_name = "assets/data.html"
    files[data_name] = compact
    files["data.json"] = compact                    # постоянный адрес: из него бот и всё прочее берут архив
    js_name = f"assets/app.{short_hash(js)}.js"
    files[js_name] = js
    for f in sorted((WEB / "fonts").iterdir()):
        if f.suffix in (".woff2", ".txt"):
            files[f"assets/fonts/{f.name}"] = f.read_bytes()
    for src, dst in STATIC.items():
        files[dst] = (WEB / "static" / src).read_bytes()
    css = minify_css((WEB / "fonts.css").read_text("utf-8") + "\n" + (WEB / "app.css").read_text("utf-8"))
    return files, {"css": css, "js": "/" + js_name, "dataUrl": "/" + data_name}


def _prerender(cfg: dict, node: str = "node") -> list[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
        cfg_path = f.name
    try:
        res = subprocess.run([node, str(HERE / "prerender.js"), cfg_path], capture_output=True, text=True, timeout=600)
    finally:
        Path(cfg_path).unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError("prerender.js: " + (res.stderr or res.stdout)[-1500:])
    return json.loads(res.stdout)


def build(D: dict, out: Path, built: str | None = None, site: str = SITE_URL, node: str = "node") -> list[str]:
    """Весь сайт без картинок записей → out. Возвращает список записанных файлов (пути на сайте)."""
    out = Path(out)
    D = json.loads(json.dumps(D))
    D["built"] = built or date.today().isoformat()
    D["v"] = 2                                      # формат данных этой сборки (бот проверяет перед публикацией)
    files, names = _assets(D)
    for rel, b in files.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    data_path = out / "data.json"
    pages = _prerender({"data": str(data_path), "out": str(out), "site": site, "built": D["built"], **names}, node)
    # временные адреса /a/<ключ>/ из постов канала («в архиве →») ведут на постоянные — всегда, при любой пересборке
    for o in D["objects"]:
        k = str(o.get("ik") or "")
        if k.startswith("b") and not o.get("tmp"):
            for pre in ("", "en/"):
                rel = f"{pre}a/{k[1:]}/index.html"
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_bytes(redirect_page(f"/{pre}o/{o['id']}/", site))
                pages.append(rel)
    rels = sorted(set(files) | set(pages))
    # что лежит на сайте после этой сборки: бот сверяется с этим списком и заливает только изменённое
    manifest = file_hashes(out, rels)
    (out / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=0), "utf-8")
    return rels + [MANIFEST]


def build_provisional(D: dict, kind: str, temp_id: int, key: str, out: Path, site: str = SITE_URL,
                      node: str = "node") -> list[str]:
    """Страница записи, у которой ещё нет номера: /a/<key>/ и /en/a/<key>/ (закрыты от поисковиков).
    Пользуется ассетами, которые уже лежат на сайте, — их имена берутся из тех же данных без записи."""
    out = Path(out)
    D = json.loads(json.dumps(D))
    D["built"] = date.today().isoformat()
    base = json.loads(json.dumps(D))
    base["objects"] = [o for o in base["objects"] if not o.get("tmp")]
    base["notes"] = [n for n in base["notes"] if not n.get("tmp")]
    _, names = _assets(base)
    out.mkdir(parents=True, exist_ok=True)
    data_path = out / "_provisional.json"
    data_path.write_text(json.dumps(D, ensure_ascii=False), "utf-8")
    try:
        return _prerender({"data": str(data_path), "out": str(out), "site": site, "built": D["built"],
                           "provisional": {"kind": kind, "id": temp_id, "key": key}, **names}, node)
    finally:
        data_path.unlink(missing_ok=True)


def redirect_page(target: str, site: str = SITE_URL) -> bytes:
    """Страница-переадресация: /a/<key>/ → постоянный адрес записи, когда у неё появился номер."""
    t = target if target.startswith("/") else "/" + target
    return ("<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>AH Magazine</title>\n"
            "<meta name=\"robots\" content=\"noindex\">\n"
            f"<link rel=\"canonical\" href=\"{site}{t}\">\n"
            f"<meta http-equiv=\"refresh\" content=\"0; url={t}\">\n"
            f"<script>location.replace({json.dumps(t)}+location.search+location.hash)</script>\n"
            f"</head><body><a href=\"{t}\">AH Magazine</a></body></html>\n").encode()


def file_hashes(root: Path, rels: list[str]) -> dict[str, str]:
    return {r: hashlib.sha1((Path(root) / r).read_bytes()).hexdigest() for r in rels}


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "build":
        data = json.loads(Path(sys.argv[2]).read_text("utf-8"))
        files = build(data, Path(sys.argv[3]))
        print(f"{len(files)} файлов → {sys.argv[3]}")
    else:
        print(__doc__)
