"""Озвучка рилсов. Нужны не только звук, но и время каждого слова: по нему слова появляются на экране
одно за другим, и камера приезжает к детали на нужном слове.

Голоса (выбор — кнопкой «🎙 Голос» на экране «🎬 Рилсы», там же можно послушать):
  ElevenLabs — если в Railway задан ELEVENLABS_API_KEY (напрямую) или FAL_KEY (та же модель Eleven v3 через
               fal.ai — если сайт ElevenLabs недоступен). Весь рассказ читается одним дублем, а время каждого
               слова ElevenLabs присылает сам — без распознавания речи. К фразам добавляются теги интонации v3
               ([softly], [curious]…) по указаниям Claude. С ключом это голос по умолчанию.
               ELEVENLABS_VOICE_ID — свой голос из библиотеки (по умолчанию George), ELEVENLABS_MODEL — модель
               (eleven_v3; если модель не отдаёт время слов, бот сам берёт eleven_multilingual_v2).
  OpenAI     — если задан OPENAI_API_KEY. Один дубль с указаниями, как читать. Время слов — распознаванием (whisper-1).
  Kokoro     — открытая модель прямо на сервере бота: без аккаунта и ключа. Запасной голос.
  Edge       — голоса Microsoft через edge-tts, бесплатно, но синтетичнее.
REEL_TTS=0 — без озвучки: слова появляются в своём темпе.

Порядок запасных: ElevenLabs напрямую → ElevenLabs через fal → OpenAI → Kokoro → Edge. Какой голос
в итоге прочитал рилс и почему — в карточке."""
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
EL_KEY = os.getenv("ELEVENLABS_API_KEY", os.getenv("XI_API_KEY", "")).strip()
FAL_KEY = os.getenv("FAL_KEY", "").strip()
EL_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb").strip()     # George
EL_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_v3").strip()
EL_FALLBACK_MODEL = "eleven_multilingual_v2"
FAL_URL = "https://fal.run/fal-ai/elevenlabs/tts/eleven-v3"
OA_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OA_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OA_STT = os.getenv("OPENAI_STT_MODEL", "whisper-1")
NARRATOR = """Identity: a real person telling a friend about a painting they can't stop thinking about. Warm, curious, alive. Not an announcer, not an audiobook reader, not a movie-trailer voice.

Voice affect: natural and conversational, close to the microphone; the pitch moves the way it does when someone is genuinely telling a story.
Tone: intrigued at the start, drawn in by each reveal, a quiet smile where there is irony, and real weight at the emotional turn.
Pacing: lively and varied — quicker through setup, slower where it matters. Never even and metronomic.
Pauses: a short beat after the opening line; a real pause before the emotional turn; line breaks in the text are breaths.
Emphasis: lean on the one word in each sentence that carries the surprise or the contrast."""

