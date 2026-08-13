/**
 * Auth helpers for the Orivellum web client.
 *
 * Authentication is handled via an HttpOnly session cookie set by the API
 * when the user logs in through POST /api/auth/login.  The browser sends
 * the cookie automatically on every same-origin request.
 *
 * Once-and-done: on successful login the API key is ALSO stored in
 * localStorage.  Every request then carries `Authorization: Bearer <key>`
 * as a fallback, so the app keeps working even when the session cookie is
 * gone (backend restart before the secret persisted, cookie eviction,
 * installed-PWA cookie partitioning).  If a request still gets a 401, we
 * silently re-establish the session with the stored key and retry — the
 * login form only appears when the stored key itself is rejected.
 *
 * This is a deliberate single-user, private-network trade-off: the key in
 * localStorage is equivalent in power to the session cookie it backs up.
 */
import { setAuthTokenGetter, setMutationTracker } from "@workspace/api-client-react";
import { acquireBusy } from "./app-busy";

// Every mutating request issued through the generated react-query client
// (orval hooks → customFetch) also holds the same app-busy reason — the PWA
// update prompt must never reload mid-write regardless of which API path
// the mutation used.
setMutationTracker(() => acquireBusy("api-write"));

const STORAGE_KEY = "orivellum.apiKey";

// ── Stored-key helpers ────────────────────────────────────────────────────────

function getStoredKey(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // storage unavailable (private mode edge cases)
  }
}

function setStoredKey(key: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, key);
  } catch {
    // Non-fatal: session cookie still works for this run.
  }
}

function clearStoredKey(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

// Generated react-query hooks send the stored key as a bearer token.
setAuthTokenGetter(() => getStoredKey());

function apiBase(): string {
  return (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
}

// ── Auth-status helpers ───────────────────────────────────────────────────────

/**
 * Check whether the current session is authenticated.
 * If the session cookie is gone but a key is stored, silently re-login.
 */
export async function checkAuth(): Promise<boolean> {
  try {
    const resp = await fetch(`${apiBase()}/api/auth/me`, {
      credentials: "same-origin",
    });
    if (resp.ok) {
      const data = await resp.json();
      if (data.authenticated) return true;
    }
  } catch {
    return false; // server unreachable — don't touch the stored key
  }

  // Session missing/expired — try the stored key before showing the login form.
  const stored = getStoredKey();
  if (!stored) return false;
  return login(stored);
}

/**
 * Submit the API key to the login endpoint.
 * On success the key is persisted so future sessions re-establish silently.
 * Returns true on success, false if the key is wrong or the server is down.
 */
export async function login(key: string): Promise<boolean> {
  try {
    const resp = await fetch(`${apiBase()}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ key }),
    });
    if (resp.ok) {
      setStoredKey(key);
      return true;
    }
    if (resp.status === 401) {
      // The key itself is wrong — stop retrying with it.
      if (getStoredKey() === key) clearStoredKey();
    }
    return false;
  } catch {
    return false;
  }
}

/** Clear the current session AND the stored key (explicit logout). */
export async function logout(): Promise<void> {
  // Authenticate the logout request with the cached key BEFORE clearing it,
  // so the server session is cleared even when the cookie is gone/partitioned.
  const headers = buildAuthHeaders();
  clearStoredKey();
  try {
    await fetch(`${apiBase()}/api/auth/logout`, {
      method: "POST",
      credentials: "same-origin",
      headers,
    });
  } catch {
    // Ignore network errors on logout
  }
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────

function withAuthHeaders(init?: RequestInit): RequestInit {
  const stored = getStoredKey();
  const headers = new Headers(init?.headers);
  if (stored && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${stored}`);
  }
  return { credentials: "same-origin", ...init, headers };
}

/**
 * Thin wrapper around `fetch` for same-origin API calls.
 * Sends the session cookie plus a bearer-token fallback, and on a 401
 * silently re-establishes the session with the stored key and retries once.
 *
 * Update-safety: every MUTATING call (anything except GET/HEAD) holds an
 * app-busy reason for its full duration, so the PWA update prompt can never
 * reload the page while a write is still on the wire.
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const method = (
    init?.method ??
    (typeof Request !== "undefined" && input instanceof Request ? input.method : "GET")
  ).toUpperCase();
  const release =
    method !== "GET" && method !== "HEAD" ? acquireBusy("api-write") : null;
  try {
    const resp = await fetch(input, withAuthHeaders(init));
    if (resp.status !== 401) return resp;

    // Session + bearer both rejected. If we hold a key, try to re-login once
    // and retry — unless the body is a one-shot stream that can't be resent.
    const stored = getStoredKey();
    const bodyIsStream =
      typeof ReadableStream !== "undefined" && init?.body instanceof ReadableStream;
    if (!stored || bodyIsStream) return resp;

    const ok = await login(stored);
    if (!ok) return resp;
    return await fetch(input, withAuthHeaders(init));
  } finally {
    release?.();
  }
}

/**
 * Build auth header object for calls that assemble headers manually
 * (SSE streaming in chat/index.tsx, XHR uploads).
 */
export function buildAuthHeaders(): Record<string, string> {
  const stored = getStoredKey();
  return stored ? { Authorization: `Bearer ${stored}` } : {};
}

/**
 * No-op kept for backwards compatibility.
 */
export async function initAuth(): Promise<void> {
  // Auth is established via checkAuth()/login() in App.tsx before render.
}

/** Return the stored API key, if any (used as a bearer token fallback). */
export function getApiToken(): string | null {
  return getStoredKey();
}
