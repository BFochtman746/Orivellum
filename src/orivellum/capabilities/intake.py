"""Universal Intake pipeline — the 5-stage orchestrator.

Every document that enters Orivellum passes through this pipeline after
the file has been saved and the DB record created:

  Stage 1  Identify    — read the document record; classify if no tier stored.
  Stage 2  Extract     — read existing extracted text, or trigger extraction.
  Stage 3  Embed       — embed chunks if the embeddings endpoint is available.
  Stage 4  Research    — optional on-demand web search (user-confirmed, egress-gated).
  Stage 5  Profile     — build an IntakeProfile with type-aware suggested actions.

No-pollution guarantee: this module NEVER creates a Work for a non-CANON
object.  ARTIFACT, SYSTEM, and CONVERSATION objects are profiled and
returned with "file" actions — never promoted.

Usage
-----
    from orivellum.capabilities.intake import run_intake
    profile = run_intake(doc_id, db=db, cfg=cfg)
    profile = run_intake(doc_id, db=db, cfg=cfg, research=True)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from orivellum.capabilities.classify import Tier, classify_object, EXCLUDED_FROM_WORKS

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB
    from orivellum.configuration.config import OrivellumConfig

logger = logging.getLogger("orivellum.intake")


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class SuggestedAction:
    """One clickable action surfaced on the Intake Profile card."""
    id: str          # stable machine key, e.g. "slot_book"
    label: str       # button label shown to the user
    description: str # tooltip / secondary text
    kind: str        # slot_book | file_taxes | find_gaps | research | extract_actions | chat | link_work


@dataclass
class IntakeProfile:
    """Full intake profile returned by run_intake()."""
    doc_id: str
    what_it_is: str            # "PDF research paper", "Manuscript chapter", etc.
    kind: str                  # pdf, docx, image, …
    tier: str                  # canon | source | artifact | system | conversation
    filed_to: Optional[str]    # Work title if the doc is already linked, else None
    filed_to_id: Optional[str] # Work ID if linked
    confidence: float          # 0.0–1.0 from classifier
    summary: str               # short extractive summary (<200 words)
    word_count: int
    headings: list[str]
    text_snippet: Optional[str] = None       # first ~500 chars of extracted text for chat grounding
    suggested_actions: list[SuggestedAction] = field(default_factory=list)
    research_summary: Optional[str] = None   # filled when stage 4 ran
    research_sources: list[dict] = field(default_factory=list)
    error: Optional[str] = None


# ── Stage helpers ──────────────────────────────────────────────────────────────

def _human_label(tier: str, kind: str, title: str) -> str:
    """Build a readable 'what it is' description from tier + kind + title."""
    kind_labels = {
        "pdf":       "PDF document",
        "docx":      "Word document",
        "excel":     "spreadsheet",
        "csv":       "CSV dataset",
        "image":     "image",
        "pptx":      "presentation",
        "markdown":  "Markdown document",
        "text":      "text document",
        "html":      "web page",
        "json":      "JSON data file",
        "zip":       "ZIP archive",
    }
    base = kind_labels.get(kind, f"{kind} file")

    if tier == Tier.CANON.value:
        lower = title.lower()
        if any(w in lower for w in ("chapter", "ch.", " ch ", "scene", "act")):
            return f"Manuscript chapter ({base})"
        if any(w in lower for w in ("manuscript", "draft", "novel")):
            return f"Manuscript draft ({base})"
        if any(w in lower for w in ("bible", "series")):
            return f"Series bible ({base})"
        return f"Canon document ({base})"
    elif tier == Tier.SOURCE.value:
        lower = title.lower()
        if kind == "image":
            if any(w in lower for w in ("receipt", "invoice", "bill")):
                return "Receipt or invoice (image)"
            if any(w in lower for w in ("whiteboard", "board", "sketch", "diagram")):
                return "Whiteboard or diagram (image)"
            if any(w in lower for w in ("screenshot", "screen", "product")):
                return "Product / web screenshot (image)"
            return "Image"
        if any(w in lower for w in ("receipt", "invoice", "expense")):
            return f"Receipt or invoice ({base})"
        if any(w in lower for w in ("product", "spec", "specification")):
            return f"Product specification ({base})"
        return f"Research / source material ({base})"
    elif tier == Tier.ARTIFACT.value:
        return f"Build / migration artifact ({base})"
    elif tier == Tier.SYSTEM.value:
        return f"System / configuration file ({base})"
    elif tier == Tier.CONVERSATION.value:
        return f"Conversation export ({base})"
    return base


def _build_summary(text: str, headings: list[str], word_count: int) -> str:
    """Build a short (<200 word) extractive summary."""
    if not text.strip():
        return "No text could be extracted from this file."
    # Use headings for structure hint, then first substantive paragraphs
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Skip lines that are only headings (already captured separately)
    body_lines = [ln for ln in lines if not any(ln == h for h in headings[:5])]
    joined = " ".join(body_lines)
    words = joined.split()
    if len(words) <= 80:
        return joined
    return " ".join(words[:80]) + "…"


def _suggest_actions(
    tier: str, kind: str, title: str, text: str, filed_to_id: Optional[str]
) -> list[SuggestedAction]:
    """Return type-aware suggested actions.

    !! NO-POLLUTION GUARANTEE !!
    This function MUST NOT return a "create_work" or "slot_book" action for
    any tier other than CANON.  Tests enforce this invariant.
    """
    actions: list[SuggestedAction] = []
    lower_title = title.lower()
    lower_text = (text or "")[:2000].lower()

    if tier == Tier.CANON.value:
        # Manuscript / chapter → slot into book pipeline
        actions.append(SuggestedAction(
            id="slot_book",
            label="Slot into Book Pipeline",
            description="Add this chapter to the 17-stage book pipeline for your Work.",
            kind="slot_book",
        ))
        # Only offer Link to Work if not already linked
        if not filed_to_id:
            actions.append(SuggestedAction(
                id="link_work",
                label="Link to a Work",
                description="Associate this document with one of your active Works.",
                kind="link_work",
            ))
        actions.append(SuggestedAction(
            id="find_gaps",
            label="Find Research Gaps",
            description="Analyse this manuscript for open questions and missing sources.",
            kind="find_gaps",
        ))

    elif tier == Tier.SOURCE.value:
        # Receipt / invoice
        if kind in ("image", "pdf") and any(
            w in lower_title or w in lower_text
            for w in ("receipt", "invoice", "expense", "bill", "total", "merchant")
        ):
            actions.append(SuggestedAction(
                id="file_taxes",
                label="File for Expenses / Taxes",
                description="Add this receipt to your Expenses Work and tag it for tax review.",
                kind="file_taxes",
            ))
        # Whiteboard / diagram → extract action items
        if kind == "image" and any(
            w in lower_title or w in lower_text
            for w in ("whiteboard", "board", "sketch", "action", "todo", "task", "diagram")
        ):
            actions.append(SuggestedAction(
                id="extract_actions",
                label="Extract Action Items",
                description="Identify and save the tasks and decisions visible in this image.",
                kind="extract_actions",
            ))
        # Product page / link → research
        if kind in ("image", "html", "pdf") and any(
            w in lower_title or w in lower_text
            for w in ("product", "price", "compare", "review", "spec", "specification", "screenshot")
        ):
            actions.append(SuggestedAction(
                id="research",
                label="Research It Online",
                description="Search the web for reviews, alternatives, and pricing. Requires confirmation.",
                kind="research",
            ))
        # Generic research material
        if not actions or (actions and not any(a.id == "research" for a in actions)):
            actions.append(SuggestedAction(
                id="find_gaps",
                label="Find Related Gaps",
                description="Find open questions in your Works that this document could help answer.",
                kind="find_gaps",
            ))
        if not filed_to_id:
            actions.append(SuggestedAction(
                id="link_work",
                label="Link to a Work",
                description="Associate this document with one of your active Works.",
                kind="link_work",
            ))

    elif tier in (Tier.ARTIFACT.value, Tier.SYSTEM.value):
        # Build / migration artifacts — never promote, only archive
        actions.append(SuggestedAction(
            id="archive",
            label="Archive",
            description="Mark this file as archived. It will not appear in research results.",
            kind="archive",
        ))

    elif tier == Tier.CONVERSATION.value:
        actions.append(SuggestedAction(
            id="find_gaps",
            label="Find Topics Discussed",
            description="Extract key discussion points and add them to a Work's knowledge base.",
            kind="find_gaps",
        ))

    # Always: chat about the document
    actions.append(SuggestedAction(
        id="chat",
        label="Chat About This",
        description="Open a new conversation with this document as context.",
        kind="chat",
    ))

    return actions


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_intake(
    doc_id: str,
    *,
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    research: bool = False,
    research_query: Optional[str] = None,
) -> IntakeProfile:
    """Run the 5-stage intake pipeline for an already-stored document.

    Args:
        doc_id:           ID of the document in the library (must already exist).
        db:               OrivellumDB instance.
        cfg:              OrivellumConfig instance (for data_dir, model config).
        research:         If True, run stage 4 (web search). Requires TAVILY_API_KEY.
        research_query:   Override the search query (default: document title).

    Returns:
        IntakeProfile — never raises; errors are captured in profile.error.
    """
    # ── Stage 1: Identify ────────────────────────────────────────────────────
    doc = db.get_document(doc_id)
    if not doc:
        return IntakeProfile(
            doc_id=doc_id,
            what_it_is="Unknown",
            kind="unknown",
            tier="source",
            filed_to=None,
            filed_to_id=None,
            confidence=0.0,
            summary="Document not found.",
            word_count=0,
            headings=[],
            error=f"Document {doc_id!r} not found in the library.",
        )

    title     = doc.get("title") or ""
    kind      = doc.get("kind") or "file"
    source    = doc.get("source") or title
    readiness = doc.get("readiness") or "imported"
    stored_tier = doc.get("tier") or "source"

    # ── Processing gate: if extraction is still running, return a clear
    # "processing" profile rather than a misleading no-text profile.
    if readiness == "imported":
        return IntakeProfile(
            doc_id=doc_id,
            what_it_is=f"Processing… ({kind} document)",
            kind=kind,
            tier=stored_tier,
            filed_to=None,
            filed_to_id=doc.get("work_id"),
            confidence=0.0,
            summary=(
                "This document is still being extracted in the background. "
                "Run Intake again once readiness is 'ready' to see the full profile."
            ),
            word_count=0,
            headings=[],
            suggested_actions=[SuggestedAction(
                id="retry",
                label="Check Again",
                description="Re-run the intake pipeline to check if extraction has finished.",
                kind="retry",  # handled by clients as intake refetch, NOT as chat
            )],
            error=None,
        )

    # Extraction failed — surface the error clearly instead of a silent no-text
    if readiness in ("error", "no_text"):
        return IntakeProfile(
            doc_id=doc_id,
            what_it_is=f"Extraction failed ({kind})",
            kind=kind,
            tier=stored_tier,
            filed_to=None,
            filed_to_id=doc.get("work_id"),
            confidence=0.0,
            summary=(
                "Text could not be extracted from this file. "
                "Try re-importing a different format or check the file for corruption."
            ),
            word_count=0,
            headings=[],
            suggested_actions=[SuggestedAction(
                id="chat",
                label="Chat About This",
                description="Open a conversation even without extracted text.",
                kind="chat",
            )],
            error=f"Extraction readiness: {readiness}",
        )

    # Re-classify if tier is still default/missing (idempotent, cheap)
    classification = classify_object(
        name=Path(source).name if source else title,
        kind=kind,
        source_path=source,
    )
    tier = classification.tier.value
    confidence = classification.confidence

    # Persist tier to DB if it changed or was unset
    try:
        if stored_tier != tier:
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET tier=? WHERE id=?", (tier, doc_id)
                )
                db._conn.commit()
    except Exception as exc:
        logger.debug("Could not persist tier update: %s", exc)

    # ── Stage 2: Extract ─────────────────────────────────────────────────────
    full_text = ""
    headings: list[str] = []
    word_count = doc.get("word_count") or 0
    lib_root = Path(cfg.data_dir) / "library"
    content_path = doc.get("content_path")

    # PRIMARY: read the column the extraction pipeline writes to
    stored_text = doc.get("extracted_text") or ""
    if stored_text.strip():
        full_text = stored_text

    # FALLBACK 1: sidecar .txt file next to the content file
    if not full_text.strip() and content_path:
        text_file = lib_root / content_path if not Path(content_path).is_absolute() else Path(content_path)
        candidates = [
            text_file.with_suffix(".txt"),
            text_file.parent / (text_file.stem + "_text.txt"),
        ]
        for cand in candidates:
            try:
                if cand.exists() and cand.stat().st_size > 0:
                    raw = cand.read_text(encoding="utf-8", errors="replace")
                    if len(raw.strip()) > 10:
                        full_text = raw
                        break
            except Exception:
                pass

    # FALLBACK 2: inline extraction from the original file (only if ready and no text yet)
    if not full_text.strip() and content_path:
        actual_path = lib_root / content_path if not Path(content_path).is_absolute() else Path(content_path)
        if actual_path.exists():
            try:
                from orivellum.capabilities.extraction import extract as _extract
                result = _extract(actual_path, kind)
                full_text = result.full_text or ""
                headings = result.headings or []
                word_count = result.word_count or word_count
            except Exception as exc:
                logger.debug("Intake inline extraction failed for %s: %s", doc_id, exc)

    # FALLBACK 3: DB chunk text (column is "text")
    if not full_text.strip():
        try:
            with db._lock:
                rows = db._conn.execute(
                    "SELECT text FROM chunks WHERE doc_id=? ORDER BY page LIMIT 20",
                    (doc_id,)
                ).fetchall()
            if rows:
                full_text = "\n".join(r["text"] for r in rows if r["text"])
        except Exception as exc:
            logger.debug("Could not load chunks for %s: %s", doc_id, exc)

    if not word_count and full_text:
        word_count = len(full_text.split())

    if not headings and full_text:
        # Infer headings from short all-caps or title-case lines
        import re
        for line in full_text.splitlines()[:100]:
            line = line.strip()
            if 10 < len(line) < 100 and (line.isupper() or re.match(r"^#{1,3}\s", line)):
                headings.append(line.lstrip("#").strip())
                if len(headings) >= 6:
                    break

    # ── Stage 3: Embed ───────────────────────────────────────────────────────
    try:
        if full_text.strip():
            from orivellum.capabilities.embeddings import embed_chunks_for_doc
            embed_chunks_for_doc(doc_id, db)
    except Exception as exc:
        logger.debug("Intake embed failed for %s (non-fatal): %s", doc_id, exc)

    # ── Stage 4: Research (on-demand, user-confirmed) ─────────────────────────
    research_summary: Optional[str] = None
    research_sources: list[dict] = []

    if research:
        query = research_query or title
        if not query:
            logger.info("Intake research skipped — no query for %s", doc_id)
        else:
            try:
                from orivellum.capabilities.websearch import web_search_synthesize
                base_url = getattr(cfg, "serving", None)
                model_name = "gpt-3.5-turbo"
                try:
                    from orivellum.api._deps import get_config as _get_cfg
                    _c = _get_cfg()
                    model_name = _c.llm.model if hasattr(_c, "llm") else model_name
                except Exception:
                    pass
                research_summary, research_sources = web_search_synthesize(
                    query, None, model_name, db=db
                )
                # Persist as a recallable note linked to this document
                if research_summary and doc.get("work_id"):
                    try:
                        db.create_knowledge_item(
                            work_id=doc["work_id"],
                            kind="note",
                            text=f'Web research for "{title}":\n\n{research_summary}',
                            subject=title,
                            predicate="researched_via",
                            obj="web_search",
                            confidence=0.7,
                            source_doc_id=doc_id,
                            review_status="auto",
                            meta={"intake_research": True, "sources": research_sources[:3]},
                        )
                    except Exception as exc:
                        logger.debug("Could not persist research note: %s", exc)
            except Exception as exc:
                logger.warning("Intake web research failed for %s: %s", doc_id, exc)
                research_summary = None

    # ── Stage 5: Profile + Actions ───────────────────────────────────────────
    # Resolve work title
    work_id = doc.get("work_id")
    filed_to: Optional[str] = None
    if work_id:
        try:
            work = db.get_work(work_id)
            filed_to = work.get("title") if work else None
        except Exception:
            pass

    what_it_is = _human_label(tier, kind, title)
    summary = _build_summary(full_text, headings, word_count)
    actions = _suggest_actions(tier, kind, title, full_text, work_id)

    # Build a short text snippet for chat grounding when the doc is not linked to a Work
    text_snippet: Optional[str] = None
    if full_text.strip():
        text_snippet = full_text.strip()[:500]

    return IntakeProfile(
        doc_id=doc_id,
        what_it_is=what_it_is,
        kind=kind,
        tier=tier,
        filed_to=filed_to,
        filed_to_id=work_id,
        confidence=confidence,
        summary=summary,
        word_count=word_count,
        headings=headings[:8],
        text_snippet=text_snippet,
        suggested_actions=actions,
        research_summary=research_summary,
        research_sources=research_sources[:5],
        error=None,
    )
