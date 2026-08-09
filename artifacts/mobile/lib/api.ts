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
/**
 * Authenticated fetch with a READABLE STREAMING BODY.
 *
 * React Native's built-in `fetch` does not expose `response.body` as a
 * ReadableStream on device, so any SSE / chunked-streaming consumer that
 * calls `response.body.getReader()` silently breaks on iOS/Android (body is
 * null → "unexpected response type" style errors).  Expo ships a
 * WinterCG-compliant fetch (`expo/fetch`) whose responses DO support
 * streaming on native.  Use this wrapper for every streaming endpoint.
 *
 * Note: `expo/fetch` accepts a URL string (not a Request object).
 */
export async function mobileStreamFetch(
  url: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    signal?: AbortSignal;
  },
): Promise<Response> {
  const { fetch: expoFetch } = await import('expo/fetch');
  const token = getApiToken();
  const headers: Record<string, string> = { ...(init?.headers ?? {}) };
  if (token && !Object.keys(headers).some(k => k.toLowerCase() === 'authorization')) {
    headers['authorization'] = `Bearer ${token}`;
  }
  return expoFetch(url, { ...init, headers }) as unknown as Response;
}

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