KOKORO_DIR = config.DATA_DIR / "models" / "kokoro"
KOKORO_FILES = {
    # модель с длительностями звуков — по ним считается время каждого слова
    "model.onnx": "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/resolve/main/onnx/model.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}

# голоса ElevenLabs из общей библиотеки: id → (имя, описание). Имя нужно для fal.ai
EL_VOICES = {
    "JBFqnCBsd6RMkjVDRZzb": ("George", "британский мужской, тёплый рассказчик"),
    "nPczCjzI2devNBz1zQrb": ("Brian", "мужской, низкий, спокойный"),
    "onwK4e9ZLuTAKqWW03F9": ("Daniel", "британский мужской, ровный"),
    "Xb7hH8MSUJpSbSDYk0k2": ("Alice", "британский женский, ясный"),
    "XrExE9yKIg1WjnnlVkGX": ("Matilda", "женский, тёплый"),
}
if EL_VOICE not in EL_VOICES:
    EL_VOICES = {EL_VOICE: ("Свой", "ElevenLabs · голос из ELEVENLABS_VOICE_ID"), **EL_VOICES}

# голос → (название на кнопке, описание)
VOICES = {
    **{f"elevenlabs:{k}": (n, f"ElevenLabs · {d}") for k, (n, d) in EL_VOICES.items()},
    "openai:cedar": ("Cedar", "OpenAI · мужской, самый живой"),
    "openai:marin": ("Marin", "OpenAI · женский, самый живой"),
    "openai:onyx": ("Onyx", "OpenAI · мужской, низкий, весомый"),
    "openai:ash": ("Ash", "OpenAI · мужской, тёплый"),
    "openai:ballad": ("Ballad", "OpenAI · мужской, мягкий, выразительный"),
    "openai:verse": ("Verse", "OpenAI · мужской, живой"),
    "openai:sage": ("Sage", "OpenAI · женский, спокойный"),
    "openai:coral": ("Coral", "OpenAI · женский, тёплый"),
    "openai:shimmer": ("Shimmer", "OpenAI · женский, мягкий"),
    "kokoro:af_heart": ("Heart", "Kokoro · женский, тёплый"),
    "kokoro:bf_emma": ("Emma", "Kokoro · британский женский"),
    "kokoro:bm_george": ("George", "Kokoro · британский мужской"),
    "edge:en-GB-RyanNeural": ("Ryan", "Edge · британский мужской"),
}


def el_ready() -> bool:
    return bool(EL_KEY or FAL_KEY)


def available() -> list[str]:
    """Голоса для экрана выбора: OpenAI и ElevenLabs — только когда задан их ключ."""
    return [k for k in VOICES if (OA_KEY or not k.startswith("openai:")) and (el_ready() or not k.startswith("elevenlabs:"))]


DEFAULT = os.getenv("REEL_VOICE", f"elevenlabs:{EL_VOICE}" if el_ready() else "openai:cedar" if OA_KEY else "kokoro:af_heart")
if ":" not in DEFAULT:                       # старое значение REEL_VOICE=en-GB-RyanNeural
    DEFAULT = f"edge:{DEFAULT}"
SAMPLE_TEXT = ("At first, this looks like a tired clown taking a break from a party. "
               "But look through the doorway. In the next room, the royal court is dancing at a ball.")


async def voice() -> str:
    if OA_KEY and not await db.get_setting("reel_voice_openai_v2"):
        # 4.9: Onyx, выбранный по умолчанию в 4.8, звучал плоско — один раз переводим на Cedar
        await db.set_setting("reel_voice_openai_v2", True)
        if await db.get_setting("reel_voice") in (None, "openai:onyx"):
            await db.set_setting("reel_voice", DEFAULT if DEFAULT.startswith("openai:") else "openai:cedar")
            await db.set_setting("reel_voice_openai_on", True)
    if OA_KEY and not await db.get_setting("reel_voice_openai_on"):
        # ключ OpenAI появился — один раз переключаемся на его голос; дальше решает выбор на экране
        await db.set_setting("reel_voice_openai_on", True)
        if not str(await db.get_setting("reel_voice", "") or "").startswith("openai:"):
            await db.set_setting("reel_voice", DEFAULT if DEFAULT.startswith("openai:") else "openai:cedar")
    if el_ready() and not await db.get_setting("reel_voice_el_on"):
        # ключ ElevenLabs (или fal) появился — один раз переключаемся на его голос; дальше решает выбор на экране
        await db.set_setting("reel_voice_el_on", True)
        await db.set_setting("reel_voice", f"elevenlabs:{EL_VOICE}")
    v = await db.get_setting("reel_voice", None)
    if not v or (v.startswith("openai:") and not OA_KEY) or (v.startswith("elevenlabs:") and not el_ready()):
        v = DEFAULT if DEFAULT in available() else ("openai:cedar" if OA_KEY else "kokoro:af_heart")
    return v if v.startswith(("kokoro:", "edge:", "openai:", "elevenlabs:")) else "kokoro:af_heart"


async def provider() -> str:
    if not ENABLED:
        return "off"
    return (await voice()).split(":")[0]


async def label() -> str:
    p = await provider()
    if p == "off":
        return "без озвучки"
    v = await voice()
    tail = {"openai": " (OpenAI)", "elevenlabs": " (ElevenLabs)"}.get(p, "")
    return f"голос {VOICES.get(v, (v.split(':')[1], ''))[0]}{tail}"


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


def align_q(text: str, spoken: list[tuple[float, str]], total: float) -> tuple[list[float], float]:
    """Время начала каждого экранного слова по словам, которые услышало распознавание [(начало, слово)].
    Сопоставление — по всей последовательности сразу (difflib), поэтому одно расхождение (число словами,
    слитное слово, пропуск) не сбивает всё остальное. → (время слов, доля сопоставленных слов)."""
    import difflib
    toks = tokens(text)
    tn = [_norm(t) for t in toks]
    sp = [(float(t), _norm(w)) for t, w in spoken if _norm(w)]
    starts: list[float | None] = [None] * len(toks)
    sm = difflib.SequenceMatcher(None, tn, [w for _, w in sp], autojunk=False)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            starts[a + k] = sp[b + k][0]
    # время не может идти назад: выбрасываем совпадения, которые нарушают порядок
    last = -1.0
    for k, v in enumerate(starts):
        if v is not None:
            if v < last:
                starts[k] = None
            else:
                last = v
    got = sum(v is not None for v in starts)
    return _fill(starts, total), got / max(1, len(toks))


def align(text: str, spoken: list[tuple[float, str]], total: float) -> list[float]:
    return align_q(text, spoken, total)[0]


def _sane(parts_words: list[int], starts: list[float], total: float) -> bool:
    """Части рассказа не слиплись: у каждой есть время хотя бы на 0,12 с на слово, и ни одна не тянется
    дольше, чем 1 с на слово."""
    pos, spans = 0, []
    for n in parts_words:
        s0 = starts[pos] if n else None
        pos += n
        s1 = starts[pos] if pos < len(starts) else total
        if s0 is not None:
            spans.append((n, s1 - s0, pos >= len(starts)))
    # у последней части верхней границы нет: после неё может быть тишина до конца дорожки
    return all(0.12 * n <= d and (last or d <= 1.0 * n + 1.5) for n, d, last in spans if n)


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


def _chars_to_words(chars: list, starts: list) -> list[tuple[float, str]]:
    """Время символов ElevenLabs → [(начало, слово)]. Теги интонации [..] пропускаются."""
    words, cur, t0, tag = [], "", None, False
    for ch, t in zip(chars, starts):
        if ch == "[":
            tag = True
            continue
        if tag:
            tag = ch != "]"
            continue
        if ch.isspace():
            if cur:
                words.append((float(t0), cur))
            cur, t0 = "", None
        else:
            if not cur:
                t0 = t
            cur += ch
    if cur:
        words.append((float(t0), cur))
    return words


class ElError(RuntimeError):
    """Понятная причина, почему ElevenLabs не прочитал."""


async def _el_direct(text: str, voice_id: str, dest: Path, model: str) -> list[tuple[float, str]]:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    vs = {"stability": 0.5, "similarity_boost": 0.75} if model == "eleven_v3" else \
        {"stability": 0.45, "similarity_boost": 0.75, "style": 0.15, "use_speaker_boost": True}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=240)) as client:
        r = await client.post(url, params={"output_format": "mp3_44100_128"},
                              headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"},
                              json={"text": text, "model_id": model, "voice_settings": vs})
    body = r.text[:300].lower()
    if r.status_code == 401:
        raise ElError("ElevenLabs: ключ не подходит (ELEVENLABS_API_KEY)")
    if r.status_code in (402, 429) or "quota" in body:
        raise ElError("ElevenLabs: закончились кредиты тарифа")
    if "unusual" in body or "free tier" in body:
        raise ElError("ElevenLabs не пускает бесплатный тариф с сервера — нужен платный (Starter) или FAL_KEY")
    if r.status_code in (400, 422) and "model" in body:
        raise ElError(f"model:{r.text[:200]}")
    if r.status_code >= 400:
        raise ElError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    data = r.json()
    dest.write_bytes(base64.b64decode(data["audio_base64"]))
    al = data.get("alignment") or data.get("normalized_alignment") or {}
    return _chars_to_words(al.get("characters") or [], al.get("character_start_times_seconds") or [])


