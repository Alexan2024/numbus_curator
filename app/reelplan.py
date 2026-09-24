"""Рилсы v5: вёрстка и анимация в Remotion (папка remotion/). Здесь бот готовит для него props:
камеру, время каждого слова, выноски, звуки, титр — и запускает рендер.

Картинки и голос Remotion берёт с маленького локального HTTP-сервера, который поднимается на время рендера
и отдаёт папку рилса. Если Remotion недоступен или упал, reels.py собирает видео старой вёрсткой (reelrender.py)."""
import functools
import http.server
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import wave
from pathlib import Path

from PIL import Image

from app import reelrender

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent / "remotion"
BUNDLE = ROOT / "build"
FPS = 30
W, H = 1080, 1920
A = H / W
M = 72
MOVE = 1.3
SEG = float(os.getenv("REEL_SEG", "3.6"))
TITLE_HOLD = 3.0
RUBRIC = os.getenv("REEL_RUBRIC", "Paintings, closely")
CONCURRENCY = os.getenv("REMOTION_CONCURRENCY", "2")
SFX_VOL = {"hit": 0.32, "click": 0.11, "tick": 0.08, "tone": 0.15}


def available() -> bool:
    return BUNDLE.joinpath("index.html").exists() and bool(shutil.which("npx"))


def why_not() -> str:
    if not BUNDLE.joinpath("index.html").exists():
        return "нет сборки remotion/build (Dockerfile собирает её при деплое)"
    if not shutil.which("npx"):
        return "на сервере нет Node.js"
    return ""


# ======================= локальный сервер для картинок и голоса =======================

class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


class Serve:
    """with Serve(folder) as url: …  — отдаёт файлы папки по http://127.0.0.1:порт/"""

    def __init__(self, folder: Path):
        self.folder = folder

    def __enter__(self) -> str:
        handler = functools.partial(_Quiet, directory=str(self.folder))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def _url(base: str, folder: Path, path: Path | str) -> str:
    rel = Path(path).resolve().relative_to(folder.resolve())
    return f"{base}/{'/'.join(rel.parts)}"


# ======================= камера =======================

def _size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def _clamp_in(pw, ph, cx, cy, w):
    w = min(w, pw, ph / A)
    h = w * A
    return [min(max(cx, w / 2), pw - w / 2), min(max(cy, h / 2), ph - h / 2), w]


def _full(pw, ph):
    """Вся картина в кадре: по ширине, сверху и снизу — размытый фон (у высоких — по высоте)."""
    return [pw / 2, ph / 2, max(pw, ph / A)]


def _box_rect(pw, ph, b):
    x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in b]
    cx, cy = (x0 + x1) / 2 * pw, (y0 + y1) / 2 * ph
    w = max((x1 - x0) * pw * 1.45, (y1 - y0) * ph * 1.45 / A, pw * 0.17)
    return _clamp_in(pw, ph, cx, cy, w)


def _drift(r, full):
    if r is full or r == full:
        return [r[0], r[1], r[2] / 1.04]
    return [r[0], r[1] - r[2] * 0.015, r[2] / 1.05]


# ======================= слова =======================

def _words(raw: str, starts: list[float]) -> list[dict]:
    toks = raw.split()
    starts = (list(starts) + [starts[-1] if starts else 0.0] * len(toks))[:len(toks)]
    out = []
    for tok, t in zip(toks, starts):
        em = tok.startswith("*") or tok.rstrip(".,;:!?…—\"'’”").endswith("*")
        out.append({"w": tok.replace("*", ""), "em": em, "t": round(float(t), 3)})
    return out


# ======================= «детали картины» =======================

