/**
 * useWeather — resilience tests.
 *
 * Verifies the three critical failure paths:
 *
 *   (1) DENIED      — location permission refused → status='denied', data=null
 *                     WeatherCard renders null (silently hidden, no error UI)
 *
 *   (2) OFFLINE     — Open-Meteo unreachable + no prior cache → status='error',
 *                     data=null → WeatherCard also renders null (hidden)
 *
 *   (3) STALE CACHE — Open-Meteo unreachable BUT we have prior cached data →
 *                     surfaces stale data with status='ok' so the card stays
 *                     visible; the "Xh ago" timestamp label is the only signal
 *
 *   (4) GEOCODE     — reverseGeocodeAsync returns [] (no result) → city falls
 *                     back to 'Your Location' gracefully; weather still shown
 *
 *   (5) FOREGROUND  — AppState 'active' event triggers a reload when cache
 *                     is stale but skips it when cache is still fresh
 */

import { act, renderHook } from '@testing-library/react';
import { useWeather, buildHourlyPoints } from '../hooks/useWeather';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MOCK_POSITION = {
  coords: { latitude: 37.7749, longitude: -122.4194 },
  timestamp: Date.now(),
};

function makeWeatherJson() {
  return {
    current: {
      temperature_2m:       68,
      apparent_temperature: 65,
      weathercode:          1,
      windspeed_10m:        10,
      relative_humidity_2m: 72,
      is_day:               1,
    },
    daily: {
      time:               ['2026-08-08', '2026-08-09', '2026-08-10', '2026-08-11'],
      temperature_2m_max: [75, 73, 70, 68],
      temperature_2m_min: [55, 53, 51, 50],
      weathercode:        [1, 2, 3, 61],
    },
  };
}

// ── AppState mock ──────────────────────────────────────────────────────────────
// Variable name MUST start with "mock" so Jest allows it inside the factory.

const mockAppStateListeners: Array<(state: string) => void> = [];

function simulateForeground() {
  mockAppStateListeners.forEach(fn => fn('active'));
}

jest.mock('react-native', () => ({
  AppState: {
    addEventListener: jest.fn((event: string, handler: (state: string) => void) => {
      if (event === 'change') mockAppStateListeners.push(handler);
      return {
        remove: jest.fn(() => {
          const i = mockAppStateListeners.indexOf(handler);
          if (i > -1) mockAppStateListeners.splice(i, 1);
        }),
      };
    }),
  },
}));

// ── expo-location mock ─────────────────────────────────────────────────────────
// All variables accessed inside jest.mock factories must start with "mock".

let mockPermStatus   = 'granted';
let mockLastKnown: typeof MOCK_POSITION | null = MOCK_POSITION;
let mockGeocodeResult: any[] = [
  { city: 'San Francisco', region: 'California', country: 'US', district: null, name: null },
];

jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn(async () => ({ status: mockPermStatus })),
  getLastKnownPositionAsync:         jest.fn(async () => mockLastKnown),
  getCurrentPositionAsync:           jest.fn(async () => MOCK_POSITION),
  reverseGeocodeAsync:               jest.fn(async () => mockGeocodeResult),
  Accuracy:                          { Balanced: 3 },
}));

// ── fetch mock ─────────────────────────────────────────────────────────────────
// Not inside jest.mock(), so no naming constraint.  Mutated in beforeEach.

type FetchLike = () => Promise<Pick<Response, 'ok' | 'json'>>;
let fetchImpl: FetchLike;

global.fetch = jest.fn(() => fetchImpl()) as unknown as typeof fetch;

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Flush enough microtask ticks for the chained awaits inside load() to settle. */
async function settle(ticks = 10) {
  for (let i = 0; i < ticks; i++) await Promise.resolve();
}

function okFetch(): FetchLike {
  return () =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(makeWeatherJson()) } as any);
}

function failFetch(): FetchLike {
  return () => Promise.reject(new Error('Network error'));
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  jest.useFakeTimers();
  mockPermStatus    = 'granted';
  mockLastKnown     = MOCK_POSITION;
  mockGeocodeResult = [
    { city: 'San Francisco', region: 'California', country: 'US', district: null, name: null },
  ];
  fetchImpl = okFetch();
  mockAppStateListeners.length = 0;
});

afterEach(() => {
  jest.useRealTimers();
  jest.clearAllMocks();
});

