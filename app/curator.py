"""Оценка кандидата, написание постов и заметок #ahmagnotes через Claude."""
import json
import logging
import random
import re
from pathlib import Path

import anthropic
from anthropic import AsyncAnthropic

from app import config, db, media

log = logging.getLogger(__name__)
client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

PROFILE = config.PROFILE_PATH.read_text(encoding="utf-8")
ARCHIVE = json.loads(config.ARCHIVE_PATH.read_text(encoding="utf-8"))
ARCHIVE_HEADLINES = [p["headline"] for p in ARCHIVE if p["headline"]]
# Эталон — поздний голос канала, без лонгридов
POOL = [p for p in ARCHIVE
        if not p["date"].endswith("March 2025") and "ahmagnotes" not in p["tags"]
        and 150 < len(p["text"]) < 900]
FIXED = [p for p in ARCHIVE if p["headline"].startswith((
    "Индийский институт управления", "Стеклянный чайный павильон",
    "Том и Джерри", "Дом с соломенной крышей"))]
NOTES_POOL = [p for p in ARCHIVE if "ahmagnotes" in p["tags"]]

SYSTEM = f"""Ты — редактор-куратор Telegram-канала AHMAG. Ниже профиль канала: вкус, формат и голос. Следуй ему строго.

{PROFILE}

# Твоя задача
Тебе дают материал-кандидат (текст источника и пронумерованные превью фото). Нужно:
1. Проверить стоп-лист и повтор (сравни со списком уже опубликованного).
2. Оценить соответствие вкусу канала по шкале 0–10 (раздел 6 профиля). Будь строгим: 7+ только для того, что автор канала опубликовал бы сам.
3. Если оценка ≥ {config.SCORE_THRESHOLD} — выбрать формат и написать пост.

# Форматы поста
- "std" — основной: заголовок, 1–2 абзаца, кредиты, теги. Для проектов, где есть мысль, которую стоит проговорить: материал, свет, приём, контекст.
- "mini" — короткий визуальный пост: заголовок, по желанию одна фраза и теги. Для материала, который говорит сам за себя: отдельный объект или работа художника, серия фотографий, кадр, музейный предмет, деталь. 1–{config.MINI_MAX_PHOTOS} фото.
Поле mini_line — одна фраза до 140 знаков, без пересказа заголовка. Заполняй его и для std: автор может сжать пост до мини. Если коротко сказать нечего — null, мини-пост выйдет без текста.

# Жёсткие правила текста
- Только факты из материала. Неизвестные год, город, фотограф — null. Не выдумывай.
- Русский язык. Тело std: 1–2 абзаца, суммарно 250–650 знаков. Итоговая подпись целиком должна уложиться в {config.CAPTION_LIMIT} знаков.
- Не переводи пресс-релиз. Выдели одну мысль: материал, свет, приём, контекст.
- Антитеза «не X, а Y» и финальная короткая фраза — характерные приёмы, но не в каждом посте; не делай текст шаблонным.
- Только длинное тире «—». Без эмодзи, без восклицаний, без обращений к читателю.
- В body допускаются только теги <b> и <i> (1–3 выделения максимум, можно без них). Абзацы разделяй пустой строкой.
- Теги: 2–3, строчными, с префиксом ahmag: сначала рубрика, затем страна по-английски одним словом. Для исторического материала добавь ahmagarchive.

# Выбор фото
photo_order — индексы превью в порядке публикации: для std от 4 до 10, для mini от 1 до {config.MINI_MAX_PHOTOS} (для музейных объектов допустимо 1–3). Последовательность: общий план → детали/материал → интерьер/свет. Исключай слабые, повторяющиеся, с текстом поверх, планы-чертежи без необходимости.

# Формат ответа
Верни ТОЛЬКО JSON без пояснений и без markdown:
{{
  "stoplist": false,
  "already_posted": false,
  "score": 0,
  "score_reason": "одна фраза по-русски, почему такая оценка",
  "category": "architecture|art|photography|archive|cinema|interiors|sculpture|exhibition|installation",
  "format": "std|mini",
  "headline_parts": ["Название", "Автор/бюро или null", "Город, Страна, Год или null"],
  "body": "текст поста (для mini можно пустую строку)",
  "mini_line": "одна фраза или null",
  "credits": {{"pr": null, "pr_url": null, "ph": null, "ph_url": null, "via": null}},
  "tags": ["ahmagarchitecture", "ahmagjapan"],
  "photo_order": [0, 1, 2],
  "flags": ["нет ph", "..."]
}}
Если score < {config.SCORE_THRESHOLD}, stoplist или already_posted — поля текста можно оставить пустыми.
"""

