import { promises as fs } from 'node:fs';
import path from 'node:path';
import { runProcess } from './process.mjs';
import { nowIso, resolveWithin, truncate, writeJsonAtomic } from './utils.mjs';

export async function runQualityGates({ workspace, jobDirectory, policy, onEvent, previewUrl = null }) {
  const gates = [];
  const packageFile = path.join(workspace, 'package.json');
  const packageJson = await tryReadJson(packageFile);
  const reportDirectory = path.join(jobDirectory, 'quality');
  await fs.mkdir(reportDirectory, { recursive: true });

  if (packageJson?.scripts?.lint) gates.push(await scriptGate('lint', ['run', 'lint'], workspace, onEvent));
  else gates.push({ id: 'lint', status: 'not_configured', detail: 'No package.json lint script found.' });

  if (packageJson?.scripts?.test) gates.push(await scriptGate('unit', ['test'], workspace, onEvent));
  else gates.push({ id: 'unit', status: 'not_configured', detail: 'No package.json test script found.' });

  if (packageJson?.scripts?.build) gates.push(await scriptGate('build', ['run', 'build'], workspace, onEvent));
  else gates.push({ id: 'build', status: 'not_configured', detail: 'No package.json build script found.' });

  gates.push(await visualGate(workspace, onEvent));
  gates.push(await linkGate(workspace, onEvent));
  gates.push(await scopeGate(workspace, onEvent));
  gates.push(await browserGate(workspace, reportDirectory, onEvent, previewUrl));
  gates.push(await commandGate('semgrep', ['semgrep', 'scan', '--config', '.semgrep/forge-rules.yml', '--quiet', '--json', '.'], workspace, onEvent, { optional: true, timeoutMs: 180000 }));
  gates.push(await commandGate('gitleaks', ['gitleaks', 'detect', '--no-banner', '--redact'], workspace, onEvent, { optional: true, timeoutMs: 180000 }));
  gates.push(await osvGate(workspace, onEvent));

  const report = { generatedAt: nowIso(), workspace, gates, summary: summariseGates(gates, policy) };
  await writeJsonAtomic(path.join(reportDirectory, 'quality-report.json'), report);
  await onEvent('quality_complete', `Quality gates: ${report.summary.status}.`, report.summary);
  return report;
}

export function releaseDecision({ jobId, gates, reviewer, policy }) {
  const summary = summariseGates(gates.gates || gates, policy);
  const reviewerVerdict = reviewer?.verdict || 'conditional';
  const status = summary.status === 'blocked' || reviewerVerdict === 'block'
    ? 'BLOCKED'
    : summary.status === 'verified' && reviewerVerdict === 'pass'
      ? 'VERIFIED'
      : 'CONDITIONAL';
  return {
    status,
    jobId,
    createdAt: nowIso(),
    gates: gates.gates || gates,
    reviewer,
    manualApprovalRequired: Boolean(policy.release.requireManualApproval),
    reason: status === 'VERIFIED'
      ? 'All required quality gates passed and the independent review passed.'
      : status === 'BLOCKED'
        ? 'At least one required gate or independent review blocked release.'
        : 'Required evidence is incomplete, unconfigured, or needs manual approval.'
  };
}

function summariseGates(gates, policy) {
  const byId = new Map(gates.map((gate) => [gate.id, gate]));
  const requiredPasses = policy.release.requiredPasses || [];
  const missingRequired = requiredPasses.filter((id) => byId.get(id)?.status !== 'passed');
  const blockingFailures = gates.filter((gate) => ['failed', 'blocked'].includes(gate.status));
  const browser = byId.get('browser');
  const security = ['semgrep', 'gitleaks', 'osv'].map((id) => byId.get(id));
  const browserMissing = policy.release.requireBrowserEvidence && browser?.status !== 'passed';
  const securityMissing = policy.release.requireSecurityEvidence && security.some((gate) => gate?.status !== 'passed');
  if (blockingFailures.length || missingRequired.some((id) => byId.get(id)?.status === 'failed')) return { status: 'blocked', missingRequired, blocking: blockingFailures.map((gate) => gate.id) };
  if (missingRequired.length || browserMissing || securityMissing) return { status: 'conditional', missingRequired, browserMissing, securityMissing };
  return { status: 'verified', missingRequired: [] };
}

async function scriptGate(id, args, workspace, onEvent) {
  const result = await runProcess('npm', args, { cwd: workspace, timeoutMs: 180000 });
  await onEvent('gate', `${id}: ${result.ok ? 'passed' : 'failed'}`, { command: `npm ${args.join(' ')}`, code: result.code });
  return { id, status: result.ok ? 'passed' : 'failed', command: `npm ${args.join(' ')}`, result: compactResult(result) };
}

