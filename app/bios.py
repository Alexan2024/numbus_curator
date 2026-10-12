"""Плашки указателя (7.3): кто это — бюро, архитектор, художник, фотограф — в одном-двух предложениях.

Где видно на сайте: на странице имени (/p/<id>/) под заголовком — текст, город, год основания, сайт; в записи
архива — карточка автора под текстом записи (со ссылкой на его страницу).

Откуда черновик. Сначала Википедия и Wikidata (бесплатно): кандидаты с похожим названием, их описание, годы,
год основания, официальный сайт. Claude решает, тот ли это человек (сверяет роль, время и записи архива), и пишет
плашку. Если в Википедии никого подходящего — сам ищет в сети (официальный сайт, профильные издания), до
BIO_SEARCHES запросов. Ничего надёжного — плашка остаётся пустой: лучше пусто, чем выдумано.

Черновик сам на сайт не идёт: его одобряют, правят или отклоняют в редакции (Указатель → Плашки).

Когда собирается
• Кнопка «Собрать черновики» в редакции — для всех имён без плашки. Свой потолок трат BIO_BUDGET_USD ($12),
  дневной лимит бота не трогает. Закончил — сообщение в Telegram со счётом.
• Сам, раз в 6 часов — для новых имён (пришли с новыми записями после запуска 7.3), до BIO_AUTO_MAX за раз,
  в счёт дневного потолка бота. Есть новые черновики — сообщение в Telegram.

Состояние — /data/admin/bios.json: {"people": {id: {st, d, src, note, at}}, "base": [id, …]}
  st: draft — ждёт решения; none — источников не нашлось; skip — отклонён (сам больше не собирается);
      ok — на сайте; err — сбой (сам повторит через сутки).
  base — имена, что были на сайте при запуске 7.3: их собирает только кнопка, не расписание.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta

import httpx
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web

from app import admin, config, curator

log = logging.getLogger(__name__)

STATE = admin.ROOT / "bios.json"
BUDGET = float(os.getenv("BIO_BUDGET_USD", "12") or 12)      # потолок одного пакета по кнопке, $
SEARCHES = int(os.getenv("BIO_SEARCHES", "2") or 2)          # запросов веб-поиска на одно имя
AUTO_MAX = int(os.getenv("BIO_AUTO_MAX", "8") or 8)          # новых имён за один проход расписания
EST = 0.035                                                  # средняя цена одного черновика, $ (для оценки)
PARALLEL = 3
BIO_MAX = 420                                                # длиннее — это уже не плашка
WM_UA = "AHMagazineBot/7.3 (https://theahmag.com; editor tools of AH Magazine)"
URL_RE = re.compile(r"^https?://[^\s/]+\.[^\s]+$")
YEAR_RE = re.compile(r"^(1[5-9]\d\d|20\d\d)$")

_lock = asyncio.Lock()
JOB: dict = {"running": False}


# ======================= состояние =======================

def _state() -> dict:
    s = admin._read(STATE, {})
    if not isinstance(s, dict):
        s = {}
    s.setdefault("people", {})
    return s


def _save(s: dict) -> None:
    admin._write(STATE, s)


def mark(pid: str, st: str) -> None:
    """Решение редакции по имени (плашка сохранена — ok, убрана — skip)."""
    s = _state()
    x = s["people"].setdefault(pid, {})
    x.update(st=st, at=admin._iso())
    _save(s)


def _put(pid: str, entry: dict) -> None:
    s = _state()
    old = s["people"].get(pid) or {}
    if old.get("st") in ("ok", "skip") and entry.get("st") != "draft":
        return                      # редакция уже решила, а сбой или пустой поиск решение не отменяют
    s["people"][pid] = {**entry, "at": admin._iso()}
    _save(s)


def ensure_base(D: dict) -> None:
    """Запомнить имена, что уже были на сайте: расписание берёт только новые."""
    s = _state()
    if "base" not in s:
        s["base"] = sorted(D["people"])
        _save(s)


def eligible(D: dict, s: dict | None = None) -> list[str]:
    """Имена без плашки, которые можно собрать кнопкой: ни разу не собирались или прошлый раз был сбой."""
    s = s or _state()
    out = []
    for pid, p in D["people"].items():
        if p.get("bio"):
            continue
        st = (s["people"].get(pid) or {}).get("st")
        if st in (None, "err"):
            out.append(pid)
    return sorted(out, key=lambda k: (-len(D["people"][k].get("objs") or []), k))


# ======================= Википедия и Wikidata =======================

def _toks(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", str(s or "")).replace("ё", "е").replace("Ё", "Е")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)          # «Foo (architect)» → «foo»
    return [t for t in re.split(r"[^\w]+", s) if t and t not in ("and", "und", "et", "y", "и", "the")]


def close(name: str, title: str) -> bool:
    """Похоже ли название статьи на имя: все слова имени есть в названии (лишних — не больше двух) или наоборот."""
    a, b = _toks(name), _toks(title)
    if not a or not b:
        return False
    sa, sb = set(a), set(b)
    if sa == sb:
        return True
    if sa <= sb and len(sb - sa) <= 2:
        return True
    return sb <= sa and len(sb) >= 2 and len(sa - sb) <= 1


class WikiDown(Exception):
    """Википедия или Wikidata не ответили — черновик соберётся по веб-поиску."""


async def _wm(c: httpx.AsyncClient, host: str, params: dict) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    last = None
    for attempt in range(3):
        try:
            r = await c.get(f"https://{host}/w/api.php", params=params, timeout=20)
            if r.status_code == 429:
                last = "429"
                await asyncio.sleep(3 + attempt * 4)
                continue
            r.raise_for_status()
            j = r.json()
            if isinstance(j, dict) and not j.get("error"):
                return j
            last = (j or {}).get("error") if isinstance(j, dict) else "не JSON"
        except (httpx.HTTPError, ValueError) as exc:
            last = repr(exc)
        await asyncio.sleep(1 + attempt)
    raise WikiDown(f"{host}: {str(last)[:120]}")


def _year(claims: dict, prop: str) -> int | None:
    for c in claims.get(prop) or []:
        try:
            t = c["mainsnak"]["datavalue"]["value"]["time"]           # «+1869-04-04T00:00:00Z»
            if c["mainsnak"]["datavalue"]["value"].get("precision", 11) >= 9:
                return int(t[1:5]) if t[0] == "+" else None
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _site_of(claims: dict) -> str | None:
    best = None
    for c in claims.get("P856") or []:
        try:
            v = c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if c.get("rank") == "preferred":
            return v
        best = best or v
    return best


def _ids(claims: dict, prop: str) -> list[str]:
    out = []
    for c in claims.get(prop) or []:
        try:
            out.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    return out


async def _search(c: httpx.AsyncClient, lang: str, name: str) -> list[str]:
    j = await _wm(c, f"{lang}.wikipedia.org", {"action": "query", "list": "search", "srsearch": name,
                                                 "srlimit": 5, "srprop": ""})
    return [x["title"] for x in (j.get("query") or {}).get("search") or [] if close(name, x.get("title", ""))]


async def _qids(c: httpx.AsyncClient, lang: str, titles: list[str]) -> list[str]:
    if not titles:
        return []
    j = await _wm(c, f"{lang}.wikipedia.org", {"action": "query", "prop": "pageprops", "redirects": 1,
                                                 "ppprop": "wikibase_item|disambiguation", "titles": "|".join(titles[:5])})
    pages = {p.get("title"): p for p in (j.get("query") or {}).get("pages") or []}
    order = [r.get("to") for r in (j.get("query") or {}).get("redirects") or []]
    out = []
    for t in titles + order:
        p = pages.get(t) or {}
        pp = p.get("pageprops") or {}
        if "disambiguation" in pp or not pp.get("wikibase_item"):
            continue
        if pp["wikibase_item"] not in out:
            out.append(pp["wikibase_item"])
    return out


async def _extracts(c: httpx.AsyncClient, lang: str, titles: list[str]) -> dict[str, str]:
    if not titles:
        return {}
    j = await _wm(c, f"{lang}.wikipedia.org", {"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                                 "redirects": 1, "exlimit": 5, "titles": "|".join(titles[:5])})
    return {p.get("title"): re.sub(r"\s+", " ", p.get("extract") or "").strip()[:1300]
            for p in (j.get("query") or {}).get("pages") or []}


def _cyr(s: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))


async def research(p: dict) -> tuple[list[dict], bool]:
    """Кандидаты из Википедии/Wikidata для имени → ([{qid, label, desc, human, born, died, founded, web, en, ru, urls}],
    ответила ли Википедия). Не ответила — пустой список: Claude поищет в сети сам."""
    try:
        return await _research(p), True
    except WikiDown as exc:
        log.info("Плашки: Википедия недоступна (%s) — только веб-поиск", exc)
        return [], False


async def _research(p: dict) -> list[dict]:
    en, ru = (p.get("en") or "").strip(), (p.get("ru") or "").strip()
    async with httpx.AsyncClient(headers={"User-Agent": WM_UA}, follow_redirects=True) as c:
        qids: list[str] = []
        if en:
            qids += await _qids(c, "en", await _search(c, "en", en))
        if ru and (_cyr(ru) or ru != en):
            for q in await _qids(c, "ru", await _search(c, "ru", ru)):
                if q not in qids:
                    qids.append(q)
        qids = qids[:3]
        if not qids:
            return []
        j = await _wm(c, "www.wikidata.org", {"action": "wbgetentities", "ids": "|".join(qids),
                                              "props": "claims|sitelinks|descriptions|labels", "languages": "en|ru",
                                              "sitefilter": "enwiki|ruwiki"})
        ents = j.get("entities") or {}
        out = []
        for q in qids:
            e = ents.get(q) or {}
            if not e or "missing" in e:
                continue
            cl = e.get("claims") or {}
            sl = e.get("sitelinks") or {}
            label = ((e.get("labels") or {}).get("en") or (e.get("labels") or {}).get("ru") or {}).get("value", "")
            desc = " / ".join(d.get("value", "") for k in ("en", "ru") for d in [(e.get("descriptions") or {}).get(k) or {}]
                              if d.get("value"))
            out.append({"qid": q, "label": label, "desc": desc, "human": "Q5" in _ids(cl, "P31"),
                        "born": _year(cl, "P569"), "died": _year(cl, "P570"), "founded": _year(cl, "P571"),
                        "web": _site_of(cl), "en_t": (sl.get("enwiki") or {}).get("title"),
                        "ru_t": (sl.get("ruwiki") or {}).get("title")})
        ex_en = await _extracts(c, "en", [x["en_t"] for x in out if x["en_t"]])
        ex_ru = await _extracts(c, "ru", [x["ru_t"] for x in out if x["ru_t"]])
    for x in out:
        x["en"] = ex_en.get(x["en_t"] or "", "")
        x["ru"] = ex_ru.get(x["ru_t"] or "", "")[:900]
        x["urls"] = [u for u in (
            f"https://en.wikipedia.org/wiki/{x['en_t'].replace(' ', '_')}" if x["en_t"] else None,
            f"https://ru.wikipedia.org/wiki/{x['ru_t'].replace(' ', '_')}" if x["ru_t"] else None,
            f"https://www.wikidata.org/wiki/{x['qid']}") if u]
    return out


# ======================= черновик =======================

BIO_SYSTEM = """Ты пишешь короткие плашки для указателя имён AH Magazine (theahmag.com) — визуального архива архитектуры, интерьеров, искусства, фотографии и кино. Плашка стоит на странице имени и под записью архива и отвечает на вопрос «кто это».

