import { api } from '../api.js';
import { LAST_VISIT, SAVED_OVERVIEW, currentEmail } from '../auth.js';
import { donut, valueChart } from '../charts.js';
import { GROUPS, KINDS, groupChip, groupLabel, hasPartner, personName, portfolioTitle, possessive } from '../groups.js';
import { EARNINGS_HOUR, add, fill, fmt, h, icon, pageHead, tone } from '../ui.js';

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
let returnsAt = 0;
let earnings = []; // upcoming earnings reports (GET /earnings), refreshed with the returns

/** Period returns change once each evening: fetch at most hourly (not on every 15-minute refresh), and never
 *  let a failure take down the Overview; Performance then shows "—". */
async function loadReturns() {
  if (Date.now() - returnsAt < 60 * 60 * 1000) return;
  // Each loads on its own, so one failing doesn't blank the other.
  const [signalsRes, earningsRes] = await Promise.allSettled([api('GET', '/signals'), api('GET', '/earnings')]);
  if (signalsRes.status === 'fulfilled') returns = new Map(signalsRes.value.signals.map((s) => [s.ticker, s]));
  if (earningsRes.status === 'fulfilled') earnings = earningsRes.value.earnings;
  if (signalsRes.status === 'fulfilled' && earningsRes.status === 'fulfilled') returnsAt = Date.now();
}

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

/** Minutes since midnight in New York (market time). */
function nyMinutes(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: 'numeric', hourCycle: 'h23' }).formatToParts(date);
  const part = (type) => Number(parts.find((p) => p.type === type)?.value || 0);
  return part('hour') * 60 + part('minute');
}

/** Live during market hours (intraday quotes every 15 min), otherwise the last close. Between the
 *  close and the evening price sync the last quotes stay, and are labeled as closing prices.
 *  Whether the market is open comes from the server, not this device's clock. */
function pricesNote(data) {
  if (data.live_as_of) {
    return data.market?.open === false
      ? `Market closed · prices as of ${fmt.time(data.live_as_of)} · official close added this evening`
      : `Live prices as of ${fmt.time(data.live_as_of)} · updates every 15 min`;
  }
  return data.as_of ? `Prices as of ${fmt.date(data.as_of)} close` : 'Waiting for the first price sync';
}

