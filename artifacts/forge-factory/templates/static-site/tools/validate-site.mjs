import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
const required = ['index.html', 'about.html', 'contact.html', 'design-tokens.css', 'design-system.json', 'styles.css', 'app.js', 'site.config.json'];
for (const file of required) { try { await readFile(file); } catch { throw new Error(`Required starter file missing: ${file}`); } }
const html = await readFile('index.html', 'utf8');
if (!/<main\b/i.test(html) || !/<h1\b/i.test(html) || !/meta\s+name=["']description/i.test(html)) throw new Error('Home page requires a main landmark, h1, and meta description.');
if (/\{\{[A-Z_]+\}\}/.test(html)) throw new Error('Unresolved starter token found in index.html.');
for (const page of ['index.html', 'about.html', 'contact.html']) {
  const pageHtml = await readFile(page, 'utf8');
  if (!/href=["']design-tokens\.css["']/.test(pageHtml)) throw new Error(`${page} must load the shared design-token stylesheet.`);
}
const tokenSheet = await readFile('design-tokens.css', 'utf8');
for (const tokenGroup of ['--color-', '--font-', '--space-', '--radius-', '--motion-']) if (!tokenSheet.includes(tokenGroup)) throw new Error(`Missing design token group ${tokenGroup}.`);
if (!/prefers-reduced-motion/.test(tokenSheet)) throw new Error('Design tokens must provide a reduced-motion fallback.');
const designSystem = JSON.parse(await readFile('design-system.json', 'utf8'));
if (!designSystem.selectedConceptId || !designSystem.palette || !designSystem.typography) throw new Error('Design-system manifest is incomplete.');
console.log(`Validated ${required.length} required site files.`);
