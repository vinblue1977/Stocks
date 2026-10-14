import { fmt, h, s, tone } from './ui.js';

const LEFT = 52;
const RIGHT = 760;
const TOP = 20;

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

/**
 * Value-over-time chart that fills `host` (give it a height), redrawing when it resizes. Hover or tap
 * shows the day's value and the change since the first point. points: [{ date, value }], oldest first.
 */
export function valueChart(host, points, { ariaLabel, sinceLabel, dayLabel }) {
  const tip = h('div', { class: 'chart-tip', hidden: true });
  host.replaceChildren(tip);
  const first = points[0].value;
  const lastIndex = points.length - 1;

  function draw(width, height) {
    const W = 780; // viewBox width; the height follows the host so the chart fills it
    const H = Math.max(200, Math.round((height * W) / width));
    const B = H - 32;
    const values = points.map((p) => p.value);
    const ticks = niceTicks(Math.min(...values), Math.max(...values));
    const lo = ticks[0];
    const hi = ticks[ticks.length - 1];
    const x = (i) => LEFT + (lastIndex ? (i / lastIndex) * (RIGHT - LEFT) : 0);
    const y = (v) => B - ((v - lo) / (hi - lo || 1)) * (B - TOP);
    const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');

    const svg = s('svg', { class: 'chart chart-fill', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': ariaLabel });
    const grid = s('g', { stroke: '#1E2A40', 'stroke-width': 1 });
    const axis = s('g', { 'font-family': 'IBM Plex Mono, monospace', 'font-size': 11, fill: '#8392AB' });
    for (const t of ticks) {
      grid.append(s('line', { x1: LEFT, y1: y(t), x2: RIGHT, y2: y(t) }));
      axis.append(s('text', { x: 0, y: y(t) + 4, text: compact(t) }));
    }
    const step = Math.max(1, Math.ceil(points.length / 5));
    for (let i = 0; i < points.length; i += step) {
      axis.append(s('text', { x: x(i), y: H - 8, 'text-anchor': i === 0 ? 'start' : 'middle', text: dayLabel(points[i].date) }));
    }
    const cross = s('line', { y1: TOP, y2: B, stroke: '#8392AB', 'stroke-dasharray': '3 3', visibility: 'hidden' });
    const dot = s('circle', { r: 5, fill: '#121B2E', stroke: '#7CC4FA', 'stroke-width': 2.5 });
    const place = (i) => { dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(points[i].value)); };
    place(lastIndex);
    svg.append(grid, axis,
      s('polygon', { points: `${line} ${x(lastIndex)},${B} ${LEFT},${B}`, fill: '#7CC4FA', 'fill-opacity': 0.12 }),
      s('polyline', { points: line, fill: 'none', stroke: '#7CC4FA', 'stroke-width': 2.5, 'stroke-linejoin': 'round' }),
      cross, dot);
    host.querySelector('svg')?.remove();
    host.prepend(svg);

    const show = (event) => {
      const box = svg.getBoundingClientRect();
      const sx = ((event.clientX - box.left) / box.width) * W;
      const i = Math.max(0, Math.min(lastIndex, Math.round(((sx - LEFT) / (RIGHT - LEFT)) * lastIndex)));
      const p = points[i];
      const change = p.value - first;
      cross.setAttribute('x1', x(i));
      cross.setAttribute('x2', x(i));
      cross.setAttribute('visibility', 'visible');
      place(i);
      tip.replaceChildren(
        h('div', { class: 'muted', text: new Date(`${p.date}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }) }),
        h('div', { class: 'chart-tip-value', text: fmt.money(p.value) }),
        h('div', { class: tone(change), text: `${fmt.signedMoney(change)} (${fmt.signedPct(first ? change / first : null, 1)}) ${sinceLabel}` }));
      tip.hidden = false;
      const px = (x(i) / W) * box.width;
      tip.style.left = `${px > box.width / 2 ? px - tip.offsetWidth - 12 : px + 12}px`;
    };
    host.onpointermove = show;
    host.onpointerdown = show;
    hide = () => { cross.setAttribute('visibility', 'hidden'); tip.hidden = true; place(lastIndex); };
    // On touch, pointerleave fires as soon as the finger lifts; keep the tap's details until a tap elsewhere.
    host.onpointerleave = (event) => { if (event.pointerType !== 'touch') hide(); };
  }
  let hide = () => {};
  const tapElsewhere = (event) => {
    if (!host.isConnected) { document.removeEventListener('pointerdown', tapElsewhere); return; }
    if (!host.contains(event.target)) hide();
  };
  document.addEventListener('pointerdown', tapElsewhere);

  // Redraw on resize, at most once a frame; stop observing once the page replaces this chart.
  let size = '';
  let attached = false;
  let frame = 0;
  const observer = new ResizeObserver(([entry]) => {
    if (!host.isConnected) { if (attached) observer.disconnect(); return; }
    attached = true;
    const { width, height } = entry.contentRect;
    const next = `${Math.round(width)}x${Math.round(height)}`;
    if (!width || next === size) return;
    size = next;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => draw(width, height));
  });
  observer.observe(host);
}

/** Donut of shares (items: [{ name, weight, color }]) with a label and value in the middle. */
export function donut(allItems, { label, value, ariaLabel }) {
  const items = allItems.filter((item) => item.weight > 0);
  const r = 66;
  const circumference = 2 * Math.PI * r;
  let offset = 0;
  const arcs = items.map((item) => {
    const length = item.weight * circumference;
    const arc = s('circle', {
      cx: 84, cy: 84, r, fill: 'none', stroke: item.color, 'stroke-width': 26,
      'stroke-dasharray': `${Math.max(length - (items.length > 1 ? 2 : 0), 0.5)} ${circumference}`,
      'stroke-dashoffset': -offset, transform: 'rotate(-90 84 84)',
    });
    offset += length;
    return arc;
  });
  return s('svg', { class: 'donut', viewBox: '0 0 168 168', role: 'img', 'aria-label': ariaLabel },
    s('circle', { cx: 84, cy: 84, r, fill: 'none', stroke: '#0E1626', 'stroke-width': 26 }), arcs,
    s('text', { x: 84, y: 80, 'text-anchor': 'middle', fill: '#9AA8BF', 'font-size': 11, 'font-family': 'IBM Plex Sans, sans-serif', text: label }),
    s('text', { x: 84, y: 99, 'text-anchor': 'middle', fill: '#E6EDF7', 'font-size': 16, 'font-family': 'IBM Plex Mono, monospace', text: value }));
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
      const barHeight = Math.max(2, Math.abs(v) * (mid - 6));
      svg.append(s('rect', { x, y: v > 0 ? mid - barHeight : mid, width: bw, height: barHeight, rx: 2, fill: v > 0 ? '#7CC4FA' : '#F5A35C' }));
    }
    svg.append(s('text', { x: x + bw / 2, y: height - 4, 'text-anchor': 'middle', 'font-size': 10, fill: '#8392AB', 'font-family': 'IBM Plex Mono, monospace', text: labels[i] }));
  });
  return svg;
}
