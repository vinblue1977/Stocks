import { api } from '../api.js';
import { hasPartner, personName } from '../groups.js';
import { add, fill, fmt, h, pageHead, toast, tone } from '../ui.js';

// Starting point offered when no target mix is saved yet (the user edits it before saving).
const EXAMPLE = {
  us: [40, 'VTI'], intl: [20, 'VXUS'], dividend: [10, 'SCHD'], small: [5, 'AVUV'], growth: [10, 'QQQM'], sector: [15, 'SMH'], bonds: [0, 'BND'],
};
let person = 'household';
let editing = false;
let amount = 1000;

const viewLabel = () => (person === 'household' ? 'Household' : personName(person));

/** New money split across the areas below target after adding it, in proportion to the shortfall. */
function split(view, targets, money) {
  const after = view.total_value + money;
  const short = Object.entries(targets.areas)
    .map(([key, t]) => [key, (t.pct / 100) * after - (view.by_area[key] || 0)])
    .filter(([, gap]) => gap > 0);
  const total = short.reduce((a, [, gap]) => a + gap, 0);
  if (money <= 0 || !total) return [];
  return short.map(([key, gap]) => [key, (gap / total) * money]).filter(([, x]) => x >= 1).sort((a, b) => b[1] - a[1]);
}

function tile(label, value, sub, cls = '') {
  return h('div', { class: `tile ${cls}` }, h('div', { class: 'tile-label', text: label }),
    h('div', { class: 'tile-value', text: value }), h('div', { class: 'tile-sub', text: sub }));
}

function tiles(view, data) {
  const r = view.risk;
  const m = r.market || {};
  const [topKey, topValue] = Object.entries(view.by_area).sort((a, b) => b[1] - a[1])[0] || ['', 0];
  const share = view.total_value ? topValue / view.total_value : null;
  const ceiling = data.targets?.sector_ceiling;
  const overCeiling = topKey === 'sector' && ceiling != null && share > ceiling / 100;
  const target = data.targets?.areas?.[topKey]?.pct;
  return h('section', { class: 'tiles', 'aria-label': `Checkup summary for ${viewLabel()}` },
    tile(`Largest area · ${data.areas[topKey] || 'Unassigned'}`, fmt.pct(share, 0),
      overCeiling ? `Your ceiling: ${ceiling}%` : target != null ? `Target ${target}%` : 'No target set', overCeiling ? 'attn' : ''),
    tile('Swings vs. market', r.beta == null ? '—' : `${r.beta.toFixed(1)}×`, `Volatility ${fmt.pct(r.volatility, 0)} vs ${fmt.pct(m.volatility, 0)}`),
    tile('Last 12 months', fmt.signedPct(r.return_1y, 0), `Market ${fmt.signedPct(m.return_1y, 0)} · 2022: ${fmt.signedPct(r.return_2022, 0)}`),
    tile('Spread', view.effective_holdings == null ? '—' : `≈ ${Math.round(view.effective_holdings)} holdings`,
      `${view.holdings_count} tickers, counted by size`));
}

function summaryCard(summary) {
  if (!summary) return null;
  return h('section', { class: 'card', 'aria-labelledby': 'ms-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'ms-h', text: 'Last monthly summary' }),
      h('span', { class: 'muted small', text: `${fmt.date(summary.created_at)} · Claude on Amazon Bedrock` })),
    h('div', { class: 'summary-box', text: summary.summary }),
    h('p', { class: 'xsmall muted', style: { margin: '10px 0 0' }, text: 'Describes your data and your own targets; not investment advice.' }));
}

function mixCard(view, data, rerender) {
  const ceiling = data.targets.sector_ceiling;
  const rows = view.drift.filter((d) => d.now || d.target).map((d) => {
    const over = d.area === 'sector' ? d.now > ceiling / 100 : d.now - d.target > 0.05;
    const near = Math.abs(d.now - d.target) < 0.02;
    return h('div', { class: 'mix-row' },
      h('span', null, h('strong', { text: d.label }), h('div', { class: 'hint', text: `Your fund: ${data.targets.areas[d.area]?.fund || '—'}` })),
      h('div', { class: 'mix-track', role: 'img', 'aria-label': `${d.label}: ${fmt.pct(d.now)} now, target ${fmt.pct(d.target, 0)}` },
        h('div', { class: `mix-fill ${over ? 'over' : ''}`, style: { width: `${Math.min(100, d.now * 100)}%` } }),
        h('div', { class: 'mix-target', style: { left: `calc(${d.target * 100}% - 1px)` } })),
      h('div', { class: 'mix-nums' }, `${fmt.pct(d.now)} `, h('span', { class: 'muted', text: `/ ${fmt.pct(d.target, 0)}` }),
        h('div', { class: near ? 'muted' : d.gap > 0 ? 'neg' : 'pos', text: near ? 'on target' : `${d.gap > 0 ? 'over' : 'under'} by ${fmt.money(Math.abs(d.gap))}` })));
  });
  return h('section', { class: 'card', 'aria-labelledby': 'mix-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'mix-h', text: `Your mix vs. target · ${viewLabel()}` }),
      h('button', { type: 'button', class: 'btn btn-ghost', text: 'Edit target mix', onClick: () => { editing = true; rerender(); } })),
    rows,
    h('p', { class: 'xsmall muted', style: { margin: '12px 0 0' }, text: `White mark = your target. Orange = more than 5 points above target, or above your ${ceiling}% single-sector ceiling.` }));
}

