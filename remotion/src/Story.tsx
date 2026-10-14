import React from 'react';
import {AbsoluteFill, Audio, interpolate, useCurrentFrame} from 'remotion';
import {Bar, Fonts, Grain, INK, Sfx, Stage, camState, clamp, clampCam} from './common';
import {Label, Narration, Scrims} from './Narration';
import {Marks} from './Marks';
import {Backdrop, Captions, Headline, PulseScrims, punchAt} from './Pulse';
import {StoryGuide} from './Guide';

/* Одна картинка, камера ходит по деталям под голос: «Детали картины», «Одна фотография», «Масштаб»,
   «Разбор здания». Хук на крупной детали, рассказ словами по голосу, над строкой — номер и имя детали,
   кульминация антиквой, в конце камера ужимает картинку в этикетку. С loopStart камера возвращается
   к первому кадру — повтор ролика начинается без стыка. Линии (marks) — только в «Разборе» и «Масштабе».
   style = pulse (8.0) — своя вёрстка под ленту: см. Pulse.tsx и StoryPulse ниже. */

export const Story: React.FC<any> = (p) => p.style === 'guide' ? <StoryGuide {...p} />
  : p.style === 'pulse' ? <StoryPulse {...p} /> : <StoryCalm {...p} />;

const StoryCalm: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const st = camState(p.cam, t);
  const loop = p.loopStart ? t >= p.loopStart : false;
  const fix = (c: [number, number, number], tt: number) => (tt < p.endStart || (p.loopStart && tt >= p.loopStart + 0.7))
    ? clampCam(p.pw, p.ph, c, p.slack) : c;
  const cam = fix(st.cam, t);
  // скорость картины на экране за кадр → смаз по осям (как выдержка камеры в полкадра)
  const nx = fix(camState(p.cam, t + 1 / p.fps).cam, t + 1 / p.fps);
  const sc = 1080 / cam[2];
  const moving = (t < p.endStart || loop) && !st.prev;
  const mb: [number, number] = moving
    ? [Math.min(9, Math.abs(nx[0] - cam[0]) * sc * 0.3), Math.min(9, Math.abs(nx[1] - cam[1]) * sc * 0.3)] : [0, 0];
  const endIn = interpolate(t, [p.endStart, p.endStart + 0.8], [0, 1], clamp);
  const endK = p.loopStart ? Math.min(endIn, interpolate(t, [p.loopStart, p.loopStart + 0.45], [1, 0], clamp)) : endIn;
  const beat = p.beats.find((b: any) => t >= b.start - 0.1 && t < b.end) || p.beats[p.beats.length - 1];
  // приглушённые края — только пока камера стоит на детали
  const det = p.beats.find((b: any) => (b.kind === 'reveal' || b.kind === 'climax') && t >= b.arrive - 0.2 && t < b.end);
  const vig = det ? Math.min(interpolate(t, [det.arrive - 0.2, det.arrive + 0.6], [0, 1], clamp),
    interpolate(t, [det.end - 0.4, det.end], [1, 0], clamp)) : 0;
  const text = t < p.endStart;

  return <AbsoluteFill style={{background: '#0d0c0b', color: INK, overflow: 'hidden'}}>
    <Fonts lang={p.lang} />
    {st.prev && st.mix < 1 && <Stage src={p.image} pw={p.pw} ph={p.ph} cam={fix(st.prev, t)} />}
    <div style={{position: 'absolute', inset: 0, opacity: st.mix}}>
      <Stage src={p.image} pw={p.pw} ph={p.ph} cam={cam} shadow={endK} blur={mb} />
    </div>
    <div style={{position: 'absolute', inset: 0, opacity: vig * (1 - endK),
      background: 'radial-gradient(ellipse 75% 45% at 50% 40%, rgba(8,7,6,0) 55%, rgba(8,7,6,.55) 100%)'}} />
    <Marks marks={p.marks} cam={cam} t={t} fade={1 - endK} />
    <Scrims kind={loop ? 'hook' : beat.kind} opacity={text ? 1 : loop ? 1 - endK : 1 - endK} />

    <Bar text={p.beats.indexOf(beat) === 0 || loop ? p.rubric : p.series} opacity={1 - endK} />
    <Narration beats={p.beats} t={t} hidden={!text} />

    {endK > 0 && <AbsoluteFill style={{opacity: endK}}>
      <Bar text={p.rubric} />
      <Label t={t} start={p.endStart} top={p.labelTop} title={p.title} sub={p.sub} meta={p.meta} />
    </AbsoluteFill>}

    <Grain frame={frame} />
    {p.voice && <Audio src={p.voice} />}
    <Sfx list={p.sfx} fps={p.fps} />
  </AbsoluteFill>;
};

