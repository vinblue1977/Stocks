import { api } from '../api.js';
import { donut, valueChart } from '../charts.js';
import { GROUPS, KINDS, groupChip, groupLabel, hasPartner, personName, portfolioTitle, possessive } from '../groups.js';
import { add, fill, fmt, h, icon, pageHead, tone } from '../ui.js';

const RANGES = { '1M': 22, '3M': 66, '1Y': 252, All: Infinity };
/** A group's color, from the same CSS tokens as its chip (Stock Holdings blue, Automatic Investment sand). */
const kindColor = (kind) => getComputedStyle(document.documentElement).getPropertyValue(kind === 'auto' ? '--auto' : '--accent-strong').trim();
const PALETTE = ['#BFE3FF', '#7CC4FA', '#3E8FD6', '#2A5F96', '#1D4573', '#F5A35C', '#F5C38F', '#C97B3F', '#8392AB', '#5A6B85'];
let person = 'household'; // household | mine | partner
let range = '1Y';
let breakdown = 'holding'; // Allocation: holding | kind | person
let kindFilter = 'both'; // Holdings: both | stocks | auto
let holdingsMode = 'value'; // Holdings columns: value | perf
const sorts = { value: { key: 'value', dir: -1 }, perf: { key: 'return_1y', dir: -1 } }; // each view keeps its own sort
let returns = new Map(); // ticker -> signal snapshot (period returns), from GET /signals

/** Replaces a card after one of its buttons was pressed, keeping keyboard focus on the same button. */
function swapCard(card, next) {
  const index = [...card.querySelectorAll('button')].indexOf(document.activeElement);
  card.replaceWith(next);
  if (index >= 0) next.querySelectorAll('button')[index]?.focus();
}

/** Name of what's on screen: "Overall" with one person, else "Household" or the person's name. */
function viewLabel() {
  if (!hasPartner()) return 'Overall';
  return person === 'household' ? 'Household' : personName(person);
}

function tile(label, value, sub, cls = '') {
  return h('div', { class: 'tile' },
    h('div', { class: 'tile-label', text: label }),
    h('div', { class: `tile-value ${cls}`, text: value }),
    h('div', { class: `tile-sub ${cls}`, text: sub }));
}

/** Live during market hours (intraday quotes every 15 min), otherwise the last close. */
function pricesNote(data) {
  if (data.live_as_of) return `Live prices as of ${fmt.time(data.live_as_of)} · updates every 15 min`;
  return data.as_of ? `Prices as of ${fmt.date(data.as_of)} close` : 'Waiting for the first price sync';
}

/** Marks a live-prices view so the app keeps refreshing it until the evening close is stored. */
function liveMarker(el, live) {
  if (live) el.dataset.live = '';
  return el;
}

/** The selected person's Stock Holdings, Automatic Investment and total. */
function summaryTable(views, dayLabel) {
  const total = views.overall.total_value || 0;
  const row = (key, label) => {
    const g = views[key];
    return h('tr', { class: key === 'overall' ? 'total-row' : null },
      h('td', { class: 'text', text: label }),
      h('td', { text: fmt.money(g.total_value) }),
      h('td', { text: fmt.money(g.total_cost) }),
      h('td', { class: tone(g.total_gain) }, `${fmt.signedMoney(g.total_gain)} `, h('span', { class: 'xsmall', text: fmt.signedPct(g.total_gain_pct, 1) })),
      h('td', { class: tone(g.day_change), text: fmt.signedMoney(g.day_change) }),
      h('td', { class: tone(g.return_1y), text: fmt.signedPct(g.return_1y, 1) }),
      h('td', { text: total ? fmt.pct(g.total_value / total) : '—' }));
  };
  return h('section', { class: 'card', 'aria-labelledby': 'sum-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'sum-h', text: `By group · ${viewLabel()}` })),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Group', 'Value', 'Cost basis', 'Gain / loss', dayLabel, '1Y return', 'Share of total']
        .map((t, i) => h('th', { scope: 'col', class: i === 0 ? 'text' : null, text: t })))),
      h('tbody', null, row('stocks', KINDS.stocks.label), row('auto', KINDS.auto.label), row('overall', 'Total')))));
}