Тебе дают имя в двух написаниях, роль на сайте, записи архива с этим именем (по ним видно, о ком речь) и кандидатов из Википедии и Wikidata.

Как решать
1. Сначала пойми, о ком речь. Кандидат подходит, только если сходятся род занятий, время и работы: в Википедии архитектор той эпохи, а у нас его здание. Тёзка или однофамилец из другой области не подходит. Сомневаешься — не подходит.
2. Если ни один кандидат не подошёл или их нет, поищи в сети: официальный сайт бюро или автора, страница в профильном издании (ArchDaily, Dezeen, divisare, домус), музей, галерея. Подходит только источник, где точно этот человек или это бюро — сверяй с записями архива.
3. Ничего надёжного — верни "match": "none" и пустые тексты. Пустая плашка лучше выдуманной.

Как писать
- Одно-два предложения, по-русски не длиннее 240 знаков. Первое — кто это: откуда и чем занимается; для бюро — город и с какого года работает, если это известно. Второе, если есть что сказать, — одна точная деталь: чем известен, на чём сосредоточен, с чем работает.
- Только факты из источников. Ни года, ни города, ни награды от себя. Нет факта — не пиши о нём.
- Просто и живо, как пишет редактор, который знает предмет, а не пресс-служба. Никаких оценок и штампов: «уникальный», «знаковый», «культовый», «выдающийся», «легендарный», «гармония», «диалог с», «игра света и тени», «переосмысляет», «стирает границы», «вдохновлённый».
- Без антитезы «не X, а Y», без красивой концовки-афоризма, без перечня наград и регалий, без восклицаний, тире — не больше одного.
- Имя не повторяй: оно уже стоит заголовком. Начинай с сути: «Португальский архитектор из Коимбры…», «Бюро из Токио…».
- en — то же по-английски: те же факты, обычный разговорный английский. Never use: stunning, breathtaking, masterpiece, iconic, renowned, visionary, testament to, seamlessly, harmonious, "a dialogue between", "not X but Y", exclamation marks.

