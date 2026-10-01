"""NUMBUS Branding — сервер Mini App (редактор бренда и шаблонов).

Работает в том же процессе, что и бот. Каждый запрос к /api подписан
Telegram: заголовок X-Init-Data проверяется HMAC-ом по токену бота
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
Редактировать может только владелец бренда.
"""
import os
import io
import re
import json
import hmac
import time
import asyncio
import hashlib
import logging
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web
from PIL import Image

import db
import render as R
import spec as S
import importer as I

logger = logging.getLogger("numbus.web")
BASE = os.path.dirname(os.path.abspath(__file__))
WEBAPP_FILE = os.path.join(BASE, "webapp.html")
INIT_MAX_AGE = 24 * 3600
IMPORT_MAX = 60 * 1024 * 1024          # PSD бывают тяжёлыми
IMG_KIND = re.compile(r"^img_[0-9a-f]{12}$")
FIGMA_API = "https://api.figma.com/v1"
UPLOAD_KINDS = {"logo", "logo_alt", "sample", "image"} | set(R.CUSTOM_FONT_SLOTS)
_SEM = asyncio.Semaphore(2)
_DEFAULT_SAMPLE = None


def check_init_data(init_data: str, token: str):
    """Возвращает dict пользователя Telegram или None."""
    if not init_data or not token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", None)
    if not got:
        return None
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > INIT_MAX_AGE:
            return None
        return json.loads(pairs.get("user", "{}")) or None
    except Exception:
        return None


async def heavy(fn, *a):
    async with _SEM:
        return await asyncio.to_thread(fn, *a)


def jerr(status, code):
    return web.json_response({"error": code}, status=status)


@web.middleware
async def auth_mw(request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    try:
        bid = int(request.query.get("b", "0"))
    except ValueError:
        return jerr(400, "brand")
    user = check_init_data(request.headers.get("X-Init-Data", ""), request.app["token"])
    if user:
        uid = int(user["id"])
    else:
        # Вход с компьютера: сессия, выданная по одноразовой ссылке из бота
        sess = db.check_session(request.headers.get("X-Session", ""))
        if not sess or sess[1] != bid:
            return jerr(401, "auth")
        uid = sess[0]
    if db.member_role(bid, uid) != "owner":
        return jerr(403, "owner_only")
    request["uid"] = uid
    request["bid"] = bid
    request["lang"] = (db.get_user(uid) or {}).get("lang", "ru")
    return await handler(request)


# ============ Статика ============
async def index(request):
    return web.FileResponse(WEBAPP_FILE, headers={"Cache-Control": "no-cache"})


_FONT_FILES = {v["file"] for v in R.FONTS.values()}


async def font_file(request):
    name = request.match_info["name"]
    path = os.path.join(R.FONT_DIR, name)
    if name not in _FONT_FILES or not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=2592000",
                                           "Content-Type": "font/ttf"})


# ============ Состояние ============
def _fonts_public(kit):
    out = [{"key": k, "label": v["label"], "file": v["file"], "min": v["min"], "max": v["max"],
            "group": v["group"]} for k, v in R.FONTS.items()]
    for slot, name in (kit.get("custom_fonts") or {}).items():
        out.append({"key": slot, "label": name, "custom": True, "min": 1, "max": 1000, "group": "custom"})
    return out


async def api_state(request):
    bid = request["bid"]
    b = db.get_brand(bid)
    kit = b["kit"]
    return web.json_response({
        "brand": {"id": bid, "name": kit.get("name") or "", "palette": S.sanitize_palette(kit.get("palette")),
                  "hashtags": kit.get("hashtags") or [], "custom_fonts": kit.get("custom_fonts") or {}},
        "assets": {k: db.has_asset(bid, k) for k in ("logo", "logo_alt", "sample")},
        "fonts": _fonts_public(kit),
        "templates": [{"id": t["id"], "name": t["name"], "spec": t["spec"]} for t in db.list_templates(bid)],
        "presets": S.presets_public(request["lang"]),
        "lang": request["lang"],
        "max_templates": db.MAX_TEMPLATES,
    })


async def api_kit(request):
    bid = request["bid"]
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "json")
    changes = {}
    if "name" in body:
        name = str(body["name"]).strip()[:40]
        if name:
            changes["name"] = name
    if "palette" in body:
        changes["palette"] = S.sanitize_palette(body["palette"])
    if "hashtags" in body:
        tags, seen = [], set()
        for t in body["hashtags"] if isinstance(body["hashtags"], list) else []:
            t = "#" + str(t).strip().lstrip("#")[:30]
            if len(t) > 1 and t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t)
        changes["hashtags"] = tags[:16]
    if changes:
        db.update_kit(bid, **changes)
    return web.json_response({"ok": True})


