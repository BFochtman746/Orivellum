"""Divergent thinking engine — Guilford-grounded brainstorm sessions.

Architecture
------------
1. Parallel domain-shift LLM workers: each worker is forced into an analogical
   domain (ecology, game theory, jazz, etc.) and must express one structural
   idea through that domain's vocabulary.

2. Originality scoring: cosine distance from the Work's own knowledge
   embedding baseline ("cliché pool").  Ideas identical to known knowledge
   score near 0; genuinely novel ideas score near 1.

3. Usefulness judge: a secondary LLM call rates each idea 1-5 on practical
   applicability.

4. Pareto front selection: the non-dominated set on the
   originality × usefulness frontier (4-8 ideas returned).

5. Graceful fallback: when the embeddings endpoint is down, originality is
   approximated by bigram dissimilarity from the work knowledge corpus.
   When the LLM is unavailable the session is stored with status='failed'.
"""
from __future__ import annotations

import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.brainstorm")

# ── Domain library ─────────────────────────────────────────────────────────────
# Each domain must be epistemically distant from the others so the Flexibility
# dimension (Guilford) is genuinely served — not just vocabulary variation on
# the same conceptual cluster.

DOMAINS: list[str] = [
    "ecological food webs and succession dynamics",
    "musical counterpoint and harmonic tension",
    "urban planning and zoning logic",
    "common law precedent and legal argumentation",
    "evolutionary selection pressure and niche occupation",
    "jazz improvisation and call-and-response",
    "game theory and strategic equilibria",
    "architectural load distribution and spatial hierarchy",
    "culinary technique and flavor layering",
    "film editing and scene rhythm",
    "network topology and fault tolerance",
    "mythology and archetypal hero structure",
    "mechanical clock and gear-train design",
    "economic market clearing and price signals",
    "theatrical staging and dramatic tension",
]

CONTEXT_DESCRIPTIONS: dict[str, str] = {
    "narrative_structure":    "structuring the narrative arc and story progression",
    "knowledge_organization": "organizing and connecting knowledge domains",
    "chapter_architecture":   "sequencing chapters and their structural relationships",
    "research_planning":      "prioritizing research directions and framing open questions",
    "general":                "generating new approaches and structural perspectives",
}

# ── Idea representation ────────────────────────────────────────────────────────

def _new_idea(domain: str, text: str) -> dict:
    return {
        "id":               _short_id(),
        "domain":           domain,
        "text":             text.strip(),
        "originality":      0.5,   # overwritten after scoring
        "usefulness":       3,     # overwritten after judge
        "on_pareto_front":  False,
        "knowledge_item_id": None,
    }


def _short_id() -> str:
    import uuid
    return str(uuid.uuid4())[:8]


# ── Domain-shift worker ────────────────────────────────────────────────────────

_WORKER_SYSTEM = """\
You are a creative structural advisor who thinks EXCLUSIVELY through the lens of {domain}.
Every concept you articulate must be grounded in the mechanisms, vocabulary, and structural
patterns native to {domain} — not as decoration, but as the actual reasoning substrate.
Never name the domain explicitly in your response.
"""

_WORKER_USER = """\
Using structural principles from your domain, suggest ONE concrete idea for:
"{seed}"

Purpose: {context_desc}

{knowledge_context}
{negative_constraint}
Your idea must:
- Describe a specific mechanism or structural pattern (not a vague theme)
- Be immediately actionable as a design choice
- Differ from the conventional approach implied by the seed

Respond in exactly 2-3 sentences. No preamble, no headers, just the idea.
"""


