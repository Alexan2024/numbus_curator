"""Рилсы v5: вёрстка и анимация в Remotion (папка remotion/). Здесь бот готовит для него props:
камеру, время каждого слова, выноски, звуки, титр — и запускает рендер.

Картинки и голос Remotion берёт с маленького локального HTTP-сервера, который поднимается на время рендера
и отдаёт папку рилса. Если Remotion недоступен или упал, reels.py собирает видео старой вёрсткой (reelrender.py)."""
import functools
import hashlib
import http.server
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from PIL import Image

from app import config, reelrender

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent / "remotion"
BUNDLE = ROOT / "build"
FPS = 30
W, H = 1080, 1920
A = H / W
M = 72
SEG = float(os.getenv("REEL_SEG", "3.6"))
TITLE_HOLD = 3.0
RUBRIC = os.getenv("REEL_RUBRIC", "Paintings, closely")
# больше, чем ядер у машины, Remotion не принимает и падает
CONCURRENCY = str(max(1, min(int(os.getenv("REMOTION_CONCURRENCY", "2")), os.cpu_count() or 1)))


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
# Камера — [время, cx, cy, cw(, изгиб)] в пикселях картины: центр кадра и его ширина. На экране масштаб s = W / cw.
# Деталь ставится не в центр кадра, а в центр «чистого окна» — между верхней строкой и текстом внизу,
# и не приближается сильнее ZOOM_MAX экранных пикселей на пиксель картины (иначе мыло).

ZOOM_MAX = float(os.getenv("REEL_ZOOM_MAX", "1.25"))
WIN = (290, 1170)            # окно для детали под рассказом: от верхней строки до подписи (безопасные зоны Instagram)
FULL_H = 1450                # общий план: картина не выше этого
HOLD_ZOOM = 1.04             # медленный наезд, пока камера стоит на детали
HOOK_TOP = 290               # хук сверху — ниже шапки Instagram
HOOK_PUSH = float(os.getenv("REEL_HOOK_PUSH", "1.12"))   # первый кадр чуть шире и сразу наезд: движение с кадра 0
HOOK_PUSH_T = 0.9
LOOP = os.getenv("REEL_LOOP", "1").strip().lower() not in ("0", "off", "false", "no")
LABEL_HOLD = 3.2             # от начала титра до возврата к первому кадру, с
LOOP_BACK = 0.7              # возврат к первому кадру, с
SLACK = (60, 220)            # насколько кадр может выйти за край картины (экранные px по x, y): деталь у самого
                             # края лучше показать с полоской тёмного фона, чем уводить под текст или отъезжать


def _size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def _clamp_cam(pw, ph, cx, cy, cw, slack=(0, 0)):
    """Кадр не выходит за картину (с запасом slack экранных px) — по тем осям, где он меньше картины."""
    ch = cw * A
    s = W / cw
    sx, sy = slack[0] / s, slack[1] / s
    if cw <= pw:
        cx = min(max(cx, cw / 2 - sx), pw - cw / 2 + sx)
    if ch <= ph:
        cy = min(max(cy, ch / 2 - sy), ph - ch / 2 + sy)
    return [cx, cy, cw]


def _s_full(pw, ph):
    return min(W / pw, FULL_H / ph)


def _full(pw, ph):
    """Вся картина: по ширине кадра (высокая — не выше FULL_H), центр — в середине окна над текстом."""
    s = _s_full(pw, ph)
    wy = (WIN[0] + WIN[1]) / 2
    top = max(WIN[0] - 40, wy - ph * s / 2)            # высокая картина начинается у верхней строки
    cy = ph / 2 - (top + ph * s / 2 - H / 2) / s
    return [pw / 2, cy, W / s]


def _hook_win(text: str, lang: str = "en") -> tuple[int, int]:
    """Хук набран крупно сверху: деталь — под ним. Русская антиква шире, и слова длиннее: в строку входит меньше."""
    lines = max(1, -(-len(text) // (15 if lang == "ru" else 18)))
    return (min(HOOK_TOP + lines * 108 + 60, 940), 1440)


HOOK_SLACK = (60, 900)       # на хуке верх кадра всё равно под текстом: деталь у верхнего края картины можно
                             # опустить в окно под хуком, над ней будет тёмный фон


def _fit(pw, ph, box, win, slack=SLACK):
    """Кадр, в котором деталь box (доли) целиком в окне win (y экрана) с полями. → [cx, cy, cw]."""
    x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in box]
    bx0, by0, bx1, by1 = x0 * pw, y0 * ph, x1 * pw, y1 * ph
    bw, bh = max(bx1 - bx0, pw * 0.02), max(by1 - by0, ph * 0.02)
    bcx, bcy = (bx0 + bx1) / 2, (by0 + by1) / 2
    wy0, wy1 = win
    wyc = (wy0 + wy1) / 2
    s_min = _s_full(pw, ph)
    # деталь — около двух третей окна: вокруг остаётся картина, видно, в каком она месте
    s = max(min(0.70 * 960 / bw, 0.62 * (wy1 - wy0) / bh, ZOOM_MAX), s_min)
    for _ in range(12):
        cx, cy, cw = _clamp_cam(pw, ph, bcx, bcy - (wyc - H / 2) / s, W / s, slack)
        # у края картины камера упирается — проверяем, что деталь всё ещё в окне и не под текстом
        top = H / 2 + (by0 - cy) * s
        bot = H / 2 + (by1 - cy) * s
        left = W / 2 + (bx0 - cx) * s
        right = W / 2 + (bx1 - cx) * s
        if top >= wy0 - 60 and bot <= wy1 + 60 and left >= -10 and right <= W + 10:
            return [cx, cy, cw]
        if s <= s_min * 1.001:
            break
        s = max(s * 0.9, s_min)
    return _full(pw, ph)


def _hold(pw, ph, r, box, k=HOLD_ZOOM):
    """Медленный наезд на месте: приблизить в k раз так, чтобы центр детали остался в той же точке экрана."""
    cx, cy, cw = r
    if box:
        x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in box]
        px, py = (x0 + x1) / 2 * pw, (y0 + y1) / 2 * ph
    else:
        px, py = pw / 2, ph / 2
    s, s2 = W / cw, W / cw * k
    sx, sy = W / 2 + (px - cx) * s, H / 2 + (py - cy) * s
    return _clamp_cam(pw, ph, px - (sx - W / 2) / s2, py - (sy - H / 2) / s2, cw / k, SLACK if box else (0, 0))


