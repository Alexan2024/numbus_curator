import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, useCurrentFrame} from 'remotion';
import {Bar, Fonts, Grain, INK, M, MUTE, Rich, Sfx, clamp, easeSine, out3} from './common';
import {Word} from './Narration';

/* Подборка: титул на первой работе, затем работы по несколько секунд. Все работы подборки — в одном виде:
   mode = bleed — все на весь кадр (подборка вертикальных работ), mode = frame — все целиком, как на стене:
   низ картины на одной линии, подпись на одном месте. Без наездов на детали: работа видна вся.
   Смена — через короткое затемнение: старая уходит, потом появляется новая, кадры не накладываются.
   С голосом (5.5): на титуле звучит вступление, у каждой работы — своя строка (слова по голосу, под автором),
   работа сменяется, когда начинается её строка. В конце — возврат к титулу: повтор без стыка. */

const OUT = 0.3, IN = 0.45;   // уход и появление работы, с

// титул — 150 px; в русском длинное слово («Архитектурные») в такую строку не влезает — кегль меньше
const titleSize = (title: string, lang?: string) => {
  if (lang !== 'ru') return 150;
  const longest = Math.max(...String(title || '').replace(/\*/g, '').split(/\s+/).map((w) => w.length), 1);
  return Math.max(96, Math.min(150, Math.floor(900 / (0.5 * longest))));
};

const Line: React.FC<{words?: any[]; t: number}> = ({words, t}) => !words || !words.length ? null :
  <div style={{marginTop: 26, fontFamily: 'AhGrot', fontSize: 38, fontWeight: 460, lineHeight: 1.2, letterSpacing: '-.006em',
    maxWidth: 830}}>{words.map((w: any, j: number) => <Word key={j} w={w} t={t} size={38} />)}</div>;

const Caption: React.FC<{it: any; i: number; total: number; t: number}> = ({it, i, total, t}) => <>
  <div style={{fontFamily: 'AhMono', fontSize: 21, letterSpacing: '.14em', color: MUTE, marginBottom: 24,
    display: 'flex', gap: 18, alignItems: 'center'}}>
    <span style={{color: INK}}>{String(i + 1).padStart(2, '0')}</span>
    <span style={{width: 46, height: 1, background: MUTE}} />
    <span>{String(total).padStart(2, '0')}</span>
  </div>
  <div style={{fontFamily: 'AhSerif', fontSize: it.title.length > 42 ? 64 : 76, lineHeight: .98,
    letterSpacing: '-.01em', textWrap: 'balance' as any}}><Rich text={it.title} /></div>
  <div style={{marginTop: 22, fontFamily: 'AhGrot', fontSize: 30, fontWeight: 450, color: MUTE}}>
    {it.author}{it.year ? <>&nbsp;&nbsp;·&nbsp;&nbsp;{it.year}</> : null}</div>
  <Line words={it.line} t={t} />
</>;

