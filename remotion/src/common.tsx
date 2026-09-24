import React from 'react';
import {Easing, Img, interpolate, staticFile} from 'remotion';

export const W = 1080, H = 1920, A = H / W, M = 72;
export const INK = '#F2EEE6', MUTE = 'rgba(242,238,230,.62)';

export const Fonts: React.FC = () => <style>{`
@font-face{font-family:Serif;src:url(${staticFile('InstrumentSerif-Regular.ttf')})}
@font-face{font-family:Serif;font-style:italic;src:url(${staticFile('InstrumentSerif-Italic.ttf')})}
@font-face{font-family:Grot;src:url(${staticFile('InterTight.ttf')});font-weight:100 900}
@font-face{font-family:Mono;src:url(${staticFile('IBMPlexMono-Regular.ttf')})}
@font-face{font-family:Mono;font-weight:500;src:url(${staticFile('IBMPlexMono-Medium.ttf')})}`}</style>;

export const clamp = {extrapolateLeft: 'clamp' as const, extrapolateRight: 'clamp' as const};
export const out3 = {...clamp, easing: Easing.out(Easing.cubic)};

const ease = (u: number) => u * u * (3 - 2 * u);
// камера: [время, cx, cy, ширина кадра] в пикселях исходной картинки
export type Cam = number[];
export function camAt(keys: Cam[], t: number): [number, number, number] {
  if (!keys.length) return [0, 0, 1];
  if (t <= keys[0][0]) return [keys[0][1], keys[0][2], keys[0][3]];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t <= b[0]) {
      const u = ease((t - a[0]) / Math.max(b[0] - a[0], 1e-6));
      return [a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u,
        Math.exp(Math.log(a[3]) + (Math.log(b[3]) - Math.log(a[3])) * u)];
    }
  }
  const l = keys[keys.length - 1];
  return [l[1], l[2], l[3]];
}

// картина под камерой: размытая копия на фоне, сама картина — по координатам камеры
export const Stage: React.FC<{src: string; pw: number; ph: number; cam: [number, number, number]; dim?: number;
  shadow?: number; dark?: number}> = ({src, pw, ph, cam, dim = 0.32, shadow = 0, dark = 0}) => {
  const [cx, cy, cw] = cam; const s = W / cw, ch = cw * A;
  return <>
    <Img src={src} style={{position: 'absolute', inset: -120, width: W + 240, height: H + 240, objectFit: 'cover',
      filter: `blur(38px) brightness(${dim})`}} />
    {dark > 0 && <div style={{position: 'absolute', inset: 0, background: '#0f0e0c', opacity: dark}} />}
    <Img src={src} style={{position: 'absolute', left: -(cx - cw / 2) * s, top: -(cy - ch / 2) * s, width: pw * s,
      height: ph * s, boxShadow: shadow ? `0 30px 80px rgba(0,0,0,${0.6 * shadow})` : undefined}} />
  </>;
};

export const Bar: React.FC<{text: string; opacity?: number}> = ({text, opacity = 1}) =>
  <div style={{position: 'absolute', left: M, right: M, top: 150, display: 'flex', alignItems: 'center', gap: 22,
    fontFamily: 'Mono', fontSize: 21, letterSpacing: '.14em', textTransform: 'uppercase', color: MUTE, opacity}}>
    <Img src={staticFile('logo.png')} style={{height: 30, opacity: .9}} />
    <span style={{width: 46, height: 1, background: MUTE}} />
    <span>{text}</span>
  </div>;

export const Grain: React.FC<{frame: number}> = ({frame}) => {
  const g = (frame * 37) % 300;
  return <div style={{position: 'absolute', inset: 0, opacity: .08, mixBlendMode: 'overlay',
    backgroundPosition: `${g}px ${g * 1.7}px`,
    backgroundImage: `url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")`}} />;
};

// *слово* → курсив антиквы
export const Rich: React.FC<{text: string}> = ({text}) => <>{
  text.split(/(\*[^*]+\*)/).map((part, i) => part.startsWith('*') && part.endsWith('*')
    ? <i key={i} style={{fontFamily: 'Serif', fontStyle: 'italic'}}>{part.slice(1, -1)}</i> : <span key={i}>{part}</span>)
}</>;