def _domain_worker(
    domain: str,
    seed: str,
    context_type: str,
    work_knowledge: list[str],
    db: "OrivellumDB",
    cfg: Any,
    negative_constraint: str = "",
) -> str | None:
    """Run one domain-shift worker. Returns the idea text or None on failure."""
    from orivellum.capabilities.llm import llm_call

    context_desc = CONTEXT_DESCRIPTIONS.get(context_type, CONTEXT_DESCRIPTIONS["general"])
    kn_ctx = ""
    if work_knowledge:
        sample = work_knowledge[:5]
        kn_ctx = "Existing knowledge to be DISTINCT from:\n" + "\n".join(
            f"  • {k}" for k in sample
        ) + "\n"
    neg_ctx = f"\nNEGATIVE CONSTRAINT — do not suggest anything involving: {negative_constraint}\n" \
              if negative_constraint else ""

    messages = [
        {"role": "system", "content": _WORKER_SYSTEM.format(domain=domain)},
        {"role": "user",   "content": _WORKER_USER.format(
            seed=seed,
            context_desc=context_desc,
            knowledge_context=kn_ctx,
            negative_constraint=neg_ctx,
        )},
    ]
    result = llm_call(
        messages,
        cfg=cfg,
        db=db,
        purpose="brainstorm.worker",
        timeout=30,
        temperature=0.85,
        max_tokens=200,
    )
    if not result.ok or not result.text:
        return None
    text = result.text.strip()
    # Strip any accidental headers or bullets
    text = re.sub(r"^[\*\-#>\s]+", "", text, flags=re.MULTILINE)
    return text[:800]


# ── Originality scoring ────────────────────────────────────────────────────────

def _bigrams(text: str) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {f"{words[i]}{words[i+1]}" for i in range(len(words) - 1)}


def _bigram_dissimilarity(idea_text: str, corpus_texts: list[str]) -> float:
    """1 - Jaccard(idea_bigrams, union(corpus_bigrams)).  Range [0, 1]."""
    if not corpus_texts:
        return 0.5
    idea_bg = _bigrams(idea_text)
    corpus_bg: set[str] = set()
    for t in corpus_texts:
        corpus_bg |= _bigrams(t)
    if not idea_bg or not corpus_bg:
        return 0.5
    inter = idea_bg & corpus_bg
    union = idea_bg | corpus_bg
    return round(1.0 - len(inter) / len(union), 3) if union else 0.5


def _score_originality(
    idea_texts: list[str],
    work_id: str,
    db: "OrivellumDB",
) -> list[float]:
    """Score each idea's originality against the Work's knowledge baseline.

    Primary: cosine distance from mean knowledge embedding vector.
    Fallback: bigram dissimilarity from knowledge text corpus.
    """
    from orivellum.capabilities.embeddings import embed_texts, unpack_vector, cosine, pack_vector

    with db._lock:
        kn_rows = db._conn.execute(
            """SELECT k.text, v.embedding, v.dim
               FROM knowledge k
               LEFT JOIN vectors v ON v.object_id = k.id AND v.object_type='knowledge'
               WHERE k.work_id=? AND k.review_status IN ('auto','approved')
               LIMIT 40""",
            (work_id,),
        ).fetchall()

    kn_texts = [r["text"] for r in kn_rows]

    # Try embedding-based scoring
    kn_vecs = []
    for r in kn_rows:
        if r["embedding"] and r["dim"]:
            try:
                kn_vecs.append(unpack_vector(r["embedding"], r["dim"]))
            except Exception:
                pass

    idea_vecs = embed_texts(idea_texts, timeout=10)
    if idea_vecs and kn_vecs:
        # Compute mean of knowledge vectors as the cliché baseline
        dim = len(kn_vecs[0])
        mean_kn = [sum(v[d] for v in kn_vecs) / len(kn_vecs) for d in range(dim)]
        scores = []
        for iv in idea_vecs:
            if iv:
                sim = cosine(iv, mean_kn)
                # originality = 1 - similarity to baseline (clamp 0-1)
                scores.append(round(max(0.0, min(1.0, 1.0 - sim)), 3))
            else:
                scores.append(0.5)
        return scores

    # Fallback: bigram dissimilarity
    return [_bigram_dissimilarity(t, kn_texts) for t in idea_texts]


# ── Usefulness judge ───────────────────────────────────────────────────────────

_JUDGE_PROMPT = """\
Rate each idea below on usefulness for: "{seed}"
Context: {context_desc}

Scale: 1=Not useful, 2=Marginal, 3=Moderate, 4=Very useful, 5=Exceptional

Consider:
- Concreteness: can this be directly acted upon?
- Distinctiveness: does it differ from conventional approaches?
- Relevance: does it address the actual challenge in the seed?

Ideas (numbered):
{ideas_block}

Return ONLY a JSON array of integers with one score per idea, e.g. [3,5,2,4].
No explanation, no markdown, just the array.
"""


