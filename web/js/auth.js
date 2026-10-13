// Sign-in with the Cognito hosted login page using OAuth 2.0 authorization code + PKCE.
// The password is entered only on Cognito's page; this app never sees it.
// Tokens live in sessionStorage (cleared when the tab closes); the strict CSP blocks injected scripts.

const STORE = 'pt.tokens';
const PKCE = 'pt.pkce';
let cfg;

function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomString(len = 32) {
  return b64url(crypto.getRandomValues(new Uint8Array(len)));
}

function redirectUri() {
  return `${location.origin}/`;
}

function authUrl(path, params) {
  const url = new URL(`${cfg.authDomain}${path}`);
  url.search = new URLSearchParams({ client_id: cfg.clientId, ...params }).toString();
  return url.toString();
}

function save(tokens) {
  const expiresAt = Date.now() + (tokens.expires_in || 3600) * 1000;
  const prev = load() || {};
  sessionStorage.setItem(STORE, JSON.stringify({
    access_token: tokens.access_token,
    id_token: tokens.id_token,
    refresh_token: tokens.refresh_token || prev.refresh_token,
    expires_at: expiresAt,
  }));
}

function load() {
  try { return JSON.parse(sessionStorage.getItem(STORE)); } catch { return null; }
}

async function tokenRequest(params) {
  const res = await fetch(`${cfg.authDomain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: cfg.clientId, ...params }),
  });
  if (!res.ok) throw new Error('Sign-in failed');
  return res.json();
}

export async function login() {
  const verifier = randomString(48);
  const state = randomString(16);
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  sessionStorage.setItem(PKCE, JSON.stringify({ verifier, state }));
  location.assign(authUrl('/oauth2/authorize', {
    response_type: 'code',
    redirect_uri: redirectUri(),
    scope: 'openid email',
    code_challenge_method: 'S256',
    code_challenge: challenge,
    state,
  }));
}

// Cognito's page for registering a passkey on this device; Cognito asks to sign in again if its session expired.
export function addPasskeyUrl() {
  return authUrl('/passkeys/add', { redirect_uri: redirectUri() });
}

export function logout() {
  sessionStorage.removeItem(STORE);
  location.assign(authUrl('/logout', { logout_uri: redirectUri() }));
}

// Returns true when signed in; otherwise starts the redirect to the login page.
export async function initAuth(config) {
  cfg = config;
  const params = new URLSearchParams(location.search);
  if (params.has('code')) {
    const pkce = JSON.parse(sessionStorage.getItem(PKCE) || '{}');
    sessionStorage.removeItem(PKCE);
    history.replaceState(null, '', `${location.pathname}${location.hash || '#/overview'}`);
    if (!pkce.state || pkce.state !== params.get('state')) {
      throw new Error('Sign-in could not be verified. Please try again.');
    }
    save(await tokenRequest({
      grant_type: 'authorization_code',
      code: params.get('code'),
      redirect_uri: redirectUri(),
      code_verifier: pkce.verifier,
    }));
    return true;
  }
  if (params.has('error')) {
    history.replaceState(null, '', location.pathname);
    if (load()) return true; // e.g. a cancelled passkey registration: still signed in
    throw new Error(params.get('error_description') || 'Sign-in was cancelled.');
  }
  if (load()) return true;
  await login();
  return false;
}

export async function getAccessToken(forceRefresh = false) {
  const tokens = load();
  if (!tokens) { await login(); return null; }
  if (forceRefresh || Date.now() > tokens.expires_at - 60_000) {
    if (!tokens.refresh_token) { await login(); return null; }
    try {
      save(await tokenRequest({ grant_type: 'refresh_token', refresh_token: tokens.refresh_token }));
    } catch {
      sessionStorage.removeItem(STORE);
      await login();
      return null;
    }
  }
  return load().access_token;
}

export function currentEmail() {
  const tokens = load();
  if (!tokens?.id_token) return '';
  try {
    const payload = tokens.id_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(payload)).email || '';
  } catch { return ''; }
}