// ── 1. Happy path (baseline) ──────────────────────────────────────────────────

describe('happy path', () => {
  it('resolves to status=ok with city and temperature', async () => {
    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.status).toBe('ok');
    expect(result.current.data?.city).toBe('San Francisco');
    expect(result.current.data?.tempF).toBe(68);
    expect(result.current.data?.forecast).toHaveLength(4);
  });
});

// ── 2. Permission denied ──────────────────────────────────────────────────────

describe('permission denied', () => {
  it('sets status=denied when location permission is not granted', async () => {
    mockPermStatus = 'denied';

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.status).toBe('denied');
    expect(result.current.data).toBeNull();
  });

  it('does not call fetch when permission is denied', async () => {
    mockPermStatus = 'denied';

    renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(global.fetch).not.toHaveBeenCalled();
  });

  /**
   * WeatherCard guard:
   *   if (status === 'denied' || (status === 'error' && !data)) return null;
   * Verified via hook output rather than mounting the RN component in Jest.
   */
  it('hook output satisfies the WeatherCard null-render guard', async () => {
    mockPermStatus = 'denied';

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    const { status, data } = result.current;
    expect(status === 'denied' || (status === 'error' && !data)).toBe(true);
  });

  it('handles "undetermined" the same as denied', async () => {
    mockPermStatus = 'undetermined';

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.status).toBe('denied');
    expect(result.current.data).toBeNull();
  });
});

// ── 3. Network offline — no cache ────────────────────────────────────────────

describe('network offline with no cached data', () => {
  it('sets status=error when Open-Meteo is unreachable on first load', async () => {
    fetchImpl = failFetch();

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.status).toBe('error');
    expect(result.current.data).toBeNull();
  });

  it('hook output satisfies the null-render guard on first-fetch failure', async () => {
    fetchImpl = failFetch();

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    const { status, data } = result.current;
    expect(status === 'denied' || (status === 'error' && !data)).toBe(true);
  });
});

// ── 4. Network offline — stale cache ─────────────────────────────────────────

describe('network offline with stale cached data', () => {
  it('surfaces stale cache with status=ok when fetch fails after a warm cache', async () => {
    // Populate cache with a successful first load.
    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });
    expect(result.current.status).toBe('ok');

    // Network goes down.
    fetchImpl = failFetch();

    // Force reload — bypasses the 15-min fresh-cache guard.
    await act(async () => {
      result.current.reload();
      await settle();
    });

    expect(result.current.status).toBe('ok');
    expect(result.current.data?.city).toBe('San Francisco');
    expect(result.current.data?.tempF).toBe(68);
  });

  it('fetchedAt is preserved from the cached snapshot (not reset on error)', async () => {
    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    const cachedAt = result.current.data!.fetchedAt;

    fetchImpl = failFetch();
    await act(async () => {
      result.current.reload();
      await settle();
    });

    expect(result.current.data?.fetchedAt).toBe(cachedAt);
  });

  it('does NOT satisfy null-render guard when stale cache is available (card stays visible)', async () => {
    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    fetchImpl = failFetch();
    await act(async () => {
      result.current.reload();
      await settle();
    });

    const { status, data } = result.current;
    expect(status === 'denied' || (status === 'error' && !data)).toBe(false);
  });
});

// ── 5. Geocode failure → city fallback ────────────────────────────────────────

describe('geocode failure / sparse results', () => {
  it('falls back to "Your Location" when reverseGeocodeAsync returns no results', async () => {
    mockGeocodeResult = [];

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.status).toBe('ok');
    expect(result.current.data?.city).toBe('Your Location');
  });

  it('still shows temperature and forecast when geocode is empty', async () => {
    mockGeocodeResult = [];

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.data?.tempF).toBe(68);
    expect(result.current.data?.forecast).toHaveLength(4);
  });

  it('uses city when present in geocode result', async () => {
    mockGeocodeResult = [{ city: 'Austin', region: 'Texas', country: 'US', district: null, name: null }];

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.data?.city).toBe('Austin');
  });

  it('falls back to district when city is absent', async () => {
    mockGeocodeResult = [{ city: null, district: 'Mission District', region: 'CA', country: 'US', name: null }];

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.data?.city).toBe('Mission District');
  });

  it('falls back to name when both city and district are absent', async () => {
    mockGeocodeResult = [{ city: null, district: null, region: 'CA', country: 'US', name: 'Golden Gate Park' }];

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(result.current.data?.city).toBe('Golden Gate Park');
  });
});

