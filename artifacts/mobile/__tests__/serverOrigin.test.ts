/**
 * lib/server.ts — user-configurable server origin.
 *
 * Guarantees under test:
 *   (1) normalizeOrigin accepts the address formats a user will actually type
 *       (bare host:port, http://, trailing slash, pasted /api suffix) and
 *       rejects garbage instead of storing a broken origin.
 *   (2) Persistence round-trip: saveServerOrigin -> loadServerOrigin restores
 *       the custom origin; clearServerOrigin reverts to the built-in default.
 *   (3) apiOrigin() always returns a usable origin (never null/empty).
 */
import {
  DEFAULT_ORIGIN,
  apiOrigin,
  clearServerOrigin,
  isCustomServer,
  loadServerOrigin,
  normalizeOrigin,
  saveServerOrigin,
} from '../lib/server';

describe('normalizeOrigin', () => {
  it('adds http:// to a bare Tailscale host:port', () => {
    expect(normalizeOrigin('100.92.116.70:8080')).toBe('http://100.92.116.70:8080');
  });

  it('keeps an explicit scheme and strips trailing slash', () => {
    expect(normalizeOrigin('http://100.92.116.70:8080/')).toBe('http://100.92.116.70:8080');
    expect(normalizeOrigin('https://example.com/')).toBe('https://example.com');
  });

  it('strips a pasted path such as /api or /orivellum-ui/', () => {
    expect(normalizeOrigin('http://192.168.4.37:8080/api')).toBe('http://192.168.4.37:8080');
    expect(normalizeOrigin('http://nimo:8080/orivellum-ui/')).toBe('http://nimo:8080');
  });

  it('trims surrounding whitespace', () => {
    expect(normalizeOrigin('  100.92.116.70:8080  ')).toBe('http://100.92.116.70:8080');
  });

  it('rejects empty and unparseable input', () => {
    expect(normalizeOrigin('')).toBeNull();
    expect(normalizeOrigin('   ')).toBeNull();
    expect(normalizeOrigin('ftp://host')).toBeNull();
    expect(normalizeOrigin('http://')).toBeNull();
  });
});

describe('origin persistence round-trip', () => {
  beforeEach(async () => {
    await clearServerOrigin();
  });

  it('starts at the built-in default', async () => {
    await loadServerOrigin();
    expect(apiOrigin()).toBe(DEFAULT_ORIGIN);
    expect(isCustomServer()).toBe(false);
  });

  it('save -> load restores the custom origin', async () => {
    await saveServerOrigin('http://100.92.116.70:8080');
    // Simulate an app restart: module state is reloaded from storage.
    await loadServerOrigin();
    expect(apiOrigin()).toBe('http://100.92.116.70:8080');
    expect(isCustomServer()).toBe(true);
  });

  it('clear reverts to the default', async () => {
    await saveServerOrigin('http://100.92.116.70:8080');
    await clearServerOrigin();
    await loadServerOrigin();
    expect(apiOrigin()).toBe(DEFAULT_ORIGIN);
    expect(isCustomServer()).toBe(false);
  });
});
