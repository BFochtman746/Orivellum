---
name: Sidebar accordion, cognition system, nightshift, and progress panel
description: Architecture decisions for the major features added in the Monarch gap-fill sprint.
---

## Sidebar accordion (layout.tsx)

- `AppLayout` now uses a `SidebarInner` component shared by both desktop `<Sidebar>` and mobile `<Sheet>` (side=left, w-64).
- Four phase sections (Import/Understand/Write/Review) rendered with `Collapsible` + auto-expand on route match.
- Conversation history in sidebar: `useListConversations` + `useListWorks` for work-group labels; date buckets (Today/Yesterday/Previous 7 days/Older); inline rename via double-click; archive/restore on hover.
- Font-size controls: `useFontSize()` reads/writes `localStorage["oriv-font-size"]`, applies to `document.documentElement.style.fontSize`; range 13–20px.
- `useConnectivity()` hook in `src/lib/useConnectivity.ts` is the single source of truth for health; registers `window.online/offline` events and calls `recheckNow()` on reconnect. All components (ServerStatus in layout, aiOnline in chat) import from this hook.
- **Do NOT duplicate health polling** — import `useConnectivity` everywhere instead of `useGetSystemHealth`.

## Progress panel (#53)

- `GET /api/system/jobs` in `system.py` — returns documents with readiness NOT IN (ready, error, no_text) plus last nightshift row.
- `ProgressPanel` Sheet in `layout.tsx` — polls every 3s when open, 15s when closed (via `useJobs(open)` hook).
- Floating "Progress" badge in top-right of content area; shows active job count with pulse animation when > 0.
- The sticky badge has `pointer-events-none` on its container + `pointer-events-auto` on the button to avoid blocking content clicks.

## Nightshift daemon (#57)

- `src/orivellum/capabilities/nightshift.py` — daemon thread, fires at configurable hour (setting: `nightshift_hour`, default 3).
- Started in `app.py` lifespan alongside DB init; controlled by `db.get_setting("nightshift_enabled","true")`.
- Re-processes docs with `readiness=ready` and fewer than 3 knowledge items; runs `harvest()` + optional `llm_harvest()`.
- Writes markdown report to `data/nightshift/YYYY-MM-DD.md`; records run in `nightshift_runs` table (schema v40).

## Automemory (#57)

- After every user message, a daemon thread calls `_maybe_capture_memory()` in `conversations.py`.
- Only triggered if the message contains known patterns ("remember that", "my name is", "i prefer", etc.).
- Uses a quick LLM call to extract `{key, value}` pairs and upserts them into `user_memory` table (schema v40, unique index on `key`).
- `_build_system_prompt()` prepends a MEMORY block from `user_memory` to every system prompt.
- System page shows memories with delete buttons via `GET/DELETE /api/system/user-memory`.

## Cognition system (#54)

- `src/orivellum/capabilities/cognition.py` — classify() gate + deliberate() council + _call_sync() helper.
- `MessageSend` has `deep: bool = False`; when True, `send_message` calls `asyncio.to_thread(_deep_response, messages, model)`.
- `_deep_response()` calls `cognition.deliberate()` (Author→Critic→Synthesizer, 3 sequential LLM calls); falls through to `_UNAVAILABLE` on any failure.
- Frontend: `deepMode` state + Zap/Brain toggle button to the left of the Send button (right-11, absolute positioned).
- streamChat() now accepts `deep` boolean and passes it in the POST body.
- Project Compass state stored in `project_compass` table (schema v41), keyed by `work_id`; `cognition.read_compass()` / `update_compass()` helpers available but not yet wired to the streaming path.

## Quiz tab (#56)

- `POST /api/works/{work_id}/quiz` in `works.py` — async endpoint; fetches up to 20 knowledge items, calls AI with structured JSON prompt, strips markdown fences, parses questions.
- Returns `{"questions": [...], "work_id": ...}`; each question has `q`, `options[4]`, `answer` (0-based index), `explanation`.
- `QuizTab` component in `works/detail.tsx` — 3 phases: idle (generate button), active (answer questions), done (score + explanations).
- Added `GraduationCap`, `RefreshCw`, `ChevronRight` icons; "Quiz" tab entry added to the tab list.

## Work files drawer (#52)

- `WorkFilesDrawer` component in `chat/index.tsx` — Sheet (side=right, w-80) opened by "Files" button in chat header when `convWorkId` is set.
- Uses `useGetWorkDocuments(workId, { query: { queryKey: getGetWorkDocumentsQueryKey(workId), enabled: open } })`.
- `GET /api/knowledge/ask` in `works.py` — cross-work knowledge + chunk search; accepts optional `work_id` to scope.

## Schema versions

- v40: `user_memory` (unique on key) + `nightshift_runs`
- v41: `project_compass` (keyed by work_id, ON CONFLICT UPDATE)

**Why:** Keeping schema version numbers documented prevents accidentally re-using them in parallel task agents.

## Learning reset (added)
- `POST /api/works/{work_id}/learning/reset` — resets ALL mastery for a work; `reset_mastery(db, work_id)` in capabilities/learning.py.
- `POST /api/works/{work_id}/learning/concepts/{concept_id}/reset` — resets one concept; same function with concept_id arg.
- "Reset & study again" button added to `all_done` phase in both `works/detail.tsx` (LearnTab) and `mobile/app/work/[id].tsx` (MobileLearnTab).

## Progress panel → Library link
- Job rows in `layout.tsx` ProgressPanel now include a `<Link href="/library/{j.id}">` with ExternalLink icon; `j.id` IS the document id (from the SQL `SELECT d.id ...`).

## no_text kind
- `extraction.py` `_extract_image()` now returns `kind="no_text"` (was `kind="image"`) when OCR yields no text; also excluded from system/jobs query via `NOT IN ('ready','error','no_text')`.
