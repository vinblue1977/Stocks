import { api } from '../api.js';
import { lineChart } from '../charts.js';
import { fmt, h, icon, pageHead, tone } from '../ui.js';

const RANGES = { '1M': 22, '3M': 66, '1Y': 252, All: Infinity };
const PALETTE = ['#BFE3FF', '#7CC4FA', '#3E8FD6', '#2A5F96', '#1D4573', '#F5A35C', '#F5C38F', '#8392AB'];

function tile(label, value, sub, cls = '') {
  return h('div', { class: 'tile' },
    h('div', { class: 'tile-label', text: label }),
    h('div', { class: `tile-value ${cls}`, text: value }),
    h('div', { class: `tile-sub ${cls}`, text: sub }));
}

function performanceCard(data) {
  const card = h('section', { class: 'card grow', 'aria-labelledby': 'perf-h' });
  const chartHost = h('div');
  let range = '1Y';
  const buttons = Object.keys(RANGES).map((key) => h('button', {
    type: 'button', 'aria-pressed': String(key === range), text: key,
    onClick: () => { range = key; buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.textContent === key))); draw(); },
  }));
  const bench = new Map(data.benchmark_history.map((p) => [p.date, p.close]));

  function draw() {
    const points = data.history.slice(-RANGES[range] - 1);
    chartHost.replaceChildren();
    if (points.length < 2) {
      chartHost.append(h('p', { class: 'muted small', text: 'Not enough price history yet. New tickers fill in within a minute of being added.' }));
      return;
    }
    const start = points[0];
    const benchStart = bench.get(start.date);
    const benchValues = points.map((p) => (benchStart && bench.has(p.date) ? (bench.get(p.date) / benchStart) * start.value : null));
    const labels = points.map((p) => new Date(`${p.date}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: range === '1M' ? 'numeric' : undefined }));
    const last = points[points.length - 1].value;
    chartHost.append(lineChart({
      series: [
        { values: points.map((p) => p.value), color: '#7CC4FA', fill: true },
        { values: benchValues, color: '#6F7F99', dashed: true },
      ],
      labels,
      ariaLabel: `Portfolio value over ${range}: from ${fmt.money(start.value)} to ${fmt.money(last)}, compared with ${data.benchmark.ticker} from the same starting value`,
    }));
  }

  card.append(
    h('div', { class: 'card-head' }, h('h2', { id: 'perf-h', text: 'Performance' }),
      h('div', { class: 'segmented', role: 'group', 'aria-label': 'Time range' }, buttons)),
    h('div', { class: 'legend' },
      h('span', null, h('span', { class: 'swatch' }), 'Your portfolio'),
      h('span', null, h('span', { class: 'swatch-dashed' }), `${data.benchmark.ticker}, same starting value`)),
    chartHost,
  );
  draw();
  return card;
}

function alertsCard(rules) {
  const triggered = rules.filter((r) => r.triggered);
  const quiet = rules.length - triggered.length;
  const items = triggered.map((r) => h('div', { class: 'alert-item' }, icon('warn', '#F5A35C'),
    h('div', null, h('div', { class: 'alert-title', text: `${r.ticker}: ${r.detail || 'condition met'}` }))));
  items.push(h('div', { class: 'alert-item quiet' }, icon('check', '#9AA8BF'),
    h('div', null, h('div', { class: 'alert-title', text: rules.length ? `${quiet} rule${quiet === 1 ? '' : 's'} quiet` : 'No rules yet' }),
      h('div', { class: 'muted small', text: rules.length ? 'Checked each weekday after market close.' : 'Create rules to get email alerts.' }))));
  return h('section', { class: 'card side stack', 'aria-labelledby': 'alerts-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'alerts-h', text: 'Alerts' }),
      h('span', { class: 'muted small', text: `${triggered.length} triggered · ${rules.length} rules` })),
    ...items,
    h('a', { href: '#/alerts', class: 'small', text: 'Manage rules' }));
}

function holdingsCard(positions) {
  const rows = positions.map((p) => h('tr', null,
    h('td', { class: 'text' }, h('strong', { text: p.ticker })),
    h('td', { text: fmt.num(p.shares, 4) }),
    h('td', { text: fmt.money(p.cost_basis) }),
    h('td', { text: p.price == null ? 'Loading…' : fmt.money(p.price) }),
    h('td', { text: fmt.money(p.value) }),
    h('td', { class: tone(p.gain) }, h('div', { text: fmt.signedMoney(p.gain) }), h('div', { class: 'sub', text: fmt.signedPct(p.gain_pct, 1) })),
    h('td', { text: fmt.pct(p.weight) })));
  return h('section', { class: 'card grow', 'aria-labelledby': 'hold-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'hold-h', text: 'Holdings' }), h('a', { href: '#/holdings', class: 'small', text: 'Edit holdings' })),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Ticker', 'Shares', 'Avg cost', 'Price', 'Value', 'Gain / loss', 'Weight'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, rows))));
}

function allocationCard(positions) {
  const priced = positions.filter((p) => p.weight != null);
  const colored = priced.map((p, i) => ({ ...p, color: PALETTE[i % PALETTE.length] }));
  const label = `Allocation: ${colored.map((p) => `${p.ticker} ${fmt.pct(p.weight)}`).join(', ')}`;
  return h('section', { class: 'card side', 'aria-labelledby': 'alloc-h' },
    h('h2', { id: 'alloc-h', text: 'Allocation' }),
    h('div', { class: 'alloc-bar', role: 'img', 'aria-label': label },
      colored.map((p) => h('div', { style: { width: `${p.weight * 100}%`, background: p.color } }))),
    h('div', { style: { marginTop: '20px' } }, colored.map((p) => h('div', { class: 'alloc-row' },
      h('span', { class: 'dot', style: { background: p.color } }),
      h('strong', { text: p.ticker, style: { width: '64px' } }),
      h('span', { class: 'muted', style: { flex: '1' }, text: fmt.money(p.value) }),
      h('span', { class: 'mono', text: fmt.pct(p.weight) })))));
}

export async function renderOverview(main) {
  const [data, rulesRes] = await Promise.all([api('GET', '/portfolio'), api('GET', '/rules')]);
  if (!data.positions.length) {
    main.replaceChildren(pageHead('Overview'), h('div', { class: 'card empty-card' },
      h('h2', { text: 'Add your first holding' }),
      h('p', { class: 'muted', text: 'Enter what you own and the app tracks value, gains, news and alerts from there.' }),
      h('a', { class: 'btn btn-primary', href: '#/holdings', style: { display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }, text: 'Add a holding' })));
    return;
  }
  const b = data.benchmark;
  main.replaceChildren(
    pageHead('Overview', data.as_of ? `Prices as of ${fmt.date(data.as_of)} close` : 'Waiting for the first price sync'),
    h('section', { class: 'tiles', 'aria-label': 'Summary' },
      tile('Total value', fmt.money(data.total_value), `Cost basis ${fmt.money(data.total_cost)}`),
      tile('Total gain', fmt.signedMoney(data.total_gain), `${fmt.signedPct(data.total_gain_pct)} all time`, tone(data.total_gain)),
      tile('Last trading day', fmt.signedMoney(data.day_change), fmt.signedPct(data.day_change_pct), tone(data.day_change)),
      tile(`vs. ${b.ticker} (1Y)`, b.difference == null ? '—' : `${b.difference >= 0 ? '+' : '−'}${Math.abs(b.difference * 100).toFixed(1)} pts`,
        b.difference == null ? 'Needs more history' : `You ${fmt.signedPct(b.portfolio_return, 1)} · ${b.ticker} ${fmt.signedPct(b.benchmark_return, 1)}`, tone(b.difference))),
    h('div', { class: 'row' }, performanceCard(data), alertsCard(rulesRes.rules)),
    h('div', { class: 'row' }, holdingsCard(data.positions), allocationCard(data.positions)),
    h('div', { class: 'footer-note' },
      h('span', { text: 'Performance shows how your current holdings moved; it ignores past buys and sells.' }),
      h('span', { text: 'Describes data only. Not investment advice.' })),
  );
}