Тон (вымышленные примеры — факты из них не бери)
- «Швейцарское бюро из Базеля, работает с 2009 года. Строит в основном школы и жильё, почти всегда на деревянном каркасе, который оставляет открытым.»
- «Американский фотограф, полвека снимал маленькие города Среднего Запада — заправки, вывески, пустые улицы рано утром.»

Поля
- match: id кандидата (Q…), "web" — если плашка по найденному в сети, "none" — ничего надёжного.
- city: где работает бюро или автор, «Город, Страна»; для исторических фигур — город, с которым связана работа. Не знаешь — null.
- fd: год основания — только для бюро, студии, мастерской, и только если он есть в источнике; иначе null.
- web: официальный сайт бюро или автора, если нашёл (не Википедия, не соцсети, не журнал); иначе null.
- src: ссылки на страницы, по которым написана плашка.
- note: одна строка редактору: почему уверен, что это тот самый, или что под вопросом.

Верни ТОЛЬКО JSON:
{"match": "Q123", "ru": "…", "en": "…", "city": {"ru": "Порту, Португалия", "en": "Porto, Portugal"}, "fd": null, "web": null, "src": ["https://…"], "note": "…"}"""


def _entries(D: dict, pid: str, p: dict) -> str:
    by = {o["id"]: o for o in D["objects"]}
    lines = []
    for oid in (p.get("objs") or [])[:5]:
        o = by.get(oid)
        if not o:
            continue
        head = admin.md_plain((o.get("t") or {}).get("ru")) or admin.md_plain((o.get("t") or {}).get("en"))
        bits = [f"«{head}»", (o.get("pl") or {}).get("en") or "", (o.get("y") or {}).get("ru") or "",
                ", ".join(o.get("cats") or [])]
        lead = admin.md_plain((o.get("s") or {}).get("ru") or "")
        lines.append("- " + " · ".join(b for b in bits if b) + (f"\n  {lead[:220]}" if lead else ""))
    for n in D.get("notes") or []:
        if any(x.get("id") == pid for x in n.get("ppl") or []):
            lines.append(f"- упоминается в заметке «{admin.md_plain((n.get('t') or {}).get('ru'))}»")
    return "\n".join(lines) or "—"


def _cands_text(cands: list[dict], wiki_ok: bool = True) -> str:
    if not cands:
        return ("Кандидатов нет: в Википедии ничего похожего по названию." if wiki_ok
                else "Википедия сейчас не ответила — кандидатов нет, ищи в сети.")
    out = []
    for x in cands:
        yrs = (f"{x['born'] or '?'}–{x['died'] or ''}".rstrip("–") if x["born"] or x["died"] else "")
        meta = " · ".join(b for b in (
            "человек" if x["human"] else "организация или другое",
            f"годы: {yrs}" if yrs else "", f"основано: {x['founded']}" if x["founded"] else "",
            f"сайт: {x['web']}" if x["web"] else "") if b)
        out.append(f"[{x['qid']}] {x['label']} — {x['desc'] or 'без описания'}\n{meta}\n"
                   + (f"EN Wikipedia: {x['en']}\n" if x["en"] else "")
                   + (f"RU Википедия: {x['ru']}\n" if x["ru"] else ""))
    return "\n".join(out)


def _txt(v, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return "" if s.lower() in ("null", "none", "—", "-") else s[:limit]


def _bi(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    ru, en = _txt(v.get("ru"), 120), _txt(v.get("en"), 120)
    return {"ru": ru or en, "en": en or ru} if (ru or en) else None


def _flags(ru: str) -> list[str]:
    """Что редактору стоит перечитать: обороты, которые автор просил не использовать."""
    out = []
    if re.search(r"(^|[\s,.])не\s[^.,;]{1,50},\s*а\s", ru, re.I):
        out.append("антитеза «не X, а Y»")
    if ru.count("—") > 1:
        out.append("много тире")
    if len(ru) > 260:
        out.append(f"длинновато: {len(ru)} знаков")
    return out


async def draft_one(pid: str, p: dict, D: dict, *, background: bool) -> dict:
    """Черновик плашки для одного имени. → запись состояния {st, d, src, note}"""
    from app import voice
    t0 = time.time()
    cands, wiki_ok = await research(p)
    roles = ", ".join(p.get("roles") or [])
    banned = await voice.banned()
    prompt = (f"# Имя\nRU: {p.get('ru')}\nEN: {p.get('en')}\nРоль на сайте: {roles or '—'}\n"
              + (f"Годы жизни на сайте: {p['life']}\n" if p.get("life") else "")
              + f"\n# Записи архива с этим именем\n{_entries(D, pid, p)}\n\n"
              + f"# Кандидаты из Википедии и Wikidata\n{_cands_text(cands, wiki_ok)}\n"
              + (f"\n# Автор запретил эти слова и обороты\n{'; '.join(banned)}\n" if banned else "")
              + "\nРеши, кто это, и напиши плашку. Верни JSON.")
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max(1, SEARCHES)}] if SEARCHES else None
    out = await curator._call(prompt, system=BIO_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=1500, tools=tools,
                              background=background)
    return shape(out, cands, p, time.time() - t0)


def shape(out: dict, cands: list[dict], p: dict, secs: float = 0.0) -> dict:
    """Ответ Claude → запись состояния. Годы, год основания и сайт берутся из Wikidata, если кандидат подошёл."""
    by = {x["qid"]: x for x in cands}
    match = _txt(out.get("match"), 20)
    ru, en = _txt(out.get("ru"), BIO_MAX), _txt(out.get("en"), BIO_MAX)
    src = [u for u in (out.get("src") or []) if isinstance(u, str) and URL_RE.match(u.strip())]
    note = _txt(out.get("note"), 300)
    if (match not in by and match != "web") or not (ru or en):
        return {"st": "none", "note": note or "Ничего надёжного не нашлось", "src": src[:5]}
    w = by.get(match)
    d: dict = {"bio": {"ru": ru or en, "en": en}}
    city = _bi(out.get("city"))
    if city:
        d["city"] = city
    web_ = _txt(out.get("web"), 300)
    if w and w.get("web"):
        web_ = w["web"]
    if web_ and URL_RE.match(web_) and not re.search(r"wikipedia\.org|wikidata\.org|instagram\.com|facebook\.com", web_):
        d["web"] = web_
    fd = _txt(out.get("fd"), 8)
    if w and w.get("founded") and not w.get("human"):
        fd = str(w["founded"])
    if fd and YEAR_RE.match(fd) and ("studio" in (p.get("roles") or []) or (w and not w.get("human"))):
        d["fd"] = fd
    if w and w.get("human") and w.get("born") and w.get("died") and not p.get("life"):
        d["life"] = f"{w['born']}–{w['died']}"
    if w:
        src = w["urls"][:2] + [u for u in src if u not in w["urls"]]
    flags = _flags(ru)
    return {"st": "draft", "d": d, "src": list(dict.fromkeys(src))[:5], "match": match,
            "note": " · ".join(x for x in (note, "проверь: " + ", ".join(flags) if flags else "") if x), "secs": round(secs, 1)}


# ======================= пакет =======================

def _public_job() -> dict:
    return {k: v for k, v in JOB.items() if not k.startswith("_")}


async def _run(ids: list[str], cap: float, label: str, background: bool, notify: bool) -> None:
    """Черновики по списку имён — по три параллельно, пока не кончится список или потолок."""
    acc = [0.0]
    tok = curator.cost_sink(acc) if not background else None
    JOB.update(running=True, label=label, total=len(ids), done=0, made=0, none=0, err=0, cost=0.0, cap=cap,
               started=admin._iso(), stop=False, error=None, stopped="")
    sem = asyncio.Semaphore(PARALLEL)
    halt: list[str] = []
    down = [0]                        # API Anthropic не отвечает: три раза подряд — пакет встаёт

    async def one(pid: str, D: dict) -> None:
        async with sem:
            p = D["people"].get(pid)
            if halt or JOB["stop"] or not p or p.get("bio") or (not background and acc[0] >= cap):
                return
            try:
                entry = await draft_one(pid, p, D, background=background)
                down[0] = 0
            except curator.BudgetExceeded as exc:
                halt.append("" if background else curator.explain(exc))   # расписание просто подождёт завтра
                return
            except curator.NoCredits as exc:
                halt.append(curator.explain(exc))
                return
            except curator.ApiDown as exc:
                down[0] += 1
                if down[0] >= 3:
                    halt.append(curator.explain(exc))
                entry = {"st": "err", "note": "API Anthropic не ответил — соберётся в следующий раз"}
            except Exception as exc:
                log.warning("Плашки: %s не собралась: %r", pid, exc)
                entry = {"st": "err", "note": f"сбой: {curator.explain(exc)[:200]}"}
            _put(pid, entry)
            JOB["done"] += 1
            JOB[{"draft": "made", "none": "none"}.get(entry["st"], "err")] += 1
            JOB["cost"] = round(acc[0], 3)

    try:
        D, _ = await admin.effective()
        await asyncio.gather(*(one(p, D) for p in ids))
    except Exception as exc:
        log.exception("Плашки: пакет оборвался")
        halt.append(f"пакет оборвался: {curator.explain(exc)[:200]}")
    finally:
        if tok is not None:
            curator._sink.reset(tok)
        stopped = "остановлено" if JOB["stop"] else ("потолок трат" if not background and acc[0] >= cap and
                                                      JOB["done"] < len(ids) else "")
        JOB.update(running=False, finished=admin._iso(), error=halt[0] if halt and halt[0] else None, stopped=stopped)
    if notify or JOB["error"]:
        await _tell(label, JOB["error"], stopped)


async def _tell(label: str, error: str | None, stopped: str) -> None:
    from app import ui
    bot: Bot | None = ui.BOT
    if not bot or (not JOB["made"] and not error and label == "auto"):
        return
    lines = [f"🪪 <b>Плашки указателя</b>{' — новые имена' if label == 'auto' else ''}",
             f"Черновиков: {JOB['made']}" + (f" · не нашлось: {JOB['none']}" if JOB["none"] else "")
             + (f" · сбоев: {JOB['err']}" if JOB["err"] else "")]
    if label != "auto":
        lines.append(f"Потрачено: ${JOB['cost']:.2f}" + (f" ({stopped})" if stopped else ""))
    if error:
        lines.append("⚠️ " + html.escape(error[:300]))
    lines.append("Одобрить или поправить — в редакции: Указатель → Плашки.")
    url = admin.admin_url()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 Открыть плашки", url=url + "#/bios")]]) \
        if url and url.startswith("https://") else None
    try:
        await bot.send_message(config.ADMIN_ID, "\n".join(lines), reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        log.warning("Плашки: сообщение не ушло", exc_info=True)


def _start(ids: list[str], cap: float, label: str, background: bool, notify: bool) -> None:
    if JOB.get("running"):
        raise admin.AdminError("Черновики уже собираются — дождись конца")
    JOB.update(running=True, label=label, total=len(ids), done=0, made=0, none=0, err=0, cost=0.0)
    from app import sitepub
    task = asyncio.create_task(_run(ids, cap, label, background, notify))
    sitepub._tasks.add(task)
    task.add_done_callback(sitepub._tasks.discard)


async def auto(bot: Bot | None = None) -> None:
    """Расписание: черновики для имён, что появились после запуска 7.3 (и для сбоев прошлого раза)."""
    from app import sitepub
    if JOB.get("running") or not await sitepub.enabled():
        return
    D, _ = await admin.effective()
    if not D.get("people"):
        return
    async with _lock:
        ensure_base(D)
        s = _state()
        base = set(s.get("base") or [])
        day = 86400
        todo = []
        for pid, p in D["people"].items():
            x = s["people"].get(pid) or {}
            if p.get("bio") or pid in base:
                continue
            if x.get("st") is None or (x.get("st") == "err" and time.time() - _ts(x.get("at")) > day):
                todo.append(pid)
    if todo:
        _start(todo[:AUTO_MAX], 0.0, "auto", background=True, notify=True)


def _ts(iso: str | None) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return 0.0


def schedule(sched, bot: Bot, guarded) -> None:
    sched.add_job(guarded(bot, "плашки указателя", auto, bot), "interval", hours=6, id="bios_auto", max_instances=1,
                  next_run_time=admin._now() + timedelta(minutes=20))


# ======================= редакция: API =======================

async def api_list(request: web.Request) -> web.Response:
    D, _ = await admin.effective()
    ensure_base(D)
    s = _state()
    todo = eligible(D, s)
    return web.json_response({"st": s["people"], "job": _public_job(), "todo": len(todo), "est": EST, "cap": BUDGET,
                              "searches": SEARCHES}, headers={"Cache-Control": "no-store"})


async def api_run(request: web.Request) -> web.Response:
    b = await admin._body(request)
    D, _ = await admin.effective()
    if b.get("all"):
        ids = eligible(D)
        label = "all"
    else:
        ids = [admin._s(x, 80) for x in (b.get("ids") or []) if admin._s(x, 80) in D["people"]][:50]
        label = "some"
    if not ids:
        raise admin.AdminError("Собирать нечего: у всех имён уже есть плашка или решение")
    cap = min(BUDGET, max(0.5, float(b.get("cap") or BUDGET)))
    _start(ids, cap, label, background=False, notify=label == "all")
    return admin._ok(n=len(ids))


async def api_stop(request: web.Request) -> web.Response:
    if JOB.get("running"):
        JOB["stop"] = True
    return admin._ok()


async def api_skip(request: web.Request) -> web.Response:
    b = await admin._body(request)
    pid = admin._s(b.get("id"), 80)
    async with _lock:
        s = _state()
        x = s["people"].setdefault(pid, {})
        x.update(st="skip" if not b.get("undo") else ("draft" if x.get("d") else None), at=admin._iso())
        if x["st"] is None:
            s["people"].pop(pid, None)
        _save(s)
    return admin._ok()


async def api_approve(request: web.Request) -> web.Response:
    """Черновик (как есть или поправленный) → на сайт: та же правка имени, что в указателе."""
    b = await admin._body(request)
    pid = admin._s(b.get("id"), 80)
    D, _ = await admin.effective()
    p = D["people"].get(pid)
    if not p:
        raise admin.AdminError("Такого имени в указателе нет")
    card = admin.clean_card({k: b.get(k) for k in ("bio", "city", "fd", "web") if k in b})
    if not card.get("bio"):
        raise admin.AdminError("Плашка пустая — напиши текст или нажми «Отклонить»")
    rec = {"ru": p["ru"], "en": p["en"], "roles": p.get("roles") or ["architect"], **card}
    life = admin._s(b.get("life"), 60)
    if life and not p.get("life"):
        rec["life"] = life
    await admin.enqueue({"t": "person", "id": pid, "rec": rec})
    async with _lock:
        s = _state()
        x = s["people"].setdefault(pid, {})
        x.update(st="ok", at=admin._iso())
        _save(s)
    return admin._ok()


def setup(r) -> None:
    for method, path, fn in (("GET", "bio/list", api_list), ("POST", "bio/run", api_run), ("POST", "bio/stop", api_stop),
                             ("POST", "bio/skip", api_skip), ("POST", "bio/approve", api_approve)):
        r.add_route(method, f"/admin/api/{path}", admin.auth(fn))