/** Holiday or early-close notice for today's US market, or nothing on a normal day. */
function marketBanner(data) {
  const m = data.market;
  if (!m) return null;
  const lastClose = data.as_of ? new Date(`${data.as_of}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : null;
  // data-market-closed tells the app (app.js) not to keep re-fetching: nothing will change until the evening.
  if (m.holiday) {
    return h('div', { class: 'market-banner', role: 'status', 'data-market-closed': '' }, h('strong', { text: `Markets are closed today for ${m.holiday}.` }),
      lastClose ? ` Prices are from the last close (${lastClose}).` : '');
  }
  if (m.early_close) {
    if (nyMinutes() >= 13 * 60) {
      return h('div', { class: 'market-banner', role: 'status', 'data-market-closed': '' }, h('strong', { text: 'Markets closed early today, at 1:00 PM ET.' }),
        ' Prices are from that close until the evening update stores it.');
    }
    return h('div', { class: 'market-banner', role: 'status' }, h('strong', { text: 'Markets close early today at 1:00 PM ET.' }), ' Live prices stop then.');
  }
  return null;
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


/** Next earnings reports for held and watched stocks (funds don't report). Hidden when there are none. */
function earningsCard() {
  if (!earnings.length) return null;
  const today = new Date(`${new Date().toLocaleDateString('en-CA')}T12:00:00`);
  const daysAway = (d) => Math.round((new Date(`${d}T12:00:00`) - today) / 864e5);
  const whereLabel = (place) => (place === 'watchlist' ? 'Watchlist' : groupLabel(place, { short: true, withPerson: hasPartner() }));
  const soon = earnings.filter((e) => daysAway(e.date) <= 7);
  const weekday = (d) => new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short' });
  return h('section', { class: 'card', 'aria-labelledby': 'er-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'er-h', text: 'Upcoming earnings' }),
      h('span', { class: 'muted small', text: "Your stocks and watchlist · next 60 days · funds don't report earnings" })),
    soon.length ? h('div', { class: 'er-banner', text: `This week: ${soon.map((e) => `${e.ticker} ${weekday(e.date)}${e.hour ? ` ${EARNINGS_HOUR[e.hour].toLowerCase()}` : ''}`).join(' · ')}` }) : null,
    h('div', null, earnings.map((e) => {
      const n = daysAway(e.date);
      return h('div', { class: 'er' },
        h('span', { class: 'er-day' }, fmt.shortDate(e.date), h('small', { class: n <= 7 ? 'soon' : null, text: n <= 0 ? 'today' : n === 1 ? 'tomorrow' : `in ${n} days` })),
        h('strong', { text: e.ticker }),
        h('span', null, e.hour ? EARNINGS_HOUR[e.hour] : 'Time not announced', h('div', { class: 'xsmall muted', text: e.where.map(whereLabel).join(', ') })),
        h('span', { class: 'er-est mono small muted', text: e.eps_estimate == null ? '' : `EPS est. ${fmt.money(e.eps_estimate)}` }));
    })),
    h('p', { class: 'xsmall muted', style: { margin: '10px 0 0' }, text: 'Dates and estimates from Finnhub, refreshed each evening; companies sometimes move them.' }));
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
  const chip = (group, kinds) => h('span', { class: `group-chip ${kinds ? (kinds.size === 1 ? KINDS[[...kinds][0]]?.chip || '' : 'chip-mixed') : groupChip(group)}`,
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

const SAVED_MAX_AGE = 3 * 24 * 60 * 60 * 1000;

/** Keeps the last Overview data on this device so the next open can draw it straight away. */
function saveOverview(data, rules) {
  try {
    localStorage.setItem(SAVED_OVERVIEW, JSON.stringify({
      email: currentEmail(), at: Date.now(), data, rules, returns: [...returns.values()], earnings,
    }));
  } catch { /* storage full or blocked: just no instant view next time */ }
}

function savedOverview() {
  try {
    const saved = JSON.parse(localStorage.getItem(SAVED_OVERVIEW));
    if (saved?.email && saved.email === currentEmail() && Date.now() - saved.at < SAVED_MAX_AGE) return saved;
    localStorage.removeItem(SAVED_OVERVIEW); // another account's, or too old
  } catch { /* unreadable: ignore */ }
  return null;
}

/** Draws the saved Overview, marked with its age, while fresh data loads (or after loading failed).
 *  Returns false if there is none. */
export function renderSavedOverview(main, failed = false) {
  const saved = savedOverview();
  if (!saved) return false;
  if (!returns.size) {
    returns = new Map((saved.returns || []).map((r) => [r.ticker, r]));
    earnings = saved.earnings || [];
  }
  // Saved live prices are no longer live, and a saved holiday banner may be yesterday's: drop both.
  const data = { ...saved.data, live_as_of: null, market: null };
  drawOverview(main, data, saved.rules, `Saved ${fmt.ago(new Date(saved.at).toISOString())} · ${failed ? "couldn't update" : 'Updating…'}`);
  return true;
}

// "Since your last visit": the first Overview load in each app session compares with the visit before it,
// if that was 4+ hours ago. Every load then records this visit (time and household total) on the device.
const AWAY_MS = 4 * 60 * 60 * 1000;
let lastVisit; // undefined until the session's first load; then the earlier visit, or null
let changes = null; // GET /changes since that visit
let sinceDismissed = false;

function readLastVisit() {
  try {
    const v = JSON.parse(localStorage.getItem(LAST_VISIT));
    return v?.email && v.email === currentEmail() && Date.now() - v.at >= AWAY_MS ? v : null;
  } catch { return null; }
}

function recordVisit(data) {
  try {
    localStorage.setItem(LAST_VISIT, JSON.stringify({ email: currentEmail(), at: Date.now(), value: data.portfolios.household.overall.total_value }));
  } catch { /* storage blocked: no "since" strip next time */ }
}

export async function renderOverview(main) {
  const first = lastVisit === undefined;
  const earlier = first ? readLastVisit() : null;
  const [data, rulesRes, changesRes] = await Promise.all([api('GET', '/portfolio'), api('GET', '/rules'), loadReturns(),
    earlier ? api('GET', `/changes?since=${encodeURIComponent(new Date(earlier.at).toISOString())}`).catch(() => null) : null]);
  if (first) { lastVisit = earlier; changes = changesRes; }
  recordVisit(data);
  saveOverview(data, rulesRes.rules);
  drawOverview(main, data, rulesRes.rules, null);
}

/** The strip at the top: value change, alerts fired and new headlines since the last visit. */
function sinceStrip(data) {
  if (!lastVisit || sinceDismissed) return null;
  const now = data.portfolios.household.overall.total_value;
  const delta = typeof lastVisit.value === 'number' && now != null ? now - lastVisit.value : null;
  const alerts = changes?.alerts || [];
  const headlines = changes?.headlines || 0;
  if (delta == null && !alerts.length && !headlines) return null;
  const when = new Date(lastVisit.at).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' });
  const strip = h('section', { class: 'since', 'aria-labelledby': 'since-h' },
    h('div', { class: 'since-when' },
      h('span', null, h('strong', { id: 'since-h', text: 'Since your last visit' }), ` · ${when}`),
      h('button', { type: 'button', class: 'dismiss', text: 'Dismiss', onClick: () => { sinceDismissed = true; strip.remove(); } })),
    h('div', { class: 'since-items' },
      delta != null ? h('span', { class: 'pill' }, `${hasPartner() ? 'Household' : 'Total'} `,
        h('span', { class: `mono ${tone(delta)}`, text: `${fmt.signedMoney(delta)}${lastVisit.value ? ` (${fmt.signedPct(delta / lastVisit.value, 1)})` : ''}` })) : null,
      alerts.length ? h('a', { class: 'pill alerting', href: '#/alerts', text: `${alerts.length} alert${alerts.length === 1 ? '' : 's'} fired` }) : null,
      headlines ? h('a', { class: 'pill', href: '#/news', text: `${headlines} new headline${headlines === 1 ? '' : 's'}` }) : null),
    alerts.length ? h('ul', { class: 'since-alerts' }, alerts.slice(0, 3).map((a) => h('li', null,
      h('time', { datetime: a.created_at, text: new Date(a.created_at).toLocaleDateString('en-US', { weekday: 'short' }) }), a.message))) : null);
  return strip;
}

/** Warns when last night's price sync or news run didn't arrive (nothing on a normal day). */
function lateBanner(data) {
  const f = data.freshness;
  if (!f || (!f.prices_late && !f.news_late)) return null;
  const day = (iso) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  const newsDay = f.news_at ? new Date(f.news_at).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : null;
  return h('section', { class: 'late', role: 'status' }, icon('warn', 'currentColor'),
    h('div', null,
      h('strong', { text: f.prices_late ? "The evening price update didn't arrive. " : "The evening news update didn't arrive. " }),
      f.prices_late && data.as_of ? `Prices are from the ${day(data.as_of)} close, so these numbers may be out of date. ` : '',
      f.news_late && newsDay ? `News summaries are from ${newsDay}. ` : '',
      'It usually catches up on the next evening run.'));
}

/** `savedNote` marks a saved copy shown instead of fresh data. */
function drawOverview(main, data, rules, savedNote) {
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
      liveMarker(pageHead('Overview', savedNote ? `${pricesNote(data)} · ${savedNote}` : pricesNote(data), switcher), data.live_as_of),
      savedNote ? null : lateBanner(data),
      marketBanner(data),
      savedNote ? null : sinceStrip(data),
      summaryTable(portfolios[person], data.live_as_of ? 'Today' : 'Last day'),
      h('section', { class: 'tiles', 'aria-label': `Summary for ${viewLabel()}` },
        tile(`Total value · ${viewLabel()}`, fmt.money(s.total_value), `Cost basis ${fmt.money(s.total_cost)}`),
        tile('Total gain', fmt.signedMoney(s.total_gain), `${fmt.signedPct(s.total_gain_pct)} all time`, tone(s.total_gain)),
        tile(data.live_as_of ? 'Today' : 'Last trading day', fmt.signedMoney(s.day_change), fmt.signedPct(s.day_change_pct), tone(s.day_change)),
        tile(`1Y return · ${viewLabel()}`, fmt.signedPct(s.return_1y, 1),
          s.return_1y == null ? 'Needs a year of price history' : 'Current holdings over the last year', tone(s.return_1y))),
      moversCard(portfolios, data.live_as_of),
      earningsCard(),
      h('div', { class: 'row' }, performanceCard(s), allocationCard(s)),
      holdingsCard(s),
      alertsCard(rules),
      h('div', { class: 'footer-note' },
        h('span', { text: 'Performance shows how current holdings moved; it ignores past buys and sells.' }),
        h('span', { text: 'Describes data only. Not investment advice.' })),
    );
  };
  draw();
}
