#!/usr/bin/env node
/**
 * Bundle byte budgets (WP5) — fails CI when the shipped payload regresses.
 *
 * Reads dist/public/.vite/manifest.json (build.manifest: true) and enforces:
 *   1. Home initial JS  — the entry chunk plus its full static-import closure
 *      must be ≤ 250 KB gzipped.
 *   2. CSS              — stylesheets shipped with the initial closure must be
 *      ≤ 50 KB gzipped.
 *   3. Route chunks     — every non-editor chunk must be ≤ 150 KB gzipped.
 *      The Writing Desk editor chunk (TipTap/ProseMirror) and chunks reachable
 *      ONLY from it are exempt by design.
 *   4. Isolation        — the initial closure must not contain the heavy
 *      point-of-use dependencies (TipTap/ProseMirror, recharts, markdown).
 *
 * Usage:  node scripts/check_bundle_budgets.mjs   (after `pnpm run build`)
 */
import { readFileSync, statSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIST = path.join(ROOT, 'dist', 'public');
const MANIFEST = path.join(DIST, '.vite', 'manifest.json');

// ── Budgets (gzip bytes) ─────────────────────────────────────────────────────
const BUDGET_HOME_JS = 250 * 1024;
const BUDGET_CSS = 50 * 1024;
const BUDGET_ROUTE_CHUNK = 150 * 1024;

// Source keys whose chunks are the "editor" — exempt from the route budget.
const EDITOR_KEYS = [/src\/pages\/write\/index\.tsx$/];

// Strings that must NOT appear in the initial closure (heavy deps that load
// at point of use). Package self-references usually survive minification.
const FORBIDDEN_IN_HOME = [/prosemirror/i, /recharts/i, /rehype-highlight/, /@tiptap/];

const gz = (file) => gzipSync(readFileSync(file), { level: 9 }).length;
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

let manifest;
try {
  manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'));
} catch (err) {
  console.error(`✗ Cannot read ${MANIFEST} — run \`pnpm run build\` first (${err.message})`);
  process.exit(1);
}

// Map file → manifest record for reverse lookups.
const byFile = new Map(Object.values(manifest).map((m) => [m.file, m]));
const entryKey = Object.keys(manifest).find((k) => manifest[k].isEntry);
if (!entryKey) {
  console.error('✗ No entry chunk found in manifest');
  process.exit(1);
}

// ── 1+2. Entry closure (static imports only — dynamic chunks load on demand) ─
const closureFiles = new Set();
const closureCss = new Set();
(function walk(key) {
  const rec = manifest[key];
  if (!rec || closureFiles.has(rec.file)) return;
  closureFiles.add(rec.file);
  for (const css of rec.css ?? []) closureCss.add(css);
  for (const imp of rec.imports ?? []) walk(imp);
})(entryKey);

let homeJs = 0;
for (const f of closureFiles) homeJs += gz(path.join(DIST, f));
let cssTotal = 0;
for (const f of closureCss) cssTotal += gz(path.join(DIST, f));

// ── 3. Per-chunk budget with editor exemption ────────────────────────────────
// The exempt set starts with the editor chunk(s) and grows to include chunks
// that are imported ONLY by already-exempt chunks (fixpoint).
const editorFiles = new Set(
  Object.keys(manifest)
    .filter((k) => EDITOR_KEYS.some((re) => re.test(k)))
    .map((k) => manifest[k].file),
);
// importers[file] = Set of files that statically OR dynamically import it
const importers = new Map();
for (const rec of Object.values(manifest)) {
  for (const dep of [...(rec.imports ?? []), ...(rec.dynamicImports ?? [])]) {
    const depFile = manifest[dep]?.file;
    if (!depFile) continue;
    if (!importers.has(depFile)) importers.set(depFile, new Set());
    importers.get(depFile).add(rec.file);
  }
}
const exempt = new Set(editorFiles);
let grew = true;
while (grew) {
  grew = false;
  for (const rec of Object.values(manifest)) {
    if (exempt.has(rec.file)) continue;
    const who = importers.get(rec.file);
    if (who && who.size > 0 && [...who].every((f) => exempt.has(f))) {
      exempt.add(rec.file);
      grew = true;
    }
  }
}

const failures = [];
const rows = [];
for (const rec of Object.values(manifest)) {
  if (!rec.file.endsWith('.js')) continue;
  if (closureFiles.has(rec.file)) continue; // counted in the Home budget
  const size = gz(path.join(DIST, rec.file));
  const isExempt = exempt.has(rec.file);
  rows.push({ file: rec.file, size, isExempt });
  if (!isExempt && size > BUDGET_ROUTE_CHUNK) {
    failures.push(`route chunk ${rec.file} is ${kb(size)} gzip (budget ${kb(BUDGET_ROUTE_CHUNK)})`);
  }
}
rows.sort((a, b) => b.size - a.size);

// ── 4. Heavy deps must not leak into the initial closure ────────────────────
for (const f of closureFiles) {
  const text = readFileSync(path.join(DIST, f), 'utf8');
  for (const re of FORBIDDEN_IN_HOME) {
    if (re.test(text)) failures.push(`initial closure chunk ${f} contains forbidden dependency marker ${re}`);
  }
}

// ── Report ───────────────────────────────────────────────────────────────────
console.log(`Home initial JS (entry + static closure, ${closureFiles.size} files): ${kb(homeJs)} gzip  (budget ${kb(BUDGET_HOME_JS)})`);
console.log(`Initial CSS: ${kb(cssTotal)} gzip  (budget ${kb(BUDGET_CSS)})`);
console.log('Largest on-demand chunks:');
for (const r of rows.slice(0, 8)) {
  console.log(`  ${kb(r.size).padStart(9)}  ${r.file}${r.isExempt ? '  (editor — exempt)' : ''}`);
}

if (homeJs > BUDGET_HOME_JS) failures.push(`Home initial JS ${kb(homeJs)} exceeds ${kb(BUDGET_HOME_JS)}`);
if (cssTotal > BUDGET_CSS) failures.push(`CSS ${kb(cssTotal)} exceeds ${kb(BUDGET_CSS)}`);

if (failures.length) {
  console.error('\n✗ BUNDLE BUDGET FAILURES:');
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log('\n✓ All bundle budgets met');
