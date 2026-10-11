import { api } from '../api.js';
import { fmt, h, pageHead, toast } from '../ui.js';

const TICKER_PATTERN = '[A-Za-z][A-Za-z0-9.\\-]{0,9}';
const today = () => new Date().toISOString().slice(0, 10);

function field(label, input, hint) {
  return h('div', { class: 'field' }, h('label', { for: input.id, text: label }), input,
    hint ? h('span', { class: 'xsmall muted', text: hint }) : null);
}

function tickerInput(id) {
  return h('input', { id, name: 'ticker', required: true, maxlength: 10, autocomplete: 'off', placeholder: 'VTI', pattern: TICKER_PATTERN, list: 'held-tickers' });
}

function numberInput(id, placeholder, min) {
  return h('input', { id, type: 'number', required: true, min, step: 'any', inputmode: 'decimal', placeholder });
}

// Shows what the new average will be before saving.
function previewText(holdings, symbol, shares, price) {
  const s = Number(shares);
  const p = Number(price);
  if (!symbol || !(s > 0) || !(p >= 0)) return '';
  const current = holdings.find((x) => x.ticker === symbol.toUpperCase());
  if (!current) return `New holding: ${fmt.num(s, 6)} shares at ${fmt.money(p)}.`;
  const total = current.shares + s;
  const avg = (current.shares * current.cost_basis + s * p) / total;
  return `${current.ticker}: ${fmt.num(current.shares, 6)} → ${fmt.num(total, 6)} shares, average ${fmt.money(current.cost_basis)} → ${fmt.money(avg)}.`;
}

