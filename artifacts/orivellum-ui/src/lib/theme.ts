/**
 * theme.ts — single owner of appearance + calibration state.
 *
 * The saved *preference* may be 'daylight' | 'hull' | 'system'; the *resolved*
 * theme is always 'daylight' | 'hull'. index.html runs a pre-paint copy of the
 * resolve logic so first paint is always correct; this module re-applies on
 * mount (idempotent) and owns every change after that.
 *
 * It also owns the `.dark` class: legacy VELLUM pages follow the same saved
 * preference (Hull ⇒ dark shadcn tokens), so no page ever forces appearance
 * on its own — portals, sheets, and toasts all inherit the root theme.
 *
 * Calibration (text size / editor measure / reading face) rides <html> data
 * attributes the same way, and every preference is mirrored fire-and-forget
 * to the personal settings record so a re-install can restore it.
 */
import { useSyncExternalStore } from "react";
import { apiFetch } from "@/lib/auth";

const API_BASE = `${import.meta.env.BASE_URL?.replace(/\/$/, "") || ""}/api`;

export type ThemePreference = "daylight" | "hull" | "system";
export type ResolvedTheme = "daylight" | "hull";
export type TextSize = "100" | "112" | "125";
export type EditorMeasure = "focused" | "standard" | "wide";
export type ReadingFace = "sans" | "serif";

const THEME_KEY = "orivellum-theme";
const TEXT_KEY = "orivellum-text-size";
const MEASURE_KEY = "orivellum-measure";
const FACE_KEY = "orivellum-reading-face";

export interface UiPreferences {
  theme: ThemePreference;
  textSize: TextSize;
  measure: EditorMeasure;
  readingFace: ReadingFace;
}

export const UI_PREF_DEFAULTS: UiPreferences = {
  theme: "daylight",
  textSize: "100",
  measure: "standard",
  readingFace: "sans",
};

const listeners = new Set<() => void>();
function notify() {
  for (const fn of listeners) fn();
}

function read<T extends string>(key: string, valid: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v && (valid as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

export function getThemePreference(): ThemePreference {
  return read(THEME_KEY, ["daylight", "hull", "system"] as const, "daylight");
}

export function resolveTheme(pref: ThemePreference): ResolvedTheme {
  if (pref === "system") {
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "hull" : "daylight";
    } catch {
      return "daylight";
    }
  }
  return pref;
}

/** Sync every root-level artifact of the resolved theme. Idempotent. */
export function applyResolvedTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.dataset.theme = resolved;
  root.classList.toggle("dark", resolved === "hull");
  root.style.colorScheme = resolved === "hull" ? "dark" : "light";

  // Browser/PWA chrome color follows the canvas token — read it from CSS so
  // the palette stays single-sourced in the token layer.
  const canvas = getComputedStyle(root).getPropertyValue("--gd-bg").trim();
  const themeMeta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (themeMeta && canvas) themeMeta.content = canvas;
  const barMeta = document.querySelector<HTMLMetaElement>(
    'meta[name="apple-mobile-web-app-status-bar-style"]',
  );
  if (barMeta) barMeta.content = resolved === "hull" ? "black-translucent" : "default";
}

function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode — in-memory state still applies */
  }
}

export function getUiPreferences(): UiPreferences {
  return {
    theme: getThemePreference(),
    textSize: read(TEXT_KEY, ["100", "112", "125"] as const, "100"),
    measure: read(MEASURE_KEY, ["focused", "standard", "wide"] as const, "standard"),
    readingFace: read(FACE_KEY, ["sans", "serif"] as const, "sans"),
  };
}

function applyCalibration(p: UiPreferences) {
  const root = document.documentElement;
  root.dataset.textSize = p.textSize;
  root.dataset.measure = p.measure;
  root.dataset.readingFace = p.readingFace;
}

const PREF_KEYS: Record<keyof UiPreferences, { storage: string; valid: readonly string[] }> = {
  theme: { storage: THEME_KEY, valid: ["daylight", "hull", "system"] },
  textSize: { storage: TEXT_KEY, valid: ["100", "112", "125"] },
  measure: { storage: MEASURE_KEY, valid: ["focused", "standard", "wide"] },
  readingFace: { storage: FACE_KEY, valid: ["sans", "serif"] },
};

/** Provenance marker: which preference keys the user AUTHORED on this
 *  device (via a setter), as opposed to values hydrated from the server.
 *  Hydrated values share the same storage keys, so presence alone cannot
 *  distinguish "the user chose this here" from "restored from elsewhere" —
 *  mirroring a hydrated (possibly stale) value would overwrite another
 *  device's newer choice. */
const AUTHORED_KEY = "orivellum-ui-authored";

function authoredSet(): Set<string> {
  try {
    const raw = localStorage.getItem(AUTHORED_KEY);
    const arr: unknown = raw ? JSON.parse(raw) : [];
    return new Set(
      Array.isArray(arr) ? arr.filter((k): k is string => typeof k === "string" && k in PREF_KEYS) : [],
    );
  } catch {
    return new Set();
  }
}

function markAuthored(...names: (keyof UiPreferences)[]): void {
  try {
    const s = authoredSet();
    for (const n of names) s.add(n);
    localStorage.setItem(AUTHORED_KEY, JSON.stringify([...s]));
  } catch {
    /* private mode */
  }
}

