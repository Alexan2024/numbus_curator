import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, useCurrentFrame} from 'remotion';
import {Bar, Fonts, Grain, H, INK, Sfx, W, clamp, easeSine} from './common';
import {Label, Narration, Scrims} from './Narration';

/* Две картинки и переход между ними: «Чертёж → здание», «Тогда / сейчас», «Картина и место», «Кадр ← картина»,
   «Что под слоем», «Какая из двух». Вся раскладка считается в боте (app/reelplan.py → pair_props): у каждой
   картинки — ключи [t, x, y, w, h, cx, cy, cw, видимость, шторка, ярлык]. x, y, w, h — окно на экране, вне
   которого картинка обрезана; cx, cy, cw — камера, как в «Деталях»: какая точка картинки в центре экрана и сколько
   её пикселей укладывается в ширину экрана. Шторка — доля ширины окна слева, под которой картинка B ещё закрыта.
   Между ключами — плавно, по синусу; ширина кадра — по логарифму. Чертёж может «проявиться тушью»: сначала
   самые тёмные линии, потом всё остальное (ink). */

type Key = number[];
const F = {t: 0, x: 1, y: 2, w: 3, h: 4, cx: 5, cy: 6, cw: 7, op: 8, clip: 9, lab: 10};

function at(keys: Key[], t: number): Key {
  if (t <= keys[0][0]) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t <= b[0]) {
      const u = easeSine((t - a[0]) / Math.max(b[0] - a[0], 1e-6));
      return a.map((v, j) => j === F.cw ? Math.exp(Math.log(v) + (Math.log(b[j]) - Math.log(v)) * u) : v + (b[j] - v) * u);
    }
  }
  return keys[keys.length - 1];
}

const Layer: React.FC<{l: any; k: Key; t: number; id: string}> = ({l, k, t, id}) => {
  const [, x, y, w, h, cx, cy, cw, op, clip] = k;
  if (op <= 0.001 || clip >= 0.999 || w < 2 || h < 2) return null;
  const s = W / cw;
  const ink = l.ink && t < l.ink[1] + 0.05;
  const thr = ink ? interpolate(t, [l.ink[0], l.ink[1]], [l.ink[2] ?? 0.3, 1.3], clamp) : 1.3;
  const kk = 4.2;
  return <div style={{position: 'absolute', left: x, top: y, width: w, height: h, overflow: 'hidden', opacity: op,
    clipPath: clip > 0.001 ? `inset(0 0 0 ${(clip * 100).toFixed(3)}%)` : undefined,
    }}>
    {ink && <svg width={0} height={0} style={{position: 'absolute'}}><filter id={id} colorInterpolationFilters="sRGB">
      <feColorMatrix type="matrix" values={`1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  ${(-kk * .2126).toFixed(4)} ${(-kk * .7152).toFixed(4)} ${(-kk * .0722).toFixed(4)} 0 ${(kk * thr).toFixed(4)}`} />
    </filter></svg>}
    {ink && <div style={{position: 'absolute', left: 0, top: 0, width: l.pw * s, height: l.ph * s, background: l.paper,
      transform: `translate3d(${W / 2 - cx * s - x}px, ${H / 2 - cy * s - y}px, 0)`}} />}
    <Img src={l.src} style={{position: 'absolute', left: 0, top: 0, width: l.pw * s, height: l.ph * s,
      transform: `translate3d(${W / 2 - cx * s - x}px, ${H / 2 - cy * s - y}px, 0)`,
      filter: ink ? `url(#${id})` : undefined, boxShadow: ink ? undefined : '0 24px 70px rgba(0,0,0,.5)'}} />
  </div>;
};

// ярлык над картинкой в сравнении: «A», «Then», «Painting, 1563»
const Tag: React.FC<{text: string; k: Key; l: any; right?: boolean}> = ({text, k, l, right}) => {
  const [, x, y, w, h, cx, cy, cw, op, , lab] = k;
  if (!text || lab <= 0.01 || op <= 0.01) return null;
  // угол видимой части картинки: пересечение окна и самой картинки на экране
  const s = W / cw, ix = W / 2 - cx * s, iy = H / 2 - cy * s;
  const L = Math.max(x, ix), T = Math.max(y, iy), R = Math.min(x + w, ix + l.pw * s);
  void h;
  return <div style={{position: 'absolute', top: T + 22, ...(right ? {left: R - 22, transform: 'translateX(-100%)'} : {left: L + 22}),
    opacity: lab * op, fontFamily: 'AhMono', fontSize: 21, letterSpacing: '.14em', textTransform: 'uppercase', color: INK,
    background: 'rgba(8,7,6,.55)', padding: '8px 14px 7px'}}>{text}</div>;
};

export const Pair: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const ks = p.layers.map((l: any) => at(l.keys, t));
  const endIn = interpolate(t, [p.endStart, p.endStart + 0.8], [0, 1], clamp);
  const endK = p.loopStart ? Math.min(endIn, interpolate(t, [p.loopStart, p.loopStart + 0.45], [1, 0], clamp)) : endIn;
  const loop = p.loopStart ? t >= p.loopStart : false;
  const beat = p.beats.find((b: any) => t >= b.start - 0.1 && t < b.end) || p.beats[p.beats.length - 1];
  const text = t < p.endStart;
  const b = ks[1];
  const wipe = b[F.clip] > 0.002 && b[F.clip] < 0.998 && b[F.op] > 0.05;
  return <AbsoluteFill style={{background: '#0d0c0b', color: INK, overflow: 'hidden'}}>
    <Fonts lang={p.lang} />
    {p.layers.map((l: any, i: number) => <Layer key={i} l={l} k={ks[i]} t={t} id={`ink${i}`} />)}
    {wipe && <div style={{position: 'absolute', left: b[F.x] + b[F.clip] * b[F.w] - 1, top: b[F.y], width: 2, height: b[F.h],
      background: INK, opacity: .9 * b[F.op], boxShadow: '0 0 12px rgba(0,0,0,.45)'}} />}
    {/* ярлык A прячется, когда B целиком закрывает её в том же окне; ярлык B — пока шторка не открыла B */}
    <Tag text={p.layers[0].tag} l={p.layers[0]} k={(() => {
      const a = ks[0], same = p.layout !== 'split' && Math.abs(a[F.x] - b[F.x]) + Math.abs(a[F.y] - b[F.y]) + Math.abs(a[F.w] - b[F.w]) < 2;
      const hid = same ? Math.min(b[F.op], 1 - b[F.clip] * 2) : 0;
      return a.map((v, j) => j === F.lab ? v * Math.max(0, 1 - Math.max(0, hid) * 1.5) : v);
    })()} />
    <Tag text={p.layers[1].tag} l={p.layers[1]} k={b.map((v, j) => j === F.lab ? v * Math.max(0, Math.min(1, (0.97 - b[F.clip]) * 4)) : v)}
      right={b[F.clip] > 0.02} />
    <Scrims kind={loop ? 'hook' : beat.kind} opacity={1 - endK} />
    <Bar text={p.beats.indexOf(beat) === 0 || loop ? p.rubric : p.series} opacity={1 - endK} />
    <Narration beats={p.beats} t={t} hidden={!text} />
    {endK > 0 && <AbsoluteFill style={{opacity: endK}}>
      <Bar text={p.rubric} />
      <Label t={t} start={p.endStart} top={p.labelTop} title={p.title} meta={p.meta} metaCol={170} />
    </AbsoluteFill>}
    <Grain frame={frame} />
    {p.voice && <Audio src={p.voice} />}
    <Sfx list={p.sfx} fps={p.fps} />
  </AbsoluteFill>;
};