def _fal_words(ts) -> list[tuple[float, str]]:
    """Время слов из ответа fal.ai — формат бывает разный: список слов или символы, как у ElevenLabs."""
    if isinstance(ts, dict):
        if ts.get("characters"):
            return _chars_to_words(ts["characters"], ts.get("character_start_times_seconds") or [])
        ts = ts.get("words") or ts.get("chunks") or []
    out = []
    for w in ts or []:
        if not isinstance(w, dict):
            continue
        word = w.get("text") or w.get("word") or ""
        t = w.get("start", w.get("start_time", w.get("timestamp")))
        if isinstance(t, (list, tuple)):
            t = t[0] if t else None
        if word.strip() and t is not None and not re.fullmatch(r"\[.*\]", word.strip()):
            out.append((float(t), word.strip()))
    return out


async def _el_fal(text: str, voice_id: str, dest: Path) -> list[tuple[float, str]]:
    """Та же модель Eleven v3 через fal.ai — если к сайту ElevenLabs не достучаться."""
    name = EL_VOICES.get(voice_id, (voice_id,))[0]
    name = voice_id if name == "Свой" else name
    head = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), follow_redirects=True) as client:
        r = await client.post(FAL_URL, headers=head, json={"text": text, "voice": name, "stability": 0.5,
                                                          "timestamps": True, "apply_text_normalization": "auto"})
        if r.status_code in (401, 403):
            raise ElError("fal.ai: ключ не подходит (FAL_KEY)")
        if r.status_code == 402 or "balance" in r.text.lower():
            raise ElError("fal.ai: на счёте закончились деньги")
        if r.status_code >= 400:
            raise ElError(f"fal.ai {r.status_code}: {r.text[:200]}")
        data = r.json()
        url = (data.get("audio") or {}).get("url")
        if not url:
            raise ElError("fal.ai не вернул звук")
        a = await client.get(url)
        a.raise_for_status()
        dest.write_bytes(a.content)
    words = _fal_words(data.get("timestamps"))
    if not words and OA_KEY:
        words = await _transcribe(dest)
    return words


