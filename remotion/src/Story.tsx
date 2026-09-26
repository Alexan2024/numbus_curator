import React from 'react';
import {AbsoluteFill, Audio, interpolate, useCurrentFrame} from 'remotion';
import {Bar, Fonts, Grain, INK, Sfx, Stage, camState, clamp, clampCam} from './common';
import {Label, Narration, Scrims} from './Narration';
import {Marks} from './Marks';

/* Одна картинка, камера ходит по деталям под голос: «Детали картины», «Одна фотография», «Масштаб»,
   «Разбор здания». Хук на крупной детали, рассказ словами по голосу, над строкой — номер и имя детали,
   кульминация антиквой, в конце камера ужимает картинку в этикетку. С loopStart камера возвращается
   к первому кадру — повтор ролика начинается без стыка. Линии (marks) — только в «Разборе» и «Масштабе». */

export const Story: React.FC<any> = (p) => {
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
    <Fonts />
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