# ============ Ассеты ============
def _default_sample_jpeg():
    global _DEFAULT_SAMPLE
    if _DEFAULT_SAMPLE is None:
        img = R.sample_image(1200, 1500)
        _DEFAULT_SAMPLE = R.to_jpeg(img, 85)
    return _DEFAULT_SAMPLE


async def api_asset(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in ("logo", "logo_alt", "sample") and not IMG_KIND.match(kind):
        raise web.HTTPNotFound()
    data = db.get_asset(bid, kind)
    if not data:
        if kind == "sample":
            return web.Response(body=await heavy(_default_sample_jpeg), content_type="image/jpeg")
        raise web.HTTPNotFound()
    ctype = "image/jpeg" if kind == "sample" else "image/png"
    cache = "public, max-age=31536000, immutable" if kind.startswith("img_") else "no-cache"
    return web.Response(body=data, content_type=ctype, headers={"Cache-Control": cache})


async def api_font(request):
    slot = request.match_info["slot"]
    if slot not in R.CUSTOM_FONT_SLOTS:
        raise web.HTTPNotFound()
    data = db.get_asset(request["bid"], slot)
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="font/ttf")


def _prep_sample(data):
    img = R.open_photo(data)
    img.thumbnail((1600, 1600), Image.LANCZOS)
    return R.to_jpeg(img, 88)


