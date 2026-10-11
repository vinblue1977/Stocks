// DOM helpers. All dynamic text goes through textContent, never innerHTML, so data from the
// API (headlines, tickers, messages) can't inject markup.

const SVG_NS = 'http://www.w3.org/2000/svg';

function apply(el, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.setAttribute('class', value);
    else if (key === 'text') el.textContent = value;
    else if (key === 'style') Object.assign(el.style, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
}

function append(el, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  apply(el, attrs);
  append(el, children);
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  apply(el, attrs);
  append(el, children);
  return el;
}

const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

export const fmt = {
  money: (v) => (v == null ? '—' : money.format(v)),
  signedMoney: (v) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${money.format(Math.abs(v))}`),
  pct: (v, digits = 1) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`),
  signedPct: (v, digits = 2) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(digits)}%`),
  num: (v, digits = 2) => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: digits })),
  score: (v) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}`),
  date: (iso) => (iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'),
  ago(iso) {
    const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (minutes < 60) return `${Math.max(minutes, 1)} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    const days = Math.round(hours / 24);
    return `${days} day${days === 1 ? '' : 's'} ago`;
  },
};

export const tone = (v) => (v == null ? '' : v >= 0 ? 'pos' : 'neg');

export function sentimentTag(label) {
  const cls = { Positive: 'tag tag-pos', Negative: 'tag tag-neg' }[label] || 'tag tag-neutral';
  return h('span', { class: cls, text: label || 'Neutral' });
}

let toastTimer;
export function toast(message, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.className = isError ? 'toast error' : 'toast';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

export function pageHead(title, subtitle, ...extra) {
  return h('div', { class: 'page-head' },
    h('div', null, h('h1', { text: title }), subtitle ? h('p', { class: 'muted small', text: subtitle }) : null),
    ...extra);
}

export function icon(name, color) {
  const paths = {
    warn: [s('path', { d: 'M12 3 2 21h20L12 3z' }), s('line', { x1: 12, y1: 10, x2: 12, y2: 14 }), s('line', { x1: 12, y1: 17.5, x2: 12, y2: 17.6 })],
    check: [s('circle', { cx: 12, cy: 12, r: 9 }), s('polyline', { points: '8,12 11,15 16,9' })],
    lines: [s('path', { d: 'M4 5h16M4 10h16M4 15h10M4 20h7' })],
  };
  return s('svg', { width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linecap': 'round', 'aria-hidden': 'true' }, paths[name]);
}

export function errorCard(message) {
  return h('div', { class: 'card empty-card' }, h('p', { class: 'neg', text: message }));
}