# Движение: плавный разгон и торможение (синус), скорость ограничена — на телефоне быстрый проезд дёргается.
# Камера всегда едет от детали к детали — зритель видит, где на картине эта часть. На быстром участке проезда
# картинка чуть смазывается по направлению движения (как у настоящей камеры), поэтому проезд не стробит.
PAN_MAX = float(os.getenv("REEL_PAN_MAX", "30"))     # пик скорости проезда, экранных px за кадр
ZOOM_RATE = 0.025                                    # пик скорости наезда: 2,5% масштаба за кадр
MOVE_MIN, MOVE_MAX = 1.8, 4.5                        # переезд, с
MIN_HOLD = 1.2                                       # сколько камера стоит на детали до следующего переезда, с
CUT_OVER = float(os.getenv("REEL_CUT_OVER", "99"))   # переезд дольше — растворением (по умолчанию — никогда)
DISSOLVE = 0.7


def _need(a, b):
    """Сколько секунд нужно на переезд a → b, чтобы не превысить скорость проезда и наезда."""
    s = W / (a[2] * b[2]) ** 0.5
    d = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 * s
    t_pan = d * (math.pi / 2) / (PAN_MAX * FPS)
    t_zoom = abs(math.log(a[2] / b[2])) * (math.pi / 2) / (ZOOM_RATE * FPS)
    return min(MOVE_MAX, max(MOVE_MIN, t_pan, t_zoom))


# ======================= слова =======================

def _words(raw: str, starts: list[float]) -> list[dict]:
    """*слово* — акцент (курсив), ^слово — на нём камера приезжает к детали (на экране знака нет)."""
    toks = raw.split()
    starts = (list(starts) + [starts[-1] if starts else 0.0] * len(toks))[:len(toks)]
    out = []
    for tok, t in zip(toks, starts):
        em = "*" in tok
        out.append({"w": tok.replace("*", "").replace("^", ""), "em": em, "t": round(float(t), 3)})
    return out


def _anchor(raw: str) -> int | None:
    for n, tok in enumerate(raw.split()):
        if "^" in tok:
            return n
    return None


# ======================= звук =======================

SFX_VOL = {"page": 0.3, "move": 0.22, "close": 0.28}
SFX_DIR = config.DATA_DIR / "sfx"


def _sfx_pool(name: str) -> list:
    """Свои звуки из /data/sfx (move*.wav, page*.wav, close*.wav), иначе встроенные — студийные записи Mixkit
    (remotion/public/SFX-SOURCES.txt)."""
    own = []
    if SFX_DIR.exists():
        own = sorted(SFX_DIR.glob(f"{name}*.wav")) + sorted(SFX_DIR.glob(f"{name}*.mp3"))
    return own or sorted((ROOT / "public").glob(f"sfx_{name}_*.wav"))


_peaks: dict = {}