async function commandGate(id, argv, workspace, onEvent, { optional = false, timeoutMs = 120000 } = {}) {
  const result = await runProcess(argv[0], argv.slice(1), { cwd: workspace, timeoutMs });
  const unavailable = result.code === null || /ENOENT|not found|is not recognized/i.test(result.stderr || '');
  const status = unavailable && optional ? 'not_configured' : result.ok ? 'passed' : 'failed';
  await onEvent('gate', `${id}: ${status}`, { command: argv.join(' '), code: result.code });
  return { id, status, command: argv.join(' '), result: compactResult(result) };
}

async function browserGate(workspace, reportDirectory, onEvent, previewUrl) {
  const executable = process.platform === 'win32' ? 'playwright.cmd' : 'playwright';
  const local = path.join(workspace, 'node_modules', '.bin', executable);
  try {
    await fs.access(local);
  } catch {
    await onEvent('gate', 'browser: not_configured', { detail: 'Install Playwright in the website project before browser verification.' });
    return { id: 'browser', status: 'not_configured', detail: 'Local Playwright is not installed; no network install was attempted.' };
  }
  if (!previewUrl) return { id: 'browser', status: 'not_configured', detail: 'No private preview URL was supplied to browser verification.' };
  const browserOutput = path.join(reportDirectory, 'browser');
  const result = await runProcess(local, ['test'], { cwd: workspace, timeoutMs: 180000, env: { FORGE_PREVIEW_URL: previewUrl, FORGE_BROWSER_OUTPUT: browserOutput } });
  const status = result.ok ? 'passed' : 'failed';
  await onEvent('gate', `browser: ${status}`, { command: `${local} test`, code: result.code, previewUrl, browserOutput });
  return { id: 'browser', status, command: `${local} test`, browserOutput, result: compactResult(result) };
}

async function osvGate(workspace, onEvent) {
  if (process.env.FORGE_ALLOW_OSV_NETWORK !== '1') {
    await onEvent('gate', 'osv: not_configured', { detail: 'Set FORGE_ALLOW_OSV_NETWORK=1 only after approving vulnerability-feed access.' });
    return { id: 'osv', status: 'not_configured', detail: 'OSV network access is denied by default.' };
  }
  return commandGate('osv', ['osv-scanner', 'scan', 'source', '-r', '.'], workspace, onEvent, { optional: true, timeoutMs: 180000 });
}

async function linkGate(workspace, onEvent) {
  try {
    const findings = await inspectHtmlLinks(workspace);
    const status = findings.length ? 'failed' : 'passed';
    await onEvent('gate', `links: ${status}`, { count: findings.length });
    return { id: 'links', status, findings };
  } catch (error) {
    return { id: 'links', status: 'failed', detail: error.message };
  }
}

