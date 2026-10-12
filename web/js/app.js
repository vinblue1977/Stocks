import { setApiBase } from './api.js';
import { currentEmail, initAuth, logout } from './auth.js';
import { errorCard, h } from './ui.js';
import { renderAlerts } from './views/alerts.js';
import { renderGroup } from './views/holdings.js';
import { renderNews } from './views/news.js';
import { renderOverview } from './views/overview.js';

const ROUTES = {
  overview: renderOverview,
  stocks: (main) => renderGroup(main, 'stocks'),
  auto: (main) => renderGroup(main, 'auto'),
  news: renderNews,
  alerts: renderAlerts,
};
const main = document.getElementById('main');

function currentRoute() {
  const name = location.hash.replace(/^#\/?/, '').split('?')[0];
  return ROUTES[name] ? name : 'overview';
}

async function route() {
  const name = currentRoute();
  document.querySelectorAll('.nav a').forEach((a) => {
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  main.replaceChildren(h('p', { class: 'loading', text: 'Loading…' }));
  try {
    await ROUTES[name](main);
  } catch (e) {
    main.replaceChildren(errorCard(`Something went wrong: ${e.message}`));
  }
  main.focus({ preventScroll: true });
}

async function start() {
  try {
    const config = await (await fetch('config.json', { cache: 'no-store' })).json();
    setApiBase(config.apiUrl);
    const signedIn = await initAuth(config);
    if (!signedIn) return; // redirecting to the sign-in page
  } catch (e) {
    main.replaceChildren(errorCard(e.message), h('p', null, h('a', { href: '/', text: 'Try again' })));
    return;
  }
  document.getElementById('account-email').textContent = currentEmail();
  const signOut = document.getElementById('sign-out');
  signOut.hidden = false;
  signOut.addEventListener('click', logout);
  window.addEventListener('hashchange', route);
  route();
}

start();
