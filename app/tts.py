"""Озвучка рилсов «детали картины». Нужны не только звук, но и время каждого слова: по нему слова
появляются на экране одно за другим.

Голоса (выбор — кнопкой «🎙 Голос» на экране «🎬 Рилсы», там же можно послушать):
  Kokoro     — открытая модель, работает прямо на сервере бота: без аккаунта, оплаты и ключа. По умолчанию.
               Модель (~330 МБ) скачивается на диск Railway при первой озвучке и дальше лежит там.
  Edge       — голоса Microsoft через пакет edge-tts, бесплатно, но синтетичнее.
  ElevenLabs — если в Railway задан ELEVENLABS_API_KEY, он главнее выбора на экране.
REEL_TTS=0 — без озвучки: слова появляются в своём темпе.

Если выбранный голос не сработал, бот пробует Edge; не вышло и так — рилс собирается без звука,
а в карточке об этом написано."""
import asyncio
import base64
import logging
import os
import re
import subprocess
import wave
from pathlib import Path

import httpx

from app import config, db

log = logging.getLogger(__name__)

ENABLED = os.getenv("REEL_TTS", "1").strip().lower() not in ("0", "off", "false", "no")
EDGE_RATE = os.getenv("REEL_VOICE_RATE", "-4%")
SPEED = float(os.getenv("REEL_VOICE_SPEED", "0.95"))      # темп Kokoro
EL_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
EL_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
EL_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