function calcCard(view, data) {
  const card = h('section', { class: 'card side', 'aria-labelledby': 'calc-h' });
  const input = h('input', { id: 'calc-amount', type: 'number', min: 0, step: 100, value: amount, inputmode: 'decimal' });
  const results = h('div', { style: { marginTop: '12px' } });
  const draw = () => {
    const parts = split(view, data.targets, amount);
    fill(results, parts.length
      ? parts.map(([key, x]) => h('div', { class: 'split-row' }, h('span', { text: data.areas[key] }),
        h('span', { class: 'muted small', text: data.targets.areas[key]?.fund || '' }), h('span', { class: 'mono', text: fmt.money(x) })))
      : h('p', { class: 'muted small', text: 'Every area is at or above its target.' }));
  };
  const form = h('form', { class: 'calc', novalidate: true },
    h('div', { class: 'field' }, h('label', { for: 'calc-amount', text: 'Amount' }), input),
    h('button', { type: 'submit', class: 'btn btn-primary', text: 'Split' }));
  form.addEventListener('submit', (event) => { event.preventDefault(); amount = Math.max(0, Number(input.value) || 0); draw(); });
  draw();
  return add(card, h('h2', { id: 'calc-h', text: 'Where the next dollars would go' }),
    h('p', { class: 'small muted', style: { margin: '6px 0 12px' }, text: 'Splits new money across the areas furthest below your target. No selling.' }),
    form, results);
}

function riskCard(view) {
  const r = view.risk;
  const m = r.market || {};
  const rows = [
    ['Last 12 months', fmt.signedPct(r.return_1y, 1), fmt.signedPct(m.return_1y, 1)],
    ['Volatility (yearly)', fmt.pct(r.volatility, 0), fmt.pct(m.volatility, 0)],
    ['Beta vs. market', r.beta == null ? '—' : r.beta.toFixed(2), '1.00'],
    ['2022 (calendar year)', fmt.signedPct(r.return_2022, 1), fmt.signedPct(m.return_2022, 1)],
    ['Worst drop, 2022', fmt.signedPct(r.worst_drop_2022, 1), fmt.signedPct(m.worst_drop_2022, 1)],
    ['Worst drop, 12 months', fmt.signedPct(r.worst_drop_1y, 1), '—'],
    ['Worst drop, 5 years', fmt.signedPct(r.worst_drop_5y, 1), '—'],
  ];
  return h('section', { class: 'card grow', 'aria-labelledby': 'risk-h' },
    h('h2', { id: 'risk-h', text: 'Risk vs. the market' }),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['', viewLabel(), 'Market (VTI)'].map((t, i) => h('th', { scope: 'col', class: i === 0 ? 'text' : null, text: t })))),
      h('tbody', null, rows.map(([label, mine, market]) => h('tr', null,
        h('td', { class: 'text', text: label }), h('td', { class: label.startsWith('Last') ? tone(r.return_1y) : null, text: mine }),
        h('td', { class: 'muted', text: market })))))),
    h('p', { class: 'xsmall muted', style: { margin: '10px 0 0' }, text: "Today's shares replayed over past prices: how this mix behaves, not what you earned." }));
}

