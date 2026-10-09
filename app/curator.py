"""Все обращения к Claude: первичный фильтр (Haiku), оценка (Sonnet, пакетами и с кэшем),
тексты больших постов и заметок (Opus), учёт расходов в долларах."""
import base64
import contextvars
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
from anthropic import AsyncAnthropic

from app import config, db, media, voice

log = logging.getLogger(__name__)
client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


# ---------- профиль вкуса ----------

def _taste_profile(raw: str) -> str:
    """Из профиля убран раздел «5. Голос» с примерами: его приёмы («не X, а Y», афоризм в конце,
    «выверенный», «сдержанный») звучали как нейросеть. Голос теперь задаёт voice.RULES."""
    out = re.sub(r"\n## 5\..*?(?=\n## 6\.)", "\n", raw, flags=re.S)
    out = re.sub(r"\*\*Целевой микс[^\n]*\n", "", out)
    return out


def _section(raw: str, n: int) -> str:
    """Один раздел профиля («## 4. …») целиком — для коротких промптов, где весь профиль не нужен."""
    m = re.search(rf"\n## {n}\..*?(?=\n## \d+\.|\Z)", "\n" + raw, flags=re.S)
    return m.group(0).strip() if m else ""


PROFILE = _taste_profile(config.PROFILE_PATH.read_text(encoding="utf-8"))
PROFILE_FORMAT = _section(PROFILE, 4) or PROFILE   # правила заголовка и кредитов
ARCHIVE = json.loads(config.ARCHIVE_PATH.read_text(encoding="utf-8"))
def _within_repeat_window(p: dict) -> bool:
    """Пост из архива канала вышел не раньше REPEAT_DAYS назад? Старше — объект можно показать снова."""
    try:
        day = datetime.strptime(p.get("date", ""), "%d %B %Y").date()
    except ValueError:
        return True
    return day >= date.today() - timedelta(days=config.REPEAT_DAYS)


ARCHIVE_HEADLINES = [p["headline"] for p in ARCHIVE if p.get("headline") and _within_repeat_window(p)]
NOTES_HEADLINES = [p["headline"] for p in ARCHIVE if "ahmagnotes" in (p.get("tags") or []) and p.get("headline")]
# Примеры заголовков и тегов из архива — только формат, без текстов
HEADLINE_EXAMPLES = "\n".join(
    f"{p['headline']}  →  " + " ".join("#" + t for t in (p.get("tags") or []))
    for p in [p for p in ARCHIVE if " // " in (p.get("headline") or "") and "ahmagnotes" not in (p.get("tags") or [])][:8])


# ---------- ошибки ----------

class BudgetExceeded(RuntimeError):
    """Достигнут дневной потолок фоновых трат или вызовов."""


class NoCredits(RuntimeError):
    """На счёте Anthropic закончились средства."""


class ApiDown(RuntimeError):
    """API Anthropic временно недоступен."""


def explain(exc: Exception) -> str:
    """Человеческое объяснение ошибки для сообщения в чат."""
    if isinstance(exc, NoCredits):
        return ("На счёте Anthropic закончились средства. Пополните баланс: "
                "console.anthropic.com → Plans & Billing → Add credits. После этого всё заработает само.")
    if isinstance(exc, BudgetExceeded):
        return (f"Дневной потолок фоновых трат исчерпан (${config.DAILY_BUDGET_USD:.2f} или "
                f"{config.DAILY_API_CALLS_MAX} вызовов). Сбор продолжится завтра; поднять потолок — "
                "переменная DAILY_BUDGET_USD. Кнопки работают как обычно.")
    if isinstance(exc, ApiDown):
        return "API Anthropic сейчас недоступен. Обычно это ненадолго — попробуйте через несколько минут."
    return f"Что-то пошло не так: {exc!r}"


def _map_error(exc: Exception) -> Exception:
    if isinstance(exc, anthropic.APIStatusError):
        detail = str(getattr(exc, "message", "") or exc).lower()
        if "credit balance" in detail or "billing" in detail:
            return NoCredits()
        if exc.status_code in (429, 500, 502, 503, 529):
            return ApiDown()
    if isinstance(exc, anthropic.APIConnectionError):
        return ApiDown()
    return exc


# ---------- расход ----------

# $ за миллион токенов: вход, выход, запись в кэш (5 мин), чтение из кэша
PRICES = {
    "opus": (5.0, 25.0, 6.25, 0.5),
    "sonnet": (2.0, 10.0, 2.5, 0.2),
    "haiku": (1.0, 5.0, 1.25, 0.1),
}


def cost_of(model: str, usage, batch: bool = False) -> float:
    p = next((v for k, v in PRICES.items() if k in (model or "")), PRICES["sonnet"])
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    c = ((usage.input_tokens or 0) * p[0] + (usage.output_tokens or 0) * p[1] + cw * p[2] + cr * p[3]) / 1e6
    if batch:
        c *= 0.5
    stu = getattr(usage, "server_tool_use", None)
    searches = getattr(stu, "web_search_requests", 0) or 0 if stu else 0
    return c + searches * 0.01


# Отдельный счёт для долгих работ (архив сайта): пока внутри контекста задан список [сумма], каждый вызов
# Claude прибавляет туда свою стоимость. Так у архива свой потолок, отдельный от дневного.
_sink: contextvars.ContextVar = contextvars.ContextVar("cost_sink", default=None)


def cost_sink(acc: list):
    """with-блока нет: token = cost_sink(acc) … _sink.reset(token)."""
    return _sink.set(acc)


async def _record(model: str, usage, background: bool, batch: bool = False) -> None:
    acc = _sink.get()
    if acc is not None:          # свой бюджет: дневной лимит бота не трогаем
        try:
            acc[0] += cost_of(model, usage, batch)
        except Exception:
            pass
    try:
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        await db.add_usage((usage.input_tokens or 0) + cw + cr, usage.output_tokens or 0,
                           cost_of(model, usage, batch), background and acc is None, count_call=acc is None)
    except Exception:
        log.warning("расход не записан", exc_info=True)