async function visualGate(workspace, onEvent) {
  const findings = [];
  const tokensFile = path.join(workspace, 'design-tokens.css');
  const manifestFile = path.join(workspace, 'design-system.json');
  let tokenSheet = null; // null = file absent or unreadable; '' = file present but empty
  try { tokenSheet = await fs.readFile(tokensFile, 'utf8'); }
  catch { findings.push({ area: 'tokens', detail: 'design-tokens.css is missing.' }); }
  // An empty file is just as broken as a missing one — treat it as absent for
  // structural checks.  The `null` guard distinguishes "file missing" from
  // "file present but empty" so the missing finding is not emitted twice.
  if (tokenSheet !== null && tokenSheet.trim() === '') {
    findings.push({ area: 'tokens', detail: 'design-tokens.css is empty.' });
    tokenSheet = null; // suppress further structural checks
  }
  const requiredTokenGroups = ['--color-', '--font-', '--space-', '--radius-', '--motion-'];
  for (const group of requiredTokenGroups) if (tokenSheet && !tokenSheet.includes(group)) findings.push({ area: 'tokens', detail: `Missing required token group ${group}` });
  if (tokenSheet && !/:root\s*\{/.test(tokenSheet)) findings.push({ area: 'tokens', detail: 'Tokens must be declared in :root.' });
  if (tokenSheet && !/prefers-reduced-motion/.test(tokenSheet)) findings.push({ area: 'motion', detail: 'The token sheet must include a prefers-reduced-motion fallback.' });
  let manifest = null;
  try { manifest = JSON.parse(await fs.readFile(manifestFile, 'utf8')); }
  catch { findings.push({ area: 'manifest', detail: 'design-system.json is missing or invalid JSON.' }); }
  if (manifest && (!manifest.selectedConceptId || !manifest.palette || !manifest.typography)) findings.push({ area: 'manifest', detail: 'The design-system manifest must record a selected concept, palette, and typography.' });
  if (manifest?.palette && tokenSheet) {
    for (const [key, value] of Object.entries(manifest.palette)) if (typeof value === 'string' && !tokenSheet.includes(value)) findings.push({ area: 'tokens', detail: `Palette value for ${key} is not represented in design-tokens.css.` });
  }
  const htmlFiles = await findFiles(workspace, (file) => file.endsWith('.html'));
  for (const file of htmlFiles) {
    const html = await fs.readFile(file, 'utf8');
    const relative = path.relative(workspace, file).replaceAll('\\', '/');
    if (!/href=["']design-tokens\.css["']/.test(html)) findings.push({ area: 'pages', file: relative, detail: 'Page does not load design-tokens.css.' });
    if (!/:focus-visible/.test(tokenSheet) && !/:focus-visible/.test(html)) findings.push({ area: 'focus', file: relative, detail: 'No visible keyboard-focus styling was detected.' });
    for (const image of html.matchAll(/<img\b[^>]*>/gi)) if (!/\balt\s*=/.test(image[0])) findings.push({ area: 'images', file: relative, detail: 'An image is missing an alt attribute.' });
  }
  if (tokenSheet && !/@media\s*\(/.test(tokenSheet)) {
    const cssFiles = await findFiles(workspace, (file) => file.endsWith('.css') && path.basename(file) !== 'design-tokens.css');
    const hasResponsiveRule = (await Promise.all(cssFiles.map((file) => fs.readFile(file, 'utf8')))).some((css) => /@media\s*\(/.test(css));
    if (!hasResponsiveRule) findings.push({ area: 'responsive', detail: 'No responsive media query was detected.' });
  }
  const status = findings.length ? 'failed' : 'passed';
  await onEvent('gate', `visual: ${status}`, { count: findings.length });
  return { id: 'visual', status, findings };
}

async function scopeGate(workspace, onEvent) {
  const result = await runProcess('git', ['status', '--porcelain'], { cwd: workspace });
  const changed = result.ok ? result.stdout.split('\n').filter(Boolean).map((line) => line.slice(3)) : [];
  const forbidden = changed.filter((item) => /(^|\/)(\.env|node_modules|\.git)(\/|$)/.test(item));
  const status = result.ok && !forbidden.length ? 'passed' : 'failed';
  await onEvent('gate', `scope: ${status}`, { changed, forbidden });
  return { id: 'scope', status, changed, forbidden, result: compactResult(result) };
}

async function inspectHtmlLinks(workspace) {
  const htmlFiles = await findFiles(workspace, (file) => file.endsWith('.html'));
  const findings = [];
  for (const file of htmlFiles) {
    const html = await fs.readFile(file, 'utf8');
    const matches = [...html.matchAll(/(?:href|src)=["']([^"']+)["']/gi)];
    for (const match of matches) {
      const target = match[1];
      if (!target || target.startsWith('#') || /^(https?:|mailto:|tel:|data:|javascript:)/i.test(target)) continue;
      // Link targets come from LLM-generated HTML. Resolve relative to the file's
      // directory but confine to the workspace root; a target that escapes the
      // workspace is reported as a broken/forbidden link rather than probed on disk.
      let resolved;
      try { resolved = resolveWithin(workspace, path.relative(workspace, path.resolve(path.dirname(file), target.split(/[?#]/)[0]))); }
      catch { findings.push({ file: path.relative(workspace, file).replaceAll('\\', '/'), target }); continue; }
      try { await fs.access(resolved); }
      catch { findings.push({ file: path.relative(workspace, file).replaceAll('\\', '/'), target }); }
    }
  }
  return findings;
}

async function findFiles(root, predicate, results = []) {
  const entries = await fs.readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    if (['.git', 'node_modules'].includes(entry.name)) continue;
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) await findFiles(absolute, predicate, results);
    else if (entry.isFile() && predicate(absolute)) results.push(absolute);
  }
  return results;
}

async function tryReadJson(file) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); } catch { return null; }
}

function compactResult(result) {
  return { code: result.code, timedOut: result.timedOut, stdout: truncate(result.stdout, 4000), stderr: truncate(result.stderr, 4000) };
}
