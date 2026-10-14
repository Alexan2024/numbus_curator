import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {Fonts, INK, MUTE, Sfx, Stage, camState, clamp, clampCam, easeSine, out3} from './common';
import {Marks} from './Marks';
import type {Wd} from './Narration';

/* guide (8.1) — вёрстка по рефам автора: Cimmera, Paintings Analyzed, The Non Museum, Architectural Ethos.
   Только изображение и тихий текст. Картина на весь экран; камера спокойно плывёт от детали к детали под голос;
   субтитры — антиквой, по 3–6 слов, внизу по центру; маленький знак в углу. Ни строки с рубрикой, ни рамок,
   ни плашек, ни номеров. */

const SHADOW = '0 1px 3px rgba(0,0,0,.55), 0 2px 18px rgba(0,0,0,.5)';
// тонкая тёмная обводка (как у Cimmera) + мягкая тень: текст читается и на светлом холсте
const OUTLINE = ['-1.5px -1.5px 0', '1.5px -1.5px 0', '-1.5px 1.5px 0', '1.5px 1.5px 0', '0 2px 0', '0 -2px 0', '2px 0 0', '-2px 0 0']
  .map((x) => `${x} rgba(12,10,8,.78)`).join(', ') + ', 0 3px 16px rgba(0,0,0,.45)';
export const CAP_BOTTOM = 470;      // низ субтитров: выше подписи и кнопок Instagram

// куски субтитров: 3–6 слов, разрыв — на знаке препинания
export function gchunks(words: Wd[]): Wd[][] {
  const out: Wd[][] = []; let cur: Wd[] = [];
  const len = (g: Wd[]) => g.reduce((s, w) => s + w.w.length + 1, 0);
  words.forEach((w) => {
    cur.push(w);
    const stop = /[.!?…]$/.test(w.w), pause = /[,;:—]$/.test(w.w);
    if (stop || (pause && cur.length >= 3) || cur.length >= 7 || len(cur) >= 34) { out.push(cur); cur = []; }
  });
  if (cur.length) out.push(cur);
  // хвост из одного-двух коротких слов — к предыдущему куску
  if (out.length > 1 && len(out[out.length - 1]) <= 11 && len(out[out.length - 2]) <= 28
    && !/[.!?…]$/.test(out[out.length - 2][out[out.length - 2].length - 1].w)) {
    const last = out.pop()!; out[out.length - 1].push(...last);
  }
  return out;
}

export const GuideCaptions: React.FC<{beats: any[]; t: number; hidden?: boolean; size?: number}> =
  ({beats, t, hidden, size = 64}) => {
  if (hidden) return null;
  const b = beats.find((x: any) => t >= x.start - 0.05 && t < x.end + 0.05);
  if (!b || !b.words || !b.words.length) return null;
  const gs = gchunks(b.words);
  let gi = -1;
  gs.forEach((g, j) => { if (t >= g[0].t - 0.06) gi = j; });
  if (gi < 0) return null;
  const g = gs[gi];
  const next = gs[gi + 1] ? gs[gi + 1][0].t - 0.06 : b.end;
  const k = Math.min(interpolate(t, [g[0].t - 0.06, g[0].t + 0.1], [0, 1], clamp), interpolate(t, [next - 0.08, next], [1, 0], clamp));
  return <div style={{position: 'absolute', left: 80, right: 80, bottom: CAP_BOTTOM, textAlign: 'center', opacity: k,
    fontFamily: 'AhCap', fontSize: size, lineHeight: 1.1, letterSpacing: '-.018em', color: '#FFFFFF', textShadow: OUTLINE,
    textWrap: 'balance' as any}}>{g.map((w) => w.w).join(' ')}</div>;
};

// маленький знак AHMAG в углу, как подпись автора на ролике
export const Mark: React.FC<{opacity?: number}> = ({opacity = 0.6}) =>
  <Img src={staticFile('logo.png')} style={{position: 'absolute', left: 40, top: 1500, height: 22, opacity}} />;

