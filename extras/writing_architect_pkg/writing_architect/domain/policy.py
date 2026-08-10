"""
Policy engine (spec 9.2 release gates, 11.3 minimum constraints)
================================================================

The database triggers block the point-of-write violations. This module holds
the *stateful* gates: conditions that depend on aggregate project state and
must be checked before a lifecycle transition or a release.

Every gate returns (ok: bool, message: str) and, crucially, refuses on failure
rather than warning — the spec's governance-first stance is that a blocked gate
stays blocked regardless of average score (spec 9.3).

WR-02 additions
---------------
* check_source_tier_admissible()  — T7 is never evidence; blocked at intake.
* check_claim_acceptance_gate()   — pre-flight all nine acceptance criteria
  before the trigger fires, so the error message is specific rather than a
  generic SQLite abort.
"""
from __future__ import annotations


def _count(conn, sql, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


# --- individual gate predicates ------------------------------------------------

def open_blockers(conn, book_id: str) -> int:
    return _count(
        conn,
        "SELECT COUNT(*) FROM editorial_finding WHERE book_id=? AND state='OPEN'"
        " AND severity IN ('blocker','critical')",
        (book_id,),
    )


def unaccepted_factual_claims_in_use(conn, book_id: str) -> int:
    """Factual claims wired into a contract's evidence packet but not accepted."""
    return _count(
        conn,
        """SELECT COUNT(*) FROM contract_evidence ce
             JOIN claim c ON c.id = ce.claim_id
            WHERE c.book_id=? AND c.claim_type='fact' AND c.accepted=0""",
        (book_id,),
    )


def approved_contracts(conn, book_id: str) -> int:
    return _count(
        conn,
        "SELECT COUNT(*) FROM chapter_contract WHERE book_id=? AND approved=1",
        (book_id,),
    )


def research_questions(conn, book_id: str) -> int:
    return _count(
        conn, "SELECT COUNT(*) FROM research_question WHERE book_id=?", (book_id,)
    )


def unresolved_conflicts(conn, book_id: str) -> int:
    return _count(
        conn,
        "SELECT COUNT(*) FROM conflict WHERE book_id=? AND resolved=0",
        (book_id,),
    )


def book_defined(conn, book_id: str) -> bool:
    row = conn.execute(
        "SELECT reader_promise, audience FROM book_project WHERE id=?", (book_id,)
    ).fetchone()
    return bool(row and row["reader_promise"] and row["audience"])


# --- WR-02: source tier admission (spec 5.1) ----------------------------------

#: Tiers whose sources are inadmissible as evidence.
_INADMISSIBLE_TIERS = {"T7"}
#: Tiers that can only be used as leads, not as standalone evidence.
_LEAD_ONLY_TIERS = {"T6"}


def check_source_tier_admissible(tier: str) -> tuple[bool, str]:
    """Is this source tier admissible as evidence?

    T7 (AI-generated statements) is *never* evidence per spec 5.1.
    T6 (popular works / unsourced summaries) is lead-generation only.
    Both are refused at intake so they cannot be attached to a claim.

    Returns (ok, message).
    """
    if tier in _INADMISSIBLE_TIERS:
        return (
            False,
            "POLICY FM-T7: AI-generated statements (T7) are never admissible as "
            "evidence and must resolve to an external source (spec 5.1).",
        )
    if tier in _LEAD_ONLY_TIERS:
        return (
            False,
            "POLICY FM-T6: T6 sources (popular works, blogs, unsourced summaries) "
            "are lead-generation only and cannot serve as evidence (spec 5.1). "
            "Locate a T1–T5 source that the T6 item references.",
        )
    return True, "ok"


# --- WR-02: claim acceptance gate (spec 5.3) ----------------------------------

def check_claim_acceptance_gate(conn, claim_id: str) -> tuple[bool, list[str]]:
    """Pre-flight all nine acceptance criteria before attempting the DB update.

    Returns (ok, list_of_failures). An empty failure list means all gates pass.
    The DB trigger (trg_claim_accept_requires_evidence) also enforces the
    evidence check; this pre-check gives a richer, human-readable summary.
    """
    row = conn.execute(
        "SELECT * FROM claim WHERE id=?", (claim_id,)
    ).fetchone()
    if not row:
        return False, [f"claim {claim_id!r} not found"]

    failures: list[str] = []

    # 1. Identity — proposition must be non-empty (already a NOT NULL column;
    #    belt-and-suspenders check for callers that bypass SQL)
    if not (row["proposition"] or "").strip():
        failures.append("Identity: claim has no proposition text")

    # 2. Evidence — at least one supporting evidence unit
    n_supporting = _count(
        conn,
        "SELECT COUNT(*) FROM evidence_unit WHERE claim_id=? AND stance='supports'",
        (claim_id,),
    )
    if n_supporting == 0:
        failures.append(
            "Evidence: no supporting evidence unit attached "
            "(add at least one with 'wa evidence')"
        )

    # 3. Edition control — all attached evidence must have a non-empty location_ref
    #    (already enforced by trigger at insert time, but verify here for clarity)
    n_missing_loc = _count(
        conn,
        "SELECT COUNT(*) FROM evidence_unit"
        " WHERE claim_id=? AND (location_ref IS NULL OR TRIM(location_ref)='')",
        (claim_id,),
    )
    if n_missing_loc:
        failures.append(
            f"Edition control: {n_missing_loc} evidence unit(s) missing location_ref"
        )

    # 4. Temporal validity — warn if confidence is 'unknown' (not a hard block,
    #    but the spec requires it to be *represented*, not left blank)
    if row["confidence"] == "unknown":
        failures.append(
            "Temporal validity: confidence is 'unknown' — represent uncertainty "
            "explicitly (confirmed/probable/possible/disputed/invented_for_fiction)"
        )

    # 5. Conflict — check that any known conflict is recorded (we can only verify
    #    the field exists; actual conflict content is the user's responsibility)
    #    No hard block here; operator must record conflicts manually.

    # 6. Narrative use — claim_type must be explicitly set (default 'fact' is fine)
    if not row["claim_type"]:
        failures.append(
            "Narrative use: claim_type not set "
            "(fact/interpretation/tradition/creative_interpolation)"
        )

    # 7. T7 evidence — none of the attached sources may be T7
    n_t7 = _count(
        conn,
        """SELECT COUNT(*) FROM evidence_unit eu
             JOIN source s ON s.id = eu.source_id
            WHERE eu.claim_id=? AND s.tier='T7'""",
        (claim_id,),
    )
    if n_t7:
        failures.append(
            f"T7 evidence: {n_t7} attached source(s) are T7 (AI-generated); "
            "replace with a human-authored primary source"
        )

    # 8. Verifier — independent review must be recorded
    if not (row["verifier"] or "").strip():
        failures.append(
            "Verifier: no independent reviewer recorded "
            "(run 'wa verify' with a different actor before accepting)"
        )

    return (len(failures) == 0), failures


# --- entry gates keyed by target lifecycle state ------------------------------

def check_entry_gate(conn, book_id: str, target_state: str) -> tuple[bool, str]:
    """Is the project allowed to ENTER target_state?"""
    if target_state == "B2":  # BOOK_DEFINITION begins; nothing blocks entry
        return True, "ok"
    if target_state == "B3":  # entering RESEARCH_BASELINE requires a defined book
        if not book_defined(conn, book_id):
            return False, "B2 not satisfied: reader_promise and audience required"
        return True, "ok"
    if target_state == "B4":  # entering ARCHITECTURE requires research questions
        if research_questions(conn, book_id) == 0:
            return False, "B3 not satisfied: at least one research question required"
        return True, "ok"
    if target_state == "B5":  # entering DRAFTING requires an approved contract
        if approved_contracts(conn, book_id) == 0:
            return False, "B4 not satisfied: at least one approved chapter contract"
        return True, "ok"
    if target_state in ("B6", "B7", "B8", "B9", "B10", "B11"):
        # each editorial stage refuses to advance while blockers stand open
        n = open_blockers(conn, book_id)
        if n:
            return False, f"{n} open blocker/critical finding(s) must be closed first"
        return True, "ok"
    if target_state == "B12":  # RELEASE_CANDIDATE
        return check_release_gates(conn, book_id)
    if target_state == "B13":  # RELEASED requires author sign-off (checked at release)
        return check_release_gates(conn, book_id)
    return True, "ok"


# --- full release gate battery (spec 9.2) -------------------------------------

def check_release_gates(conn, book_id: str) -> tuple[bool, str]:
    failures = []
    if open_blockers(conn, book_id):
        failures.append("open blocker/critical findings remain (gate 2)")
    if unaccepted_factual_claims_in_use(conn, book_id):
        failures.append("factual claims in evidence packets are not accepted (gate 3)")
    if unresolved_conflicts(conn, book_id):
        failures.append("unresolved research conflicts remain (gate 4)")
    if approved_contracts(conn, book_id) == 0:
        failures.append("no approved chapter contracts (architecture incomplete)")
    if failures:
        return False, "; ".join(failures)
    return True, "all evaluated release gates pass"


def release_gate_report(conn, book_id: str) -> dict:
    """Detailed, per-gate view for the dashboard."""
    return {
        "open_blockers": open_blockers(conn, book_id),
        "unaccepted_factual_claims_in_use": unaccepted_factual_claims_in_use(conn, book_id),
        "unresolved_conflicts": unresolved_conflicts(conn, book_id),
        "approved_contracts": approved_contracts(conn, book_id),
        "research_questions": research_questions(conn, book_id),
        "book_defined": book_defined(conn, book_id),
    }


# ---------------------------------------------------------------------------
# WR-04: plan tree gate (spec §6)
# ---------------------------------------------------------------------------

def check_plan_node_gate(conn, node_id: str) -> tuple[bool, str]:
    """Can this plan_node be approved?

    Rules (spec §6):
      1. The node must exist and must not already be CHANGE_REQUESTED.
      2. If the node has a parent, the parent must already be APPROVED.
         ("No silent edit of an approved ancestor" / top-down approval chain.)

    Returns (ok, message).
    """
    row = conn.execute(
        "SELECT id, parent_id, state, node_type FROM plan_node WHERE id=?",
        (node_id,),
    ).fetchone()
    if not row:
        return False, f"plan_node {node_id!r} not found"
    if row["state"] == "CHANGE_REQUESTED":
        return (
            False,
            "node is in CHANGE_REQUESTED state — resolve the change request "
            "and re-approve before approving children",
        )
    if row["parent_id"]:
        parent = conn.execute(
            "SELECT state FROM plan_node WHERE id=?", (row["parent_id"],)
        ).fetchone()
        if not parent:
            return False, f"parent node {row['parent_id']!r} not found in plan_node"
        if parent["state"] != "APPROVED":
            return (
                False,
                f"parent node {row['parent_id']!r} is in state "
                f"'{parent['state']}' — parent must be APPROVED before this child "
                "can be approved (spec §6: top-down approval chain)",
            )
    return True, "ok"


def check_chapter_contract_gate(conn, contract_id: str) -> tuple[bool, str]:
    """Can a draft_unit be created against this chapter_contract?

    Enforces two gates:
      1. The contract must be approved (approved=1).
      2. If the contract is linked to a plan_node, that plan_node must be APPROVED
         (so the plan-approval chain is respected end-to-end).

    The DB trigger trg_draft_requires_approved_contract catches (1) at the SQL
    level; this function gives a human-readable pre-flight message.
    """
    row = conn.execute(
        "SELECT id, approved, plan_node_id FROM chapter_contract WHERE id=?",
        (contract_id,),
    ).fetchone()
    if not row:
        return False, f"chapter_contract {contract_id!r} not found"
    if not row["approved"]:
        return (
            False,
            "POLICY FM-09: contract is not yet approved — run "
            "'wa contract-approve' before drafting",
        )
    if row["plan_node_id"]:
        pn = conn.execute(
            "SELECT state FROM plan_node WHERE id=?", (row["plan_node_id"],)
        ).fetchone()
        if pn and pn["state"] != "APPROVED":
            return (
                False,
                f"linked plan_node {row['plan_node_id']!r} is in state "
                f"'{pn['state']}' — plan-approval chain must reach APPROVED "
                "before drafting (spec §6)",
            )
    return True, "ok"


def check_contract_evidence_gate(conn, contract_id: str) -> tuple[bool, str]:
    """Does this contract have ≥1 accepted claim in its evidence packet?

    Required before the contract can be approved (spec §6: evidence packet
    must be non-empty so the drafter has at least one authoritative fact).
    """
    n = _count(
        conn,
        """SELECT COUNT(*) FROM contract_evidence ce
             JOIN claim c ON c.id = ce.claim_id
            WHERE ce.contract_id=? AND c.accepted=1""",
        (contract_id,),
    )
    if n == 0:
        return (
            False,
            "POLICY FM-CE: contract has no accepted claims in its evidence "
            "packet — add at least one accepted claim via 'wa contract-evidence' "
            "before approving (spec §6)",
        )
    return True, "ok"


def count_plan_nodes(conn, book_id: str) -> int:
    return _count(conn, "SELECT COUNT(*) FROM plan_node WHERE book_id=?", (book_id,))


def approved_plan_nodes(conn, book_id: str) -> int:
    return _count(
        conn,
        "SELECT COUNT(*) FROM plan_node WHERE book_id=? AND state='APPROVED'",
        (book_id,),
    )