def _score_usefulness(
    idea_texts: list[str],
    seed: str,
    context_type: str,
    db: "OrivellumDB",
    cfg: Any,
) -> list[int]:
    """Single-call usefulness judge.  Returns list of ints 1-5."""
    from orivellum.capabilities.llm import llm_call

    if not idea_texts:
        return []

    ideas_block = "\n".join(f"{i+1}. {t}" for i, t in enumerate(idea_texts))
    context_desc = CONTEXT_DESCRIPTIONS.get(context_type, CONTEXT_DESCRIPTIONS["general"])
    prompt = _JUDGE_PROMPT.format(
        seed=seed,
        context_desc=context_desc,
        ideas_block=ideas_block,
    )
    result = llm_call(
        [{"role": "user", "content": prompt}],
        cfg=cfg,
        db=db,
        purpose="brainstorm.judge",
        timeout=30,
        temperature=0.0,
        max_tokens=100,
    )
    if not result.ok or not result.text:
        return [3] * len(idea_texts)

    # Parse the returned JSON array
    try:
        text = result.text.strip()
        # Strip markdown fences if model added them
        if "```" in text:
            text = re.search(r"\[.*?\]", text, re.DOTALL).group(0) if re.search(r"\[.*?\]", text, re.DOTALL) else "[]"
        arr = json.loads(text)
        if isinstance(arr, list) and len(arr) == len(idea_texts):
            return [max(1, min(5, int(x))) for x in arr]
    except Exception as exc:
        logger.debug("Usefulness judge parse failed: %s — raw: %s", exc, result.text[:200])

    return [3] * len(idea_texts)


# ── Deduplication ─────────────────────────────────────────────────────────────

def _deduplicate(ideas: list[dict], threshold: float = 0.55) -> list[dict]:
    """Remove near-duplicate ideas by bigram Jaccard overlap.

    When two ideas overlap above ``threshold``, keep the one with higher
    originality (or higher index as tiebreaker).
    """
    kept: list[dict] = []
    for idea in ideas:
        is_dup = False
        idea_bg = _bigrams(idea["text"])
        for k in kept:
            k_bg = _bigrams(k["text"])
            union = idea_bg | k_bg
            if not union:
                continue
            overlap = len(idea_bg & k_bg) / len(union)
            if overlap > threshold:
                # Replace kept idea if this one has higher originality
                if idea["originality"] > k["originality"]:
                    kept.remove(k)
                    kept.append(idea)
                is_dup = True
                break
        if not is_dup:
            kept.append(idea)
    return kept


# ── Pareto front ──────────────────────────────────────────────────────────────

def _pareto_front(ideas: list[dict]) -> list[dict]:
    """Return ideas on the Pareto front of (originality × usefulness).

    An idea A is Pareto-dominated if some other idea B has
    B.originality >= A.originality AND B.usefulness >= A.usefulness
    with at least one strictly greater.
    """
    front = []
    for i, a in enumerate(ideas):
        dominated = any(
            (b["originality"] >= a["originality"] and b["usefulness"] >= a["usefulness"]
             and (b["originality"] > a["originality"] or b["usefulness"] > a["usefulness"]))
            for j, b in enumerate(ideas) if i != j
        )
        if not dominated:
            front.append(a)
    return front


# ── Negative constraint detection ────────────────────────────────────────────

def _extract_common_theme(idea_texts: list[str]) -> str:
    """Extract the most frequent content word across a cluster (poor man's topic)."""
    from collections import Counter
    stopwords = {"the", "a", "an", "and", "or", "of", "in", "to", "for",
                 "that", "this", "with", "as", "by", "is", "are", "be", "it"}
    words = []
    for t in idea_texts:
        words.extend(w for w in re.findall(r"\b[a-z]{4,}\b", t.lower()) if w not in stopwords)
    if not words:
        return "similar concepts"
    most_common = Counter(words).most_common(3)
    return ", ".join(w for w, _ in most_common)


# ── Main session runner ───────────────────────────────────────────────────────

