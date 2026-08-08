import { promises as fs } from 'node:fs';
import path from 'node:path';
import { createPlan, createVisualDesign, runBuilder, runReviewer } from './agent-runner.mjs';
import { runQualityGates, releaseDecision } from './quality-gates.mjs';
import { runProcess } from './process.mjs';
import { errorRecord, nowIso, readJson, sha256, writeJsonAtomic } from './utils.mjs';
import { cssTokenSheet, designSystemManifest, getSelectedConcept } from './visual-design.mjs';

export class FactoryService {
  constructor({ config, store, policy }) {
    this.config = config;
    this.store = store;
    this.policy = policy;
    this.running = new Set();
  }

  async health() {
    let lemonade = { ok: false, message: 'Not checked.' };
    try {
      const response = await fetch(`${this.config.lemonade.baseUrl}/models`, { headers: { authorization: `Bearer ${this.config.lemonade.apiKey}` } });
      const body = await response.json();
      lemonade = { ok: response.ok, modelCount: (body.data || body.models || []).length };
    } catch (error) { lemonade = { ok: false, message: error.message }; }
    return { status: 'ok', time: nowIso(), lemonade, loopbackOnly: true };
  }

  async createProject(input) {
    return this.store.createProject(input);
  }

  async startJob(projectId, input) {
    const project = await this.store.getProject(projectId);
    const type = String(input.type || '').toUpperCase();
    if (!['PLAN', 'DESIGN', 'BUILD', 'REPAIR', 'VERIFY', 'REVIEW', 'RELEASE'].includes(type)) throw new Error('Unsupported job type.');
    const job = await this.store.createJob(projectId, { type, targetJobId: input.targetJobId || null, planJobId: input.planJobId || null, designJobId: input.designJobId || null, instruction: input.instruction || '' });
    queueMicrotask(() => this.runJob(project, job).catch(() => {}));
    return job;
  }

  async approveJob(projectId, jobId) {
    const job = await this.store.getJob(projectId, jobId);
    if (!['PLAN', 'DESIGN'].includes(job.type) || job.status !== 'awaiting_approval') throw new Error('Only a completed plan or visual design awaiting approval can be approved.');
    if (job.type === 'DESIGN') {
      const design = await this.store.readArtifact(projectId, jobId, 'visual-design.json');
      const selected = getSelectedConcept(design);
      if (!selected) throw new Error('Select one visual direction before approving the visual design.');
    }
    await this.store.updateJob(projectId, jobId, { status: 'passed', approvedAt: nowIso() });
    await this.store.appendEvent(projectId, jobId, 'approval', `${job.type === 'PLAN' ? 'Plan' : 'Visual design'} approved for build work.`);
    return this.store.getJob(projectId, jobId);
  }

  async approvePlan(projectId, jobId) { return this.approveJob(projectId, jobId); }

  async selectDesign(projectId, jobId, conceptId) {
    const job = await this.store.getJob(projectId, jobId);
    if (job.type !== 'DESIGN' || job.status !== 'awaiting_approval') throw new Error('Only a completed visual design awaiting approval can receive a selection.');
    const design = await this.store.readArtifact(projectId, jobId, 'visual-design.json');
    const selected = (design?.concepts || []).find((concept) => concept.id === conceptId);
    if (!selected) throw new Error('The selected visual direction is not in this design artifact.');
    const next = { ...design, selectedConceptId: selected.id, selectedAt: nowIso() };
    await this.store.saveArtifact(projectId, jobId, 'visual-design.json', next);
    await this.store.updateJob(projectId, jobId, { selectedConceptId: selected.id, selectedAt: next.selectedAt });
    await this.store.appendEvent(projectId, jobId, 'design_selected', `Selected visual direction: ${selected.name}.`, { conceptId: selected.id });
    return this.store.getJob(projectId, jobId);
  }

