"""A-01 Mail Steward facade — sync, assess, draft, send, and move.

Entry points called by the API layer and nightshift:
  sync_mail(db, cfg)                     — delta sync all configured folders
  assess_message(db, cfg, record_id)     — full pipeline: Graph fetch → threat → LLM
  create_draft(db, record_id, nonce)     — compose an Outlook reply draft
  send_draft(db, action_request_id, nonce) — send a composed draft (send gate required)
  move_message(db, record_id, dest_enc, nonce) — reversible move
  undo_move(db, action_request_id, nonce) — reverse a move
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from orivellum.capabilities.mail.models import (
    MailStewardError, ACTION_CREATE_DRAFT, ACTION_MOVE, ACTION_UNDO_MOVE, ACTION_SEND,
)

logger = logging.getLogger("orivellum.mail.steward")

_DEFAULT_REVIEW_FOLDER = "A-01 Review"


# ─── Token helpers ─────────────────────────────────────────────────────────────

def _get_fresh_client(db: Any):
    """Return a GraphClient with a live access token, or None if not connected."""
    from orivellum.capabilities.mail.token_vault import load_token, store_token
    from orivellum.capabilities.mail.oauth import refresh_access_token
    from orivellum.capabilities.mail.graph_client import GraphClient

    token_data = load_token(db)
    if not token_data:
        return None

    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_at    = float(token_data.get("expires_at", 0))

    if time.time() >= expires_at - 120:
        if not refresh_token:
            logger.warning("steward: token expired and no refresh_token stored")
            return None
        try:
            new_data = refresh_access_token(refresh_token)
            new_data["expires_at"] = time.time() + float(new_data.get("expires_in", 3600))
            store_token(db, new_data)
            access_token = new_data["access_token"]
        except MailStewardError as exc:
            logger.warning("steward: token refresh failed: %s", exc)
            return None

    return GraphClient(access_token)


def _ensure_review_folder(client: Any, db: Any) -> str:
    """Return the Graph folder ID for 'A-01 Review', creating it if needed."""
    cached = db.get_setting("mail_steward.review_folder_id", "")
    if cached:
        return cached

    folders = client.list_mail_folders()
    for f in folders:
        if f.get("displayName", "").lower() == _DEFAULT_REVIEW_FOLDER.lower():
            fid = f["id"]
            db._set_setting("mail_steward.review_folder_id", fid)
            return fid

    new_folder = client.create_mail_folder(_DEFAULT_REVIEW_FOLDER)
    fid = new_folder["id"]
    db._set_setting("mail_steward.review_folder_id", fid)
    return fid


# ─── Sync ──────────────────────────────────────────────────────────────────────

def sync_mail(db: Any, cfg: Any) -> dict[str, Any]:
    """Run one delta-sync pass for all configured mail folders.

    Called by nightshift every cycle when mail is connected.
    """
    if db.get_setting("mail_steward.connected", "false") != "true":
        return {"skipped": True, "reason": "not_connected"}

    client = _get_fresh_client(db)
    if client is None:
        db._set_setting("mail_steward.connected", "false")
        return {"error": "token_unavailable"}

    from orivellum.database.mail_store import MailStore
    store = MailStore(db)

    folders_raw = db.get_setting("mail_steward.sync_folders", '["inbox"]')
    try:
        folders = json.loads(folders_raw)
    except json.JSONDecodeError:
        folders = ["inbox"]

    summary: dict[str, Any] = {"folders": [], "new": 0, "updated": 0, "errors": 0}

    for folder_name in folders:
        try:
            folder_id = client.get_folder_id(folder_name)
            if not folder_id:
                logger.warning("steward.sync: folder not found: %s", folder_name)
                continue

            delta_link = store.get_delta_link(folder_id)
            result = _sync_folder(client, store, folder_id, delta_link)
            summary["folders"].append({"folder": folder_name, **result})
            summary["new"]     += result.get("new", 0)
            summary["updated"] += result.get("updated", 0)

        except Exception as exc:
            from orivellum.capabilities.mail.graph_client import GraphGoneError
            if isinstance(exc, GraphGoneError):
                logger.warning("steward.sync: delta link expired for %s — resetting", folder_name)
                fid = client.get_folder_id(folder_name) or ""
                if fid:
                    store.clear_delta_link(fid)
            else:
                logger.warning("steward.sync: error syncing %s: %s", folder_name, exc)
            summary["errors"] += 1

    return summary


def _sync_folder(client: Any, store: Any, folder_id: str, delta_link: str | None) -> dict[str, Any]:
    """Sync all pages for one folder; persist only the final delta link."""
    new_count = updated_count = 0
    continuation = delta_link
    final_delta: str | None = None

    while True:
        page = client.delta_page(folder_id, continuation)
        for msg in page.get("value", []):
            if msg.get("@removed"):
                store.mark_record_deleted(msg.get("id", ""))
                continue
            if store.upsert_mail_record(msg, folder_id):
                new_count += 1
            else:
                updated_count += 1

        next_link  = page.get("@odata.nextLink")
        delta_link = page.get("@odata.deltaLink")
        if delta_link:
            final_delta = delta_link
            break
        if next_link:
            continuation = next_link
        else:
            break

    if final_delta:
        store.save_delta_link(folder_id, final_delta)

    return {"new": new_count, "updated": updated_count}


# ─── Assessment ────────────────────────────────────────────────────────────────

def assess_message(db: Any, cfg: Any, mail_record_id: str) -> dict[str, Any]:
    """Full pipeline: fetch from Graph → threat intel → Lemonade → persist."""
    from orivellum.database.mail_store import MailStore
    from orivellum.capabilities.mail import threat_intel
    from orivellum.capabilities.mail import lemonade_analyzer as lemonade

    store  = MailStore(db)
    record = store.get_mail_record(mail_record_id)
    if not record:
        raise MailStewardError(f"MailRecord {mail_record_id} not found")

    client = _get_fresh_client(db)
    if not client:
        raise MailStewardError("Not connected to Microsoft Graph")

    from orivellum.capabilities.mail.token_vault import decrypt_str
    graph_msg_id = decrypt_str(record["graph_message_id_enc"])
    message = client.get_message(graph_msg_id)

    body_obj  = message.get("body") or {}
    body_text = re.sub(r"<[^>]+>", " ", body_obj.get("content", "") if isinstance(body_obj, dict) else "")

    threat_evidence = threat_intel.inspect_message(body_text)

    lemonade_url   = db.get_setting("mail_steward.lemonade_url", "http://127.0.0.1:13305/api/v1")
    lemonade_model = db.get_setting("mail_steward.lemonade_model", "")
    assessment = lemonade.analyze(
        message, threat_evidence,
        lemonade_url=lemonade_url, model_id=lemonade_model,
    )

    assessment_id = str(uuid.uuid4())
    store.create_assessment(assessment_id, mail_record_id, assessment, threat_evidence)
    store.update_lifecycle(mail_record_id, "ASSESSED", assessment_id=assessment_id)
    store.create_audit_event(mail_record_id, "ASSESSMENT_CREATED",
                             signals=assessment.signals, model_id=assessment.model_id)

    return {
        "assessment_id":    assessment_id,
        "attention_level":  assessment.attention_level,
        "needs_reply":      assessment.needs_reply,
        "recommended_action": assessment.recommended_action,
        "confidence":       assessment.confidence,
        "is_high_risk":     assessment.is_high_risk,
        "injection_flagged": assessment.injection_flagged,
    }


# ─── Draft create ──────────────────────────────────────────────────────────────

def create_draft(db: Any, mail_record_id: str, nonce: str) -> dict[str, Any]:
    """Create an Outlook reply draft pre-populated with the suggested reply.

    The user reviews and edits it in the compose view before sending.
    Nothing is sent automatically.
    """
    from orivellum.database.mail_store import MailStore
    from orivellum.capabilities.mail.token_vault import decrypt_str, encrypt_str
    from orivellum.capabilities.mail import action_policy as policy

    store  = MailStore(db)
    record = store.get_mail_record(mail_record_id)
    if not record:
        raise MailStewardError("MailRecord not found")

    assessment_row = store.get_latest_assessment(mail_record_id)
    if not assessment_row:
        raise MailStewardError("Message has not been assessed yet")

    if not store.consume_nonce(nonce, mail_record_id, ACTION_CREATE_DRAFT):
        raise MailStewardError("Invalid or already-used approval nonce")

    decision = policy.evaluate(
        ACTION_CREATE_DRAFT,
        explicit_user_approval=True,
        is_high_risk=bool(assessment_row.get("is_high_risk")),
        confidence=float(assessment_row.get("confidence", 0.0)),
    )
    if not decision.allowed:
        store.create_audit_event(mail_record_id, "ACTION_REJECTED", result="REJECTED")
        raise MailStewardError(decision.reason)

    client = _get_fresh_client(db)
    if not client:
        raise MailStewardError("Not connected to Microsoft Graph")

    graph_msg_id  = decrypt_str(record["graph_message_id_enc"])
    draft_created = client.create_reply_draft(graph_msg_id)
    draft_id      = draft_created.get("id", "")
    if not draft_id:
        raise MailStewardError("Graph did not return a draft ID")

    suggested = assessment_row.get("suggested_reply") or ""
    if suggested.strip():
        client.update_draft(draft_id, suggested)

    action_id = store.create_action_request(
        mail_record_id=mail_record_id,
        assessment_id=assessment_row["id"],
        action_type=ACTION_CREATE_DRAFT,
        graph_draft_id_enc=encrypt_str(draft_id),
        status="APPLIED",
    )

    store.update_lifecycle(mail_record_id, "ACTION_APPLIED", action_request_id=action_id)
    store.create_audit_event(mail_record_id, "ACTION_APPLIED",
                             model_id=assessment_row.get("model_id", ""),
                             after={"draft_created": True})

    # Issue a send nonce so the compose view can display it immediately
    send_nonce = store.issue_nonce(mail_record_id, ACTION_SEND) if (
        db.get_setting("mail_steward.send_enabled", "false") == "true"
    ) else None

    return {
        "action_request_id": action_id,
        "suggested_reply":   suggested,
        "subject":           record.get("subject", ""),
        "send_nonce":        send_nonce,
    }


# ─── Send ──────────────────────────────────────────────────────────────────────

def send_draft(db: Any, action_request_id: str, nonce: str) -> dict[str, Any]:
    """Send a composed Outlook draft.

    Requires mail_steward.send_enabled = true and a valid single-use nonce.
    """
    from orivellum.database.mail_store import MailStore
    from orivellum.capabilities.mail.token_vault import decrypt_str
    from orivellum.capabilities.mail import action_policy as policy

    send_enabled = db.get_setting("mail_steward.send_enabled", "false") == "true"
    decision = policy.evaluate(
        ACTION_SEND,
        explicit_user_approval=True,
        is_high_risk=False,
        confidence=1.0,
        send_enabled=send_enabled,
    )
    if not decision.allowed:
        raise MailStewardError(decision.reason)

    store  = MailStore(db)
    action = store.get_action_request(action_request_id)
    if not action:
        raise MailStewardError("Action request not found")
    if action["action_type"] != ACTION_CREATE_DRAFT:
        raise MailStewardError("Can only send from a CREATE_DRAFT action")
    if action["status"] != "APPLIED":
        raise MailStewardError("Draft has not been created yet")

    if not store.consume_nonce(nonce, action["mail_record_id"], ACTION_SEND):
        raise MailStewardError("Invalid or already-used send nonce")

    client = _get_fresh_client(db)
    if not client:
        raise MailStewardError("Not connected to Microsoft Graph")

    draft_id = decrypt_str(action.get("graph_draft_id_enc", ""))
    if not draft_id:
        raise MailStewardError("Draft ID not found in action request")

    client.send_draft(draft_id)
    store.update_action_status(action_request_id, "SENT")
    store.update_lifecycle(action["mail_record_id"], "VERIFIED")
    store.create_audit_event(action["mail_record_id"], "ACTION_APPLIED",
                             after={"sent": True})

    return {"sent": True, "action_request_id": action_request_id}


# ─── Move ──────────────────────────────────────────────────────────────────────

def move_message(db: Any, mail_record_id: str, destination_folder_id_enc: str, nonce: str) -> dict[str, Any]:
    """Reversible move to a folder."""
    from orivellum.database.mail_store import MailStore
    from orivellum.capabilities.mail.token_vault import decrypt_str, encrypt_str

    store  = MailStore(db)
    if not store.consume_nonce(nonce, mail_record_id, ACTION_MOVE):
        raise MailStewardError("Invalid or already-used move nonce")

    record = store.get_mail_record(mail_record_id)
    if not record:
        raise MailStewardError("MailRecord not found")

    client = _get_fresh_client(db)
    if not client:
        raise MailStewardError("Not connected to Microsoft Graph")

    graph_msg_id    = decrypt_str(record["graph_message_id_enc"])
    original_folder = record.get("graph_folder_id_enc", "")  # already encrypted
    dest_folder_id  = decrypt_str(destination_folder_id_enc)

    result     = client.move_message(graph_msg_id, dest_folder_id)
    new_msg_id = result.get("id", "")
    if not new_msg_id:
        raise MailStewardError("Graph did not return new message ID after move")

    action_id = store.create_action_request(
        mail_record_id=mail_record_id,
        assessment_id=None,
        action_type=ACTION_MOVE,
        destination_folder_id_enc=destination_folder_id_enc,
        original_folder_id_enc=original_folder,
        result_message_id_enc=encrypt_str(new_msg_id),
        status="APPLIED",
    )

    store.update_record_after_move(
        mail_record_id,
        new_graph_id_enc=encrypt_str(new_msg_id),
        new_folder_id_enc=destination_folder_id_enc,
        action_request_id=action_id,
    )
    store.create_audit_event(mail_record_id, "ACTION_APPLIED",
                             before={"folder": "original"}, after={"folder": "moved"})

    return {"action_request_id": action_id, "moved": True}


# ─── Undo move ─────────────────────────────────────────────────────────────────

def undo_move(db: Any, action_request_id: str, nonce: str) -> dict[str, Any]:
    """Reverse a previous move operation."""
    from orivellum.database.mail_store import MailStore
    from orivellum.capabilities.mail.token_vault import decrypt_str, encrypt_str

    store  = MailStore(db)
    action = store.get_action_request(action_request_id)
    if not action:
        raise MailStewardError("Action request not found")
    if action["action_type"] != ACTION_MOVE:
        raise MailStewardError("Only MOVE actions can be undone")
    if action["status"] != "APPLIED":
        raise MailStewardError("Action has not been applied")

    if not store.consume_nonce(nonce, action["mail_record_id"], ACTION_UNDO_MOVE):
        raise MailStewardError("Invalid or already-used undo nonce")

    client = _get_fresh_client(db)
    if not client:
        raise MailStewardError("Not connected to Microsoft Graph")

    current_msg_id  = decrypt_str(action.get("result_message_id_enc", ""))
    original_folder = decrypt_str(action.get("original_folder_id_enc", ""))
    if not current_msg_id or not original_folder:
        raise MailStewardError("Undo data is incomplete — original folder not recorded")

    result     = client.move_message(current_msg_id, original_folder)
    new_msg_id = result.get("id", "")

    store.update_action_status(action_request_id, "REVERSED")
    store.update_record_after_move(
        action["mail_record_id"],
        new_graph_id_enc=encrypt_str(new_msg_id),
        new_folder_id_enc=encrypt_str(original_folder),
        action_request_id=None,
    )
    store.create_audit_event(action["mail_record_id"], "ACTION_REVERSED",
                             after={"undone": True})

    return {"undone": True}
