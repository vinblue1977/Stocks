// Stock Holdings page body: buys and sales (first-in, first-out), each holding's history, and realized gains.
import { api } from '../api.js';
import { possessive } from '../groups.js';
import { add, fill, fmt, h, stackTable, toast, tone } from '../ui.js';

const today = () => new Date().toISOString().slice(0, 10);
const TYPE_LABEL = { opening: 'Opening position', buy: 'Buy', sell: 'Sale' };
let tradeType = 'buy';
let openTicker = null;
let pendingTrade = null; // a row's Buy/Sell pressed while the form was in the other mode: applied after the redraw

/** Same rule as the server (portfolio/ledger.py): oldest lots sold first; long term = held more than a year. */
function replay(txns) {
  const order = { opening: 0, buy: 1, sell: 2 };
  const sorted = [...txns].sort((a, b) => (a.date || '0000-00-00').localeCompare(b.date || '0000-00-00') || order[a.type] - order[b.type]
    || String(a.txn_id || '').localeCompare(String(b.txn_id || '')));
  const lots = [];
  const realized = [];
  for (const t of sorted) {
    if (t.type !== 'sell') { lots.push({ date: t.date, shares: Number(t.shares), price: Number(t.price) }); continue; }
    let left = Number(t.shares);
    while (left > 1e-9 && lots.length) {
      const lot = lots[0];
      const used = Math.min(lot.shares, left);
      realized.push({ shares: used, gain: used * (Number(t.price) - lot.price), long_term: longTerm(lot.date, t.date) });
      lot.shares -= used;
      left -= used;
      if (lot.shares <= 1e-9) lots.shift();
    }
    if (left > 1e-9) return null; // more than held
  }
  const shares = lots.reduce((a, l) => a + l.shares, 0);
  return { shares, cost: lots.reduce((a, l) => a + l.shares * l.price, 0), realized };
}

function longTerm(bought, sold) {
  if (!bought) return null;
  const [y, m, d] = bought.split('-').map(Number);
  const anniversary = `${y + 1}-${String(m).padStart(2, '0')}-${String(m === 2 && d === 29 ? 28 : d).padStart(2, '0')}`; // as the server: Feb 29 -> Feb 28
  return sold > anniversary;
}

const termTag = (term) => h('span', {
  class: `tag ${term === true ? 'tag-pos' : term === false ? 'tag-neg' : 'tag-neutral'}`,
  text: term === true ? 'Long term' : term === false ? 'Short term' : 'Term unknown',
});

