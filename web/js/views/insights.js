// "Analysts & insiders" (GET /insights): analyst rating counts and insider trades from Finnhub, refreshed
// each evening for held and watched stocks. A table for many stocks, and a detail card for one.
import { fmt, h } from '../ui.js';

const ROUTINE = { A: 'Award', G: 'Gift', M: 'Option exercise', F: 'Held for tax', C: 'Conversion', D: 'Returned to company' };
const NOTE = 'Bought and sold count open-market trades only (dollars · number of trades). Awards, gifts, option exercises and shares held for tax are other filings.';

const monthName = (period, style = 'short') => new Date(`${period}-15T12:00:00`).toLocaleDateString('en-US', { month: style });
const compact = (v) => `$${Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v)}`;

/** Buy | Hold | Sell as one bar; the counts are always written next to it, so color isn't the only cue. */
function ratingBar({ buy, hold, sell }, wide = false) {
  const parts = [[buy, 'buy', 'Buy'], [hold, 'hold', 'Hold'], [sell, 'sell', 'Sell']].filter(([n]) => n);
  return h('div', { class: `abar${wide ? ' wide' : ''}`, role: 'img', 'aria-label': `${buy} buy, ${hold} hold, ${sell} sell` },
    parts.map(([n, cls, label]) => h('span', { class: cls, style: { flex: String(n) }, title: `${label}: ${n}` })));
}

function legend() {
  return h('div', { class: 'legend small' },
    [['buy', 'Buy (incl. strong buy)'], ['hold', 'Hold'], ['sell', 'Sell (incl. strong sell)']]
      .map(([cls, text]) => h('span', null, h('span', { class: `dot abar-${cls}`, 'aria-hidden': 'true' }), text)));
}

/** "+2 Buy vs. Jun" between the newest and oldest stored month, or null with only one month. */
function buyChange(analysts) {
  if (!analysts || analysts.length < 2) return null;
  const first = analysts[analysts.length - 1];
  const diff = analysts[0].buy - first.buy;
  return diff ? `${diff > 0 ? '+' : '−'}${Math.abs(diff)} Buy vs. ${monthName(first.period)}` : `No change since ${monthName(first.period)}`;
}

/** "$1M · 2"; when a trade has no price its dollars are unknown, so the total is marked as at least that. */
const tradeCell = (value, count, unpriced) => (count ? `${unpriced ? '≥ ' : ''}${compact(value)} · ${count}` : '—');

const tickerLink = (ticker, cls) => h('a', { class: cls, href: `#/news?ticker=${encodeURIComponent(ticker)}`, text: ticker });

/** iPhone layout: one stacked block per stock, so nothing scrolls sideways. */
function stackedItem(item) {
  const a = item.analysts?.[0];
  const ins = item.insiders || {};
  const trades = [
    ins.sells ? `sold ${tradeCell(ins.sold, ins.sells, ins.unpriced)}` : null,
    ins.buys ? `bought ${tradeCell(ins.bought, ins.buys, ins.unpriced)}` : null,
  ].filter(Boolean);
  return h('div', { class: 'ai-item' },
    tickerLink(item.ticker, 'ai-ticker'),
    h('span', { class: 'xsmall muted', text: buyChange(item.analysts) || '' }),
    a ? ratingBar(a, true) : null,
    h('div', { class: 'ai-line', text: a ? `${monthName(a.period)}: ${a.buy} buy · ${a.hold} hold · ${a.sell} sell` : 'No analyst ratings' }),
    h('div', { class: 'ai-line' }, 'Insiders, 90d: ', h('b', { text: trades.length ? trades.join(' · ') : 'no open-market trades' }),
      ins.routine ? ` · ${ins.routine} other` : ''));
}

