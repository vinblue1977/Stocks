import { api } from '../api.js';
import { sentimentBars } from '../charts.js';
import { GROUPS, KINDS, hasPartner, personName } from '../groups.js';
import { fill, fmt, h, icon, pageHead, sentimentTag, tone } from '../ui.js';
import { insightsCard, insightsDetail } from './insights.js';

let selected = 'All';
let groupFilter = 'all'; // all, a person (mine / partner) or a kind (stocks / auto)
let watched = null; // a watched ticker opened from the Watchlist (#/news?ticker=X), shown as an extra chip

function safeUrl(url) {
  try { return new URL(url).protocol === 'https:' ? url : null; } catch { return null; }
}

function briefCard(brief, scope) {
  if (!brief) {
    return h('section', { class: 'card' }, h('h2', { text: 'Daily briefing' }),
      h('p', { class: 'muted', text: 'No briefing yet. It is generated each weekday evening after the news is collected.' }));
  }
  return h('section', { class: 'card brief', 'aria-labelledby': 'brief-h' },
    h('div', { class: 'grow' },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } }, icon('lines', '#7CC4FA'),
        h('h2', { id: 'brief-h', text: `Daily briefing: ${scope === 'All' ? 'your portfolio' : scope}` })),
      h('p', { text: brief.summary }),
      h('p', { class: 'xsmall muted', text: `AI summary by Claude on Amazon Bedrock · ${brief.generated_at ? fmt.ago(brief.generated_at) : ''}. Read the sources before acting on anything.` })),
    h('div', { class: 'score-box side' },
      h('div', { class: 'muted small', text: 'News sentiment, last 7 days' }),
      h('div', { style: { display: 'flex', alignItems: 'baseline', gap: '10px', marginTop: '8px' } },
        h('span', { class: `mono ${tone(brief.sentiment_7d)}`, style: { fontSize: '30px' }, text: fmt.score(brief.sentiment_7d) }),
        sentimentTag(brief.label)),
      h('div', { class: 'xsmall muted', style: { marginTop: '6px' }, text: 'Scale −1 (negative) to +1 (positive)' })));
}

