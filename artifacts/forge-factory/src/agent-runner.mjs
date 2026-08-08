import { LemonadeClient } from './lemonade-client.mjs';
import { createToolExecutor, toolDefinitions } from './agent-tools.mjs';
import { truncate } from './utils.mjs';
import { deterministicVisualDesign, normalizeVisualDesign } from './visual-design.mjs';

export async function createPlan({ lemonade, project, instruction = '', onEvent }) {
  const fallback = deterministicPlan(project, instruction);
  const system = `You are Orivellum Forge's read-only website planning agent. Return only valid JSON with keys: siteName, goal, audience, pages, visualDirection, components, contentNeeds, responsiveRules, accessibilityRules, seoRules, acceptanceTests, risks. Do not use markdown, do not claim unverified facts, and do not propose paid or cloud dependencies.`;
  const user = `Project: ${project.name}\nProfile: ${project.profile}\nBrief: ${project.brief}\nAdditional instruction: ${instruction || 'None'}\nCreate a practical implementation plan.`;
  try {
    await onEvent('lemonade', 'Requesting local planning model.');
    const client = new LemonadeClient(lemonade);
    const result = await client.chat({ messages: [{ role: 'system', content: system }, { role: 'user', content: user }], temperature: 0.15, maxTokens: 3000 });
    const parsed = parseJson(result.message.content);
    if (parsed && Array.isArray(parsed.pages) && Array.isArray(parsed.acceptanceTests)) {
      return { plan: { ...fallback, ...parsed, source: 'lemonade', model: client.model }, raw: result.message.content };
    }
    await onEvent('plan_fallback', 'Local model response was not valid plan JSON; retained deterministic baseline.');
    return { plan: { ...fallback, source: 'deterministic-fallback', model: client.model }, raw: result.message.content };
  } catch (error) {
    await onEvent('plan_fallback', `Lemonade unavailable: ${error.message}`);
    return { plan: { ...fallback, source: 'deterministic-fallback', lemonadeError: error.message }, raw: null };
  }
}

export async function createVisualDesign({ lemonade, project, plan, instruction = '', onEvent }) {
  const fallback = deterministicVisualDesign(project, plan, instruction);
  const system = `You are Orivellum Forge's read-only visual design authority. Return only valid JSON with exactly three concepts. Each concept must have: id, name, summary, rationale, palette {canvas,surface,text,muted,line,accent,accentStrong,focus,onAccent}, typography {displayStyle,bodyStyle,scale}, layout {density,hero,grid,imageTreatment}, components (array), motion {character}. Also return principles, responsiveRules, accessibility {target,requirements}, visualAcceptanceTests, and optional modelNotes. Create original visual directions from the approved plan and brief; do not imitate a named brand or copy a reference site. Do not require paid fonts, external CDNs, remote images, trackers, or cloud services. The result must be implementable as editable HTML/CSS design tokens and meet a WCAG 2.2 AA-informed mobile-first baseline.`;
  const user = `Project: ${project.name}\nProfile: ${project.profile}\nBrief: ${project.brief}\nVisual brief: ${project.visualBrief || 'None supplied'}\nApproved site plan: ${JSON.stringify(plan, null, 2)}\nAdditional instruction: ${instruction || 'None'}\nCreate three distinct, practical, original visual directions. Do not select one; a human will select and approve one later.`;
  try {
    await onEvent('lemonade', 'Requesting local visual-design directions from Lemonade.');
    const client = new LemonadeClient(lemonade);
    const result = await client.chat({ messages: [{ role: 'system', content: system }, { role: 'user', content: user }], temperature: 0.35, maxTokens: 4200 });
    const design = normalizeVisualDesign(parseJson(result.message.content), fallback);
    if (design) return { design: { ...design, source: 'lemonade', model: client.model }, raw: result.message.content };
    await onEvent('design_fallback', 'Local model response was not a complete visual-design contract; retained deterministic directions.');
    return { design: { ...fallback, source: 'deterministic-fallback', model: client.model }, raw: result.message.content };
  } catch (error) {
    await onEvent('design_fallback', `Lemonade unavailable: ${error.message}`);
    return { design: { ...fallback, source: 'deterministic-fallback', lemonadeError: error.message }, raw: null };
  }
}

