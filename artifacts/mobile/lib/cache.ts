/**
 * Lightweight offline cache using AsyncStorage.
 *
 * Caches API responses locally so the app can show stale data when the server
 * is unreachable. Each entry stores the payload and the time it was fetched.
 *
 * Usage:
 *   import { readCache, writeCache, isCacheStale } from "@/lib/cache";
 *   const cached = await readCache("library:list");
 *   await writeCache("library:list", data);
 */
import AsyncStorage from "@react-native-async-storage/async-storage";

interface CacheEntry<T> {
  ts: number;    // Unix ms timestamp
  data: T;
}

const PREFIX = "@orivellum:cache:";
const DEFAULT_MAX_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours

/** Read a cached value. Returns null if not found or JSON parse fails. */
export async function readCache<T = unknown>(key: string): Promise<CacheEntry<T> | null> {
  try {
    const raw = await AsyncStorage.getItem(PREFIX + key);
    if (!raw) return null;
    return JSON.parse(raw) as CacheEntry<T>;
  } catch {
    return null;
  }
}

/** Write a value to the cache with the current timestamp. */
export async function writeCache<T = unknown>(key: string, data: T): Promise<void> {
  try {
    const entry: CacheEntry<T> = { ts: Date.now(), data };
    await AsyncStorage.setItem(PREFIX + key, JSON.stringify(entry));
  } catch {
    // AsyncStorage failures are always silent — prefer fresh data
  }
}

/** Return true if the cached entry is older than maxAgeMs (default 24 h). */
export function isCacheStale(entry: CacheEntry<unknown>, maxAgeMs = DEFAULT_MAX_AGE_MS): boolean {
  return Date.now() - entry.ts > maxAgeMs;
}

/** Clear a single cached key. */
export async function clearCache(key: string): Promise<void> {
  try {
    await AsyncStorage.removeItem(PREFIX + key);
  } catch {}
}

/** Clear all Orivellum cache entries. */
export async function clearAllCache(): Promise<void> {
  try {
    const keys = await AsyncStorage.getAllKeys();
    const ourKeys = keys.filter(k => k.startsWith(PREFIX));
    if (ourKeys.length) await AsyncStorage.multiRemove(ourKeys);
  } catch {}
}
