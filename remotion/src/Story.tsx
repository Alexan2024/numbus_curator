import React from 'react';
import {AbsoluteFill, Audio, Img, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {A, Bar, Fonts, Grain, H, INK, M, MUTE, Stage, W, camAt, clamp, out3} from './common';

/* «Детали картины»: хук на крупной детали, рассказ со словами по голосу, выноски у деталей,
   кульминация антиквой, финал — камера сама ужимает картину в этикетку. */

type Wd = {w: string; em: boolean; t: number};

// группа на экране — предложение; длинное делится на запятой после 7 слов или на 11-м слове
function groups(words: Wd[]) {
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

const Word: React.FC<{w: Wd; t: number; serif?: boolean; mute?: boolean; instant?: boolean; size?: number}> =
  ({w, t, serif, mute, instant, size}) => {
  const k = instant ? (t >= w.t - 0.02 ? 1 : 0) : interpolate(t, [w.t - 0.04, w.t + 0.24], [0, 1], out3);
  return <span style={{display: 'inline-block', opacity: k, transform: `translateY(${(1 - k) * 14}px)`,
    marginRight: '0.26em', color: w.em ? INK : mute ? MUTE : INK, fontFamily: w.em || serif ? 'Serif' : 'Grot',
    fontStyle: w.em ? 'italic' : 'normal', fontWeight: w.em || serif ? 400 : 480,
    fontSize: w.em && !serif && size ? size * 1.2 : undefined}}>{w.w}</span>;
};

export const Story: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const cam = camAt(p.cam, t); const [cx, cy, cw] = cam; const s = W / cw, ch = cw * A;
  const endK = interpolate(t, [p.endStart, p.endStart + 0.8], [0, 1], clamp);
  const beat = p.beats.find((b: any) => t >= b.arrive - 1.3 && t < b.end) || p.beats[p.beats.length - 1];
  const reveals = p.beats.filter((b: any) => b.kind === 'reveal');

  return <AbsoluteFill style={{background: '#0d0c0b', color: INK, overflow: 'hidden'}}>
    <Fonts />
    <Stage src={p.image} pw={p.pw} ph={p.ph} cam={cam} shadow={endK} dark={endK} />
    {/* затемнения под текст; на титре уходят */}
    <div style={{position: 'absolute', inset: 0, opacity: 1 - endK}}>
      <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: beat.kind === 'hook' ? 900 : 460,
        background: 'linear-gradient(to bottom,rgba(8,7,6,.82),rgba(8,7,6,.4) 55%,rgba(8,7,6,0))'}} />
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: beat.kind === 'climax' ? 1150 : 1000,
        background: 'linear-gradient(to top,rgba(8,7,6,.9),rgba(8,7,6,.62) 45%,rgba(8,7,6,0))'}} />
    </div>
    {/* выноски */}
    {p.beats.map((b: any, i: number) => {
      if (b.kind !== 'reveal' || !b.box) return null;
      const k = interpolate(t, [b.arrive, b.arrive + 0.4], [0, 1], out3);
      const fade = interpolate(t, [b.end - 0.35, b.end], [1, 0], clamp);
      if (k <= 0 || fade <= 0) return null;
      const x0 = (b.box[0] - (cx - cw / 2)) * s - 26, y0 = (b.box[1] - (cy - ch / 2)) * s - 26;
      const x1 = (b.box[2] - (cx - cw / 2)) * s + 26, y1 = (b.box[3] - (cy - ch / 2)) * s + 26;
      const arm = 42 * k, n = reveals.indexOf(b) + 1;
      const lab = interpolate(t, [b.arrive + 0.35, b.arrive + 0.8], [0, 1], out3);
      const lx = Math.max(M, Math.min(x0, W - 420)), ly = Math.min(Math.max(250, y0 - 150), 1300);
      return <div key={i} style={{position: 'absolute', inset: 0, opacity: fade}}>
        <svg width={W} height={H} style={{position: 'absolute', inset: 0}}>
          <g stroke={INK} strokeWidth={2.5} fill="none">
            <path d={`M${x0} ${y0 + arm} V${y0} H${x0 + arm}`} /><path d={`M${x1 - arm} ${y0} H${x1} V${y0 + arm}`} />
            <path d={`M${x0} ${y1 - arm} V${y1} H${x0 + arm}`} /><path d={`M${x1 - arm} ${y1} H${x1} V${y1 - arm}`} />
          </g>
          {y0 > ly + 60 && <line x1={lx} y1={ly + 44} x2={lx} y2={ly + 44 + (y0 - ly - 44) * lab}
            stroke="rgba(242,238,230,.7)" strokeWidth={1.5} />}
        </svg>
        {b.label && <div style={{position: 'absolute', left: lx, top: ly, fontFamily: 'Mono', fontSize: 22,
          letterSpacing: '.14em', textTransform: 'uppercase', display: 'flex', gap: 16, opacity: lab,
          transform: `translateX(${(1 - lab) * -16}px)`}}>
          <span style={{color: MUTE}}>{String(n).padStart(2, '0')}</span><span>{b.label}</span>
        </div>}
      </div>;
    })}

    <Bar text={p.beats.indexOf(beat) === 0 ? p.rubric : p.series} opacity={1 - endK} />

    {/* текст */}
    {p.beats.map((b: any, i: number) => {
      if (t < b.start - 0.1 || t >= b.end + 0.05 || endK >= 1) return null;
      const fade = interpolate(t, [b.end - 0.25, b.end], [1, 0], clamp);
      const gs = groups(b.words);
      if (b.kind === 'hook') {
        return <div key={i} style={{position: 'absolute', left: M, width: 900, top: 250, fontFamily: 'Serif',
          fontSize: 112, lineHeight: .96, letterSpacing: '-.012em', opacity: fade}}>
          {gs.map((g, gi) => <div key={gi}>{g.map((w, wi) => <Word key={wi} w={w} t={t} serif mute={gi > 0} instant />)}</div>)}
        </div>;
      }
      const climax = b.kind === 'climax';
      const gi = Math.max(0, gs.findIndex((g, j) => t < (gs[j + 1]?.[0].t ?? 1e9) - 0.02));
      return <div key={i} style={{position: 'absolute', left: M, width: climax ? 860 : 800, bottom: 440, opacity: fade,
        fontFamily: climax ? 'Serif' : 'Grot', fontSize: climax ? 80 : 48, lineHeight: climax ? 1.02 : 1.16,
        letterSpacing: climax ? '-.01em' : '-.008em'}}>
        {climax ? gs.map((g, j) => <div key={j}>{g.map((w, wi) => <Word key={wi} w={w} t={t} serif />)}</div>)
          : gs[gi].map((w, wi) => <Word key={wi} w={w} t={t} size={48} />)}
      </div>;
    })}

    {/* этикетка */}
    {endK > 0 && <AbsoluteFill style={{opacity: endK}}>
      <Bar text={p.rubric} />
      <div style={{position: 'absolute', left: M, top: p.labelTop, width: 936}}>
        <div style={{height: 1, background: 'rgba(242,238,230,.35)', marginBottom: 34,
          width: `${interpolate(t, [p.endStart + 1.0, p.endStart + 1.8], [0, 100], out3)}%`}} />
        {[<div key="a" style={{fontFamily: 'Serif', fontStyle: 'italic', fontSize: 86, lineHeight: 1}}>{p.title}</div>,
          <div key="b" style={{marginTop: 22, fontFamily: 'Grot', fontSize: 32, fontWeight: 450}}>{p.sub}</div>,
          <div key="c" style={{marginTop: 34, display: 'grid', gridTemplateColumns: '220px 1fr', rowGap: 12,
            fontFamily: 'Mono', fontSize: 21, letterSpacing: '.08em', textTransform: 'uppercase', color: MUTE}}>
            {(p.meta || []).map((m: string[]) => [<span key={m[0]}>{m[0]}</span>,
              <span key={m[0] + 'v'} style={{color: INK}}>{m[1]}</span>])}
          </div>].map((el, j) => {
          const k = interpolate(t, [p.endStart + 1.1 + j * .18, p.endStart + 1.7 + j * .18], [0, 1], out3);
          return <div key={j} style={{opacity: k, transform: `translateY(${(1 - k) * 14}px)`}}>{el}</div>;
        })}
      </div>
    </AbsoluteFill>}

    <Grain frame={frame} />
    {p.voice && <Audio src={p.voice} />}
    {(p.sfx || []).map((e: any, i: number) => <Sequence key={i} from={Math.round(e.t * p.fps)}>
      <Audio src={staticFile(`sfx_${e.sfx}.wav`)} volume={e.vol} /></Sequence>)}
  </AbsoluteFill>;
};