async def budget_ok() -> bool:
    return (await db.calls_today() < config.DAILY_API_CALLS_MAX
            and await db.cost_today(background=True) < config.DAILY_BUDGET_USD)


# ---------- общий вызов ----------

def _system(text: str) -> list[dict]:
    """Системный промпт неизменный — кэшируем: повторное чтение стоит десятую часть цены."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _img(path, size: int | None = None) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": media.thumb_b64(Path(path), size or config.THUMB_SIZE)}}


_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _close_of(text: str, i: int) -> int:
    """Где закрывается скобка, открытая в позиции i (грубо, по счёту скобок); -1 — не закрывается."""
    depth = 0
    for k in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[k], 0)
        if depth == 0:
            return k
    return -1


def _parse_json(text: str) -> dict:
    """Ответ Claude → dict. Терпит то, на чём раньше падало: текст до и после JSON (в том числе с фигурными
    скобками), переносы строк прямо внутри строк, висячие запятые. Берёт самый большой разбираемый объект —
    но не обломок изнутри битого или обрезанного ответа: такой случай остаётся ошибкой (дальше — повтор/починка)."""
    clean = re.sub(r"```(?:json)?", "", text).strip()
    if clean.find("{") < 0 or clean.rfind("}") <= clean.find("{"):
        raise ValueError(f"Claude вернул не JSON: {text[:200]!r}")
    dec = json.JSONDecoder(strict=False)
    last: Exception | None = None
    for cand in (clean, _TRAILING_COMMA.sub(r"\1", clean)):
        best, b0, b1, failed = None, 0, 0, []
        i = cand.find("{")
        while i >= 0:
            if best is not None and i < b1:      # внутри уже найденного объекта — дальше не ищем
                i = cand.find("{", i + 1)
                continue
            try:
                obj, j = dec.raw_decode(cand, i)
                if isinstance(obj, dict) and j - i > b1 - b0:
                    best, b0, b1 = obj, i, j
            except ValueError as exc:
                last = last or exc
                failed.append(i)
            i = cand.find("{", i + 1)
        # битая скобка, внутри которой лежит найденный объект, — значит найден обломок, а не ответ
        if best is not None and not any(f < b0 and not (0 <= _close_of(cand, f) < b0) for f in failed):
            return best
    raise ValueError(f"Claude вернул не JSON (битый): {last}")


def _public(data: dict) -> dict:
    """Без служебных полей (_source_text и т.п.) — чтобы не гонять их в промпт."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


async def _create(params: dict, background: bool) -> tuple[str, str | None]:
    """→ (текст, stop_reason). stop_reason == "max_tokens" — ответ обрезан на полуслове."""
    if background and not await budget_ok():
        raise BudgetExceeded()
    messages = list(params["messages"])
    text = ""
    resp = None
    for _ in range(4):  # веб-поиск может вернуть pause_turn — тогда продолжаем тот же ход
        try:
            resp = await client.messages.create(**{**params, "messages": messages})
        except Exception as exc:
            raise _map_error(exc) from exc
        await _record(params["model"], resp.usage, background)
        text += "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    if not text.strip():
        log.warning("Пустой ответ Claude: model=%s stop_reason=%s max_tokens=%s",
                    params.get("model"), getattr(resp, "stop_reason", None), params.get("max_tokens"))
    return text, getattr(resp, "stop_reason", None)


REPAIR_SYSTEM = ("You repair broken JSON. Return the same data as ONE valid JSON object: escape quotes inside "
                 "strings, remove trailing commas, drop any text around the object, close brackets if the end is cut. "
                 "Never change, translate, shorten or add values. Output only the JSON, no code fences.")


