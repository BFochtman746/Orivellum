# Operating Guide

## Status meanings

| Status | Meaning |
| --- | --- |
| `queued` / `running` | The job is in progress. |
| `awaiting_approval` | A read-only plan or visual design is ready. No later stage may use it until approved. |
| `passed` | The job's own required work passed. This is not automatically a production release. |
| `conditional` | The candidate may be useful, but required evidence or manual acceptance remains. |
| `blocked` | A verification, scope, policy, or review condition stopped acceptance. |
| `failed` | The job or local service failed technically; inspect its ledger. |

## Project brief standard

Provide:

- Audience and intended action.
- Required pages and features.
- Exact approved name, contact details, claims, and content sources.
- Brand constraints, reference sites, visual requirements, and accessibility needs.
- Explicit non-goals and forbidden changes.
- Any form, privacy, payment, database, legal, or deployment requirements.

An incomplete brief can produce a plan, but the plan must label missing facts instead of inventing them.

## Visual design sequence

After approving a plan, create a `DESIGN` job. It produces three distinct, editable visual directions. Inspect the layout, palette, typography, component approach, asset policy, mobile behavior, and visual acceptance rules. Select exactly one direction, then approve the design. A `BUILD` job requires an approved design tied to the same approved plan; it writes the selection into `design-tokens.css` and `design-system.json` before the builder begins.

Do not treat a reference site as permission to copy its layout or visual identity. Do not use image assets without known rights and provenance. See [VISUAL-DESIGN-AUTHORITY.md](VISUAL-DESIGN-AUTHORITY.md).

## Release evidence

Every candidate retains its work ledger, agent summary, Git checkpoint, quality report, review, release decision, and SHA-256 manifest. A release becomes `VERIFIED` only when all required deterministic gates and the independent review pass. If Playwright, Semgrep, Gitleaks, or OSV-Scanner are not configured, status is `CONDITIONAL`, not `VERIFIED`.

## Website types

| Profile | Use it for | Default emphasis |
| --- | --- | --- |
| Marketing | A campaign, portfolio, landing page, or informational site | Fast loading, clear message, SEO, CTA |
| Business | A business/services website | Trust, contact flow, service information, later CMS/integrations |
| Web app | An interactive product or dashboard | UI states, APIs, authentication, functional tests |
| Orivellum module | A feature inside Orivellum | Existing Java, job, artifact, lifecycle, and security conventions |

## Browser verification

Install Playwright only as an intentional setup action within a disposable website worktree or baseline template. Configure `FORGE_PREVIEW_URL` to point Playwright to the private preview. The included configuration runs Chromium and an iPhone-sized WebKit profile, captures a visual-evidence screenshot for each configured viewport, retains trace/screenshot/video artifacts on failure, and must never point at production. Review a screenshot baseline explicitly before using it for a later visual-regression comparison.