function editor(data, rerender) {
  const saved = data.targets;
  const values = Object.fromEntries(Object.keys(data.areas).map((key) => [key, {
    pct: saved ? saved.areas[key]?.pct ?? 0 : EXAMPLE[key][0],
    fund: saved ? saved.areas[key]?.fund ?? '' : EXAMPLE[key][1],
  }]));
  const overrides = { ...(saved?.overrides || {}) };
  const total = h('span', { class: 'sum' });
  const error = h('p', { class: 'form-error', role: 'alert' });
  const update = () => {
    const sum = Object.values(values).reduce((a, v) => a + (Number(v.pct) || 0), 0);
    total.textContent = `${sum}%`;
    total.className = `sum ${sum === 100 ? 'pos' : 'neg'}`;
    error.textContent = sum === 100 ? '' : `Targets add up to ${sum}%; they need to add up to 100%.`;
    return sum;
  };
  const rows = Object.entries(data.areas).map(([key, label]) => h('div', { class: 'ed-row' },
    h('div', null, h('strong', { text: label }), h('div', { class: 'hint', text: `Shortlist: ${data.shortlist[key]}` })),
    h('input', { type: 'number', min: 0, max: 100, step: 1, value: values[key].pct, 'aria-label': `${label} target %`,
      onInput: (e) => { values[key].pct = Number(e.target.value) || 0; update(); } }),
    h('input', { type: 'text', value: values[key].fund, maxlength: 10, class: 'upper', 'aria-label': `${label} fund`,
      onInput: (e) => { values[key].fund = e.target.value.toUpperCase().trim(); } })));
  // Every holding with its area; unrecognised ones first. Changing one saves it as your own choice.
  const tickers = Object.entries(data.assigned)
    .sort(([a, x], [b, y]) => (x !== 'unassigned') - (y !== 'unassigned') || a.localeCompare(b));
  const unassigned = tickers.filter(([, area]) => area === 'unassigned').length;
  const assignRows = tickers.map(([ticker, area]) => h('div', { class: 'ed-row' }, h('strong', { text: ticker }),
    h('select', { 'aria-label': `Area for ${ticker}`, onChange: (e) => { overrides[ticker] = e.target.value; } },
      area === 'unassigned' && !overrides[ticker] ? h('option', { value: '', text: 'Choose an area', selected: true }) : null,
      Object.entries(data.areas).map(([key, label]) => h('option', { value: key, text: label, selected: (overrides[ticker] || area) === key })))));
  const ceiling = h('input', { id: 'ceiling', type: 'number', min: 0, max: 100, step: 1, value: saved?.sector_ceiling ?? 15 });
  const save = h('button', { type: 'button', class: 'btn btn-primary', text: 'Save' });
  save.addEventListener('click', async () => {
    if (update() !== 100) return;
    save.disabled = true;
    try {
      await api('PUT', '/targets', {
        areas: values,
        sector_ceiling: Number(ceiling.value) || 0,
        overrides: Object.fromEntries(Object.entries(overrides).filter(([, area]) => area)),
      });
      toast('Target mix saved.');
      editing = false;
      rerender(true);
    } catch (e) { error.textContent = e.message; save.disabled = false; }
  });
  update();
  return [
    pageHead('Your target mix', "Set the share you want in each area and the one fund you'd use for it. The Checkup measures against this."),
    h('section', { class: 'card', 'aria-labelledby': 'ed-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'ed-h', text: 'Areas' }), h('span', { class: 'small' }, 'Total ', total)),
      h('div', { class: 'ed-row ed-head' }, h('span', { class: 'hint', text: 'Area' }), h('span', { class: 'hint', text: 'Target %' }), h('span', { class: 'hint', text: 'Your fund' })),
      rows,
      h('div', { class: 'form', style: { marginTop: '18px' } },
        h('div', { class: 'field' }, h('label', { for: 'ceiling', text: 'Single-sector ceiling (%)' }), ceiling),
        h('div', { style: { display: 'flex', gap: '8px' } }, save,
          saved ? h('button', { type: 'button', class: 'btn btn-ghost', text: 'Cancel', onClick: () => { editing = false; rerender(); } }) : null)),
      error),
    h('section', { class: 'card', 'aria-labelledby': 'un-h' },
      h('h2', { id: 'un-h', text: 'Which area each holding counts toward' }),
      h('p', { class: 'small muted', style: { margin: '6px 0 10px' }, text: unassigned
        ? `${unassigned} holding${unassigned === 1 ? ' isn\'t' : 's aren\'t'} recognised yet; choose an area so ${unassigned === 1 ? 'it counts' : 'they count'} toward your mix.`
        : 'Known funds are placed automatically; change any of them here.' }),
      assignRows),
    h('div', { class: 'footer-note' }, h('span', { text: 'You choose the targets and funds; the app only measures. Not investment advice.' })),
  ];
}

export async function renderCheckup(main) {
  const data = await api('GET', '/checkup');
  const rerender = (refetch = false) => (refetch ? renderCheckup(main) : draw());
  const draw = () => {
    if (!hasPartner() || !data.views[person]) person = 'household';
    if (editing || !data.targets) {
      fill(main, ...editor(data, rerender));
      return;
    }
    const view = data.views[person];
    const switcher = hasPartner()
      ? h('div', { class: 'segmented segmented-lg', role: 'group', 'aria-label': 'Whose holdings' },
        [['household', 'Household'], ['mine', personName('mine')], ['partner', personName('partner')]]
          .filter(([key]) => data.views[key])
          .map(([key, text]) => h('button', { type: 'button', 'aria-pressed': String(key === person), text, onClick: () => { person = key; draw(); } })))
      : null;
    fill(main,
      pageHead('Checkup', `How your mix compares with the target you set · prices as of ${fmt.date(data.as_of)}`, switcher),
      tiles(view, data),
      person === 'household' ? summaryCard(data.summary) : null, // the monthly summary covers the household
      mixCard(view, data, rerender),
      h('div', { class: 'row' }, riskCard(view), calcCard(view, data)),
      h('div', { class: 'footer-note' }, h('span', { text: 'You choose the targets and funds; the app only measures. Not investment advice.' })));
  };
  draw();
}