/** Only the preferences the user explicitly chose on THIS device.
 *  Untouched and merely-hydrated settings are never mirrored, so they
 *  can't clobber choices saved from another device (the server merges
 *  partial records). */
function explicitPreferences(): Partial<UiPreferences> {
  const out: Partial<UiPreferences> = {};
  const authored = authoredSet();
  for (const [name, spec] of Object.entries(PREF_KEYS)) {
    if (!authored.has(name)) continue;
    try {
      const v = localStorage.getItem(spec.storage);
      if (v && spec.valid.includes(v)) (out as Record<string, string>)[name] = v;
    } catch {
      /* private mode */
    }
  }
  return out;
}

let hydrated = false;
let mirrorQueued = false;
let mirrorTimer: ReturnType<typeof setTimeout> | undefined;

/** Fire-and-forget mirror into the personal settings record (debounced).
 *  Sends only explicitly-set keys, and never before hydration settles —
 *  an unhydrated client must not overwrite a saved record. */
function mirrorToServer() {
  if (!hydrated) {
    mirrorQueued = true;
    return;
  }
  clearTimeout(mirrorTimer);
  mirrorTimer = setTimeout(() => {
    const explicit = explicitPreferences();
    if (Object.keys(explicit).length === 0) return;
    apiFetch(`${API_BASE}/system/settings/ui-preferences`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(explicit),
    }).catch(() => {
      /* offline is fine — localStorage remains authoritative on this device */
    });
  }, 600);
}

/** Restore saved preferences: any key the user has NOT authored on this
 *  device adopts the server-saved value (fresh installs restore everything;
 *  returning devices refresh stale hydrated values). Locally authored
 *  choices always win, and hydration never marks a key as authored. */
async function hydrateFromServer(): Promise<void> {
  try {
    const res = await apiFetch(`${API_BASE}/system/settings/ui-preferences`);
    if (res.ok) {
      const saved = (await res.json()) as Partial<UiPreferences>;
      const authored = authoredSet();
      let adopted = false;
      for (const [name, spec] of Object.entries(PREF_KEYS)) {
        const value = saved[name as keyof UiPreferences];
        if (value && spec.valid.includes(value) && !authored.has(name)) {
          try {
            if (localStorage.getItem(spec.storage) !== value) {
              persist(spec.storage, value);
              adopted = true;
            }
          } catch {
            persist(spec.storage, value);
            adopted = true;
          }
        }
      }
      if (adopted) {
        const prefs = getUiPreferences();
        applyResolvedTheme(resolveTheme(prefs.theme));
        applyCalibration(prefs);
        notify();
      }
    }
  } catch {
    /* offline — this device's own choices still mirror safely (merge PUT) */
  } finally {
    hydrated = true;
    if (mirrorQueued) {
      mirrorQueued = false;
      mirrorToServer();
    }
  }
}

export function setThemePreference(pref: ThemePreference): void {
  persist(THEME_KEY, pref);
  markAuthored("theme");
  applyResolvedTheme(resolveTheme(pref));
  mirrorToServer();
  notify();
}

export function setCalibration(patch: Partial<Omit<UiPreferences, "theme">>): void {
  if (patch.textSize) {
    persist(TEXT_KEY, patch.textSize);
    markAuthored("textSize");
  }
  if (patch.measure) {
    persist(MEASURE_KEY, patch.measure);
    markAuthored("measure");
  }
  if (patch.readingFace) {
    persist(FACE_KEY, patch.readingFace);
    markAuthored("readingFace");
  }
  applyCalibration(getUiPreferences());
  mirrorToServer();
  notify();
}

export function resetUiPreferences(): void {
  persist(THEME_KEY, UI_PREF_DEFAULTS.theme);
  persist(TEXT_KEY, UI_PREF_DEFAULTS.textSize);
  persist(MEASURE_KEY, UI_PREF_DEFAULTS.measure);
  persist(FACE_KEY, UI_PREF_DEFAULTS.readingFace);
  // Reset is an explicit choice of the defaults — it propagates everywhere.
  markAuthored("theme", "textSize", "measure", "readingFace");
  applyResolvedTheme(resolveTheme(UI_PREF_DEFAULTS.theme));
  applyCalibration(UI_PREF_DEFAULTS);
  mirrorToServer();
  notify();
}

/** Called once from main.tsx before render. Re-applies (idempotent with the
 *  index.html boot script), restores saved preferences for keys this device
 *  hasn't chosen yet, and tracks OS scheme changes while pref=system. */
export function initTheme(): void {
  const prefs = getUiPreferences();
  applyResolvedTheme(resolveTheme(prefs.theme));
  applyCalibration(prefs);
  void hydrateFromServer();
  try {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (getThemePreference() === "system") {
        applyResolvedTheme(resolveTheme("system"));
        notify();
      }
    });
  } catch {
    /* older WebKit without addEventListener on MQL — preference UI still works */
  }
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useThemePreference(): {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (p: ThemePreference) => void;
} {
  const preference = useSyncExternalStore(subscribe, getThemePreference);
  const resolved = useSyncExternalStore(subscribe, () =>
    resolveTheme(getThemePreference()),
  );
  return { preference, resolved, setPreference: setThemePreference };
}

export function useUiPreferences(): UiPreferences {
  const snap = useSyncExternalStore(subscribe, () => JSON.stringify(getUiPreferences()));
  return JSON.parse(snap) as UiPreferences;
}
