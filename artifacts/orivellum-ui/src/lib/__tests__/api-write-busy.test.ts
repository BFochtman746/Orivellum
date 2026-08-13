/**
 * Update-safety regression: EVERY mutating API call — through either the raw
 * `apiFetch` wrapper or the generated client's `customFetch` — must hold the
 * app-busy registry for its full duration, so the PWA update prompt
 * (`applyUpdate` consults `isAppBusy()`) can never reload mid-write.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { isAppBusy, busyReasons, setBusyFlag } from '../app-busy';
import { apiFetch } from '../auth';
import { customFetch, setMutationTracker } from '@workspace/api-client-react';
import { acquireBusy } from '../app-busy';

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

const realFetch = globalThis.fetch;

describe('mutating API calls hold the app-busy registry', () => {
  beforeEach(() => {
    for (const r of busyReasons()) setBusyFlag(r, false);
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it('apiFetch PATCH holds busy while in flight and releases after', async () => {
    const gate = deferred<Response>();
    globalThis.fetch = vi.fn(() => gate.promise) as unknown as typeof fetch;

    const call = apiFetch('/api/write/documents/x', { method: 'PATCH', body: '{}' });
    expect(isAppBusy()).toBe(true);
    expect(busyReasons()).toContain('api-write');

    gate.resolve(new Response('{}', { status: 200 }));
    await call;
    expect(isAppBusy()).toBe(false);
  });

  it('apiFetch PATCH releases busy even when the request rejects', async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('offline'))) as unknown as typeof fetch;
    await expect(apiFetch('/api/x', { method: 'POST', body: '{}' })).rejects.toThrow('offline');
    expect(isAppBusy()).toBe(false);
  });

  it('apiFetch GET does NOT hold busy', async () => {
    const gate = deferred<Response>();
    globalThis.fetch = vi.fn(() => gate.promise) as unknown as typeof fetch;
    const call = apiFetch('/api/x');
    expect(isAppBusy()).toBe(false);
    gate.resolve(new Response('{}', { status: 200 }));
    await call;
  });

  it('customFetch mutating call holds busy via the registered tracker', async () => {
    // auth.ts registers the tracker at import time; re-register explicitly so
    // the test does not depend on module-eval order.
    setMutationTracker(() => acquireBusy('api-write'));
    const gate = deferred<Response>();
    globalThis.fetch = vi.fn(() => gate.promise) as unknown as typeof fetch;

    const call = customFetch('/api/things', { method: 'POST', body: '{}' });
    // customFetch may await an auth-token getter before dispatching — flush
    // microtasks so the request has actually started (fetch is still gated).
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(isAppBusy()).toBe(true);
    expect(busyReasons()).toContain('api-write');

    gate.resolve(new Response('{"ok":true}', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    await call;
    expect(isAppBusy()).toBe(false);
  });

  it('customFetch releases busy when the server rejects the write', async () => {
    setMutationTracker(() => acquireBusy('api-write'));
    globalThis.fetch = vi.fn(async () => new Response('nope', { status: 500 })) as unknown as typeof fetch;
    await expect(customFetch('/api/things', { method: 'DELETE' })).rejects.toThrow();
    expect(isAppBusy()).toBe(false);
  });

  it('customFetch GET does not invoke the tracker', async () => {
    const tracker = vi.fn(() => acquireBusy('api-write'));
    setMutationTracker(tracker);
    globalThis.fetch = vi.fn(async () => new Response('{}', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as unknown as typeof fetch;
    await customFetch('/api/things');
    expect(tracker).not.toHaveBeenCalled();
  });
});
