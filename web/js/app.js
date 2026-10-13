import { api, setApiBase } from './api.js';
import { addPasskeyUrl, currentEmail, hasSession, initAuth, logout } from './auth.js';
import { KINDS, hasPartner, portfolioTitle, setProfile } from './groups.js';
import { errorCard, h } from './ui.js';
import { renderAlerts } from './views/alerts.js';
import { renderPerson } from './views/holdings.js';
import { renderNews } from './views/news.js';
import { renderOverview } from './views/overview.js';
import { renderWatchlist } from './views/watchlist.js';

const ROUTES = {
  overview: renderOverview,
  news: renderNews,
  watchlist: renderWatchlist,
  alerts: renderAlerts,
};
const main = document.getElementById('main');

// The Overview re-fetches shortly after each 15-minute intraday price job (:02, :17, :32, :47) while
// the US market is open, or while it still shows live prices (until the evening close is stored),
// and only when its tab is visible. Other pages have forms, so they only update on navigation.
const QUARTER_MS = 15 * 60 * 1000;
const JOB_DELAY_MS = 2 * 60 * 1000;
let renderedAt = 0;
let renderSeq = 0;

function marketOpen(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
  }).formatToParts(now).map((p) => [p.type, p.value]));
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  return !['Sat', 'Sun'].includes(parts.weekday) && minutes >= 570 && minutes <= 960; // 9:30 AM-4:00 PM
}

function maybeRefresh() {
  if (document.hidden || currentRoute()[0] !== 'overview') return;
  if (!marketOpen() && !main.querySelector('[data-live]')) return;
  const now = Date.now();
  const lastJob = Math.floor((now - JOB_DELAY_MS) / QUARTER_MS) * QUARTER_MS + JOB_DELAY_MS;
  if (renderedAt < lastJob) route({ quiet: true });
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

/**
 * Renders the current page off-screen and swaps it in only if no newer render started meanwhile.
 * `quiet` (background refresh) keeps what's on screen: no "Loading…", no focus jump, errors ignored.
 */
async function route({ quiet = false } = {}) {
  const legacy = location.hash.match(/^#\/?(stocks|auto)$/); // links from before per-person pages
  if (legacy) { location.replace(`#/mine/${legacy[1]}`); return; }
  const [name, render] = currentRoute();
  const seq = ++renderSeq;
  if (!quiet) {
    document.querySelectorAll('.nav a').forEach((a) => {
      if (a.dataset.route === name) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    main.replaceChildren(h('p', { class: 'loading', text: 'Loading…' }));
  }
  const page = h('div', { class: 'page' });
  try {
    await render(page);
  } catch (e) {
    if (quiet) return; // retried on the next check
    page.replaceChildren(errorCard(`Something went wrong: ${e.message}`));
  }
  if (seq !== renderSeq) return; // a newer navigation or refresh owns the page now
  renderedAt = Date.now();
  // A refresh rebuilds the same controls in the same order, so keep focus on the same one.
  const focusables = (root) => [...root.querySelectorAll('a[href], button, input, select, textarea')];
  const focusedIndex = quiet ? focusables(main).indexOf(document.activeElement) : -1;
  main.replaceChildren(page);
  if (focusedIndex >= 0) focusables(main)[focusedIndex]?.focus({ preventScroll: true });
  if (!quiet) main.focus({ preventScroll: true });
}

/** Shows the page once sign-in is confirmed (or an error needs showing). */
function reveal({ header = true } = {}) {
  document.querySelector('.topbar').hidden = !header;
  document.documentElement.classList.remove('auth-pending');
}

// Back after signing out can restore this page from the browser cache; reload so it signs in again.
window.addEventListener('pageshow', (event) => {
  if (event.persisted && !hasSession()) {
    document.documentElement.classList.add('auth-pending');
    location.reload();
  }
});

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
    reveal({ header: false }); // the error alone, without the app's menu
    main.replaceChildren(errorCard(e.message), h('p', null, h('a', { href: '/', text: 'Try again' })));
    return;
  }
  document.getElementById('account-email').textContent = currentEmail();
  const signOut = document.getElementById('sign-out');
  signOut.hidden = false;
  signOut.addEventListener('click', logout);
  const addPasskey = document.getElementById('add-passkey');
  addPasskey.href = addPasskeyUrl();
  addPasskey.hidden = false;
  window.addEventListener('hashchange', () => route());
  setInterval(maybeRefresh, 60 * 1000);
  document.addEventListener('visibilitychange', maybeRefresh); // catch up when the tab comes back
  reveal(); // after /profile, so a stale session redirects to sign-in before anything shows
  route();
}

start();