def _mix_voice(clips: list[tuple[float, str]], dur: float, dest: Path) -> Path:
    """Фразы голоса в своих местах → одна дорожка wav."""
    exe = reelrender.ffmpeg_exe()
    cmd = [exe, "-y", "-loglevel", "error"]
    for _, path in clips:
        cmd += ["-i", path]
    parts = [f"[{n}:a]aresample=44100,adelay={int(t * 1000)}:all=1[a{n}]" for n, (t, _) in enumerate(clips)]
    graph = ";".join(parts) + ";" + "".join(f"[a{n}]" for n in range(len(clips))) + \
        f"amix=inputs={len(clips)}:normalize=0,apad[a]"
    cmd += ["-filter_complex", graph, "-map", "[a]", "-t", f"{dur:.2f}", "-ac", "1", "-ar", "44100", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    return dest


def story_props(folder: Path, image: Path, beats: list[dict], voice, pt: dict, sfx: bool = True) -> dict:
    """beats — reels.beats(d) (с raw — текст с *акцентами*); voice — дубль OpenAI (dict), фразы по одной
    (list) или None. → props для композиции Story (пути — относительно folder, их заменит render())."""
    pw, ph = _size(image)
    full = _full(pw, ph)
    targets = [_box_rect(pw, ph, b["box"]) if b.get("box") else full for b in beats]
    out, cam, events, clips = [], [], [], []
    one = voice if isinstance(voice, dict) and voice.get("one_take") else None
    per = voice if isinstance(voice, list) else [None] * len(beats)
    per = list(per) + [None] * len(beats)

    if one:
        tb = list(one.get("beats") or []) + [{"start": None, "starts": []}] * len(beats)
        starts = []
        for i in range(len(beats)):
            s = tb[i].get("start")
            starts.append(float(s) if s is not None else (starts[-1] + 2.5 if starts else 0.0))
        arrive = [0.0]
        for i in range(1, len(beats)):
            arrive.append(max(starts[i] + 0.75, arrive[-1] + 1.4))
        ends = [starts[i + 1] - 0.05 if i + 1 < len(beats) else float(one["dur"]) + 0.3 for i in range(len(beats))]
        ends = [max(e, a + 1.0) for e, a in zip(ends, arrive)]
        for i, b in enumerate(beats):
            ws = tb[i].get("starts") or [starts[i] + k * 0.32 for k in range(len(b["raw"].split()))]
            out.append({"kind": b["kind"], "label": b.get("label"), "arrive": arrive[i], "start": starts[i], "end": ends[i],
                        "words": _words(b["raw"], ws)})
        clips = [(0.0, one["audio"])]
        voice_dur = float(one["dur"])
    else:
        t = 0.0
        arrive, ends = [], []
        for i, b in enumerate(beats):
            v = per[i]
            n = len(b["raw"].split())
            if i == 0:
                a, s0 = 0.0, 0.25
            else:
                a = t + MOVE
                s0 = a - 0.75 + (1.0 if b["kind"] == "climax" else 0)
            speech = float(v["dur"]) if v else n * 0.32 + 0.3
            ws = [s0 + x for x in v["starts"]] if v and v.get("starts") else [s0 + k * 0.32 for k in range(n)]
            tail = 0.7 if b["kind"] == "hook" else 1.0 if b["kind"] == "climax" else 0.4
            e = max(a + 2.0, s0 + speech + tail)
            out.append({"kind": b["kind"], "label": b.get("label"), "arrive": a, "start": s0, "end": e,
                        "words": _words(b["raw"], ws)})
            if v:
                clips.append((s0, v["audio"]))
            arrive.append(a)
            ends.append(e)
            t = e
        voice_dur = ends[-1]

    # камера: приехать к детали к arrive, там медленно дрейфовать, уехать за MOVE до следующей
    for i, r in enumerate(targets):
        if i == 0:
            cam.append([0.0, *r])
        else:
            cam.append([arrive[i] - MOVE, *_drift(targets[i - 1], full)])
            cam.append([arrive[i], *r])
        if i + 1 == len(targets):
            cam.append([ends[i], *_drift(r, full)])
    cam.sort(key=lambda k: k[0])

    for b, o, r in zip(beats, out, targets):
        if b.get("box") and b["kind"] == "reveal":
            o["box"] = [b["box"][0] * pw, b["box"][1] * ph, b["box"][2] * pw, b["box"][3] * ph]
            events += [{"t": o["arrive"] + 0.05, "sfx": "click"}, {"t": o["arrive"] + 0.45, "sfx": "tick"}]
        else:
            o["box"] = None
    end_start = max(ends[-1], voice_dur) + 0.3
    # титр: картина вписывается в поле 936×760 слева сверху, камера сама приводит её туда
    s_end = min(936 / pw, 760 / ph)
    cwe = W / s_end
    cam.append([end_start, *cam[-1][1:]])
    cam.append([end_start + 1.3, cwe / 2 - M / s_end, cwe * A / 2 - 300 / s_end, cwe])
    events = ([{"t": 0.0, "sfx": "hit"}] + events + [{"t": end_start + 0.2, "sfx": "tone"}]) if sfx else []
    for e in events:
        e["vol"] = SFX_VOL[e["sfx"]]
    dur = end_start + 3.9

    voice_path = None
    if clips:
        voice_path = folder / "voice_mix.wav"
        _mix_voice(clips, dur, voice_path)
    year = str(pt.get("year") or "").strip()
    meta = [[k, v] for k, v in (("Medium", pt.get("medium")), ("Size", pt.get("size")),
                                ("Collection", pt.get("museum"))) if v]
    return {"fps": FPS, "duration": round(dur * FPS), "pw": pw, "ph": ph, "cam": [[round(x, 3) for x in k] for k in cam],
            "beats": out, "sfx": events, "endStart": round(end_start, 3), "labelTop": round(300 + ph * s_end + 60),
            "title": pt.get("title") or "", "sub": ", ".join(x for x in (pt.get("author"), year) if x),
            "rubric": RUBRIC, "series": " · ".join(x for x in (pt.get("title"), year) if x), "meta": meta,
            "image": str(image), "voice": str(voice_path) if voice_path else None, "_dur": dur}


# ======================= подборка =======================

def collection_props(folder: Path, d: dict, sfx: bool = True) -> dict:
    items, t = [], 0.0
    for n, it in enumerate(d["items"]):
        pw, ph = _size(Path(it["path"]))
        fx, fy = (it.get("focus") or [0.5, 0.5])[:2]
        full = _clamp_in(pw, ph, pw / 2, ph / 2, 1e9)          # на весь кадр, без полей
        dur = SEG + (TITLE_HOLD if n == 0 else 0)
        if pw / ph > W / H * 1.25:                            # широкая — медленный проезд мимо главного
            w = full[2]
            span = min(pw * 0.16, (pw - w) / 2)
            a = _clamp_in(pw, ph, fx * pw - span / 2, ph / 2, w)
            b = _clamp_in(pw, ph, fx * pw + span / 2, ph / 2, w)
            if n % 2:
                a, b = b, a
        else:                                                 # высокая — наезд к главному
            a = full
            b = _clamp_in(pw, ph, full[0] + (fx * pw - full[0]) * 0.35, full[1] + (fy * ph - full[1]) * 0.35,
                          full[2] / 1.1)
        items.append({"image": it["path"], "pw": pw, "ph": ph, "start": round(t, 3), "dur": dur,
                      "cam": [[0.0, *a], [dur, *b]], "title": it.get("title") or "", "author": it.get("author") or "",
                      "year": str(it.get("year") or "")})
        t += dur
    cap = re.split(r"(?<=[.!?])\s", (d.get("caption") or "").strip())[0] if d.get("caption") else ""
    events = ([{"t": 0.0, "sfx": "hit", "vol": 0.25}] +
              [{"t": it["start"] + 0.3, "sfx": "tick", "vol": 0.06} for it in items[1:]]) if sfx else []
    dur = t + 0.3
    return {"fps": FPS, "duration": round(dur * FPS), "items": items, "titleEnd": TITLE_HOLD,
            "title": d.get("title_em") or d.get("title") or "", "subtitle": cap if len(cap) <= 90 else "",
            "series": (d.get("title") or "").replace("*", ""), "sfx": events, "_dur": dur}


# ======================= рендер =======================

def render(comp: str, props: dict, folder: Path, out: Path) -> float:
    """Рендер композиции Remotion → out (mp4 до 50 МБ, громкость под соцсети). → длительность, с."""
    props = json.loads(json.dumps(props))
    with Serve(folder) as base:
        def fix(v):
            return _url(base, folder, v) if isinstance(v, str) and v.startswith(str(folder)) else v
        if props.get("image"):
            props["image"] = fix(props["image"])
        if props.get("voice"):
            props["voice"] = fix(props["voice"])
        for it in props.get("items") or []:
            it["image"] = fix(it["image"])
        pfile = folder / f"props_{comp}.json"
        pfile.write_text(json.dumps(props, ensure_ascii=False))
        raw = out.with_name(out.stem + "_raw.mp4")
        cmd = ["npx", "remotion", "render", str(BUNDLE), comp, str(raw), f"--props={pfile}",
               f"--concurrency={CONCURRENCY}", "--crf=20", "--log=error"]
        if os.getenv("REMOTION_BROWSER"):
            cmd.append(f"--browser-executable={os.getenv('REMOTION_BROWSER')}")
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=int(os.getenv("REMOTION_TIMEOUT", "1800")))
        if r.returncode != 0 or not raw.exists():
            raise RuntimeError(f"Remotion: {(r.stderr or r.stdout)[-400:]}")
    # сжать под лимит Telegram и выровнять громкость
    exe = reelrender.ffmpeg_exe()
    has_audio = "Audio:" in subprocess.run([exe, "-hide_banner", "-i", str(raw)], capture_output=True, text=True).stderr
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-maxrate", "9M", "-bufsize", "18M", "-pix_fmt", "yuv420p"]
    cmd += (["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "44100"] if has_audio else ["-an"])
    cmd += ["-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    raw.unlink(missing_ok=True)
    return float(props.get("_dur") or 0)
