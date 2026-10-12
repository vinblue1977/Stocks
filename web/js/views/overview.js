import { api } from '../api.js';
import { lineChart } from '../charts.js';
import { GROUPS } from '../groups.js';
import { fmt, h, icon, pageHead, tone } from '../ui.js';

const RANGES = { '1M': 22, '3M': 66, '1Y': 252, All: Infinity };
const PALETTE = ['#BFE3FF', '#7CC4FA', '#3E8FD6', '#2A5F96', '#1D4573', '#F5A35C', '#F5C38F', '#C97B3F', '#8392AB', '#5A6B85'];
const VIEWS = { overall: 'Overall', stocks: GROUPS.stocks.label, auto: GROUPS.auto.label };
let view = 'overall';
let range = '1Y';

function tile(label, value, sub, cls = '') {
  return h('div', { class: 'tile' },
    h('div', { class: 'tile-label', text: label }),
    h('div', { class: `tile-value ${cls}`, text: value }),
    h('div', { class: `tile-sub ${cls}`, text: sub }));
}

function vsText(b) {
  return b.difference == null ? '—' : `${b.difference >= 0 ? '+' : '−'}${Math.abs(b.difference * 100).toFixed(1)} pts`;
}

function summaryTable(groups, rerender) {
  const total = groups.overall.total_value || 0;
  const row = (key) => {
    const g = groups[key];
    const isTotal = key === 'overall';
    const pick = h('button', { type: 'button', class: `link-btn ${key === view ? 'active' : ''}`, 'aria-pressed': String(key === view), text: VIEWS[key], onClick: () => { view = key; rerender(); } });
    return h('tr', { class: [key === view ? 'highlight' : '', isTotal ? 'total-row' : ''].join(' ').trim() || null },
      h('td', { class: 'text' }, pick),
      h('td', { text: fmt.money(g.total_value) }),
      h('td', { text: fmt.money(g.total_cost) }),
      h('td', { class: tone(g.total_gain) }, `${fmt.signedMoney(g.total_gain)} `, h('span', { class: 'xsmall', text: fmt.signedPct(g.total_gain_pct, 1) })),
      h('td', { class: tone(g.day_change), text: fmt.signedMoney(g.day_change) }),
      h('td', { class: tone(g.benchmark.portfolio_return), text: fmt.signedPct(g.benchmark.portfolio_return, 1) }),
      h('td', { class: tone(g.benchmark.difference), text: vsText(g.benchmark) }),
      h('td', { text: total ? fmt.pct(g.total_value / total) : '—' }));
  };
  return h('section', { class: 'card', 'aria-labelledby': 'sum-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'sum-h', text: 'By group' }), h('span', { class: 'muted small', text: 'Select a row to focus the page on it' })),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Group', 'Value', 'Cost basis', 'Gain / loss', 'Last day', '1Y return', 'vs SPY (1Y)', 'Share of total']
        .map((t, i) => h('th', { scope: 'col', class: i === 0 ? 'text' : null, text: t })))),
      h('tbody', null, row('stocks'), row('auto'), row('overall')))));
}