async def _repair_json(text: str, background: bool) -> dict:
    """Дешёвая починка битого JSON через Haiku — вместо повтора всего дорогого запроса с картинкой."""
    params = {"model": config.TRIAGE_MODEL, "max_tokens": min(16000, len(text) + 500),
              "system": _system(REPAIR_SYSTEM), "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]}
    fixed, _ = await _create(params, background)
    return _parse_json(fixed)


async def _ask_json(params: dict, background: bool) -> dict:
    """Запрос → JSON. Три известные поломки и что с ними делаем:
    1) пустой ответ (весь запас ушёл на размышления/поиск) и 2) ответ обрезан по max_tokens — ещё раз с запасом
    втрое больше; 3) JSON битый (кавычка внутри текста, мусор вокруг) — чиним через Haiku, а не повторяем всё."""
    text, stop = await _create(params, background)
    if not text.strip() or stop == "max_tokens":
        log.warning("Ответ Claude %s (max_tokens=%s) — повторяю с запасом ×3",
                    "обрезан" if text.strip() else "пустой", params["max_tokens"])
        text, stop = await _create({**params, "max_tokens": params["max_tokens"] * 3}, background)
    try:
        return _parse_json(text)
    except ValueError as exc:
        if not text.strip():
            raise
        log.warning("Битый JSON от Claude (stop=%s, %s симв.): %s | начало: %r | конец: %r",
                    stop, len(text), exc, text[:300], text[-300:])
    try:
        return await _repair_json(text, background)
    except ValueError as exc:
        raise ValueError(f"Claude вернул не JSON и починить не вышло: {exc}") from exc


async def _call(content: list | str, *, system: str, model: str, max_tokens: int = 2000,
                tools: list | None = None, background: bool = False) -> dict:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    params = {"model": model, "max_tokens": max_tokens, "system": _system(system),
              "messages": [{"role": "user", "content": content}]}
    if tools:
        params["tools"] = tools
    return await _ask_json(params, background)


async def ping() -> None:
    """Дешёвая проверка доступа к API — для /diag."""
    try:
        await client.messages.create(model=config.TRIAGE_MODEL, max_tokens=4,
                                     messages=[{"role": "user", "content": "ping"}])
    except Exception as exc:
        raise _map_error(exc) from exc


# ---------- 1. первичный фильтр (Haiku, текст без фото) ----------

NEWS_YES = ("\n• новость о вещи нашего вкуса: открылось построенное здание, открылась выставка художника или фотографа "
            "из нашего круга, реставрация, снос или угроза сносу, находка, умер мастер, вышла книга." if config.NEWS else "")

TRIAGE_SYSTEM = """Ты — первый фильтр для Telegram-канала AHMAG об архитектуре, искусстве, фотографии, архивах и кино. По заголовку и началу текста реши, стоит ли показывать материал редактору. Подробно его посмотрят потом; сейчас важно отсеять явно чужое.

yes — похоже на вкус канала:
• построенная архитектура и интерьеры: частные дома, небольшие объекты малоизвестных бюро, модернистская и послевоенная классика, сакральное, руины, мемориалы, переделка старых зданий; естественные материалы, свет, связь с ландшафтом;
• искусство без кича: сюрреализм и тихая метафизика, лэнд-арт, объекты в среде, мастер за работой, визуальная культура (гравюры, манускрипты, вывески, мультипликация), тёплый юмор;
• документальная, уличная, архивная фотография, этнография;
• исторические серии и находки из прошлого;
• авторское кино с сильной визуальной стороной, закулисье съёмок.{NEWS_YES}

no — не наше:
• рендеры, конкурсы, концепции и неосуществлённые проекты; небоскрёбы, девелоперские комплексы, офисы, торговые центры, сетевые отели;
• продуктовый и промышленный дизайн, мебель, гаджеты, мода, автомобили, еда;
• новости индустрии (бизнес, назначения, рейтинги, финансы), анонсы мероприятий, вакансии, подборки и рейтинги, реклама, интервью без конкретной работы, политика;
• обзоры мейнстримного кино и сериалов.

maybe — если не ясно.

Архитектуры в потоке много, к ней будь строже. К искусству, фотографии, архиву и кино — мягче.
Рубрика cat: architecture (включая интерьеры), art, photography, archive, cinema.

Верни ТОЛЬКО JSON: {"r": [{"i": 0, "v": "yes|maybe|no", "cat": "architecture", "why": "3–6 слов"}]}""".replace(
    "{NEWS_YES}", NEWS_YES)


async def triage(items: list[dict]) -> dict[int, dict]:
    """items: [{i, source, hint, title, excerpt}] → {i: {v, cat, why}}"""
    lines = [f"[{it['i']}] {it['source']} ({it['hint']}) | {it['title']}\n{it['excerpt']}" for it in items]
    data = await _call("\n\n".join(lines), system=TRIAGE_SYSTEM, model=config.TRIAGE_MODEL,
                       max_tokens=60 * len(items) + 200, background=True)
    out = {}
    for r in data.get("r") or []:
        try:
            out[int(r["i"])] = r
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ---------- 2. оценка (Sonnet, с фото) ----------

if config.MINI_IG:
    _FORMAT_RULES = f"""- "std" — пост для Telegram-канала и сайта (в Instagram он тоже уйдёт, с короткой подписью). Только если есть угол — см. «Правило угла» выше: история, которой не видно на фото. Угол запиши в angle. И нужно не меньше {config.MIN_PHOTOS_ARTICLE} хороших фото.
- "mini" — фото-пост только для Instagram: красивые фото, а истории нет или фото говорят сами. В канал и на сайт он не идёт. Если сомневаешься, есть ли угол, — mini."""
else:
    _FORMAT_RULES = f"""- "std" — большой пост: заголовок, 1–2 абзаца, кредиты, теги. Только если есть угол — см. «Правило угла» выше. Угол запиши в angle. И нужно не меньше {config.MIN_PHOTOS_ARTICLE} хороших фото.
- "mini" — всё остальное: заголовок, одна простая фраза, кредиты и теги, от 1 до {config.MINI_MAX_PHOTOS} фото. Если сомневаешься — mini."""

_NEWS_RULES = """
# Новость (kind)
kind = "news", если материал сообщает о событии последних дней: открылось построенное здание или выставка, реставрация, снос или угроза сносу, находка, умер мастер, конкретная работа получила важную премию, вышла книга. И только если сам объект или человек — из вкуса канала: AHMAG написал бы о нём и без повода. Новость — всегда std, angle_type — «новость», в news_date — дата события, если она есть в тексте. Анонсы мероприятий, отраслевые новости, рейтинги, назначения, деньги и политика — не наши новости: низкая оценка.
Обычная публикация проекта или работы — kind = "object".
Для новости already_posted = false, даже если о самой вещи канал уже писал: событие новое. Для новости всегда заполняй kind.
""" if config.NEWS else ""

_PHOTO_RULES = """# Качество фото (soft_photos)
Если после превью есть лист фрагментов — из каждого фото кусок в 100% масштабе, без уменьшения; номер в углу — номер фото, — суди о качестве по нему, а не по превью. В soft_photos перечисли номера фото, которые на 100% явно плохи: мыло (вне фокуса, смазано), растянутое увеличение (контуры мягкие и «оплывшие», как у апскейла), сильные артефакты сжатия (квадраты, ореолы вокруг контуров), сильный цифровой шум, водяной знак или текст поверх. Плёночное зерно и мягкость старой фотографии — не дефект. Сомневаешься — не включай. Эти фото в пост не попадут. Листа нет — soft_photos пустой, если только превью не показывает явный брак.""" if config.PHOTO_CHECK else """# Качество фото (soft_photos)
В soft_photos перечисли номера фото с явным браком на превью: сильное мыло, водяной знак или текст поверх. Сомневаешься — не включай."""

EVAL_SYSTEM = f"""Ты — редактор-куратор AHMAG: Telegram-канал, Instagram и сайт. Ниже профиль канала: вкус, темы и формат.

{PROFILE}

{voice.EDITORIAL}

{voice.RULES}

# Твоя задача
Тебе дают материал-кандидат: текст источника, пронумерованные превью фото и, если есть, лист фрагментов этих фото в 100% масштабе.
1. Проверь стоп-лист и повтор: сравни со списком «Уже опубликовано» ниже и с недавними заголовками из сообщения.
2. Оцени соответствие вкусу канала по шкале 0–10 (раздел 6 профиля). Будь строгим: 7 и выше — только то, что автор канала опубликовал бы сам.
3. Если оценка не ниже {config.SCORE_THRESHOLD}: определи рубрику, формат и угол, составь заголовок, кредиты, теги, одну фразу для фото-поста, отметь плохие фото и задай порядок фото. Основной текст поста сейчас НЕ пиши: его напишут отдельно, если пост выберут.

# Рубрика (category)
architecture — архитектура и интерьеры; art — искусство, скульптура, инсталляции, выставки, музейные предметы; photography — фотография; archive — исторические серии, старые снимки и документы визуальной культуры; cinema — кино.

# Формат (format) и угол (angle)
{_FORMAT_RULES}
angle — одна фраза: факт из материала, ради которого стоит писать (не оценка). angle_type — тип угла: судьба, человек, конфликт, деталь, контекст, парадокс, новость. Угла в материале нет — angle и angle_type = null, формат mini.
{_NEWS_RULES}
# Фраза фото-поста (mini_line)
Одна простая фраза до 140 знаков: что это за вещь и что видно на фото, как сказал бы человек в переписке. Пиши её почти всегда, и для std тоже. null — только если заголовок уже сказал всё.
Хорошо: «Бетонная часовня посреди поля, внутри обугленные стены и дыра в потолке.» · «Большая волна в Канагаве Хокусая, та самая, с маленькой Фудзи на заднем плане.» · «Ночной Париж Брассаи: туман, фонари и мокрая брусчатка.»
Плохо: «Архитектура, которая растворяется в тишине.» · «Не дом, а манифест.» · «Гармония света и материала.»

# Заголовок, кредиты, теги
- Заголовок и кредиты — по правилам раздела 4 профиля. Только факты из материала: неизвестные год, город, фотограф — null. Не выдумывай.
- Теги: 2–3, строчными, с префиксом ahmag: сначала рубрика (architecture, interiors, art, sculpture, photography, archive, cinema), затем страна по-английски одним словом. Для исторического материала добавь ahmagarchive.
- Так выглядят заголовки канала:
{HEADLINE_EXAMPLES}

{_PHOTO_RULES}

# Фото (photo_order)
photo_order — индексы превью в порядке публикации, без фото из soft_photos: для std от {config.MIN_PHOTOS_ARTICLE} до {config.EVAL_PHOTOS}, для mini от 1 до {config.MINI_MAX_PHOTOS}. Последовательность: общий план → детали и материал → интерьер и свет. Исключай слабые, повторяющиеся, с текстом поверх, чертежи без необходимости.

# Уже опубликовано в канале (не повторять)
{chr(10).join(ARCHIVE_HEADLINES)}

# Формат ответа
Верни ТОЛЬКО JSON без пояснений и без markdown:
{{
  "stoplist": false,
  "already_posted": false,
  "score": 0,
  "score_reason": "одна фраза по-русски, почему такая оценка",
  "category": "architecture|art|photography|archive|cinema",
  "kind": "object|news",
  "format": "std|mini",
  "angle": "одна фраза или null",
  "angle_type": "судьба|человек|конфликт|деталь|контекст|парадокс|новость или null",
  "news_date": null,
  "headline_parts": ["Название", "Автор/бюро или null", "Город, Страна, Год или null"],
  "mini_line": "одна простая фраза или null",
  "credits": {{"pr": null, "pr_url": null, "ph": null, "ph_url": null, "via": null}},
  "tags": ["ahmagarchitecture", "ahmagjapan"],
  "soft_photos": [],
  "photo_order": [0, 1, 2],
  "flags": ["нет ph"]
}}
Если оценка ниже {config.SCORE_THRESHOLD}, stoplist или already_posted — достаточно полей stoplist, already_posted, score, score_reason, category и kind."""

SHEET_NOTE = "Лист фрагментов в 100% масштабе (номер в углу — номер фото):"

# Метка поста, который автор заказал сам (по ссылке или по запросу): по ней evaluate() понимает,
# что оформление нужно полностью, даже если оценка низкая.
FORCED_NOTE = "Автор канала сам прислал этот материал и хочет пост по нему."


async def eval_context() -> str:
    """Общая для всех кандидатов прохода часть: недавние заголовки и отказы автора."""
    recent = await db.recent_headlines()
    parts = ["# Недавно в канале и в очереди (не повторять)\n" + ("\n".join(recent) or "—")]
    rej = await db.recent_rejections()
    if rej:
        parts.append("# Недавно отклонено автором — учитывай при оценке\n" + "\n".join(
            f"- {json.loads(r['data']).get('headline', '?')} — {r['reject_reason']}" for r in rej))
    from app import taste
    rules = await taste.active_text("select")
    if rules:
        parts.append(rules)
    return "\n\n".join(parts)


def eval_params(context: str, source: str, url: str, title: str, text: str, images: list,
                allow_std: bool = True, forced: bool = False, published: str = "", sheet: str | None = None) -> dict:
    extra = []
    if not allow_std:
        extra.append(f"Качественных фото меньше {config.MIN_PHOTOS_ARTICLE}: возможен только формат mini.")
    if forced:
        extra.append(FORCED_NOTE + " Оценку поставь честно, но заполни ВСЕ поля ответа: headline_parts, "
                     "mini_line, credits, tags, format, photo_order — даже при низкой оценке, совпадении "
                     "со стоп-листом или повторе (это отметь во flags). Короткий ответ из пяти полей здесь не подходит.")
    content: list = [
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": (
            f"# Кандидат\nИсточник: {source}\nURL: {url}\nЗаголовок: {title}\n"
            + (f"Опубликовано в источнике: {published}\n" if published else "")
            + f"Сегодня: {datetime.now(ZoneInfo(config.TZ_NAME)):%d.%m.%Y}\n\n"
            f"Текст:\n{text[:config.EVAL_TEXT_CHARS]}\n\n"
            + ("# Важно\n" + "\n".join(extra) + "\n\n" if extra else "")
            + f"# Превью фото ({len(images)} шт., индексы по порядку)")},
    ]
    for i, p in enumerate(images):
        content.append({"type": "text", "text": f"Фото {i}:"})
        content.append(_img(p))
    if config.PHOTO_CHECK and sheet and Path(sheet).exists():      # лист собирается заранее, в _prepare
        content.append({"type": "text", "text": SHEET_NOTE})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(Path(sheet).read_bytes()).decode()}})
    elif config.PHOTO_CHECK:
        content.append({"type": "text", "text": "Листа фрагментов нет."})
    return {"model": config.CLAUDE_MODEL, "max_tokens": 1500, "system": _system(EVAL_SYSTEM),
            "messages": [{"role": "user", "content": content}]}


