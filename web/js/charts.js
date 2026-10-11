import { s } from './ui.js';

const W = 780;
const H = 270;
const LEFT = 52;
const RIGHT = 760;
const TOP = 20;
const BOTTOM = 240;

function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((m) => m >= raw);
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.5; v += step) ticks.push(v);
  return ticks;
}

function compact(v) {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(v % 1000 === 0 ? 0 : 1)}k`;
  return `$${v.toFixed(0)}`;
}

// series: [{ values: number[], color, dashed, fill }]; labels: string[] (same length as values)
export function lineChart({ series, labels, ariaLabel }) {
  const all = series.flatMap((x) => x.values).filter((v) => v != null);
  const ticks = niceTicks(Math.min(...all), Math.max(...all));
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const n = labels.length;
  const x = (i) => LEFT + (n <= 1 ? 0 : (i / (n - 1)) * (RIGHT - LEFT));
  const y = (v) => BOTTOM - ((v - lo) / (hi - lo || 1)) * (BOTTOM - TOP);

  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': ariaLabel });
  const grid = s('g', { stroke: '#1E2A40', 'stroke-width': 1 });
  const axis = s('g', { 'font-family': 'IBM Plex Mono, monospace', 'font-size': 11, fill: '#8392AB' });
  for (const t of ticks) {
    grid.append(s('line', { x1: LEFT, y1: y(t), x2: RIGHT, y2: y(t) }));
    axis.append(s('text', { x: 0, y: y(t) + 4, text: compact(t) }));
  }
  const step = Math.max(1, Math.ceil(n / 5));
  for (let i = 0; i < n; i += step) {
    axis.append(s('text', { x: x(i), y: 262, 'text-anchor': i === 0 ? 'start' : 'middle', text: labels[i] }));
  }
  svg.append(grid, axis);

  for (const line of series) {
    const pts = line.values.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean);
    if (pts.length < 2) continue;
    if (line.fill) {
      svg.append(s('polygon', { points: `${pts.join(' ')} ${x(n - 1)},${BOTTOM} ${LEFT},${BOTTOM}`, fill: line.color, 'fill-opacity': 0.12 }));
    }
    svg.append(s('polyline', {
      points: pts.join(' '), fill: 'none', stroke: line.color, 'stroke-width': line.dashed ? 2 : 2.5,
      'stroke-dasharray': line.dashed ? '5 5' : null, 'stroke-linejoin': 'round',
    }));
  }
  const main = series[0];
  const last = main.values.length - 1;
  if (last >= 0 && main.values[last] != null) {
    svg.append(s('circle', { cx: x(last), cy: y(main.values[last]), r: 5, fill: '#121B2E', stroke: main.color, 'stroke-width': 2.5 }));
  }
  return svg;
}

// values in [-1, 1]; positive bars blue above the axis, negative orange below.
export function sentimentBars(values, labels, ariaLabel) {
  const width = 340;
  const height = 160;
  const mid = 70;
  const gap = 6;
  const bw = (width - gap * (values.length - 1)) / Math.max(values.length, 1);
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': ariaLabel });
  svg.append(s('line', { x1: 0, y1: mid, x2: width, y2: mid, stroke: '#2A3A57' }));
  values.forEach((v, i) => {
    const x = i * (bw + gap);
    if (v != null && v !== 0) {
      const h = Math.max(2, Math.abs(v) * (mid - 6));
      svg.append(s('rect', { x, y: v > 0 ? mid - h : mid, width: bw, height: h, rx: 2, fill: v > 0 ? '#7CC4FA' : '#F5A35C' }));
    }
    svg.append(s('text', { x: x + bw / 2, y: height - 4, 'text-anchor': 'middle', 'font-size': 10, fill: '#8392AB', 'font-family': 'IBM Plex Mono, monospace', text: labels[i] }));
  });
  return svg;
}
