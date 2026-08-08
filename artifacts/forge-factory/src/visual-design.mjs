import { nowIso } from './utils.mjs';

const SYSTEM_SANS = 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const SYSTEM_SERIF = 'Iowan Old Style, "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif';
const HEX = /^#[0-9a-f]{6}$/i;

export function deterministicVisualDesign(project, plan = {}, instruction = '') {
  const profile = String(project.profile || 'marketing');
  const directions = profile === 'web-app' || profile === 'orivellum-module'
    ? appDirections()
    : siteDirections();
  return {
    schemaVersion: '1.0.0',
    source: 'deterministic-fallback',
    projectName: project.name,
    planJobContext: plan.siteName || project.name,
    createdAt: nowIso(),
    selectionRequired: true,
    selectedConceptId: null,
    visualBrief: String(project.visualBrief || instruction || '').trim() || null,
    principles: [
      'Content and user action are clearer than decoration.',
      'The selected system must remain editable as CSS tokens and semantic components.',
      'The mobile composition is designed intentionally, not scaled down from desktop.',
      'No external fonts, trackers, stock-image downloads, or remote design dependencies are assumed.'
    ],
    assetPolicy: {
      default: 'Use CSS composition, typography, and simple original shapes before imagery.',
      allowed: ['User-provided, rights-cleared assets stored in the project repository.', 'Original local assets whose provenance is recorded.'],
      prohibited: ['Unverified stock assets.', 'Remote hotlinked images.', 'Generated-person or brand imagery presented as factual evidence.'],
      provenanceRequired: true
    },
    responsiveRules: [
      'Design from 320px upward; no horizontal overflow is acceptable.',
      'Use a single-column reading flow on phone widths unless a comparison genuinely needs columns.',
      'Use touch targets of at least 44 by 44 CSS pixels.',
      'Preserve hierarchy, call-to-action clarity, and focus visibility at 390px iPhone width.'
    ],
    accessibility: {
      target: 'WCAG 2.2 AA-informed baseline',
      requirements: ['Semantic landmarks and heading hierarchy.', 'Visible non-obscured focus treatment.', 'Sufficient text and control contrast.', 'Reduced-motion alternative.', 'No color-only state meaning.']
    },
    visualAcceptanceTests: [
      'All pages load the shared design token stylesheet before component styling.',
      'The selected concept is recorded and the design-system file names the selected concept.',
      'Desktop and 390px mobile screenshots are captured by private browser verification when Playwright is configured.',
      'Navigation, controls, focus states, and text remain usable at 320px width.',
      'Any imagery has meaningful alternative text and recorded provenance.'
    ],
    concepts: directions
  };
}

export function normalizeVisualDesign(candidate, fallback) {
  if (!candidate || !Array.isArray(candidate.concepts) || candidate.concepts.length < 3) return null;
  const concepts = candidate.concepts.slice(0, 3).map((concept, index) => normalizeConcept(concept, fallback.concepts[index], index));
  if (new Set(concepts.map((concept) => concept.id)).size !== concepts.length) return null;
  return {
    ...fallback,
    source: 'lemonade',
    modelNotes: text(candidate.modelNotes, 700) || null,
    principles: stringList(candidate.principles, fallback.principles, 6),
    responsiveRules: stringList(candidate.responsiveRules, fallback.responsiveRules, 8),
    accessibility: {
      target: text(candidate.accessibility?.target, 140) || fallback.accessibility.target,
      requirements: stringList(candidate.accessibility?.requirements, fallback.accessibility.requirements, 8)
    },
    visualAcceptanceTests: stringList(candidate.visualAcceptanceTests, fallback.visualAcceptanceTests, 8),
    concepts,
    selectedConceptId: null,
    selectionRequired: true,
    createdAt: nowIso()
  };
}

export function getSelectedConcept(design) {
  if (!design?.selectedConceptId) return null;
  return (design.concepts || []).find((concept) => concept.id === design.selectedConceptId) || null;
}

export function designSystemManifest(design) {
  const selected = getSelectedConcept(design);
  if (!selected) throw new Error('A selected visual direction is required to create a design-system manifest.');
  return {
    schemaVersion: '1.0.0',
    source: 'Orivellum Forge approved visual design',
    selectedConceptId: selected.id,
    selectedConceptName: selected.name,
    selectedAt: design.selectedAt || null,
    palette: selected.palette,
    typography: selected.typography,
    layout: selected.layout,
    components: selected.components,
    motion: selected.motion,
    responsiveRules: design.responsiveRules,
    accessibility: design.accessibility,
    visualAcceptanceTests: design.visualAcceptanceTests,
    assetPolicy: design.assetPolicy
  };
}