async def evaluate(params: dict, background: bool = True) -> dict:
    """Оценка сразу, без пакета: пост по ссылке и срочный сбор при пустом запасе.
    У заказанного автором поста недостающее оформление (заголовок, теги, кредиты) дописывается отдельным шагом."""
    data = await _ask_json(params, background)
    if _is_forced(params) and _missing_branding(data):
        data = await _complete_branding(data, params, background)
    return data


# ---------- оформление заказанного поста ----------

BRAND_SYSTEM = f"""Ты оформляешь пост для Telegram-канала AHMAG (архитектура, искусство, фотография, архив, кино): заголовок, кредиты, теги и одну фразу для мини-поста. Автор канала сам заказал этот пост — оформи его полностью, оценка тут не нужна.

# Правила заголовка и кредитов (из профиля канала)
{PROFILE_FORMAT}

# Заголовок
headline_parts — части заголовка, как в примерах ниже: название; автор, бюро или режиссёр; город, страна, год (для фильма — страна и год). Неизвестное — null. Только факты из материала, ничего не выдумывай.
Так выглядят заголовки канала:
{HEADLINE_EXAMPLES}

# Теги
2–3, строчными, с префиксом ahmag: сначала рубрика (architecture, interiors, art, sculpture, photography, archive, cinema), затем страна по-английски одним словом. Для исторического материала добавь ahmagarchive.

# Фраза мини-поста
Одна простая фраза до 140 знаков: что это и что видно на фото, как сказал бы человек в переписке. Без «не X, а Y», без афоризмов и красивостей."""