KOKORO_DIR = config.DATA_DIR / "models" / "kokoro"
KOKORO_FILES = {
    # модель с длительностями звуков — по ним считается время каждого слова
    "model.onnx": "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/resolve/main/onnx/model.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}

# голос → (название на кнопке, описание)
VOICES = {
    "kokoro:af_heart": ("Heart", "американский женский, тёплый — лучший у Kokoro"),
    "kokoro:af_bella": ("Bella", "американский женский, чуть ярче"),
    "kokoro:bf_emma": ("Emma", "британский женский, ровный"),
    "kokoro:bm_george": ("George", "британский мужской, низкий"),
    "kokoro:bm_fable": ("Fable", "британский мужской, мягче"),
    "kokoro:am_michael": ("Michael", "американский мужской, спокойный"),
    "kokoro:am_fenrir": ("Fenrir", "американский мужской, плотный"),
    "edge:en-GB-RyanNeural": ("Ryan · Edge", "британский мужской, Microsoft"),
    "edge:en-US-AndrewNeural": ("Andrew · Edge", "американский мужской, Microsoft"),
}
DEFAULT = os.getenv("REEL_VOICE", "kokoro:af_heart")
if ":" not in DEFAULT:                       # старое значение REEL_VOICE=en-GB-RyanNeural
    DEFAULT = f"edge:{DEFAULT}"
SAMPLE_TEXT = ("At first, this looks like a tired clown taking a break from a party. "
               "But look through the doorway. In the next room, the royal court is dancing at a ball.")


async def voice() -> str:
    v = await db.get_setting("reel_voice", DEFAULT)
    return v if v in VOICES or v.startswith(("kokoro:", "edge:")) else DEFAULT


async def provider() -> str:
    if not ENABLED:
        return "off"
    return "elevenlabs" if EL_KEY else (await voice()).split(":")[0]


async def label() -> str:
    p = await provider()
    if p == "off":
        return "без озвучки"
    if p == "elevenlabs":
        return "голос ElevenLabs"
    v = await voice()
    return f"голос {VOICES.get(v, (v.split(':')[1], ''))[0]}"


def tokens(text: str) -> list[str]:
    """Слова так, как они будут на экране, — с пунктуацией."""
    return str(text).split()


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


def _fill(starts: list, total: float) -> list[float]:
    """Пропуски — между известными соседями; время не идёт назад."""
    known = [(i, s) for i, s in enumerate(starts) if s is not None]
    if not known:
        step = total / max(1, len(starts))
        return [i * step for i in range(len(starts))]
    out = []
    for i, s in enumerate(starts):
        if s is None:
            prev = max(((k, v) for k, v in known if k < i), default=(-1, 0.0))
            nxt = min(((k, v) for k, v in known if k > i), default=(len(starts), total))
            s = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / max(1, nxt[0] - prev[0])
        out.append(s)
    last, res = 0.0, []
    for s in out:
        last = max(last, float(s))
        res.append(last)
    return res


def align(text: str, spoken: list[tuple[float, str]], total: float) -> list[float]:
    """Время начала каждого экранного слова по словам, которые назвал синтезатор [(начало, слово)]."""
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
    return _fill(starts, total)


def duration(path: Path) -> float:
    from app import reelrender
    exe = reelrender.ffmpeg_exe()
    r = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


# ======================= Kokoro =======================

_kokoro = None
_kokoro_lock = asyncio.Lock()
_SKIP = set(" .,!?;:—–-…\"'“”‘’()[]ˈˌː")


async def _download() -> None:
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as client:
        for name, url in KOKORO_FILES.items():
            dest = KOKORO_DIR / name
            if dest.exists() and dest.stat().st_size > 1_000_000:
                continue
            part = dest.with_suffix(".part")
            log.info("Kokoro: скачиваю %s", name)
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with part.open("wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
            part.rename(dest)


async def _engine():
    global _kokoro
    async with _kokoro_lock:
        if _kokoro is None:
            await _download()

            def load():
                from kokoro_onnx import Kokoro
                k = Kokoro(str(KOKORO_DIR / "model.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
                k.has_timings = True     # у этой модели выход называется «durations», библиотека ждёт «duration»
                return k
            _kokoro = await asyncio.to_thread(load)
    return _kokoro


def _kokoro_words(k, text: str, name: str, lang: str, dest: Path) -> tuple[float, list[float]]:
    """Синтез + время каждого экранного слова по длительностям звуков."""
    import numpy as np
    audio, sr, spoken = k.create_timed(text, name, SPEED, lang)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    total = len(audio) / sr
    sounds = [x.start for x in spoken if x.phoneme not in _SKIP]
    toks = tokens(text)
    lens = [len([c for c in k.tokenizer.phonemize(t, lang) if c not in _SKIP]) for t in toks]
    starts: list[float | None] = []
    pos, whole = 0, sum(lens)
    for n in lens:
        # если слова и звуки разошлись по счёту, распределяем пропорционально
        i = pos if whole == len(sounds) else round(pos * len(sounds) / max(1, whole))
        starts.append(sounds[i] if n and i < len(sounds) else None)
        pos += n
    return total, _fill(starts, total)


async def _kokoro_speak(text: str, name: str, dest: Path) -> dict:
    k = await _engine()
    lang = "en-gb" if name.startswith("b") else "en-us"
    total, starts = await asyncio.to_thread(_kokoro_words, k, text, name, lang, dest)
    return {"audio": str(dest), "dur": total, "starts": starts}


# ======================= Edge и ElevenLabs =======================

async def _edge(text: str, name: str, dest: Path) -> list[tuple[float, str]]:
    import edge_tts
    com = edge_tts.Communicate(text, name, rate=EDGE_RATE, boundary="WordBoundary")
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


async def _speak_with(v: str, text: str, dest_base: Path) -> dict:
    engine, _, name = v.partition(":")
    dest_base.parent.mkdir(parents=True, exist_ok=True)
    if engine == "elevenlabs":
        dest = dest_base.with_suffix(".mp3")
        spoken = await _elevenlabs(text, dest)
    elif engine == "kokoro":
        return await _kokoro_speak(text, name, dest_base.with_suffix(".wav"))
    else:
        dest = dest_base.with_suffix(".mp3")
        spoken = await _edge(text, name, dest)
    dur = duration(dest) or (spoken[-1][0] + 0.6 if spoken else 0.0)
    return {"audio": str(dest), "dur": dur, "starts": align(text, spoken, dur)}


async def current_key() -> str:
    """Какой голос сейчас звучит — для кэша озвучки."""
    p = await provider()
    return f"elevenlabs:{EL_VOICE}" if p == "elevenlabs" else await voice()


async def speak(text: str, dest_base: Path) -> dict | None:
    """Озвучить фразу → {audio, dur, starts} (starts — начало каждого экранного слова, с) или None.
    dest_base — путь без расширения. Выбранный голос не сработал — пробуем Edge."""
    if await provider() == "off" or not str(text).strip():
        return None
    dest_base.parent.mkdir(parents=True, exist_ok=True)
    v = await current_key()
    try:
        return await _speak_with(v, text, dest_base)
    except Exception:
        if v.startswith("edge:"):
            raise
        log.warning("Голос %s не сработал, пробую Edge", v, exc_info=True)
        return await _speak_with("edge:en-GB-RyanNeural", text, dest_base)


async def sample(v: str) -> Path:
    """Образец голоса для кнопки «послушать» (mp3, кэшируется на диске)."""
    from app import reelrender
    folder = config.DATA_DIR / "reels" / "samples"
    mp3 = folder / f"{re.sub(r'[^A-Za-z0-9_-]', '_', v)}.mp3"
    if mp3.exists():
        return mp3
    res = await _speak_with(v, SAMPLE_TEXT, folder / f"{re.sub(r'[^A-Za-z0-9_-]', '_', v)}_raw")
    src = Path(res["audio"])
    if src.suffix != ".mp3":
        subprocess.run([reelrender.ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src), "-b:a", "128k",
                        str(mp3)], check=True, timeout=60)
        src.unlink(missing_ok=True)
    else:
        src.rename(mp3)
    return mp3