EL_TAGS = [(("wry", "smile", "irony", "ironic", "amused"), "[wryly]"), (("soft", "gentle", "tender", "quiet"), "[softly]"),
           (("curious", "intrigu", "wonder"), "[curious]"), (("heavy", "weight", "slow", "grave", "sombre", "somber"),
                                                           "[thoughtful]"), (("tense", "urgent", "quicker"), "[tense]")]


def el_tag(how: str | None) -> str:
    """Указание Claude («slower, softer, let it hang») → тег интонации Eleven v3 или пусто."""
    low = (how or "").lower()
    for keys, tag in EL_TAGS:
        if any(k in low for k in keys):
            return tag
    return ""


async def _elevenlabs(text: str, dest: Path, voice_id: str | None = None, tagged: str | None = None) -> tuple[list[tuple[float, str]], str]:
    """Озвучка ElevenLabs → ([(начало, слово)], чем прочитано). tagged — тот же текст с тегами v3.
    Порядок: напрямую (v3, при отказе модели — multilingual v2 без тегов), затем fal.ai."""
    voice_id = voice_id or EL_VOICE
    errors = []
    if EL_KEY:
        for model in dict.fromkeys([EL_MODEL, EL_FALLBACK_MODEL]):
            try:
                src = tagged if (tagged and model == "eleven_v3") else text
                return await _el_direct(src, voice_id, dest, model), f"ElevenLabs {model}"
            except ElError as exc:
                if str(exc).startswith("model:"):
                    errors.append(f"модель {model} не подошла")
                    continue
                errors.append(str(exc))
                break
            except httpx.HTTPError as exc:
                errors.append(f"ElevenLabs недоступен ({type(exc).__name__})")
                break
    if FAL_KEY:
        try:
            return await _el_fal(tagged or text, voice_id, dest), "Eleven v3 через fal.ai"
        except (ElError, httpx.HTTPError) as exc:
            errors.append(str(exc) or type(exc).__name__)
    raise ElError("; ".join(errors) or "ElevenLabs: нет ключа (ELEVENLABS_API_KEY или FAL_KEY)")


# ======================= OpenAI =======================

