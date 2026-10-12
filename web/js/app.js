import { setApiBase } from './api.js';
import { currentEmail, initAuth, logout } from './auth.js';
import { KINDS, hasPartner, portfolioTitle, setNames } from './groups.js';
import { errorCard, h } from './ui.js';
import { renderAlerts } from './views/alerts.js';
import { renderPerson } from './views/holdings.js';
import { renderNews } from './views/news.js';
import { renderOverview } from './views/overview.js';

const ROUTES = {
  overview: renderOverview,
  news: renderNews,
  alerts: renderAlerts,
};
const main = document.getElementById('main');

/** Returns [nav key, render function]. Portfolio pages are #/<person>/<kind>, e.g. #/partner/auto. */
function currentRoute() {
  const [name, kind] = location.hash.replace(/^#\/?/, '').split('?')[0].split('/');
  if (name === 'mine' || (name === 'partner' && hasPartner())) {
    const k = KINDS[kind] ? kind : 'stocks';
    return [name, (m) => renderPerson(m, name, k)];
  }
  return ROUTES[name] ? [name, ROUTES[name]] : ['overview', renderOverview];
}

async function route() {
  const legacy = location.hash.match(/^#\/?(stocks|auto)$/); // links from before per-person pages
  if (legacy) { location.replace(`#/mine/${legacy[1]}`); return; }
  const [name, render] = currentRoute();
  document.querySelectorAll('.nav a').forEach((a) => {
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  main.replaceChildren(h('p', { class: 'loading', text: 'Loading…' }));
  try {
    await render(main);
  } catch (e) {
    main.replaceChildren(errorCard(`Something went wrong: ${e.message}`));
  }
  main.focus({ preventScroll: true });
}

async function start() {
  try {
    const config = await (await fetch('config.json', { cache: 'no-store' })).json();
    setApiBase(config.apiUrl);
    setNames(config);
    document.getElementById('nav-mine').textContent = portfolioTitle('mine');
    const partnerNav = document.getElementById('nav-partner');
    partnerNav.textContent = portfolioTitle('partner');
    partnerNav.hidden = !hasPartner();
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