function headlinesCard(articles) {
  const list = articles.map((a) => {
    const href = safeUrl(a.url);
    return h('article', { class: 'article' },
      h('div', { class: 'article-meta' }, h('span', { text: a.source }), h('span', { 'aria-hidden': 'true', text: '·' }),
        h('span', { text: fmt.ago(a.published_at) }), sentimentTag(a.sentiment)),
      href ? h('a', { href, target: '_blank', rel: 'noopener noreferrer', text: a.headline }) : h('span', { text: a.headline }),
      h('div', { class: 'chips' }, h('span', { class: 'ticker-chip', text: a.ticker })));
  });
  return h('section', { class: 'card grow', 'aria-labelledby': 'news-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'news-h', text: 'Headlines' }), h('span', { class: 'muted small', text: `${articles.length} in the last 7 days` })),
    list.length ? list : h('p', { class: 'muted', text: 'No headlines yet for this selection.' }));
}

function trendCard(articles) {
  const days = [];
  for (let i = 6; i >= 0; i -= 1) {
    const d = new Date(Date.now() - i * 86400000);
    days.push(d.toISOString().slice(0, 10));
  }
  const byDay = new Map(days.map((d) => [d, []]));
  for (const a of articles) {
    const day = a.published_at.slice(0, 10);
    if (byDay.has(day) && a.score != null) byDay.get(day).push(a.score);
  }
  const values = days.map((d) => { const xs = byDay.get(d); return xs.length ? xs.reduce((x, y) => x + y, 0) / xs.length : null; });
  const labels = days.map((d) => new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short' }));
  return h('section', { class: 'card side', 'aria-labelledby': 'trend-h' },
    h('h2', { id: 'trend-h', text: 'Sentiment trend' }),
    h('p', { class: 'muted small', style: { margin: '6px 0 0' }, text: 'Average headline score per day, last 7 days' }),
    sentimentBars(values, labels, 'Daily average news sentiment over the last 7 days'),
    h('div', { class: 'legend', style: { marginTop: '12px' } },
      h('span', null, h('span', { class: 'dot', style: { background: '#7CC4FA' } }), 'Positive'),
      h('span', null, h('span', { class: 'dot', style: { background: '#F5A35C' } }), 'Negative')));
}

function signalsCard(signals, rules) {
  const ruleText = (ticker) => {
    const hits = rules.filter((r) => r.ticker === ticker && r.triggered);
    if (hits.length) return h('span', { class: 'tag tag-neg', text: `${hits.length} active` });
    return h('span', { class: 'muted', text: rules.some((r) => r.ticker === ticker) ? 'Quiet' : '—' });
  };
  const rows = signals.map((sg) => h('tr', { class: sg.ticker === selected ? 'highlight' : null },
    h('td', { class: 'text' }, h('strong', { text: sg.ticker })),
    h('td', { text: fmt.num(sg.rsi14, 0) }),
    h('td', { class: tone(sg.vs_sma50), text: fmt.signedPct(sg.vs_sma50, 1) }),
    h('td', { class: tone(sg.sentiment_7d), text: fmt.score(sg.sentiment_7d) }),
    h('td', { class: tone(sg.vs_sma200), text: fmt.signedPct(sg.vs_sma200, 1) }),
    h('td', { text: fmt.signedPct(sg.drawdown_from_high, 1) }),
    h('td', { class: 'text' }, ruleText(sg.ticker))));
  // iPhone layout: one block per stock, short- and long-term facts on their own lines (no sideways scrolling).
  const fact = (label, value, cls) => h('span', null, `${label} `, h('b', { class: cls, text: value }));
  const blocks = signals.map((sg) => h('div', { class: `sig-item${sg.ticker === selected ? ' highlight' : ''}` },
    h('strong', { class: 'sig-ticker', text: sg.ticker }),
    ruleText(sg.ticker),
    h('div', { class: 'sig-line' }, h('span', { class: 'sig-horizon', text: 'Short' }),
      fact('RSI', fmt.num(sg.rsi14, 0)), fact('vs 50d', fmt.signedPct(sg.vs_sma50, 1), tone(sg.vs_sma50)),
      fact('News', fmt.score(sg.sentiment_7d), tone(sg.sentiment_7d))),
    h('div', { class: 'sig-line' }, h('span', { class: 'sig-horizon', text: 'Long' }),
      fact('vs 200d', fmt.signedPct(sg.vs_sma200, 1), tone(sg.vs_sma200)), fact('From 1Y high', fmt.signedPct(sg.drawdown_from_high, 1)))));
  return h('section', { class: 'card', 'aria-labelledby': 'sig-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'sig-h', text: 'Signals' }), h('span', { class: 'muted small', text: 'Facts by time horizon. The decision is yours.' })),
    h('div', { class: 'sig-stack' }, blocks.length ? blocks : h('p', { class: 'muted', text: 'Signals appear after the evening pipeline runs.' })),
    h('div', { class: 'table-wrap sig-table' }, h('table', null,
      h('thead', null,
        h('tr', null, h('th'), h('th', { colspan: 3, class: 'text', style: { color: '#7CC4FA' }, text: 'Short term' }),
          h('th', { colspan: 2, class: 'text', style: { color: '#7CC4FA' }, text: 'Long term' }), h('th')),
        h('tr', null, ['Ticker', 'RSI (14)', 'vs 50-day', 'News, 7d', 'vs 200-day', 'From 1Y high', 'Your rules']
          .map((t, i) => h('th', { scope: 'col', class: i === 0 || i === 6 ? 'text' : null, text: t })))),
      h('tbody', null, rows.length ? rows : h('tr', null, h('td', { colspan: 7, class: 'text muted', text: 'Signals appear after the evening pipeline runs.' }))))),
    h('p', { class: 'muted small', style: { marginBottom: 0 }, text: 'RSI above 70 is commonly read as overbought, below 30 as oversold. News score runs from −1 (negative) to +1 (positive).' }));
}

export async function renderNews(main, { keepWatched = false } = {}) {
  const wanted = new URLSearchParams(location.hash.split('?')[1] || '').get('ticker');
  if (wanted) {
    selected = wanted.toUpperCase();
    groupFilter = 'all';
    history.replaceState(null, '', '#/news'); // later chip clicks re-render without re-applying it
  } else if (!keepWatched) {
    watched = null; // a fresh visit shows only your holdings
  }
  const [holdingsRes, signalsRes, rulesRes, insightsRes] = await Promise.all([api('GET', '/holdings'), api('GET', '/signals'), api('GET', '/rules'),
    api('GET', '/insights').catch(() => ({ insights: [] }))]); // optional: the page still works without it
  const insightFor = new Map(insightsRes.insights.map((i) => [i.ticker, i]));
  const filters = hasPartner()
    ? [['all', 'Everyone'], ['mine', personName('mine')], ['partner', personName('partner')]]
    : [['all', 'All groups'], ['stocks', KINDS.stocks.label], ['auto', KINDS.auto.label]];
  if (!filters.some(([key]) => key === groupFilter)) groupFilter = 'all';
  const matches = (x) => groupFilter === 'all' || GROUPS[x.group]?.person === groupFilter || GROUPS[x.group]?.kind === groupFilter;
  const inGroup = holdingsRes.holdings.filter(matches);
  const allTickers = [...new Set(inGroup.map((x) => x.ticker))].sort();
  if (wanted && !allTickers.includes(selected)) watched = selected;
  if (watched && !allTickers.includes(watched)) allTickers.push(watched);
  if (selected !== 'All' && !allTickers.includes(selected)) selected = 'All';
  const news = await api('GET', selected === 'All' ? '/news' : `/news?ticker=${encodeURIComponent(selected)}`);
  const newsOff = new Set(news.news_excluded || []);
  const tickers = allTickers.filter((t) => !newsOff.has(t));
  const visible = new Set(tickers);
  news.articles = news.articles.filter((a) => visible.has(a.ticker));
  signalsRes.signals = signalsRes.signals.filter((sg) => allTickers.includes(sg.ticker));
  const offHere = allTickers.filter((t) => newsOff.has(t));

  const groupChips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filter by group' },
    filters.map(([key, label]) => h('button', {
      type: 'button', class: 'chip-btn', 'aria-pressed': String(key === groupFilter), text: label,
      onClick: () => { groupFilter = key; selected = 'All'; watched = null; renderNews(main, { keepWatched: true }); },
    })));

  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filter by ticker' },
    ['All', ...tickers].map((t) => h('button', {
      type: 'button', class: 'chip-btn', 'aria-pressed': String(t === selected), text: t,
      onClick: () => { selected = t; if (t !== watched) watched = null; renderNews(main, { keepWatched: true }); },
    })));
  fill(main,
    pageHead('News & signals', 'Headlines from Finnhub, scored and summarized each weekday evening', groupChips),
    chips,
    offHere.length ? h('p', { class: 'xsmall muted', style: { margin: '-12px 0 0' }, text: `News is turned off for ${offHere.join(', ')}. Their price signals still appear below.` }) : null,
    briefCard(news.brief, selected === 'All' ? 'All' : selected),
    h('div', { class: 'row' }, headlinesCard(news.articles), trendCard(news.articles)),
    signalsCard(signalsRes.signals, rulesRes.rules),
    selected === 'All'
      ? insightsCard(allTickers.filter((t) => t !== watched && insightFor.has(t)).map((t) => insightFor.get(t)),
        { id: 'ins-h', subtitle: 'Your stocks · refreshed each evening', footnote: ' Funds have no analyst ratings or insiders.' })
      : insightsDetail(insightFor.get(selected)),
    h('div', { class: 'footer-note' }, h('span', { text: 'Signals describe data; they are not buy or sell recommendations.' })),
  );
}