async def _openai(text: str, name: str, dest: Path, how: str | None) -> tuple[float, list[tuple[float, str]]]:
    """Озвучка с указанием, как читать, + время слов распознаванием речи."""
    head = {"Authorization": f"Bearer {OA_KEY}"}
    body = {"model": OA_MODEL, "voice": name, "input": text, "response_format": "mp3",
            "instructions": (how if how and how.startswith("Identity:") else NARRATOR + (f"\nThis line: {how}" if how else ""))}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=180)) as client:
        r = await client.post("https://api.openai.com/v1/audio/speech", headers=head, json=body)
        if r.status_code == 401:
            raise RuntimeError("OpenAI: ключ не подходит (OPENAI_API_KEY)")
        if r.status_code == 429 and "quota" in r.text.lower():
            raise RuntimeError("OpenAI: на счёте API закончились деньги")
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI TTS {r.status_code}: {r.text[:200]}")
        dest.write_bytes(r.content)
    return duration(dest), await _transcribe(dest)


async def _transcribe(path: Path) -> list[tuple[float, str]]:
    """Время слов распознаванием речи (OpenAI whisper-1). Не вышло — пустой список (тогда — по длине слов)."""
    if not OA_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=180)) as client:
            t = await client.post("https://api.openai.com/v1/audio/transcriptions",
                                  headers={"Authorization": f"Bearer {OA_KEY}"},
                                  data={"model": OA_STT, "response_format": "verbose_json", "language": "en",
                                        "timestamp_granularities[]": "word"},
                                  files={"file": (path.name, path.read_bytes(), "audio/mpeg")})
            t.raise_for_status()
            return [(float(w["start"]), str(w["word"])) for w in t.json().get("words") or []]
    except Exception:
        log.warning("Распознавание: время слов не получено, распределяю по длине", exc_info=True)
        return []


def is_one_take(v: str) -> bool:
    """OpenAI и ElevenLabs читают весь рассказ одним дублем: интонация течёт от фразы к фразе."""
    return v.startswith(("openai:", "elevenlabs:"))


def direction(parts: list[tuple[str, str, str]], extra: str = "") -> str:
    """Указания для одного дубля: общая манера + как читать каждую часть. parts — [(вид, текст, как)]."""
    names = {"hook": "opening hook", "context": "context", "reveal": "reveal", "climax": "emotional turn",
             "final": "closing line"}
    lines = [NARRATOR]
    if extra:
        lines.append(f"This story: {extra}")
    lines.append("Part by part:")
    for n, (kind, text, how) in enumerate(parts, 1):
        lines.append(f"{n}. {names.get(kind, kind)} (\"{' '.join(text.split()[:6])}…\"): {how or 'natural'}")
    return "\n".join(lines)[:3800]


async def speak_story(parts: list[tuple[str, str, str]], dest_base: Path, extra: str = "", v: str | None = None) -> dict:
    """Весь рассказ одним дублем (OpenAI или ElevenLabs) → {audio, dur, beats: [{start, starts}], by} —
    время каждой части и слова."""
    v = v or await current_key()
    engine, name = v.split(":", 1)
    text = "\n\n".join(t for _, t, _ in parts)
    dest = dest_base.with_suffix(".mp3")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if engine == "elevenlabs":
        tagged = "\n\n".join((el_tag(how) + " " if el_tag(how) else "") + t for _, t, how in parts)
        spoken, by = await _elevenlabs(text, dest, name, tagged)
        dur = duration(dest)
    else:
        dur, spoken = await _openai(text, name, dest, direction(parts, extra))
        by = "OpenAI"
    starts, q = align_q(text, spoken, dur) if spoken else (None, 0.0)
    counts = [len(tokens(t)) for _, t, _ in parts]
    if starts is None or q < 0.6 or not _sane(counts, starts, dur):
        log.warning("Время слов: распознавание не сошлось с текстом (совпало %.0f%%), распределяю по длине", q * 100)
        starts = _by_length(text, dur)
        approx = True
    else:
        approx = False
    beats, pos = [], 0
    for _, t, _ in parts:
        n = len(tokens(t))
        beats.append({"start": starts[pos] if n else (beats[-1]["start"] if beats else 0.0),
                      "starts": starts[pos:pos + n]})
        pos += n
    return {"audio": str(dest), "dur": dur, "beats": beats, "one_take": True, "approx": approx, "by": by}


