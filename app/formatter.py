"""Сборка подписи в формате AHMAG (Telegram HTML)."""
import html
import re

TAG_FIX = {
    "ahmagreece": "ahmaggreece",
    "ahmagchine": "ahmagchina",
    "ahmagkorea": "ahmagsouthkorea",
    "ahmagengland": "ahmaguk",
    "ahmagunitedkingdom": "ahmaguk",
    "ahmagunitedstates": "ahmagusa",
}


def _safe_body(text: str) -> str:
    """Экранирует всё, кроме <b>/<i>; чинит тире."""
    s = html.escape(text or "", quote=False)
    s = re.sub(r"&lt;(/?)(b|i)&gt;", r"<\1\2>", s)
    s = re.sub(r"(?<=\s)-(?=\s)", "—", s)  # дефис между пробелами → тире
    s = re.sub(r"\n{3,}", "\n\n", s.strip())
    return s


def _credit(label: str, name: str | None, url: str | None) -> str | None:
    if not name:
        return None
    name_e = html.escape(name, quote=False)
    if url and url.startswith("http"):
        return f'<i>{label}: </i><a href="{html.escape(url)}"><i>{name_e}</i></a>'
    return f"<i>{label}: {name_e}</i>"


def normalize_tags(tags: list[str]) -> list[str]:
    out = []
    for t in tags or []:
        t = re.sub(r"[^a-z]", "", t.lower().lstrip("#"))
        if not t:
            continue
        if not t.startswith("ahmag"):
            t = "ahmag" + t
        t = TAG_FIX.get(t, t)
        if t not in out:
            out.append(t)
    return out[:3]


def headline_parts(data: dict) -> list[str]:
    return [str(p).strip() for p in (data.get("headline_parts") or []) if p and str(p).strip().lower() != "null"]


def build_caption(data: dict) -> str:
    """Собирает подпись и заодно проставляет data['headline'] для истории."""
    parts = headline_parts(data)
    data["headline"] = " // ".join(parts)
    headline = " // ".join(html.escape(p, quote=False) for p in parts)
    blocks = [f"<b>{headline}</b>", _safe_body(data.get("body", ""))]

    c = data.get("credits") or {}
    credits = [x for x in (
        _credit("pr", c.get("pr"), c.get("pr_url")),
        _credit("ph", c.get("ph"), c.get("ph_url")),
        _credit("via", c.get("via"), None),
    ) if x]
    if credits:
        blocks.append("\n".join(credits))

    tags = normalize_tags(data.get("tags", []))
    if tags:
        blocks.append(" ".join("#" + t for t in tags))
    return "\n\n".join(b for b in blocks if b)


def visible_len(caption: str) -> int:
    """Длина подписи без HTML-разметки — так её считает Telegram."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", caption)))