/** One row per stock (a table on wider screens, stacked blocks on a phone); a ticker opens its detail on News & signals. */
export function insightsCard(items, { id, subtitle, footnote = '' }) {
  if (!items.length) return null;
  const rows = items.map((item) => {
    const a = item.analysts?.[0];
    const ins = item.insiders || {};
    return h('tr', null,
      h('td', { class: 'text' }, tickerLink(item.ticker)),
      h('td', { class: 'text' }, a
        ? [ratingBar(a), h('div', { class: 'xsmall muted mono', text: `${monthName(a.period)}: ${a.buy} buy · ${a.hold} hold · ${a.sell} sell` })]
        : h('span', { class: 'muted', text: 'No ratings' })),
      h('td', { class: 'text small', text: buyChange(item.analysts) || '—' }),
      h('td', { text: tradeCell(ins.bought, ins.buys, ins.unpriced) }),
      h('td', { text: tradeCell(ins.sold, ins.sells, ins.unpriced) }),
      h('td', { class: 'text small muted', text: ins.routine ? `${ins.routine} other` : '—' }));
  });
  return h('section', { class: 'card', 'aria-labelledby': id },
    h('div', { class: 'card-head' }, h('h2', { id, text: 'Analysts & insiders' }), h('span', { class: 'muted small', text: subtitle })),
    legend(),
    h('div', { class: 'ai-stack' }, items.map(stackedItem)),
    h('div', { class: 'table-wrap ai-table', style: { marginTop: '12px' } }, h('table', null,
      h('thead', null, h('tr', null, ['Stock', 'Latest analyst ratings', 'Change', 'Insiders bought, 90d', 'Insiders sold, 90d', 'Other filings']
        .map((t, i) => h('th', { scope: 'col', class: i < 3 || i === 5 ? 'text' : null, text: t })))),
      h('tbody', null, rows))),
    h('p', { class: 'xsmall muted', style: { margin: '12px 0 0' }, text: `${NOTE}${footnote}` }));
}

function tradeTag(code) {
  if (code === 'S') return h('span', { class: 'tag tag-neg', text: 'Sold' });
  if (code === 'P') return h('span', { class: 'tag tag-pos', text: 'Bought' });
  return h('span', { class: 'tag tag-neutral', text: ROUTINE[code] || 'Other' });
}

/** Monthly ratings and each insider trade for one stock. */
export function insightsDetail(item) {
  if (!item) return null;
  const analysts = item.analysts || [];
  const ins = item.insiders || { trades: [] };
  const a = analysts[0];
  const openMarket = ins.buys || ins.sells
    ? `Open market: ${ins.sells} sale${ins.sells === 1 ? '' : 's'}${ins.sells ? ` (${compact(ins.sold)})` : ''}, ${ins.buys ? `${ins.buys} purchase${ins.buys === 1 ? '' : 's'} (${compact(ins.bought)})` : 'no purchases'}.`
    : 'No open-market trades.';
  return h('section', { class: 'card', 'aria-labelledby': 'ins-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'ins-h', text: `Analysts & insiders: ${item.ticker}` }),
      h('span', { class: 'muted small', text: item.fetched_at ? `Updated ${fmt.ago(item.fetched_at)}` : 'Refreshed each evening' })),
    h('div', { class: 'insights' },
      h('div', null,
        h('h3', { text: 'Analyst ratings by month' }),
        a ? [
          h('p', { class: 'insights-fact', text: `${a.buy} of ${a.buy + a.hold + a.sell} analysts rate it Buy in ${monthName(a.period, 'long')}${buyChange(analysts) ? ` (${buyChange(analysts).replace('Buy', 'buy')})` : ''}.` }),
          h('div', { class: 'months' }, analysts.map((m) => [
            h('span', { class: 'muted', text: monthName(m.period) }),
            ratingBar(m, true),
            h('span', { class: 'mono xsmall muted', text: `${m.buy} · ${m.hold} · ${m.sell}` }),
          ])),
          h('div', { style: { marginTop: '12px' } }, legend()),
        ] : h('p', { class: 'muted', text: 'No analyst ratings for this stock.' })),
      h('div', null,
        h('h3', { text: 'Insider trades, last 90 days' }),
        h('p', { class: 'insights-fact', text: openMarket }),
        ins.trades?.length ? h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, ['Date', 'Who', 'Trade', 'Shares', 'Price'].map((t, i) => h('th', { scope: 'col', class: i < 3 ? 'text' : null, text: t })))),
          h('tbody', null, ins.trades.map((t) => h('tr', null,
            h('td', { class: 'text', text: fmt.shortDate(t.date) }),
            h('td', { class: 'text', text: t.name }),
            h('td', { class: 'text' }, tradeTag(t.code)),
            h('td', { text: fmt.num(t.shares, 0) }),
            h('td', { text: t.price ? fmt.money(t.price) : '—' })))))) : null)),
    h('p', { class: 'xsmall muted', style: { margin: '12px 0 0' }, text: 'Sold and Bought are open-market trades; the others are routine and not counted. Many executive sales are scheduled in advance (10b5-1 plans). From SEC Form 4 filings via Finnhub.' }));
}
