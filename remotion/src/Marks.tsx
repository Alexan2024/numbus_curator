import React from 'react';
import {interpolate} from 'remotion';
import {ACCENT, H, INK, W, out3} from './common';

/* Линии поверх снимка: «Разбор здания» (ось, уровень, сетка, контур, диагонали) и кольцо вокруг человека
   в «Масштабе». Координаты — в пикселях картинки, линии едут вместе с камерой. Линия прорисовывается
   от начала к концу — как рукой по кальке. Прошлые линии остаются, но тише: схема складывается. */

type Pt = [number, number];
const RED = '#E2492B';   // правка архитектора: красная линия по фото

const len = (a: Pt, b: Pt) => Math.hypot(b[0] - a[0], b[1] - a[1]);

// ломаные, по которым идёт прорисовка; k — доля общей длины
function partial(lines: Pt[][], k: number): Pt[][] {
  const total = lines.reduce((s, l) => s + l.slice(1).reduce((q, p, i) => q + len(l[i], p), 0), 0);
  let left = total * Math.min(1, Math.max(0, k));
  const out: Pt[][] = [];
  for (const l of lines) {
    if (left <= 0) break;
    const cur: Pt[] = [l[0]];
    for (let i = 1; i < l.length && left > 0; i++) {
      const d = len(l[i - 1], l[i]);
      if (d <= left) { cur.push(l[i]); left -= d; } else {
        const u = left / d; cur.push([l[i - 1][0] + (l[i][0] - l[i - 1][0]) * u, l[i - 1][1] + (l[i][1] - l[i - 1][1]) * u]);
        left = 0;
      }
    }
    out.push(cur);
  }
  return out;
}

function shape(m: any, sx: (x: number) => number, sy: (y: number) => number): {lines: Pt[][]; dash?: string} {
  const x0 = sx(m.x0), x1 = sx(m.x1), y0 = sy(m.y0), y1 = sy(m.y1);
  const rect: Pt[] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
  const ex = (y1 - y0) * 0.06, ey = (x1 - x0) * 0.06;
  switch (m.type) {
    case 'axis': { const cx = (x0 + x1) / 2; return {lines: [[[cx, y1 + ex], [cx, y0 - ex]]], dash: '26 9 4 9'}; }
    case 'level': { const cy = (y0 + y1) / 2; return {lines: [[[x0 - ey, cy], [x1 + ey, cy]]], dash: '26 9 4 9'}; }
    case 'grid': {
      const c = Math.max(1, m.cols || 1), r = Math.max(1, m.rows || 1), ls: Pt[][] = [rect];
      for (let i = 1; i < c; i++) { const x = x0 + (x1 - x0) * i / c; ls.push([[x, y0], [x, y1]]); }
      for (let j = 1; j < r; j++) { const y = y0 + (y1 - y0) * j / r; ls.push([[x0, y], [x1, y]]); }
      return {lines: ls};
    }
    case 'diagonals': return {lines: [rect, [[x0, y0], [x1, y1]], [[x1, y0], [x0, y1]]]};
    default: return {lines: [rect]};
  }
}

