# Visual Design Authority

Visual design is a governed artifact in Orivellum Forge, not an adjective appended to a build prompt.

## Lifecycle

`PLAN (approved) → DESIGN (three directions) → human selection → DESIGN approval → BUILD → visual/browser evidence → REVIEW → RELEASE`

A build cannot start until the selected visual-design artifact and the plan it belongs to are both approved. A new plan requires a new matching design artifact. The Factory records the selected concept, selection time, source, and resulting token files with the job evidence.

## What a design artifact contains

- Three original, editable directions rather than one opaque generated image.
- Palette, typography, spacing, radius, layout, components, responsive composition, and motion rules.
- An asset policy and acceptance criteria.
- A WCAG 2.2 AA-informed baseline: semantic structure, visible focus, sufficient contrast, touch targets, reduced motion, and mobile-first behavior.
- A selected concept ID only after a human selects it in the Factory interface.

The local Lemonade model may propose directions. It cannot select one, approve one, copy a reference, invent a brand asset, or turn an unknown image into factual proof.

## Editable implementation

When a design is approved, Forge writes the selected direction into the isolated build worktree as:

- `design-tokens.css` — shared semantic color, typography, spacing, radius, and motion tokens.
- `design-system.json` — the selected concept plus the evidence and acceptance contract.

The builder receives both files and may refine components, but it must retain shared tokens and must not add remote design dependencies. This keeps the site editable after generation and keeps a visual decision from dissolving into scattered one-off CSS.

## Assets and originality

The default is typography, layout, CSS composition, and original shapes. Images are permitted only when they are user-provided or otherwise rights-cleared and their provenance is recorded in the project. Forge does not download stock images, hotlink remote assets, assume an image-generation endpoint, or present generated people, places, logos, or testimonials as factual evidence.

## Visual acceptance

The deterministic `visual` gate requires the shared token sheet, a selected design-system manifest, token use across pages, responsive rules, reduced-motion handling, focus styles, and image `alt` attributes. When local Playwright is intentionally configured, it captures private desktop and iPhone-sized screenshot evidence under the job's quality artifacts. Missing browser or security tooling remains `CONDITIONAL`, never silently verified.

For meaningful visual regression comparison, first review and explicitly approve a screenshot baseline for the project. Store that baseline as a governed project artifact; do not treat a newly generated screenshot as an automatically approved design.
