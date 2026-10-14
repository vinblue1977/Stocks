import { api } from '../api.js';
import { EARNINGS_HOUR, fill, fmt, h, pageHead, sentimentTag, toast, tone } from '../ui.js';

let confirming = null; // ticker whose Remove is waiting for confirmation

function rulesCell(rules) {
  const active = rules.filter((r) => r.triggered).length;
  if (active) return h('span', { class: 'tag tag-neg', text: `${active} active` });
  return h('span', { class: 'muted', text: rules.length ? 'Quiet' : '—' });
}

function row(item, rules, main) {
  const remove = h('button', {
    type: 'button', class: 'btn btn-danger', text: 'Remove', 'aria-label': `Remove ${item.ticker} from the watchlist`,
    onClick: () => { confirming = item.ticker; renderWatchlist(main); },
  });
  const name = h('td', { class: 'text' }, h('strong', { text: item.ticker }),
    item.held ? h('span', { class: 'tag tag-neutral held-tag', text: 'Held' }) : null);
  const sg = item.signal;
  if (!sg) {
    // The backfill takes about a minute; after a while with no prices, the ticker is probably mistyped.
    const stuck = Date.now() - new Date(item.added_at).getTime() > 15 * 60 * 1000;
    return h('tr', { class: 'pending' }, name,
      h('td', { colspan: 9, class: 'text', text: stuck
        ? `No prices found for ${item.ticker}. Check the ticker, then remove it.`
        : 'Loading price history, usually under a minute. News arrives after the evening run.' }),
      h('td', null, remove));
  }
  // During market hours the live price replaces the last close, and the day change is measured against that close.
  const price = item.live ? item.live.last : sg.price;
  const day = item.live ? item.live.last / sg.price - 1 : sg.change_1d;
  const news = item.brief?.sentiment_7d;
  return h('tr', null, name,
    h('td', null, fmt.money(price), item.live ? h('span', { class: 'live', text: 'live' }) : null),
    h('td', { class: tone(day), text: fmt.signedPct(day) }),
    h('td', { class: tone(sg.return_1y), text: fmt.signedPct(sg.return_1y, 1) }),
    h('td', { text: fmt.signedPct(sg.drawdown_from_high, 1) }),
    h('td', { text: fmt.num(sg.rsi14, 0) }),
    h('td', { class: tone(sg.vs_sma200), text: fmt.signedPct(sg.vs_sma200, 1) }),
    h('td', { class: tone(news), text: fmt.score(news) }),
    h('td', { class: 'text' }, item.earnings
      ? [fmt.shortDate(item.earnings.date), h('div', { class: 'xsmall muted', text: (EARNINGS_HOUR[item.earnings.hour] || '').toLowerCase() })]
      : h('span', { class: 'muted', text: '—' })),
    h('td', { class: 'text' }, rulesCell(rules)),
    h('td', null, remove));
}

function confirmRow(item, rules, main) {
  const what = item.held || !rules.length
    ? `Stop watching ${item.ticker}?`
    : `Stop watching ${item.ticker}? Its ${rules.length} alert rule${rules.length > 1 ? 's' : ''} will be deleted too.`;
  const remove = h('button', { type: 'button', class: 'btn btn-danger', text: 'Remove' });
  remove.addEventListener('click', async () => {
    remove.disabled = true;
    try {
      await api('DELETE', `/watchlist/${encodeURIComponent(item.ticker)}`);
      confirming = null;
      toast(`${item.ticker} removed from the watchlist.`);
      renderWatchlist(main);
    } catch (e) { toast(e.message, true); remove.disabled = false; }
  });
  return h('tr', { class: 'confirm-row' }, h('td', { colspan: 11 },
    h('strong', { text: what }),
    item.held ? h('span', { class: 'muted', text: ' You still hold it, so its prices, news and rules stay.' }) : null,
    h('div', { class: 'actions' }, remove,
      h('button', { type: 'button', class: 'btn btn-ghost', text: 'Cancel', onClick: () => { confirming = null; renderWatchlist(main); } }))));
}