  async runJob(project, job) {
    if (this.running.has(job.id)) return;
    this.running.add(job.id);
    const event = (phase, message, data) => this.store.appendEvent(project.id, job.id, phase, message, data);
    try {
      await this.store.updateJob(project.id, job.id, { status: 'running', startedAt: nowIso() });
      await event('start', `${job.type} job started.`);
      if (job.type === 'PLAN') await this.runPlan(project, job, event);
      else if (job.type === 'DESIGN') await this.runDesign(project, job, event);
      else if (job.type === 'BUILD') await this.runBuild(project, job, event);
      else if (job.type === 'REPAIR') await this.runRepair(project, job, event);
      else if (job.type === 'VERIFY') await this.runVerify(project, job, event);
      else if (job.type === 'REVIEW') await this.runReview(project, job, event);
      else if (job.type === 'RELEASE') await this.runRelease(project, job, event);
    } catch (error) {
      await event('error', error.message, errorRecord(error));
      await this.store.updateJob(project.id, job.id, { status: 'failed', completedAt: nowIso(), error: errorRecord(error) });
    } finally {
      this.running.delete(job.id);
    }
  }

  async runPlan(project, job, event) {
    const result = await createPlan({ lemonade: this.config.lemonade, project, instruction: job.instruction, onEvent: event });
    await this.store.saveArtifact(project.id, job.id, 'site-plan.json', result.plan);
    await this.store.saveArtifact(project.id, job.id, 'site-plan-response.txt', result.raw || '');
    await this.store.updateProject(project.id, { latestPlanJobId: job.id });
    await this.store.updateJob(project.id, job.id, { status: 'awaiting_approval', completedAt: nowIso(), planSource: result.plan.source });
    await event('plan_ready', 'Read-only website plan is ready for approval.');
  }

  async runDesign(project, job, event) {
    const planJobId = job.planJobId || project.latestPlanJobId;
    if (!planJobId) throw new Error('An approved PLAN job is required before visual design work.');
    const planJob = await this.store.getJob(project.id, planJobId);
    if (planJob.status !== 'passed') throw new Error('The selected plan has not been approved.');
    const plan = await this.store.readArtifact(project.id, planJobId, 'site-plan.json');
    if (!plan) throw new Error('Approved plan artifact is missing.');
    const result = await createVisualDesign({ lemonade: this.config.lemonade, project, plan, instruction: job.instruction, onEvent: event });
    await this.store.saveArtifact(project.id, job.id, 'visual-design.json', result.design);
    await this.store.saveArtifact(project.id, job.id, 'visual-design-response.txt', result.raw || '');
    await this.store.updateProject(project.id, { latestDesignJobId: job.id });
    await this.store.updateJob(project.id, job.id, { status: 'awaiting_approval', completedAt: nowIso(), planJobId, designSource: result.design.source });
    await event('design_ready', 'Three visual directions are ready. Select one and approve it before build work.');
  }

  async runBuild(project, job, event) {
    const planJobId = job.planJobId || project.latestPlanJobId;
    if (!planJobId) throw new Error('A reviewed PLAN job is required before BUILD.');
    const planJob = await this.store.getJob(project.id, planJobId);
    if (planJob.status !== 'passed') throw new Error('The selected plan has not been approved.');
    const plan = await this.store.readArtifact(project.id, planJobId, 'site-plan.json');
    if (!plan) throw new Error('Approved plan artifact is missing.');
    const designJobId = job.designJobId || project.latestDesignJobId;
    if (!designJobId) throw new Error('An approved visual design is required before BUILD.');
    const designJob = await this.store.getJob(project.id, designJobId);
    if (designJob.status !== 'passed') throw new Error('The selected visual design has not been approved.');
    if (designJob.planJobId !== planJobId) throw new Error('The selected visual design belongs to a different plan; create and approve a design for this plan.');
    const visualDesign = await this.store.readArtifact(project.id, designJobId, 'visual-design.json');
    if (!getSelectedConcept(visualDesign)) throw new Error('The approved visual design has no selected direction.');
    const worktree = await this.store.createWorktree(project.id, job.id);
    await event('worktree', 'Created isolated Git worktree.', { worktree });
    await this.applyVisualSystem(worktree, visualDesign, event);
    const builder = await runBuilder({
      lemonade: this.config.lemonade, project, plan, visualDesign, workspace: worktree, policy: this.policy, instruction: job.instruction,
      onEvent: event, maxToolRounds: this.config.agent.maxToolRounds, maxOutputChars: this.config.agent.maxCommandOutputChars
    });
    await this.store.saveArtifact(project.id, job.id, 'builder-summary.json', builder);
    const gates = await runQualityGates({ workspace: worktree, jobDirectory: this.store.jobDirectory(project.id, job.id), policy: this.policy, onEvent: event, previewUrl: this.previewUrl(project.id, job.id) });
    const checkpoint = await this.store.commitWorktree(project.id, job.id, `Forge ${job.type.toLowerCase()} ${job.id}`);
    await this.store.saveArtifact(project.id, job.id, 'checkpoint.json', checkpoint);
    await this.store.updateProject(project.id, { latestBuildJobId: job.id });
    const status = gates.summary.status === 'blocked' ? 'blocked' : gates.summary.status === 'conditional' ? 'conditional' : 'passed';
    await this.store.updateJob(project.id, job.id, { status, completedAt: nowIso(), qualityStatus: gates.summary.status, checkpoint, planJobId, designJobId });
    await event('build_complete', `Build finished with ${status} quality status.`, { checkpoint });
  }