BRAND_FORMAT = """{
  "category": "architecture|art|photography|archive|cinema",
  "format": "std|mini",
  "headline_parts": ["Название", "Автор/бюро/режиссёр или null", "Город, Страна, Год или null"],
  "mini_line": "одна простая фраза",
  "credits": {"pr": null, "pr_url": null, "ph": null, "ph_url": null, "via": null},
  "tags": ["ahmagcinema", "ahmagitaly"]
}"""


def _is_forced(params: dict) -> bool:
    content = params["messages"][0]["content"]
    if isinstance(content, str):
        return FORCED_NOTE in content
    return any(isinstance(b, dict) and b.get("type") == "text" and FORCED_NOTE in (b.get("text") or "")
               for b in content)


def _real_parts(data: dict) -> list:
    return [p for p in (data.get("headline_parts") or []) if p and str(p).strip().lower() != "null"]


def _empty(v) -> bool:
    if isinstance(v, dict):
        return not any(x and str(x).strip().lower() != "null" for x in v.values())
    return not v or str(v).strip().lower() == "null"


def _missing_branding(data: dict) -> bool:
    return not _real_parts(data) or not data.get("tags") or data.get("format") not in ("std", "mini")


async def _complete_branding(data: dict, params: dict, background: bool) -> dict:
    content = params["messages"][0]["content"]
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
    text = "\n\n".join(b["text"] for b in blocks
                       if b.get("type") == "text" and "# Кандидат" in (b.get("text") or ""))
    n_img = sum(1 for b in blocks if b.get("type") == "image") \
        - any(b.get("type") == "text" and b.get("text") == SHEET_NOTE for b in blocks)   # лист фрагментов — не фото
    log.info("Заказанный пост без оформления (score=%s) — дописываю заголовок и теги", data.get("score"))
    fill = await _call(f"{text or '—'}\n\nОформи этот пост. Верни ТОЛЬКО JSON:\n{BRAND_FORMAT}",
                       system=BRAND_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=1000, background=background)
    if not _real_parts(data) and _real_parts(fill):
        data["headline_parts"] = fill["headline_parts"]
    for k in ("mini_line", "credits", "tags", "category"):
        if _empty(data.get(k)) and not _empty(fill.get(k)):
            data[k] = fill[k]
    if data.get("format") not in ("std", "mini"):
        data["format"] = fill.get("format") if fill.get("format") in ("std", "mini") else (
            "std" if n_img >= config.MIN_PHOTOS_ARTICLE else "mini")
    if not data.get("photo_order") and n_img:
        cap = config.EVAL_PHOTOS if data["format"] == "std" else config.MINI_MAX_PHOTOS
        data["photo_order"] = list(range(min(n_img, cap)))
    data["flags"] = list(data.get("flags") or []) + ["заголовок и теги дописаны отдельным шагом — проверь"]
    return data


# ---------- пакеты (Message Batches): вдвое дешевле, ответ — в пределах суток ----------

async def batch_create(requests: list[dict], check_budget: bool = True) -> str:
    """check_budget=False — у пакета свой бюджет (архив сайта), дневной потолок к нему не относится."""
    if check_budget and not await budget_ok():
        raise BudgetExceeded()
    try:
        batch = await client.messages.batches.create(requests=requests)
    except Exception as exc:
        raise _map_error(exc) from exc
    return batch.id


async def batch_status(bid: str) -> tuple[bool, dict]:
    """→ (закончен ли, счётчики)"""
    try:
        b = await client.messages.batches.retrieve(bid)
    except Exception as exc:
        raise _map_error(exc) from exc
    counts = b.request_counts.model_dump() if b.request_counts else {}
    return b.processing_status == "ended", counts


