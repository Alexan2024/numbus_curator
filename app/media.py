"""Извлечение текста и фото из статьи, скачивание и фильтр качества."""
import asyncio
import base64
import io
import logging
import re
from pathlib import Path
import urllib.parse as urlparse_mod
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageOps, ImageStat

from app import config

log = logging.getLogger(__name__)

MAX_RATIO = 2.2          # обычный предел пропорций кадра
MAX_BYTES = 30_000_000   # файл больше — не качаем (оригиналы на 10 000 px)

SKIP_IMG = re.compile(r"logo|avatar|icon|sprite|banner|(?<![a-z])ads?[_/-]|pixel|gravatar|placeholder|\.svg|\.gif", re.I)


def _best_from_srcset(srcset: str) -> str | None:
    best, best_w = None, 0
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                pass
        if w >= best_w:
            best, best_w = bits[0], w
    return best


def _upgrade(url: str) -> str:
    # ArchDaily отдаёт превью; подменяем на крупный размер
    url = re.sub(r"/(thumb_jpg|small_jpg|medium_jpg|newsletter|slideshow|square)/", "/large_jpg/", url)
    return url.split("?")[0] if "adsttc.com" in url else url


# ---------- крупнее: где у сайтов лежит оригинал ----------

BIG = 2560          # столько просим у CDN, которые режут по параметру
_SIZE_PARAMS = ("w", "width", "h", "height", "maxwidth", "max-w", "mw")
_IMG_EXT = r"\.(?:jpe?g|png|webp)"


def variants(url: str) -> list[str]:
    """Адреса одного фото, от самого крупного к исходному: сначала пробуем оригинал, не вышло — тот, что был.
    WordPress (-1024x683.jpg → .jpg), Squarespace (?format=750w → 2500w), CDN с ?w=460 (Sanity, imgix, Contentful)."""
    out: list[str] = []
    base, _, query = url.partition("?")
    if "images.metmuseum.org" in base:           # The Met: web-large / web-additional → original
        out.append(re.sub(r"/web-(?:large|additional|highlight)/", "/original/", base))
    # WordPress: размер в имени файла (у Sanity такие же цифры — часть имени файла, не размер)
    if "sanity.io" not in base and re.search(r"-\d{2,4}x\d{2,4}" + _IMG_EXT + "$", base, re.I):
        out.append(re.sub(r"-\d{2,4}x\d{2,4}(?=" + _IMG_EXT + "$)", "", base, flags=re.I))
    if query:
        params = urlparse_mod.parse_qsl(query, keep_blank_values=True)
        keys = {k.lower() for k, _ in params}
        if "format" in keys and "squarespace" in url:
            out.append(base + "?format=2500w")
        elif keys & set(_SIZE_PARAMS):
            sized = []
            for k, v in params:
                kl = k.lower()
                if kl in ("w", "width", "maxwidth", "max-w", "mw") and v.isdigit() and int(v) < BIG:
                    sized.append((k, str(BIG)))
                elif kl in ("h", "height") and v.isdigit():
                    continue          # высоту не задаём: пропорции сохранит сам CDN
                else:
                    sized.append((k, v))
            if sized != params:
                out.append(base + "?" + urlparse_mod.urlencode(sized))
    out.append(url)
    return list(dict.fromkeys(out))


# ---------- качество файла ----------

# стандартная таблица квантования яркости JPEG (IJG), по ней оценивается качество сжатия
_STD_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
             14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
             49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]


def jpeg_quality(im: Image.Image) -> int | None:
    """Примерное качество сжатия JPEG (1–100) по таблице квантования; не JPEG — None."""
    q = getattr(im, "quantization", None)
    if not q or 0 not in q:
        return None
    t = list(q[0])
    if len(t) != 64:
        return None
    scale = sum(t) / sum(_STD_LUMA) * 100
    return round((200 - scale) / 2) if scale <= 100 else round(5000 / scale)


def parse_html(html_text: str, base_url: str) -> dict:
    soup = BeautifulSoup(html_text, "lxml")
    og = soup.find("meta", property="og:title")
    title = og["content"] if og and og.get("content") else (soup.title.string if soup.title else "")

    containers = soup.find_all(["article", "main"]) + [soup.body or soup]
    root = max(containers, key=lambda c: sum(len(p.get_text()) for p in c.find_all("p")))
    for bad in root.select("script, style, nav, footer, aside, form, .related, .comments"):
        bad.decompose()

    paragraphs = [p.get_text(" ", strip=True) for p in root.find_all(["p", "h2", "h3", "li", "figcaption"])]
    text = "\n".join(t for t in paragraphs if len(t) > 30)[:9000]

    urls: list[str] = []
    ogi = soup.find("meta", property="og:image")
    if ogi and ogi.get("content"):
        urls.append(_upgrade(ogi["content"]))
    img_nodes = root.find_all(["img", "source", "a"])
    if len(img_nodes) < 8:  # галерея часто вне основного текста
        for bad in soup.select("nav, footer, header, .related, .related-in-article, [class*=related]"):
            bad.decompose()
        img_nodes += [n for n in soup.find_all(["img", "source", "a"]) if n not in img_nodes]
    for img in img_nodes:
        cand = None
        if img.name == "a":
            href = img.get("href", "")
            if re.search(r"\.(jpe?g|png|webp)(\?|$)", href, re.I):
                cand = href
        else:
            for attr in ("data-srcset", "srcset"):
                if img.get(attr):
                    cand = _best_from_srcset(img[attr])
                    break
            cand = cand or img.get("data-src") or img.get("data-lazy-src") or img.get("src")
        if not cand or cand.startswith("data:") or SKIP_IMG.search(cand):
            continue
        urls.append(_upgrade(urljoin(base_url, cand)))
    return {"title": title or "", "text": text, "image_urls": urls}


