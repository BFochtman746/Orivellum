/**
 * WP5 update-safety: the busy registry must reliably gate the PWA update
 * reload — an update while a draft/stream/upload is in flight destroys work.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  acquireBusy,
  setBusyFlag,
  isAppBusy,
  busyReasons,
  subscribeBusy,
  busyLabel,
} from '../app-busy';

function drain() {
  // Reset module-level state between tests via the public API.
  for (const r of busyReasons()) setBusyFlag(r, false);
}

describe('app-busy registry', () => {
  beforeEach(drain);

  it('starts idle', () => {
    expect(isAppBusy()).toBe(false);
    expect(busyLabel()).toBeNull();
  });

  it('acquireBusy holds and releases a reason', () => {
    const release = acquireBusy('chat-stream');
    expect(isAppBusy()).toBe(true);
    expect(busyReasons()).toContain('chat-stream');
    release();
    expect(isAppBusy()).toBe(false);
  });

  it('release is idempotent and hold counts stack', () => {
    const r1 = acquireBusy('op');
    const r2 = acquireBusy('op');
    r1();
    r1(); // double release of the same hold must not free r2's hold
    expect(isAppBusy()).toBe(true);
    r2();
    expect(isAppBusy()).toBe(false);
  });

  it('setBusyFlag is level-triggered and idempotent', () => {
    setBusyFlag('chat-draft', true);
    setBusyFlag('chat-draft', true);
    expect(busyReasons()).toEqual(['chat-draft']);
    setBusyFlag('chat-draft', false);
    setBusyFlag('chat-draft', false);
    expect(isAppBusy()).toBe(false);
  });

  it('notifies subscribers on every transition', () => {
    let calls = 0;
    const unsub = subscribeBusy(() => calls++);
    setBusyFlag('x', true);
    setBusyFlag('x', false);
    unsub();
    setBusyFlag('x', true);
    expect(calls).toBe(2);
    setBusyFlag('x', false);
  });

  it('busyLabel prioritises draft > stream > upload > generic', () => {
    setBusyFlag('library-upload', true);
    expect(busyLabel()).toBe('An upload is in progress');
    setBusyFlag('chat-stream', true);
    expect(busyLabel()).toBe('A reply is still streaming');
    setBusyFlag('write-draft', true);
    expect(busyLabel()).toBe('You have an unsent draft');
    drain();
    setBusyFlag('operation-run', true);
    expect(busyLabel()).toBe('Work is in progress');
  });
});
