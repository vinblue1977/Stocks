import { api } from '../api.js';
import { add, fill, h, icon, pageHead, toast } from '../ui.js';
import { pushCard } from './push.js';

const PARAM_LABELS = { window: 'Days in average', pct: 'Percent', threshold: 'Threshold', price: 'Price ($)' };

function describe(rule, types) {
  const spec = types[rule.type];
  const params = Object.entries(rule.params || {}).map(([k, v]) => `${PARAM_LABELS[k] || k}: ${v}`).join(', ');
  return `${spec ? spec.description : rule.type}${params ? ` (${params})` : ''}`;
}

export async function renderAlerts(main) {
  const [typesRes, rulesRes, holdingsRes, alertsRes, watchRes, push] = await Promise.all([
    api('GET', '/rule-types'), api('GET', '/rules'), api('GET', '/holdings'), api('GET', '/alerts'), api('GET', '/watchlist'),
    pushCard(() => renderAlerts(main)).catch(() => null), // the notifications card never blocks the page
  ]);
  const types = typesRes.rule_types;
  const tickers = [...new Set([...holdingsRes.holdings, ...watchRes.watchlist].map((x) => x.ticker))].sort();

  // ---------- new rule form
  const typeSelect = h('select', { id: 'r-type' }, Object.entries(types).map(([name, spec]) => h('option', { value: name, text: spec.description })));
  const tickerSelect = h('select', { id: 'r-ticker' }, tickers.map((t) => h('option', { value: t, text: t })));
  const paramsHost = h('div', { style: { display: 'contents' } });
  const submit = h('button', { type: 'submit', class: 'btn btn-primary', text: 'Create rule', disabled: !tickers.length });
  const error = h('p', { class: 'form-error', role: 'alert', text: tickers.length ? '' : 'Add a holding or a watched ticker first.' });

  function drawParams() {
    fill(paramsHost);
    for (const [name, range] of Object.entries(types[typeSelect.value].params)) {
      const input = h('input', { id: `r-${name}`, name, type: 'number', required: true, min: range.min, max: range.max, step: 'any', inputmode: 'decimal' });
      add(paramsHost, h('div', { class: 'field' },
        h('label', { for: input.id, text: `${PARAM_LABELS[name] || name} (${range.min} to ${range.max})` }), input));
    }
  }
  typeSelect.addEventListener('change', drawParams);
  drawParams();

  const form = h('form', { class: 'form', novalidate: true },
    h('div', { class: 'field field-wide' }, h('label', { for: 'r-type', text: 'Condition' }), typeSelect),
    h('div', { class: 'field' }, h('label', { for: 'r-ticker', text: 'Ticker' }), tickerSelect),
    paramsHost, submit);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    if (!form.checkValidity()) { error.textContent = 'Enter a value within the allowed range.'; return; }
    const params = {};
    for (const input of paramsHost.querySelectorAll('input')) params[input.name] = Number(input.value);
    submit.disabled = true;
    try {
      await api('POST', '/rules', { type: typeSelect.value, ticker: tickerSelect.value, params });
      toast('Rule created. It is checked each weekday after market close.');
      renderAlerts(main);
    } catch (e) {
      error.textContent = e.message;
      submit.disabled = false;
    }
  });

  // ---------- existing rules
  const ruleItems = rulesRes.rules.map((rule) => {
    const remove = h('button', { type: 'button', class: 'btn btn-danger', text: 'Delete', 'aria-label': `Delete rule for ${rule.ticker}` });
    let armed = false;
    remove.addEventListener('click', async () => {
      if (!armed) { armed = true; remove.textContent = 'Confirm delete'; setTimeout(() => { armed = false; remove.textContent = 'Delete'; }, 4000); return; }
      remove.disabled = true;
      try { await api('DELETE', `/rules/${encodeURIComponent(rule.rule_id)}`); toast('Rule deleted.'); renderAlerts(main); } catch (e) { toast(e.message, true); remove.disabled = false; }
    });
    return h('div', { class: `alert-item ${rule.triggered ? '' : 'quiet'}`, style: { alignItems: 'center' } },
      icon(rule.triggered ? 'warn' : 'check', rule.triggered ? '#F5A35C' : '#9AA8BF'),
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div', { class: 'alert-title', text: `${rule.ticker}: ${describe(rule, types)}` }),
        h('div', { class: 'muted small', text: rule.triggered ? rule.detail : (rule.detail ? `Quiet. ${rule.detail}` : 'Not checked yet') })),
      remove);
  });

  const history = alertsRes.alerts.map((a) => h('tr', null,
    h('td', { class: 'text wrap', text: new Date(a.created_at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }) }),
    h('td', { class: 'text wrap', text: a.message })));

  fill(main,
    pageHead('Alerts', 'Email (and phone notifications, if on) when a rule changes from not met to met'),
    push,
    h('section', { class: 'card', 'aria-labelledby': 'new-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'new-h', text: 'New rule' })), form, error),
    h('section', { class: 'card stack', 'aria-labelledby': 'rules-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'rules-h', text: 'Your rules' }), h('span', { class: 'muted small', text: `${rulesRes.rules.length} of 50` })),
      ruleItems.length ? ruleItems : h('p', { class: 'muted', text: 'No rules yet.' })),
    h('section', { class: 'card', 'aria-labelledby': 'hist-h' },
      h('h2', { id: 'hist-h', text: 'Alert history' }),
      history.length
        ? h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, h('th', { scope: 'col', class: 'text', text: 'When' }), h('th', { scope: 'col', class: 'text', text: 'What happened' }))),
          h('tbody', null, history)))
        : h('p', { class: 'muted', text: 'No alerts have fired yet.' })),
    h('div', { class: 'footer-note' }, h('span', { text: 'Alerts describe data; they are not investment advice.' })),
  );
}
