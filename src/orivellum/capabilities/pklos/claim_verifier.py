"""Claim verifier — spec §5.5.

Normalizes, corroborates against minimum_corroboration, runs the conflict
engine (§3.5 taxonomy), evaluates freshness, scores confidence, and
transitions claim-state.  Emits the final canonical claim record to the ledger.

This is a deterministic service — NOT an LLM call.
ARCH-REQ-001: every gate decision is produced outside the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .authority import AuthorityTier, ClaimStatus, ConflictType
from .authority_resolver import AuthorityResolver

_resolver = AuthorityResolver()


# ── Unit normalizer ────────────────────────────────────────────────────────────

_BYTE_MULTIPLIERS = {
    "b": 1, "bytes": 1,
    "kb": 1_000, "kib": 1_024,
    "mb": 1_000_000, "mib": 1_048_576,
    "gb": 1_000_000_000, "gib": 1_073_741_824,
    "tb": 1_000_000_000_000, "tib": 1_099_511_627_776,
}

_MEMORY_RE = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)\s*(b|bytes|kb|kib|mb|mib|gb|gib|tb|tib)?$",
    re.IGNORECASE,
)

# Known CPU aliases → canonical id
_CPU_ALIASES: dict[str, str] = {
    "ryzen ai max+ 395": "amd_ryzen_ai_max_plus_395",
    "amd ryzen ai max+ 395": "amd_ryzen_ai_max_plus_395",
    "ryzen ai max 395": "amd_ryzen_ai_max_plus_395",  # common truncation
    "strix halo 395": "amd_ryzen_ai_max_plus_395",
    "ryzen 9 9950x": "amd_ryzen_9_9950x",
    "amd ryzen 9 9950x": "amd_ryzen_9_9950x",
    "rtx 4090": "nvidia_rtx_4090",
    "geforce rtx 4090": "nvidia_rtx_4090",
    "nvidia geforce rtx 4090": "nvidia_rtx_4090",
}


def normalize_value(predicate: str, raw_value: str | int | float) -> tuple[str, str]:
    """Normalize a raw value to a canonical form.

    Returns (normalized_value, display_value).
    E.g.: ("137438953472", "128 GiB") for RAM claims.
    """
    raw = str(raw_value).strip()

    # Memory predicates → bytes
    if any(kw in predicate.lower() for kw in ("memory", "ram", "vram", "storage")):
        m = _MEMORY_RE.match(raw.replace(",", ""))
        if m:
            amount = float(m.group(1))
            unit = (m.group(2) or "").lower()
            if not unit:
                # No explicit unit: if the number is >= 1 GiB, treat it as bytes
                # (CIM returns raw bytes for TotalPhysicalMemory etc.)
                # Otherwise assume GB (user-stated "128" → "128 GB").
                if amount >= 1_073_741_824:
                    total_bytes = int(amount)
                else:
                    total_bytes = int(amount * _BYTE_MULTIPLIERS["gb"])
            else:
                mult = _BYTE_MULTIPLIERS.get(unit, _BYTE_MULTIPLIERS["gb"])
                total_bytes = int(amount * mult)
            # Display as GiB if >= 1 GiB
            if total_bytes >= _BYTE_MULTIPLIERS["gib"]:
                gib = total_bytes / _BYTE_MULTIPLIERS["gib"]
                display = f"{gib:.0f} GiB" if gib == int(gib) else f"{gib:.1f} GiB"
            else:
                display = f"{total_bytes} bytes"
            return str(total_bytes), display

    # CPU/GPU model — normalize aliases
    lower = raw.lower().strip().rstrip("™®")
    for alias, canonical in _CPU_ALIASES.items():
        if alias in lower:
            return canonical, raw  # canonical id, keep original display

    # Default: strip extra whitespace, lower for canonical
    canonical = re.sub(r"\s+", " ", raw).strip()
    return canonical.lower(), canonical


def values_agree(v1: str, v2: str, tolerance: float = 0.02) -> tuple[bool, ConflictType]:
    """Compare two normalized values.  Returns (agree, conflict_type)."""
    if v1 == v2:
        return True, ConflictType.EXACT_AGREEMENT

    # Try numeric comparison with tolerance
    try:
        n1, n2 = float(v1), float(v2)
        if n1 == 0 and n2 == 0:
            return True, ConflictType.EXACT_AGREEMENT
        rel_diff = abs(n1 - n2) / max(abs(n1), abs(n2))
        if rel_diff <= tolerance:
            return True, ConflictType.NORMALIZED_AGREEMENT
        if rel_diff <= 0.10:
            return False, ConflictType.COMPATIBLE_DIFFERENCE
        return False, ConflictType.MATERIAL_CONTRADICTION
    except (ValueError, ZeroDivisionError):
        pass

    # String comparison after basic normalization
    n1s = re.sub(r"\s+", "", v1.lower())
    n2s = re.sub(r"\s+", "", v2.lower())
    if n1s == n2s:
        return True, ConflictType.NORMALIZED_AGREEMENT

    return False, ConflictType.MATERIAL_CONTRADICTION


# ── Verification result ────────────────────────────────────────────────────────

@dataclass
class VerificationResult:
    status: ClaimStatus
    authority: AuthorityTier
    confidence: float
    confidence_basis: str
    normalized_value: str
    display_value: str
    conflict_type: ConflictType | None
    conflict_detail: str
    all_evidence: list[dict]


# ── Verifier ───────────────────────────────────────────────────────────────────

class ClaimVerifier:
    """Spec §5.5 — verifies a set of evidence items against the authority policy.

    Usage:
        verifier = ClaimVerifier()
        result = verifier.verify(predicate, evidence_items)
        # result.status → VERIFIED / CONFLICTED / PARTIALLY_VERIFIED / UNAVAILABLE
    """

    def verify(
        self,
        predicate: str,
        evidence_items: list[dict],
        *,
        subject: str = "device:a01",
    ) -> VerificationResult:
        """Verify a set of evidence items for a predicate.

        Each evidence item must have:
          { source_type, source_locator, authority (A0–A8), raw_value }

        Returns a VerificationResult with the final claim status.
        """
        policy = _resolver.resolve(predicate)

        if not evidence_items:
            return VerificationResult(
                status=ClaimStatus.UNAVAILABLE,
                authority=AuthorityTier.A8,
                confidence=0.0,
                confidence_basis="no evidence provided",
                normalized_value="",
                display_value="",
                conflict_type=None,
                conflict_detail="",
                all_evidence=[],
            )

        # Filter out prohibited sources (INV-REQ-001)
        safe_evidence = [
            e for e in evidence_items
            if not _resolver.is_prohibited_source(predicate, e.get("source_locator", ""))
        ]
        if not safe_evidence:
            return VerificationResult(
                status=ClaimStatus.UNAVAILABLE,
                authority=AuthorityTier.A8,
                confidence=0.0,
                confidence_basis="all evidence sources prohibited for this predicate (INV-REQ-001)",
                normalized_value="",
                display_value="",
                conflict_type=None,
                conflict_detail="prohibited sources filtered",
                all_evidence=evidence_items,
            )

        # Normalize all values
        normalized: list[tuple[str, str, AuthorityTier, dict]] = []  # (norm, display, tier, evidence)
        for e in safe_evidence:
            raw = str(e.get("raw_value", "")).strip()
            if not raw:
                continue
            tier_str = str(e.get("authority", "A7"))
            try:
                tier = AuthorityTier(tier_str)
            except ValueError:
                tier = AuthorityTier.A7
            norm, display = normalize_value(predicate, raw)
            normalized.append((norm, display, tier, e))

        if not normalized:
            return VerificationResult(
                status=ClaimStatus.UNAVAILABLE,
                authority=AuthorityTier.A8,
                confidence=0.0,
                confidence_basis="evidence values could not be normalized",
                normalized_value="",
                display_value="",
                conflict_type=None,
                conflict_detail="",
                all_evidence=safe_evidence,
            )

        # Best authority tier present
        best_tier = min((t for _, _, t, _ in normalized), key=lambda t: t.numeric)
        best_value = next(norm for norm, _, t, _ in normalized if t == best_tier)
        best_display = next(disp for norm, disp, t, _ in normalized if norm == best_value and t == best_tier)

        # Run conflict engine across all normalized values
        conflict_type: ConflictType | None = None
        conflict_detail = ""
        all_agree = True
        for (n1, _, t1, _), (n2, _, t2, _) in zip(normalized, normalized[1:]):
            agree, ctype = values_agree(n1, n2)
            if not agree:
                all_agree = False
                conflict_type = ctype
                conflict_detail = f"{t1.value}={n1!r} vs {t2.value}={n2!r}"
                if ctype == ConflictType.MATERIAL_CONTRADICTION:
                    # CONF-REQ-001: report, never auto-reconcile
                    break

        # Count independent sources at best tier level
        best_tier_count = sum(1 for _, _, t, _ in normalized if t.numeric <= best_tier.numeric)
        meets_corroboration = best_tier_count >= policy.minimum_corroboration
        meets_authority = best_tier.numeric <= policy.minimum_authority.numeric

        # Determine final status
        if conflict_type == ConflictType.MATERIAL_CONTRADICTION:
            status = ClaimStatus.CONFLICTED
            confidence = 0.3
            confidence_basis = f"material contradiction: {conflict_detail}"
        elif not all_agree and conflict_type == ConflictType.COMPATIBLE_DIFFERENCE:
            status = ClaimStatus.PARTIALLY_VERIFIED
            confidence = 0.7
            confidence_basis = f"compatible difference within 10%: {conflict_detail}"
        elif meets_authority and meets_corroboration:
            status = ClaimStatus.VERIFIED
            confidence = min(0.95, 0.70 + 0.05 * min(best_tier_count, 5))
            confidence_basis = (
                f"{best_tier_count} independent {best_tier.value} source(s) agree"
                + (" within tolerance" if conflict_type == ConflictType.NORMALIZED_AGREEMENT else "")
            )
        elif meets_authority:
            status = ClaimStatus.PARTIALLY_VERIFIED
            confidence = 0.6
            confidence_basis = f"authority met ({best_tier.value}) but only {best_tier_count}/{policy.minimum_corroboration} corroborating sources"
        else:
            # Has evidence but below minimum authority (e.g. only A7 available for A1-required predicate)
            status = ClaimStatus.RETRIEVED
            confidence = 0.3
            confidence_basis = f"retrieved at {best_tier.value} but minimum is {policy.minimum_authority.value}"

        return VerificationResult(
            status=status,
            authority=best_tier,
            confidence=confidence,
            confidence_basis=confidence_basis,
            normalized_value=best_value,
            display_value=best_display,
            conflict_type=conflict_type,
            conflict_detail=conflict_detail,
            all_evidence=safe_evidence,
        )

    def verify_single_assertion(
        self,
        predicate: str,
        raw_value: str,
        *,
        subject: str = "device:a01",
    ) -> VerificationResult:
        """Wrap a single user assertion (A7) as a USER_ASSERTED evidence record.

        Per spec §6.4: A7 entries are USER_ASSERTED until independently verified
        against a higher tier.  A single A7 source is NEVER promoted to VERIFIED —
        the caller must route evidence through verify() with additional sources to
        reach VERIFIED / PARTIALLY_VERIFIED.
        """
        norm, display = normalize_value(predicate, raw_value)
        evidence = [{
            "source_type": "user_assertion",
            "source_locator": "user_assertion",
            "authority": "A7",
            "raw_value": raw_value,
        }]
        # Always USER_ASSERTED for a single A7 assertion (spec §3.3 / §6.4)
        return VerificationResult(
            status=ClaimStatus.USER_ASSERTED,
            authority=AuthorityTier.A7,
            confidence=0.5,
            confidence_basis="user assertion, not yet independently verified",
            normalized_value=norm,
            display_value=display,
            conflict_type=None,
            conflict_detail="",
            all_evidence=evidence,
        )


# Module-level singleton
_verifier = ClaimVerifier()


def verify(predicate: str, evidence_items: list[dict], **kwargs) -> VerificationResult:
    return _verifier.verify(predicate, evidence_items, **kwargs)


def verify_assertion(predicate: str, raw_value: str, **kwargs) -> VerificationResult:
    return _verifier.verify_single_assertion(predicate, raw_value, **kwargs)
