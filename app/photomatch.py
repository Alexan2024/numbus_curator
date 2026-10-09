"""Тот же ли это снимок: сравнение фото, которое пережило кадрирование, уменьшение, пересжатие и логотип.

Нужно архиву (app/archive.py): у старой записи сайта или поста Instagram есть только мелкая копия фото,
а на странице источника — оригинал. Сравнение по особым точкам (ORB) и проверка, что одна картинка
переходит в другую простым сдвигом и масштабом (без поворота и перспективы): так совпадает именно тот же
кадр, а не другой снимок того же здания с соседней точки."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

SIDE = 900            # сравниваем копии не больше 900 px по длинной стороне
FEATURES = 2000
MIN_INLIERS = 30      # совпавших точек после проверки геометрии — меньше считаем разными кадрами
MIN_SHARE = 0.6       # и не меньше этой доли от хороших пар: у того же кадра сходится почти всё (0.85–0.96)
MAX_ROT = 1.0         # градусов: тот же кадр не повёрнут
MIN_COVER = 2         # совпавшие точки лежат хотя бы в 2 клетках сетки 4×4 (минимализм с небом — точки только по кромке)
INSIDE = 0.9          # такая доля мелкой копии должна лежать внутри кандидата


@dataclass
class Feat:
    kp: np.ndarray       # координаты точек, N×2 (в px копии)
    des: np.ndarray      # дескрипторы ORB
    w: int
    h: int


def _gray(src) -> np.ndarray:
    im = src if isinstance(src, Image.Image) else Image.open(src)
    im = ImageOps.exif_transpose(im).convert("L")
    im.thumbnail((SIDE, SIDE), Image.LANCZOS)
    return np.asarray(im)


def features(src) -> Feat | None:
    """Особые точки фото (путь или открытая картинка). Однотонная картинка без деталей — None."""
    g = _gray(src)
    orb = cv2.ORB_create(nfeatures=FEATURES, scaleFactor=1.2, nlevels=8, fastThreshold=10)
    kp, des = orb.detectAndCompute(g, None)
    if des is None or len(kp) < 20:
        return None
    return Feat(np.float32([k.pt for k in kp]), des, g.shape[1], g.shape[0])


def score(a: Feat | None, b: Feat | None, min_cover: float = 0.7) -> int:
    """Сколько точек подтверждают, что a и b — один кадр (0 — разные). a — мелкая копия, b — кандидат.
    Рамка: мелкая копия почти целиком (от INSIDE) лежит внутри кандидата — значит, кандидат не вырезанный кусок
    и не соседний ракурс; и занимает не меньше min_cover его площади — значит, кандидат не коллаж и не более
    широкий кадр. Для ленты сайта (весь кадр) — 0.7, для Instagram (кадр 4:5 из горизонтального) — 0.4."""
    if a is None or b is None:
        return 0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    try:
        pairs = bf.knnMatch(a.des, b.des, k=2)
    except cv2.error:
        return 0
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.78 * p[1].distance]
    if len(good) < MIN_INLIERS:
        return 0
    src = np.float32([a.kp[m.queryIdx] for m in good])
    dst = np.float32([b.kp[m.trainIdx] for m in good])
    M, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0,
                                          maxIters=3000, confidence=0.995)
    if M is None or mask is None:
        return 0
    inl = int(mask.sum())
    if inl < MIN_INLIERS or inl < MIN_SHARE * len(good):
        return 0
    s = float(np.hypot(M[0, 0], M[1, 0]))                 # масштаб
    rot = abs(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    if rot > MAX_ROT or s <= 0:
        return 0
    pts = src[mask.ravel().astype(bool)]
    cells = {(min(3, int(x * 4 / a.w)), min(3, int(y * 4 / a.h))) for x, y in pts}
    if len(cells) < MIN_COVER:
        return 0
    # рамка мелкой копии в координатах кандидата (поворота нет — прямоугольник)
    x0, y0 = float(M[0, 2]), float(M[1, 2])
    x1, y1 = x0 + a.w * s, y0 + a.h * s
    area = (x1 - x0) * (y1 - y0)
    inter = max(0.0, min(x1, b.w) - max(x0, 0.0)) * max(0.0, min(y1, b.h) - max(y0, 0.0))
    if area <= 0 or inter / area < INSIDE or inter / (b.w * b.h) < min_cover:
        return 0
    return inl


def ranked(refs: list[Feat | None], cands: list[Feat | None], min_cover: float = 0.7) -> dict[int, list[int]]:
    """Для каждого фото — кандидаты, которые с ним совпали, от лучшего к худшему. → {номер фото: [номера]}"""
    out: dict[int, list[tuple[int, int]]] = {}
    for r, rf in enumerate(refs):
        if rf is None:
            continue
        for c, cf in enumerate(cands):
            sc = score(rf, cf, min_cover)
            if sc:
                out.setdefault(r, []).append((sc, c))
    return {r: [c for _, c in sorted(v, reverse=True)] for r, v in out.items()}


def any_match(refs: list[Feat | None], cands: list[Feat | None], min_cover: float = 0.4) -> bool:
    return any(score(r, c, min_cover) for r in refs if r is not None for c in cands if c is not None)


def load_all(paths: list) -> list[Feat | None]:
    out = []
    for p in paths:
        try:
            out.append(features(p))
        except Exception:
            out.append(None)
    return out


def path_ok(p) -> bool:
    return bool(p) and Path(p).exists()
