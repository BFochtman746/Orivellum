import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { ForgeStore } from '../src/store.mjs';
import { runQualityGates } from '../src/quality-gates.mjs';
import { packageRoot } from '../src/config.mjs';

const policy = {
  release: { requiredPasses: ['lint', 'unit', 'build', 'visual', 'links', 'scope'], requireBrowserEvidence: true, requireSecurityEvidence: true, requireManualApproval: true }
};

test('new website projects are initialized with an owned Git starter', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'forge-store-'));
  try {
    const store = new ForgeStore({ dataRoot: root, templateRoot: path.join(packageRoot, 'templates', 'static-site') });
    const project = await store.createProject({ name: 'North Star Advisory', brief: 'Trusted operations advice for practical leaders.', profile: 'business' });
    const html = await readFile(path.join(store.repositoryDirectory(project.id), 'index.html'), 'utf8');
    assert.match(html, /North Star Advisory/);
    assert.doesNotMatch(html, /\{\{SITE_NAME\}\}/);
    const job = await store.createJob(project.id, { type: 'PLAN' });
    assert.equal((await store.getJob(project.id, job.id)).status, 'queued');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('built-in starter passes deterministic local gates before optional tools are installed', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'forge-gates-'));
  try {
    const store = new ForgeStore({ dataRoot: root, templateRoot: path.join(packageRoot, 'templates', 'static-site') });
    const project = await store.createProject({ name: 'Test Site', brief: 'A focused, trustworthy local test website.', profile: 'marketing' });
    const job = await store.createJob(project.id, { type: 'PLAN' });
    const report = await runQualityGates({ workspace: store.repositoryDirectory(project.id), jobDirectory: store.jobDirectory(project.id, job.id), policy, onEvent: async () => {} });
    for (const id of ['lint', 'unit', 'build', 'visual', 'links', 'scope']) assert.equal(report.gates.find((gate) => gate.id === id).status, 'passed', id);
    assert.equal(report.summary.status, 'conditional');
  } finally { await rm(root, { recursive: true, force: true }); }
});
