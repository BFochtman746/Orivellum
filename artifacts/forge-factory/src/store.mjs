import { promises as fs } from 'node:fs';
import path from 'node:path';
import { appendJsonLine, createId, ensureDir, nowIso, readJson, safeName, writeJsonAtomic } from './utils.mjs';
import { runProcess } from './process.mjs';

export class ForgeStore {
  constructor(config) {
    this.config = config;
    this.projectsRoot = path.join(config.dataRoot, 'projects');
  }

  async init() {
    await ensureDir(this.projectsRoot);
  }

  projectDirectory(projectId) { return path.join(this.projectsRoot, projectId); }
  projectFile(projectId) { return path.join(this.projectDirectory(projectId), 'project.json'); }
  repositoryDirectory(projectId) { return path.join(this.projectDirectory(projectId), 'repository'); }
  jobsDirectory(projectId) { return path.join(this.projectDirectory(projectId), 'jobs'); }
  jobDirectory(projectId, jobId) { return path.join(this.jobsDirectory(projectId), jobId); }
  jobFile(projectId, jobId) { return path.join(this.jobDirectory(projectId, jobId), 'job.json'); }
  eventFile(projectId, jobId) { return path.join(this.jobDirectory(projectId, jobId), 'work-ledger.ndjson'); }
  worktreeDirectory(projectId, jobId) { return path.join(this.projectDirectory(projectId), 'worktrees', jobId); }

