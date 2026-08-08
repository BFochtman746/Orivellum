---
name: Mail Steward design decisions
description: A-01 Mail Steward architecture, token vault, schema, and integration points — critical for Phases 2–4
---

# A-01 Mail Steward

## Key decisions

**Client ID:** `1eaaef36-5239-4332-8a99-4309dfe36182` (env var `MAIL_CLIENT_ID`).  
**Tenant:** `consumers` (personal Microsoft accounts).

**Token encryption:** Fernet with SHA-256(`SESSION_SECRET`) → `mail_steward.token` in settings table. Never in audit log. Module: `src/orivellum/capabilities/mail/token_vault.py`.

**Graph IDs at rest:** All Graph message IDs, change keys, folder IDs, draft IDs stored encrypted via `encrypt_str`/`decrypt_str` in same vault module.

**Schema:** v107 — `mail_records`, `mail_assessments`, `mail_action_requests`, `mail_audit_events`, `mail_delta_links`.

**DB layer:** `src/orivellum/database/mail_store.py` — standalone class wrapping db._conn/_lock. Do not touch db.py for mail ops.

**Capability modules:** All under `src/orivellum/capabilities/mail/`. Imports in `__init__.py` are lazy (avoids circular imports at startup).

**Nightshift integration:** Pass 14b in `_run_nightshift_passes()` — only fires when `mail_steward.connected=true`.

**Send gate:** `mail_steward.send_enabled=true` required. User must also add `Mail.Send` scope to their Entra app registration. PATCH /api/mail/settings to enable.

**Compose-and-send flow:** `POST /api/mail/decisions/{id}/draft` → creates Outlook draft + returns `send_nonce`; `PATCH /api/mail/drafts/{actionRequestId}` → edit body/recipients; `POST /api/mail/decisions/{id}/send` with nonce → actually sends. Nothing auto-sends.

**Nonce pattern:** Single-use UUIDs stored in `mail_action_requests.nonce` with status=PENDING; `consume_nonce()` atomically sets APPROVED. Every mutating action requires a nonce.

**Lemonade URL:** `mail_steward.lemonade_url` setting (default `http://127.0.0.1:13305/api/v1`). Model: `mail_steward.lemonade_model`. Responses validated for required fields; extras rejected; safe fallback on any failure.

**Threat feeds:** OpenPhish + URLhaus, in-memory only, no external lookup by default. `threat_intel.refresh_openphish()` / `refresh_urlhaus()` for manual refresh. Match is evidence only — never triggers automatic action.

**API invariant:** `_safe_record()` / `_safe_assessment()` helpers strip all encrypted Graph IDs from responses. Browser never sees a raw Graph ID.

## Phase status
- Phase 1 (backend): ✅ COMPLETE — schema v107 live, all routes at /api/mail/*, nightshift integrated
- Phase 2 (web UI): Task #1011
- Phase 3 (mobile): Task #1012  
- Phase 4 (chat context): Task #1013

**Why:**  User added `Mail.Send` to Entra app and wants compose-and-send, not just draft-and-save.