/* ---------- разбор картины ---------- */
export const StoryGuide: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const st = camState(p.cam, t);
  const fix = (c: [number, number, number]) => clampCam(p.pw, p.ph, c, p.slack || [0, 0]);
  const cam = t < p.endStart ? fix(st.cam) : st.cam;
  const nx = camState(p.cam, t + 1 / p.fps).cam;
  const sc = 1080 / cam[2];
  const mb: [number, number] = [Math.min(5, Math.abs(nx[0] - cam[0]) * sc * 0.25), Math.min(5, Math.abs(nx[1] - cam[1]) * sc * 0.25)];
  const endK = interpolate(t, [p.endStart + 0.6, p.endStart + 1.3], [0, 1], out3);
  return <AbsoluteFill style={{background: '#000', color: INK, overflow: 'hidden'}}>
    <Fonts lang={p.lang} />
    <Stage src={p.image} pw={p.pw} ph={p.ph} cam={cam} blur={mb} />
    <Marks marks={p.marks} cam={cam} t={t} fade={1 - endK} />
    {/* лёгкая тень снизу — только чтобы читались субтитры на светлой картине */}
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 760, opacity: 1 - endK,
      background: 'linear-gradient(to top,rgba(0,0,0,.42),rgba(0,0,0,.16) 50%,rgba(0,0,0,0))'}} />
    <GuideCaptions beats={p.beats} t={t} hidden={t >= p.endStart} />
    {endK > 0 && <div style={{position: 'absolute', left: 80, right: 80, top: p.labelTop, textAlign: 'center', opacity: endK,
      transform: `translateY(${(1 - endK) * 10}px)`}}>
      <div style={{fontFamily: 'AhCap', fontSize: p.title.length > 34 ? 62 : 76, lineHeight: 1.02, textWrap: 'balance' as any}}>{p.title}</div>
      {p.sub && <div style={{marginTop: 18, fontFamily: 'AhGrot', fontSize: 30, fontWeight: 400, color: MUTE}}>{p.sub}</div>}
    </div>}
    <Mark opacity={0.55 * (1 - endK)} />
    {p.voice && <Audio src={p.voice} />}
    <Sfx list={p.sfx} fps={p.fps} />
  </AbsoluteFill>;
};

/* ---------- подборка, как у The Non Museum ---------- */
// работа на весь экран (кадрирование по фокусу), медленный наезд, склейка; название и автор — тонкими прописными
const coverStyle = (it: any, z: number): React.CSSProperties => {
  const ar = it.pw / it.ph, scr = 1080 / 1920;
  const fx = (it.focus || [0.5, 0.5])[0], fy = (it.focus || [0.5, 0.5])[1];
  // ширина картинки на экране при «cover»; сдвиг — чтобы фокус был как можно ближе к центру, без пустых краёв
  // на 3% больше экрана: край скана (рама, полоса фона) не попадает в кадр
  const w = (ar > scr ? 1920 * ar : 1080) * 1.03, h = (ar > scr ? 1920 : 1080 / ar) * 1.03;
  const left = Math.min(0, Math.max(1080 - w, 540 - fx * w)), top = Math.min(0, Math.max(1920 - h, 960 - fy * h));
  return {position: 'absolute', left, top, width: w, height: h, transformOrigin: `${(540 - left) / w * 100}% ${(960 - top) / h * 100}%`,
    transform: `scale(${z})`};
};