  async listProjects() {
    await this.init();
    const entries = await fs.readdir(this.projectsRoot, { withFileTypes: true });
    const projects = await Promise.all(entries.filter((entry) => entry.isDirectory()).map(async (entry) => readJson(this.projectFile(entry.name), null)));
    return projects.filter(Boolean).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  async createProject({ name, brief, profile = 'marketing', visualBrief = '' }) {
    if (!String(name || '').trim() || !String(brief || '').trim()) throw new Error('Project name and brief are required.');
    const id = createId('WEB');
    const project = {
      id,
      name: safeName(name),
      brief: String(brief).trim(),
      profile,
      visualBrief: String(visualBrief || '').trim().slice(0, 6000),
      status: 'active',
      createdAt: nowIso(),
      updatedAt: nowIso(),
      latestPlanJobId: null,
      latestDesignJobId: null,
      latestBuildJobId: null
    };
    const root = this.projectDirectory(id);
    await ensureDir(root);
    await ensureDir(this.jobsDirectory(id));
    await ensureDir(path.join(root, 'worktrees'));
    await writeJsonAtomic(this.projectFile(id), project);
    await this.initializeRepository(project);
    return project;
  }

  async initializeRepository(project) {
    const repository = this.repositoryDirectory(project.id);
    await fs.cp(this.config.templateRoot, repository, { recursive: true, force: false });
    const configFile = path.join(repository, 'site.config.json');
    const siteConfig = await readJson(configFile);
    siteConfig.siteName = project.name;
    siteConfig.tagline = deriveTagline(project.brief);
    siteConfig.description = project.brief.slice(0, 155);
    siteConfig.profile = project.profile;
    await writeJsonAtomic(configFile, siteConfig);
    const tokens = {
      SITE_NAME: escapeHtml(project.name),
      TAGLINE: escapeHtml(siteConfig.tagline),
      DESCRIPTION: escapeHtml(siteConfig.description)
    };
    await Promise.all(['index.html', 'about.html', 'contact.html'].map((name) => replaceTokens(path.join(repository, name), tokens)));
    for (const args of [['init'], ['config', 'user.email', 'forge@localhost'], ['config', 'user.name', 'Orivellum Forge'], ['add', '.'], ['commit', '-m', 'Initialize governed website project']]) {
      const result = await runProcess('git', args, { cwd: repository });
      if (!result.ok) throw new Error(`Git setup failed: ${result.stderr || result.stdout}`);
    }
  }

  async getProject(projectId) {
    const project = await readJson(this.projectFile(projectId), null);
    if (!project) throw new Error(`Unknown project: ${projectId}`);
    return project;
  }

  async updateProject(projectId, patch) {
    const project = await this.getProject(projectId);
    const next = { ...project, ...patch, updatedAt: nowIso() };
    await writeJsonAtomic(this.projectFile(projectId), next);
    return next;
  }

  async createJob(projectId, { type, targetJobId = null, instruction = '', planJobId = null, designJobId = null }) {
    const job = {
      id: createId('JOB'), projectId, type, targetJobId, planJobId, designJobId, instruction: String(instruction || ''),
      status: 'queued', createdAt: nowIso(), updatedAt: nowIso(), worktree: null, events: 0, artifacts: []
    };
    await ensureDir(this.jobDirectory(projectId, job.id));
    await writeJsonAtomic(this.jobFile(projectId, job.id), job);
    await this.appendEvent(projectId, job.id, 'queued', `${type} job created.`);
    return job;
  }

  async getJob(projectId, jobId) {
    const job = await readJson(this.jobFile(projectId, jobId), null);
    if (!job) throw new Error(`Unknown job: ${jobId}`);
    return job;
  }

  async listJobs(projectId) {
    try {
      const entries = await fs.readdir(this.jobsDirectory(projectId), { withFileTypes: true });
      const jobs = await Promise.all(entries.filter((entry) => entry.isDirectory()).map((entry) => readJson(this.jobFile(projectId, entry.name), null)));
      return jobs.filter(Boolean).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    } catch (error) {
      if (error.code === 'ENOENT') return [];
      throw error;
    }
  }

  async updateJob(projectId, jobId, patch) {
    const job = await this.getJob(projectId, jobId);
    const next = { ...job, ...patch, updatedAt: nowIso() };
    await writeJsonAtomic(this.jobFile(projectId, jobId), next);
    return next;
  }

  async appendEvent(projectId, jobId, phase, message, data = undefined) {
    const event = { at: nowIso(), phase, message, ...(data === undefined ? {} : { data }) };
    await appendJsonLine(this.eventFile(projectId, jobId), event);
    // Single read+write: avoid the redundant getJob() call that updateJob() would
    // make internally — events are appended on every tool call during a build, so
    // this is a hot path where the extra file I/O adds up noticeably.
    const job = await this.getJob(projectId, jobId);
    const next = { ...job, events: (job.events || 0) + 1, updatedAt: nowIso() };
    await writeJsonAtomic(this.jobFile(projectId, jobId), next);
    return event;
  }

  async getEvents(projectId, jobId) {
    try {
      const raw = await fs.readFile(this.eventFile(projectId, jobId), 'utf8');
      return raw.split('\n').filter(Boolean).map((line) => JSON.parse(line));
    } catch (error) {
      if (error.code === 'ENOENT') return [];
      throw error;
    }
  }

  async saveArtifact(projectId, jobId, name, value) {
    const fileName = safeName(name, 90);
    const file = path.join(this.jobDirectory(projectId, jobId), fileName);
    if (typeof value === 'string') await fs.writeFile(file, value, 'utf8');
    else await writeJsonAtomic(file, value);
    const job = await this.getJob(projectId, jobId);
    const artifacts = [...new Set([...(job.artifacts || []), fileName])];
    await this.updateJob(projectId, jobId, { artifacts });
    return file;
  }

  async readArtifact(projectId, jobId, name, fallback = null) {
    const file = path.join(this.jobDirectory(projectId, jobId), safeName(name, 90));
    return readJson(file, fallback);
  }

  async createWorktree(projectId, jobId) {
    const repository = this.repositoryDirectory(projectId);
    const worktree = this.worktreeDirectory(projectId, jobId);
    const result = await runProcess('git', ['worktree', 'add', '-b', `forge/${jobId.toLowerCase()}`, worktree, 'HEAD'], { cwd: repository });
    if (!result.ok) throw new Error(`Unable to create isolated worktree: ${result.stderr || result.stdout}`);
    await this.updateJob(projectId, jobId, { worktree });
    return worktree;
  }

  async commitWorktree(projectId, jobId, message) {
    const job = await this.getJob(projectId, jobId);
    if (!job.worktree) throw new Error('No job worktree exists.');
    const status = await runProcess('git', ['status', '--porcelain'], { cwd: job.worktree });
    if (!status.ok) throw new Error(status.stderr || 'Unable to read worktree status.');
    if (!status.stdout.trim()) return { committed: false, reason: 'no_changes' };
    const add = await runProcess('git', ['add', '.'], { cwd: job.worktree });
    const commit = add.ok ? await runProcess('git', ['commit', '-m', message], { cwd: job.worktree }) : add;
    if (!commit.ok) throw new Error(`Checkpoint commit failed: ${commit.stderr || commit.stdout}`);
    const hash = await runProcess('git', ['rev-parse', 'HEAD'], { cwd: job.worktree });
    return { committed: true, commit: hash.stdout.trim() };
  }
}

function deriveTagline(brief) {
  const sentence = String(brief).split(/[.!?]/).find((part) => part.trim())?.trim() || 'Built with purpose and clarity.';
  return sentence.slice(0, 100);
}

async function replaceTokens(file, tokens) {
  let content = await fs.readFile(file, 'utf8');
  for (const [key, value] of Object.entries(tokens)) content = content.replaceAll(`{{${key}}}`, value);
  await fs.writeFile(file, content, 'utf8');
}

function escapeHtml(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}