export async function renderHoldings(main) {
  const { holdings } = await api('GET', '/holdings');
  holdings.sort((a, b) => a.ticker.localeCompare(b.ticker));
  const datalist = h('datalist', { id: 'held-tickers' }, holdings.map((x) => h('option', { value: x.ticker })));

  // ---------- record a purchase (updates the weighted average automatically)
  const pTicker = tickerInput('p-ticker');
  const pShares = numberInput('p-shares', '5', '0.000001');
  const pPrice = numberInput('p-price', '280.00', '0');
  const pDate = h('input', { id: 'p-date', type: 'date', max: today() });
  const pSubmit = h('button', { type: 'submit', class: 'btn btn-primary', text: 'Record purchase' });
  const pPreview = h('p', { class: 'small muted', 'aria-live': 'polite', style: { margin: '12px 0 0' } });
  const pError = h('p', { class: 'form-error', role: 'alert' });
  const updatePreview = () => { pPreview.textContent = previewText(holdings, pTicker.value.trim(), pShares.value, pPrice.value); };
  [pTicker, pShares, pPrice].forEach((el) => el.addEventListener('input', updatePreview));

  const purchaseForm = h('form', { class: 'form', novalidate: true },
    field('Ticker', pTicker), field('Shares bought', pShares), field('Price per share ($)', pPrice), field('Date (optional)', pDate), pSubmit);
  purchaseForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    pError.textContent = '';
    if (!purchaseForm.checkValidity()) { pError.textContent = 'Check the ticker, shares and price.'; return; }
    pSubmit.disabled = true;
    const symbol = pTicker.value.trim().toUpperCase();
    try {
      const body = { shares: Number(pShares.value), price: Number(pPrice.value) };
      if (pDate.value) body.purchased_on = pDate.value;
      const res = await api('POST', `/holdings/${encodeURIComponent(symbol)}/purchases`, body);
      const x = res.holding;
      toast(res.previous
        ? `${symbol}: now ${fmt.num(x.shares, 6)} shares at ${fmt.money(x.cost_basis)} average.`
        : `${symbol} added. Price history loads within a minute.`);
      renderHoldings(main);
    } catch (e) {
      pError.textContent = e.message;
      pSubmit.disabled = false;
    }
  });

  // ---------- set a holding directly (e.g. copy totals from your broker)
  const sTicker = tickerInput('s-ticker');
  const sShares = numberInput('s-shares', '15', '0.000001');
  const sCost = numberInput('s-cost', '260.00', '0');
  const sDate = h('input', { id: 's-date', type: 'date', max: today() });
  const sSubmit = h('button', { type: 'submit', class: 'btn btn-ghost', text: 'Save totals' });
  const sError = h('p', { class: 'form-error', role: 'alert' });
  const setForm = h('form', { class: 'form', novalidate: true },
    field('Ticker', sTicker), field('Total shares', sShares), field('Average cost per share ($)', sCost), field('First purchase (optional)', sDate), sSubmit);
  setForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    sError.textContent = '';
    if (!setForm.checkValidity()) { sError.textContent = 'Check the ticker, shares and cost.'; return; }
    sSubmit.disabled = true;
    const symbol = sTicker.value.trim().toUpperCase();
    try {
      const body = { shares: Number(sShares.value), cost_basis: Number(sCost.value) };
      if (sDate.value) body.purchased_on = sDate.value;
      const isNew = !holdings.some((x) => x.ticker === symbol);
      await api('PUT', `/holdings/${encodeURIComponent(symbol)}`, body);
      toast(isNew ? `${symbol} added. Price history loads within a minute.` : `${symbol} updated.`);
      renderHoldings(main);
    } catch (e) {
      sError.textContent = e.message;
      sSubmit.disabled = false;
    }
  });

  // ---------- table
  const rows = holdings.map((x) => {
    let armed = false;
    const remove = h('button', { type: 'button', class: 'btn btn-danger', text: 'Remove', 'aria-label': `Remove ${x.ticker}` });
    remove.addEventListener('click', async () => {
      if (!armed) { armed = true; remove.textContent = 'Confirm remove'; setTimeout(() => { armed = false; remove.textContent = 'Remove'; }, 4000); return; }
      remove.disabled = true;
      try { await api('DELETE', `/holdings/${encodeURIComponent(x.ticker)}`); toast(`${x.ticker} removed.`); renderHoldings(main); } catch (e) { toast(e.message, true); remove.disabled = false; }
    });
    const buyMore = h('button', {
      type: 'button', class: 'btn btn-ghost', text: 'Buy more', 'aria-label': `Record a purchase of ${x.ticker}`,
      onClick: () => { pTicker.value = x.ticker; updatePreview(); pShares.focus(); purchaseForm.scrollIntoView({ behavior: 'smooth', block: 'center' }); },
    });
    const edit = h('button', {
      type: 'button', class: 'btn btn-ghost', text: 'Edit totals', 'aria-label': `Edit totals for ${x.ticker}`,
      onClick: () => { sTicker.value = x.ticker; sShares.value = x.shares; sCost.value = x.cost_basis; sDate.value = x.purchased_on || ''; sShares.focus(); setForm.scrollIntoView({ behavior: 'smooth', block: 'center' }); },
    });
    return h('tr', null,
      h('td', { class: 'text' }, h('strong', { text: x.ticker })),
      h('td', { text: fmt.num(x.shares, 6) }),
      h('td', { text: fmt.money(x.cost_basis) }),
      h('td', { text: fmt.money(x.shares * x.cost_basis) }),
      h('td', { class: 'text', text: x.purchased_on ? fmt.date(x.purchased_on) : '—' }),
      h('td', null, h('div', { class: 'actions' }, buyMore, edit, remove)));
  });

  main.replaceChildren(
    datalist,
    pageHead('Holdings', `${holdings.length} of 40 holdings`),
    h('section', { class: 'card', 'aria-labelledby': 'buy-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'buy-h', text: 'Record a purchase' }),
        h('span', { class: 'muted small', text: 'Adds to your shares and recalculates the average cost.' })),
      purchaseForm, pPreview, pError),
    h('section', { class: 'card', 'aria-labelledby': 'list-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'list-h', text: 'Your holdings' })),
      holdings.length
        ? h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, ['Ticker', 'Shares', 'Avg cost', 'Cost basis', 'First purchase', ''].map((t, i) => h('th', { scope: 'col', class: i === 0 || i === 4 ? 'text' : null, text: t })))),
          h('tbody', null, rows)))
        : h('p', { class: 'muted', text: 'No holdings yet. Record your first purchase above.' })),
    h('section', { class: 'card', 'aria-labelledby': 'set-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'set-h', text: 'Set totals directly' }),
        h('span', { class: 'muted small', text: "Use your broker's total shares and average cost. Replaces what's saved." })),
      setForm, sError),
  );
}