export function cssTokenSheet(design) {
  const selected = getSelectedConcept(design);
  if (!selected) throw new Error('A selected visual direction is required to create a CSS token sheet.');
  const color = selected.palette;
  return `/* Generated from the approved Orivellum Forge visual direction: ${selected.id}. */
:root {
  --color-canvas: ${color.canvas};
  --color-surface: ${color.surface};
  --color-text: ${color.text};
  --color-muted: ${color.muted};
  --color-line: ${color.line};
  --color-accent: ${color.accent};
  --color-accent-strong: ${color.accentStrong};
  --color-focus: ${color.focus};
  --color-on-accent: ${color.onAccent};
  --font-display: ${selected.typography.displayFamily};
  --font-body: ${selected.typography.bodyFamily};
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --space-7: 3rem;
  --space-8: 4rem;
  --radius-sm: 0.5rem;
  --radius-md: 1.1rem;
  --radius-lg: 1.75rem;
  --shadow-card: 0 1rem 3rem rgba(16, 37, 38, 0.08);
  --motion-fast: 160ms;
  --motion-standard: 240ms;
  --ink: var(--color-text);
  --muted: var(--color-muted);
  --paper: var(--color-canvas);
  --surface: var(--color-surface);
  --line: var(--color-line);
  --accent: var(--color-accent);
  --accent-strong: var(--color-accent-strong);
  --radius: var(--radius-md);
}
:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 3px; }
@media (prefers-reduced-motion: reduce) {
  :root { --motion-fast: 0ms; --motion-standard: 0ms; }
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: 0ms !important; animation-duration: 0ms !important; }
}
`;
}

function normalizeConcept(candidate, fallback, index) {
  const id = conceptId(candidate?.id, index);
  return {
    id,
    name: text(candidate?.name, 80) || fallback.name,
    summary: text(candidate?.summary, 280) || fallback.summary,
    rationale: text(candidate?.rationale, 700) || fallback.rationale,
    palette: palette(candidate?.palette, fallback.palette),
    typography: {
      displayFamily: fallback.typography.displayFamily,
      bodyFamily: SYSTEM_SANS,
      displayStyle: text(candidate?.typography?.displayStyle, 160) || fallback.typography.displayStyle,
      bodyStyle: text(candidate?.typography?.bodyStyle, 160) || fallback.typography.bodyStyle,
      scale: text(candidate?.typography?.scale, 160) || fallback.typography.scale
    },
    layout: {
      density: text(candidate?.layout?.density, 120) || fallback.layout.density,
      hero: text(candidate?.layout?.hero, 220) || fallback.layout.hero,
      grid: text(candidate?.layout?.grid, 220) || fallback.layout.grid,
      imageTreatment: text(candidate?.layout?.imageTreatment, 220) || fallback.layout.imageTreatment
    },
    components: stringList(candidate?.components, fallback.components, 10),
    motion: {
      character: text(candidate?.motion?.character, 160) || fallback.motion.character,
      reducedMotion: 'Respect prefers-reduced-motion; no essential information depends on animation.'
    }
  };
}

function palette(value, fallback) {
  const output = {};
  for (const key of Object.keys(fallback)) {
    const candidate = String(value?.[key] || '').trim();
    output[key] = HEX.test(candidate) ? candidate : fallback[key];
  }
  return output;
}

function conceptId(value, index) {
  const id = String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 48);
  return id || `concept-${index + 1}`;
}

function text(value, max) {
  return typeof value === 'string' ? value.trim().slice(0, max) : '';
}

function stringList(value, fallback, max) {
  if (!Array.isArray(value)) return fallback;
  const items = value.map((item) => text(item, 320)).filter(Boolean).slice(0, max);
  return items.length ? items : fallback;
}