def _dedupe(urls: list[str]) -> list[str]:
    seen, uniq = set(), []
    for u in urls:
        key = re.sub(r"[-_]\d{2,4}x\d{2,4}|-scaled|/(large_jpg|newsletter)/", "", u.split("?")[0])
        if key not in seen:
            seen.add(key)
            uniq.append(u)
    return uniq


async def extract_article(client: httpx.AsyncClient, url: str, feed_html: str = "") -> dict:
    """Текст и фото: полный текст из RSS + страница статьи, если она доступна."""
    parts = []
    if feed_html:
        parts.append(parse_html(feed_html, url))
    raw = ""
    try:
        r = await client.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        raw = r.text
        parts.append(parse_html(raw, url))
    except Exception as exc:
        log.info("Страница недоступна (%s), работаем по RSS: %s", exc, url)
    if not parts:
        raise RuntimeError("нет ни RSS-текста, ни страницы")
    text = max((p["text"] for p in parts), key=len)
    title = next((p["title"] for p in reversed(parts) if p["title"]), "")
    urls = _dedupe([u for p in parts for u in p["image_urls"]])
    urls = fix_images(url, urls, raw) or urls   # у некоторых сайтов свои правила, где лежат крупные фото
    return {"title": title, "text": text, "image_urls": urls[:30]}


# ======================= фото со страниц: правила по сайтам =======================

WP_SIZE = re.compile(r"-\d{2,4}x\d{2,4}(?=\.(?:jpe?g|png|webp)$)", re.I)


def _stem(u: str) -> str:
    name = urlparse_mod.urlparse(u).path.rsplit("/", 1)[-1]
    name = WP_SIZE.sub("", name)
    return re.sub(r"(-\d{1,3})?\.(jpe?g|png|webp)$", "", name, flags=re.I).lower()


def fix_images(url: str, urls: list[str], raw_html: str = "") -> list[str]:
    dom = urlparse_mod.urlparse(url).netloc
    if "afasiaarchzine.com" in dom:
        ups = [WP_SIZE.sub("", u.split("?")[0]) for u in urls if "/wp-content/uploads/" in u]
        base = next((_stem(u) for u in ups if "afasia" in _stem(u)), None)
        if base:
            ups = [u for u in ups if _stem(u) == base]
        return list(dict.fromkeys(ups))
    if "publicdomainreview.org" in dom:
        out = [u.split("?")[0] for u in urls if "pdr-assets" in u and "/sources/" not in u]
        return list(dict.fromkeys(out))
    if "inigo.com" in dom:
        found = re.findall(r"https://cdn\.themodernhouse\.com/[^\"'\\\s)]+?_webres\.jpg", raw_html)
        return list(dict.fromkeys(found + [u for u in urls if "themodernhouse" in u]))
    return urls


def _ahash(im: Image.Image) -> int:
    small = im.convert("L").resize((8, 8))
    px = list(small.getdata())
    avg = sum(px) / len(px)
    return sum(1 << i for i, p in enumerate(px) if p > avg)