def run_brainstorm_session(
    session_id: str,
    work_id: str,
    seed_prompt: str,
    context_type: str,
    db: "OrivellumDB",
    cfg: Any,
    n_domains: int = 5,
) -> list[dict]:
    """Run a complete divergent brainstorm session synchronously.

    This is the main entry point called from the API route via run_in_threadpool.
    Returns the list of scored ideas (Pareto front first, then alternates).
    On total failure raises RuntimeError.
    """
    # Clamp domain count
    n_domains = max(3, min(n_domains, len(DOMAINS)))

    # Load work context
    with db._lock:
        kn_rows = db._conn.execute(
            "SELECT text FROM knowledge WHERE work_id=? AND review_status IN ('auto','approved') "
            "ORDER BY confidence DESC LIMIT 10",
            (work_id,),
        ).fetchall()
    work_knowledge = [r["text"] for r in kn_rows]

    # Select domains, shuffle for variety
    selected_domains = random.sample(DOMAINS, n_domains)

    # ── Phase 1: Parallel domain workers ─────────────────────────────────────
    raw_ideas: list[dict] = []
    with ThreadPoolExecutor(max_workers=n_domains) as pool:
        futures = {
            pool.submit(
                _domain_worker, domain, seed_prompt, context_type,
                work_knowledge, db, cfg,
            ): domain
            for domain in selected_domains
        }
        for future in as_completed(futures, timeout=45):
            domain = futures[future]
            try:
                text = future.result()
            except Exception as exc:
                logger.debug("Domain worker %r failed: %s", domain, exc)
                text = None
            if text:
                raw_ideas.append(_new_idea(domain, text))

    if not raw_ideas:
        raise RuntimeError("All domain workers failed — LLM endpoint unavailable")

    # ── Phase 2: Detect cluster saturation; inject negative constraints ───────
    # If we have fewer than 4 ideas or first-pass has obvious duplicates, run
    # more workers with negative constraints derived from the cluster themes.
    if len(raw_ideas) < 4:
        neg_theme = _extract_common_theme([i["text"] for i in raw_ideas])
        extras_needed = max(2, 5 - len(raw_ideas))
        extra_domains = [d for d in DOMAINS if d not in selected_domains]
        random.shuffle(extra_domains)
        extra_futures = {}
        with ThreadPoolExecutor(max_workers=extras_needed) as pool2:
            extra_futures = {
                pool2.submit(
                    _domain_worker, domain, seed_prompt, context_type,
                    work_knowledge, db, cfg, neg_theme,
                ): domain
                for domain in extra_domains[:extras_needed]
            }
            for future in as_completed(extra_futures, timeout=30):
                domain = extra_futures[future]
                try:
                    text = future.result()
                except Exception:
                    text = None
                if text:
                    raw_ideas.append(_new_idea(domain, text))

    # ── Phase 3: Score originality ────────────────────────────────────────────
    idea_texts = [i["text"] for i in raw_ideas]
    orig_scores = _score_originality(idea_texts, work_id, db)
    for idea, score in zip(raw_ideas, orig_scores):
        idea["originality"] = score

    # ── Phase 4: Score usefulness (batch LLM judge) ───────────────────────────
    use_scores = _score_usefulness(idea_texts, seed_prompt, context_type, db, cfg)
    for idea, score in zip(raw_ideas, use_scores):
        idea["usefulness"] = score

    # ── Phase 5: Deduplicate similar ideas ───────────────────────────────────
    # Sort by combined score first so dedup keeps the better idea
    raw_ideas.sort(key=lambda i: i["originality"] + i["usefulness"] * 0.2, reverse=True)
    unique_ideas = _deduplicate(raw_ideas, threshold=0.5)

    # Filter out clearly useless ideas (usefulness == 1 AND originality < 0.3)
    filtered = [i for i in unique_ideas if not (i["usefulness"] <= 1 and i["originality"] < 0.3)]
    if not filtered:
        filtered = unique_ideas  # keep all if filter is too aggressive

    # ── Phase 6: Pareto front ─────────────────────────────────────────────────
    front = _pareto_front(filtered)
    for idea in filtered:
        idea["on_pareto_front"] = idea in front

    # Sort: Pareto front first (by sum score desc), then alternates
    filtered.sort(key=lambda i: (0 if i["on_pareto_front"] else 1,
                                  -(i["originality"] + i["usefulness"] * 0.4)))

    # Cap at 8 ideas total
    return filtered[:8]
