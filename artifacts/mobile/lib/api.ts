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
 * Returns the raw Response — callers must check `response.ok` themselves.
 * For JSON endpoints that should throw on HTTP errors, use `mobileFetchJson`.
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

/**
 * Authenticated fetch that parses JSON and throws a descriptive Error on any
 * non-2xx HTTP status.  Use this for any hand-rolled call that expects JSON
 * back — it eliminates the common bug of treating HTTP 4xx/5xx as success.
 *
 * The error message includes the HTTP status and, when the server returns a
 * JSON body with a `detail` or `message` field, that text too.
 *
 * @example
 *   const data = await mobileFetchJson<{ items: Item[] }>('/api/items');
 */
export async function mobileFetchJson<T = unknown>(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<T> {
  const response = await mobileFetch(input, init);
  if (!response.ok) {
    let message = `HTTP ${response.status} ${response.statusText}`;
    try {
      const body = await response.json() as Record<string, unknown>;
      const detail = (body?.detail ?? body?.message ?? body?.error) as string | undefined;
      if (detail) message = `${message}: ${detail}`;
    } catch {
      // body was not JSON — keep the status-only message
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
