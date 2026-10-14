import React from 'react';
import {AbsoluteFill, Img, interpolate} from 'remotion';
import {ACCENT, INK, M, clamp, out3} from './common';
import type {Wd} from './Narration';

/* pulse (8.0): вёрстка под ленту Instagram.
   Заголовок-хук — крупно сверху и целиком с первого кадра: его читают без звука, пока решают, листать ли дальше.
   Субтитры — по 2–4 слова в центре нижней трети, крупно; сказанные слова светлеют, ударное слово (*слово*)
   встаёт на красную плашку. Фон — та же картина, размытая: вокруг картины нет чёрных полей.
   Все размеры — с учётом зон Instagram: сверху шапка (до 280 px), снизу подпись и кнопки (последние 500 px). */

export const HEAD_TOP = 330, CAP_BOTTOM = 540;
const SHADOW = '0 2px 22px rgba(0,0,0,.55), 0 1px 3px rgba(0,0,0,.7)';

// *слово* → на красной плашке (в заголовке). Возвращает куски строки с признаком акцента
const parts = (text: string) => String(text || '').split(/(\*[^*]+\*)/).filter(Boolean)
  .map((x) => x.startsWith('*') && x.endsWith('*') ? {t: x.slice(1, -1), em: true} : {t: x, em: false});

const Accent: React.FC<{children: React.ReactNode; on?: number; serif?: boolean}> = ({children, on = 1, serif = true}) =>
  <span style={{position: 'relative', display: 'inline-block', fontFamily: serif ? 'AhSerif' : undefined,
    fontStyle: serif ? 'italic' : undefined, fontWeight: serif ? 400 : undefined, padding: '0 .12em', margin: '0 -.04em',
    textShadow: on > 0.5 ? 'none' : SHADOW}}>
    <span style={{position: 'absolute', left: 0, right: 0, top: '.12em', bottom: '.02em', background: ACCENT,
      transformOrigin: '0 50%', transform: `scaleX(${on})`, borderRadius: 3}} />
    <span style={{position: 'relative'}}>{children}</span>
  </span>;

// размер заголовка: длинное слово (особенно по-русски) не должно вылезать за край
export function headSize(text: string): number {
  const clean = String(text || '').replace(/\*/g, '');
  const longest = Math.max(...clean.split(/\s+/).map((w) => w.length), 1);
  const base = clean.length <= 20 ? 124 : clean.length <= 34 ? 108 : clean.length <= 50 ? 94 : 82;
  return Math.round(Math.min(base, 936 / (0.6 * longest)));
}

export const Headline: React.FC<{text: string; t: number; end: number; loopStart?: number | null; loopBack?: number}> =
  ({text, t, end, loopStart, loopBack = 0.5}) => {
  if (!text) return null;
  const out = interpolate(t, [end - 0.05, end + 0.3], [1, 0], clamp);
  const back = loopStart ? interpolate(t, [loopStart, loopStart + loopBack * 0.8], [0, 1], clamp) : 0;
  const k = Math.max(out, back);
  if (k <= 0.001) return null;
  // с кадра 0 — целиком; лёгкое «приземление» за четверть секунды (на кадре 0 уже читается)
  const land = t < 1 ? interpolate(t, [0, 0.28], [1.05, 1], out3) : 1;
  const size = headSize(text);
  const accentOn = interpolate(t, [0.12, 0.42], [0, 1], out3);
  return <div style={{position: 'absolute', left: M, width: 936, top: HEAD_TOP, opacity: k,
    transform: `translateY(${(1 - out) * -24 * (back > 0 ? 0 : 1)}px) scale(${land})`, transformOrigin: '0 0',
    fontFamily: 'AhGrot', fontWeight: 760, fontSize: size, lineHeight: 1.0, letterSpacing: '-.028em', color: INK,
    textShadow: SHADOW, textWrap: 'balance' as any}}>
    {parts(text).map((x, i) => x.em ? <Accent key={i} on={back > 0 ? 1 : accentOn}>{x.t}</Accent> : <span key={i}>{x.t}</span>)}
  </div>;
};

// субтитры: группы по 2–4 слова, разрыв — на знаке препинания или на длинной группе
export function chunks(words: Wd[]): Wd[][] {
  const out: Wd[][] = []; let cur: Wd[] = [];
  const len = (g: Wd[]) => g.reduce((s, w) => s + w.w.length + 1, 0);
  words.forEach((w) => {
    cur.push(w);
    const stop = /[.!?…]$/.test(w.w), pause = /[,;:—]$/.test(w.w);
    if ((stop && cur.length >= 1) || (pause && cur.length >= 2) || cur.length >= 4 || len(cur) >= 24) {
      out.push(cur); cur = [];
    }
  });
  if (cur.length) out.push(cur);
  // хвост из одного короткого слова — к предыдущей группе
  if (out.length > 1 && out[out.length - 1].length === 1 && out[out.length - 1][0].w.length <= 4
    && len(out[out.length - 2]) < 22) { const last = out.pop()!; out[out.length - 1].push(...last); }
  return out;
}