NOTES_SYSTEM = f"""Ты — редактор Telegram-канала AHMAG и ведёшь рубрику #ahmagnotes: длинные авторские заметки об архитектуре, искусстве, фотографии и кино. Ниже профиль канала: вкус и голос. Следуй ему строго.

{PROFILE}

# Правила заметок
- Заметка — не энциклопедическая справка, а одна мысль, развёрнутая на материале: приём, линия влияния, судьба здания, взгляд автора.
- Только проверяемые факты. Даты, имена, цифры — лишь те, что подтверждены источниками. Сомневаешься — не пиши.
- Русский язык. Только длинное тире «—». Без эмодзи, без восклицаний, без обращений к читателю, без подзаголовков и списков.
- Тот же голос, что в постах канала: сдержанно, точно, без восторгов.
- Стоп-лист канала действует и здесь.
"""


class BudgetExceeded(RuntimeError):
    """Достигнут дневной потолок фоновых вызовов."""


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
        return (f"Дневной лимит обращений к Claude исчерпан ({config.DAILY_API_CALLS_MAX}). "
                "Он сбросится в полночь, а поднять его можно переменной DAILY_API_CALLS_MAX.")
    if isinstance(exc, ApiDown):
        return "API Anthropic сейчас недоступен. Обычно это ненадолго — попробуйте через несколько минут."
    return f"Что-то пошло не так: {exc!r}"


def _examples() -> str:
    picks = FIXED + random.sample(POOL, min(5, len(POOL)))
    return "\n\n---\n\n".join(p["text"] + "\n" + " ".join("#" + t for t in p["tags"]) for p in picks)


def _notes_examples() -> str:
    picks = random.sample(NOTES_POOL, min(3, len(NOTES_POOL)))
    return "\n\n---\n\n".join(p["text"][:2500] for p in picks) or "(в архиве пока нет заметок)"


