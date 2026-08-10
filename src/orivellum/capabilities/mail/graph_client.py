"""Microsoft Graph HTTP client for the A-01 Mail Steward.

Covers all operations from IMPLEMENTATION-SPECIFICATION §6:
  delta sync, message fetch, attachment inventory, draft create/update,
  move, undo-move, and (when send_enabled) send.

Never logs access tokens, refresh tokens, raw attachment content,
or full message bodies in error paths.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from orivellum.capabilities.mail.models import MailStewardError

logger = logging.getLogger("orivellum.mail.graph")

_BASE = "https://graph.microsoft.com/v1.0/me"
_TIMEOUT = 30


class GraphClient:
    """Thin authenticated wrapper around the Microsoft Graph mail API."""

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(url, headers=self._headers(), params=params)
        self._raise(r)
        return r.json()

    def _post(self, url: str, body: dict | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(url, headers=self._headers(), json=body or {})
        self._raise(r)
        return r.json() if r.content else {}

    def _patch(self, url: str, body: dict) -> dict[str, Any]:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.patch(url, headers=self._headers(), json=body)
        self._raise(r)
        return r.json() if r.content else {}

    def _delete(self, url: str) -> None:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.delete(url, headers=self._headers())
        self._raise(r)

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code == 429:
            retry = int(response.headers.get("Retry-After", "10"))
            logger.warning("Graph rate-limited; sleeping %ds", retry)
            time.sleep(retry)
            raise MailStewardError("Graph rate limit — retry the operation")
        if response.status_code == 410:
            raise GraphGoneError("Delta link expired (410 Gone) — full resync required")
        if not response.is_success:
            raise MailStewardError(
                f"Graph {response.status_code}: "
                f"{response.text[:200] if response.content else '(empty)'}"
            )

    # ── User profile ───────────────────────────────────────────────────────────

    def get_me(self) -> dict[str, Any]:
        return self._get(f"{_BASE.replace('/me', '')}/me")

    # ── Folder helpers ─────────────────────────────────────────────────────────

    def list_mail_folders(self) -> list[dict[str, Any]]:
        data = self._get(f"{_BASE}/mailFolders", {"$top": "50"})
        return data.get("value", [])

    def create_mail_folder(self, display_name: str) -> dict[str, Any]:
        return self._post(f"{_BASE}/mailFolders", {"displayName": display_name})

    def get_folder_id(self, well_known_name: str) -> str | None:
        """Resolve inbox/sentitems/deleteditems/junkemail well-known names."""
        try:
            data = self._get(f"{_BASE}/mailFolders/{well_known_name}")
            return data.get("id")
        except MailStewardError:
            return None

    # ── Delta sync ─────────────────────────────────────────────────────────────

    def delta_page(
        self,
        folder_id: str,
        continuation: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one page of the delta query.

        Pass the stored deltaLink (or nextLink) as `continuation`.
        If None, starts a fresh delta query for the folder.
        """
        if continuation:
            url = continuation  # opaque MS URL — use as-is
            params = None
        else:
            url = f"{_BASE}/mailFolders/{folder_id}/messages/delta"
            params = {
                "$select": (
                    "id,conversationId,subject,sender,from,toRecipients,"
                    "receivedDateTime,hasAttachments,importance,isRead,"
                    "internetMessageHeaders,changeKey,parentFolderId"
                ),
                "$top": "50",
            }
        return self._get(url, params)

    # ── Message detail ─────────────────────────────────────────────────────────

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Fetch full message with text body and internet headers."""
        return self._get(
            f"{_BASE}/messages/{message_id}",
            {
                "$select": (
                    "id,conversationId,subject,sender,from,toRecipients,ccRecipients,"
                    "receivedDateTime,hasAttachments,importance,isRead,body,"
                    "internetMessageHeaders,changeKey,parentFolderId"
                ),
                "$expand": "singleValueExtendedProperties",
            },
        )

    # ── Attachments ────────────────────────────────────────────────────────────

    def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        """List attachment metadata only — no content yet."""
        data = self._get(
            f"{_BASE}/messages/{message_id}/attachments",
            {"$select": "id,name,contentType,size,isInline"},
        )
        return data.get("value", [])

    # ── Draft create + update ──────────────────────────────────────────────────

    def create_reply_draft(self, message_id: str) -> dict[str, Any]:
        """Create a reply draft in Outlook (does not send)."""
        return self._post(f"{_BASE}/messages/{message_id}/createReply")

    def update_draft(self, draft_id: str, body_text: str) -> dict[str, Any]:
        """Update the draft body (plain text)."""
        return self._patch(
            f"{_BASE}/messages/{draft_id}",
            {"body": {"contentType": "Text", "content": body_text}},
        )

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return self._get(f"{_BASE}/messages/{draft_id}")

    def update_draft_full(
        self,
        draft_id: str,
        body_text: str | None = None,
        to_recipients: list[str] | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        """Update body and/or recipients on a draft."""
        payload: dict[str, Any] = {}
        if body_text is not None:
            payload["body"] = {"contentType": "Text", "content": body_text}
        if to_recipients is not None:
            payload["toRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in to_recipients
            ]
        if subject is not None:
            payload["subject"] = subject
        return self._patch(f"{_BASE}/messages/{draft_id}", payload)

    # ── Send ───────────────────────────────────────────────────────────────────

    def send_draft(self, draft_id: str) -> None:
        """Send a draft.  Requires Mail.Send scope in the access token."""
        self._post(f"{_BASE}/messages/{draft_id}/send")

    # ── Move ───────────────────────────────────────────────────────────────────

    def move_message(self, message_id: str, destination_folder_id: str) -> dict[str, Any]:
        """Move a message.  Returns the new message resource (new ID)."""
        return self._post(
            f"{_BASE}/messages/{message_id}/move",
            {"destinationId": destination_folder_id},
        )

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete_message(self, message_id: str) -> None:
        """Permanently delete.  Disabled in v1 — only callable programmatically."""
        self._delete(f"{_BASE}/messages/{message_id}")


class GraphGoneError(MailStewardError):
    """Raised when the Graph delta link has expired (HTTP 410)."""