function siteDirections() {
  return [
    {
      id: 'executive-editorial',
      name: 'Executive Editorial',
      summary: 'Calm, high-trust typography and generous white space for expertise-led services or organizations.',
      rationale: 'The message has room to breathe, while dark type and a restrained green accent make calls to action unambiguous.',
      palette: { canvas: '#F8F7F1', surface: '#FFFEFA', text: '#102526', muted: '#506062', line: '#D7DFD8', accent: '#166A63', accentStrong: '#0E4E49', focus: '#B3DED1', onAccent: '#FFFFFF' },
      typography: { displayFamily: SYSTEM_SERIF, bodyFamily: SYSTEM_SANS, displayStyle: 'Measured editorial headings with a compact sans-serif utility layer.', bodyStyle: 'Plain, highly readable system sans.', scale: '1.125 modular scale with fluid display sizing.' },
      layout: { density: 'Spacious', hero: 'Text-led asymmetric hero with one decisive action.', grid: '12-column desktop rhythm that collapses to one reading column.', imageTreatment: 'No image required; if approved imagery is added, use one purposeful full-bleed or framed asset.' },
      components: ['Quiet sticky header', 'Text-led hero', 'Proof band', 'Bordered content cards', 'Dark callout', 'Plain labelled form controls'],
      motion: { character: 'Short opacity and color transitions only; no ambient motion.', reducedMotion: '' }
    },
    {
      id: 'precision-industrial',
      name: 'Precision Industrial',
      summary: 'Crisp structure, cool contrast, and information-dense modules for technical, product, and operational work.',
      rationale: 'A strict grid and blue signal color communicate competence without turning the site into a dashboard.',
      palette: { canvas: '#F3F6FA', surface: '#FFFFFF', text: '#10233D', muted: '#53657B', line: '#CBD5E1', accent: '#155EEF', accentStrong: '#0E46B7', focus: '#B7D2FF', onAccent: '#FFFFFF' },
      typography: { displayFamily: SYSTEM_SANS, bodyFamily: SYSTEM_SANS, displayStyle: 'Tight geometric system-sans headings with numeric clarity.', bodyStyle: 'Compact system sans with generous line height.', scale: '1.2 display scale with 8px spacing rhythm.' },
      layout: { density: 'Structured', hero: 'Split message-and-proof hero with a constrained data-like highlight.', grid: '12-column desktop and 4-column mobile grid with deliberate alignment.', imageTreatment: 'Use diagrams or original product frames only when they clarify a decision.' },
      components: ['Utility header', 'Signal label', 'Metric/proof modules', 'Structured cards', 'High-contrast action rail', 'Validation-forward form controls'],
      motion: { character: 'Fast, restrained state transitions that reinforce control feedback.', reducedMotion: '' }
    },
    {
      id: 'warm-service',
      name: 'Warm Service',
      summary: 'Approachable contrast, rounded forms, and reassuring pacing for visitor relationships and local service work.',
      rationale: 'Warm neutrals and a plum accent create humanity while keeping enough contrast for a serious, accessible experience.',
      palette: { canvas: '#FCF7F2', surface: '#FFFDFC', text: '#2A1F24', muted: '#6A5960', line: '#E5D5D8', accent: '#8A3D5B', accentStrong: '#672841', focus: '#F0C6D4', onAccent: '#FFFFFF' },
      typography: { displayFamily: SYSTEM_SERIF, bodyFamily: SYSTEM_SANS, displayStyle: 'Friendly serif display with a grounded system-sans interface.', bodyStyle: 'Open system sans tuned for longer reading.', scale: 'Fluid 1.125 scale with relaxed spacing.' },
      layout: { density: 'Relaxed', hero: 'Centered invitation with a clear next step and supportive proof.', grid: 'Simple paired sections that become a single conversational flow on mobile.', imageTreatment: 'Use original, rights-cleared human or place imagery only when it adds truthful context.' },
      components: ['Inviting header', 'Centered hero', 'Reassurance cards', 'Service/story split', 'Rounded callout', 'Supportive contact form'],
      motion: { character: 'Gentle color and position transitions; avoid parallax or auto-advance.', reducedMotion: '' }
    }
  ];
}

function appDirections() {
  const concepts = siteDirections();
  concepts[0] = { ...concepts[0], id: 'calm-command', name: 'Calm Command', summary: 'A quiet, high-contrast application shell that makes data and next actions easy to scan.', layout: { ...concepts[0].layout, hero: 'Application overview with current state, clear queue, and single primary action.' }, components: ['Status header', 'Command panel', 'Evidence list', 'Detail drawer', 'Clear empty states', 'Labelled controls'] };
  concepts[1] = { ...concepts[1], id: 'operator-grid', name: 'Operator Grid', summary: 'Dense but deliberate operational screens built around a consistent grid and sober signal colors.', components: ['Utility bar', 'Work queue', 'Evidence table', 'Inline validation', 'Command confirmation', 'Responsive drawer'] };
  concepts[2] = { ...concepts[2], id: 'guided-workflow', name: 'Guided Workflow', summary: 'A more human application path with stepwise guidance and reduced cognitive load.', components: ['Progress header', 'Task cards', 'Plain-language guidance', 'Evidence attachment', 'Review step', 'Helpful empty states'] };
  return concepts;
}
