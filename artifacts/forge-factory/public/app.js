const state = { projects: [], selected: null, timer: null };
const $ = (selector) => document.querySelector(selector);
const api = async (path, options = {}) => {
  const response = await fetch(path, { headers: { 'content-type': 'application/json', ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
};

async function loadHealth() {
  try { const health = await api('/api/health'); $('#health').textContent = health.lemonade.ok ? `Lemonade ready · ${health.lemonade.modelCount} model(s)` : `Lemonade unavailable`; $('#health-dot').className = `dot ${health.lemonade.ok ? 'good' : 'bad'}`; }
  catch { $('#health').textContent = 'Factory unavailable'; $('#health-dot').className = 'dot bad'; }
}

async function loadProjects(selectId = state.selected?.project?.id) {
  state.projects = await api('/api/projects');
  $('#project-count').textContent = `${state.projects.length} active project${state.projects.length === 1 ? '' : 's'}`;
  const container = $('#projects'); container.textContent = '';
  for (const project of state.projects) {
    const node = $('#project-template').content.firstElementChild.cloneNode(true);
    node.querySelector('.project-name').textContent = project.name;
    node.querySelector('.project-meta').textContent = `${project.profile} · ${new Date(project.createdAt).toLocaleDateString()}`;
    node.querySelector('button').addEventListener('click', () => selectProject(project.id));
    container.append(node);
  }
  if (selectId) await selectProject(selectId, true);
}

async function selectProject(projectId, quiet = false) {
  const detail = await api(`/api/projects/${encodeURIComponent(projectId)}`);
  state.selected = detail;
  renderDetail(detail);
  if (!quiet) window.history.replaceState({}, '', `#${projectId}`);
  scheduleRefresh();
}

function renderDetail({ project, jobs }) {
  const panel = $('#detail-panel'); panel.textContent = '';
  const header = document.createElement('div'); header.className = 'detail-header';
  header.innerHTML = `<div><p class="eyebrow">${escape(project.profile)}</p><h2>${escape(project.name)}</h2><p class="brief">${escape(project.brief)}</p></div>`;
  const actions = document.createElement('div'); actions.className = 'actions';
  actions.append(actionButton('Plan website', () => createJob(project.id, 'PLAN')));
  const latestPlan = jobs.find((job) => job.id === project.latestPlanJobId);
  const latestDesign = jobs.find((job) => job.id === project.latestDesignJobId);
  if (latestPlan?.status === 'passed') actions.append(actionButton('Design direction', () => createJob(project.id, 'DESIGN', { planJobId: latestPlan.id }), 'secondary'));
  if (latestPlan?.status === 'passed' && latestDesign?.status === 'passed' && latestDesign.planJobId === latestPlan.id) actions.append(actionButton('Build approved design', () => createJob(project.id, 'BUILD', { planJobId: latestPlan.id, designJobId: latestDesign.id })));
  header.append(actions); panel.append(header);
  if (!jobs.length) { const notice = document.createElement('p'); notice.className = 'notice'; notice.textContent = 'Start with a read-only Plan. Then select and approve a visual direction before a build may start.'; panel.append(notice); }
  const list = document.createElement('section'); list.className = 'jobs';
  for (const job of jobs) list.append(renderJob(project, job));
  panel.append(list);
}

function renderJob(project, job) {
  const article = document.createElement('article'); article.className = 'job';
  const top = document.createElement('div'); top.className = 'job-top';
  top.innerHTML = `<div><h3>${escape(job.type)} <small>${escape(job.id)}</small></h3><p>${new Date(job.createdAt).toLocaleString()}</p></div><span class="status ${job.status}">${escape(job.status)}</span>`;
  article.append(top);
  const actions = document.createElement('div'); actions.className = 'job-actions';
  if (job.type === 'PLAN' && job.status === 'awaiting_approval') actions.append(actionButton('Approve plan', () => approveJob(project.id, job.id)));
  if (job.type === 'DESIGN' && job.status === 'awaiting_approval' && job.selectedConceptId) actions.append(actionButton('Approve visual design', () => approveJob(project.id, job.id)));
  if (['passed', 'conditional', 'blocked'].includes(job.status) && job.worktree) {
    actions.append(actionButton('Verify', () => createJob(project.id, 'VERIFY', { targetJobId: job.id })));
    actions.append(actionButton('Review', () => createJob(project.id, 'REVIEW', { targetJobId: job.id })));
    actions.append(actionButton('Release decision', () => createJob(project.id, 'RELEASE', { targetJobId: job.id })));
    actions.append(actionButton('Preview', () => showPreview(article, project.id, job.id), 'secondary'));
  }
  if (actions.children.length) article.append(actions);
  if (job.type === 'DESIGN') {
    const designMount = document.createElement('div'); designMount.className = 'visual-design'; designMount.textContent = 'Loading visual directions…';
    article.append(designMount);
    hydrateVisualDesign(project.id, job, designMount);
  }
  const events = document.createElement('ul'); events.className = 'event-list';
  for (const event of (job.eventsData || []).slice(-8).reverse()) { const li = document.createElement('li'); li.innerHTML = `<time>${new Date(event.at).toLocaleTimeString()}</time> · <strong>${escape(event.phase)}</strong> · ${escape(event.message)}`; events.append(li); }
  article.append(events);
  hydrateEvents(project.id, job.id, events);
  return article;
}

async function hydrateEvents(projectId, jobId, container) {
  try { const events = await api(`/api/projects/${projectId}/jobs/${jobId}/events`); container.textContent = ''; for (const event of events.slice(-8).reverse()) { const li = document.createElement('li'); li.innerHTML = `<time>${new Date(event.at).toLocaleTimeString()}</time> · <strong>${escape(event.phase)}</strong> · ${escape(event.message)}`; container.append(li); } }
  catch { /* next refresh retries */ }
}

async function hydrateVisualDesign(projectId, job, mount) {
  try {
    const design = await api(`/api/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(job.id)}/artifacts/visual-design.json`);
    mount.textContent = '';
    const heading = document.createElement('div'); heading.className = 'visual-heading';
    heading.innerHTML = `<div><p class="eyebrow">Visual design authority</p><h4>Choose one direction</h4></div><span class="design-source">${escape(design.source || 'local')}</span>`;
    mount.append(heading);
    const guidance = document.createElement('p'); guidance.className = 'visual-guidance'; guidance.textContent = job.status === 'awaiting_approval' ? 'Selection is explicit and reversible only by creating a new design job. Build remains locked until this direction is approved.' : `Approved direction: ${design.selectedConceptId || 'not recorded'}.`;
    mount.append(guidance);
    const concepts = document.createElement('div'); concepts.className = 'concept-grid';
    for (const concept of design.concepts || []) concepts.append(renderConcept(projectId, job, design, concept));
    mount.append(concepts);
    const checks = document.createElement('p'); checks.className = 'visual-checks'; checks.textContent = `Evidence: ${(design.visualAcceptanceTests || []).slice(0, 2).join(' · ')}`;
    mount.append(checks);
  } catch { mount.textContent = 'Visual-design artifact is not available yet.'; }
}

function renderConcept(projectId, job, design, concept) {
  const card = document.createElement('section'); card.className = `concept ${design.selectedConceptId === concept.id ? 'selected' : ''}`;
  const title = document.createElement('div'); title.className = 'concept-title';
  title.innerHTML = `<div><h5>${escape(concept.name)}</h5><p>${escape(concept.summary)}</p></div>${design.selectedConceptId === concept.id ? '<span class="selected-badge">Selected</span>' : ''}`;
  card.append(title);
  const swatches = document.createElement('div'); swatches.className = 'swatches';
  for (const [name, value] of Object.entries(concept.palette || {}).slice(0, 6)) {
    const swatch = document.createElement('span'); swatch.className = 'swatch'; swatch.title = `${name}: ${value}`;
    swatch.style.backgroundColor = /^#[0-9a-f]{6}$/i.test(value) ? value : '#33415e';
    swatches.append(swatch);
  }
  card.append(swatches);
  const meta = document.createElement('p'); meta.className = 'concept-meta'; meta.textContent = `${concept.layout?.density || 'Intentional'} layout · ${concept.typography?.displayStyle || 'System typography'}`;
  card.append(meta);
  if (job.status === 'awaiting_approval' && design.selectedConceptId !== concept.id) card.append(actionButton(`Select ${concept.name}`, () => selectDesign(projectId, job.id, concept.id), 'secondary'));
  return card;
}

function showPreview(article, projectId, jobId) { const old = article.querySelector('iframe'); if (old) return old.remove(); const frame = document.createElement('iframe'); frame.className = 'preview'; frame.title = 'Private website preview'; frame.src = `/preview/${projectId}/${jobId}/`; article.append(frame); }
function actionButton(label, handler, style = '') { const button = document.createElement('button'); button.className = style; button.textContent = label; button.addEventListener('click', async () => { button.disabled = true; try { await handler(); toast(`${label}: started`); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } }); return button; }
async function createJob(projectId, type, extra = {}) { await api(`/api/projects/${projectId}/jobs`, { method:'POST', body: JSON.stringify({ type, ...extra }) }); await selectProject(projectId, true); }
async function approveJob(projectId, jobId) { await api(`/api/projects/${projectId}/jobs/${jobId}/approve`, { method:'POST', body:'{}' }); await selectProject(projectId, true); }
async function selectDesign(projectId, jobId, conceptId) { await api(`/api/projects/${projectId}/jobs/${jobId}/select-design`, { method:'POST', body: JSON.stringify({ conceptId }) }); await selectProject(projectId, true); }
function scheduleRefresh() { clearTimeout(state.timer); state.timer = setTimeout(async () => { if (state.selected) { try { await selectProject(state.selected.project.id, true); } catch {} } }, 3000); }
function toast(message, isError = false) { const element = document.createElement('div'); element.className = `toast ${isError ? 'error' : ''}`; element.textContent = message; document.body.append(element); setTimeout(() => element.remove(), 3600); }
function escape(value) { return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' })[char]); }

$('#create-project').addEventListener('submit', async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); try { const project = await api('/api/projects', { method:'POST', body:JSON.stringify(Object.fromEntries(form)) }); event.currentTarget.reset(); toast('Project created.'); await loadProjects(project.id); } catch (error) { toast(error.message, true); } });
$('#refresh').addEventListener('click', () => loadProjects());
loadHealth(); loadProjects(location.hash.slice(1) || undefined).catch((error) => toast(error.message, true)); setInterval(loadHealth, 15000);