/** Value of the current holdings over the chosen range, with the range's return above the chart. */
function performanceCard(summary) {
  const points = summary.history.slice(-RANGES[range] - 1);
  const buttons = Object.keys(RANGES).map((key) => h('button', {
    type: 'button', 'aria-pressed': String(key === range), text: key,
    onClick: () => { range = key; redraw(); },
  }));
  const card = h('section', { class: 'card grow perf-card oneline-head', 'aria-labelledby': 'perf-h' });
  const redraw = () => swapCard(card, performanceCard(summary));
  const head = h('div', { class: 'card-head' }, h('h2', { id: 'perf-h', text: `Performance · ${viewLabel()}` }),
    h('div', { class: 'segmented', role: 'group', 'aria-label': 'Time range' }, buttons));
  if (points.length < 2) {
    add(card, head, h('p', { class: 'muted small', text: 'Not enough price history yet for this view.' }));
    return card;
  }
  const first = points[0];
  const last = points[points.length - 1];
  const change = last.value - first.value;
  const span = range === 'All' ? 'all history' : `the last ${range}`;
  const host = h('div', { class: 'chart-host' });
  add(card, head,
    h('div', { class: 'perf-summary' },
      h('span', { class: `perf-return ${tone(change)}`, text: fmt.signedPct(first.value ? change / first.value : null, 1) }),
      h('span', { class: tone(change), text: fmt.signedMoney(change) }),
      h('span', { class: 'muted small', text: `over ${span} · ${fmt.money(first.value)} → ${fmt.money(last.value)}` })),
    host,
    h('p', { class: 'xsmall muted', style: { margin: '8px 0 0' }, text: "How today's holdings moved; it ignores past buys and sells. Hover or tap the chart for a day's value." }));
  valueChart(host, points, {
    ariaLabel: `${viewLabel()} value over ${span}: from ${fmt.money(first.value)} to ${fmt.money(last.value)}`,
    sinceLabel: range === 'All' ? 'since the start' : `since ${range} ago`,
    dayLabel: (d) => new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: range === '1M' ? 'numeric' : undefined, year: range === 'All' ? '2-digit' : undefined }),
  });
  return card;
}

const TOP_SLICES = 6; // the rest are grouped as "Other" so the list stays short

/** Donut of the view by holding, group (Stock Holdings / Automatic Investment) or person (Household only). */
function allocationCard(summary) {
  const options = [['holding', 'Holdings'], ['kind', 'Group'], ...(person === 'household' && hasPartner() ? [['person', 'Person']] : [])];
  // "Person" only exists on Household; fall back without forgetting the choice for when you return.
  const by = options.some(([key]) => key === breakdown) ? breakdown : 'holding';
  const keyOf = {
    holding: (p) => p.ticker,
    kind: (p) => KINDS[GROUPS[p.group]?.kind]?.label || p.group,
    person: (p) => personName(GROUPS[p.group]?.person),
  }[by];
  const kindOfLabel = Object.fromEntries(Object.entries(KINDS).map(([kind, k]) => [k.label, kind]));
  const colorOf = (name, i) => (by === 'kind' && kindOfLabel[name] ? kindColor(kindOfLabel[name]) : PALETTE[i % PALETTE.length]);
  const sums = new Map();
  for (const p of summary.positions) {
    if (p.value != null) sums.set(keyOf(p), (sums.get(keyOf(p)) || 0) + p.value);
  }
  const total = [...sums.values()].reduce((a, b) => a + b, 0);
  const sorted = [...sums.entries()].sort((a, b) => b[1] - a[1]);
  const rest = sorted.length > TOP_SLICES + 1 ? sorted.slice(TOP_SLICES) : [];
  const kept = rest.length ? [...sorted.slice(0, TOP_SLICES), [`Other (${rest.length})`, rest.reduce((a, [, v]) => a + v, 0)]] : sorted;
  const items = kept.map(([name, value], i) => ({
    name, value, weight: total ? value / total : 0,
    color: rest.length && i === kept.length - 1 ? '#5A6B85' : colorOf(name, i),
  }));
  const tickers = summary.positions.map((p) => p.ticker);
  const inSeveral = by === 'holding' ? [...new Set(tickers.filter((tk, i) => tickers.indexOf(tk) !== i))] : [];
  const card = h('section', { class: 'card side oneline-head', 'aria-labelledby': 'alloc-h' });
  const switcher = h('div', { class: 'segmented', role: 'group', 'aria-label': 'Break down by' },
    options.map(([key, text]) => h('button', {
      type: 'button', 'aria-pressed': String(key === by), text,
      onClick: () => { breakdown = key; swapCard(card, allocationCard(summary)); },
    })));
  add(card, h('div', { class: 'card-head' }, h('h2', { id: 'alloc-h', text: `Allocation · ${viewLabel()}` }), switcher));
  if (!items.length) {
    add(card, h('p', { class: 'muted', text: 'Nothing in this group yet.' }));
    return card;
  }
  add(card,
    h('div', { class: 'alloc-top' },
      donut(items, { label: 'Total', value: compactMoney(total), ariaLabel: `Allocation: ${items.map((x) => `${x.name} ${fmt.pct(x.weight)}`).join(', ')}` }),
      h('div', { class: 'alloc-list' }, items.map((x) => h('div', { class: 'alloc-row' },
        h('span', { class: 'dot', style: { background: x.color } }),
        h('strong', { class: 'alloc-name', text: x.name }),
        h('span', { class: 'muted small', text: compactMoney(x.value) }),
        h('span', { class: 'mono', text: fmt.pct(x.weight) }))))),
    rest.length ? h('p', { class: 'xsmall muted', style: { margin: '12px 0 0' }, text: `Other: ${rest.map(([name]) => name).join(', ')}` }) : null,
    inSeveral.length ? h('p', { class: 'xsmall muted', style: { margin: '8px 0 0' }, text: `${inSeveral.join(', ')} ${inSeveral.length === 1 ? 'is' : 'are'} held in more than one group; each counts once here.` }) : null);
  return card;
}

