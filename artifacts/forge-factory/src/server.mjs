import { createReadStream, promises as fs } from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { loadConfig, packageRoot } from './config.mjs';
import { FactoryService } from './factory-service.mjs';
import { loadPolicy } from './policy.mjs';
import { ForgeStore } from './store.mjs';
import { errorRecord, resolveWithin } from './utils.mjs';

const contentTypes = {
  '.css': 'text/css; charset=utf-8', '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon'
};

const config = await loadConfig();
const store = new ForgeStore(config);
await store.init();
const policy = await loadPolicy(packageRoot);
const factory = new FactoryService({ config, store, policy });
const publicRoot = path.join(packageRoot, 'public');

const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, `http://${request.headers.host || '127.0.0.1'}`);
    if (url.pathname.startsWith('/api/')) return await api(request, response, url);
    if (url.pathname.startsWith('/preview/')) return await preview(request, response, url);
    return await staticFile(request, response, url.pathname);
  } catch (error) {
    sendJson(response, 500, { error: error.message, detail: errorRecord(error) });
  }
});

server.listen(config.port, config.host, () => {
  console.log(`Orivellum Forge Website Factory listening on http://${config.host}:${config.port}`);
});

async function api(request, response, url) {
  const segments = url.pathname.split('/').filter(Boolean);
  if (request.method === 'GET' && url.pathname === '/api/health') return sendJson(response, 200, await factory.health());
  if (request.method === 'GET' && url.pathname === '/api/projects') return sendJson(response, 200, await store.listProjects());
  if (request.method === 'POST' && url.pathname === '/api/projects') return sendJson(response, 201, await factory.createProject(await body(request)));
  if (segments[1] === 'projects' && segments[2]) {
    const projectId = segments[2];
    if (request.method === 'GET' && segments.length === 3) return sendJson(response, 200, await projectDetail(projectId));
    if (request.method === 'GET' && segments[3] === 'jobs' && segments.length === 4) return sendJson(response, 200, await store.listJobs(projectId));
    if (request.method === 'POST' && segments[3] === 'jobs' && segments.length === 4) return sendJson(response, 202, await factory.startJob(projectId, await body(request)));
    if (segments[3] === 'jobs' && segments[4]) {
      const jobId = segments[4];
      if (request.method === 'GET' && segments.length === 5) return sendJson(response, 200, await jobDetail(projectId, jobId));
      if (request.method === 'GET' && segments[5] === 'events') return sendJson(response, 200, await store.getEvents(projectId, jobId));
      if (request.method === 'POST' && segments[5] === 'approve') return sendJson(response, 200, await factory.approveJob(projectId, jobId));
      if (request.method === 'POST' && segments[5] === 'select-design') {
        const input = await body(request);
        return sendJson(response, 200, await factory.selectDesign(projectId, jobId, String(input.conceptId || '')));
      }
      if (request.method === 'GET' && segments[5] === 'artifacts' && segments[6]) return await artifact(response, projectId, jobId, segments.slice(6).join('/'));
    }
  }
  sendJson(response, 404, { error: 'Unknown API route.' });
}

async function projectDetail(projectId) {
  const project = await store.getProject(projectId);
  const jobs = await store.listJobs(projectId);
  return { project, jobs };
}

async function jobDetail(projectId, jobId) {
  const job = await store.getJob(projectId, jobId);
  const events = await store.getEvents(projectId, jobId);
  return { job, events };
}

async function artifact(response, projectId, jobId, name) {
  const safe = path.basename(name);
  const file = path.join(store.jobDirectory(projectId, jobId), safe);
  try {
    const stat = await fs.stat(file);
    if (!stat.isFile()) throw new Error('Not a file.');
    response.writeHead(200, secureHeaders({ 'content-type': contentTypes[path.extname(file)] || 'application/octet-stream', 'content-length': stat.size, 'cache-control': 'no-store' }));
    createReadStream(file).pipe(response);
  } catch { sendJson(response, 404, { error: 'Artifact not found.' }); }
}

async function preview(request, response, url) {
  const segments = url.pathname.split('/').filter(Boolean);
  const [, projectId, jobId, ...requested] = segments;
  if (!projectId || !jobId) return sendJson(response, 404, { error: 'Preview project and job are required.' });
  const job = await store.getJob(projectId, jobId);
  if (!job.worktree) return sendJson(response, 404, { error: 'This job has no previewable worktree.' });
  const relative = requested.join('/') || 'index.html';
  if (relative.split('/').some((segment) => ['.git', '.env', 'node_modules', '.semgrep'].includes(segment))) return sendJson(response, 403, { error: 'Preview path is forbidden.' });
  const file = resolveWithin(job.worktree, relative);
  await serveFile(response, file, { cache: 'no-store' });
}

async function staticFile(request, response, pathname) {
  const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const file = resolveWithin(publicRoot, relative);
  await serveFile(response, file, { cache: 'no-store' });
}

async function serveFile(response, file, { cache }) {
  try {
    const stat = await fs.stat(file);
    if (!stat.isFile()) throw new Error('Not a file.');
    response.writeHead(200, secureHeaders({ 'content-type': contentTypes[path.extname(file).toLowerCase()] || 'application/octet-stream', 'content-length': stat.size, 'cache-control': cache }));
    createReadStream(file).pipe(response);
  } catch {
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Not found');
  }
}

async function body(request) {
  const chunks = [];
  let length = 0;
  for await (const chunk of request) {
    length += chunk.length;
    if (length > 1_000_000) throw new Error('Request body is too large.');
    chunks.push(chunk);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'); }
  catch { throw new Error('Request body must be valid JSON.'); }
}

function sendJson(response, status, value) {
  const payload = JSON.stringify(value);
  response.writeHead(status, secureHeaders({ 'content-type': 'application/json; charset=utf-8', 'content-length': Buffer.byteLength(payload), 'cache-control': 'no-store' }));
  response.end(payload);
}

function secureHeaders(headers) {
  return {
    ...headers,
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'cross-origin-opener-policy': 'same-origin',
    'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'"
  };
}

process.on('SIGINT', () => server.close(() => process.exit(0)));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
