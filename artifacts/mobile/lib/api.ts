/**
 * Thin authenticated fetch wrapper for the Orivellum mobile app.
 *
 * The generated react-query hooks already use the bearer token via
 * `setAuthTokenGetter` (wired by `loadToken()` in `_layout.tsx`).
 * For the remaining hand-rolled `fetch` calls — chat messages, learning
 * endpoints, library knowledge — use `mobileFetch` so the bearer token
 * is attached automatically.
 */
import { getApiToken } from './token';

/**
 * Fetch with the local API bearer token attached.
 * Drop-in replacement for `fetch` in hand-rolled API calls.
 */
export async function mobileFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const token = getApiToken();
  const headers = new Headers(init?.headers);
  if (token && !headers.has('authorization')) {
    headers.set('authorization', `Bearer ${token}`);
  }
  return fetch(input, { ...init, headers });
}