async def _learning_block() -> str:
    parts = []
    rej = await db.recent_rejections()
    if rej:
        lines = [f"- {json.loads(r['data']).get('headline', '?')} — причина: {r['reject_reason']}" for r in rej]
        parts.append("Недавно ОТКЛОНЕНО автором (учитывай при оценке):\n" + "\n".join(lines))
    pub = await db.published_posts(5, fmt="std")
    if pub:
        parts.append("Недавно ОДОБРЕНО автором (ориентир):\n" + "\n---\n".join(r["caption"] for r in pub))
    return "\n\n".join(parts)


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"Claude вернул не JSON: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _public(data: dict) -> dict:
    """Без служебных полей (_source_text и т.п.) — чтобы не гонять их в промпт."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


async def _call(content: list, max_tokens: int = 2000, system: str = SYSTEM,
                tools: list | None = None, background: bool = False) -> dict:
    """background=True — фоновый вызов, подчиняется дневному лимиту."""
    if background and await db.calls_today() >= config.DAILY_API_CALLS_MAX:
        raise BudgetExceeded("дневной лимит вызовов Claude исчерпан")
    messages = [{"role": "user", "content": content}]
    kwargs = {"tools": tools} if tools else {}
    text = ""
    for _ in range(4):  # веб-поиск может вернуть pause_turn — тогда продолжаем тот же ход
        try:
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL, max_tokens=max_tokens, system=system, messages=messages, **kwargs)
        except anthropic.APIStatusError as exc:
            detail = str(getattr(exc, "message", "") or exc).lower()
            if "credit balance" in detail or "billing" in detail:
                raise NoCredits() from exc
            if exc.status_code in (429, 500, 502, 503, 529):
                raise ApiDown() from exc
            raise
        except anthropic.APIConnectionError as exc:
            raise ApiDown() from exc
        try:
            await db.add_usage(resp.usage.input_tokens or 0, resp.usage.output_tokens or 0)
        except Exception:
            log.warning("usage не записан", exc_info=True)
        text += "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    return _parse_json(text)


async def ping() -> None:
    """Дешёвая проверка доступа к API — для /diag."""
    try:
        await client.messages.create(model=config.CLAUDE_MODEL, max_tokens=4,
                                     messages=[{"role": "user", "content": "ping"}])
    except anthropic.APIStatusError as exc:
        detail = str(getattr(exc, "message", "") or exc).lower()
        if "credit balance" in detail or "billing" in detail:
            raise NoCredits() from exc
        raise


# ---------- посты ----------

async def evaluate(source: str, url: str, title: str, text: str, images: list[Path],
                   allow_std: bool = True, forced: bool = False) -> dict:
    recent = await db.recent_headlines()
    extra = []
    if not allow_std:
        extra.append("Качественных фото мало: возможен только формат mini.")
    if forced:
        extra.append("Автор канала сам прислал эту ссылку. Оценку поставь честно, но пост напиши в любом случае, "
                     "даже при низкой оценке или совпадении со стоп-листом (отметь это во flags).")
    content: list = [{"type": "text", "text": (
        f"# Эталонные посты канала\n\n{_examples()}\n\n"
        f"# Уже опубликовано (не повторять)\n" + "\n".join(ARCHIVE_HEADLINES + recent) + "\n\n"
        f"{await _learning_block()}\n\n"
        f"# Кандидат\nИсточник: {source}\nURL: {url}\nЗаголовок: {title}\n\nТекст:\n{text}\n\n"
        + ("# Важно\n" + "\n".join(extra) + "\n\n" if extra else "")
        + f"# Превью фото ({len(images)} шт., индексы по порядку)"
    )}]
    for i, p in enumerate(images):
        content.append({"type": "text", "text": f"Фото {i}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": media.thumb_b64(p)}})
    return await _call(content, background=not forced)


async def rewrite(data: dict, source_text: str, comment: str, fmt: str = "std") -> dict:
    if fmt == "notes":
        return await _rewrite_notes(data, source_text, comment)
    what = ("headline_parts, mini_line, credits и tags (это мини-пост: mini_line — одна фраза до 140 знаков или null)"
            if fmt == "mini" else "headline_parts, body, mini_line, credits и tags")
    content = [{"type": "text", "text": (
        f"# Эталонные посты канала\n\n{_examples()}\n\n"
        f"# Текущая версия поста (JSON)\n{json.dumps(_public(data), ensure_ascii=False)}\n\n"
        f"# Исходный материал\n{source_text[:6000]}\n\n"
        f"# Комментарий автора\n{comment or 'Перепиши иначе, точнее и сильнее.'}\n\n"
        f"Перепиши {what} по комментарию. Оценку, format и photo_order оставь как есть. "
        "Верни полный JSON в том же формате."
    )}]
    new = await _call(content)
    new["score"], new["photo_order"] = data.get("score"), data.get("photo_order")
    return new


# ---------- #ahmagnotes ----------

NOTE_FORMAT = """{
  "headline_parts": ["Заголовок заметки", "подзаголовок или null"],
  "body": "текст заметки",
  "credits": {"pr": null, "pr_url": null, "ph": null, "ph_url": null, "via": null},
  "tags": ["ahmagnotes", "ahmagarchitecture", "ahmagjapan"],
  "photo_order": [0, 1, 2],
  "flags": ["что автору стоит перепроверить"]
}"""


async def notes_topics(avoid: list[str]) -> list[dict]:
    recent = [r["data"] for r in await db.published_posts(25)]
    heads = [json.loads(d).get("headline", "") for d in recent]
    content = [{"type": "text", "text": (
        "Предложи 5 тем для заметки #ahmagnotes.\n\n"
        "# О чём канал писал в последнее время (темы могут расти отсюда, но не повторять посты)\n"
        + "\n".join(h for h in heads if h) + "\n\n"
        "# Заметки, которые уже выходили или предлагались (не повторять)\n"
        + "\n".join([p["headline"] for p in NOTES_POOL if p["headline"]] + avoid) + "\n\n"
        "Темы должны быть конкретными (не «японская архитектура», а один приём, один автор, одна линия, одно здание "
        "и его судьба), разными по рубрикам и такими, по которым есть достоверные открытые источники и хорошие фото.\n\n"
        'Верни ТОЛЬКО JSON: {"topics": [{"title": "короткое название темы", "angle": "одна фраза: в чём мысль заметки"}]}'
    )}]
    data = await _call(content, max_tokens=1200, system=NOTES_SYSTEM)
    return [t for t in data.get("topics", []) if t.get("title")][:6]


BRIEF_FORMAT = """{
  "title": "рабочий заголовок",
  "thesis": "главная мысль заметки одной-двумя фразами",
  "plan": ["о чём первый абзац", "о чём второй", "..."],
  "facts": [{"text": "проверенный факт", "url": "откуда"}],
  "sources": [{"title": "название страницы", "url": "https://..."}],
  "page_urls": ["страницы с хорошими фотографиями по теме (до 3)"],
  "image_queries": ["запросы на английском для поиска фото в Wikimedia Commons (3–5)"]
}"""


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
        brief = await _call([{"type": "text", "text": prompt}], max_tokens=4000, system=NOTES_SYSTEM, tools=tools)
    except Exception as exc:
        log.warning("Веб-поиск не сработал (%r), собираю без него", exc)
        brief = await _call([{"type": "text", "text": prompt + (
            "\n\nВеб-поиск недоступен: опирайся только на то, в чём уверен, url оставляй пустыми.")}],
            max_tokens=3000, system=NOTES_SYSTEM)
        brief["_no_search"] = True
    brief["topic"] = topic
    return brief


async def notes_replan(brief: dict, comment: str) -> dict:
    content = [{"type": "text", "text": (
        f"# Собранный материал (JSON)\n{json.dumps(_public(brief), ensure_ascii=False)}\n\n"
        f"# Комментарий автора к плану\n{comment}\n\n"
        "Поправь thesis и plan по комментарию. Факты и источники не выдумывай: оставь те, что есть, "
        "лишние можно убрать. Верни полный JSON в том же формате."
    )}]
    new = await _call(content, max_tokens=3000, system=NOTES_SYSTEM)
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
    content: list = [{"type": "text", "text": (
        f"# Примеры заметок канала (ориентир по голосу и длине)\n\n{_notes_examples()}\n\n"
        f"# Материал\n{brief_text(brief)}\n\n"
        "Напиши заметку по плану. Только факты из материала. Объём body — 1500–2800 знаков, 4–7 абзацев, "
        "абзацы разделяй пустой строкой; допустимы <b> и <i> (до 3 выделений). Первый тег — ahmagnotes, "
        "затем рубрика и страна.\n"
        + (f"photo_order — индексы превью в порядке публикации (до 10), слабые и не по теме исключай.\n\n"
           f"# Превью фото ({len(images)} шт.)" if images else "Фото нет: photo_order — пустой список.")
        + f"\n\nВерни ТОЛЬКО JSON:\n{NOTE_FORMAT}"
    )}]
    for i, p in enumerate(images):
        content.append({"type": "text", "text": f"Фото {i}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": media.thumb_b64(p)}})
    return await _call(content, max_tokens=4000, system=NOTES_SYSTEM)


async def _rewrite_notes(data: dict, source_text: str, comment: str) -> dict:
    content = [{"type": "text", "text": (
        f"# Текущая версия заметки (JSON)\n{json.dumps(_public(data), ensure_ascii=False)}\n\n"
        f"# Материал\n{source_text[:8000]}\n\n"
        f"# Комментарий автора\n{comment or 'Перепиши иначе, точнее и сильнее.'}\n\n"
        "Перепиши headline_parts, body и tags по комментарию. Только факты из материала, объём 1500–2800 знаков. "
        f"photo_order оставь как есть. Верни полный JSON:\n{NOTE_FORMAT}"
    )}]
    new = await _call(content, max_tokens=4000, system=NOTES_SYSTEM)
    new["photo_order"] = data.get("photo_order")
    return new
