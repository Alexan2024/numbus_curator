"""Озвучка рилсов «детали картины». Нужны не только звук, но и время каждого слова: по нему слова
появляются на экране одно за другим.

Два голоса:
  ElevenLabs — если в Railway задан ELEVENLABS_API_KEY. Звучит живее всего. Голос — ELEVENLABS_VOICE_ID
               (по умолчанию George, спокойный британский рассказчик), модель — ELEVENLABS_MODEL.
               Для публикации нужен платный тариф от Starter: на бесплатном нет коммерческих прав.
  Microsoft Edge — без ключа и бесплатно (пакет edge-tts). Голос — REEL_VOICE, по умолчанию en-GB-RyanNeural.
REEL_TTS=0 — без озвучки: слова появляются в своём темпе.

Если голос не получился, рилс собирается без звука, а в карточке об этом написано."""
import base64
import logging
import os
import re
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

ENABLED = os.getenv("REEL_TTS", "1").strip().lower() not in ("0", "off", "false", "no")
EDGE_VOICE = os.getenv("REEL_VOICE", "en-GB-RyanNeural")
EDGE_RATE = os.getenv("REEL_VOICE_RATE", "-4%")
EL_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
EL_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
EL_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")


def provider() -> str:
    if not ENABLED:
        return "off"
    return "elevenlabs" if EL_KEY else "edge"


def label() -> str:
    return {"off": "без озвучки", "elevenlabs": "голос ElevenLabs",
            "edge": f"голос {EDGE_VOICE.split('-')[-1].replace('Neural', '')} (Edge)"}[provider()]


def tokens(text: str) -> list[str]:
    """Слова так, как они будут на экране, — с пунктуацией."""
    return str(text).split()


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


def align(text: str, spoken: list[tuple[float, str]], total: float) -> list[float]:
    """Время начала каждого экранного слова по словам, которые назвал синтезатор [(начало, слово)].
    Что не сопоставилось — по соседям."""
    toks = tokens(text)
    starts: list[float | None] = [None] * len(toks)
    j = 0
    for t, w in spoken:
        nw = _norm(w)
        if not nw:
            continue
        for k in range(j, min(j + 4, len(toks))):
            nt = _norm(toks[k])
            if nt and (nt == nw or nt.startswith(nw) or nw.startswith(nt)):
                if starts[k] is None:
                    starts[k] = t
                j = k + 1
                break
    # пропуски — между известными соседями
    known = [(i, s) for i, s in enumerate(starts) if s is not None]
    if not known:
        step = total / max(1, len(toks))
        return [i * step for i in range(len(toks))]
    for i in range(len(toks)):
        if starts[i] is None:
            prev = max(((k, s) for k, s in known if k < i), default=(-1, 0.0))
            nxt = min(((k, s) for k, s in known if k > i), default=(len(toks), total))
            starts[i] = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / max(1, nxt[0] - prev[0])
    out, last = [], 0.0
    for s in starts:
        last = max(last, float(s))
        out.append(last)
    return out


def duration(path: Path) -> float:
    from app import reelrender
    exe = reelrender.ffmpeg_exe()
    r = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


async def _edge(text: str, dest: Path) -> list[tuple[float, str]]:
    import edge_tts
    com = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE, boundary="WordBoundary")
    audio, words = bytearray(), []
    async for ch in com.stream():
        if ch["type"] == "audio":
            audio += ch["data"]
        elif ch["type"] == "WordBoundary":
            words.append((ch["offset"] / 1e7, ch["text"]))
    if not audio:
        raise RuntimeError("Edge не вернул звук")
    dest.write_bytes(bytes(audio))
    return words


async def _elevenlabs(text: str, dest: Path) -> list[tuple[float, str]]:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE}/with-timestamps"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, params={"output_format": "mp3_44100_128"},
                              headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"},
                              json={"text": text, "model_id": EL_MODEL,
                                    "voice_settings": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.1}})
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs: ключ не подходит (ELEVENLABS_API_KEY)")
    if r.status_code in (402, 429) or "quota" in r.text.lower():
        raise RuntimeError("ElevenLabs: закончились кредиты тарифа")
    r.raise_for_status()
    data = r.json()
    dest.write_bytes(base64.b64decode(data["audio_base64"]))
    al = data.get("alignment") or data.get("normalized_alignment") or {}
    chars, starts = al.get("characters") or [], al.get("character_start_times_seconds") or []
    words, cur, t0 = [], "", None
    for ch, t in zip(chars, starts):
        if ch.isspace():
            if cur:
                words.append((t0, cur))
            cur, t0 = "", None
        else:
            if not cur:
                t0 = t
            cur += ch
    if cur:
        words.append((t0, cur))
    return words


async def speak(text: str, dest: Path) -> dict | None:
    """Озвучить фразу → {audio, dur, starts} (starts — начало каждого экранного слова, с) или None."""
    p = provider()
    if p == "off" or not str(text).strip():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    spoken = await (_elevenlabs if p == "elevenlabs" else _edge)(text, dest)
    dur = duration(dest) or (spoken[-1][0] + 0.6 if spoken else 0.0)
    return {"audio": str(dest), "dur": dur, "starts": align(text, spoken, dur)}
