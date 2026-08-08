# Orivellum Bridge Contract

## Integration decision

The Website Factory is a local worker. Orivellum remains the system of record for users, chats, projects, object lifecycles, authorization, notifications, artifacts, and private access. Do not retain this dashboard as a competing permanent UI once the Orivellum module is integrated.

## Existing Orivellum primitives to reuse

- `ProjectService`: map a Website Project to the governed project object.
- `ChatService` and `ChatGenerationService`: preserve the user's conversation and nameable chat context.
- `JobEventHub` / `JobSocket`: stream Factory Work Ledger events to iPhone Safari.
- Artifact persistence: ingest plan, visual-design, design-system, quality, review, evidence manifest, and release decision artifacts.
- `LemonadeClient`: become the canonical local inference client. Factory requests should route through the same base URL, model policy, health check, and event telemetry.
- Security headers and runtime config: retain the established application boundary.

## Loopback worker API

The worker binds only to `127.0.0.1:4310`.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Lemonade and Factory readiness |
| `POST` | `/api/projects` | Create a governed website project |
| `GET` | `/api/projects/{id}` | Project + jobs summary |
| `POST` | `/api/projects/{id}/jobs` | Start `PLAN`, `DESIGN`, `BUILD`, `VERIFY`, `REVIEW`, or `RELEASE` |
| `POST` | `/api/projects/{id}/jobs/{jobId}/select-design` | Record the human-selected visual direction from a `DESIGN` artifact |
| `POST` | `/api/projects/{id}/jobs/{jobId}/approve` | Approve a plan or selected visual design |
| `GET` | `/api/projects/{id}/jobs/{jobId}/events` | Pollable ledger source; bridge into `JobSocket` |
| `GET` | `/preview/{projectId}/{jobId}/` | Private preview to proxy only after authorization |

## Required bridge behavior

1. Authorize the user and project before each call.
2. Translate worker events into existing JobSocket events; never display raw model reasoning.
3. Store worker artifacts under the project/job record with source, hash, lifecycle status, and timestamps.
4. Enforce approval at the Orivellum layer before `DESIGN`, `BUILD`, production integration, merge, or publishing. A selected design must belong to the approved plan used by the build.
5. Proxy previews only over the established private VPN path. Never make the `4310` worker listener public.
