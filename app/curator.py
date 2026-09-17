"""Оценка кандидата и написание поста через Claude."""
import json
import logging
import random
import re
from pathlib import Path

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

SYSTEM = f"""Ты — редактор-куратор Telegram-канала AHMAG. Ниже профиль канала: вкус, формат и голос. Следуй ему строго.

{PROFILE}

# Твоя задача
Тебе дают материал-кандидат (текст источника и пронумерованные превью фото). Нужно:
1. Проверить стоп-лист и повтор (сравни со списком уже опубликованного).
2. Оценить соответствие вкусу канала по шкале 0–10 (раздел 6 профиля). Будь строгим: 7+ только для того, что автор канала опубликовал бы сам.
3. Если оценка ≥ {config.SCORE_THRESHOLD} — написать пост.

# Жёсткие правила текста
- Только факты из материала. Неизвестные год, город, фотограф — null. Не выдумывай.
- Русский язык. Тело: 1–2 абзаца, суммарно 250–650 знаков. Итоговая подпись целиком должна уложиться в {config.CAPTION_LIMIT} знаков.
- Не переводи пресс-релиз. Выдели одну мысль: материал, свет, приём, контекст.
- Антитеза «не X, а Y» и финальная короткая фраза — характерные приёмы, но не в каждом посте; не делай текст шаблонным.
- Только длинное тире «—». Без эмодзи, без восклицаний, без обращений к читателю.
- В body допускаются только теги <b> и <i> (1–3 выделения максимум, можно без них). Абзацы разделяй пустой строкой.
- Теги: 2–3, строчными, с префиксом ahmag: сначала рубрика, затем страна по-английски одним словом. Для исторического материала добавь ahmagarchive.

# Выбор фото
photo_order — индексы превью в порядке публикации, от 4 до 10 (для музейных объектов допустимо 1–3). Последовательность: общий план → детали/материал → интерьер/свет. Исключай слабые, повторяющиеся, с текстом поверх, планы-чертежи без необходимости.

# Формат ответа
Верни ТОЛЬКО JSON без пояснений и без markdown:
{{
  "stoplist": false,
  "already_posted": false,
  "score": 0,
  "score_reason": "одна фраза по-русски, почему такая оценка",
  "category": "architecture|art|photography|archive|cinema|interiors|sculpture|exhibition|installation",
  "headline_parts": ["Название", "Автор/бюро или null", "Город, Страна, Год или null"],
  "body": "текст поста",
  "credits": {{"pr": null, "pr_url": null, "ph": null, "ph_url": null, "via": null}},
  "tags": ["ahmagarchitecture", "ahmagjapan"],
  "photo_order": [0, 1, 2],
  "flags": ["нет ph", "..."]
}}
Если score < {config.SCORE_THRESHOLD}, stoplist или already_posted — поля текста можно оставить пустыми.
"""


def _examples() -> str:
    picks = FIXED + random.sample(POOL, min(5, len(POOL)))
    return "\n\n---\n\n".join(p["text"] + "\n" + " ".join("#" + t for t in p["tags"]) for p in picks)


async def _learning_block() -> str:
    parts = []
    rej = await db.recent_rejections()
    if rej:
        lines = [f"- {json.loads(r['data']).get('headline', '?')} — причина: {r['reject_reason']}" for r in rej]
        parts.append("Недавно ОТКЛОНЕНО автором (учитывай при оценке):\n" + "\n".join(lines))
    pub = await db.published_posts(5)
    if pub:
        parts.append("Недавно ОДОБРЕНО автором (ориентир):\n" + "\n---\n".join(r["caption"] for r in pub))
    return "\n\n".join(parts)


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


async def _call(content: list, max_tokens: int = 2000) -> dict:
    resp = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(text)


async def evaluate(source: str, url: str, title: str, text: str, images: list[Path]) -> dict:
    recent = await db.recent_headlines()
    content: list = [{"type": "text", "text": (
        f"# Эталонные посты канала\n\n{_examples()}\n\n"
        f"# Уже опубликовано (не повторять)\n" + "\n".join(ARCHIVE_HEADLINES + recent) + "\n\n"
        f"{await _learning_block()}\n\n"
        f"# Кандидат\nИсточник: {source}\nURL: {url}\nЗаголовок: {title}\n\nТекст:\n{text}\n\n"
        f"# Превью фото ({len(images)} шт., индексы по порядку)"
    )}]
    for i, p in enumerate(images):
        content.append({"type": "text", "text": f"Фото {i}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": media.thumb_b64(p)}})
    return await _call(content)


async def rewrite(data: dict, source_text: str, comment: str) -> dict:
    content = [{"type": "text", "text": (
        f"# Эталонные посты канала\n\n{_examples()}\n\n"
        f"# Текущая версия поста (JSON)\n{json.dumps(data, ensure_ascii=False)}\n\n"
        f"# Исходный материал\n{source_text[:6000]}\n\n"
        f"# Комментарий автора\n{comment or 'Перепиши иначе, точнее и сильнее.'}\n\n"
        "Перепиши headline_parts, body, credits и tags по комментарию. Оценку и photo_order оставь как есть. "
        "Верни полный JSON в том же формате."
    )}]
    new = await _call(content)
    new["score"], new["photo_order"] = data.get("score"), data.get("photo_order")
    return new