export const Marks: React.FC<{marks?: any[]; cam: [number, number, number]; t: number; fade: number}> =
  ({marks, cam, t, fade}) => {
  if (!marks || !marks.length || fade <= 0) return null;
  const [cx, cy, cw] = cam; const s = W / cw;
  const sx = (x: number) => W / 2 + (x - cx) * s, sy = (y: number) => H / 2 + (y - cy) * s;
  return <svg width={W} height={H} style={{position: 'absolute', left: 0, top: 0, opacity: fade}}>
    {marks.map((m: any, i: number) => {
      if (t < m.start || t > m.end) return null;
      const k = interpolate(t, [m.start, m.start + (m.draw || 1.1)], [0, 1], out3);
      const op = Math.min(k * 3, 1) * (t < m.active ? 1 : 0.55) * interpolate(t, [m.end - 0.35, m.end], [1, 0], out3);
      if (m.type === 'figure') {
        // кольцо вокруг человека: при отъезде камеры не даёт потерять его из виду
        const rx = Math.max(26, (sx(m.x1) - sx(m.x0)) * 0.9), ry = Math.max(34, (sy(m.y1) - sy(m.y0)) * 0.75);
        const fx = (sx(m.x0) + sx(m.x1)) / 2, fy = (sy(m.y0) + sy(m.y1)) / 2;
        const right = fx < W * 0.62;
        return <g key={i} opacity={op}>
          <ellipse cx={fx} cy={fy} rx={rx * (1.25 - 0.25 * k)} ry={ry * (1.25 - 0.25 * k)} fill="none"
            stroke="rgba(8,7,6,.55)" strokeWidth={6} />
          <ellipse cx={fx} cy={fy} rx={rx * (1.25 - 0.25 * k)} ry={ry * (1.25 - 0.25 * k)} fill="none" stroke={INK}
            strokeWidth={2.4} />
          {m.label && <>
            <line x1={fx + (right ? rx : -rx)} y1={fy} x2={fx + (right ? rx + 44 : -rx - 44)} y2={fy} stroke={INK} strokeWidth={1.4} />
            <text x={fx + (right ? rx + 56 : -rx - 56)} y={fy + 8} fill={INK} fontFamily="AhGrot" fontWeight={500} fontSize={27}
              textAnchor={right ? 'start' : 'end'}>{m.label}</text>
          </>}
        </g>;
      }
      if (m.type === 'focus') {
        // pulse: уголки видоискателя вокруг детали и ярлык «01 · Письмо» — куда смотреть
        const x0 = sx(m.x0), x1 = sx(m.x1), y0 = sy(m.y0), y1 = sy(m.y1);
        const fx = (x0 + x1) / 2, fy = (y0 + y1) / 2, z = 1.16 - 0.16 * k;
        const X0 = fx + (x0 - fx) * z, X1 = fx + (x1 - fx) * z, Y0 = fy + (y0 - fy) * z, Y1 = fy + (y1 - fy) * z;
        const c = Math.max(26, Math.min(86, Math.min(X1 - X0, Y1 - Y0) * 0.24));
        const L = [[[X0, Y0 + c], [X0, Y0], [X0 + c, Y0]], [[X1 - c, Y0], [X1, Y0], [X1, Y0 + c]],
          [[X1, Y1 - c], [X1, Y1], [X1 - c, Y1]], [[X0 + c, Y1], [X0, Y1], [X0, Y1 - c]]]
          .map((l) => l.map((q) => q.join(',')).join(' '));
        const below = Y0 < 420;
        const lx = Math.max(24, Math.min(X0, W - 420)), ly = below ? Math.min(Y1 + 16, H - 620) : Y0 - 58;
        return <g key={i} opacity={op}>
          {L.map((pt, j) => <polyline key={'h' + j} points={pt} fill="none" stroke="rgba(8,7,6,.5)" strokeWidth={11}
            strokeLinecap="square" />)}
          {L.map((pt, j) => <polyline key={j} points={pt} fill="none" stroke={ACCENT} strokeWidth={5.5} strokeLinecap="square" />)}
          {m.label && <foreignObject x={lx} y={ly} width={420} height={48}>
            <div style={{display: 'inline-block', background: ACCENT, color: INK, fontFamily: 'AhMono', fontWeight: 500,
              fontSize: 23, letterSpacing: '.12em', textTransform: 'uppercase', padding: '9px 14px 8px', lineHeight: 1,
              whiteSpace: 'nowrap', transform: `translateX(${(1 - k) * -10}px)`}}>{m.label}</div>
          </foreignObject>}
        </g>;
      }
      const sh = shape(m, sx, sy);
      const pts = partial(sh.lines, k).filter((l) => l.length > 1).map((l) => l.map((p) => p.join(',')).join(' '));
      // тёмная подложка под светлой линией: линия читается и на белом бетоне, и на тёмном камне
      return <g key={i} opacity={op}>
        {pts.map((pt, j) => <polyline key={'h' + j} points={pt} fill="none" stroke="rgba(8,7,6,.45)" strokeWidth={8}
          strokeDasharray={sh.dash} strokeLinecap="square" />)}
        {pts.map((pt, j) => <polyline key={j} points={pt} fill="none" stroke={RED} strokeWidth={3.6}
          strokeDasharray={sh.dash} strokeLinecap="square" />)}
      </g>;
    })}
  </svg>;
};
