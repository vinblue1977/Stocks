import { api, setApiBase } from './api.js';
import { currentEmail, initAuth, logout } from './auth.js';
import { KINDS, hasPartner, portfolioTitle, setProfile } from './groups.js';
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

// While the US market is open, the Overview re-fetches every 15 minutes (matching the intraday
// price job) when its tab is visible. Other pages have forms, so they only update on navigation.
const REFRESH_MS = 15 * 60 * 1000;
let renderedAt = 0;
let renderSeq = 0;

function marketOpen(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
  }).formatToParts(now).map((p) => [p.type, p.value]));
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  // 9:30 AM to 4:15 PM, so the last quote (4:00 PM run) is picked up too.
  return !['Sat', 'Sun'].includes(parts.weekday) && minutes >= 570 && minutes <= 975;
}

function maybeRefresh() {
  if (document.hidden || currentRoute()[0] !== 'overview') return;
  if (Date.now() - renderedAt >= REFRESH_MS && marketOpen()) route({ quiet: true });
}

/** Returns [nav key, render function]. Portfolio pages are #/<person>/<kind>, e.g. #/partner/auto. */
function currentRoute() {
  const [name, kind] = location.hash.replace(/^#\/?/, '').split('?')[0].split('/');
  if (name === 'mine' || (name === 'partner' && hasPartner())) {
    const k = KINDS[kind] ? kind : 'stocks';
    return [name, (m) => renderPerson(m, name, k)];
  }
  return ROUTES[name] ? [name, ROUTES[name]] : ['overview', renderOverview];
}

/** Renders the current page. `quiet` keeps what's on screen (no "Loading…", no focus jump, errors ignored). */
async function route({ quiet = false } = {}) {
  const legacy = location.hash.match(/^#\/?(stocks|auto)$/); // links from before per-person pages
  if (legacy) { location.replace(`#/mine/${legacy[1]}`); return; }
  const [name, render] = currentRoute();
  document.querySelectorAll('.nav a').forEach((a) => {
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  const seq = ++renderSeq;
  renderedAt = Date.now();
  if (quiet) {
    // Build off-screen and swap in only if no navigation started meanwhile; on error keep what's shown.
    const page = h('div', { class: 'page' });
    try { await render(page); } catch { return; }
    if (seq === renderSeq) main.replaceChildren(page);
    return;
  }
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
    const signedIn = await initAuth(config);
    if (!signedIn) return; // redirecting to the sign-in page
    setProfile(await api('GET', '/profile'));
    document.getElementById('nav-mine').textContent = portfolioTitle('mine');
    const partnerNav = document.getElementById('nav-partner');
    partnerNav.textContent = portfolioTitle('partner');
    partnerNav.hidden = !hasPartner();
  } catch (e) {
    main.replaceChildren(errorCard(e.message), h('p', null, h('a', { href: '/', text: 'Try again' })));
    return;
  }
  document.getElementById('account-email').textContent = currentEmail();
  const signOut = document.getElementById('sign-out');
  signOut.hidden = false;
  signOut.addEventListener('click', logout);
  window.addEventListener('hashchange', () => route());
  setInterval(maybeRefresh, 60 * 1000);
  document.addEventListener('visibilitychange', maybeRefresh); // catch up when the tab comes back
  route();
}

start();
