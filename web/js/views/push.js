// "Phone notifications" card on the Alerts page: turn Web Push on or off for this device, and send a test.
// iPhones support it only in the installed (Home Screen) app, iOS 16.4+.
import { api } from '../api.js';
import { getConfig } from '../config.js';
import { add, h, toast } from '../ui.js';

const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
const installed = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);

function keyBytes(base64url) {
  const base64 = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
}

async function currentSubscription() {
  const registration = await navigator.serviceWorker.getRegistration();
  return registration ? registration.pushManager.getSubscription() : null;
}

export async function pushCard(rerender) {
  const card = h('section', { class: 'card', 'aria-labelledby': 'push-h' },
    h('div', { class: 'card-head' }, h('h2', { id: 'push-h', text: 'Phone notifications' }),
      h('span', { class: 'muted small', text: 'Each phone turns this on for itself' })));
  const state = (on, title, text, ...actions) => add(card, h('div', { class: 'push' },
    h('div', { class: 'push-state' }, h('span', { class: `dot-state${on ? ' on' : ''}`, 'aria-hidden': 'true' }),
      h('div', null, h('strong', { text: title }), h('p', { class: 'small muted', style: { margin: '4px 0 0' }, text }))),
    actions.length ? h('div', { class: 'push-actions' }, actions) : null));

  const vapidKey = getConfig().vapidPublicKey;
  if (!supported() || !vapidKey) {
    return state(false, isIOS() && !installed() ? 'Add the app to your Home Screen first' : 'This browser can\'t receive notifications',
      isIOS() && !installed()
        ? 'iPhone notifications work only in the installed app: in Safari tap Share, then Add to Home Screen, and open it from there.'
        : 'Use the installed app on iPhone (iOS 16.4 or later), or a current desktop browser.');
  }
  if (Notification.permission === 'denied') {
    return state(false, 'Notifications are blocked for this app', 'Turn them on in your device settings (iPhone: Settings › Notifications › Portfolio), then come back here.');
  }

  const subscription = await currentSubscription().catch(() => null);
  const { devices } = await api('GET', '/push').catch(() => ({ devices: 0 }));
  const busy = (button, task) => async () => {
    button.disabled = true;
    try { await task(); } catch (e) { toast(e.message, true); button.disabled = false; }
  };

  if (!subscription) {
    const on = h('button', { type: 'button', class: 'btn btn-primary', text: 'Turn on notifications' });
    on.addEventListener('click', busy(on, async () => {
      // Must run straight from the tap: iOS only shows the permission prompt for a user action.
      const registration = await navigator.serviceWorker.register('sw.js');
      await navigator.serviceWorker.ready;
      const sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(vapidKey) });
      await api('PUT', '/push', sub.toJSON());
      toast('Notifications are on for this device.');
      rerender();
    }));
    return state(false, 'Phone notifications are off on this device',
      `Get rule alerts as notifications here, as well as by email.${devices ? ` ${devices} other device${devices === 1 ? ' has' : 's have'} them on.` : ''}`, on);
  }

  const test = h('button', { type: 'button', class: 'btn btn-ghost', text: 'Send a test' });
  test.addEventListener('click', busy(test, async () => {
    await api('POST', '/push/test', { endpoint: subscription.endpoint });
    toast('Test sent. It should arrive in a few seconds.');
    test.disabled = false;
  }));
  const off = h('button', { type: 'button', class: 'btn btn-danger', text: 'Turn off' });
  off.addEventListener('click', busy(off, async () => {
    await api('DELETE', '/push', { endpoint: subscription.endpoint });
    await subscription.unsubscribe();
    toast('Notifications are off for this device.');
    rerender();
  }));
  return state(true, 'Notifications are on for this device',
    `Rule alerts arrive here and by email. ${devices} device${devices === 1 ? ' has' : 's have'} notifications on.`, test, off);
}