function compactMoney(v) {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}k`;
  return fmt.money(v);
}

const MOVERS = 3;

function moverRows(positions, sign) {
  // By the dollar sign, so a tiny move on a big position (rounds to 0.00%) still counts.
  const rows = positions
    .filter((p) => p.day_change != null && Math.sign(p.day_change) === sign)
    .sort((a, b) => sign * ((b.day_change_pct ?? 0) - (a.day_change_pct ?? 0)))
    .slice(0, MOVERS);
  if (!rows.length) return h('div', { class: 'mover-empty', text: sign > 0 ? 'No gainers' : 'No losers' });
  return rows.map((p) => h('div', { class: 'mover-row' },
    h('strong', { text: p.ticker }),
    h('span', { class: `pct ${tone(p.day_change_pct)}`, text: fmt.signedPct(p.day_change_pct) }),
    h('span', { class: `amt ${tone(p.day_change)}`, text: fmt.signedMoney(p.day_change) })));
}

function moverLists(positions) {
  if (!positions.some((p) => p.price != null)) return h('div', { class: 'mover-empty', text: `No ${KINDS.stocks.label} yet.` });
  return [
    h('div', null, h('div', { class: 'mover-label', text: 'Gainers' }), moverRows(positions, 1)),
    h('div', null, h('div', { class: 'mover-label', text: 'Losers' }), moverRows(positions, -1)),
  ];
}

/** Top gainers and losers in Stock Holdings: a column per person on Household, else that person's two lists. */
function moversCard(portfolios, live) {
  const stocks = (who) => portfolios[who].stocks.positions;
  const title = (who) => `${hasPartner() ? `${possessive(who)} ` : ''}${KINDS.stocks.label}`;
  const people = person === 'household' && hasPartner() ? ['mine', 'partner'] : null;
  const body = people
    ? people.map((who) => h('div', { class: 'mover-person' }, h('h3', { text: title(who) }), moverLists(stocks(who))))
    : moverLists(stocks(person === 'household' ? 'mine' : person));
  const scope = people ? KINDS.stocks.label : title(person);
  return h('section', { class: 'card', 'aria-labelledby': 'movers-h' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'movers-h', text: `${live ? "Today's" : "Last trading day's"} movers · ${scope}` }),
      h('span', { class: 'muted small', text: `Top ${MOVERS} each way by % change · ${KINDS.auto.label} not included` })),
    h('div', { class: 'movers' }, body));
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

const HOLDING_COLUMNS = {
  // [key, label, text column, hidden on phones]
  value: [['ticker', 'Ticker', true], ['group', 'Group', true], ['shares', 'Shares', false, true], ['avg_cost', 'Avg cost', false, true],
    ['price', 'Price'], ['day_change_pct', 'Today'], ['value', 'Value'], ['weight', 'Weight'], ['gain', 'Gain / loss']],
  perf: [['ticker', 'Ticker', true], ['group', 'Group', true, true], ['return_3m', '3M'], ['return_6m', '6M'], ['return_ytd', 'YTD'],
    ['return_1y', '1Y'], ['return_3y_ann', '3Y / yr']],
};
const PERIODS = ['return_3m', 'return_6m', 'return_ytd', 'return_1y', 'return_3y_ann'];

/** Performance rows: one per ticker (a fund in several groups shows once), with its period returns. */
function performanceRows(positions) {
  const byTicker = new Map();
  for (const p of positions) {
    const label = groupLabel(p.group, { short: true, withPerson: hasPartner() && person === 'household' });
    const row = byTicker.get(p.ticker);
    if (row) { row.groups.push(label); row.kinds.add(GROUPS[p.group]?.kind); continue; }
    byTicker.set(p.ticker, { ticker: p.ticker, groups: [label], kinds: new Set([GROUPS[p.group]?.kind]), ...Object.fromEntries(PERIODS.map((k) => [k, returns.get(p.ticker)?.[k] ?? null])) });
  }
  return [...byTicker.values()].map((row) => ({ ...row, group: row.groups.join(', ') }));
}

/** Sortable holdings of the view: Value or Performance columns, filtered to both groups, Stocks or Auto. */
function holdingsCard(summary) {
  const positions = summary.positions.filter((p) => kindFilter === 'both' || GROUPS[p.group]?.kind === kindFilter);
  const perf = holdingsMode === 'perf';
  const rows = perf ? performanceRows(positions) : positions;
  const sort = sorts[holdingsMode];
  const maxWeight = Math.max(0, ...positions.map((p) => p.weight || 0));
  const cell = (p, key) => (key === 'group' ? (perf ? p.group : groupLabel(p.group)) : p[key]);
  const sorted = [...rows].sort((a, b) => {
    const A = cell(a, sort.key);
    const B = cell(b, sort.key);
    if (A == null || B == null) return (A == null) - (B == null); // missing values (no price or history yet) last
    return (typeof A === 'string' ? A.localeCompare(B) : A - B) * sort.dir;
  });
  const card = h('section', { class: 'card oneline-head', 'aria-labelledby': 'hold-h' });
  const redraw = () => swapCard(card, holdingsCard(summary));
  const head = h('tr', null, HOLDING_COLUMNS[holdingsMode].map(([key, label, text, narrowHidden]) => {
    const active = key === sort.key;
    return h('th', {
      scope: 'col', class: [text ? 'text' : null, narrowHidden ? 'hide-narrow' : null].filter(Boolean).join(' ') || null,
      'aria-sort': active ? (sort.dir > 0 ? 'ascending' : 'descending') : null,
    }, h('button', {
      type: 'button', class: 'sort-btn',
      onClick: () => { sorts[holdingsMode] = active ? { key, dir: -sort.dir } : { key, dir: text ? 1 : -1 }; redraw(); },
    }, label, h('span', { class: 'sort-arrow', 'aria-hidden': 'true', text: active ? (sort.dir > 0 ? '▲' : '▼') : '' })));
  }));
  const chip = (group, kinds) => h('span', { class: `group-chip ${kinds ? (kinds.size === 1 ? KINDS[[...kinds][0]]?.chip || '' : 'chip-stocks') : groupChip(group)}`,
    text: kinds ? group : groupLabel(group, { short: true, withPerson: hasPartner() && person === 'household' }) });
  const body = sorted.map((p) => (perf
    ? h('tr', null,
      h('td', { class: 'text' }, h('strong', { text: p.ticker })),
      h('td', { class: 'text hide-narrow' }, chip(p.group, p.kinds)),
      PERIODS.map((key) => h('td', { class: tone(p[key]), text: fmt.signedPct(p[key], 1) })))
    : h('tr', null,
      h('td', { class: 'text' }, h('strong', { text: p.ticker })),
      h('td', { class: 'text' }, chip(p.group)),
      h('td', { class: 'hide-narrow', text: fmt.num(p.shares, 5) }),
      h('td', { class: 'hide-narrow', text: fmt.money(p.avg_cost) }),
      h('td', { text: p.price == null ? 'Loading…' : fmt.money(p.price) }),
      h('td', { class: tone(p.day_change) }, `${fmt.signedPct(p.day_change_pct)} `, h('span', { class: 'xsmall', text: fmt.signedMoney(p.day_change) })),
      h('td', { text: fmt.money(p.value) }),
      h('td', null, h('span', { class: 'weight' }, fmt.pct(p.weight),
        h('span', { class: 'weight-track', 'aria-hidden': 'true' },
          h('span', { class: 'weight-fill', style: { width: `${maxWeight ? ((p.weight || 0) / maxWeight) * 100 : 0}%` } })))),
      h('td', { class: tone(p.gain) }, `${fmt.signedMoney(p.gain)} `, h('span', { class: 'xsmall', text: fmt.signedPct(p.gain_pct, 1) })))));
  const segmented = (label, options, current, choose) => h('div', { class: 'segmented', role: 'group', 'aria-label': label },
    options.map(([key, text]) => h('button', { type: 'button', 'aria-pressed': String(key === current), text, onClick: () => { choose(key); redraw(); } })));
  add(card,
    h('div', { class: 'card-head' }, h('h2', { id: 'hold-h', text: `Holdings · ${viewLabel()}` }),
      h('div', { class: 'card-tools' },
        h('span', { class: 'muted small', text: `${rows.length} ${perf ? 'ticker' : 'position'}${rows.length === 1 ? '' : 's'}` }),
        segmented('Columns', [['value', 'Value'], ['perf', 'Performance']], holdingsMode, (key) => { holdingsMode = key; }),
        segmented('Show group', [['both', 'Both'], ['stocks', 'Stocks'], ['auto', 'Auto']], kindFilter, (key) => { kindFilter = key; }))),
    rows.length
      ? h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, head), h('tbody', null, body)))
      : h('p', { class: 'muted', text: 'Nothing in this group yet.' }),
    rows.length ? h('p', { class: 'xsmall muted', style: { marginBottom: 0 }, text: perf
      ? 'Total return of each fund or stock (dividends included) as of the last close; not your own return, which depends on when you bought. 3Y is per year. Tap a column to sort.'
      : 'Tap a column to sort. Weight is the share of this whole view.' }) : null);
  return card;
}

export async function renderOverview(main) {
  const [data, rulesRes, signalsRes] = await Promise.all([api('GET', '/portfolio'), api('GET', '/rules'), api('GET', '/signals')]);
  returns = new Map(signalsRes.signals.map((s) => [s.ticker, s]));
  const { portfolios } = data;
  if (!hasPartner()) person = 'household';
  if (!portfolios.household.overall.positions.length) {
    fill(main, pageHead('Overview'), h('div', { class: 'card empty-card' },
      h('h2', { text: 'Add your first holding' }),
      h('p', { class: 'muted', text: 'Record purchases under Stock Holdings or Automatic Investment, and the app tracks value, gains, news and alerts from there.' }),
      h('div', { style: { display: 'flex', gap: '12px', justifyContent: 'center' } },
        h('a', { class: 'btn btn-primary btn-link', href: '#/mine/stocks', text: portfolioTitle('mine') }),
        hasPartner() ? h('a', { class: 'btn btn-ghost btn-link', href: '#/partner/stocks', text: portfolioTitle('partner') }) : null)));
    return;
  }

  const draw = () => {
    const s = portfolios[person].overall;
    const switcher = hasPartner()
      ? h('div', { class: 'segmented segmented-lg', role: 'group', 'aria-label': 'Whose holdings' },
        [['household', 'Household'], ['mine', personName('mine')], ['partner', personName('partner')]].map(([key, text]) => h('button', {
          type: 'button', 'aria-pressed': String(key === person), text, onClick: () => { person = key; draw(); },
        })))
      : null;
    fill(main,
      liveMarker(pageHead('Overview', pricesNote(data), switcher), data.live_as_of),
      summaryTable(portfolios[person], data.live_as_of ? 'Today' : 'Last day'),
      h('section', { class: 'tiles', 'aria-label': `Summary for ${viewLabel()}` },
        tile(`Total value · ${viewLabel()}`, fmt.money(s.total_value), `Cost basis ${fmt.money(s.total_cost)}`),
        tile('Total gain', fmt.signedMoney(s.total_gain), `${fmt.signedPct(s.total_gain_pct)} all time`, tone(s.total_gain)),
        tile(data.live_as_of ? 'Today' : 'Last trading day', fmt.signedMoney(s.day_change), fmt.signedPct(s.day_change_pct), tone(s.day_change)),
        tile(`1Y return · ${viewLabel()}`, fmt.signedPct(s.return_1y, 1),
          s.return_1y == null ? 'Needs a year of price history' : 'Current holdings over the last year', tone(s.return_1y))),
      moversCard(portfolios, data.live_as_of),
      h('div', { class: 'row' }, performanceCard(s), allocationCard(s)),
      holdingsCard(s),
      alertsCard(rulesRes.rules),
      h('div', { class: 'footer-note' },
        h('span', { text: 'Performance shows how current holdings moved; it ignores past buys and sells.' }),
        h('span', { text: 'Describes data only. Not investment advice.' })),
    );
  };
  draw();
}
