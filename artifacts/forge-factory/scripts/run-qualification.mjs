import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { ForgeStore } from '../src/store.mjs';
import { packageRoot } from '../src/config.mjs';
import { runQualityGates } from '../src/quality-gates.mjs';

const root = await mkdtemp(path.join(os.tmpdir(), 'forge-qualification-'));
const policy = { release: { requiredPasses: ['lint', 'unit', 'build', 'visual', 'links', 'scope'], requireBrowserEvidence: true, requireSecurityEvidence: true, requireManualApproval: true } };
try {
  const store = new ForgeStore({ dataRoot: root, templateRoot: path.join(packageRoot, 'templates', 'static-site') });
  const project = await store.createProject({ name: 'Forge Qualification Site', brief: 'A local controlled website fixture for A-01 qualification.', profile: 'marketing' });
  const job = await store.createJob(project.id, { type: 'PLAN' });
  const report = await runQualityGates({ workspace: store.repositoryDirectory(project.id), jobDirectory: store.jobDirectory(project.id, job.id), policy, onEvent: async () => {} });
  console.log(JSON.stringify({ project: project.id, summary: report.summary, gates: report.gates.map(({ id, status }) => ({ id, status })) }, null, 2));
  if (report.summary.status === 'blocked') process.exitCode = 1;
} finally { await rm(root, { recursive: true, force: true }); }