export async function renderStockHoldings(main, { person, group, head, rerender, maxTickers }) {
  const [holdingsRes, portfolio, realizedRes] = await Promise.all([api('GET', '/holdings'), api('GET', '/portfolio'), api('GET', '/realized')]);
  const mine = holdingsRes.holdings.filter((x) => x.group === group).sort((a, b) => a.ticker.localeCompare(b.ticker));
  const summary = portfolio.portfolios[person].stocks;
  const priced = new Map(summary.positions.map((p) => [p.ticker, p]));
  const sales = realizedRes.sales.filter((s) => s.group === group);
  const year = String(new Date().getFullYear());
  const realizedYear = sales.filter((s) => s.sold.startsWith(year)).reduce((a, s) => a + s.gain, 0);
  const base = (ticker) => `/groups/${group}/holdings/${encodeURIComponent(ticker)}/transactions`;
  const history = new Map(); // ticker -> { transactions, implicit_opening }, loaded on demand

  async function ledgerFor(ticker) {
    if (!history.has(ticker)) {
      history.set(ticker, mine.some((x) => x.ticker === ticker) ? await api('GET', base(ticker)) : { transactions: [], implicit_opening: false });
    }
    return history.get(ticker);
  }
  const txnsFor = async (ticker) => (await ledgerFor(ticker)).transactions;

  // ---------- record a trade
  const tTicker = h('input', { id: 't-ticker', required: true, maxlength: 10, autocomplete: 'off', placeholder: 'NVDA', list: 'group-tickers', class: 'upper' });
  const tShares = h('input', { id: 't-shares', type: 'number', required: true, min: '0.000001', step: 'any', inputmode: 'decimal', placeholder: '2' });
  const tPrice = h('input', { id: 't-price', type: 'number', required: true, min: '0', step: 'any', inputmode: 'decimal', placeholder: '226.32' });
  const tDate = h('input', { id: 't-date', type: 'date', required: true, max: today(), value: today() });
  const tSubmit = h('button', { type: 'submit', class: 'btn btn-primary', text: tradeType === 'sell' ? 'Record sale' : 'Record purchase' });
  const preview = h('p', { class: 'small preview', 'aria-live': 'polite' });
  const error = h('p', { class: 'form-error', role: 'alert' });
  let previewSeq = 0;

  async function updatePreview() {
    const seq = ++previewSeq;
    const ticker = tTicker.value.trim().toUpperCase();
    const shares = Number(tShares.value);
    const price = Number(tPrice.value);
    if (!ticker || !(shares > 0) || !(price > 0)) {
      preview.textContent = tradeType === 'sell'
        ? 'Enter a ticker, shares and sale price to see the realized gain before saving.'
        : 'Enter a ticker, shares and price to see the new average before saving.';
      return;
    }
    let txns;
    try { txns = await txnsFor(ticker); } catch { return; }
    if (seq !== previewSeq) return; // a newer keystroke owns the preview
    const before = replay(txns) || { shares: 0, cost: 0 };
    if (tradeType === 'buy') {
      const total = before.shares + shares;
      fill(preview, `${ticker}: ${fmt.num(total, 6)} shares, average cost ${fmt.money((before.cost + shares * price) / total)}`,
        before.shares ? ` (was ${fmt.money(before.cost / before.shares)}).` : '.');
      return;
    }
    const after = replay([...txns, { type: 'sell', date: tDate.value || today(), shares, price }]);
    if (!after) { preview.textContent = `You hold ${fmt.num(before.shares, 6)} shares of ${ticker} on that date; you can't sell ${fmt.num(shares, 6)}.`; return; }
    const pieces = after.realized.slice(replay(txns).realized.length);
    const sum = (term) => pieces.filter((p) => p.long_term === term).reduce((a, p) => a + p.gain, 0);
    const unknown = sum(null);
    fill(preview, `Realized gain ${fmt.signedMoney(pieces.reduce((a, p) => a + p.gain, 0))}: `,
      h('span', { class: tone(sum(true)), text: `long term ${fmt.signedMoney(sum(true))}` }), ' · ',
      h('span', { class: tone(sum(false)), text: `short term ${fmt.signedMoney(sum(false))}` }),
      unknown ? ` · term unknown ${fmt.signedMoney(unknown)} (set the opening date in History)` : null,
      `. ${fmt.num(after.shares, 6)} shares left.`);
  }
  [tTicker, tShares, tPrice, tDate].forEach((el) => el.addEventListener('input', updatePreview));
  updatePreview();

  const form = h('form', { class: 'form', novalidate: true },
    h('div', { class: 'field' }, h('label', { for: 't-ticker', text: 'Ticker' }), tTicker),
    h('div', { class: 'field' }, h('label', { for: 't-shares', text: 'Shares' }), tShares),
    h('div', { class: 'field' }, h('label', { for: 't-price', text: tradeType === 'sell' ? 'Sale price per share ($)' : 'Price per share ($)' }), tPrice),
    h('div', { class: 'field' }, h('label', { for: 't-date', text: 'Trade date' }), tDate),
    tSubmit);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    if (!form.checkValidity()) { error.textContent = 'Check the ticker, shares, price and date.'; return; }
    tSubmit.disabled = true;
    const ticker = tTicker.value.trim().toUpperCase();
    try {
      const res = await api('POST', base(ticker), { type: tradeType, shares: Number(tShares.value), price: Number(tPrice.value), date: tDate.value });
      toast(res.holding ? `${tradeType === 'sell' ? 'Sale' : 'Purchase'} recorded: ${fmt.num(res.holding.shares, 6)} ${ticker} shares now.`
        : `Sold all ${ticker}. It leaves the list; the sale stays under Realized gains.`);
      rerender();
    } catch (e) { error.textContent = e.message; tSubmit.disabled = false; }
  });
  const typeSwitch = h('div', { class: 'segmented trade-type', role: 'group', 'aria-label': 'Trade type' },
    [['buy', 'Buy'], ['sell', 'Sell']].map(([key, text]) => h('button', {
      type: 'button', 'aria-pressed': String(key === tradeType), text,
      onClick: () => { tradeType = key; rerender(); },
    })));
  const tradeCard = h('section', { class: 'card', 'aria-labelledby': 'trade-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'trade-h', text: 'Record a trade' }),
      h('span', { class: 'muted small', text: 'Sales use first-in, first-out: your oldest shares count as sold first.' })),
    typeSwitch, form, preview, error);
  const startTrade = (ticker, type) => {
    if (type !== tradeType) { tradeType = type; pendingTrade = ticker; rerender(); return; }
    tTicker.value = ticker;
    updatePreview();
    tShares.focus();
    tradeCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  // ---------- holdings and their history
  function historyRow(x) {
    const cell = h('td', { colspan: 7 }, h('p', { class: 'muted small', text: 'Loading…' }));
    ledgerFor(x.ticker).then(({ transactions: txns, implicit_opening: implicit }) => {
      const hasSales = txns.some((t) => t.type === 'sell');
      const rows = txns.map((t) => {
        const setDate = t.type === 'opening' && !t.date
          ? h('input', { type: 'date', max: today(), 'aria-label': `Purchase date for the ${x.ticker} opening position`,
            onChange: async (e) => {
              try { await api('PUT', `${base(x.ticker)}/${encodeURIComponent(t.txn_id)}`, { date: e.target.value }); toast('Opening date saved.'); rerender(); } catch (err) { toast(err.message, true); }
            } })
          : null;
        const remove = implicit ? h('span', { class: 'xsmall muted', text: 'from your totals' }) : h('button', {
          type: 'button', class: 'btn btn-danger btn-sm', text: 'Delete', 'aria-label': `Delete ${TYPE_LABEL[t.type]} of ${x.ticker} on ${t.date || 'unknown date'}`,
          onClick: async () => {
            try { await api('DELETE', `${base(x.ticker)}/${encodeURIComponent(t.txn_id)}`); toast('Transaction deleted.'); rerender(); } catch (err) { toast(err.message, true); }
          },
        });
        return h('tr', null,
          h('td', { class: 'text' }, t.date ? fmt.date(t.date) : h('span', null, 'Date not set ', setDate)),
          h('td', { class: 'text', text: TYPE_LABEL[t.type] }),
          h('td', { text: fmt.num(t.shares, 6) }), h('td', { text: fmt.money(t.price) }), h('td', { text: fmt.money(t.shares * t.price) }),
          h('td', null, remove));
      });
      const removeHolding = hasSales ? null : h('button', {
        type: 'button', class: 'btn btn-danger btn-sm', text: `Remove ${x.ticker}`,
        onClick: async () => {
          try { await api('DELETE', `/groups/${group}/holdings/${encodeURIComponent(x.ticker)}`); toast(`${x.ticker} removed.`); openTicker = null; rerender(); } catch (err) { toast(err.message, true); }
        },
      });
      fill(cell,
        h('div', { class: 'table-wrap' }, stackTable(h('table', null,
          h('thead', null, h('tr', null, ['Date', 'Type', 'Shares', 'Price', 'Total', ''].map((t, i) => h('th', { scope: 'col', class: i < 2 ? 'text' : null, text: t })))),
          h('tbody', null, rows)))),
        h('div', { class: 'history-foot' },
          h('span', { class: 'xsmall muted', text: implicit
            ? 'This opening position comes from your saved totals. Set its date for correct long/short term, or record trades to start the history.'
            : 'Deleting a buy that a later sale relies on is blocked.' }),
          removeHolding));
    }).catch((e) => fill(cell, h('p', { class: 'neg small', text: e.message })));
    return h('tr', { class: 'history-row' }, cell);
  }

  const rows = mine.flatMap((x) => {
    const p = priced.get(x.ticker) || {};
    const isOpen = openTicker === x.ticker;
    const row = h('tr', null,
      h('td', { class: 'text' }, h('strong', { text: x.ticker })),
      h('td', { text: fmt.num(x.shares, 6) }),
      h('td', { text: fmt.money(x.avg_cost) }),
      h('td', { text: p.price == null ? 'Loading…' : fmt.money(p.price) }),
      h('td', { text: fmt.money(p.value) }),
      h('td', { class: tone(p.gain) }, h('div', { text: fmt.signedMoney(p.gain) }), h('div', { class: 'sub', text: fmt.signedPct(p.gain_pct, 1) })),
      h('td', null, h('div', { class: 'actions' },
        h('button', { type: 'button', class: 'btn btn-ghost', text: isOpen ? 'Hide history' : 'History', 'aria-expanded': String(isOpen),
          onClick: () => { openTicker = isOpen ? null : x.ticker; rerender(); } }),
        h('button', { type: 'button', class: 'btn btn-ghost', text: 'Buy', 'aria-label': `Record a purchase of ${x.ticker}`, onClick: () => startTrade(x.ticker, 'buy') }),
        h('button', { type: 'button', class: 'btn btn-ghost', text: 'Sell', 'aria-label': `Record a sale of ${x.ticker}`, onClick: () => startTrade(x.ticker, 'sell') }))));
    return [row, isOpen ? historyRow(x) : null];
  });

  // ---------- realized gains
  const sum = (term) => sales.filter((s) => s.sold.startsWith(year) && s.long_term === term).reduce((a, s) => a + s.gain, 0);
  const box = (label, value) => h('div', { class: 'rbox' }, h('div', { class: 'small muted', text: label }), h('div', { class: `rbox-value ${tone(value)}`, text: fmt.signedMoney(value) }));
  const realizedCard = h('section', { class: 'card', 'aria-labelledby': 'rg-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'rg-h', text: 'Realized gains' }),
      h('span', { class: 'muted small', text: 'From sales in these Stock Holdings · first-in, first-out' })),
    h('div', { class: 'realized-grid' }, box(`${year} total`, realizedYear), box('Long term (held over a year)', sum(true)), box('Short term', sum(false))),
    sales.length
      ? h('div', { class: 'table-wrap' }, stackTable(h('table', null,
        h('thead', null, h('tr', null, ['Sold', 'Ticker', 'Shares', 'Bought', 'Proceeds', 'Cost', 'Gain', 'Term']
          .map((t, i) => h('th', { scope: 'col', class: i < 2 || i === 7 ? 'text' : null, text: t })))),
        h('tbody', null, sales.map((s) => h('tr', null,
          h('td', { class: 'text', text: fmt.date(s.sold) }), h('td', { class: 'text' }, h('strong', { text: s.ticker })),
          h('td', { text: fmt.num(s.shares, 6) }), h('td', { text: s.bought ? fmt.date(s.bought) : '—' }),
          h('td', { text: fmt.money(s.proceeds) }), h('td', { text: fmt.money(s.cost) }),
          h('td', { class: tone(s.gain), text: fmt.signedMoney(s.gain) }), h('td', { class: 'text' }, termTag(s.long_term)))))), { lead: 1 }))
      : h('p', { class: 'muted', text: 'No sales yet.' }),
    realizedRes.unreadable.length ? h('p', { class: 'small neg', style: { margin: '10px 0 0' }, text: `Couldn't read the trades for ${realizedRes.unreadable.join(', ')}: a sale is larger than the shares held then. Check its History.` }) : null,
    h('p', { class: 'xsmall muted', style: { margin: '10px 0 0' }, text: "For your own tracking; your broker's tax forms are the official numbers." }));

  fill(main,
    h('datalist', { id: 'group-tickers' }, [...new Set(holdingsRes.holdings.map((x) => x.ticker))].map((t) => h('option', { value: t }))),
    ...head({
      note: `${mine.length} position${mine.length === 1 ? '' : 's'} · ${new Set(holdingsRes.holdings.map((x) => x.ticker)).size} of ${maxTickers} tickers across all groups · buys and sales tracked`,
      stats: [['Value', fmt.money(summary.total_value)], ['Cost basis', fmt.money(summary.total_cost)],
        ['Unrealized', fmt.signedMoney(summary.total_gain), tone(summary.total_gain)],
        [`Realized in ${year}`, fmt.signedMoney(realizedYear), tone(realizedYear)]],
    }),
    tradeCard,
    add(h('section', { class: 'card', 'aria-labelledby': 'list-h' }),
      h('div', { class: 'card-head' }, h('h2', { id: 'list-h', text: `${possessive(person)} stock holdings` })),
      mine.length
        ? h('div', { class: 'table-wrap' }, stackTable(h('table', null,
          h('thead', null, h('tr', null, ['Ticker', 'Shares', 'Avg cost', 'Price', 'Value', 'Gain / loss', ''].map((t, i) => h('th', { scope: 'col', class: i === 0 ? 'text' : null, text: t })))),
          h('tbody', null, rows))))
        : h('p', { class: 'muted', text: 'Nothing here yet. Record the first purchase above.' }),
      h('p', { class: 'xsmall muted', style: { margin: '10px 0 0' }, text: 'Each holding starts with one opening position from your saved totals. Set its date in History for correct long/short term.' })),
    realizedCard);
  if (pendingTrade) { const ticker = pendingTrade; pendingTrade = null; startTrade(ticker, tradeType); }
}
