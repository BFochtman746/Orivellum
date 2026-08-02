/**
 * API credential management for the Orivellum mobile app.
 *
 * Credentials are stored in expo-secure-store (device Keychain / Android
 * Keystore) — never in the app bundle or AsyncStorage.  The user enters
 * their API key once via the in-app login screen; it is persisted securely
 * on-device and loaded at startup without any network request.
 *
 * On startup:
 *   1. Call `loadToken()` — reads from SecureStore, wires setAuthTokenGetter.
 *   2. If it returns null → show the LoginScreen.
 *   3. Otherwise → proceed to the main app.
 *
 * On user-initiated key entry:
 *   1. Validate via `validateKey(key)` — POST /api/auth/login.
 *   2. If valid → call `saveToken(key)`.
 *
 * On logout:
 *   1. Call `clearToken()`.
 */
import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { setAuthTokenGetter } from '@workspace/api-client-react';

const SECURE_STORE_KEY = 'orivellum_api_key';

// SecureStore has no web implementation — fall back to localStorage there.
const _isWeb = Platform.OS === 'web';

async function _storageGet(): Promise<string | null> {
  if (_isWeb) {
    try { return window.localStorage.getItem(SECURE_STORE_KEY); } catch { return null; }
  }
  return SecureStore.getItemAsync(SECURE_STORE_KEY);
}

async function _storageSet(value: string): Promise<void> {
  if (_isWeb) {
    window.localStorage.setItem(SECURE_STORE_KEY, value);
    return;
  }
  await SecureStore.setItemAsync(SECURE_STORE_KEY, value);
}

async function _storageDelete(): Promise<void> {
  if (_isWeb) {
    window.localStorage.removeItem(SECURE_STORE_KEY);
    return;
  }
  await SecureStore.deleteItemAsync(SECURE_STORE_KEY);
}

let _token: string | null = null;

/** Wires the generated react-query hooks with the current token. */
function _wireHooks(): void {
  setAuthTokenGetter(() => _token || null);
}

/**
 * Load the persisted API key from SecureStore.
 * Must be called at app startup (before any API requests).
 * Returns the key, or null if none is stored.
 */
export async function loadToken(): Promise<string | null> {
  try {
    const stored = await _storageGet();
    _token = stored || null;
  } catch {
    _token = null;
  }
  _wireHooks();
  return _token;
}

/**
 * Validate a user-supplied key against the API server.
 * Returns true if the server accepts the key.
 */
export async function validateKey(key: string, baseUrl: string): Promise<boolean> {
  try {
    const resp = await fetch(`${baseUrl}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

/**
 * Persist a validated API key to SecureStore and update the current token.
 */
export async function saveToken(key: string): Promise<void> {
  await _storageSet(key);
  _token = key;
  _wireHooks();
}

/**
 * Remove the stored API key (logout / key rotation).
 */
export async function clearToken(): Promise<void> {
  try {
    await _storageDelete();
  } catch {
    // Ignore — key may not exist
  }
  _token = null;
  _wireHooks();
}

/** Return the current bearer token synchronously (after loadToken() has run). */
export function getApiToken(): string | null {
  return _token;
}
