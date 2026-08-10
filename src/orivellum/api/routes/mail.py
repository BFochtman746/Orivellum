"""A-01 Mail Steward API routes — /api/mail/*

All routes sit behind Orivellum's existing session-cookie authentication.
Graph IDs, tokens, and message content never appear in HTTP responses;
only stable local record IDs and display-safe metadata are returned.

Send and delete routes are present but gated by feature flags:
  mail_steward.send_enabled   = true   (requires Mail.Send scope in token)
  mail_steward.delete_enabled = true   (always disabled in v1)
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db
from orivellum.capabilities.mail.models import MailStewardError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mail")


# ── Request bodies ─────────────────────────────────────────────────────────────

class ConnectPollBody(BaseModel):
    handle: str          # opaque handle from /connect/start (contains device_code)

class DisconnectBody(BaseModel):
    confirm: str         # must equal "disconnect"

class DraftBody(BaseModel):
    nonce: str

class DraftUpdateBody(BaseModel):
    body_text: str | None = None
    to_recipients: list[str] | None = None
    subject: str | None = None

class SendBody(BaseModel):
    action_request_id: str
    nonce: str

class MoveBody(BaseModel):
    destination: str     # "review" | "junk" | "inbox"
    nonce: str

class UndoBody(BaseModel):
    nonce: str

class SyncBody(BaseModel):
    folders: list[str] | None = None


# ── Helper: read encrypted handle stored in DB ─────────────────────────────────

def _get_pending_device_code(db: Any, handle: str) -> str | None:
    """Retrieve a stored device_code by handle (short-lived; deleted after use)."""
    val = db.get_setting(f"mail_steward.pending_dc.{handle}", "")
    return val if val else None


def _store_pending_device_code(db: Any, handle: str, device_code: str) -> None:
    db._set_setting(f"mail_steward.pending_dc.{handle}", device_code)


def _clear_pending_device_code(db: Any, handle: str) -> None:
    db._set_setting(f"mail_steward.pending_dc.{handle}", "")


# ── Connection ─────────────────────────────────────────────────────────────────

@router.get("/summary")
def mail_summary():
    """Counts, connection state, and feed freshness."""
    db = get_db()
    connected = db.get_setting("mail_steward.connected", "false") == "true"
    from orivellum.capabilities.mail.threat_intel import feed_status
    from orivellum.database.mail_store import MailStore
    store = MailStore(db)
    counts = store.summary() if connected else {}
    return {
        "connected": connected,
        "send_enabled": db.get_setting("mail_steward.send_enabled", "false") == "true",
        "feeds": feed_status(),
        **counts,
    }


@router.post("/connect/start")
def connect_start():
    """Request a Microsoft device code.

    Returns user_code + verification_uri for display.
    The device_code is stored server-side only and referenced by an opaque handle.
    """
    db = get_db()
    from orivellum.capabilities.mail import oauth
    include_send = db.get_setting("mail_steward.send_enabled", "false") == "true"
    try:
        data = oauth.request_device_code(include_send=include_send)
    except MailStewardError as exc:
        raise HTTPException(502, str(exc))

    handle = secrets.token_urlsafe(16)
    _store_pending_device_code(db, handle, data["device_code"])

    return {
        "handle": handle,
        "user_code": data["user_code"],
        "verification_uri": data["verification_uri"],
        "expires_in": data.get("expires_in", 900),
        "message": data.get("message", ""),
    }


@router.post("/connect/poll")
def connect_poll(body: ConnectPollBody):
    """Poll for token completion.

    Returns {"status": "pending"} while the user is signing in,
    {"status": "connected"} on success.  Never returns a token.
    """
    db = get_db()
    device_code = _get_pending_device_code(db, body.handle)
    if not device_code:
        raise HTTPException(404, "Handle not found or already used")

    import time

    from orivellum.capabilities.mail import oauth
    from orivellum.capabilities.mail.graph_client import GraphClient
    from orivellum.capabilities.mail.token_vault import store_token

    try:
        token_data = oauth.poll_for_token(device_code, interval=0, max_wait=2)
    except MailStewardError as exc:
        msg = str(exc)
        if "timed out" in msg or "pending" in msg.lower():
            return {"status": "pending"}
        _clear_pending_device_code(db, body.handle)
        raise HTTPException(400, msg)

    # Token acquired — store encrypted, verify identity
    token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
    store_token(db, token_data)
    _clear_pending_device_code(db, body.handle)

    try:
        client = GraphClient(token_data["access_token"])
        me = client.get_me()
        display_name = me.get("displayName", "")
        mail_addr    = me.get("mail") or me.get("userPrincipalName", "")
    except Exception:
        display_name = ""
        mail_addr    = ""

    db._set_setting("mail_steward.connected", "true")
    db._set_setting("mail_steward.account_display", f"{display_name} <{mail_addr}>")

    return {
        "status": "connected",
        "display_name": display_name,
        "mail": mail_addr,
    }


@router.post("/disconnect")
def disconnect(body: DisconnectBody):
    if body.confirm != "disconnect":
        raise HTTPException(400, "Confirm must equal 'disconnect'")
    db = get_db()
    from orivellum.capabilities.mail.token_vault import delete_token
    delete_token(db)
    db._set_setting("mail_steward.connected", "false")
    db._set_setting("mail_steward.account_display", "")
    db._set_setting("mail_steward.review_folder_id", "")
    return {"disconnected": True}


# ── Sync ───────────────────────────────────────────────────────────────────────

@router.post("/sync")
def trigger_sync(body: SyncBody | None = None, background_tasks: BackgroundTasks = None):
    """Trigger an immediate delta sync (runs in background thread)."""
    db = get_db()
    if db.get_setting("mail_steward.connected", "false") != "true":
        raise HTTPException(400, "Not connected to Outlook")

    def _run():
        from orivellum.capabilities.mail.steward import sync_mail
        cfg = get_config()
        try:
            result = sync_mail(db, cfg)
            logger.info("mail.sync: %s", result)
        except Exception as exc:
            logger.exception("mail.sync background error: %s", exc)

    background_tasks.add_task(_run)
    return {"status": "sync_started"}


# ── Attention / decision queue ─────────────────────────────────────────────────

@router.get("/attention")
def list_attention(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    attention_level: str | None = Query(None),
):
    """Decision queue — high-attention messages first."""
    db = get_db()
    from orivellum.database.mail_store import MailStore
    store = MailStore(db)
    records = store.list_mail_records(attention_level=attention_level, limit=limit, offset=offset)
    # Never return encrypted Graph IDs
    safe = [_safe_record(r) for r in records]
    return {"decisions": safe, "total": len(safe)}


@router.get("/decisions/{record_id}")
def get_decision(record_id: str):
    """Full decision detail — assessment, evidence, current action choices."""
    db = get_db()
    from orivellum.database.mail_store import MailStore
    store = MailStore(db)
    record = store.get_mail_record(record_id)
    if not record:
        raise HTTPException(404, "Decision not found")
    assessment = store.get_latest_assessment(record_id)
    audit = store.list_audit_events(record_id, limit=20)

    # Issue nonces for available actions
    actions: list[dict] = []
    if assessment:
        if assessment.get("needs_reply") and not assessment.get("is_high_risk"):
            nonce = store.issue_nonce(record_id, "CREATE_DRAFT")
            actions.append({"type": "CREATE_DRAFT", "nonce": nonce, "label": "Compose reply"})
        nonce = store.issue_nonce(record_id, "MOVE")
        actions.append({"type": "MOVE", "nonce": nonce, "label": "Move to Review"})

    # Offer UNDO_MOVE when the last action was a reversible move
    action_request_id = record.get("action_request_id")
    if action_request_id:
        ar = store.get_action_request(action_request_id)
        if ar and ar.get("action_type") == "MOVE" and ar.get("status") == "APPLIED":
            undo_nonce = store.issue_nonce(record_id, "UNDO_MOVE")
            actions.append({
                "type": "UNDO_MOVE",
                "nonce": undo_nonce,
                "action_request_id": action_request_id,
                "label": "Undo move",
            })

    return {
        "record": _safe_record(record),
        "assessment": _safe_assessment(assessment) if assessment else None,
        "available_actions": actions,
        "audit_trail": [_safe_audit(e) for e in audit],
    }


# ── Draft compose + send ───────────────────────────────────────────────────────

@router.post("/decisions/{record_id}/draft")
def create_draft(record_id: str, body: DraftBody):
    """Create an Outlook reply draft and return the composed text for review.

    The draft exists in Outlook as a draft — the user edits and sends it
    from the compose view.  Nothing is sent automatically.
    """
    db = get_db()
    from orivellum.capabilities.mail.steward import create_draft as _create_draft
    try:
        result = _create_draft(db, record_id, body.nonce)
    except MailStewardError as exc:
        raise HTTPException(400, str(exc))
    return result


@router.patch("/drafts/{action_request_id}")
def update_draft(action_request_id: str, body: DraftUpdateBody):
    """Update the draft body/recipients before sending."""
    db = get_db()
    from orivellum.capabilities.mail.steward import _get_fresh_client
    from orivellum.capabilities.mail.token_vault import decrypt_str
    from orivellum.database.mail_store import MailStore

    store  = MailStore(db)
    action = store.get_action_request(action_request_id)
    if not action:
        raise HTTPException(404, "Draft action not found")
    if action["action_type"] != "CREATE_DRAFT" or action["status"] != "APPLIED":
        raise HTTPException(400, "No active draft for this action")

    draft_id = decrypt_str(action.get("graph_draft_id_enc", ""))
    if not draft_id:
        raise HTTPException(400, "Draft ID missing")

    client = _get_fresh_client(db)
    if not client:
        raise HTTPException(503, "Not connected to Microsoft Graph")

    try:
        client.update_draft_full(
            draft_id,
            body_text=body.body_text,
            to_recipients=body.to_recipients,
            subject=body.subject,
        )
    except MailStewardError as exc:
        raise HTTPException(502, str(exc))

    return {"updated": True}


@router.post("/decisions/{record_id}/send")
def send_mail(record_id: str, body: SendBody):
    """Send the composed draft.

    Requires mail_steward.send_enabled = true and an explicit per-item nonce.
    After sending the user cannot undo — an audit event is recorded.
    """
    db = get_db()
    send_enabled = db.get_setting("mail_steward.send_enabled", "false") == "true"
    if not send_enabled:
        raise HTTPException(403, "FEATURE_DISABLED: send_enabled is false. Add Mail.Send scope and enable in settings.")

    from orivellum.capabilities.mail.steward import send_draft as _send
    try:
        result = _send(db, body.action_request_id, body.nonce)
    except MailStewardError as exc:
        raise HTTPException(400, str(exc))
    return result


# ── Move / undo ────────────────────────────────────────────────────────────────

@router.post("/decisions/{record_id}/move")
def move_message(record_id: str, body: MoveBody):
    """Move a message to a folder (reversible)."""
    db = get_db()
    from orivellum.capabilities.mail.steward import _ensure_review_folder, _get_fresh_client
    from orivellum.capabilities.mail.token_vault import encrypt_str
    from orivellum.database.mail_store import MailStore

    client = _get_fresh_client(db)
    if not client:
        raise HTTPException(503, "Not connected to Microsoft Graph")

    store = MailStore(db)

    # Resolve destination folder
    if body.destination == "review":
        folder_id = _ensure_review_folder(client, store, db)
    elif body.destination == "junk":
        folder_id = client.get_folder_id("junkemail") or ""
    elif body.destination == "inbox":
        folder_id = client.get_folder_id("inbox") or ""
    else:
        raise HTTPException(400, "destination must be 'review', 'junk', or 'inbox'")

    if not folder_id:
        raise HTTPException(502, f"Could not resolve folder: {body.destination}")

    from orivellum.capabilities.mail.steward import move_message as _move
    try:
        result = _move(db, record_id, encrypt_str(folder_id), body.nonce)
    except MailStewardError as exc:
        raise HTTPException(400, str(exc))
    return result


@router.post("/actions/{action_id}/undo")
def undo_action(action_id: str, body: UndoBody):
    """Undo a reversible move."""
    db = get_db()
    from orivellum.capabilities.mail.steward import undo_move as _undo
    try:
        result = _undo(db, action_id, body.nonce)
    except MailStewardError as exc:
        raise HTTPException(400, str(exc))
    return result


# ── Disabled in v1 ─────────────────────────────────────────────────────────────

@router.post("/decisions/{record_id}/delete")
def delete_message_disabled(record_id: str):
    """Permanent delete — disabled in v1.0."""
    raise HTTPException(403, "FEATURE_DISABLED: permanent delete is not available in v1.0")


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
def list_audit(
    record_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    from orivellum.database.mail_store import MailStore
    store = MailStore(db)
    events = store.list_audit_events(record_id, limit=limit, offset=offset)
    return {"events": [_safe_audit(e) for e in events], "total": len(events)}


# ── Settings ───────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_mail_settings():
    db = get_db()
    try:
        context_days = int(db.get_setting("mail_steward.context_days", "30"))
    except (ValueError, TypeError):
        context_days = 30
    return {
        "send_enabled":        db.get_setting("mail_steward.send_enabled", "false") == "true",
        "lemonade_url":        db.get_setting("mail_steward.lemonade_url", "http://127.0.0.1:13305/api/v1"),
        "lemonade_model":      db.get_setting("mail_steward.lemonade_model", ""),
        "sync_folders":        json.loads(db.get_setting("mail_steward.sync_folders", '["inbox"]')),
        "account_display":     db.get_setting("mail_steward.account_display", ""),
        "threat_feeds_enabled": db.get_setting("mail_steward.threat_feeds", "true") == "true",
        "context_days":        context_days,
    }


class MailSettingsBody(BaseModel):
    send_enabled: bool | None = None
    lemonade_url: str | None = None
    lemonade_model: str | None = None
    sync_folders: list[str] | None = None
    threat_feeds_enabled: bool | None = None
    context_days: int | None = None   # recency cap for chat context injection (0 = disabled)


@router.patch("/settings")
def update_mail_settings(body: MailSettingsBody):
    db = get_db()
    if body.send_enabled is not None:
        db._set_setting("mail_steward.send_enabled", "true" if body.send_enabled else "false")
    if body.lemonade_url is not None:
        url = body.lemonade_url.strip()
        if url == "":
            # Empty string = reset to built-in default
            db._set_setting("mail_steward.lemonade_url", "http://127.0.0.1:13305/api/v1")
        elif not url.startswith("http://127.") and not url.startswith("http://localhost"):
            raise HTTPException(400, "Lemonade must be a loopback URL")
        else:
            db._set_setting("mail_steward.lemonade_url", url)
    if body.lemonade_model is not None:
        # Empty string = clear the model override (server picks default)
        db._set_setting("mail_steward.lemonade_model", body.lemonade_model.strip())
    if body.sync_folders is not None:
        folders = body.sync_folders if body.sync_folders else ["inbox"]
        db._set_setting("mail_steward.sync_folders", json.dumps(folders))
    if body.threat_feeds_enabled is not None:
        db._set_setting("mail_steward.threat_feeds", "true" if body.threat_feeds_enabled else "false")
    if body.context_days is not None:
        days = max(0, int(body.context_days))   # clamp: 0 = no cap
        db._set_setting("mail_steward.context_days", str(days))
    return {"updated": True}


# ── Add to knowledge ──────────────────────────────────────────────────────────

class AddToKnowledgeBody(BaseModel):
    work_id: str | None = None      # optional — save under a specific Work
    research: bool = False          # fetch live web context about the sender/topic


@router.post("/decisions/{record_id}/add-to-knowledge")
def add_to_knowledge(record_id: str, body: AddToKnowledgeBody, background_tasks: BackgroundTasks):
    """Save a mail record's key information as a knowledge item.

    Builds a knowledge item from the assessment rationale and optionally
    enriches it with a live web search about the sender / email topic.
    The item is created with review_status="approved" so it is immediately
    available in chat context.

    Body:
        work_id  — optional Work to associate with; None = global knowledge
        research — when True, runs a quick web search and appends findings

    Returns:
        {"knowledge_id": str, "researched": bool}
    """
    db = get_db()
    from orivellum.database.mail_store import MailStore

    store = MailStore(db)
    record = store.get_mail_record(record_id)
    if not record:
        raise HTTPException(404, "Decision not found")

    assessment = store.get_latest_assessment(record_id)

    subject       = (record.get("subject") or "(no subject)")[:200]
    sender_name   = record.get("sender_name") or ""
    sender_domain = record.get("sender_domain") or "unknown"
    received_at   = (record.get("received_at") or "")[:10]
    attention     = (assessment or {}).get("attention_level", "medium")
    rationale     = (assessment or {}).get("rationale") or ""
    needs_reply   = bool((assessment or {}).get("needs_reply"))

    # Build the core knowledge text
    lines = [
        f"Email from @{sender_domain}" + (f" ({sender_name})" if sender_name else ""),
        f"Subject: {subject}",
        f"Received: {received_at}  |  Attention: {attention.upper()}" +
        ("  |  Needs reply" if needs_reply else ""),
    ]
    if rationale:
        lines.append(f"\nAI Assessment: {rationale}")

    researched = False
    web_snippet = ""

    if body.research:
        try:
            from orivellum.capabilities.websearch import fetch_web_context
            query = f"{subject} {sender_domain}"
            results = fetch_web_context(query, max_results=3, timeout=8)
            if results:
                parts = []
                for r in results[:3]:
                    title   = r.get("title") or r.get("url", "")
                    snippet = r.get("content") or r.get("snippet") or ""
                    if snippet:
                        parts.append(f"• {title}: {snippet[:300]}")
                if parts:
                    web_snippet = "\n\nOnline research:\n" + "\n".join(parts)
                    researched = True
        except Exception as _exc:
            logger.debug("mail.add-to-knowledge web research failed (non-fatal): %s", _exc)

    knowledge_text = "\n".join(lines) + web_snippet

    knowledge_id = db.create_knowledge_item(
        work_id=body.work_id or None,
        kind="claim",
        text=knowledge_text,
        subject=sender_domain,
        confidence=0.85,
        review_status="approved",
        meta={
            "source": "mail_steward",
            "mail_record_id": record_id,
            "mail_subject": subject,
            "sender_domain": sender_domain,
            "attention_level": attention,
            "researched_online": researched,
        },
    )

    store.create_audit_event(
        record_id,
        "KNOWLEDGE_SAVED",
        model_id="",
        result="SUCCESS",
        after={"knowledge_id": knowledge_id, "work_id": body.work_id, "researched": researched},
    )

    return {"knowledge_id": knowledge_id, "researched": researched}


# ── Assess on demand ──────────────────────────────────────────────────────────

@router.post("/decisions/{record_id}/assess")
def assess_decision(record_id: str, background_tasks: BackgroundTasks):
    """Trigger (re)assessment of a mail record."""
    db = get_db()

    def _run():
        from orivellum.capabilities.mail.steward import assess_message
        cfg = get_config()
        try:
            assess_message(db, cfg, record_id)
        except Exception as exc:
            logger.exception("mail.assess background error: %s", exc)

    background_tasks.add_task(_run)
    return {"status": "assessment_started"}


# ── Send-nonce issuance ────────────────────────────────────────────────────────

@router.post("/decisions/{record_id}/send-nonce")
def issue_send_nonce(record_id: str):
    """Issue a single-use send nonce for an existing draft action."""
    db = get_db()
    send_enabled = db.get_setting("mail_steward.send_enabled", "false") == "true"
    if not send_enabled:
        raise HTTPException(403, "FEATURE_DISABLED")
    from orivellum.database.mail_store import MailStore
    store = MailStore(db)
    nonce = store.issue_nonce(record_id, "SEND")
    return {"nonce": nonce}


# ── Serialisation helpers (no Graph IDs in responses) ─────────────────────────

def _safe_record(r: dict) -> dict:
    """Return only display-safe fields — never encrypted Graph IDs."""
    return {
        "id":              r.get("id"),
        "subject":         r.get("subject"),
        "sender_name":     r.get("sender_name"),
        "sender_domain":   r.get("sender_domain"),
        "received_at":     r.get("received_at"),
        "has_attachments": bool(r.get("has_attachments")),
        "importance":      r.get("importance"),
        "is_read":         bool(r.get("is_read")),
        "lifecycle_state": r.get("lifecycle_state"),
        "assessment_id":   r.get("assessment_id"),
        "action_request_id": r.get("action_request_id"),
        # Denormed from assessment join (may be None)
        "attention_level": r.get("attention_level"),
        "needs_reply":     bool(r.get("needs_reply")) if r.get("needs_reply") is not None else None,
        "recommended_action": r.get("recommended_action"),
        "confidence":      r.get("confidence"),
        "is_high_risk":    bool(r.get("is_high_risk")) if r.get("is_high_risk") is not None else None,
    }


def _safe_assessment(a: dict) -> dict:
    return {
        "id":               a.get("id"),
        "attention_level":  a.get("attention_level"),
        "needs_reply":      bool(a.get("needs_reply")),
        "rationale":        a.get("rationale"),
        "suggested_reply":  a.get("suggested_reply"),
        "recommended_action": a.get("recommended_action"),
        "confidence":       a.get("confidence"),
        "is_high_risk":     bool(a.get("is_high_risk")),
        "injection_flagged": bool(a.get("injection_flagged")),
        "model_id":         a.get("model_id"),
        "signals":          json.loads(a.get("signals_json") or "[]"),
        "created_at":       a.get("created_at"),
    }


def _safe_audit(e: dict) -> dict:
    return {
        "id":               e.get("id"),
        "at":               e.get("at"),
        "actor":            e.get("actor"),
        "event_type":       e.get("event_type"),
        "policy_version":   e.get("policy_version"),
        "model_id":         e.get("model_id"),
        "signals":          json.loads(e.get("signals_json") or "[]"),
        "result":           e.get("result"),
    }