/* pulse: кадр 0 — деталь крупно, заголовок-хук целиком и резкий наезд; переезды-рывки со смазом; на ударных
   словах — толчок камеры; на детали встаёт рамка с ярлыком; вокруг картины — она же, размытая; в конце —
   подпись «кто и что» и сразу возврат к первому кадру. */
const StoryPulse: React.FC<any> = (p) => {
  const frame = useCurrentFrame(); const t = frame / p.fps;
  const st = camState(p.cam, t, 'quart');
  const loop = p.loopStart ? t >= p.loopStart : false;
  const fix = (c: [number, number, number]) => clampCam(p.pw, p.ph, c, p.slack);
  const cam = fix(st.cam);
  const nx = fix(camState(p.cam, t + 1 / p.fps, 'quart').cam);
  const sc = 1080 / cam[2];
  // смаз сильнее, чем в спокойной вёрстке: рывок должен читаться как движение камеры, а не как дрожь
  const mb: [number, number] = !st.prev
    ? [Math.min(16, Math.abs(nx[0] - cam[0]) * sc * 0.42), Math.min(16, Math.abs(nx[1] - cam[1]) * sc * 0.42)] : [0, 0];
  const zoom = punchAt(p.punches, t, 0.045);
  const det = p.beats.find((b: any) => (b.kind === 'reveal' || b.kind === 'climax') && t >= b.arrive - 0.2 && t < b.end);
  const vig = det ? Math.min(interpolate(t, [det.arrive - 0.2, det.arrive + 0.4], [0, 1], clamp),
    interpolate(t, [det.end - 0.3, det.end], [1, 0], clamp)) : 0;
  const hl = p.headline || {};
  const headK = Math.max(interpolate(t, [hl.end - 0.05, hl.end + 0.3], [1, 0], clamp),
    p.loopStart ? interpolate(t, [p.loopStart, p.loopStart + 0.4], [0, 1], clamp) : 0);
  const beat = p.beats.find((b: any) => t >= b.start - 0.1 && t < b.end) || p.beats[p.beats.length - 1];

  return <AbsoluteFill style={{background: '#0d0c0b', color: INK, overflow: 'hidden'}}>
    <Fonts lang={p.lang} />
    <Backdrop src={p.backdrop} t={t} />
    <div style={{position: 'absolute', inset: 0, transform: `scale(${zoom})`, transformOrigin: '50% 40%'}}>
      {st.prev && st.mix < 1 && <Stage src={p.image} pw={p.pw} ph={p.ph} cam={fix(st.prev)} />}
      <div style={{position: 'absolute', inset: 0, opacity: st.mix}}>
        <Stage src={p.image} pw={p.pw} ph={p.ph} cam={cam} blur={mb} shadow={0.8} />
      </div>
      <Marks marks={p.marks} cam={cam} t={t} fade={loop ? 0 : 1} />
    </div>
    <div style={{position: 'absolute', inset: 0, opacity: vig * 0.8,
      background: 'radial-gradient(ellipse 80% 48% at 50% 40%, rgba(8,7,6,0) 58%, rgba(8,7,6,.5) 100%)'}} />
    <PulseScrims head={headK} />

    {/* строка со знаком: рубрика в начале, «кто и что» — пока звучит последняя фраза */}
    {(() => {
      const cs = p.creditStart ?? p.endStart;
      const credit = p.credit && t >= cs && !loop;
      const k = credit ? interpolate(t, [cs, cs + 0.3], [0.3, 1], clamp) : 0.9;
      return <Bar text={credit ? p.credit : p.beats.indexOf(beat) <= 1 || loop ? p.rubric : ''} opacity={k} strong={!!credit} />;
    })()}
    <Headline text={hl.text} t={t} end={hl.end} loopStart={p.loopStart} />
    <Captions beats={p.beats} t={t} hidden={t >= p.endStart} />

    <Grain frame={frame} />
    {p.voice && <Audio src={p.voice} />}
    <Sfx list={p.sfx} fps={p.fps} />
  </AbsoluteFill>;
};