async def batch_results(bid: str):
    """Асинхронно отдаёт (custom_id, data | None, ошибка | None) и записывает расход по тарифу пакетов."""
    try:
        results = await client.messages.batches.results(bid)
    except Exception as exc:
        raise _map_error(exc) from exc
    async for entry in results:
        res = entry.result
        if res.type != "succeeded":
            detail = ""
            if res.type == "errored":
                detail = str(getattr(getattr(res, "error", None), "error", "") or getattr(res, "error", ""))[:200]
            yield entry.custom_id, None, f"{res.type} {detail}".strip()
            continue
        msg = res.message
        await _record(msg.model or config.CLAUDE_MODEL, msg.usage, background=True, batch=True)
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            yield entry.custom_id, _parse_json(text), None
        except Exception as exc:
            yield entry.custom_id, None, f"не JSON: {exc}"[:200]


# ---------- 3. тексты (Opus) ----------

WRITER_SYSTEM = f"""Ты пишешь тексты для AHMAG: Telegram-канал и сайт об архитектуре, искусстве, фотографии и кино. Автор канала — архитектор по образованию. Пишет для людей со вкусом, без снобизма и без восторгов.

{voice.EDITORIAL}

{voice.RULES}

# Пост для канала (body)
- 1–2 абзаца, всего 350–700 знаков. Абзацы разделяй пустой строкой.
- Строй текст вокруг угла (он дан в задании; если его нет или он слабый, найди сильнее в материале и фактах). Одна линия, не пересказ всего.
- Первая фраза сразу о сути угла: факт, который цепляет. Без вступлений.
- Только факты из материала и из найденных фактов. Не выдумывай ни дат, ни цифр, ни имён, ни причин.
- Разметка: можно одно выделение <b> или <i>, лучше без них.
- Без заголовка, кредитов и хэштегов — их добавят отдельно.

# Новость
Если пост — новость: первая фраза — что произошло и когда (по материалу). Дальше — почему это интересно: история вещи или человека. Без пресс-релизных оборотов («было объявлено», «состоялось открытие»).

# Продолжение для сайта (site_more)
На сайте пост выходит полнее. site_more — 1–3 абзаца, 400–1200 знаков: что не влезло в канал — подробности истории, люди, контекст, что было потом. Тот же голос, те же правила, только проверенные факты. body не повторяй. Добавить нечего — null.

# Проверка перед ответом
- invisible — одной фразой: что в body нельзя увидеть на фото. Если ответить нечем, перепиши body.
- В body нет описания материалов, площадей и планировки, если в них нет истории.

# Фраза фото-поста
Одна простая фраза до 140 знаков: что это и что видно на фото, как сказал бы человек в переписке.

{voice.EXAMPLES}"""

RESEARCH_SYSTEM = """Ты — исследователь редакции AHMAG (архитектура, искусство, фотография, архив, кино). Перед тем как автор напишет короткий пост, ты ищешь историю вещи: то, чего не видно на фотографиях.

Что искать (в порядке ценности): судьбу (что случилось потом: снос, перестройка, заброшенность, спасение), людей (заказчик, архитектор, жилец, модель, странные детали биографии), конфликт (скандал, отказ, провал, суд), деталь с объяснением, контекст (на что отвечает, что было на этом месте), парадокс. Для новости — что именно произошло, когда, и предысторию.

Правила
- Ищи именно про этот объект, работу или человека. Не путай с однофамильцами и одноимёнными зданиями.
- Факты — только найденные в источниках, к каждому url. Пресс-релизные данные (площадь, материалы, программа) — не факты для нас, не выписывай их.
- Ничего не нашёл — так и скажи: verdict = weak, facts пустые. Это нормально.

Верни ТОЛЬКО JSON:
{"angle": "самый сильный угол одной фразой или null", "angle_type": "судьба|человек|конфликт|деталь|контекст|парадокс|новость|null", "facts": [{"text": "факт по-русски", "url": "https://..."}], "verdict": "strong|weak"}"""


async def research(data: dict, source_text: str) -> dict:
    """Поиск истории объекта перед текстом поста для канала (Sonnet + веб-поиск, до WRITER_SEARCHES запросов).
    Не получилось — пустой результат, пост пишется по материалу источника, как раньше."""
    if not config.WRITER_RESEARCH:
        return {}          # поиск выключен — пишем по материалу источника
    head = " // ".join(p for p in (data.get("headline_parts") or []) if p and str(p).lower() != "null")
    prompt = (f"# Пост\nЗаголовок: {head or data.get('headline', '')}\n"
              f"Тип: {'новость' if data.get('kind') == 'news' else 'объект'}\n"
              f"Угол, который увидел редактор: {data.get('angle') or '—'}\n\n"
              f"# Материал источника (начало)\n{(source_text or '')[:2500]}\n\n"
              "Найди историю этой вещи. Верни JSON.")
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max(1, config.WRITER_SEARCHES)}]
    try:
        out = await _call(prompt, system=RESEARCH_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=2500, tools=tools)
    except (NoCredits, ApiDown):
        raise
    except Exception as exc:
        log.warning("Поиск фактов не сработал (%r) — пишу по материалу источника", exc)
        return {"_failed": True}

    def clean(v):
        v = str(v or "").strip()
        return None if v.lower() in ("", "null", "none", "—", "-") else v
    facts = [f for f in (out.get("facts") or []) if isinstance(f, dict) and clean(f.get("text"))][:10]
    return {"angle": clean(out.get("angle")), "angle_type": clean(out.get("angle_type")), "facts": facts,
            "verdict": "strong" if out.get("verdict") == "strong" and facts else "weak"}


async def _voice_context() -> str:
    parts = []
    edits = await db.recent_edits(5)
    if edits:
        parts.append("# Так автор правит тексты бота — пиши сразу как во второй версии\n" + "\n\n".join(
            f"Было: {e['before'][:700]}\nСтало: {e['after'][:700]}" for e in edits))
    banned = await voice.banned()
    if banned:
        parts.append("# Автор запретил эти слова и обороты\n" + "; ".join(banned))
    from app import taste
    rules = await taste.active_text("write")
    if rules:
        parts.append(rules)
    return "\n\n".join(parts)