export async function runBuilder({ lemonade, project, plan, visualDesign, workspace, policy, instruction = '', onEvent, maxToolRounds, maxOutputChars }) {
  const client = new LemonadeClient(lemonade);
  const execute = createToolExecutor({ workspace, policy, onEvent, maxOutputChars });
  const system = `You are Orivellum Forge's local website builder. Work only in the isolated repository provided through tools. Build a polished, mobile-first, accessible, secure website from the approved plan and approved visual direction. Preserve existing working features. The approved visual direction is a contract: implement it using editable shared CSS tokens, update design-system.json to record the selected concept, and do not introduce unapproved external fonts, images, CDNs, trackers, or remote services. Do not copy a reference design. Use tools to inspect before edit and test after edits. Never access secrets, run network/install commands, weaken a test, write outside the worktree, or declare a release. End with a concise evidence-based summary of files changed and tests actually run.`;
  const task = `Project brief:\n${project.brief}\n\nApproved site plan:\n${JSON.stringify(plan, null, 2)}\n\nApproved visual design:\n${JSON.stringify(visualDesign, null, 2)}\n\nTask instruction:\n${instruction || 'Implement the approved plan and selected visual direction, improving the existing starter only where necessary.'}`;
  const messages = [{ role: 'system', content: system }, { role: 'user', content: task }];
  await onEvent('lemonade', 'Starting local website builder through Lemonade.');
  for (let round = 1; round <= maxToolRounds; round += 1) {
    const response = await client.chat({ messages, tools: toolDefinitions, temperature: 0.15, maxTokens: 4096 });
    const assistant = response.message;
    messages.push(assistant);
    if (!assistant.tool_calls?.length) {
      await onEvent('agent_complete', `Builder stopped after ${round} round(s).`, { usage: response.usage });
      return { ok: true, final: truncate(assistant.content || 'Builder completed without narrative.', 6000), rounds: round, model: client.model };
    }
    await onEvent('agent_tools', `Builder requested ${assistant.tool_calls.length} tool action(s).`, { round });
    for (const call of assistant.tool_calls) {
      const result = await execute(call.function.name, call.function.arguments);
      messages.push({ role: 'tool', tool_call_id: call.id, content: JSON.stringify(result) });
    }
  }
  throw new Error(`Builder exceeded ${maxToolRounds} tool rounds without completing.`);
}

export async function runReviewer({ lemonade, project, plan, visualDesign, diff, gates, onEvent }) {
  const client = new LemonadeClient(lemonade);
  const system = 'You are a read-only release reviewer. Return only JSON: {verdict:"pass|conditional|block", findings:[{severity,area,detail}], requirementsTrace:[{requirement,status,evidence}]}. Do not fabricate tests or facts.';
  const user = `Brief: ${project.brief}\nPlan: ${JSON.stringify(plan)}\nApproved visual design: ${JSON.stringify(visualDesign)}\nDiff: ${diff}\nGate report: ${JSON.stringify(gates)}`;
  try {
    const result = await client.chat({ messages: [{ role: 'system', content: system }, { role: 'user', content: user }], temperature: 0.05, maxTokens: 3000 });
    const review = parseJson(result.message.content) || { verdict: 'conditional', findings: [{ severity: 'warning', area: 'review', detail: 'Review output was not structured JSON.' }], raw: result.message.content };
    await onEvent('review', `Local reviewer returned ${review.verdict || 'conditional'} verdict.`);
    return review;
  } catch (error) {
    await onEvent('review', `Local reviewer unavailable: ${error.message}`);
    return { verdict: 'conditional', findings: [{ severity: 'warning', area: 'review', detail: `No local review: ${error.message}` }], requirementsTrace: [] };
  }
}

function parseJson(value) {
  // Strip both ```json and bare ``` fences — models often omit the language tag.
  const text = String(value || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '').trim();
  try { return JSON.parse(text); } catch { return null; }
}

function deterministicPlan(project, instruction) {
  return {
    siteName: project.name,
    goal: 'Provide a clear, credible, mobile-first website that turns the brief into navigable content and an explicit call to action.',
    audience: 'Visitors described or implied by the project brief.',
    pages: [{ slug: '/', title: 'Home', sections: ['Hero', 'Benefits', 'Proof', 'Call to action'] }, { slug: '/about', title: 'About', sections: ['Mission', 'Story', 'Values'] }, { slug: '/contact', title: 'Contact', sections: ['Contact options', 'Form', 'Location/service area'] }],
    visualDirection: 'Professional, high-contrast, spacious, responsive, and content-led. Use tokens instead of one-off styling.',
    components: ['Header/navigation', 'Hero', 'Feature cards', 'Content section', 'Call to action', 'Footer', 'Contact form'],
    contentNeeds: ['Approved name, value proposition, service/product details, proof/testimonials, contact information, rights-cleared visual assets'],
    responsiveRules: ['Mobile-first layout', '44px minimum touch targets', 'No horizontal overflow', 'Readable text at 320px width'],
    accessibilityRules: ['Semantic landmarks', 'Visible focus', 'Meaningful headings', 'Labels for controls', 'Sufficient contrast'],
    seoRules: ['Unique title and meta description', 'Single h1', 'Descriptive links', 'Canonical URL when domain exists'],
    acceptanceTests: ['Home page loads without console errors', 'Navigation works at mobile and desktop widths', 'Contact controls are labelled', 'No broken internal links', 'Design passes manual brand review'],
    risks: ['Brief may not contain approved brand assets, legal copy, or actual contact details.', 'Publishing and external integrations require explicit approval.'],
    additionalInstruction: instruction || null
  };
}
