// In-memory mock of expo-secure-store for jest (the real module is ESM and
// native-only). Mirrors the async API surface used by lib/token.ts and
// lib/server.ts.
const _store = new Map();

module.exports = {
  getItemAsync: jest.fn(async (key) => (_store.has(key) ? _store.get(key) : null)),
  setItemAsync: jest.fn(async (key, value) => { _store.set(key, value); }),
  deleteItemAsync: jest.fn(async (key) => { _store.delete(key); }),
};
