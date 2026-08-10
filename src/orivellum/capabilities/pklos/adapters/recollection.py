"""Adapter 4 — User recollection / conversational assertion (spec §6.4).

Everything the user states enters as A7 USER_ASSERTED, verification_status: pending.
Verified against a higher tier when a path exists; otherwise carried forward as
an assertion, never promoted.

This adapter:
  1. Wraps the capture_stamp system to formalize every user assertion
  2. Returns Evidence records for the claim ledger
  3. Never promotes A7 to a higher tier (only the verifier can do that)

Authority: A7 — User recollection / conversational assertion
"""

from __future__ import annotations

import logging
from typing import Any

from .base import AdapterBase, Evidence, Recipe

logger = logging.getLogger("orivellum.pklos.adapters.recollection")

# Predicates this adapter can contribute A7 evidence for
# (any user-assertable hardware/system fact)
_CAPABILITIES: list[str] = [
    "ram_gb",
    "vram_gb",
    "cpu_model",
    "cpu_cores",
    "cpu_threads",
    "gpu_model",
    "storage_total_tb",
    "storage_total_gb",
    "os_name",
    "os_version",
    "os_build",
    "installed_models",
    "display_resolution",
    "display_refresh_hz",
    "motherboard_model",
    "psu_watts",
    "network_adapter",
    "installed_software",
    "programming_language",
    "framework_version",
    # Generic: any predicate the user asserts
    "*",
]


class RecollectionAdapter(AdapterBase):
    """Adapter 4: turns user-stated facts into canonical A7 evidence records.

    This adapter is always available — it requires no external tool.
    It reads from the claim ledger (looking for USER_ASSERTED claims)
    and wraps them as Evidence for the verifier pipeline.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def adapter_id(self) -> str:
        return "recollection@0.1.0"

    def capabilities(self) -> list[str]:
        return _CAPABILITIES

    def can_answer(self, predicate: str) -> bool:
        # This adapter can contribute A7 evidence for any predicate
        return True

    def evidence_recipe(self, predicate: str) -> Recipe:
        return Recipe(
            predicate=predicate,
            sources=["user_assertion", "claim_ledger:USER_ASSERTED"],
            minimum_authority="A7",
            minimum_corroboration=1,
            notes=(
                "A7 evidence only. If a higher-tier adapter can verify, "
                "the verifier will upgrade the claim. Otherwise it stays "
                "USER_ASSERTED and is presented with a qualifier."
            ),
        )

    def fetch(self, predicate: str, *, freshness: str = "DURABLE") -> list[Evidence]:
        """Return any USER_ASSERTED or VERIFIED claims for this predicate as Evidence."""
        try:
            claims = self._db.list_claims(
                status=None,  # include all statuses
                subject=None,
                limit=200,
            )
            # Filter to this predicate with usable statuses
            usable_statuses = {"USER_ASSERTED", "VERIFIED", "PARTIALLY_VERIFIED", "CURRENT"}
            matching = [
                c
                for c in claims
                if c.get("predicate") == predicate
                and c.get("status") in usable_statuses
                and c.get("authority_tier") != "A8"
            ]
            return [
                Evidence(
                    source_type="user_assertion",
                    source_locator=f"claim_ledger:{c.get('id', '')[:12]}",
                    authority=c.get("authority_tier", "A7"),
                    raw_value=c.get("value", ""),
                    predicate=predicate,
                    subject=c.get("subject", "device:a01"),
                    captured_at=c.get("updated_at", ""),
                    meta={"claim_id": c.get("id", ""), "status": c.get("status", "")},
                )
                for c in matching
            ]
        except Exception as exc:
            logger.debug("RecollectionAdapter.fetch failed (non-fatal): %s", exc)
            return []

    def capture_assertion(
        self,
        subject: str,
        predicate: str,
        raw_value: str,
        *,
        conv_id: str | None = None,
        evidence_text: str | None = None,
    ) -> str:
        """Capture a user assertion into the claim ledger as A7 USER_ASSERTED.

        Returns the claim_id.
        """
        return self._db.upsert_claim(
            subject,
            predicate,
            raw_value,
            authority_tier="A7",
            conv_id=conv_id,
            evidence_text=evidence_text,
        )