function performanceCard(summary, benchHistory, benchTicker) {
  const card = h('section', { class: 'card grow', 'aria-labelledby': 'perf-h' });
  const chartHost = h('div');
  const bench = new Map(benchHistory.map((p) => [p.date, p.close]));
  const buttons = Object.keys(RANGES).map((key) => h('button', {
    type: 'button', 'aria-pressed': String(key === range), text: key,
    onClick: () => { range = key; buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.textContent === key))); draw(); },
  }));

  function draw() {
    const points = summary.history.slice(-RANGES[range] - 1);
    chartHost.replaceChildren();
    if (points.length < 2) {
      chartHost.append(h('p', { class: 'muted small', text: 'Not enough price history yet for this view.' }));
      return;
    }
    const start = points[0];
    const benchStart = bench.get(start.date);
    const benchValues = points.map((p) => (benchStart && bench.has(p.date) ? (bench.get(p.date) / benchStart) * start.value : null));
    const labels = points.map((p) => new Date(`${p.date}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: range === '1M' ? 'numeric' : undefined }));
    chartHost.append(lineChart({
      series: [{ values: points.map((p) => p.value), color: '#7CC4FA', fill: true }, { values: benchValues, color: '#6F7F99', dashed: true }],
      labels,
      ariaLabel: `${VIEWS[view]} value over ${range}: from ${fmt.money(start.value)} to ${fmt.money(points[points.length - 1].value)}, compared with ${benchTicker}`,
    }));
  }

  card.append(
    h('div', { class: 'card-head' }, h('h2', { id: 'perf-h', text: `Performance · ${VIEWS[view]}` }),
      h('div', { class: 'segmented', role: 'group', 'aria-label': 'Time range' }, buttons)),
    h('div', { class: 'legend' },
      h('span', null, h('span', { class: 'swatch' }), VIEWS[view]),
      h('span', null, h('span', { class: 'swatch-dashed' }), `${benchTicker}, same starting value`)),
    chartHost);
  draw();
  return card;
}

function allocationCard(summary) {
  const byTicker = new Map();
  for (const p of summary.positions) {
    if (p.value != null) byTicker.set(p.ticker, (byTicker.get(p.ticker) || 0) + p.value);
  }
  const total = [...byTicker.values()].reduce((a, b) => a + b, 0);
  const items = [...byTicker.entries()].sort((a, b) => b[1] - a[1])
    .map(([ticker, value], i) => ({ ticker, value, weight: total ? value / total : 0, color: PALETTE[i % PALETTE.length] }));
  const inBoth = view === 'overall' ? [...new Set(summary.positions.filter((p, i, arr) => arr.findIndex((q) => q.ticker === p.ticker) !== i).map((p) => p.ticker))] : [];
  return h('section', { class: 'card side', 'aria-labelledby': 'alloc-h' },
    h('h2', { id: 'alloc-h', text: `Allocation · ${VIEWS[view]}` }),
    items.length ? [
      h('div', { class: 'alloc-bar', role: 'img', 'aria-label': `Allocation: ${items.map((x) => `${x.ticker} ${fmt.pct(x.weight)}`).join(', ')}` },
        items.map((x) => h('div', { style: { width: `${x.weight * 100}%`, background: x.color } }))),
      h('div', { style: { marginTop: '16px' } }, items.map((x) => h('div', { class: 'alloc-row' },
        h('span', { class: 'dot', style: { background: x.color } }),
        h('strong', { text: x.ticker, style: { width: '56px' } }),
        h('span', { class: 'muted', style: { flex: '1' }, text: fmt.money(x.value) }),
        h('span', { class: 'mono', text: fmt.pct(x.weight) })))),
      inBoth.length ? h('p', { class: 'xsmall muted', style: { margin: '14px 0 0' }, text: `${inBoth.join(', ')} ${inBoth.length === 1 ? 'is' : 'are'} held in both groups; Overall combines them.` }) : null,
    ] : h('p', { class: 'muted', text: 'Nothing in this group yet.' }));
}

function alertsCard(rules) {
  const triggered = rules.filter((r) => r.triggered);
  const quiet = rules.length - triggered.length;
  const items = triggered.map((r) => h('div', { class: 'alert-item' }, icon('warn', '#F5A35C'),
    h('div', null, h('div', { class: 'alert-title', text: `${r.ticker}: ${r.detail || 'condition met'}` }))));
  items.push(h('div', { class: 'alert-item quiet' }, icon('check', '#9AA8BF'),
    h('div', null, h('div', { class: 'alert-title', text: rules.length ? `${quiet} rule${quiet === 1 ? '' : 's'} quiet` : 'No rules yet' }),
      h('div', { class: 'muted small', text: rules.length ? 'Checked each weekday after market close.' : 'Create rules to get email alerts.' }))));
  return h('section', { class: 'card stack', 'aria-labelledby': 'alerts-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'alerts-h', text: 'Alerts' }),
      h('span', { class: 'muted small', text: `${triggered.length} triggered · ${rules.length} rules` })),
    ...items, h('a', { href: '#/alerts', class: 'small', text: 'Manage rules' }));
}

function holdingsCard(summary) {
  const totals = view === 'auto'; // Automatic Investment is shown in dollar totals
  const positions = [...summary.positions].sort((a, b) => (b.value || 0) - (a.value || 0));
  const rows = positions.map((p) => h('tr', null,
    h('td', { class: 'text' }, h('strong', { text: p.ticker })),
    h('td', { class: 'text' }, h('span', { class: `group-chip ${GROUPS[p.group]?.chip || ''}`, text: GROUPS[p.group]?.short || p.group })),
    h('td', { text: fmt.num(p.shares, 5) }),
    h('td', { text: fmt.money(totals ? p.shares * p.cost_basis : p.cost_basis) }),
    h('td', { text: p.price == null ? 'Loading…' : fmt.money(p.price) }),
    h('td', { text: fmt.money(p.value) }),
    h('td', { class: tone(p.gain) }, `${fmt.signedMoney(p.gain)} `, h('span', { class: 'xsmall', text: fmt.signedPct(p.gain_pct, 1) }))));
  return h('section', { class: 'card', 'aria-labelledby': 'hold-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'hold-h', text: `Holdings · ${VIEWS[view]}` }),
      h('span', { class: 'muted small', text: `${rows.length} position${rows.length === 1 ? '' : 's'}` })),
    rows.length
      ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ['Ticker', 'Group', 'Shares', totals ? 'Cost basis' : 'Avg cost', 'Price', 'Value', 'Gain / loss']
          .map((t, i) => h('th', { scope: 'col', class: i < 2 ? 'text' : null, text: t })))),
        h('tbody', null, rows)))
      : h('p', { class: 'muted', text: 'Nothing in this group yet.' }));
}

export async function renderOverview(main) {
  const [data, rulesRes] = await Promise.all([api('GET', '/portfolio'), api('GET', '/rules')]);
  const { groups } = data;
  if (!groups.overall.positions.length) {
    main.replaceChildren(pageHead('Overview'), h('div', { class: 'card empty-card' },
      h('h2', { text: 'Add your first holding' }),
      h('p', { class: 'muted', text: 'Record purchases under Stock Holdings or Automatic Investment, and the app tracks value, gains, news and alerts from there.' }),
      h('div', { style: { display: 'flex', gap: '12px', justifyContent: 'center' } },
        h('a', { class: 'btn btn-primary btn-link', href: '#/stocks', text: 'Stock Holdings' }),
        h('a', { class: 'btn btn-ghost btn-link', href: '#/auto', text: 'Automatic Investment' }))));
    return;
  }

  const draw = () => {
    const s = groups[view];
    const b = s.benchmark;
    const switcher = h('div', { class: 'segmented segmented-lg', role: 'group', 'aria-label': 'Show' },
      Object.entries(VIEWS).map(([key, label]) => h('button', {
        type: 'button', 'aria-pressed': String(key === view), text: label, onClick: () => { view = key; draw(); },
      })));
    main.replaceChildren(
      pageHead('Overview', data.as_of ? `Prices as of ${fmt.date(data.as_of)} close` : 'Waiting for the first price sync', switcher),
      summaryTable(groups, draw),
      h('section', { class: 'tiles', 'aria-label': `Summary for ${VIEWS[view]}` },
        tile(`Total value · ${VIEWS[view]}`, fmt.money(s.total_value), `Cost basis ${fmt.money(s.total_cost)}`),
        tile('Total gain', fmt.signedMoney(s.total_gain), `${fmt.signedPct(s.total_gain_pct)} all time`, tone(s.total_gain)),
        tile('Last trading day', fmt.signedMoney(s.day_change), fmt.signedPct(s.day_change_pct), tone(s.day_change)),
        tile(`vs. ${b.ticker} (1Y)`, vsText(b),
          b.difference == null ? 'Needs more history' : `This view ${fmt.signedPct(b.portfolio_return, 1)} · ${b.ticker} ${fmt.signedPct(b.benchmark_return, 1)}`, tone(b.difference))),
      h('div', { class: 'row' }, performanceCard(s, data.benchmark_history, data.benchmark_ticker), allocationCard(s)),
      h('div', { class: 'row' }, h('div', { class: 'grow' }, holdingsCard(s)), h('div', { class: 'side' }, alertsCard(rulesRes.rules))),
      h('div', { class: 'footer-note' },
        h('span', { text: 'Performance shows how current holdings moved; it ignores past buys and sells.' }),
        h('span', { text: 'Describes data only. Not investment advice.' })),
    );
  };
  draw();
}