export const CollectionGuide: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const idx = Math.max(0, p.items.findIndex((it: any) => t >= it.start && t < it.start + it.dur));
  const it = p.items[idx] || p.items[0];
  const lt = t - it.start;
  const z = 1 + 0.06 * easeSine(lt / Math.max(it.dur, 0.1));
  const title = t < p.titleEnd;
  const k = title ? interpolate(t, [p.titleEnd - 0.35, p.titleEnd], [1, 0], clamp) : interpolate(lt, [0.15, 0.6], [0, 1], out3);
  return <AbsoluteFill style={{background: '#000', color: INK, overflow: 'hidden'}}>
    <Fonts lang={p.lang} />
    <Img src={it.image} style={coverStyle(it, z)} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 760,
      background: 'linear-gradient(to bottom,rgba(0,0,0,.5),rgba(0,0,0,.2) 55%,rgba(0,0,0,0))'}} />
    {title && <div style={{position: 'absolute', inset: 0, background: 'rgba(0,0,0,.2)', opacity: k}} />}
    {title ? <div style={{position: 'absolute', left: 70, right: 70, top: 640, textAlign: 'center', opacity: k,
      fontFamily: 'AhGrot', fontWeight: 400, fontSize: p.title.length > 22 ? 96 : 116, lineHeight: 1.0,
      letterSpacing: '.02em', textTransform: 'uppercase', textShadow: SHADOW, textWrap: 'balance' as any}}>
      {String(p.title).replace(/\*/g, '')}</div>
      : <div style={{position: 'absolute', left: 70, right: 70, top: 330, textAlign: 'center', opacity: k, textShadow: SHADOW}}>
        <div style={{fontFamily: 'AhGrot', fontWeight: 500, fontSize: 40, letterSpacing: '.06em', textTransform: 'uppercase',
          lineHeight: 1.12, textWrap: 'balance' as any}}>{it.title}</div>
        <div style={{marginTop: 10, fontFamily: 'AhGrot', fontWeight: 400, fontSize: 27, letterSpacing: '.03em'}}>{it.author}</div>
      </div>}
    {p.beats && p.beats.length ? <GuideCaptions beats={p.beats} t={t} size={56} /> : null}
    <Mark opacity={0.5} />
    {p.voice && <Audio src={p.voice} />}
    <Sfx list={p.sfx} fps={p.fps} />
  </AbsoluteFill>;
};

/* ---------- прогулка по зданию, как у Architectural Ethos ---------- */
// фото за фото на весь экран, у каждого своё медленное движение; один факт текстом в начале; в конце — что это
export const Visit: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const idx = Math.max(0, p.shots.findIndex((s: any) => t >= s.start && t < s.start + s.dur));
  const sh = p.shots[idx] || p.shots[0];
  const u = easeSine((t - sh.start) / Math.max(sh.dur, 0.1));
  const m = sh.move || 'in';
  // наезд, отъезд или проводка; при проводке картинка чуть крупнее экрана, чтобы края не открывались
  const pan = m === 'left' || m === 'right' || m === 'up';
  const z = pan ? 1.09 : m === 'out' ? 1.1 - 0.08 * u : 1.02 + 0.08 * u;
  const dx = m === 'left' ? 34 - 68 * u : m === 'right' ? -34 + 68 * u : 0;
  const dy = m === 'up' ? 34 - 68 * u : 0;
  const hookK = Math.min(interpolate(t, [0.1, 0.5], [0, 1], out3), interpolate(t, [p.hookEnd - 0.4, p.hookEnd], [1, 0], clamp));
  const endK = interpolate(t, [p.creditStart, p.creditStart + 0.5], [0, 1], out3);
  return <AbsoluteFill style={{background: '#000', color: INK, overflow: 'hidden'}}>
    <Fonts lang={p.lang} />
    <div style={{position: 'absolute', inset: 0, transform: `translate(${dx}px, ${dy}px)`}}>
      <Img src={sh.image} style={coverStyle(sh, z)} />
    </div>
    {/* лёгкое затемнение, пока на кадре текст: факт в начале, название в конце */}
    <div style={{position: 'absolute', inset: 0, background: 'rgba(0,0,0,.28)', opacity: Math.max(hookK, endK)}} />
    {hookK > 0 && <div style={{position: 'absolute', left: 90, right: 90, top: 700, textAlign: 'center', opacity: hookK,
      fontFamily: 'AhGrot', fontWeight: 500, fontSize: 58, lineHeight: 1.1, letterSpacing: '-.012em', textShadow: SHADOW,
      textWrap: 'balance' as any}}>{p.hook}</div>}
    {endK > 0 && <div style={{position: 'absolute', left: 90, right: 90, top: 760, textAlign: 'center', opacity: endK,
      textShadow: OUTLINE}}>
      <div style={{fontFamily: 'AhCap', fontSize: 84, lineHeight: 1.02, textWrap: 'balance' as any}}>{p.title}</div>
      {p.sub && <div style={{marginTop: 16, fontFamily: 'AhGrot', fontSize: 32, fontWeight: 500, textShadow: SHADOW}}>{p.sub}</div>}
    </div>}
    <Mark opacity={0.5} />
    {p.voice && <Audio src={p.voice} />}
    <Sfx list={p.sfx} fps={p.fps} />
  </AbsoluteFill>;
};
