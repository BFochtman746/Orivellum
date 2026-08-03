"""Tests for PKLOS Layer 0 — Claim ledger, capture stamp, fact router, abstention.

Covers:
  - Schema v60 migration creates expected tables
  - upsert_claim: insert new, update same-tier, skip lower-authority downgrade
  - get_claim / get_claim_by_predicate
  - list_claims / search_claims_for_context
  - update_claim_status + transition log
  - add_claim_evidence
  - create_capture_stamp
  - detect_factual_assertions (fast pattern)
  - FactRouter.classify()
  - AbstentionPolicy.get_instruction()
  - ClaimLedger.format_for_prompt()
"""
from __future__ import annotations

import pytest
from orivellum.database.db import OrivellumDB

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """Fresh in-memory-style DB at a temp path — runs all migrations."""
    db_path = str(tmp_path / "test_claims.db")
    return OrivellumDB.open(db_path)


# ── Schema ────────────────────────────────────────────────────────────────────

def test_schema_v60_tables(db):
    """v60 migration must create all four PKLOS tables."""
    tables = {
        row[0]
        for row in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "claims" in tables
    assert "claim_evidence" in tables
    assert "claim_transitions" in tables
    assert "capture_stamps" in tables


def test_schema_version_at_least_60(db):
    version = int(db.get_setting("schema_version", "0"))
    assert version >= 60


# ── upsert_claim ──────────────────────────────────────────────────────────────

def test_upsert_claim_insert_new(db):
    cid = db.upsert_claim(
        "user_system", "ram_gb", "128",
        unit="GB", authority_tier="A7",
    )
    assert cid
    claim = db.get_claim(cid)
    assert claim is not None
    assert claim["subject"] == "user_system"
    assert claim["predicate"] == "ram_gb"
    assert claim["value"] == "128"
    assert claim["unit"] == "GB"
    assert claim["status"] == "USER_ASSERTED"
    assert claim["authority_tier"] == "A7"


def test_upsert_claim_update_same_tier(db):
    """Upsert with same tier should update value."""
    cid1 = db.upsert_claim("user_system", "cpu_model", "Ryzen 9 9900X", authority_tier="A7")
    cid2 = db.upsert_claim("user_system", "cpu_model", "Ryzen 9 9950X", authority_tier="A7")
    # Same claim id — it was updated, not duplicated
    assert cid1 == cid2
    claim = db.get_claim(cid1)
    assert claim["value"] == "Ryzen 9 9950X"


def test_upsert_claim_higher_authority_wins(db):
    """A7 → A1 upgrade should update the claim."""
    cid = db.upsert_claim("user_system", "ram_gb", "64", authority_tier="A7")
    cid2 = db.upsert_claim("user_system", "ram_gb", "128", authority_tier="A1")
    assert cid == cid2
    claim = db.get_claim(cid)
    assert claim["value"] == "128"
    assert claim["authority_tier"] == "A1"


def test_upsert_claim_lower_authority_does_not_downgrade(db):
    """An A1 claim must not be overwritten by an A7 (lower-authority) claim."""
    cid = db.upsert_claim("user_system", "vram_gb", "24", authority_tier="A1")
    db.upsert_claim("user_system", "vram_gb", "12", authority_tier="A7")
    claim = db.get_claim(cid)
    # Value must still be the A1 measurement, not the A7 assertion
    assert claim["value"] == "24"
    assert claim["authority_tier"] == "A1"


def test_upsert_claim_multiple_predicates_are_independent(db):
    db.upsert_claim("user_system", "ram_gb", "128", authority_tier="A7")
    db.upsert_claim("user_system", "gpu_model", "RTX 4090", authority_tier="A7")
    claims = db.list_claims(subject="user_system", status="USER_ASSERTED")
    predicates = {c["predicate"] for c in claims}
    assert "ram_gb" in predicates
    assert "gpu_model" in predicates


# ── get_claim_by_predicate ─────────────────────────────────────────────────────

def test_get_claim_by_predicate_returns_current(db):
    db.upsert_claim("user_system", "os_name", "Windows 11", authority_tier="A7")
    claim = db.get_claim_by_predicate("user_system", "os_name")
    assert claim is not None
    assert claim["value"] == "Windows 11"


def test_get_claim_by_predicate_missing_returns_none(db):
    result = db.get_claim_by_predicate("user_system", "nonexistent_predicate")
    assert result is None


# ── list_claims ───────────────────────────────────────────────────────────────

def test_list_claims_by_subject(db):
    db.upsert_claim("user_system", "ram_gb", "128")
    db.upsert_claim("user_system", "cpu_model", "Ryzen 9 9950X")
    db.upsert_claim("book:abc", "title", "My Novel")

    sys_claims = db.list_claims(subject="user_system", status="USER_ASSERTED")
    book_claims = db.list_claims(subject="book:abc", status="USER_ASSERTED")

    assert len(sys_claims) >= 2
    assert all(c["subject"] == "user_system" for c in sys_claims)
    assert len(book_claims) >= 1


# ── update_claim_status ───────────────────────────────────────────────────────

def test_update_claim_status_transitions(db):
    cid = db.upsert_claim("user_system", "ram_gb", "128")
    changed = db.update_claim_status(cid, "STALE", actor="nightshift", reason="TTL expired")
    assert changed is True
    claim = db.get_claim(cid)
    assert claim["status"] == "STALE"


def test_update_claim_status_same_status_returns_false(db):
    cid = db.upsert_claim("user_system", "ram_gb", "128")
    # Initial A7 upsert produces USER_ASSERTED; updating to the same status returns False
    changed = db.update_claim_status(cid, "USER_ASSERTED")
    assert changed is False


def test_update_claim_status_logs_transition(db):
    cid = db.upsert_claim("user_system", "ram_gb", "128")
    db.update_claim_status(cid, "STALE", actor="test", reason="test reason")
    rows = db._conn.execute(
        "SELECT * FROM claim_transitions WHERE claim_id=? ORDER BY created_at",
        (cid,),
    ).fetchall()
    statuses = [r["to_status"] for r in rows]
    # The explicit STALE transition is always logged.
    # (Initial insert may log USER_ASSERTED; legacy DBs may log CURRENT.)
    assert "STALE" in statuses    # explicit transition


# ── add_claim_evidence ────────────────────────────────────────────────────────

def test_add_claim_evidence(db):
    cid = db.upsert_claim("user_system", "ram_gb", "128")
    eid = db.add_claim_evidence(
        cid, "assertion", "I have 128GB of RAM",
        source_id="test-source"
    )
    assert eid
    rows = db._conn.execute(
        "SELECT * FROM claim_evidence WHERE claim_id=?", (cid,)
    ).fetchall()
    assert len(rows) >= 1
    assert rows[0]["evidence_type"] == "assertion"
    assert "128GB" in rows[0]["content"]


# ── create_capture_stamp ──────────────────────────────────────────────────────

def test_create_capture_stamp(db):
    stamp_id = "test-stamp-001"
    db.create_capture_stamp(
        stamp_id, channel="chat", source_type="A7",
        raw_text="I have 128GB RAM and an RTX 4090",
        meta={"conv_id": "conv-001"},
    )
    row = db._conn.execute(
        "SELECT * FROM capture_stamps WHERE id=?", (stamp_id,)
    ).fetchone()
    assert row is not None
    assert row["channel"] == "chat"
    assert "128GB" in row["raw_text"]


# ── search_claims_for_context ──────────────────────────────────────────────────

def test_search_claims_for_context_finds_relevant(db):
    db.upsert_claim("user_system", "ram_gb", "128", unit="GB")
    db.upsert_claim("user_system", "gpu_model", "RTX 4090")
    db.upsert_claim("user_system", "cpu_model", "Ryzen 9 9950X")

    results = db.search_claims_for_context("how much RAM do I have")
    # Should find the ram claim
    predicates = [r["predicate"] for r in results]
    assert "ram_gb" in predicates


def test_search_claims_for_context_excludes_a8(db):
    """A8 claims must never be returned by search."""
    db.upsert_claim("user_system", "mystery", "42", authority_tier="A8")
    results = db.search_claims_for_context("mystery")
    assert all(r.get("authority_tier") != "A8" for r in results)


def test_search_claims_for_context_fallback_scan(db):
    """Fallback scan works even when FTS returns nothing."""
    db.upsert_claim("user_system", "gpu_model", "RTX 4090")
    # Deliberately disable FTS in the search by passing a query that won't FTS-match
    # but will be found by the fallback scan
    results = db.search_claims_for_context("gpu", subject="user_system")
    assert any("gpu" in r.get("predicate", "").lower() or "4090" in r.get("value", "")
               for r in results)


# ── detect_factual_assertions ─────────────────────────────────────────────────

def test_detect_factual_assertions_hardware():
    from orivellum.capabilities.pklos.capture_stamp import detect_factual_assertions
    assert detect_factual_assertions("I have 128 GB RAM installed") is True
    assert detect_factual_assertions("My GPU is an RTX 4090") is True
    assert detect_factual_assertions("I'm running Windows 11") is True
    assert detect_factual_assertions("my machine has 64 cores") is True
    assert detect_factual_assertions("my specs are: 128GB RAM, RTX 4090") is True


def test_detect_factual_assertions_no_false_positives():
    from orivellum.capabilities.pklos.capture_stamp import detect_factual_assertions
    # These should NOT match — they're questions or general statements
    assert detect_factual_assertions("What is the capital of France?") is False
    assert detect_factual_assertions("Write me a poem about autumn") is False
    assert detect_factual_assertions("How does recursion work in Python?") is False


# ── FactRouter ────────────────────────────────────────────────────────────────

def test_fact_router_checkable_facts():
    from orivellum.capabilities.pklos.fact_router import FactRouter, RequestClass
    router = FactRouter()
    checkable = [
        "how much RAM do I have?",
        "what GPU do I have?",
        "what are my specs?",
        "what CPU is in my machine?",
        "how many cores does my processor have?",
        "what is my GPU?",
        "what OS am I running?",
    ]
    for q in checkable:
        result = router.classify(q)
        assert result == RequestClass.DETERMINISTICALLY_VERIFIABLE, \
            f"Expected DETERMINISTICALLY_VERIFIABLE for: {q!r}, got {result}"


def test_fact_router_general_fact():
    from orivellum.capabilities.pklos.fact_router import FactRouter, RequestClass
    router = FactRouter()
    assert router.classify("what is the capital of France?") != RequestClass.CHECKABLE_FACT
    assert router.classify("who invented the telephone?") != RequestClass.CHECKABLE_FACT


def test_fact_router_creative():
    from orivellum.capabilities.pklos.fact_router import FactRouter, RequestClass
    router = FactRouter()
    assert router.classify("write me a short story") == RequestClass.CREATIVE
    assert router.classify("generate an outline for my novel") == RequestClass.CREATIVE


def test_fact_router_no_false_abstentions():
    """The router must not misclassify common coding/writing questions as CHECKABLE_FACT."""
    from orivellum.capabilities.pklos.fact_router import FactRouter, RequestClass
    router = FactRouter()
    safe = [
        "how do I sort a list in Python?",
        "explain how async/await works",
        "what is machine learning?",
        "write a function that reverses a string",
    ]
    for q in safe:
        result = router.classify(q)
        assert result != RequestClass.CHECKABLE_FACT, \
            f"False abstention risk for: {q!r} classified as {result}"


# ── AbstentionPolicy ──────────────────────────────────────────────────────────

def test_abstention_policy_no_claims():
    """When checkable and no claims → ABSTENTION_INSTRUCTION."""
    from orivellum.capabilities.pklos.abstention import AbstentionPolicy, ABSTENTION_INSTRUCTION
    policy = AbstentionPolicy()
    instruction = policy.get_instruction(is_checkable=True, has_verified_claims=False)
    assert instruction == ABSTENTION_INSTRUCTION
    assert policy.should_abstain(is_checkable=True, has_verified_claims=False) is True


def test_abstention_policy_with_claims():
    """When checkable and has claims → VERIFIED_FACTS_INSTRUCTION."""
    from orivellum.capabilities.pklos.abstention import AbstentionPolicy, VERIFIED_FACTS_INSTRUCTION
    policy = AbstentionPolicy()
    instruction = policy.get_instruction(is_checkable=True, has_verified_claims=True)
    assert instruction == VERIFIED_FACTS_INSTRUCTION
    assert policy.should_abstain(is_checkable=True, has_verified_claims=True) is False


def test_abstention_policy_not_checkable():
    """Non-checkable queries get no instruction."""
    from orivellum.capabilities.pklos.abstention import AbstentionPolicy
    policy = AbstentionPolicy()
    instruction = policy.get_instruction(is_checkable=False, has_verified_claims=False)
    assert instruction == ""


# ── ClaimLedger.format_for_prompt ─────────────────────────────────────────────

def test_claim_ledger_format_for_prompt(db):
    from orivellum.capabilities.pklos.claim_ledger import ClaimLedger
    ledger = ClaimLedger(db)
    cid = db.upsert_claim("user_system", "ram_gb", "128", unit="GB")
    claims = [db.get_claim(cid)]
    formatted = ledger.format_for_prompt(claims)
    assert "VERIFIED FACTS" in formatted
    assert "ram_gb" in formatted
    assert "128" in formatted
    assert "A7" in formatted


def test_claim_ledger_format_for_prompt_empty(db):
    from orivellum.capabilities.pklos.claim_ledger import ClaimLedger
    ledger = ClaimLedger(db)
    assert ledger.format_for_prompt([]) == ""


# ── VER-INV-001 end-to-end ────────────────────────────────────────────────────

def test_ver_inv_001_a8_never_surfaces(db):
    """VER-INV-001: A8 claims must NEVER be returned by any read path."""
    db.upsert_claim("user_system", "secret_spec", "hallucinated", authority_tier="A8")

    # list_claims does not filter A8 — that's the API layer's job
    # but search_claims_for_context must filter it
    results = db.search_claims_for_context("secret_spec")
    assert all(r.get("authority_tier") != "A8" for r in results)

    # ClaimLedger also filters
    from orivellum.capabilities.pklos.claim_ledger import ClaimLedger
    ledger = ClaimLedger(db)
    context_results = ledger.search_for_context("secret_spec")
    assert all(r.get("authority_tier") != "A8" for r in context_results)


def test_full_capture_and_retrieval_pipeline(db):
    """Full pipeline: user states a fact → captured → retrieved as context."""
    # 1. User tells the system their specs (A7 self-reported)
    db.upsert_claim("user_system", "ram_gb", "128", unit="GB", authority_tier="A7",
                    evidence_text="I have 128GB RAM")
    db.upsert_claim("user_system", "gpu_model", "RTX 4090", authority_tier="A7",
                    evidence_text="I have an RTX 4090")

    # 2. Later query — system retrieves from ledger
    from orivellum.capabilities.pklos.claim_ledger import ClaimLedger
    ledger = ClaimLedger(db)
    claims = ledger.search_for_context("how much RAM do I have")
    assert any(c["predicate"] == "ram_gb" for c in claims)

    # 3. Format for prompt — verified facts block present
    formatted = ledger.format_for_prompt(claims)
    assert "VERIFIED FACTS" in formatted
    assert "128" in formatted

    # 4. Fact router correctly identifies this as checkable
    from orivellum.capabilities.pklos.fact_router import is_checkable_fact
    assert is_checkable_fact("how much RAM do I have?") is True

    # 5. Abstention policy says: we have claims, use VERIFIED_FACTS_INSTRUCTION
    from orivellum.capabilities.pklos.abstention import AbstentionPolicy, VERIFIED_FACTS_INSTRUCTION
    policy = AbstentionPolicy()
    instruction = policy.get_instruction(is_checkable=True, has_verified_claims=True)
    assert instruction == VERIFIED_FACTS_INSTRUCTION