// ── 6. AppState foreground refresh ───────────────────────────────────────────

describe('AppState foreground refresh', () => {
  it('registers an AppState listener on mount', async () => {
    const { AppState } = jest.requireMock('react-native') as { AppState: { addEventListener: jest.Mock } };

    renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(AppState.addEventListener).toHaveBeenCalledWith('change', expect.any(Function));
  });

  it('removes the AppState listener on unmount', async () => {
    const removeMock = jest.fn();
    const { AppState } = jest.requireMock('react-native') as { AppState: { addEventListener: jest.Mock } };
    AppState.addEventListener.mockImplementationOnce((_: string, handler: (s: string) => void) => {
      mockAppStateListeners.push(handler);
      return { remove: removeMock };
    });

    const { unmount } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    unmount();
    expect(removeMock).toHaveBeenCalledTimes(1);
  });

  it('re-fetches on foreground when cache has expired (> 15 min)', async () => {
    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    const callsAfterLoad = (global.fetch as jest.Mock).mock.calls.length;

    // Age the cache beyond 15 minutes.
    jest.advanceTimersByTime(16 * 60 * 1_000);

    await act(async () => {
      simulateForeground();
      await settle();
    });

    expect((global.fetch as jest.Mock).mock.calls.length).toBeGreaterThan(callsAfterLoad);
  });

  it('does NOT re-fetch on foreground when cache is still fresh (< 15 min)', async () => {
    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    const callsAfterLoad = (global.fetch as jest.Mock).mock.calls.length;

    // Only 5 minutes — cache is still valid.
    jest.advanceTimersByTime(5 * 60 * 1_000);

    await act(async () => {
      simulateForeground();
      await settle();
    });

    expect((global.fetch as jest.Mock).mock.calls.length).toBe(callsAfterLoad);
  });
});

// ── 7. Position source ────────────────────────────────────────────────────────

describe('position source', () => {
  it('uses last-known position when available (avoids GPS wait)', async () => {
    const Location = jest.requireMock('expo-location') as {
      getLastKnownPositionAsync: jest.Mock;
      getCurrentPositionAsync:   jest.Mock;
    };
    mockLastKnown = MOCK_POSITION;

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(Location.getLastKnownPositionAsync).toHaveBeenCalledTimes(1);
    expect(Location.getCurrentPositionAsync).not.toHaveBeenCalled();
    expect(result.current.status).toBe('ok');
  });

  it('falls back to getCurrentPositionAsync when last-known is unavailable', async () => {
    const Location = jest.requireMock('expo-location') as {
      getCurrentPositionAsync: jest.Mock;
    };
    mockLastKnown = null;

    const { result } = renderHook(() => useWeather());
    await act(async () => { await settle(); });

    expect(Location.getCurrentPositionAsync).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe('ok');
  });
});

// ── 8. buildHourlyPoints — start-index edge cases ────────────────────────────
// Tests the pure helper directly so no Date/fetch mocking is needed.
// Covers the three risk scenarios flagged in the task spec:
//   (a) midnight boundary (local hour = 0)
//   (b) late-night boundary (local hour = 23)
//   (c) timezone where the local calendar date differs from the UTC date
//       — both "behind UTC" (UTC-N, e.g. Americas) and "ahead of UTC" (UTC+N)

function makeHourly48(day1: string, day2: string) {
  const times: string[] = [];
  const temps: number[] = [];
  const codes: number[] = [];
  const precs: number[] = [];
  for (let h = 0; h < 24; h++) {
    times.push(`${day1}T${String(h).padStart(2, '0')}:00`);
    temps.push(60 + h);   // 60°F at h=0 → 83°F at h=23 (day 1)
    codes.push(1);
    precs.push(h < 6 ? 0 : 20);
  }
  for (let h = 0; h < 24; h++) {
    times.push(`${day2}T${String(h).padStart(2, '0')}:00`);
    temps.push(50 + h);   // 50°F at h=0 → 73°F at h=23 (day 2)
    codes.push(2);
    precs.push(0);
  }
  return { times, temps, codes, precs };
}

