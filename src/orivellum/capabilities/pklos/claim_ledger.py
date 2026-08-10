"""Claim ledger — canonical CRUD and state machine for PKLOS Layer 0.

The claim ledger is the single source of truth for all verified facts about
the user's system, documents, and self.  Every claim has:
  - a subject (e.g. 'user_system', 'book:uuid')
  - a predicate (e.g. 'ram_gb', 'cpu_model')
  - a value (e.g. '128', 'Ryzen 9950X')
  - an authority tier (A0-A8)
  - a status (UNOBSERVED → CURRENT / STALE / CONFLICTED / UNAVAILABLE)

VER-INV-001: Claims at A8 are NEVER surfaced to the user as facts.
"""

from __future__ import annotations

import logging
from typing import Any

from .authority import AuthorityTier, ClaimStatus, TTLClass

logger = logging.getLogger("orivellum.pklos.claim_ledger")


class ClaimLedger:
    """Thin wrapper around the db connection for claim operations.

    All actual SQL lives in db.py.  This class provides a clean domain API
    that conversations.py and the capture machinery can use without importing
    db internals.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    # ── Write ──────────────────────────────────────────────────────────────────

    def capture(
        self,
        subject: str,
        predicate: str,
        value: str,
        *,
        unit: str | None = None,
        authority_tier: str | AuthorityTier = AuthorityTier.A7,
        source_id: str | None = None,
        conv_id: str | None = None,
        ttl_class: str | TTLClass = TTLClass.DURABLE,
        evidence_text: str | None = None,
        meta: dict | None = None,
    ) -> str:
        """Insert or update a claim.  Returns the claim id.

        If a CURRENT or UNOBSERVED claim already exists for (subject, predicate)
        at the same or lower authority tier, this updates it instead of creating
        a duplicate.  A higher-authority update always wins.
        """
        tier = authority_tier.value if isinstance(authority_tier, AuthorityTier) else authority_tier
        ttl = ttl_class.value if isinstance(ttl_class, TTLClass) else ttl_class

        claim_id = self._db.upsert_claim(
            subject,
            predicate,
            value,
            unit=unit,
            authority_tier=tier,
            source_id=source_id,
            conv_id=conv_id,
            ttl_class=ttl,
            evidence_text=evidence_text,
            meta=meta,
        )
        logger.debug(
            "Claim captured: %s.%s = %r (tier=%s, id=%s)",
            subject,
            predicate,
            value,
            tier,
            claim_id[:8],
        )
        return claim_id

    def transition(
        self,
        claim_id: str,
        new_status: str | ClaimStatus,
        *,
        actor: str = "system",
        reason: str | None = None,
    ) -> bool:
        """Transition a claim to a new status.  Returns True if changed."""
        status = new_status.value if isinstance(new_status, ClaimStatus) else new_status
        return self._db.update_claim_status(claim_id, status, actor=actor, reason=reason)

    def add_evidence(
        self,
        claim_id: str,
        evidence_type: str,
        content: str,
        *,
        source_id: str | None = None,
    ) -> str:
        """Attach evidence to an existing claim.  Returns evidence id."""
        return self._db.add_claim_evidence(claim_id, evidence_type, content, source_id=source_id)

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(self, claim_id: str) -> dict | None:
        return self._db.get_claim(claim_id)

    def get_by_predicate(self, subject: str, predicate: str) -> dict | None:
        """Return the most recent CURRENT claim for (subject, predicate), or None."""
        return self._db.get_claim_by_predicate(subject, predicate)

    def list_for_subject(
        self,
        subject: str,
        *,
        status: str | None = "CURRENT",
        limit: int = 50,
    ) -> list[dict]:
        """Return claims for a subject, default to CURRENT only."""
        return self._db.list_claims(subject=subject, status=status, limit=limit)

    def search_for_context(
        self,
        query: str,
        *,
        subject: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Find CURRENT claims relevant to a query string.

        Used by _build_system_prompt() to inject verified facts into chat context.
        Returns claims ranked by relevance; A8 claims are always excluded.
        """
        claims = self._db.search_claims_for_context(query, subject=subject, limit=limit)
        # Hard filter: A8 never surfaces regardless of search score
        return [c for c in claims if c.get("authority_tier") != AuthorityTier.A8.value]

    def format_for_prompt(self, claims: list[dict]) -> str:
        """Render a list of claims as a prompt block.

        Example output:
            VERIFIED FACTS (from your claim ledger):
              • ram_gb = 128 GB  [Self-reported, A7]
              • cpu_model = Ryzen 9 9950X  [Self-reported, A7]
              • gpu_model = RTX 4090  [Self-reported, A7]
        """
        if not claims:
            return ""
        lines = ["VERIFIED FACTS (from your claim ledger — use these instead of guessing):"]
        for c in claims:
            value = c.get("value", "")
            unit = c.get("unit") or ""
            pred = c.get("predicate", "")
            tier = c.get("authority_tier", "A7")
            try:
                tier_label = AuthorityTier(tier).label
            except ValueError:
                tier_label = tier
            display = f"{value} {unit}".strip()
            lines.append(f"  • {pred} = {display}  [{tier_label}, {tier}]")
        return "\n".join(lines)
