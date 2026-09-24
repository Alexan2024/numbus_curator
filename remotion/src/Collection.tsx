import React from 'react';
import {AbsoluteFill, Sequence, Audio, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {Bar, Fonts, Grain, INK, M, MUTE, Rich, Stage, camAt, clamp, out3} from './common';

/* Подборка: титул (название во весь рост на первой работе), затем работы по несколько секунд —
   медленный наезд или проезд, счётчик «03 — 08», название антиквой, автор и год. Между работами — растворение. */

const XF = 0.5;   // растворение между работами, с

export const Collection: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const titleK = interpolate(t, [p.titleEnd - 0.4, p.titleEnd], [1, 0], clamp);
  const total = p.items.length;

  return <AbsoluteFill style={{background: '#0d0c0b', color: INK, overflow: 'hidden'}}>
    <Fonts />
    {p.items.map((it: any, i: number) => {
      if (t < it.start - XF || t > it.start + it.dur + XF) return null;
      const lt = t - it.start;
      const vis = i === 0 ? 1 : interpolate(lt, [-XF, 0], [0, 1], clamp);
      const textIn = interpolate(lt, [0.25, 0.9], [0, 1], out3);
      const textOut = interpolate(lt, [it.dur - 0.45, it.dur - 0.05], [1, 0], clamp);
      const k = Math.min(textIn, textOut);
      const showText = i > 0 || t >= p.titleEnd - 0.1;
      return <AbsoluteFill key={i} style={{opacity: vis}}>
        <Stage src={it.image} pw={it.pw} ph={it.ph} cam={camAt(it.cam, lt)} dim={0.3} />
        <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 520,
          background: 'linear-gradient(to bottom,rgba(8,7,6,.78),rgba(8,7,6,.35) 55%,rgba(8,7,6,0))'}} />
        <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 1050,
          background: 'linear-gradient(to top,rgba(8,7,6,.88),rgba(8,7,6,.62) 45%,rgba(8,7,6,0))'}} />
        {showText && <div style={{position: 'absolute', left: M, width: 860, bottom: 430,
          opacity: i === 0 ? Math.min(textOut, interpolate(t, [p.titleEnd, p.titleEnd + 0.6], [0, 1], out3)) : k,
          transform: `translateY(${(1 - (i === 0 ? 1 : textIn)) * 18}px)`}}>
          <div style={{fontFamily: 'Mono', fontSize: 21, letterSpacing: '.14em', color: MUTE, marginBottom: 26,
            display: 'flex', gap: 18, alignItems: 'center'}}>
            <span style={{color: INK}}>{String(i + 1).padStart(2, '0')}</span>
            <span style={{width: 46, height: 1, background: MUTE}} />
            <span>{String(total).padStart(2, '0')}</span>
          </div>
          <div style={{fontFamily: 'Serif', fontSize: it.title.length > 42 ? 72 : 84, lineHeight: .98,
            letterSpacing: '-.01em', textWrap: 'balance' as any}}><Rich text={it.title} /></div>
          <div style={{marginTop: 30, fontFamily: 'Grot', fontSize: 30, fontWeight: 450, color: MUTE}}>
            {it.author}{it.year ? <>&nbsp;&nbsp;·&nbsp;&nbsp;{it.year}</> : null}</div>
        </div>}
      </AbsoluteFill>;
    })}

    {/* титул */}
    {titleK > 0 && <AbsoluteFill style={{opacity: titleK}}>
      <div style={{position: 'absolute', inset: 0, background: 'rgba(8,7,6,.45)'}} />
      <div style={{position: 'absolute', left: M, top: 620, width: 900, fontFamily: 'Serif', fontSize: 150, lineHeight: .9,
        letterSpacing: '-.02em', textWrap: 'balance' as any,
        opacity: interpolate(t, [0.1, 0.8], [0, 1], out3), transform: `translateY(${(1 - interpolate(t, [0.1, 0.8], [0, 1], out3)) * 20}px)`}}>
        <Rich text={p.title} /></div>
      {p.subtitle && <div style={{position: 'absolute', left: M, top: 1040, width: 700, fontFamily: 'Grot', fontSize: 38,
        fontWeight: 450, lineHeight: 1.25, color: MUTE, textWrap: 'balance' as any,
        opacity: interpolate(t, [0.6, 1.3], [0, 1], out3)}}>{p.subtitle}</div>}
    </AbsoluteFill>}

    <Bar text={t < p.titleEnd ? `Collection · ${total} works` : p.series} />
    <Grain frame={frame} />
    {(p.sfx || []).map((e: any, i: number) => <Sequence key={i} from={Math.round(e.t * p.fps)}>
      <Audio src={staticFile(`sfx_${e.sfx}.wav`)} volume={e.vol} /></Sequence>)}
  </AbsoluteFill>;
};