const CapWord: React.FC<{w: Wd; t: number; size: number}> = ({w, t, size}) => {
  const said = t >= w.t - 0.03;
  const on = interpolate(t, [w.t - 0.03, w.t + 0.16], [0, 1], out3);
  const style: React.CSSProperties = {display: 'inline-block', marginRight: '0.24em', opacity: said ? 1 : 0.42,
    transition: 'none'};
  if (w.em) {
    return <span style={{...style, fontSize: size * 1.08}}><Accent on={on}>{w.w}</Accent></span>;
  }
  return <span style={style}>{w.w}</span>;
};

export const Captions: React.FC<{beats: any[]; t: number; hidden?: boolean; labels?: boolean}> = ({beats, t, hidden, labels}) => {
  if (hidden) return null;
  const b = beats.find((x: any) => t >= x.start - 0.08 && t < x.end + 0.02);
  if (!b || !b.words || !b.words.length) return null;
  const gs = chunks(b.words);
  let gi = -1;
  gs.forEach((g, j) => { if (t >= g[0].t - 0.08) gi = j; });
  if (gi < 0) return null;
  const g = gs[gi];
  const t0 = g[0].t - 0.08;
  const pop = interpolate(t, [t0, t0 + 0.14], [0, 1], out3);
  const fade = interpolate(t, [b.end - 0.12, b.end + 0.02], [1, 0], clamp);
  const climax = b.kind === 'climax';
  const size = climax ? 88 : 76;
  return <div style={{position: 'absolute', left: M - 12, right: M - 12, bottom: CAP_BOTTOM, textAlign: 'center',
    fontFamily: climax ? 'AhSerif' : 'AhGrot', fontWeight: climax ? 400 : 720, fontSize: size, lineHeight: 1.08,
    letterSpacing: climax ? '-.01em' : '-.022em', color: INK, textShadow: SHADOW, opacity: fade * Math.min(1, pop * 1.6),
    transform: `translateY(${(1 - pop) * 18}px) scale(${0.94 + 0.06 * pop})`, transformOrigin: '50% 100%',
    textWrap: 'balance' as any}}>
    {labels && b.label && <div style={{marginBottom: 22}}><span style={{display: 'inline-block', background: ACCENT, color: INK,
      fontFamily: 'AhMono', fontWeight: 500, fontSize: 23, letterSpacing: '.12em', textTransform: 'uppercase',
      padding: '9px 14px 8px', lineHeight: 1, textShadow: 'none'}}>{String(b.n || 1).padStart(2, '0')} · {b.label}</span></div>}
    {g.map((w, i) => <CapWord key={i} w={w} t={t} size={size} />)}
  </div>;
};

// затемнения pulse: сверху — под заголовок (пока он виден), снизу — под субтитры; середина кадра открыта
export const PulseScrims: React.FC<{head: number}> = ({head}) => <>
  <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 820, opacity: 0.35 + 0.65 * head,
    background: 'linear-gradient(to bottom,rgba(8,7,6,.7),rgba(8,7,6,.36) 50%,rgba(8,7,6,0))'}} />
  <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 920,
    background: 'linear-gradient(to top,rgba(8,7,6,.6),rgba(8,7,6,.3) 45%,rgba(8,7,6,0))'}} />
</>;

// размытая картина на весь кадр вместо чёрного фона; чуть дышит вместе с камерой
export const Backdrop: React.FC<{src?: string | null; t: number}> = ({src, t}) => !src ? null :
  <AbsoluteFill>
    <Img src={src} style={{position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover',
      transform: `scale(${1.12 + 0.02 * Math.sin(t * 0.6)})`}} />
    <div style={{position: 'absolute', inset: 0, background: 'rgba(8,7,6,.12)'}} />
  </AbsoluteFill>;

// пик толчков камеры в момент t: быстрый подъём, мягкий спад
export function punchAt(punches: number[] | undefined, t: number, amp: number): number {
  let k = 0;
  for (const p of punches || []) {
    const d = t - p;
    if (d < -0.02 || d > 0.7) continue;
    const up = interpolate(d, [-0.02, 0.06], [0, 1], out3), down = interpolate(d, [0.06, 0.7], [1, 0], out3);
    k = Math.max(k, Math.min(up, down));
  }
  return 1 + amp * k;
}