function newsItem(item) {
  const b = item.brief;
  let text = 'News arrives after the evening run.';
  if (item.news_off) text = 'News is turned off for this ticker.';
  else if (b?.summary) text = b.summary;
  else if (b) text = 'No headlines this week.';
  return h('div', { class: 'news-item' },
    h('div', null, h('span', { class: 'ticker-chip', text: item.ticker })),
    h('div', null,
      h('p', { class: b?.summary ? null : 'muted', text }),
      b?.summary ? h('div', { class: 'news-meta' },
        h('span', { text: `${b.headline_count} headline${b.headline_count === 1 ? '' : 's'}${b.generated_at ? ` · ${fmt.ago(b.generated_at)}` : ''}` }),
        sentimentTag(b.label),
        h('a', { href: `#/news?ticker=${encodeURIComponent(item.ticker)}`, text: 'Headlines' })) : null));
}

export async function renderWatchlist(main) {
  const [res, rulesRes] = await Promise.all([api('GET', '/watchlist'), api('GET', '/rules')]);
  const items = res.watchlist;
  const rulesFor = (t) => rulesRes.rules.filter((r) => r.ticker === t);
  if (!items.some((i) => i.ticker === confirming)) confirming = null;

  const input = h('input', { id: 'w-ticker', placeholder: 'e.g. MSFT', autocomplete: 'off', maxlength: 10, 'aria-describedby': 'w-err' });
  const error = h('p', { class: 'form-error', id: 'w-err', role: 'alert', hidden: true }); // no empty row until needed
  const showError = (message) => { error.textContent = message; error.hidden = false; };
  const submit = h('button', { type: 'submit', class: 'btn btn-primary', text: 'Watch' });
  const form = h('form', { class: 'add-form', novalidate: true },
    h('div', { class: 'field' }, h('label', { for: 'w-ticker', text: 'Add a ticker' }), input), submit);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const ticker = input.value.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(ticker)) { showError('Enter a ticker like MSFT or BRK.B.'); return; }
    if (items.some((i) => i.ticker === ticker)) { showError(`${ticker} is already on your watchlist.`); return; }
    submit.disabled = true;
    try {
      await api('PUT', `/watchlist/${encodeURIComponent(ticker)}`);
      toast(`${ticker} added. Prices load in about a minute.`);
      renderWatchlist(main);
    } catch (e) { showError(e.message); submit.disabled = false; }
  });

  const rows = items.flatMap((item) => {
    const rules = rulesFor(item.ticker);
    return [row(item, rules, main), confirming === item.ticker ? confirmRow(item, rules, main) : null];
  });
  fill(main,
    pageHead('Watchlist', "Tickers you don't own. Same facts as your holdings; the decision is yours.", form),
    error,
    h('section', { class: 'card', 'aria-labelledby': 'wl-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'wl-h', text: 'Watching' }),
        h('span', { class: 'muted small', text: `${items.length} of ${res.max} · prices live during market hours` })),
      h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ['Ticker', 'Price', '1 day', '1 year', 'From 1Y high', 'RSI (14)', 'vs 200-day', 'News, 7d', 'Next earnings', 'Your rules', '']
          .map((t, i) => h('th', { scope: 'col', class: i === 0 || i === 8 || i === 9 ? 'text' : null, text: t })))),
        h('tbody', null, rows.length ? rows
          : h('tr', null, h('td', { colspan: 11, class: 'text muted', text: 'Nothing on your watchlist yet. Add a ticker above.' }))))),
      h('p', { class: 'muted small', style: { marginBottom: 0 }, text: 'Watched tickers are not part of your holdings or totals. Set alerts for them on the Alerts page.' })),
    items.length ? h('section', { class: 'card', 'aria-labelledby': 'wn-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'wn-h', text: 'News this week' }),
        h('span', { class: 'muted small', text: 'AI summary by Claude on Amazon Bedrock, each weekday evening' })),
      items.map(newsItem)) : null,
    h('div', { class: 'footer-note' }, h('span', { text: 'Signals describe data; they are not buy or sell recommendations.' })),
  );
}