describe('buildHourlyPoints — start-index edge cases', () => {
  const DAY1 = '2026-08-08';
  const DAY2 = '2026-08-09';

  it('midnight (hour=0): first slot is hour 0, 24 slots cover the full day', () => {
    const { times, temps, codes, precs } = makeHourly48(DAY1, DAY2);
    const pts = buildHourlyPoints(times, temps, codes, precs, 0, DAY1);

    expect(pts[0].label).toBe('Now');
    expect(pts[0].hour).toBe(0);
    expect(pts[0].tempF).toBe(60);
    expect(pts).toHaveLength(24);
    expect(pts[23].hour).toBe(23);   // stays within day 1
  });

  it('late night (hour=23): first slot is hour 23, next 23 wrap into the next day', () => {
    const { times, temps, codes, precs } = makeHourly48(DAY1, DAY2);
    const pts = buildHourlyPoints(times, temps, codes, precs, 23, DAY1);

    expect(pts[0].label).toBe('Now');
    expect(pts[0].hour).toBe(23);
    expect(pts).toHaveLength(24);
    // Slot 1 rolls over to day 2 hour 0
    expect(pts[1].hour).toBe(0);
    expect(pts[1].label).toBe('12 AM');
    // Last slot is day 2 hour 22
    expect(pts[23].hour).toBe(22);
    expect(pts[23].tempF).toBe(50 + 22); // day-2 temp series
  });

  it('timezone behind UTC (e.g. UTC-5): local date is earlier than UTC date', () => {
    // e.g. 2026-08-09T01:00 UTC → 2026-08-08T20:00 local (New York).
    // toISOString() would wrongly return '2026-08-09'; local components give '2026-08-08'.
    // Open-Meteo (timezone=auto) emits local-time strings → day1='2026-08-08'.
    const localDay  = '2026-08-08';
    const nextDay   = '2026-08-09';
    const localHour = 20;
    const { times, temps, codes, precs } = makeHourly48(localDay, nextDay);
    const pts = buildHourlyPoints(times, temps, codes, precs, localHour, localDay);

    expect(pts[0].label).toBe('Now');
    expect(pts[0].hour).toBe(20);
    expect(pts).toHaveLength(24);
    // 4 hours remain in localDay (20,21,22,23) then 20 from nextDay
    expect(pts[3].hour).toBe(23);
    expect(pts[4].hour).toBe(0);
    expect(pts[23].hour).toBe(19);
  });

  it('timezone ahead of UTC (e.g. UTC+10): local date is later than UTC date', () => {
    // e.g. 2026-08-08T22:00 UTC → 2026-08-09T08:00 local (Sydney).
    // toISOString() would wrongly return '2026-08-08'; local components give '2026-08-09'.
    const localDay  = '2026-08-09';
    const nextDay   = '2026-08-10';
    const localHour = 8;
    const { times, temps, codes, precs } = makeHourly48(localDay, nextDay);
    const pts = buildHourlyPoints(times, temps, codes, precs, localHour, localDay);

    expect(pts[0].label).toBe('Now');
    expect(pts[0].hour).toBe(8);
    expect(pts).toHaveLength(24);
    // 16 hours from localDay (8–23) + 8 from nextDay (0–7) = 24
    expect(pts[15].hour).toBe(23);
    expect(pts[16].hour).toBe(0);
    expect(pts[23].hour).toBe(7);
  });

  it('produces correct AM/PM labels starting from hour=0', () => {
    const { times, temps, codes, precs } = makeHourly48(DAY1, DAY2);
    const pts = buildHourlyPoints(times, temps, codes, precs, 0, DAY1);

    expect(pts[0].label).toBe('Now');
    expect(pts[1].label).toBe('1 AM');
    expect(pts[11].label).toBe('11 AM');
    expect(pts[12].label).toBe('12 PM');
    expect(pts[13].label).toBe('1 PM');
    expect(pts[23].label).toBe('11 PM');
  });

  it('falls back to index 0 when no matching date prefix is found', () => {
    // localDateStr does not appear in times at all — simulate a stale/wrong date
    const { times, temps, codes, precs } = makeHourly48(DAY2, '2026-08-10');
    const pts = buildHourlyPoints(times, temps, codes, precs, 12, DAY1); // DAY1 not in times

    // baseIdx = 0 fallback; still returns 24 valid slots from the start of the array
    expect(pts).toHaveLength(24);
    expect(pts[0].label).toBe('Now');
    expect(pts[0].hour).toBe(0); // starts at index 0 of the times array (DAY2 T00:00)
  });
});