def _sfx_peak(f: Path) -> float:
    """Где у звука самая громкая точка, с — чтобы пик прохода воздуха пришёлся на середину переезда."""
    if f not in _peaks:
        try:
            import numpy as np
            exe = reelrender.ffmpeg_exe()
            raw = subprocess.run([exe, "-v", "error", "-i", str(f), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                                 capture_output=True, timeout=30).stdout
            x = np.abs(np.frombuffer(raw, "<i2").astype(np.float32))
            k = 400                                  # огибающая по 50 мс
            env = np.convolve(x, np.ones(k) / k, "same")
            _peaks[f] = float(np.argmax(env)) / 8000 if len(env) else 0.0
        except Exception:
            _peaks[f] = 0.0
    return _peaks[f]


def _sfx(folder: Path, events: list[tuple], seed: str) -> list[dict]:
    """События (t, имя[, по пику]) → [{t, src, vol}]. По пику — t означает момент самой громкой точки звука.
    Одинаковые события берут разные варианты по кругу, начало круга — от seed."""
    out, used = [], {}
    base = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    for e in events:
        t, name = e[0], e[1]
        pool = _sfx_pool(name)
        if not pool:
            continue
        k = used.get(name, 0)
        used[name] = k + 1
        f = pool[(base + k) % len(pool)]
        if len(e) > 2 and e[2]:
            t = t - _sfx_peak(f)
        if f.parent == ROOT / "public":
            src = f"static:{f.name}"
        else:
            dest = folder / "sfx" / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy(f, dest)
            src = str(dest)
        out.append({"t": round(max(0.0, t), 3), "src": src, "vol": SFX_VOL[name]})
    return out


def _mix_voice(clips: list[tuple[float, str, float]], dur: float, dest: Path) -> Path:
    """Фразы голоса в своих местах (t, файл, сколько срезать тишины в начале) → одна дорожка wav,
    громкость голоса выровнена отдельно, в два прохода: звуки потом не вытягиваются нормализацией."""
    exe = reelrender.ffmpeg_exe()
    raw = dest.with_name(dest.stem + "_raw.wav")
    cmd = [exe, "-y", "-loglevel", "error"]
    for _, path, _ in clips:
        cmd += ["-i", path]
    parts = [f"[{n}:a]aresample=44100,atrim=start={trim:.3f},asetpts=PTS-STARTPTS,adelay={int(max(0, t) * 1000)}:all=1[a{n}]"
             for n, (t, _, trim) in enumerate(clips)]
    graph = ";".join(parts) + ";" + "".join(f"[a{n}]" for n in range(len(clips))) + \
        f"amix=inputs={len(clips)}:normalize=0,apad[a]"
    cmd += ["-filter_complex", graph, "-map", "[a]", "-t", f"{dur:.2f}", "-ac", "1", "-ar", "44100", str(raw)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    target = "I=-14:TP=-1.5:LRA=9"                  # громкость под соцсети (Instagram выравнивает к −14 LUFS)
    r = subprocess.run([exe, "-hide_banner", "-i", str(raw), "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=300)
    try:
        m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
        af = (f"loudnorm={target}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
              f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    except (ValueError, KeyError):
        af = f"loudnorm={target}"
    subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(raw), "-af", af, "-ar", "44100", str(dest)],
                   check=True, capture_output=True, timeout=300)
    raw.unlink(missing_ok=True)
    return dest


# ======================= линии поверх снимка =======================

def _marks(beats: list[dict], out: list[dict], pw: int, ph: int, end_start: float) -> list[dict]:
    """Схема линий для «Разбора здания» (ось, уровень, сетка, контур, диагонали — на рамке детали) и кольцо
    вокруг человека в «Масштабе». Линия рисуется, когда камера приезжает к детали, и остаётся до конца
    кульминации — схема складывается; прошлые линии тише."""
    res = []
    climax_end = next((o["end"] for b, o in zip(beats, out) if b["kind"] == "climax"), end_start)
    for b, o in zip(beats, out):
        m = b.get("mark")
        box = m and _box4(m.get("box") or b.get("box"))
        if not box:
            continue
        x0, y0, x1, y1 = box
        row = {"type": m.get("type") or "frame", "x0": round(x0 * pw, 1), "y0": round(y0 * ph, 1),
               "x1": round(x1 * pw, 1), "y1": round(y1 * ph, 1), "start": round(o["arrive"] + 0.05, 3),
               "active": round(o["end"], 3), "label": str(m.get("label") or "")[:18]}
        if row["type"] == "grid":
            row.update(cols=max(1, min(24, int(m.get("cols") or 1))), rows=max(1, min(24, int(m.get("rows") or 1))))
        if m.get("persist"):
            row.update(start=round(max(0.0, o["start"] + 0.2), 3), end=round(end_start, 3), active=round(end_start, 3),
                       draw=0.6)
        else:
            row["end"] = round(max(climax_end, o["end"]), 3)
        res.append(row)
    return res


def _box4(b) -> list | None:
    try:
        b = [max(0.0, min(1.0, float(x))) for x in b]
        return b if len(b) == 4 and b[2] > b[0] and b[3] > b[1] else None
    except (TypeError, ValueError):
        return None


# ======================= «детали картины» =======================

def story_props(folder: Path, image: Path, beats: list[dict], voice, pt: dict, sfx: bool = True) -> dict:
    """beats — reels.beats(d) (с raw — текст с *акцентами* и ^якорем); voice — дубль OpenAI (dict), фразы по одной
    (list) или None. → props для композиции Story (пути — относительно folder, их заменит render())."""
    pw, ph = _size(image)
    full = _full(pw, ph)
    rects = []
    for b in beats:
        if not b.get("box"):
            rects.append((full, _hold(pw, ph, full, None, 1.03)))
            continue
        hook = b["kind"] == "hook"
        r0 = _fit(pw, ph, b["box"], _hook_win(b["text"], pt.get("lang") or "en") if hook else WIN, HOOK_SLACK if hook else SLACK)
        rects.append((r0, _hold(pw, ph, r0, b["box"])))
    anchors = [_anchor(b["raw"]) for b in beats]
    plan: list[tuple[float, float, int]] = []          # (уезжает, приезжает, 1 — растворение) для каждой части

    def place(i, ws, s0, prev_arrive, end=None):
        """Когда камере уехать с прошлой части и приехать к этой. Общий план — медленный отъезд на всю фразу;
        деталь — проезд, который кончается на слове-якоре (или чуть позже, если переезд длинный: скорость важнее)."""
        if i == 0:
            return 0.0, 0.0, 0
        a, b = rects[i - 1][1], rects[i][0]
        need = _need(a, b)
        if not beats[i].get("box"):
            leave = max(s0 - 0.15, prev_arrive + MIN_HOLD)
            return leave, leave + min(max(need, 2.4), MOVE_MAX), 0
        if anchors[i] is not None and anchors[i] < len(ws):
            want = ws[anchors[i]] + 0.1
        else:
            want = s0 + 0.5
        if need > CUT_OVER:
            leave = max(want - DISSOLVE / 2, prev_arrive + MIN_HOLD)
            return leave, leave + DISSOLVE, 1
        arrive = max(want, prev_arrive + MIN_HOLD + need)
        if end is not None and arrive > end - 1.2:
            # не успевает к концу фразы — приезжаем раньше, проезд короче (но не короче MOVE_MIN)
            arrive = max(end - 1.2, prev_arrive + MIN_HOLD + MOVE_MIN)
        leave = max(prev_arrive + MIN_HOLD, arrive - need)
        return leave, arrive, 0

    out, clips, arrive, ends = [], [], [], []
    one = voice if isinstance(voice, dict) and voice.get("one_take") else None
    per = voice if isinstance(voice, list) else [None] * len(beats)
    per = list(per) + [None] * len(beats)

    if one:
        tb = list(one.get("beats") or []) + [{"start": None, "starts": []}] * len(beats)
        # тишина перед первым словом срезается: хук звучит с первого кадра
        first = (tb[0].get("starts") or [0.0])[0] if tb else 0.0
        lead = max(0.0, float(first or 0.0) - 0.06)
        starts, wss = [], []
        for i, b in enumerate(beats):
            st = tb[i].get("start")
            st = float(st) - lead if st is not None else (starts[-1] + 2.5 if starts else 0.0)
            n = len(b["raw"].split())
            ws = [float(x) - lead for x in tb[i].get("starts") or []] or [st + k * 0.32 for k in range(n)]
            starts.append(st)
            wss.append(ws)
        voice_end = float(one["dur"]) - lead
        for i in range(len(beats)):
            end_i = starts[i + 1] - 0.05 if i + 1 < len(beats) else voice_end + 0.3
            plan.append(place(i, wss[i], starts[i], arrive[-1] if arrive else 0.0, end_i))
            arrive.append(plan[-1][1])
        voice_dur = float(one["dur"]) - lead
        ends = [starts[i + 1] - 0.05 if i + 1 < len(beats) else voice_dur + 0.3 for i in range(len(beats))]
        ends = [max(e, a + 1.0) for e, a in zip(ends, arrive)]
        for i, b in enumerate(beats):
            out.append({"kind": b["kind"], "arrive": arrive[i], "start": starts[i], "end": ends[i],
                        "words": _words(b["raw"], wss[i])})
        clips = [(0.0, one["audio"], lead)]
    else:
        t = 0.0
        for i, b in enumerate(beats):
            v = per[i]
            n = len(b["raw"].split())
            trim = max(0.0, float(v["starts"][0]) - 0.06) if v and v.get("starts") else 0.0
            if i == 0:
                s0 = 0.0
            else:
                s0 = t + (0.7 if b["kind"] == "climax" else 0.2)
            speech = (float(v["dur"]) - trim) if v else n * 0.32 + 0.3
            ws = [s0 + x - trim for x in v["starts"]] if v and v.get("starts") else [s0 + k * 0.32 for k in range(n)]
            plan.append(place(i, ws, s0, arrive[-1] if arrive else 0.0))
            a = plan[-1][1]
            tail = 0.5 if b["kind"] == "hook" else 1.0 if b["kind"] == "climax" else 0.3
            e = max(a + 1.6, s0 + speech + tail)
            out.append({"kind": b["kind"], "arrive": a, "start": s0, "end": e, "words": _words(b["raw"], ws)})
            if v:
                clips.append((s0, v["audio"], trim))
            arrive.append(a)
            ends.append(e)
            t = e
        voice_dur = ends[-1]

    # камера: [время, cx, cy, cw, растворение]. Приехать к arrive, медленно наезжать, уехать к следующей части
    cam = []
    start_view = _clamp_cam(pw, ph, rects[0][0][0], rects[0][0][1], min(rects[0][0][2] * HOOK_PUSH, full[2] * 1.02),
                            HOOK_SLACK if beats[0].get("box") else (0, 0))
    for i, (r0, r1) in enumerate(rects):
        if i == 0:
            cam.append([0.0, *start_view, 0])
            cam.append([HOOK_PUSH_T, *r0, 0])
        else:
            leave, a, cut = plan[i]
            cam.append([leave, *rects[i - 1][1], 0])
            cam.append([a, *r0, cut])
        if i + 1 == len(rects):
            cam.append([max(ends[i], cam[-1][0] + 0.5), *r1, 0])

    reveal_n = 0
    for b, o in zip(beats, out):
        if b["kind"] == "reveal":
            reveal_n += 1
            o["label"] = b.get("label") or ""
            o["n"] = reveal_n
    end_start = max(ends[-1], voice_dur) + 0.3
    # титр: картина вписывается в поле 936×760 слева сверху, камера сама приводит её туда
    s_end = min(936 / pw, 760 / ph)
    cwe = W / s_end
    end_start = max(end_start, cam[-1][0] + 0.1)
    cam.append([end_start, *cam[-1][1:4], 0])
    label_cam = [cwe / 2 - M / s_end, cwe * A / 2 - 300 / s_end, cwe]
    cam.append([end_start + 1.6, *label_cam, 0])
    loop_start = None
    if LOOP:
        # этикетка, потом камера возвращается к первому кадру — повтор ролика начинается без стыка
        loop_start = end_start + LABEL_HOLD
        cam.append([loop_start, *label_cam, 0])
        cam.append([loop_start + LOOP_BACK, *start_view, 0])
        dur = loop_start + LOOP_BACK
    else:
        dur = end_start + 4.0
    # звуки: мягкий проход воздуха на каждом переезде — его пик на середине переезда, где камера быстрее всего;
    # на титре — перелистнутая страница
    ev = [((pl[0] + pl[1]) / 2, "move", True) for pl in plan[1:] if pl[1] - pl[0] >= 1.0]
    events = _sfx(folder, ev + [(end_start + 0.15, "close", False)], str(image)) if sfx else []
    marks = _marks(beats, out, pw, ph, end_start)

    voice_path = None
    if clips:
        voice_path = folder / "voice_mix.wav"
        _mix_voice(clips, dur, voice_path)
    year = str(pt.get("year") or "").strip()
    names = pt.get("meta_names") or (("Техника", "Размер", "Собрание") if pt.get("lang") == "ru"
                                     else ("Medium", "Size", "Collection"))
    meta = [[k, v] for k, v in zip(names, (pt.get("medium"), pt.get("size"), pt.get("museum"))) if v]
    return {"fps": FPS, "duration": round(dur * FPS), "pw": pw, "ph": ph,
            "cam": [[round(x, 3) for x in k] for k in cam], "marks": marks,
            "loopStart": round(loop_start, 3) if loop_start else None, "coverT": round(max(0.8, out[0]["end"] - 0.35), 2),
            "beats": out, "sfx": events, "slack": list(HOOK_SLACK), "endStart": round(end_start, 3), "labelTop": round(300 + ph * s_end + 60),
            "title": pt.get("title") or "", "sub": ", ".join(x for x in (pt.get("author"), year) if x),
            "rubric": pt.get("rubric") or RUBRIC, "series": " · ".join(x for x in (pt.get("title"), year) if x), "meta": meta,
            "image": str(image), "voice": str(voice_path) if voice_path else None, "_dur": dur,
            "lang": pt.get("lang") or "en"}


# ======================= время частей рассказа (пары, подборки) =======================

def _timeline(beats: list[dict], voice, min_part: float = 1.6) -> tuple[list[dict], list, float]:
    """Когда звучит каждая часть и каждое слово — без камеры. beats — [{kind, raw}]; voice — один дубль (dict),
    фразы по одной (list) или None. → (части [{kind, start, end, words, arrive}], дорожки голоса, конец голоса).
    arrive — слово-якорь ^ (на нём происходит переход), иначе чуть после начала части."""
    out, clips = [], []
    one = voice if isinstance(voice, dict) and voice.get("one_take") else None
    per = list(voice) if isinstance(voice, list) else []
    per += [None] * len(beats)
    if one:
        tb = list(one.get("beats") or []) + [{"start": None, "starts": []}] * len(beats)
        first = (tb[0].get("starts") or [0.0])[0] if tb else 0.0
        lead = max(0.0, float(first or 0.0) - 0.06)
        starts, wss = [], []
        for i, b in enumerate(beats):
            st = tb[i].get("start")
            st = float(st) - lead if st is not None else (starts[-1] + 2.5 if starts else 0.0)
            n = len(b["raw"].split())
            wss.append([float(x) - lead for x in tb[i].get("starts") or []] or [st + k * 0.32 for k in range(n)])
            starts.append(st)
        voice_end = float(one["dur"]) - lead
        for i, b in enumerate(beats):
            end = starts[i + 1] - 0.05 if i + 1 < len(beats) else voice_end + 0.3
            out.append({"kind": b["kind"], "start": starts[i], "end": max(end, starts[i] + 0.8),
                        "words": _words(b["raw"], wss[i])})
        clips = [(0.0, one["audio"], lead)]
    else:
        t = 0.0
        for i, b in enumerate(beats):
            v = per[i]
            n = len(b["raw"].split())
            trim = max(0.0, float(v["starts"][0]) - 0.06) if v and v.get("starts") else 0.0
            s0 = 0.0 if i == 0 else t + (0.6 if b["kind"] == "climax" else 0.2)
            speech = (float(v["dur"]) - trim) if v else n * 0.32 + 0.3
            ws = [s0 + x - trim for x in v["starts"]] if v and v.get("starts") else [s0 + k * 0.32 for k in range(n)]
            tail = 0.5 if b["kind"] == "hook" else 1.0 if b["kind"] == "climax" else 0.3
            e = max(s0 + min_part, s0 + speech + tail)
            out.append({"kind": b["kind"], "start": s0, "end": e, "words": _words(b["raw"], ws)})
            if v:
                clips.append((s0, v["audio"], trim))
            t = e
        voice_end = out[-1]["end"] if out else 0.0
    for b, o in zip(beats, out):
        a = _anchor(b["raw"])
        ws = [w["t"] for w in o["words"]]
        o["arrive"] = round(ws[a] if a is not None and a < len(ws) else o["start"] + 0.25, 3)
    return out, clips, voice_end


# ======================= пары =======================
# Каждая картинка — окно на экране (вне его обрезана) и камера (как в «Деталях»: центр и ширина кадра в пикселях
# картинки). Состояния: одна картинка на экране (a / b), обе (both): в сравнении — одна над другой или рядом,
# в шторке — половина на половину, в растворении — B поверх A. Переход — на слове-якоре ^.

PAIR_TR = {"wipe": 1.4, "dissolve": 1.3, "split": 1.0}
SPLIT_TOP, SPLIT_BOTTOM = 300, 1300                    # сравнение — в этом поясе экрана
END_BOX = (M, 330, W - 2 * M, 700)                     # две картинки рядом на титре


def _view_at(pw, ph, x, y, w, h):
    """Камера, при которой вся картинка стоит в прямоугольнике x, y, w, h экрана (по ширине w)."""
    s = w / pw
    return [(W / 2 - x) / s, (H / 2 - y) / s, W / s]


def _contain(pw, ph, x, y, w, h):
    """Картинка целиком внутри области, по центру → её прямоугольник на экране."""
    s = min(w / pw, h / ph)
    return x + (w - pw * s) / 2, y + (h - ph * s) / 2, pw * s, ph * s


def _cover_view(pw, ph, rect, focus=(0.5, 0.5), box=None):
    """Камера, при которой картинка закрывает окно rect целиком; box — приблизить к детали внутри окна."""
    rx, ry, rw, rh = rect
    s = max(rw / pw, rh / ph)
    fx, fy = focus[0] * pw, focus[1] * ph
    if box:
        x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in box]
        bw, bh = max((x1 - x0) * pw, pw * 0.02), max((y1 - y0) * ph, ph * 0.02)
        s = max(min(0.7 * rw / bw, 0.62 * rh / bh, ZOOM_MAX), s)
        fx, fy = (x0 + x1) / 2 * pw, (y0 + y1) / 2 * ph
    # точка фокуса — в центр окна, но картинка не отходит от краёв окна
    left = rx + rw / 2 - fx * s
    top = ry + rh / 2 - fy * s
    left = min(max(left, rx + rw - pw * s), rx)
    top = min(max(top, ry + rh - ph * s), ry)
    return [(W / 2 - left) / s, (H / 2 - top) / s, W / s]


def _zoom(view, k, pw, ph):
    """Та же камера, приближенная в k раз к центру кадра (медленный наезд)."""
    return [view[0], view[1], view[2] / k]


def _pair_rect(sizes) -> tuple:
    """Общее окно для шторки и растворения: по средней пропорции двух картинок."""
    r = sum(w / h for w, h in sizes) / len(sizes)
    rh = min(W / max(0.56, min(1.7, r)), FULL_H)
    wy = (WIN[0] + WIN[1]) / 2
    top = max(WIN[0] - 40, wy - rh / 2)
    return (0.0, top, float(W), rh)


def _split_rects(sizes):
    """Сравнение: обе вертикальные — рядом, иначе — одна над другой. → [(x, y, w, h), (x, y, w, h)]."""
    if all(w / h < 0.9 for w, h in sizes):
        gap, cw = 24, (W - 2 * 48 - 24) / 2
        areas = [(48, SPLIT_TOP, cw, SPLIT_BOTTOM - SPLIT_TOP), (48 + cw + gap, SPLIT_TOP, cw, SPLIT_BOTTOM - SPLIT_TOP)]
    else:
        gap, hh = 24, (SPLIT_BOTTOM - SPLIT_TOP - 24) / 2
        areas = [(0, SPLIT_TOP, W, hh), (0, SPLIT_TOP + hh + gap, W, hh)]
    return [_contain(w, h, *a) for (w, h), a in zip(sizes, areas)]


def _end_rects(sizes):
    x, y, w, h = END_BOX
    if all(a / b < 1.1 for a, b in sizes):
        cw = (w - 28) / 2
        return [_contain(sizes[0][0], sizes[0][1], x, y, cw, h), _contain(sizes[1][0], sizes[1][1], x + cw + 28, y, cw, h)]
    hh = (h - 24) / 2
    return [_contain(sizes[0][0], sizes[0][1], x, y, w, hh), _contain(sizes[1][0], sizes[1][1], x, y + hh + 24, w, hh)]


def pair_props(folder: Path, images: list[Path], beats: list[dict], voice, info: dict, layout: str,
               sfx: bool = True) -> dict:
    """Пара: images — [A, B]; beats — reels.pair_beats(d) (с raw, show, box, pick); info — {title, rubric, roles,
    meta, focus, paper, ink}. layout — wipe | dissolve | split. → props для композиции Pair."""
    sizes = [_size(x) for x in images]
    out, clips, voice_end = _timeline(beats, voice)
    FR = (0.0, 0.0, float(W), float(H))
    R = _pair_rect(sizes) if layout in ("wipe", "dissolve") else FR
    split = _split_rects(sizes)
    focus = info.get("focus") or [[0.5, 0.5], [0.5, 0.5]]

    def state(i, b):
        """Ключ обеих картинок для части b: [[x, y, w, h, cx, cy, cw, op, clip, lab], …]."""
        show, box = b.get("show") or "a", b.get("box")
        lab = 0.0 if b["kind"] == "hook" else 1.0
        res = []
        for n in (0, 1):
            pw, ph = sizes[n]
            me = "ab"[n]
            if layout == "split":
                if show == "both":
                    x, y, w, h = split[n]
                    op = 1.0 if not b.get("pick") or b["pick"] == me else 0.28
                    res.append([*FR, *_view_at(pw, ph, x, y, w, h), op, 0.0, lab])
                elif show == me:
                    win = _hook_win(b["text"], info.get("lang") or "en") if b["kind"] == "hook" else WIN
                    view = _fit(pw, ph, box, win, HOOK_SLACK if b["kind"] == "hook" else SLACK) if box else _full(pw, ph)
                    res.append([*FR, *view, 1.0, 0.0, 0.0])
                else:
                    x, y, w, h = split[n]
                    res.append([*FR, *_view_at(pw, ph, x, y, w, h), 0.0, 0.0, 0.0])
            else:
                on_box = box if show == me else None
                view = _cover_view(pw, ph, R, focus[n], on_box)
                if n == 0:
                    res.append([*R, *view, 1.0, 0.0, lab])
                elif layout == "dissolve":
                    res.append([*R, *view, 1.0 if show in ("b", "both") else 0.0, 0.0, lab])
                else:
                    clip = {"a": 1.0, "b": 0.0, "both": 0.5}[show if show in ("a", "b", "both") else "a"]
                    res.append([*R, *view, 1.0, clip, lab])
        return res

    TR = PAIR_TR.get(layout, 1.0)
    keys = [[], []]
    states = [state(i, b) for i, b in enumerate(beats)]
    # первый кадр — чуть шире и сразу наезд
    s0 = states[0]
    for n in (0, 1):
        first = list(s0[n])
        if layout == "split":
            first[6] = s0[n][6] * HOOK_PUSH           # чуть шире — и наезд
        else:
            s0[n][6] = s0[n][6] / 1.07                # картинка закрывает окно целиком — наезд только внутрь
        keys[n].append([0.0, *first])
        keys[n].append([HOOK_PUSH_T, *s0[n]])
    moves = []
    last_end = HOOK_PUSH_T
    for i in range(1, len(beats)):
        a_st, b_st = states[i - 1], states[i]
        o = out[i]
        changed = any(abs(x - y) > 1e-3 for n in (0, 1) for x, y in zip(a_st[n], b_st[n]))
        # до перехода — медленный наезд на прошлом состоянии
        t0 = max(last_end + 0.3, o["arrive"] - TR * 0.55)
        for n in (0, 1):
            held = list(a_st[n])
            if layout != "split" or beats[i - 1].get("show") != "both":
                held[6] = held[6] / HOLD_ZOOM
            keys[n].append([t0, *held])
        if changed:
            t1 = t0 + TR
            for n in (0, 1):
                keys[n].append([t1, *b_st[n]])
            moves.append((t0, t1))
            last_end = t1
        else:
            last_end = t0
        # сравнение не наезжает; последнее состояние держим до конца рассказа
    end_start = max(out[-1]["end"], voice_end) + 0.3
    for n in (0, 1):
        held = list(states[-1][n])
        if not (layout == "split" and beats[-1].get("show") == "both"):
            held[6] = held[6] / HOLD_ZOOM
        keys[n].append([max(end_start, keys[n][-1][0] + 0.3), *held])
    # титр: обе картинки рядом, под ними — подписи; потом возврат к первому кадру
    ends = _end_rects(sizes)
    t_end = max(end_start + 1.4, max(keys[0][-1][0], keys[1][-1][0]) + 0.8)
    for n in (0, 1):
        pw, ph = sizes[n]
        x, y, w, h = ends[n]
        keys[n].append([t_end, *FR, *_view_at(pw, ph, x, y, w, h), 1.0, 0.0, 0.0])
    loop_start = None
    if LOOP:
        loop_start = max(end_start + LABEL_HOLD, t_end + 1.4)
        for n in (0, 1):
            keys[n].append([loop_start, *keys[n][-1][1:]])
            keys[n].append([loop_start + LOOP_BACK, *keys[n][0][1:]])
        dur = loop_start + LOOP_BACK
    else:
        dur = end_start + 4.0
    label_top = round(max(y + h for x, y, w, h in ends) + 70)

    reveal_n = 0
    for b, o in zip(beats, out):
        if b["kind"] == "reveal":
            reveal_n += 1
            o["label"] = b.get("label") or ""
            o["n"] = reveal_n
    ev = [((m0 + m1) / 2, "move", True) for m0, m1 in moves] + [(end_start + 0.15, "close", False)]
    events = _sfx(folder, ev, str(images[0])) if sfx else []
    voice_path = None
    if clips:
        voice_path = folder / "voice_mix.wav"
        _mix_voice(clips, dur, voice_path)
    roles = info.get("roles") or ("A", "B")
    layers = []
    for n in (0, 1):
        pw, ph = sizes[n]
        l = {"src": str(images[n]), "pw": pw, "ph": ph, "tag": (info.get("tags") or roles)[n],
             "keys": [[round(v, 3) for v in k] for k in keys[n]]}
        if n == 0 and info.get("ink"):
            l.update(ink=[0.0, 2.6, 0.62], paper=info.get("paper") or "#EEE8DC")
        layers.append(l)
    return {"fps": FPS, "duration": round(dur * FPS), "layers": layers, "beats": out, "sfx": events, "layout": layout,
            "endStart": round(end_start, 3), "loopStart": round(loop_start, 3) if loop_start else None,
            "labelTop": label_top, "title": info.get("title") or "", "meta": info.get("meta") or [],
            "rubric": info.get("rubric") or "", "series": info.get("series") or info.get("title") or "",
            "coverT": round(max(0.8, out[0]["end"] - 0.35), 2),
            "voice": str(voice_path) if voice_path else None, "_dur": dur, "lang": info.get("lang") or "en"}


def align(a: Path, b: Path, dest: Path) -> tuple[Path, Path] | None:
    """Совместить B с A (две версии одной картины: до и после расчистки, картина и снимок того же кадра):
    поворот, масштаб и сдвиг по общим точкам (SIFT). Удалось — B, переложенная в кадр A (dest); нет — None,
    и пара показывается рядом, а не шторкой. Нужен opencv-python-headless."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    ia, ib = cv2.imread(str(a)), cv2.imread(str(b))
    if ia is None or ib is None:
        return None
    k = 1600 / max(ia.shape[:2])
    kb = 1600 / max(ib.shape[:2])
    ga = cv2.cvtColor(cv2.resize(ia, None, fx=k, fy=k, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(cv2.resize(ib, None, fx=kb, fy=kb, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(4000)
    pa, da = sift.detectAndCompute(ga, None)
    pb, db = sift.detectAndCompute(gb, None)
    if da is None or db is None or len(pa) < 30 or len(pb) < 30:
        return None
    pairs = cv2.BFMatcher().knnMatch(db, da, k=2)
    good = [m for m, n in (x for x in pairs if len(x) == 2) if m.distance < 0.72 * n.distance]
    if len(good) < 40:
        return None
    src = np.float32([pb[m.queryIdx].pt for m in good]) / kb
    dst = np.float32([pa[m.trainIdx].pt for m in good]) / k
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0 / k)
    if M is None or inl is None or int(inl.sum()) < 30 or int(inl.sum()) < 0.25 * len(good):
        return None
    h, w = ia.shape[:2]
    out = cv2.warpAffine(ib, M, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
    # общая часть двух кадров: обе картинки обрезаются по ней, чтобы у шторки не было пустых полос
    hb, wb = ib.shape[:2]
    c = cv2.transform(np.float32([[[0, 0], [wb, 0], [wb, hb], [0, hb]]]), M)[0]
    x0, x1 = int(max(c[0][0], c[3][0], 0)) + 3, int(min(c[1][0], c[2][0], w)) - 3
    y0, y1 = int(max(c[0][1], c[1][1], 0)) + 3, int(min(c[2][1], c[3][1], h)) - 3
    if x1 - x0 < 0.6 * w or y1 - y0 < 0.6 * h:
        return None
    da, db_ = dest.with_name(dest.stem + "_a.jpg"), dest.with_name(dest.stem + "_b.jpg")
    cv2.imwrite(str(da), ia[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 94])
    cv2.imwrite(str(db_), out[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 94])
    log.info("Пара: совмещено по %s точкам из %s", int(inl.sum()), len(good))
    return da, db_


def paper_color(path: Path) -> str:
    """Цвет бумаги чертежа — по светлым пикселям по краям."""
    import numpy as np
    with Image.open(path) as im:
        a = np.asarray(im.convert("RGB").resize((200, 200)), dtype=np.float32)
    edge = np.concatenate([a[:12].reshape(-1, 3), a[-12:].reshape(-1, 3), a[:, :12].reshape(-1, 3), a[:, -12:].reshape(-1, 3)])
    light = edge[edge.mean(axis=1) >= np.percentile(edge.mean(axis=1), 50)]
    r, g, b = [int(x) for x in light.mean(axis=0)]
    return f"#{r:02x}{g:02x}{b:02x}"


# ======================= подборка =======================
# Все работы подборки показываются одинаково: либо все на весь кадр (подборка вертикальных работ), либо все
# целиком, как на стене. Без наездов на детали. Смена работ — через короткое затемнение, кадры не накладываются.

BLEED = float(os.getenv("REEL_BLEED_MAX", "0.72"))   # все работы уже этого (ширина / высота) — подборка на весь кадр
BOX_W, BOX_TOP, BOX_BOTTOM = 936, 260, 1126          # поле картины; низ картины — на одной линии у всех работ
TEXT_TOP = 1190                                       # подпись — на одном месте у всех работ
RHYTHM = [1.0, 0.85, 1.15, 0.9, 1.1, 0.95]


def collection_mode(sizes: list[tuple[int, int]]) -> str:
    return "bleed" if sizes and all(w / h <= BLEED for w, h in sizes) else "frame"


def collection_beats(d: dict) -> list[dict]:
    """Части голоса подборки: вступление на титуле и строка на каждую работу. Нет строки хоть у одной — []."""
    its = d.get("items") or []
    if not d.get("intro") or not its or not all((it.get("line") or "").strip() for it in its):
        return []
    out = [{"kind": "hook", "raw": d["intro"], "text": d["intro"].replace("*", ""), "how": d.get("intro_delivery")}]
    for it in its:
        out.append({"kind": "reveal", "raw": it["line"], "text": it["line"].replace("*", ""), "how": it.get("delivery")})
    return out


def collection_props(folder: Path, d: dict, sfx: bool = True, voice=None) -> dict:
    sizes = [_size(Path(it["path"])) for it in d["items"]]
    mode = collection_mode(sizes)
    items, t = [], 0.0
    n_all = len(d["items"])
    bs = collection_beats(d)
    tl, clips, voice_end = _timeline(bs, voice) if bs else ([], [], 0.0)
    title_end = TITLE_HOLD
    if tl:
        # работа сменяется чуть раньше своей строки; титул — пока звучит вступление
        title_end = max(1.6, tl[1]["start"] - 0.3)
        starts = [0.0] + [tl[k + 1]["start"] - 0.3 for k in range(1, n_all)]
        last_end = max(voice_end, tl[-1]["end"]) + 1.0
    for n, (it, (pw, ph)) in enumerate(zip(d["items"], sizes)):
        if tl:
            nxt = starts[n + 1] if n + 1 < n_all else last_end
            dur = nxt - starts[n]
            t = starts[n]
        else:
            dur = SEG * (RHYTHM[(n - 1) % len(RHYTHM)] if n else 1.0) + (TITLE_HOLD if n == 0 else 0) \
                + (0.8 if n == n_all - 1 else 0)
        ru = d.get("lang") == "ru"            # в русской подборке — русские названия и имена, если Claude их дал
        row = {"image": it["path"], "pw": pw, "ph": ph, "start": round(t, 3), "dur": round(dur, 3),
               "title": (ru and it.get("title_ru")) or it.get("title") or "",
               "author": (ru and it.get("author_ru")) or it.get("author") or "", "year": str(it.get("year") or "")}
        if tl:
            row["line"] = tl[n + 1]["words"]
        if mode == "frame":
            k = min(BOX_W / pw, (BOX_BOTTOM - BOX_TOP) / ph)
            fw, fh = pw * k, ph * k
            row["frame"] = [round((W - fw) / 2, 1), round(BOX_BOTTOM - fh, 1), round(fw, 1), round(fh, 1)]
        items.append(row)
        t += dur
    if tl:
        t = last_end
    cap = re.split(r"(?<=[.!?])\s", (d.get("caption") or "").strip())[0] if d.get("caption") else ""
    ev = [(0.0, "page")] + [(it["start"] - 0.15, "page") for it in items[1:]]
    events = _sfx(folder, ev, d.get("title") or "") if sfx else []
    loop_start = t if LOOP else None
    dur = t + (LOOP_BACK if LOOP else 0.3)
    voice_path = None
    if clips:
        voice_path = folder / "voice_mix.wav"
        _mix_voice(clips, dur, voice_path)
    return {"fps": FPS, "duration": round(dur * FPS), "items": items, "mode": mode,
            "textTop": TEXT_TOP - (60 if tl else 0), "captionBottom": 500 if tl else 430,
            "titleEnd": round(title_end, 3), "title": d.get("title_em") or d.get("title") or "",
            "subtitle": cap if len(cap) <= 90 else "", "series": (d.get("title") or "").replace("*", ""),
            "intro": tl[0]["words"] if tl else None, "loopStart": round(loop_start, 3) if loop_start else None,
            "voice": str(voice_path) if voice_path else None, "coverT": 1.2,
            "sfx": events, "_dur": dur, "lang": d.get("lang") or "en"}


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
        for l in props.get("layers") or []:
            l["src"] = fix(l["src"])
        for e in props.get("sfx") or []:
            e["src"] = fix(e["src"])
        pfile = folder / f"props_{comp}.json"
        pfile.write_text(json.dumps(props, ensure_ascii=False))
        raw = out.with_name(out.stem + "_raw.mp4")
        cmd = ["npx", "remotion", "render", str(BUNDLE), comp, str(raw), f"--props={pfile}",
               f"--concurrency={CONCURRENCY}", "--crf=16", "--color-space=bt709", "--log=error"]
        if os.getenv("REMOTION_BROWSER"):
            cmd.append(f"--browser-executable={os.getenv('REMOTION_BROWSER')}")
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=int(os.getenv("REMOTION_TIMEOUT", "1800")))
        if r.returncode != 0 or not raw.exists():
            raise RuntimeError(f"Remotion: {(r.stderr or r.stdout)[-400:]}")
    export(raw, out)
    raw.unlink(missing_ok=True)
    return float(props.get("_dur") or 0)


# Экспорт под Instagram: 1080×1920, 30 к/с, H.264 High, битрейт 8–12 Мбит/с (ниже — мыло после пережатия Instagram,
# выше — Instagram жмёт сильнее), метки цвета BT.709 (без них видео после загрузки «выцветает»), AAC 48 кГц.
# Файл уходит в Telegram документом — до 50 МБ; не влез — второй проход с битрейтом пониже.
EXPORT_MAXRATE = os.getenv("REEL_MAXRATE", "12M")
TG_LIMIT = 49 * 1024 * 1024


def export(raw: Path, out: Path) -> None:
    exe = reelrender.ffmpeg_exe()
    has_audio = "Audio:" in subprocess.run([exe, "-hide_banner", "-i", str(raw)], capture_output=True, text=True).stderr
    for crf, maxrate in ((19, EXPORT_MAXRATE), (23, "7M")):
        cmd = [exe, "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
               "-crf", str(crf), "-maxrate", maxrate, "-bufsize", "24M", "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
        # голос уже выровнен в _mix_voice; здесь только ограничитель пиков — тихие звуки не вытягиваются
        cmd += (["-af", "alimiter=limit=0.89:level=disabled", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
                if has_audio else ["-an"])
        cmd += ["-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=1200)
        if out.stat().st_size <= TG_LIMIT:
            return


def cover(video: Path, t: float, dest: Path) -> Path | None:
    """Кадр для обложки: хук уже целиком на экране. Хук стоит ниже шапки, поэтому переживает обрезку сетки до 3:4."""
    try:
        subprocess.run([reelrender.ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
                        "-frames:v", "1", "-q:v", "2", str(dest)], check=True, capture_output=True, timeout=60)
        return dest if dest.exists() else None
    except Exception:
        log.warning("Обложка рилса не получилась", exc_info=True)
        return None
