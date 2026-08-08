import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { FactoryService } from '../src/factory-service.mjs';
import { packageRoot } from '../src/config.mjs';
import { loadPolicy } from '../src/policy.mjs';
import { ForgeStore } from '../src/store.mjs';

test('complete Plan → visual selection → approval → Build lifecycle uses the local Lemonade provider boundary', async () => {
  const lemonade = await mockLemonade();
  const root = await mkdtemp(path.join(os.tmpdir(), 'forge-factory-'));
  try {
    const config = {
      host: '127.0.0.1', port: 4310, dataRoot: root, templateRoot: path.join(packageRoot, 'templates', 'static-site'),
      lemonade: { baseUrl: `${lemonade.url}/api/v1`, model: 'AUTO-DETECT', timeoutMs: 10000, apiKey: 'lemonade' },
      agent: { maxToolRounds: 4, maxRepairRounds: 3, maxCommandOutputChars: 4000 }, preview: { enabled: true }
    };
    const store = new ForgeStore(config);
    const factory = new FactoryService({ config, store, policy: await loadPolicy(packageRoot) });
    const project = await factory.createProject({ name: 'Lifecycle Test', brief: 'Test the local plan and website build lifecycle.', profile: 'marketing' });
    const planJob = await factory.startJob(project.id, { type: 'PLAN' });
    await waitFor(async () => (await store.getJob(project.id, planJob.id)).status === 'awaiting_approval');
    await factory.approveJob(project.id, planJob.id);
    const designJob = await factory.startJob(project.id, { type: 'DESIGN', planJobId: planJob.id });
    await waitFor(async () => (await store.getJob(project.id, designJob.id)).status === 'awaiting_approval');
    await assert.rejects(() => factory.approveJob(project.id, designJob.id), /Select one visual direction/);
    const design = await store.readArtifact(project.id, designJob.id, 'visual-design.json');
    assert.equal(design.concepts.length, 3);
    await factory.selectDesign(project.id, designJob.id, design.concepts[1].id);
    await factory.approveJob(project.id, designJob.id);
    const buildJob = await factory.startJob(project.id, { type: 'BUILD', planJobId: planJob.id, designJobId: designJob.id });
    await waitFor(async () => ['passed', 'conditional', 'blocked', 'failed'].includes((await store.getJob(project.id, buildJob.id)).status));
    const completed = await store.getJob(project.id, buildJob.id);
    assert.equal(completed.status, 'conditional');
    assert.ok(completed.worktree);
    assert.ok((completed.artifacts || []).includes('builder-summary.json'));
    assert.ok((await store.getEvents(project.id, buildJob.id)).some((event) => event.phase === 'quality_complete'));
    const manifest = JSON.parse(await readFile(path.join(completed.worktree, 'design-system.json'), 'utf8'));
    assert.equal(manifest.selectedConceptId, design.concepts[1].id);
  } finally {
    await lemonade.close();
    await rm(root, { recursive: true, force: true });
  }
});

async function mockLemonade() {
  const server = http.createServer(async (request, response) => {
    if (request.url === '/api/v1/models') return json(response, { data: [{ id: 'mock-local-coder' }] });
    if (request.url === '/api/v1/chat/completions') {
      let body = ''; for await (const chunk of request) body += chunk;
      const parsed = JSON.parse(body);
      const system = parsed.messages?.[0]?.content || '';
      const content = system.includes('planning agent')
        ? JSON.stringify({ siteName: 'Lifecycle Test', pages: [{ slug: '/', title: 'Home' }], acceptanceTests: ['Home loads'], goal: 'Test', audience: 'Test', visualDirection: 'Clear', components: [], contentNeeds: [], responsiveRules: [], accessibilityRules: [], seoRules: [], risks: [] })
        : system.includes('visual design authority')
          ? JSON.stringify({ concepts: [{ id: 'editorial', name: 'Editorial', summary: 'Calm', rationale: 'Trust', palette: {}, typography: {}, layout: {}, components: [], motion: {} }, { id: 'technical', name: 'Technical', summary: 'Precise', rationale: 'Clarity', palette: {}, typography: {}, layout: {}, components: [], motion: {} }, { id: 'warm', name: 'Warm', summary: 'Approachable', rationale: 'Connection', palette: {}, typography: {}, layout: {}, components: [], motion: {} }], principles: ['Clear'], responsiveRules: ['Mobile'], accessibility: { target: 'AA', requirements: ['Focus'] }, visualAcceptanceTests: ['Screenshot'] })
          : 'Builder finished without changes because the governed starter already satisfies the approved test plan.';
      return json(response, { choices: [{ message: { role: 'assistant', content } }], usage: { total_tokens: 1 } });
    }
    response.writeHead(404); response.end();
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  return { url: `http://127.0.0.1:${port}`, close: () => new Promise((resolve) => server.close(resolve)) };
}

function json(response, value) { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)); }
async function waitFor(predicate, limit = 50) { for (let i = 0; i < limit; i += 1) { if (await predicate()) return; await new Promise((resolve) => setTimeout(resolve, 50)); } throw new Error('Timed out waiting for asynchronous factory job.'); }
