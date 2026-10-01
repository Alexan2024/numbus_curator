import React from 'react';
import {interpolate} from 'remotion';
import {INK, M, MUTE, clamp, out3} from './common';

/* Текст рассказа — общий для «Деталей» (Story) и пар (Pair): хук крупной антиквой сверху, рассказ словами
   под голос внизу, над строкой — номер и имя детали, кульминация антиквой. */

export type Wd = {w: string; em: boolean; t: number};
export const HOOK_TOP = 290, TEXT_BOTTOM = 500;   // безопасные зоны Instagram: сверху шапка, снизу подпись и кнопки

// группа на экране — предложение; длинное делится на запятой после 7 слов или на 11-м слове
export function groups(words: Wd[]) {
  const out: Wd[][] = []; let cur: Wd[] = [];
  words.forEach((w) => {
    cur.push(w);
    if ((/[.!?…]$/.test(w.w) && cur.length >= 3) || (/[,;:—]$/.test(w.w) && cur.length >= 7) || cur.length >= 11) {
      out.push(cur); cur = [];
    }
  });
  if (cur.length) out.push(cur);
  return out;
}

export const Word: React.FC<{w: Wd; t: number; serif?: boolean; mute?: boolean; instant?: boolean; size?: number}> =
  ({w, t, serif, mute, instant, size}) => {
  const k = instant ? (t >= w.t - 0.1 ? 1 : 0) : interpolate(t, [w.t - 0.04, w.t + 0.24], [0, 1], out3);
  return <span style={{display: 'inline-block', opacity: k, transform: `translateY(${(1 - k) * 14}px)`,
    marginRight: '0.26em', color: w.em ? INK : mute ? MUTE : INK, fontFamily: w.em || serif ? 'AhSerif' : 'AhGrot',
    fontStyle: w.em ? 'italic' : 'normal', fontWeight: w.em || serif ? 400 : 480,
    fontSize: w.em && !serif && size ? size * 1.2 : undefined}}>{w.w}</span>;
};

// затемнения под текст; на титре уходят
export const Scrims: React.FC<{kind: string; opacity: number}> = ({kind, opacity}) =>
  <div style={{position: 'absolute', inset: 0, opacity}}>
    <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: kind === 'hook' ? 900 : 420,
      background: 'linear-gradient(to bottom,rgba(8,7,6,.82),rgba(8,7,6,.4) 55%,rgba(8,7,6,0))'}} />
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: kind === 'climax' ? 1000 : 900,
      background: 'linear-gradient(to top,rgba(8,7,6,.9),rgba(8,7,6,.6) 42%,rgba(8,7,6,0))'}} />
  </div>;

export const BeatText: React.FC<{b: any; t: number}> = ({b, t}) => {
  const fade = interpolate(t, [b.end - 0.25, b.end], [1, 0], clamp);
  const gs = groups(b.words);
  if (b.kind === 'hook') {
    return <div style={{position: 'absolute', left: M, width: 900, top: HOOK_TOP, fontFamily: 'AhSerif',
      fontSize: 112, lineHeight: .96, letterSpacing: '-.012em', opacity: fade}}>
      {gs.map((g, gi) => <div key={gi}>{g.map((w, wi) => <Word key={wi} w={w} t={t} serif mute={gi > 0} instant />)}</div>)}
    </div>;
  }
  const climax = b.kind === 'climax';
  const gi = Math.max(0, gs.findIndex((g, j) => t < (gs[j + 1]?.[0].t ?? 1e9) - 0.02));
  const lab = b.label ? interpolate(t, [b.arrive - 0.1, b.arrive + 0.45], [0, 1], out3) : 0;
  return <div style={{position: 'absolute', left: M, width: climax ? 860 : 820, bottom: TEXT_BOTTOM, opacity: fade}}>
    {b.label && <div style={{fontFamily: 'AhMono', fontSize: 22, letterSpacing: '.14em', textTransform: 'uppercase',
      display: 'flex', gap: 16, alignItems: 'center', marginBottom: 26, opacity: lab,
      transform: `translateX(${(1 - lab) * -14}px)`}}>
      <span style={{color: MUTE}}>{String(b.n).padStart(2, '0')}</span>
      <span style={{width: 34 * lab, height: 1, background: MUTE}} />
      <span>{b.label}</span>
    </div>}
    <div style={{fontFamily: climax ? 'AhSerif' : 'AhGrot', fontSize: climax ? 80 : 48, lineHeight: climax ? 1.02 : 1.16,
      letterSpacing: climax ? '-.01em' : '-.008em'}}>
      {climax ? gs.map((g, j) => <div key={j}>{g.map((w, wi) => <Word key={wi} w={w} t={t} serif />)}</div>)
        : (gs[gi] || []).map((w, wi) => <Word key={wi} w={w} t={t} size={48} />)}
    </div>
  </div>;
};

// все части рассказа, видимые в момент t
export const Narration: React.FC<{beats: any[]; t: number; hidden?: boolean}> = ({beats, t, hidden}) => hidden ? null :
  <>{beats.map((b: any, i: number) => (t < b.start - 0.1 || t >= b.end + 0.05) ? null : <BeatText key={i} b={b} t={t} />)}</>;

// этикетка в конце: рубрика, линия, название курсивом, подзаголовок, строки meta — по очереди
export const Label: React.FC<{t: number; start: number; top: number; title: string; sub?: string; meta?: string[][];
  metaCol?: number}> = ({t, start, top, title, sub, meta, metaCol = 220}) =>
  <div style={{position: 'absolute', left: M, top, width: 936}}>
    <div style={{height: 1, background: 'rgba(242,238,230,.35)', marginBottom: 34,
      width: `${interpolate(t, [start + 0.7, start + 1.5], [0, 100], out3)}%`}} />
    {[<div key="a" style={{fontFamily: 'AhSerif', fontStyle: 'italic', fontSize: title.length > 30 ? 70 : 86, lineHeight: 1}}>{title}</div>,
      sub ? <div key="b" style={{marginTop: 22, fontFamily: 'AhGrot', fontSize: 32, fontWeight: 450}}>{sub}</div> : null,
      <div key="c" style={{marginTop: 34, display: 'grid', gridTemplateColumns: `${metaCol}px 1fr`, rowGap: 12,
        fontFamily: 'AhMono', fontSize: 21, letterSpacing: '.08em', textTransform: 'uppercase', color: MUTE}}>
        {(meta || []).map((m: string[]) => [<span key={m[0]}>{m[0]}</span>,
          <span key={m[0] + 'v'} style={{color: INK}}>{m[1]}</span>])}
      </div>].filter(Boolean).map((el, j) => {
      const k = interpolate(t, [start + 0.8 + j * .18, start + 1.4 + j * .18], [0, 1], out3);
      return <div key={j} style={{opacity: k, transform: `translateY(${(1 - k) * 14}px)`}}>{el}</div>;
    })}
  </div>;
