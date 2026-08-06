# Orivellum E2E Test Suite

Playwright end-to-end tests covering five critical user flows.

## Prerequisites

1. Both the **API server** and **web UI** must be running:
   ```
   pnpm dev          # starts all services
   ```

2. Set the API key (from `data/api_key.txt` or startup logs):
   ```
   export E2E_API_KEY=<your-api-key>
   ```

   The suite also reads `data/api_key.txt` automatically and falls back to
   querying `data/orivellum.db` directly, so the env var is only required
   when neither file is accessible.

## Run the suite

```bash
# Headless (default — CI-friendly)
pnpm test:e2e

# Visual debugging
pnpm test:e2e:headed

# Single test file
pnpm test:e2e e2e/tests/01-doc-upload.spec.ts

# With HTML report
pnpm test:e2e --reporter=html
```

## Test flows

| # | File | What it tests |
|---|------|---------------|
| 1 | `01-doc-upload.spec.ts` | Upload document → processing completes → knowledge items appear |
| 2 | `02-work-link.spec.ts` | Create Work → link document → stats update |
| 3 | `03-chat.spec.ts` | Open chat → send message → AI response streams within 30 s |
| 4 | `04-tts.spec.ts` | Generate TTS clip → output appears in Studio Outputs |
| 5 | `05-duplicate.spec.ts` | Upload duplicate file → dedup toast/banner appears |

Tests 3 and 4 are **automatically skipped** when their backend service is
offline (AI server / TTS engine), so the suite can pass in any environment.

## Reports

HTML reports are written to `e2e/report/`. Open `e2e/report/index.html` in
a browser after a run.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `E2E_API_KEY` | *(from file/db)* | API key for authentication |
| `E2E_BASE_URL` | `http://localhost:80/orivellum-ui` | Web UI base URL |
| `E2E_API_ORIGIN` | `http://localhost:8080` | Direct API server URL |
