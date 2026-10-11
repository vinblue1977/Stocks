import { getAccessToken, login } from './auth.js';

let baseUrl = '';

export function setApiBase(url) {
  baseUrl = url.replace(/\/$/, '');
}

export async function api(method, path, body) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const token = await getAccessToken(attempt > 0);
    if (!token) return new Promise(() => {}); // redirecting to sign-in
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401 && attempt === 0) continue; // token expired: refresh once and retry
    if (res.status === 401) { await login(); return new Promise(() => {}); }
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || `Request failed (${res.status})`);
    return data;
  }
  return null;
}
