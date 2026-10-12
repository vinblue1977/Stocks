// A person's portfolio page: a switch between their Stock Holdings and Automatic Investment.
import { api } from '../api.js';
import { KINDS, MAX_TICKERS, groupFor, possessive, portfolioTitle } from '../groups.js';
import { fmt, h, toast, tone } from '../ui.js';

const TICKER_PATTERN = '[A-Za-z][A-Za-z0-9.\\-]{0,9}';
const today = () => new Date().toISOString().slice(0, 10);

function field(label, input) {
  return h('div', { class: 'field' }, h('label', { for: input.id, text: label }), input);
}

function tickerInput(id) {
  return h('input', { id, required: true, maxlength: 10, autocomplete: 'off', placeholder: 'VOO', pattern: TICKER_PATTERN, list: 'group-tickers' });
}

function numberInput(id, placeholder, min) {
  return h('input', { id, type: 'number', required: true, min, step: 'any', inputmode: 'decimal', placeholder });
}

function stat(label, value, cls = '') {
  return h('div', null, h('div', { class: 'xsmall muted', text: label }),
    h('div', { class: `mono ${cls}`, style: { fontSize: '20px', fontWeight: '500', marginTop: '4px' }, text: value }));
}

export async function renderPerson(main, person, kind) {
  const group = groupFor(person, kind);
  const otherKind = kind === 'stocks' ? 'auto' : 'stocks';
  const otherGroup = groupFor(person, otherKind);
  const info = { label: `${possessive(person)} ${KINDS[kind].label}` }; // e.g. "Sam's Stock Holdings"
  const rerender = () => renderPerson(main, person, kind);
  const [holdingsRes, portfolio] = await Promise.all([api('GET', '/holdings'), api('GET', '/portfolio')]);
  const all = holdingsRes.holdings;
  const mine = all.filter((x) => x.group === group).sort((a, b) => a.ticker.localeCompare(b.ticker));
  const otherTickers = new Set(all.filter((x) => x.group === otherGroup).map((x) => x.ticker));
  const uniqueCount = new Set(all.map((x) => x.ticker)).size;
  const summary = portfolio.portfolios[person][kind];
  const priced = new Map(summary.positions.map((p) => [p.ticker, p]));
  const base = (ticker) => `/groups/${group}/holdings/${encodeURIComponent(ticker)}`;
  // Automatic Investment works in dollar totals (amount invested, cost basis); Stock Holdings per share.
  const totals = kind === 'auto';
  const costOf = (x) => x.shares * x.cost_basis;

  // ---------- record a purchase
  const pTicker = tickerInput('p-ticker');
  const pShares = numberInput('p-shares', '2', '0.000001');
  const pPrice = numberInput('p-price', totals ? '200.00' : '700.00', '0');
  const pDate = h('input', { id: 'p-date', type: 'date', max: today() });
  const pSubmit = h('button', { type: 'submit', class: 'btn btn-primary', text: 'Record purchase' });
  const pPreview = h('p', { class: 'small preview', 'aria-live': 'polite' });
  const pError = h('p', { class: 'form-error', role: 'alert' });

  function updatePreview() {
    const symbol = pTicker.value.trim().toUpperCase();
    const s = Number(pShares.value);
    const p = Number(pPrice.value);
    if (!symbol || !(s > 0) || !(p >= 0) || pPrice.value === '') {
      pPreview.textContent = 'Enter a ticker, shares and price to see the new average before saving.';
      return;
    }
    const current = mine.find((x) => x.ticker === symbol);
    const spent = totals ? p : s * p; // dollars for this purchase
    if (current) {
      const shares = current.shares + s;
      const basis = costOf(current) + spent;
      pPreview.textContent = totals
        ? `${symbol}: ${fmt.num(current.shares, 6)} → ${fmt.num(shares, 6)} shares, cost basis ${fmt.money(costOf(current))} → ${fmt.money(basis)} (${fmt.money(spent / s)} per share this time).`
        : `${symbol}: ${fmt.num(current.shares, 6)} → ${fmt.num(shares, 6)} shares, average ${fmt.money(current.cost_basis)} → ${fmt.money(basis / shares)}.`;
    } else {
      const note = otherTickers.has(symbol) ? ` (${symbol} is also in ${KINDS[otherKind].label}; that holding is not changed.)` : '';
      pPreview.textContent = totals
        ? `New in ${info.label}: ${fmt.num(s, 6)} shares of ${symbol}, cost basis ${fmt.money(spent)} (${fmt.money(spent / s)} per share).${note}`
        : `New in ${info.label}: ${fmt.num(s, 6)} shares of ${symbol} at ${fmt.money(p)}.${note}`;
    }
  }
  [pTicker, pShares, pPrice].forEach((el) => el.addEventListener('input', updatePreview));
  updatePreview();

  const purchaseForm = h('form', { class: 'form', novalidate: true },
    field('Ticker', pTicker), field('Shares bought', pShares), field(totals ? 'Amount of this buy ($)' : 'Price per share ($)', pPrice),
    field('Date (optional)', pDate), pSubmit);
  purchaseForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    pError.textContent = '';
    if (!purchaseForm.checkValidity()) { pError.textContent = 'Check the ticker, shares and price.'; return; }
    pSubmit.disabled = true;
    const symbol = pTicker.value.trim().toUpperCase();
    try {
      const body = { shares: Number(pShares.value), [totals ? 'amount' : 'price']: Number(pPrice.value) };
      if (pDate.value) body.purchased_on = pDate.value;
      const res = await api('POST', `${base(symbol)}/purchases`, body);
      toast(res.previous
        ? `${symbol}: now ${fmt.num(res.holding.shares, 6)} shares at ${fmt.money(res.holding.cost_basis)} average.`
        : `${symbol} added to ${info.label}.${otherTickers.has(symbol) ? '' : ' Price history loads within a minute.'}`);
      rerender();
    } catch (e) {
      pError.textContent = e.message;
      pSubmit.disabled = false;
    }
  });

  // ---------- set totals directly
  const sTicker = tickerInput('s-ticker');
  const sShares = numberInput('s-shares', '15', '0.000001');
  const sCost = numberInput('s-cost', totals ? '4175.39' : '260.00', '0');
  const sDate = h('input', { id: 's-date', type: 'date', max: today() });
  const sSubmit = h('button', { type: 'submit', class: 'btn btn-ghost', text: 'Save totals' });
  const sError = h('p', { class: 'form-error', role: 'alert' });
  const setForm = h('form', { class: 'form', novalidate: true },
    field('Ticker', sTicker), field('Total shares', sShares), field(totals ? 'Cost basis (total $ invested)' : 'Average cost per share ($)', sCost),
    field('First purchase (optional)', sDate), sSubmit);
  setForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    sError.textContent = '';
    if (!setForm.checkValidity()) { sError.textContent = 'Check the ticker, shares and cost.'; return; }
    sSubmit.disabled = true;
    const symbol = sTicker.value.trim().toUpperCase();
    try {
      const body = { shares: Number(sShares.value), [totals ? 'total_cost' : 'cost_basis']: Number(sCost.value) };
      if (sDate.value) body.purchased_on = sDate.value;
      const isNew = !mine.some((x) => x.ticker === symbol);
      await api('PUT', base(symbol), body);
      toast(isNew ? `${symbol} added to ${info.label}.` : `${symbol} updated.`);
      rerender();
    } catch (e) {
      sError.textContent = e.message;
      sSubmit.disabled = false;
    }
  });

  // ---------- table
  const rows = mine.map((x) => {
    const p = priced.get(x.ticker) || {};
    let armed = false;
    const remove = h('button', { type: 'button', class: 'btn btn-danger', text: 'Remove', 'aria-label': `Remove ${x.ticker} from ${info.label}` });
    remove.addEventListener('click', async () => {
      if (!armed) { armed = true; remove.textContent = 'Confirm remove'; setTimeout(() => { armed = false; remove.textContent = 'Remove'; }, 4000); return; }
      remove.disabled = true;
      try { await api('DELETE', base(x.ticker)); toast(`${x.ticker} removed from ${info.label}.`); rerender(); } catch (e) { toast(e.message, true); remove.disabled = false; }
    });
    const buyMore = h('button', {
      type: 'button', class: 'btn btn-ghost', text: 'Buy more', 'aria-label': `Record a purchase of ${x.ticker}`,
      onClick: () => { pTicker.value = x.ticker; updatePreview(); pShares.focus(); purchaseForm.scrollIntoView({ behavior: 'smooth', block: 'center' }); },
    });
    const edit = h('button', {
      type: 'button', class: 'btn btn-ghost', text: 'Edit totals', 'aria-label': `Edit totals for ${x.ticker}`,
      onClick: () => { sTicker.value = x.ticker; sShares.value = x.shares; sCost.value = totals ? costOf(x).toFixed(2) : x.cost_basis; sDate.value = x.purchased_on || ''; sShares.focus(); setForm.scrollIntoView({ behavior: 'smooth', block: 'center' }); },
    });
    return h('tr', null,
      h('td', { class: 'text' }, h('div', null, h('strong', { text: x.ticker })),
        otherTickers.has(x.ticker) ? h('div', { class: 'sub muted', text: `Also in ${KINDS[otherKind].label} (tracked separately)` }) : null),
      h('td', { text: fmt.num(x.shares, 6) }),
      h('td', { text: fmt.money(totals ? costOf(x) : x.cost_basis) }),
      h('td', { text: p.price == null ? 'Loading…' : fmt.money(p.price) }),
      h('td', { text: fmt.money(p.value) }),
      h('td', { class: tone(p.gain) }, h('div', { text: fmt.signedMoney(p.gain) }), h('div', { class: 'sub', text: fmt.signedPct(p.gain_pct, 1) })),
      h('td', null, h('div', { class: 'actions' }, buyMore, edit, remove)));
  });

  main.replaceChildren(
    h('datalist', { id: 'group-tickers' }, [...new Set(all.map((x) => x.ticker))].map((t) => h('option', { value: t }))),
    h('div', null, h('h1', { text: portfolioTitle(person) }),
      h('div', { class: 'segmented segmented-lg sub-switch', role: 'group', 'aria-label': `${possessive(person)} holdings` },
        Object.entries(KINDS).map(([key, k]) => h('button', {
          type: 'button', 'aria-pressed': String(key === kind), text: k.label,
          onClick: () => { location.hash = `#/${person}/${key}`; },
        })))),
    h('div', { class: 'page-head' },
      h('div', null, h('h2', { class: 'section-title', text: KINDS[kind].label }),
        h('p', { class: 'muted small', text: `${mine.length} position${mine.length === 1 ? '' : 's'} · ${uniqueCount} of ${MAX_TICKERS} tickers across all groups` })),
      h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '24px' } },
        stat('Value', fmt.money(summary.total_value)),
        stat('Cost basis', fmt.money(summary.total_cost)),
        stat('Gain / loss', fmt.signedMoney(summary.total_gain), tone(summary.total_gain)))),
    h('section', { class: 'card', 'aria-labelledby': 'buy-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'buy-h', text: 'Record a purchase' }),
        h('span', { class: 'muted small', text: totals ? 'Enter the shares and dollars from each automatic buy; the cost basis adds up.' : 'Adds to the shares in this group and recalculates the average cost.' })),
      purchaseForm, pPreview, pError),
    h('section', { class: 'card', 'aria-labelledby': 'list-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'list-h', text: `${possessive(person)} ${KINDS[kind].label.toLowerCase()}` })),
      mine.length
        ? h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, ['Ticker', 'Shares', totals ? 'Cost basis' : 'Avg cost', 'Price', 'Value', 'Gain / loss', ''].map((t, i) => h('th', { scope: 'col', class: i === 0 ? 'text' : null, text: t })))),
          h('tbody', null, rows)))
        : h('p', { class: 'muted', text: `Nothing in ${info.label} yet. Record the first purchase above.` })),
    h('section', { class: 'card', 'aria-labelledby': 'set-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'set-h', text: 'Set totals directly' }),
        h('span', { class: 'muted small', text: totals ? "Use the plan's total shares and total invested. Replaces what's saved in this group." : "Use the broker's total shares and average cost. Replaces what's saved in this group." })),
      setForm, sError),
  );
}