def _prep_layer_image(data):
    img = R.open_image(data).convert("RGBA")
    img.thumbnail((2400, 2400), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    png = buf.getvalue()
    return "img_" + hashlib.sha1(png).hexdigest()[:12], png


async def api_upload(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in UPLOAD_KINDS:
        return jerr(400, "kind")
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file":
        return jerr(400, "file")
    filename = part.filename or ""
    data = bytes(await part.read(decode=False))
    if not data:
        return jerr(400, "empty")
    if kind in ("logo", "logo_alt"):
        try:
            png, had_alpha = await heavy(R.prepare_logo, data)
        except ValueError:
            return jerr(422, "logo_empty")
        except Exception:
            return jerr(422, "logo_bad")
        db.set_asset(bid, kind, png)
        return web.json_response({"ok": True, "bg_removed": not had_alpha})
    if kind == "image":
        try:
            aid, png = await heavy(_prep_layer_image, data)
        except Exception:
            return jerr(422, "photo_bad")
        db.set_asset(bid, aid, png)
        return web.json_response({"ok": True, "asset": aid})
    if kind == "sample":
        try:
            jpg = await heavy(_prep_sample, data)
        except Exception:
            return jerr(422, "photo_bad")
        db.set_asset(bid, "sample", jpg)
        return web.json_response({"ok": True})
    # свой шрифт
    if not filename.lower().endswith((".ttf", ".otf")) or not R.validate_font(data):
        return jerr(422, "font_bad")
    db.set_asset(bid, kind, data)
    kit = db.get_brand(bid)["kit"]
    fonts = dict(kit.get("custom_fonts") or {})
    fonts[kind] = R.font_name(data, os.path.splitext(filename)[0] or kind)
    db.update_kit(bid, custom_fonts=fonts)
    return web.json_response({"ok": True, "name": fonts[kind]})


async def api_asset_delete(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in ("logo_alt", "sample") and kind not in R.CUSTOM_FONT_SLOTS:
        return jerr(400, "kind")  # основной логотип удалить нельзя — только заменить
    db.del_asset(bid, kind)
    if kind in R.CUSTOM_FONT_SLOTS:
        kit = db.get_brand(bid)["kit"]
        fonts = dict(kit.get("custom_fonts") or {})
        fonts.pop(kind, None)
        db.update_kit(bid, custom_fonts=fonts)
    return web.json_response({"ok": True})


# ============ Шаблоны ============
async def _body_tpl(request):
    try:
        body = await request.json()
    except Exception:
        return None, None
    name = str(body.get("name") or "").strip()[:40] or "Шаблон"
    return name, S.sanitize_spec(body.get("spec"))


async def api_tpl_create(request):
    name, spec = await _body_tpl(request)
    if spec is None:
        return jerr(400, "json")
    tid = db.create_template(request["bid"], name, spec)
    if not tid:
        return jerr(409, "limit")
    return web.json_response({"id": tid, "name": name, "spec": spec})


async def api_tpl_update(request):
    name, spec = await _body_tpl(request)
    if spec is None:
        return jerr(400, "json")
    if not db.update_template(request["bid"], int(request.match_info["tid"]), name, spec):
        return jerr(404, "template")
    db.gc_images(request["bid"])
    return web.json_response({"id": int(request.match_info["tid"]), "name": name, "spec": spec})


async def api_tpl_delete(request):
    if not db.delete_template(request["bid"], int(request.match_info["tid"])):
        return jerr(404, "template")
    db.gc_images(request["bid"])
    return web.json_response({"ok": True})


def image_loader(bid):
    cache = {}

    def load(asset):
        if asset not in cache:
            data = db.get_asset(bid, asset) if isinstance(asset, str) and asset.startswith("img_") else None
            cache[asset] = Image.open(io.BytesIO(data)).convert("RGBA") if data else None
        return cache[asset]
    return load


def brand_ctx(bid, fields=None, dark=0.0):
    b = db.get_brand(bid)
    kit = b["kit"]
    logos = {}
    for k in ("logo", "logo_alt"):
        data = db.get_asset(bid, k)
        if data:
            logos[k] = Image.open(io.BytesIO(data)).convert("RGBA")
    customs = {slot: db.get_asset(bid, slot) for slot in (kit.get("custom_fonts") or {})}
    return R.Ctx(S.sanitize_palette(kit.get("palette")), logos, customs, fields or {}, dark, image_loader(bid))


def _server_preview(bid, spec, surface, fmt, fields):
    data = db.get_asset(bid, "sample")
    photo = R.open_photo(data) if data else R.sample_image(1200, 1500)
    ctx = brand_ctx(bid, fields)
    if surface == "story":
        W, H = R.STORY_SIZE
        layers = spec["story"]["layers"]
    else:
        W, H = R.feed_size(photo, fmt)
        layers = spec["feed"]["layers"]
    return R.to_preview(R.render_surface(photo, W, H, layers, ctx), 1400)


async def api_preview(request):
    """Точный рендер движком бота — то, что клиент получит в итоге."""
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "json")
    spec = S.sanitize_spec(body.get("spec"))
    f = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    fields = {k: str(f.get(k) or "")[:300] for k in ("title", "subtitle", "hashtag")}
    fields.update(i=1, n=8)
    fmt = body.get("fmt") if body.get("fmt") in R.FEED_SIZES else "4:5"
    surface = "story" if body.get("surface") == "story" else "feed"
    jpg = await heavy(_server_preview, request["bid"], spec, surface, fmt, fields)
    return web.Response(body=jpg, content_type="image/jpeg")


# ============ Импорт макетов ============
def _customs(bid):
    kit = db.get_brand(bid)["kit"]
    names = kit.get("custom_fonts") or {}
    return {slot: db.get_asset(bid, slot) for slot in names}, names


def _import_file_job(bid, data, filename):
    customs, names = _customs(bid)
    return I.build(I.parse_file(data, filename), customs, names)


def _import_figma_job(bid, plan, images):
    customs, names = _customs(bid)
    return I.build(I.figma_assemble(plan, images), customs, names)


def _save_import(bid, result, target, tid, name):
    layers, assets, report = result
    if not layers:
        raise I.ImportFail("empty")
    for aid, png in assets.items():
        db.set_asset(bid, aid, png)
    if target == "story" and tid:
        tpl = db.get_template(bid, tid)
        if not tpl:
            raise I.ImportFail("template")
        spec = tpl["spec"]
        spec["story"] = {"enabled": True, "layers": layers}
        spec = S.sanitize_spec(spec)
        db.update_template(bid, tid, tpl["name"], spec)
        out = {"id": tid, "name": tpl["name"], "spec": spec}
    else:
        spec = S.sanitize_spec({"feed": {"layers": layers}, "story": {"enabled": False, "layers": []}})
        name = (name or report.get("name") or "Импорт").strip()[:40] or "Импорт"
        new_id = db.create_template(bid, name, spec)
        if not new_id:
            raise I.ImportFail("limit")
        out = {"id": new_id, "name": name, "spec": spec}
    db.gc_images(bid)
    return {"template": out, "report": report}


def _import_error(e):
    status = 409 if e.code == "limit" else 422
    return web.json_response({"error": e.code}, status=status)


async def api_import(request):
    """Файл макета: PSD, AI, PDF или PNG. Поля: target=new|story, tid, file."""
    bid = request["bid"]
    target, tid, data, filename = "new", None, None, ""
    reader = await request.multipart()
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "target":
            target = "story" if (await part.text()).strip() == "story" else "new"
        elif part.name == "tid":
            txt = (await part.text()).strip()
            tid = int(txt) if txt.isdigit() else None
        elif part.name == "file":
            filename = part.filename or ""
            data = bytes(await part.read(decode=False))   # pdfium не принимает bytearray
    if not data:
        return jerr(400, "file")
    try:
        result = await heavy(_import_file_job, bid, data, filename)
        return web.json_response(_save_import(bid, result, target, tid, None))
    except I.ImportFail as e:
        return _import_error(e)
    except Exception as e:
        logger.exception("import %s: %s", filename, e)
        return jerr(422, "import_failed")


async def _figma_get(session, url, token, **params):
    async with session.get(url, params=params, headers={"X-Figma-Token": token}) as r:
        if r.status in (401, 403):
            raise I.ImportFail("figma_token")
        if r.status == 404:
            raise I.ImportFail("figma_access")
        if r.status == 429:
            raise I.ImportFail("figma_rate")
        if r.status != 200:
            raise I.ImportFail("figma_http", str(r.status))
        return await r.json()


async def api_import_figma(request):
    """Фрейм Figma по ссылке. Токен используется для одного запроса и не сохраняется."""
    bid = request["bid"]
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "json")
    token = str(body.get("token") or "").strip()
    target = "story" if body.get("target") == "story" else "new"
    tid = body.get("tid") if isinstance(body.get("tid"), int) else None
    if not token:
        return jerr(422, "figma_token")
    try:
        key, node = I.figma_ref(str(body.get("url") or ""))
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            doc = await _figma_get(s, f"{FIGMA_API}/files/{key}/nodes", token, ids=node)
            frame = ((doc.get("nodes") or {}).get(node) or {}).get("document")
            if not frame:
                raise I.ImportFail("figma_access")
            plan = I.figma_plan(frame)
            ids = [r["id"] for r in plan["render"]]
            images = {}
            scale = f"{max(0.01, min(4.0, plan['k'])):.3f}"
            for i in range(0, len(ids), 40):
                res = await _figma_get(s, f"{FIGMA_API}/images/{key}", token,
                                       ids=",".join(ids[i:i + 40]), format="png", scale=scale)
                for nid, url in (res.get("images") or {}).items():
                    if url:
                        async with s.get(url) as r:
                            if r.status == 200:
                                images[nid] = await r.read()
        result = await heavy(_import_figma_job, bid, plan, images)
        return web.json_response(_save_import(bid, result, target, tid, None))
    except I.ImportFail as e:
        return _import_error(e)
    except asyncio.TimeoutError:
        return jerr(422, "figma_timeout")
    except Exception as e:
        logger.exception("figma import: %s", type(e).__name__)
        return jerr(422, "import_failed")


# ============ Вход с компьютера ============
async def auth_login(request):
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "json")
    res = db.redeem_login_link(str(body.get("k") or ""))
    if not res:
        return jerr(401, "link")
    sess, uid, bid = res
    return web.json_response({"session": sess, "b": bid})


async def auth_logout(request):
    db.drop_session(request.headers.get("X-Session", ""))
    return web.json_response({"ok": True})


def build_web(token: str) -> web.Application:
    app = web.Application(middlewares=[auth_mw], client_max_size=IMPORT_MAX)
    app["token"] = token
    app.router.add_get("/", index)
    app.router.add_get("/fonts/{name}", font_file)
    app.router.add_post("/auth/login", auth_login)
    app.router.add_post("/auth/logout", auth_logout)
    app.router.add_get("/api/state", api_state)
    app.router.add_put("/api/kit", api_kit)
    app.router.add_get("/api/asset/{kind}", api_asset)
    app.router.add_delete("/api/asset/{kind}", api_asset_delete)
    app.router.add_get("/api/font/{slot}", api_font)
    app.router.add_post("/api/upload/{kind}", api_upload)
    app.router.add_post("/api/templates", api_tpl_create)
    app.router.add_put("/api/templates/{tid:\\d+}", api_tpl_update)
    app.router.add_delete("/api/templates/{tid:\\d+}", api_tpl_delete)
    app.router.add_post("/api/preview", api_preview)
    app.router.add_post("/api/import", api_import)
    app.router.add_post("/api/import/figma", api_import_figma)
    return app