  // REPAIR works on the existing build worktree rather than creating a new one.
  // It uses maxRepairRounds (shorter than maxToolRounds) to keep repair focused.
  async runRepair(project, job, event) {
    const planJobId = job.planJobId || project.latestPlanJobId;
    const designJobId = job.designJobId || project.latestDesignJobId;
    const { target, worktree } = await this.resolveTargetWorktree(project, job);
    if (!target.planJobId && !planJobId) throw new Error('Cannot resolve the plan that this build belongs to.');
    const resolvedPlanId = target.planJobId || planJobId;
    const resolvedDesignId = target.designJobId || designJobId;
    const plan = await this.store.readArtifact(project.id, resolvedPlanId, 'site-plan.json', {});
    const visualDesign = resolvedDesignId ? await this.store.readArtifact(project.id, resolvedDesignId, 'visual-design.json', {}) : {};
    await event('worktree', `Reusing existing worktree from ${target.id} for repair.`, { worktree });
    const maxToolRounds = this.config.agent.maxRepairRounds ?? 12;
    const builder = await runBuilder({
      lemonade: this.config.lemonade, project, plan, visualDesign, workspace: worktree, policy: this.policy,
      instruction: job.instruction || 'Address quality-gate failures and reviewer findings from the previous build. Do not add unrelated features.',
      onEvent: event, maxToolRounds, maxOutputChars: this.config.agent.maxCommandOutputChars,
    });
    await this.store.saveArtifact(project.id, job.id, 'builder-summary.json', builder);
    const gates = await runQualityGates({ workspace: worktree, jobDirectory: this.store.jobDirectory(project.id, job.id), policy: this.policy, onEvent: event, previewUrl: this.previewUrl(project.id, target.id) });
    const checkpoint = await this.store.commitWorktree(project.id, target.id, `Forge repair ${job.id}`);
    await this.store.saveArtifact(project.id, job.id, 'checkpoint.json', checkpoint);
    const status = gates.summary.status === 'blocked' ? 'blocked' : gates.summary.status === 'conditional' ? 'conditional' : 'passed';
    await this.store.updateJob(project.id, job.id, { status, completedAt: nowIso(), qualityStatus: gates.summary.status, checkpoint, planJobId: resolvedPlanId, designJobId: resolvedDesignId, repairedTargetId: target.id });
    await event('repair_complete', `Repair finished with ${status} quality status.`, { checkpoint });
  }

  async resolveTargetWorktree(project, job) {
    const targetJobId = job.targetJobId || project.latestBuildJobId;
    if (!targetJobId) throw new Error('A target build job is required.');
    const target = await this.store.getJob(project.id, targetJobId);
    if (!target.worktree) throw new Error('Target job does not have a worktree.');
    return { target, worktree: target.worktree };
  }

  async runVerify(project, job, event) {
    const { target, worktree } = await this.resolveTargetWorktree(project, job);
    const gates = await runQualityGates({ workspace: worktree, jobDirectory: this.store.jobDirectory(project.id, job.id), policy: this.policy, onEvent: event, previewUrl: this.previewUrl(project.id, target.id) });
    await this.store.saveArtifact(project.id, job.id, 'verified-target.json', { targetJobId: target.id, worktree });
    await this.store.updateJob(project.id, job.id, { status: gates.summary.status === 'blocked' ? 'blocked' : gates.summary.status === 'conditional' ? 'conditional' : 'passed', completedAt: nowIso(), qualityStatus: gates.summary.status });
  }

