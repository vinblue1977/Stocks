import { api } from '../api.js';
import { fmt, h, pageHead, toast } from '../ui.js';

function field(label, input) {
  return h('div', { class: 'field' }, h('label', { for: input.id, text: label }), input);
}

export async function renderHoldings(main) {
  const { holdings } = await api('GET', '/holdings');
  holdings.sort((a, b) => a.ticker.localeCompare(b.ticker));

  const ticker = h('input', { id: 'f-ticker', name: 'ticker', required: true, maxlength: 10, autocomplete: 'off', placeholder: 'VTI', pattern: '[A-Za-z][A-Za-z0-9.\\-]{0,9}' });
  const shares = h('input', { id: 'f-shares', name: 'shares', type: 'number', required: true, min: '0.000001', step: 'any', inputmode: 'decimal', placeholder: '10' });
  const cost = h('input', { id: 'f-cost', name: 'cost', type: 'number', required: true, min: '0', step: 'any', inputmode: 'decimal', placeholder: '250.00' });
  const bought = h('input', { id: 'f-date', name: 'date', type: 'date', max: new Date().toISOString().slice(0, 10) });
  const submit = h('button', { type: 'submit', class: 'btn btn-primary', text: 'Save holding' });
  const error = h('p', { class: 'form-error', role: 'alert' });

  const form = h('form', { class: 'form', novalidate: true },
    field('Ticker', ticker), field('Shares', shares), field('Average cost per share ($)', cost), field('Purchase date (optional)', bought), submit);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    if (!form.checkValidity()) { error.textContent = 'Check the ticker, shares and cost.'; return; }
    submit.disabled = true;
    try {
      const body = { shares: Number(shares.value), cost_basis: Number(cost.value) };
      if (bought.value) body.purchased_on = bought.value;
      const symbol = ticker.value.trim().toUpperCase();
      const isNew = !holdings.some((x) => x.ticker === symbol);
      await api('PUT', `/holdings/${encodeURIComponent(symbol)}`, body);
      toast(isNew ? `${symbol} added. Price history loads within a minute.` : `${symbol} updated.`);
      renderHoldings(main);
    } catch (e) {
      error.textContent = e.message;
      submit.disabled = false;
    }
  });

  const rows = holdings.map((x) => {
    let armed = false;
    const remove = h('button', { type: 'button', class: 'btn btn-danger', text: 'Remove', 'aria-label': `Remove ${x.ticker}` });
    remove.addEventListener('click', async () => {
      if (!armed) { armed = true; remove.textContent = 'Confirm remove'; setTimeout(() => { armed = false; remove.textContent = 'Remove'; }, 4000); return; }
      remove.disabled = true;
      try { await api('DELETE', `/holdings/${encodeURIComponent(x.ticker)}`); toast(`${x.ticker} removed.`); renderHoldings(main); } catch (e) { toast(e.message, true); remove.disabled = false; }
    });
    const edit = h('button', {
      type: 'button', class: 'btn btn-ghost', text: 'Edit', 'aria-label': `Edit ${x.ticker}`,
      onClick: () => { ticker.value = x.ticker; shares.value = x.shares; cost.value = x.cost_basis; bought.value = x.purchased_on || ''; shares.focus(); },
    });
    return h('tr', null,
      h('td', { class: 'text' }, h('strong', { text: x.ticker })),
      h('td', { text: fmt.num(x.shares, 6) }),
      h('td', { text: fmt.money(x.cost_basis) }),
      h('td', { text: fmt.money(x.shares * x.cost_basis) }),
      h('td', { class: 'text', text: x.purchased_on ? fmt.date(x.purchased_on) : '—' }),
      h('td', null, h('div', { class: 'actions' }, edit, remove)));
  });

  main.replaceChildren(
    pageHead('Holdings', `${holdings.length} of 40 holdings`),
    h('section', { class: 'card', 'aria-labelledby': 'add-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'add-h', text: 'Add or update a holding' }),
        h('span', { class: 'muted small', text: 'Saving an existing ticker updates it.' })),
      form, error),
    h('section', { class: 'card', 'aria-labelledby': 'list-h' },
      h('div', { class: 'card-head' }, h('h2', { id: 'list-h', text: 'Your holdings' })),
      holdings.length
        ? h('div', { class: 'table-wrap' }, h('table', null,
          h('thead', null, h('tr', null, ['Ticker', 'Shares', 'Avg cost', 'Cost basis', 'Purchased', ''].map((t, i) => h('th', { scope: 'col', class: i === 0 || i === 4 ? 'text' : null, text: t })))),
          h('tbody', null, rows)))
        : h('p', { class: 'muted', text: 'No holdings yet. Add your first one above.' })),
  );
}
