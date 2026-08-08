# Orivellum Forge Website Factory

An A-01 website-building system that runs with **Lemonade Server**, local models, local Git, and free verification tools. It is designed as an internal Orivellum module—not a cloud website builder and not a public-facing agent service.

## What is already implemented

- Local website-project dashboard, optimized for phone-sized browsers.
- Project brief → read-only site plan → explicit approval → three governed visual directions → human selection and approval → isolated build worktree.
- Lemonade OpenAI-compatible client at `http://127.0.0.1:13305/api/v1` by default.
- Local tool-calling builder with strict filesystem and command boundaries.
- Reusable responsive/static website starter with editable CSS tokens, a design-system manifest, accessibility basics, SEO metadata, mobile navigation, and a safe contact-form placeholder.
- Dedicated **Visual Design Authority** that creates original visual directions, records a selected concept, applies it as tokenized CSS in the worktree, and requires visual acceptance evidence.
- Git checkpoints per build, stored job ledger, quality reports, review jobs, release decisions, and SHA-256 evidence manifests.
- Deterministic lint, unit, build, link, scope, Playwright, Semgrep CE, Gitleaks, and OSV-Scanner gates. Browser/security gates never auto-install software or use the network.
- Loopback-only HTTP binding. Orivellum is the intended authenticated/VPN-facing gateway.

## What it deliberately does not do

- It never uses a paid model API, hosted inference endpoint, or website-builder subscription.
- It never installs packages, sends forms, publishes a website, changes production data, pushes Git, or merges to `main` autonomously.
- It never exposes Lemonade, the Factory, a project preview, or a worktree to the open Internet.
- It does not claim a release is complete when browser/security evidence is missing.

## A-01 prerequisites

1. **Windows 11 A-01**, with Node.js 20+ and Git available where the Factory will run.
2. **Lemonade Server**, started locally with an approved model loaded. Lemonade's official server supports OpenAI-compatible chat endpoints and is intentionally meant for local deployment. Configure a model through Lemonade; the Factory auto-detects the first loaded/available local model when `model` is `AUTO-DETECT`.
3. Optional—but required for a **Verified** web release—free local tooling installed inside each website project:
   - Playwright and its browsers
   - Semgrep CE
   - Gitleaks
   - OSV-Scanner

## Quick start

```powershell
Copy-Item .\config\factory.config.example.json .\config\factory.config.json
$env:LEMONADE_BASE_URL = "http://127.0.0.1:13305/api/v1"
node .\src\server.mjs
```

Open `http://127.0.0.1:4310` locally. Confirm the Lemonade health indicator is green before creating a plan.

The core Factory has no Node dependencies, so it does not require `npm install` to run. `npm test` validates the Factory package itself.

## Safe operating sequence

1. Create a Website Project with a concrete brief.
2. Create a **PLAN** job; inspect the plan artifact.
3. Click **Approve plan** only after requirements, scope, content limitations, and risks are correct.
4. Create a **DESIGN** job. Review its three original visual directions, select one, then click **Approve visual design**.
5. Create a **BUILD** job. It receives an isolated Git worktree and the approved design tokens.
6. Inspect the Work Ledger, design-system manifest, quality report, screenshots/traces, and the private preview.
7. Create **VERIFY**, then **REVIEW**, then **RELEASE** jobs.
8. A **VERIFIED** release still requires your explicit merge/publish decision.

See [docs/OPERATING-GUIDE.md](docs/OPERATING-GUIDE.md), [docs/VISUAL-DESIGN-AUTHORITY.md](docs/VISUAL-DESIGN-AUTHORITY.md), [docs/SECURITY-BOUNDARY.md](docs/SECURITY-BOUNDARY.md), and [integrations/ORIVELLUM-BRIDGE-CONTRACT.md](integrations/ORIVELLUM-BRIDGE-CONTRACT.md).