  async runReview(project, job, event) {
    const { target, worktree } = await this.resolveTargetWorktree(project, job);
    const plan = await this.store.readArtifact(project.id, target.planJobId || project.latestPlanJobId, 'site-plan.json', {});
    const visualDesign = target.designJobId ? await this.store.readArtifact(project.id, target.designJobId, 'visual-design.json', {}) : {};
    const gateReport = await readJson(path.join(this.store.jobDirectory(project.id, target.id), 'quality', 'quality-report.json'), { gates: [] });
    const diff = await runProcess('git', ['diff', 'HEAD~1..HEAD', '--', '.'], { cwd: worktree, maxOutputChars: 18000 });
    const review = await runReviewer({ lemonade: this.config.lemonade, project, plan, visualDesign, diff: diff.stdout || diff.stderr, gates: gateReport, onEvent: event });
    await this.store.saveArtifact(project.id, job.id, 'review.json', review);
    await this.store.updateJob(project.id, job.id, { status: review.verdict === 'block' ? 'blocked' : review.verdict === 'pass' ? 'passed' : 'conditional', completedAt: nowIso(), reviewedTarget: target.id });
  }

  async runRelease(project, job, event) {
    const { target } = await this.resolveTargetWorktree(project, job);
    const gateReport = await readJson(path.join(this.store.jobDirectory(project.id, target.id), 'quality', 'quality-report.json'), { gates: [] });
    const relatedJobs = await this.store.listJobs(project.id);
    const reviewJob = relatedJobs.find((candidate) => candidate.type === 'REVIEW' && candidate.targetJobId === target.id && ['passed', 'conditional', 'blocked'].includes(candidate.status));
    const reviewer = reviewJob ? await this.store.readArtifact(project.id, reviewJob.id, 'review.json', null) : null;
    const decision = releaseDecision({ jobId: target.id, gates: gateReport, reviewer, policy: this.policy });
    const manifest = await this.createEvidenceManifest(project.id, target.id, decision);
    await this.store.saveArtifact(project.id, job.id, 'release-decision.json', decision);
    await this.store.saveArtifact(project.id, job.id, 'evidence-manifest.json', manifest);
    const status = decision.status === 'BLOCKED' ? 'blocked' : decision.status === 'VERIFIED' ? 'passed' : 'conditional';
    await this.store.updateJob(project.id, job.id, { status, completedAt: nowIso(), releaseDecision: decision.status, targetJobId: target.id });
    await event('release', `Release decision: ${decision.status}.`, decision);
  }

  async createEvidenceManifest(projectId, targetJobId, decision) {
    const root = this.store.jobDirectory(projectId, targetJobId);
    const files = await listFiles(root);
    const entries = [];
    for (const file of files) {
      const body = await fs.readFile(file);
      entries.push({ path: path.relative(root, file).replaceAll('\\', '/'), sha256: sha256(body), bytes: body.byteLength });
    }
    return { generatedAt: nowIso(), targetJobId, releaseDecision: decision.status, files: entries.sort((a, b) => a.path.localeCompare(b.path)) };
  }

  async applyVisualSystem(worktree, visualDesign, event) {
    const selected = getSelectedConcept(visualDesign);
    await fs.writeFile(path.join(worktree, 'design-tokens.css'), cssTokenSheet(visualDesign), 'utf8');
    await writeJsonAtomic(path.join(worktree, 'design-system.json'), designSystemManifest(visualDesign));
    await event('design_tokens', `Applied approved visual direction: ${selected.name}.`, { conceptId: selected.id });
  }

  previewUrl(projectId, jobId) {
    return `http://${this.config.host}:${this.config.port}/preview/${encodeURIComponent(projectId)}/${encodeURIComponent(jobId)}/`;
  }
}

async function listFiles(root, output = []) {
  const entries = await fs.readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) await listFiles(absolute, output);
    else if (entry.isFile() && entry.name !== 'evidence-manifest.json') output.push(absolute);
  }
  return output;
}