export const Collection: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const titleK = interpolate(t, [p.titleEnd - 0.4, p.titleEnd], [1, 0], clamp);
  const total = p.items.length;
  const bleed = p.mode === 'bleed';
  const loopK = p.loopStart ? easeSine((t - p.loopStart) / Math.max(0.1, p.duration / p.fps - p.loopStart)) : 0;

  return <AbsoluteFill style={{background: '#0d0c0b', color: INK, overflow: 'hidden'}}>
    <Fonts lang={p.lang} />
    {p.items.map((it: any, i: number) => {
      const lt = t - it.start;
      if (lt < 0 || lt > it.dur) return null;
      const vis = Math.min(i === 0 ? 1 : easeSine(lt / IN), i === total - 1 ? 1 : easeSine((it.dur - lt) / OUT));
      const textIn = interpolate(lt, [0.35, 1.0], [0, 1], out3);
      const showText = i > 0 || t >= p.titleEnd - 0.1;
      const k = i === 0 ? interpolate(t, [p.titleEnd, p.titleEnd + 0.6], [0, 1], out3) : textIn;
      const lift = (1 - (i === 0 ? 1 : textIn)) * 14;
      // медленное приближение всей работы — как шаг к стене; работа остаётся видна целиком
      const z = 1 + (bleed ? 0.03 : 0.015) * easeSine(lt / it.dur);
      const caption = showText && <div style={{position: 'absolute', left: M, width: bleed ? 860 : 900,
        ...(bleed ? {bottom: p.captionBottom || 430} : {top: p.textTop}), opacity: k, transform: `translateY(${lift}px)`}}>
        <Caption it={it} i={i} total={total} t={t} /></div>;
      if (bleed) {
        return <AbsoluteFill key={i} style={{opacity: vis}}>
          <Img src={it.image} style={{position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover',
            transform: `scale(${z})`}} />
          <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 1000,
            background: 'linear-gradient(to top,rgba(8,7,6,.88),rgba(8,7,6,.6) 45%,rgba(8,7,6,0))'}} />
          <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: 420,
            background: 'linear-gradient(to bottom,rgba(8,7,6,.7),rgba(8,7,6,0))'}} />
          {caption}
        </AbsoluteFill>;
      }
      const [x, y, w, h] = it.frame;
      return <AbsoluteFill key={i} style={{opacity: vis}}>
        <Img src={it.image} style={{position: 'absolute', left: x, top: y, width: w, height: h,
          transformOrigin: '50% 100%', transform: `scale(${z})`, boxShadow: '0 24px 70px rgba(0,0,0,.55)'}} />
        {caption}
      </AbsoluteFill>;
    })}

    {/* возврат к титулу в конце: первая работа проявляется поверх последней */}
    {loopK > 0 && (() => {
      const it = p.items[0];
      if (bleed) return <AbsoluteFill style={{opacity: loopK}}><Img src={it.image} style={{position: 'absolute', inset: 0,
        width: '100%', height: '100%', objectFit: 'cover'}} /></AbsoluteFill>;
      const [x, y, w, h] = it.frame;
      return <AbsoluteFill style={{opacity: loopK, background: '#0d0c0b'}}><Img src={it.image} style={{position: 'absolute',
        left: x, top: y, width: w, height: h, boxShadow: '0 24px 70px rgba(0,0,0,.55)'}} /></AbsoluteFill>;
    })()}

    {/* титул */}
    {Math.max(titleK, loopK) > 0 && <AbsoluteFill style={{opacity: Math.max(titleK, loopK)}}>
      <div style={{position: 'absolute', inset: 0, background: 'rgba(8,7,6,.55)'}} />
      <div style={{position: 'absolute', left: M, top: 620, width: 900, fontFamily: 'AhSerif', fontSize: titleSize(p.title, p.lang), lineHeight: .9,
        letterSpacing: '-.02em', textWrap: 'balance' as any,
        /* титул виден с первого кадра: первый кадр — уже ролик */}}>
        <Rich text={p.title} /></div>
      {p.intro && p.intro.length && titleK > 0 ? <div style={{position: 'absolute', left: M, top: 1040, width: 820,
        fontFamily: 'AhGrot', fontSize: 44, fontWeight: 460, lineHeight: 1.2}}>
        {p.intro.map((w: any, j: number) => <Word key={j} w={w} t={t} size={44} />)}</div>
        : p.subtitle && titleK > 0 ? <div style={{position: 'absolute', left: M, top: 1040, width: 700, fontFamily: 'AhGrot', fontSize: 38,
        fontWeight: 450, lineHeight: 1.25, color: MUTE, textWrap: 'balance' as any,
        opacity: interpolate(t, [0.6, 1.3], [0, 1], out3)}}>{p.subtitle}</div> : null}
    </AbsoluteFill>}

    <Bar text={t < p.titleEnd || loopK > 0 ? (p.lang === 'ru' ? `Подборка · ${total} ${total % 10 >= 2 && total % 10 <= 4 && (total < 12 || total > 14) ? 'работы' : 'работ'}` : `Collection · ${total} works`) : p.series} />
    <Grain frame={frame} />
    {p.voice && <Audio src={p.voice} />}
    <Sfx list={p.sfx} fps={p.fps} />
  </AbsoluteFill>;
};