def _post_brief(data: dict) -> str:
    out = (f"Заголовок: {' // '.join(p for p in (data.get('headline_parts') or []) if p) or data.get('headline', '')}\n"
           f"Фраза фото-поста: {data.get('mini_line') or '—'}\n"
           f"Рубрика: {data.get('category') or '—'}")
    if data.get("kind") == "news":
        out += "\nЭто новость" + (f", дата события: {data['news_date']}" if data.get("news_date") else "")
    if data.get("angle"):
        out += f"\nУгол ({data.get('angle_type') or 'тип не указан'}): {data['angle']}"
    return out


def _facts_text(found: dict) -> str:
    if not found or not found.get("facts"):
        return ""
    lines = [f"- {f.get('text', '')} ({f.get('url', '')})" for f in found["facts"]]
    head = f"Угол по итогам поиска ({found.get('angle_type') or '—'}): {found['angle']}\n" if found.get("angle") else ""
    return "# Найденные факты (проверенные, с источниками)\n" + head + "\n".join(lines) + "\n\n"


async def write_body(data: dict, source_text: str, images: list, comment: str = "",
                     found: dict | None = None) -> tuple[str, list[str], dict]:
    """Текст поста для канала и продолжение для сайта.
    → (текст, штампы, которые не ушли после одной правки, {"site_more", "invisible"})"""
    ctx = await _voice_context()
    head = (f"# Пост\n{_post_brief(data)}\n\n# Материал источника\n{(source_text or '')[:5000]}\n\n"
            + _facts_text(found or {})
            + (ctx + "\n\n" if ctx else ""))
    if comment or data.get("body"):
        head += f"# Текущая версия текста\n{data.get('body') or '—'}\n\n"
    if comment:
        head += f"# Комментарий автора\n{comment}\n\n"
    head += ("Напиши текст поста для канала и продолжение для сайта. Верни ТОЛЬКО JSON: "
             '{"body": "...", "site_more": "... или null", "invisible": "что в body нельзя увидеть на фото"}')
    content: list = [{"type": "text", "text": head}]
    for p in images[:3]:
        content.append(_img(p))
    out = await _call(content, system=WRITER_SYSTEM, model=config.WRITER_MODEL, max_tokens=2500)
    body = str(out.get("body") or "").strip()
    banned = await voice.banned()
    hits = voice.check(body, banned, story=True)
    if hits and body:  # одна попытка убрать штампы и описательность
        fix = await _call(
            f"# Текст\n{body}\n\n" + _facts_text(found or {})
            + f"В тексте есть то, чего в канале быть не должно: {', '.join(hits)}. "
            "Перепиши без этого: вместо описания — история и факты, длину сохрани. "
            'Верни ТОЛЬКО JSON: {"body": "...", "invisible": "..."}',
            system=WRITER_SYSTEM, model=config.WRITER_MODEL, max_tokens=1500)
        body = str(fix.get("body") or body).strip()
        out["invisible"] = fix.get("invisible") or out.get("invisible")
        hits = voice.check(body, banned, story=True)
    more = out.get("site_more")
    more = str(more).strip() if more and str(more).strip().lower() not in ("null", "none", "—") else ""
    bad_more = voice.check(more, banned, dashes=False) if more else []
    if bad_more:           # продолжение со штампами на сайт не пускаем — там останется текст из канала
        log.info("Продолжение для сайта отброшено: %s", ", ".join(bad_more))
        more = ""
    inv = str(out.get("invisible") or "").strip()
    return body, hits, {"site_more": more, "invisible": "" if inv.lower() in ("null", "none", "—") else inv}


async def rewrite_mini(data: dict, source_text: str, comment: str, images: list) -> dict:
    ctx = await _voice_context()
    content: list = [{"type": "text", "text": (
        f"# Пост\n{_post_brief(data)}\n\n# Материал источника\n{(source_text or '')[:3000]}\n\n"
        + (ctx + "\n\n" if ctx else "")
        + f"# Комментарий автора\n{comment or 'Перепиши фразу проще и конкретнее.'}\n\n"
        "Перепиши фразу мини-поста; заголовок меняй, только если об этом просит комментарий. "
        "Верни ТОЛЬКО JSON: {\"mini_line\": \"...\", \"headline_parts\": [\"...\"]}")}]
    for p in images[:2]:
        content.append(_img(p))
    return await _call(content, system=WRITER_SYSTEM, model=config.WRITER_MODEL, max_tokens=600)


FIX_LINE_SYSTEM = """Ты правишь одну короткую фразу для Telegram-канала об архитектуре и искусстве. Фраза должна быть простой и человечной: что это за вещь и что видно на фото. Без противопоставлений «не X, а Y», без афоризмов, без слов «гармония», «баланс», «диалог», «выверенный», «сдержанный», «подчёркивает», «уникальный». Только факты из исходной фразы."""


async def fix_line(line: str, hits: list[str], headline: str) -> str:
    out = await _call(
        f"Заголовок поста: {headline}\nФраза: {line}\nУбери: {', '.join(hits)}.\n"
        "Верни ТОЛЬКО JSON: {\"line\": \"...\"}",
        system=FIX_LINE_SYSTEM, model=config.TRIAGE_MODEL, max_tokens=300, background=True)
    return str(out.get("line") or "").strip()


# ---------- #ahmagnotes ----------

NOTES_SYSTEM = f"""Ты — редактор Telegram-канала AHMAG и ведёшь рубрику #ahmagnotes: длинные авторские заметки об архитектуре, искусстве, фотографии и кино. Ниже профиль канала: вкус и темы.

{PROFILE}

{voice.RULES}

# Правила заметок
- Заметка — не энциклопедическая справка, а одна мысль, развёрнутая на материале: приём, линия влияния, судьба здания, взгляд автора.
- Только проверяемые факты. Даты, имена, цифры — лишь те, что подтверждены источниками. Сомневаешься — не пиши.
- Русский язык. Без подзаголовков и списков.
- Стоп-лист канала действует и здесь."""

NOTE_FORMAT = """{
  "headline_parts": ["Заголовок заметки", "подзаголовок или null"],
  "body": "текст заметки",
  "credits": {"pr": null, "pr_url": null, "ph": null, "ph_url": null, "via": null},
  "tags": ["ahmagnotes", "ahmagarchitecture", "ahmagjapan"],
  "photo_order": [0, 1, 2],
  "flags": ["что автору стоит перепроверить"]
}"""

