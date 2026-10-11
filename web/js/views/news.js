import { api } from '../api.js';
import { sentimentBars } from '../charts.js';
import { fmt, h, icon, pageHead, sentimentTag, tone } from '../ui.js';

let selected = 'All';

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
  return h('section', { class: 'card', 'aria-labelledby': 'sig-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'sig-h', text: 'Signals' }), h('span', { class: 'muted small', text: 'Facts by time horizon. The decision is yours.' })),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null,
        h('tr', null, h('th'), h('th', { colspan: 3, class: 'text', style: { color: '#7CC4FA' }, text: 'Short term' }),
          h('th', { colspan: 2, class: 'text', style: { color: '#7CC4FA' }, text: 'Long term' }), h('th')),
        h('tr', null, ['Ticker', 'RSI (14)', 'vs 50-day', 'News, 7d', 'vs 200-day', 'From 1Y high', 'Your rules']
          .map((t, i) => h('th', { scope: 'col', class: i === 0 || i === 6 ? 'text' : null, text: t })))),
      h('tbody', null, rows.length ? rows : h('tr', null, h('td', { colspan: 7, class: 'text muted', text: 'Signals appear after the evening pipeline runs.' })))),
    h('p', { class: 'muted small', style: { marginBottom: 0 }, text: 'RSI above 70 is commonly read as overbought, below 30 as oversold. News score runs from −1 (negative) to +1 (positive).' })));
}

export async function renderNews(main) {
  const [holdingsRes, signalsRes, rulesRes] = await Promise.all([api('GET', '/holdings'), api('GET', '/signals'), api('GET', '/rules')]);
  const tickers = holdingsRes.holdings.map((x) => x.ticker).sort();
  if (selected !== 'All' && !tickers.includes(selected)) selected = 'All';
  const news = await api('GET', selected === 'All' ? '/news' : `/news?ticker=${encodeURIComponent(selected)}`);

  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filter by ticker' },
    ['All', ...tickers].map((t) => h('button', {
      type: 'button', class: 'chip-btn', 'aria-pressed': String(t === selected), text: t,
      onClick: () => { selected = t; renderNews(main); },
    })));
  main.replaceChildren(
    pageHead('News & signals', 'Headlines from Finnhub, scored and summarized each weekday evening', chips),
    briefCard(news.brief, selected),
    h('div', { class: 'row' }, headlinesCard(news.articles), trendCard(news.articles)),
    signalsCard(signalsRes.signals, rulesRes.rules),
    h('div', { class: 'footer-note' }, h('span', { text: 'Signals describe data; they are not buy or sell recommendations.' })),
  );
}