async def download_images(client: httpx.AsyncClient, urls: list[str], dest: Path,
                          max_ratio: float = MAX_RATIO, min_short: int | None = None,
                          min_long: int | None = None, max_keep: int = 14) -> list[Path]:
    """Качает, отбрасывает мелкие, пережатые, дубли и странные пропорции, сохраняет JPEG.
    Для каждого фото сначала пробует оригинал (variants): у многих сайтов в статье стоит уменьшенная копия.
    max_ratio, min_short и min_long — для кадров из фильмов мягче: широкий кадр 1280×536 — нормальный кадр.
    Память: каждое фото сразу ужимается до 2560 px и ложится на диск, в памяти не копятся оригиналы."""
    min_short = config.MIN_SHORT_SIDE if min_short is None else min_short
    min_long = config.MIN_LONG_SIDE if min_long is None else min_long
    dest.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(4)

    def verdict(w: int, h: int, q: int | None) -> str | None:
        if max(w, h) < min_long or min(w, h) < min_short:
            return "small"
        if q is not None and q < config.JPEG_MIN_QUALITY:
            return "squeezed"
        return None

    def process(raw: bytes, tmp: Path) -> dict | None:
        """Байты → проверка по заголовку файла (без распаковки) → ужатая копия на диске."""
        try:
            im = Image.open(io.BytesIO(raw))
            w, h = im.size
            q = jpeg_quality(im)
            bad = verdict(w, h, q)
            if bad:
                return {"bad": bad, "w": w, "h": h}
            im.load()
            hsh = _ahash(im)
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((2560, 2560), Image.LANCZOS)
            im.save(tmp, "JPEG", quality=92)
            return {"bad": None, "w": w, "h": h, "hash": hsh, "path": tmp}
        except Exception:
            return None

    async def fetch(i: int, u: str):
        async with sem:
            res = None
            for v in variants(u):
                try:
                    r = await client.get(v, timeout=40, follow_redirects=True)
                    r.raise_for_status()
                except Exception:
                    continue
                if len(r.content) > MAX_BYTES:
                    continue
                got = await asyncio.to_thread(process, r.content, dest / f"_tmp{i:02d}.jpg")
                if got is None:
                    continue
                if not got["bad"]:
                    return i, got
                res = res or got          # мелкое или пережатое — запомним причину, попробуем следующий вариант
            return i, res

    results = sorted(await asyncio.gather(*(fetch(i, u) for i, u in enumerate(urls))), key=lambda x: x[0])
    saved, hashes, small, squeezed = [], [], 0, 0
    for i, got in results:
        if not got:
            continue
        if got["bad"]:
            small += got["bad"] == "small"
            squeezed += got["bad"] == "squeezed"
            continue
        tmp = got["path"]
        w, h = got["w"], got["h"]
        if len(saved) >= max_keep or max(w, h) / min(w, h) > max_ratio \
                or any(bin(got["hash"] ^ x).count("1") <= 5 for x in hashes):
            tmp.unlink(missing_ok=True)
            continue
        hashes.append(got["hash"])
        p = dest / f"{len(saved):02d}.jpg"           # запас (14): Claude выберет до 10
        tmp.replace(p)
        saved.append(p)
    for t in dest.glob("_tmp*.jpg"):
        t.unlink(missing_ok=True)
    if small or squeezed:
        log.info("Фото отсеяны: мелкие %s, пережатые %s (%s)", small, squeezed, dest.name)
    return saved


# ---------- 100%: фрагменты для проверки резкости ----------

def _busiest(g: Image.Image, tile: int) -> tuple[int, int]:
    """Левый верхний угол окна tile×tile с самой богатой деталями областью (по разбросу яркости)."""
    w, h = g.size
    step = max(tile // 2, 1)
    best, at = -1.0, (max(0, (w - tile) // 2), max(0, (h - tile) // 2))
    for y in range(0, max(1, h - tile + 1), step):
        for x in range(0, max(1, w - tile + 1), step):
            v = ImageStat.Stat(g.crop((x, y, x + tile, y + tile))).stddev[0]
            if v > best:
                best, at = v, (x, y)
    return at


def save_detail_sheet(paths: list, out: Path) -> str | None:
    """Лист фрагментов — в файл рядом с фото кандидата (собирается в отдельном потоке, бот не замирает)."""
    b64 = detail_sheet(paths)
    if not b64:
        return None
    out.write_bytes(base64.b64decode(b64))
    return str(out)


def detail_sheet(paths: list, tile: int = 256, cols: int = 4) -> str | None:
    """Лист фрагментов: из каждого фото — окно tile×tile в 100% (без уменьшения), там, где больше всего деталей.
    В углу номер фото. По такому листу видно мыло, растянутое увеличение, шум и артефакты сжатия, которые
    на превью 480 px не разглядеть. → base64 JPEG или None."""
    crops = []
    for p in paths:
        try:
            with Image.open(p) as im:
                im = im.convert("RGB")
                g = im.convert("L")
                g.thumbnail((640, 640))                      # где искать — по уменьшенной копии, быстро
                k = im.width / g.width
                small_tile = max(8, round(tile / k))
                x, y = _busiest(g, small_tile)
                x, y = round(x * k), round(y * k)
                x, y = min(x, max(0, im.width - tile)), min(y, max(0, im.height - tile))
                crops.append(im.crop((x, y, x + tile, y + tile)))
        except Exception:
            crops.append(Image.new("RGB", (tile, tile), (128, 128, 128)))
    if not crops:
        return None
    rows = (len(crops) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile + (cols - 1) * 4, rows * tile + (rows - 1) * 4), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for n, c in enumerate(crops):
        x, y = (n % cols) * (tile + 4), (n // cols) * (tile + 4)
        sheet.paste(c, (x, y))
        draw.rectangle((x, y, x + 22, y + 16), fill=(0, 0, 0))
        draw.text((x + 5, y + 2), str(n), fill=(255, 255, 255))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode()


def thumb_b64(path: Path, size: int = 640) -> str:
    im = Image.open(path)
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()