BRIEF_FORMAT = """{
  "title": "рабочий заголовок",
  "thesis": "главная мысль заметки одной-двумя фразами",
  "plan": ["о чём первый абзац", "о чём второй", "..."],
  "facts": [{"text": "проверенный факт", "url": "откуда"}],
  "sources": [{"title": "название страницы", "url": "https://..."}],
  "page_urls": ["страницы с хорошими фотографиями по теме (до 3)"],
  "image_queries": ["запросы на английском для поиска фото в Wikimedia Commons (3–5)"]
}"""


async def notes_topics(avoid: list[str]) -> list[dict]:
    recent = [r["data"] for r in await db.published_posts(25)]
    heads = [json.loads(d).get("headline", "") for d in recent]
    content = (
        "Предложи 5 тем для заметки #ahmagnotes.\n\n"
        "# О чём канал писал в последнее время (темы могут расти отсюда, но не повторять посты)\n"
        + "\n".join(h for h in heads if h) + "\n\n"
        "# Заметки, которые уже выходили или предлагались (не повторять)\n"
        + "\n".join(NOTES_HEADLINES + avoid) + "\n\n"
        "Темы должны быть конкретными (не «японская архитектура», а один приём, один автор, одна линия, одно здание "
        "и его судьба), разными по рубрикам и такими, по которым есть достоверные открытые источники и хорошие фото.\n\n"
        'Верни ТОЛЬКО JSON: {"topics": [{"title": "короткое название темы", "angle": "одна фраза: в чём мысль заметки"}]}'
    )
    data = await _call(content, system=NOTES_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=1200)
    return [t for t in data.get("topics", []) if t.get("title")][:6]


async def notes_research(topic: str, angle: str = "") -> dict:
    """Сбор материала с веб-поиском. Если поиск недоступен — без него, с пометкой."""
    prompt = (
        f"Тема заметки #ahmagnotes: {topic}\n" + (f"Угол: {angle}\n" if angle else "") + "\n"
        "Собери материал: найди достоверные источники (музеи, архивы, профильные издания, монографии, "
        "интервью), выпиши факты с адресами страниц, сформулируй главную мысль и план на 4–7 абзацев. "
        "Факты — только те, что нашёл в источниках; к каждому — url.\n\n"
        f"Верни ТОЛЬКО JSON:\n{BRIEF_FORMAT}"
    )
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": config.NOTES_WEB_SEARCHES}]
    try:
        brief = await _call(prompt, system=NOTES_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=4000, tools=tools)
    except (NoCredits, ApiDown):
        raise
    except Exception as exc:
        log.warning("Веб-поиск не сработал (%r), собираю без него", exc)
        brief = await _call(prompt + "\n\nВеб-поиск недоступен: опирайся только на то, в чём уверен, url оставляй пустыми.",
                            system=NOTES_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=3000)
        brief["_no_search"] = True
    brief["topic"] = topic
    return brief


async def notes_replan(brief: dict, comment: str) -> dict:
    content = (
        f"# Собранный материал (JSON)\n{json.dumps(_public(brief), ensure_ascii=False)}\n\n"
        f"# Комментарий автора к плану\n{comment}\n\n"
        "Поправь thesis и plan по комментарию. Факты и источники не выдумывай: оставь те, что есть, "
        "лишние можно убрать. Верни полный JSON в том же формате."
    )
    new = await _call(content, system=NOTES_SYSTEM, model=config.CLAUDE_MODEL, max_tokens=3000)
    for k in ("topic", "_no_search"):
        if k in brief:
            new.setdefault(k, brief[k])
    for k in ("facts", "sources", "page_urls", "image_queries"):
        if not new.get(k):
            new[k] = brief.get(k, [])
    return new


def brief_text(brief: dict) -> str:
    facts = "\n".join(f"- {f.get('text', '')} ({f.get('url', '')})" for f in brief.get("facts", []))
    return (f"Тема: {brief.get('topic', '')}\nМысль: {brief.get('thesis', '')}\n"
            f"План:\n" + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(brief.get("plan", []))) +
            f"\n\nФакты:\n{facts}")


async def notes_write(brief: dict, images: list[Path]) -> dict:
    ctx = await _voice_context()
    content: list = [{"type": "text", "text": (
        f"# Материал\n{brief_text(brief)}\n\n" + (ctx + "\n\n" if ctx else "")
        + "Напиши заметку по плану. Только факты из материала. Объём body — 1500–2800 знаков, 4–7 абзацев, "
        "абзацы разделяй пустой строкой; допустимы <b> и <i> (до 2 выделений). Первый тег — ahmagnotes, "
        "затем рубрика и страна.\n"
        + (f"photo_order — индексы превью в порядке публикации (до 10), слабые и не по теме исключай.\n\n"
           f"# Превью фото ({len(images)} шт.)" if images else "Фото нет: photo_order — пустой список.")
        + f"\n\nВерни ТОЛЬКО JSON:\n{NOTE_FORMAT}")}]
    for i, p in enumerate(images):
        content.append({"type": "text", "text": f"Фото {i}:"})
        content.append(_img(p))
    return await _call(content, system=NOTES_SYSTEM, model=config.WRITER_MODEL, max_tokens=4000)


async def rewrite_notes(data: dict, source_text: str, comment: str) -> dict:
    ctx = await _voice_context()
    new = await _call(
        f"# Текущая версия заметки (JSON)\n{json.dumps(_public(data), ensure_ascii=False)}\n\n"
        f"# Материал\n{source_text[:8000]}\n\n" + (ctx + "\n\n" if ctx else "")
        + f"# Комментарий автора\n{comment or 'Перепиши живее и конкретнее.'}\n\n"
        "Перепиши headline_parts, body и tags по комментарию. Только факты из материала, объём 1500–2800 знаков. "
        f"photo_order оставь как есть. Верни полный JSON:\n{NOTE_FORMAT}",
        system=NOTES_SYSTEM, model=config.WRITER_MODEL, max_tokens=4000)
    new["photo_order"] = data.get("photo_order")
    return new