def _by_length(text: str, total: float) -> list[float]:
    """Запасной вариант без времени слов: по длине слов, с паузами на знаках препинания."""
    toks = tokens(text)
    weights = [len(_norm(t)) + 2 + (4 if re.search(r"[.!?…]$", t) else 2 if re.search(r"[,;:—]$", t) else 0)
               for t in toks]
    total_w = sum(weights) or 1
    out, acc = [], 0.1
    for w in weights:
        out.append(acc)
        acc += (total - 0.3) * w / total_w
    return out


async def _speak_with(v: str, text: str, dest_base: Path, how: str | None = None) -> dict:
    engine, _, name = v.partition(":")
    dest_base.parent.mkdir(parents=True, exist_ok=True)
    if engine == "elevenlabs":
        dest = dest_base.with_suffix(".mp3")
        spoken, _ = await _elevenlabs(text, dest, name)
    elif engine == "kokoro":
        return await _kokoro_speak(text, name, dest_base.with_suffix(".wav"))
    elif engine == "openai":
        dest = dest_base.with_suffix(".mp3")
        dur, spoken = await _openai(text, name, dest, how)
        return {"audio": str(dest), "dur": dur,
                "starts": align(text, spoken, dur) if spoken else _by_length(text, dur)}
    else:
        dest = dest_base.with_suffix(".mp3")
        spoken = await _edge(text, name, dest)
    dur = duration(dest) or (spoken[-1][0] + 0.6 if spoken else 0.0)
    return {"audio": str(dest), "dur": dur, "starts": align(text, spoken, dur)}


async def current_key() -> str:
    """Какой голос сейчас звучит — для кэша озвучки."""
    return await voice()


async def speak(text: str, dest_base: Path, how: str | None = None) -> dict | None:
    """Озвучить фразу → {audio, dur, starts} (starts — начало каждого экранного слова, с) или None.
    dest_base — путь без расширения; how — как читать (понимает только OpenAI).
    Выбранный голос не сработал — пробуем Kokoro, потом Edge."""
    if await provider() == "off" or not str(text).strip():
        return None
    dest_base.parent.mkdir(parents=True, exist_ok=True)
    v = await current_key()
    chain = [v] + [x for x in ("kokoro:af_heart", "edge:en-GB-RyanNeural") if x.split(":")[0] != v.split(":")[0]]
    for n, cand in enumerate(chain):
        try:
            res = await _speak_with(cand, text, dest_base, how)
            if n:
                res["fallback"] = f"{v} не сработал, прочитал {VOICES.get(cand, (cand,))[0]}"
            return res
        except Exception as exc:
            if n == len(chain) - 1:
                raise
            log.warning("Голос %s не сработал (%s), пробую следующий", cand, exc)


async def sample(v: str) -> Path:
    """Образец голоса для кнопки «послушать» (mp3, кэшируется на диске)."""
    from app import reelrender
    folder = config.DATA_DIR / "reels" / "samples"
    mp3 = folder / f"{re.sub(r'[^A-Za-z0-9_-]', '_', v)}_v2.mp3"
    if mp3.exists():
        return mp3
    how = (direction([("hook", SAMPLE_TEXT.split(". ")[0], "quiet intrigue, a little quicker, a beat after it"),
                      ("reveal", SAMPLE_TEXT, "drawn in, lean on 'doorway', let the last sentence land slower")])
           if is_one_take(v) else None)
    res = await _speak_with(v, SAMPLE_TEXT, folder / f"{re.sub(r'[^A-Za-z0-9_-]', '_', v)}_v2_raw", how)
    src = Path(res["audio"])
    if src.suffix != ".mp3":
        subprocess.run([reelrender.ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src), "-b:a", "128k",
                        str(mp3)], check=True, timeout=60)
        src.unlink(missing_ok=True)
    else:
        src.rename(mp3)
    return mp3
