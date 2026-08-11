"""ASSAY FORCE detectors — the Story Force engines 11–17 (archive E11 / M16).

Seven Tier-2 advisory detectors recovered from the archive's Engine
Contracts, registered through ASSAY like every other instrument:

* **Engine 11 — Structural Enforcement**: chapter-length outliers and
  monolithic chapters with no scene relief.
* **Engine 12 — Narrative Physics**: actions must have visible
  consequences — long chapters whose causal-connective density falls
  below floor read as events without causation.
* **Engine 13 — Pressure Curve**: the tension curve across the book —
  a flat curve, or a mid-book pressure sag against the rolling mean.
* **Engine 14 — Conflict Escalation**: conflict density must rise into
  the final third; middle chapters with zero conflict signal are flagged.
* **Engine 15 — Scene Purpose**: chapters that introduce nothing new,
  carry no conflict/tension, and contain no dialogue have no visible
  purpose signal.
* **Engine 16 — Story Momentum**: stalled chapters (long sentences, no
  dialogue, no tension) and consecutive-stall flatlines.
* **Engine 17 — Theme Integrity**: motifs established in the opening
  third that vanish for long stretches later.

All detectors are deterministic and computable from prose alone — every
finding carries measures plus a verbatim quote with a character offset
(LAW 3).  Per the archive's ENGINE_12 contract, these are advisory
enforcement engines that *escalate instead of mutating*: forbidden
operations include authority override, merge, lock, and silent mutation.
They start in shadow mode and may block only after promotion (E10).

Book-level analyses always run over the full chapter list; a chapter-
scoped run computes the same book context but reports findings for the
requested chapter only.
"""

from __future__ import annotations

import re
from collections import Counter

from .metrics import register_bands, sentence_length_stats, words_of

# ── Signal lexicons (defaults; extendable via instrument thresholds) ─────────

TENSION_LEXICON = (
    "afraid", "fear", "feared", "terror", "dread", "danger", "dangerous",
    "threat", "threatened", "warning", "warned", "trembled", "trembling",
    "shook", "shaking", "silence fell", "held his breath", "held her breath",
    "heart pounded", "heart raced", "blood ran cold", "froze", "panic",
    "desperate", "desperation", "urgent", "urgency", "too late", "no time",
    "screamed", "scream", "cried out", "gasped", "stumbled", "fled",
    "hunted", "trapped", "cornered", "knife", "blade drawn", "blood",
    "dying", "death", "dead", "kill", "killed", "wound", "wounded",
)

CONFLICT_LEXICON = (
    "argued", "argument", "quarrel", "quarreled", "fought", "fight",
    "fighting", "struck", "strike", "blow", "shouted", "shouting", "yelled",
    "accused", "accusation", "refused", "refusal", "defied", "defiance",
    "betrayed", "betrayal", "enemy", "enemies", "against him", "against her",
    "confronted", "confrontation", "demanded", "demand", "threatened",
    "clashed", "clash", "war", "battle", "raid", "seized", "stole",
    "swore at", "cursed", "rage", "fury", "furious", "anger", "angry",
)

CAUSAL_MARKERS = (
    "because", "so that", "therefore", "as a result", "which meant",
    "and so", "since", "led to", "caused", "in consequence", "for that reason",
    "that is why", "thanks to", "owing to", "due to", "in answer",
    "in return", "in revenge", "provoked", "forced him", "forced her",
    "drove him", "drove her", "made him", "made her",
)

_SCENE_BREAK = re.compile(
    r"(?m)^[ \t]*(?:\*\s*\*\s*\*|#+|~+|—{3,}|-{3,})[ \t]*$|\n[ \t]*\n[ \t]*\n"
)
_CAPWORD = re.compile(r"\b[A-Z][a-z][a-z']+\b")

# Common sentence-openers that look like proper nouns but aren't names.
_CAP_STOPWORDS = frozenset(
    (
        "The", "She", "His", "Her", "They", "There", "Then", "That", "This",
        "But", "And", "When", "What", "Where", "Why", "How", "Not", "Now",
        "For", "You", "Yet", "Was", "Were", "Are", "Had", "Has", "Have",
        "With", "From", "Into", "After", "Before", "Over", "Under", "One",
        "Two", "Three", "All", "Some", "None", "Each", "Every", "Once",
        "Still", "Even", "Only", "Perhaps", "Though", "While", "Until",
        "Beyond", "Behind", "Above", "Below", "Chapter", "Part", "Book",
    )
)

_THEME_STOPWORDS = frozenset(
    (
        "about", "after", "again", "against", "almost", "along", "already",
        "always", "another", "anything", "around", "because", "before",
        "began", "behind", "being", "beneath", "beside", "better", "between",
        "beyond", "brought", "called", "came", "cannot", "could", "down",
        "during", "either", "enough", "every", "first", "found", "gave",
        "going", "great", "hands", "having", "himself", "herself", "house",
        "inside", "instead", "knew", "know", "known", "later", "little",
        "looked", "looking", "made", "might", "morning", "moved", "much",
        "never", "night", "nothing", "often", "once", "only", "other",
        "others", "outside", "over", "people", "perhaps", "place", "right",
        "said", "same", "seemed", "should", "since", "small", "something",
        "sometimes", "still", "such", "taken", "than", "that", "their",
        "them", "themselves", "then", "there", "these", "they", "thing",
        "things", "think", "this", "those", "though", "thought", "three",
        "through", "time", "times", "toward", "turned", "under", "until",
        "upon", "very", "voice", "walked", "wanted", "watched", "were",
        "what", "when", "where", "which", "while", "whose", "will", "with",
        "within", "without", "woman", "women", "words", "would", "years",
        "young",
    )
)

FORCE_KEYS = (
    "force.structural_enforcement",
    "force.narrative_physics",
    "force.pressure_curve",
    "force.conflict_escalation",
    "force.scene_purpose",
    "force.story_momentum",
    "force.theme_integrity",
)


# ── Shared profile ───────────────────────────────────────────────────────────


def _find_phrases(text: str, phrases: tuple[str, ...] | list[str]) -> list[tuple[int, str]]:
    """All case-insensitive whole-ish occurrences as (offset, verbatim) pairs."""
    hits: list[tuple[int, str]] = []
    low = text.lower()
    for phrase in phrases:
        p = phrase.lower()
        start = 0
        while True:
            i = low.find(p, start)
            if i < 0:
                break
            before_ok = i == 0 or not low[i - 1].isalpha()
            after = i + len(p)
            after_ok = after >= len(low) or not low[after].isalpha()
            if before_ok and after_ok:
                hits.append((i, text[i:after]))
            start = i + 1
    return sorted(hits)


def _quote_at(text: str, offset: int, span: int = 140) -> dict:
    """A verbatim evidence quote around ``offset`` (LAW 3: quote + offset)."""
    lo = max(0, offset - span // 3)
    return {"quote": text[lo : lo + span].strip(), "offset": lo}


def _opening_quote(text: str, span: int = 140) -> dict:
    return {"quote": (text or "")[:span].strip(), "offset": 0}


def _per_1k(hits: int, n_words: int) -> float:
    return round(hits / n_words * 1000, 2) if n_words else 0.0


def _proper_nouns(text: str) -> set[str]:
    """Recurring capitalized words — a cheap named-entity signal."""
    counts = Counter(
        m.group(0) for m in _CAPWORD.finditer(text or "")
        if m.group(0) not in _CAP_STOPWORDS
    )
    return {w for w, c in counts.items() if c >= 2}


def build_profiles(chapters: list[dict], thresholds: dict | None = None) -> list[dict]:
    """One deterministic signal profile per chapter, in seq order."""
    th = thresholds or {}
    tension_lex = tuple(TENSION_LEXICON) + tuple(th.get("tension_lexicon_extra") or ())
    conflict_lex = tuple(CONFLICT_LEXICON) + tuple(th.get("conflict_lexicon_extra") or ())
    causal_lex = tuple(CAUSAL_MARKERS) + tuple(th.get("causal_markers_extra") or ())
    profiles: list[dict] = []
    for ch in chapters:
        text = ch.get("text") or ""
        n = len(words_of(text))
        tension_hits = _find_phrases(text, tension_lex)
        conflict_hits = _find_phrases(text, conflict_lex)
        causal_hits = _find_phrases(text, causal_lex)
        profiles.append(
            {
                "chapter_id": ch["id"],
                "seq": ch["seq"],
                "title": ch.get("title") or "",
                "text": text,
                "words": n,
                "sentences": sentence_length_stats(text),
                "register": register_bands(text),
                "scene_breaks": len(_SCENE_BREAK.findall(text)),
                "tension_hits": tension_hits,
                "tension_per_1k": _per_1k(len(tension_hits), n),
                "conflict_hits": conflict_hits,
                "conflict_per_1k": _per_1k(len(conflict_hits), n),
                "causal_hits": causal_hits,
                "causal_per_1k": _per_1k(len(causal_hits), n),
                "proper_nouns": _proper_nouns(text),
            }
        )
    return profiles


def _finding(
    p: dict | None,
    issue_type: str,
    severity: str,
    evidence: dict,
) -> dict:
    """Standard finding shell (Unit | Issue | Severity | Classification | Action)."""
    return {
        "chapter_id": p["chapter_id"] if p else None,
        "unit": f"chapter {p['seq']}" if p else "story",
        "issue_type": issue_type,
        "severity": severity,
        "classification": "deterministic",
        "action": "author_review",
        "evidence": evidence,
    }


def _curve(profiles: list[dict], key: str) -> list[dict]:
    return [
        {"chapter": p["seq"], "value": p[key], "words": p["words"]} for p in profiles
    ]


def _story_evidence(p: dict, hits_key: str | None = None) -> dict:
    """Grounded evidence for a story-level finding: a verbatim quote from a
    representative chapter (never an empty quote — the contract forbids
    asserting a detection without quoted evidence)."""
    ev: dict = {
        "evidence_chapter": p["seq"],
        "evidence_chapter_id": p["chapter_id"],
    }
    hits = p.get(hits_key) if hits_key else None
    if hits:
        off, _ = hits[0]
        ev.update(_quote_at(p["text"], off))
    else:
        ev.update(_opening_quote(p["text"]))
    return ev


# ── Engine 11 — Structural Enforcement ───────────────────────────────────────


def detect_structural_enforcement(profiles: list[dict], th: dict) -> tuple[list[dict], dict]:
    max_ratio = float(th.get("max_length_ratio", 3.0))
    min_ratio = float(th.get("min_length_ratio", 0.25))
    mono_words = int(th.get("monolith_min_words", 3000))
    sized = sorted(p["words"] for p in profiles if p["words"] > 0)
    median = sized[len(sized) // 2] if sized else 0
    findings: list[dict] = []
    for p in profiles:
        if p["words"] == 0 or median == 0:
            continue
        ratio = p["words"] / median
        measures = {"words": p["words"], "median_words": median, "ratio": round(ratio, 2)}
        if len(sized) >= 3 and ratio > max_ratio:
            findings.append(
                _finding(p, "chapter_length_outlier_long", "medium",
                         {"measures": measures, **_opening_quote(p["text"])})
            )
        elif len(sized) >= 3 and ratio < min_ratio:
            findings.append(
                _finding(p, "chapter_length_outlier_short", "low",
                         {"measures": measures, **_opening_quote(p["text"])})
            )
        if p["words"] >= mono_words and p["scene_breaks"] == 0:
            findings.append(
                _finding(p, "monolithic_chapter", "medium",
                         {"measures": {"words": p["words"], "scene_breaks": 0},
                          **_opening_quote(p["text"])})
            )
    return findings, {"median_words": median, "chapters": len(profiles)}


# ── Engine 12 — Narrative Physics ────────────────────────────────────────────


def detect_narrative_physics(profiles: list[dict], th: dict) -> tuple[list[dict], dict]:
    floor = float(th.get("min_causal_per_1000_words", 1.5))
    min_words = int(th.get("min_chapter_words", 800))
    findings: list[dict] = []
    for p in profiles:
        if p["words"] < min_words:
            continue
        if p["causal_per_1k"] < floor:
            ev = {
                "measures": {
                    "causal_markers_per_1000_words": p["causal_per_1k"],
                    "floor": floor,
                    "words": p["words"],
                },
            }
            # Quote the highest-tension passage lacking a nearby consequence
            # marker, or the chapter opening when there is no tension signal.
            if p["tension_hits"]:
                off, _ = p["tension_hits"][0]
                ev.update(_quote_at(p["text"], off))
            else:
                ev.update(_opening_quote(p["text"]))
            findings.append(_finding(p, "consequence_gap", "medium", ev))
    return findings, {"curve": _curve(profiles, "causal_per_1k"), "floor": floor}


# ── Engine 13 — Pressure Curve ───────────────────────────────────────────────


def detect_pressure_curve(profiles: list[dict], th: dict) -> tuple[list[dict], dict]:
    min_chapters = int(th.get("min_chapters_for_curve", 5))
    flat_cv = float(th.get("flat_curve_max_cv", 0.25))
    sag_pct = float(th.get("sag_below_rolling_pct", 60.0))
    findings: list[dict] = []
    series = [p["tension_per_1k"] for p in profiles if p["words"] > 0]
    summary = {"curve": _curve(profiles, "tension_per_1k")}
    if len(series) >= min_chapters:
        mean = sum(series) / len(series)
        # A book with ZERO tension signal everywhere is the flattest curve
        # possible — treat cv as 0, never skip the check (a silent pass on
        # the all-zero case would be the detector's own worst false negative).
        if mean > 0:
            sd = (sum((x - mean) ** 2 for x in series) / len(series)) ** 0.5
            cv = sd / mean
        else:
            cv = 0.0
        summary["mean"] = round(mean, 2)
        summary["cv"] = round(cv, 3)
        if cv < flat_cv:
            live_now = [p for p in profiles if p["words"] > 0]
            rep = min(live_now, key=lambda p: abs(p["tension_per_1k"] - mean))
            findings.append(
                _finding(None, "flat_pressure_curve", "medium",
                         {"measures": {"coefficient_of_variation": round(cv, 3),
                                       "flat_ceiling": flat_cv,
                                       "mean_tension_per_1k": round(mean, 2)},
                          **_story_evidence(rep, "tension_hits")})
            )
        # Mid-book sag against the rolling mean of the previous 3 chapters.
        live = [p for p in profiles if p["words"] > 0]
        for i in range(3, len(live) - 1):  # skip opening ramp and final chapter
            window = [live[j]["tension_per_1k"] for j in range(i - 3, i)]
            roll = sum(window) / 3
            p = live[i]
            if roll > 0 and p["tension_per_1k"] < roll * (1 - sag_pct / 100.0):
                findings.append(
                    _finding(p, "pressure_sag", "low",
                             {"measures": {"tension_per_1k": p["tension_per_1k"],
                                           "rolling_mean_prev3": round(roll, 2),
                                           "sag_threshold_pct": sag_pct},
                              **_opening_quote(p["text"])})
                )
    return findings, summary


# ── Engine 14 — Conflict Escalation ──────────────────────────────────────────


def detect_conflict_escalation(profiles: list[dict], th: dict) -> tuple[list[dict], dict]:
    min_chapters = int(th.get("min_chapters_for_trend", 6))
    min_words = int(th.get("min_chapter_words", 600))
    findings: list[dict] = []
    live = [p for p in profiles if p["words"] > 0]
    summary = {"curve": _curve(profiles, "conflict_per_1k")}
    if len(live) >= min_chapters:
        third = len(live) // 3
        first = [p["conflict_per_1k"] for p in live[:third]]
        final = [p["conflict_per_1k"] for p in live[-third:]]
        first_mean = sum(first) / len(first)
        final_mean = sum(final) / len(final)
        summary["first_third_mean"] = round(first_mean, 2)
        summary["final_third_mean"] = round(final_mean, 2)
        # final <= first is a failure to escalate — INCLUDING the all-zero
        # book (0 <= 0): conflict that never appears never escalates.  The
        # only clean shape is a genuine rise (final > first).
        if final_mean <= first_mean:
            rep = min(live[-third:], key=lambda p: p["conflict_per_1k"])
            findings.append(
                _finding(None, "no_conflict_escalation", "medium",
                         {"measures": {"first_third_mean": round(first_mean, 2),
                                       "final_third_mean": round(final_mean, 2)},
                          **_story_evidence(rep, "conflict_hits")})
            )
        # Middle-third chapters with zero conflict signal.
        for p in live[third : len(live) - third]:
            if p["words"] >= min_words and not p["conflict_hits"]:
                findings.append(
                    _finding(p, "conflict_absent", "low",
                             {"measures": {"conflict_per_1k": 0.0, "words": p["words"]},
                              **_opening_quote(p["text"])})
                )
    return findings, summary


# ── Engine 15 — Scene Purpose ────────────────────────────────────────────────


def detect_scene_purpose(profiles: list[dict], th: dict) -> tuple[list[dict], dict]:
    min_words = int(th.get("min_chapter_words", 400))
    findings: list[dict] = []
    seen: set[str] = set()
    checked = 0
    for i, p in enumerate(profiles):
        new_names = p["proper_nouns"] - seen
        seen |= p["proper_nouns"]
        if p["words"] < min_words or i == 0:
            continue
        checked += 1
        has_dialogue = p["register"]["dialogue_ratio"] > 0.0
        signals = {
            "new_named_entities": len(new_names),
            "conflict_hits": len(p["conflict_hits"]),
            "tension_hits": len(p["tension_hits"]),
            "dialogue_ratio": p["register"]["dialogue_ratio"],
        }
        if not new_names and not p["conflict_hits"] and not p["tension_hits"] and not has_dialogue:
            findings.append(
                _finding(p, "purpose_unclear", "medium",
                         {"measures": signals, **_opening_quote(p["text"])})
            )
    return findings, {"chapters_checked": checked}


# ── Engine 16 — Story Momentum ───────────────────────────────────────────────


def detect_story_momentum(profiles: list[dict], th: dict) -> tuple[list[dict], dict]:
    min_words = int(th.get("min_chapter_words", 600))
    long_mean = float(th.get("stall_mean_sentence_words", 22.0))
    dialogue_floor = float(th.get("stall_max_dialogue_ratio", 0.02))
    tension_floor = float(th.get("stall_max_tension_per_1k", 1.0))
    flatline_run = int(th.get("flatline_consecutive", 3))
    findings: list[dict] = []
    stalled_seqs: list[int] = []
    stalled_by_seq: dict[int, dict] = {}
    for p in profiles:
        if p["words"] < min_words:
            continue
        is_stalled = (
            p["sentences"]["mean"] >= long_mean
            and p["register"]["dialogue_ratio"] <= dialogue_floor
            and p["tension_per_1k"] <= tension_floor
        )
        if is_stalled:
            stalled_seqs.append(p["seq"])
            stalled_by_seq[p["seq"]] = p
            findings.append(
                _finding(p, "momentum_stall", "medium",
                         {"measures": {"mean_sentence_words": p["sentences"]["mean"],
                                       "dialogue_ratio": p["register"]["dialogue_ratio"],
                                       "tension_per_1k": p["tension_per_1k"]},
                          **_opening_quote(p["text"])})
            )
    # Flatline: N consecutive stalled chapters is a story-level failure.
    run = 1
    for a, b in zip(stalled_seqs, stalled_seqs[1:], strict=False):
        run = run + 1 if b == a + 1 else 1
        if run == flatline_run:
            findings.append(
                _finding(None, "momentum_flatline", "high",
                         {"measures": {"consecutive_stalled": flatline_run,
                                       "ending_chapter": b},
                          **_story_evidence(stalled_by_seq[b])})
            )
            break
    return findings, {"stalled_chapters": stalled_seqs}


# ── Engine 17 — Theme Integrity ──────────────────────────────────────────────


def _motif_candidates(profiles: list[dict], th: dict) -> list[str]:
    """Content words recurring across most opening-third chapters."""
    min_len = int(th.get("motif_min_word_length", 5))
    presence_floor = float(th.get("motif_presence_ratio", 0.5))
    max_motifs = int(th.get("max_motifs", 8))
    live = [p for p in profiles if p["words"] > 0]
    third = max(len(live) // 3, 1)
    opening = live[:third]
    if not opening:
        return []
    presence: Counter[str] = Counter()
    frequency: Counter[str] = Counter()
    for p in opening:
        all_tokens = [
            w.lower() for w in words_of(p["text"])
            if len(w) >= min_len and w.lower() not in _THEME_STOPWORDS
        ]
        tokens = set(all_tokens) - {n.lower() for n in p["proper_nouns"]}
        presence.update(tokens)
        frequency.update(t for t in all_tokens if t in tokens)
    floor = max(2, int(len(opening) * presence_floor)) if len(opening) > 1 else 2
    # Rank by chapter presence, then by raw frequency — a motif hammered
    # thirty times must not lose its slot to a word mentioned twice.
    motifs = sorted(
        (w for w, c in presence.items() if c >= floor),
        key=lambda w: (-presence[w], -frequency[w], w),
    )
    return motifs[:max_motifs]


def detect_theme_integrity(profiles: list[dict], th: dict) -> tuple[list[dict], dict]:
    min_chapters = int(th.get("min_chapters_for_theme", 6))
    min_words = int(th.get("min_chapter_words", 400))
    live = [p for p in profiles if p["words"] > 0]
    if len(live) < min_chapters:
        return [], {"note": "too few chapters for theme analysis", "motifs": []}
    motifs = _motif_candidates(profiles, th)
    if not motifs:
        return [], {"note": "no recurring motifs derivable from the opening third",
                    "motifs": []}
    findings: list[dict] = []
    third = len(live) // 3
    for p in live[third:]:
        if p["words"] < min_words:
            continue
        tokens = {w.lower() for w in words_of(p["text"])}
        present = [m for m in motifs if m in tokens]
        if not present:
            findings.append(
                _finding(p, "theme_dropout", "low",
                         {"measures": {"motifs": motifs, "motifs_present": 0},
                          **_opening_quote(p["text"])})
            )
    return findings, {"motifs": motifs}


# ── Runner entry point ───────────────────────────────────────────────────────

DETECTORS = {
    "force.structural_enforcement": detect_structural_enforcement,
    "force.narrative_physics": detect_narrative_physics,
    "force.pressure_curve": detect_pressure_curve,
    "force.conflict_escalation": detect_conflict_escalation,
    "force.scene_purpose": detect_scene_purpose,
    "force.story_momentum": detect_story_momentum,
    "force.theme_integrity": detect_theme_integrity,
}


def run_detector(
    key: str,
    chapters: list[dict],
    thresholds: dict | None = None,
    *,
    chapter_id: str | None = None,
) -> dict:
    """Run one FORCE detector over the FULL chapter list.

    ``chapters`` must always be the whole book in seq order — the curve
    analyses are meaningless on a single chapter.  When ``chapter_id`` is
    given, book context is still computed but only findings for that
    chapter are reported (story-level findings are book-run-only, so a
    chapter-scoped run never duplicates them).
    """
    th = thresholds or {}
    profiles = build_profiles(chapters, th)
    findings, summary = DETECTORS[key](profiles, th)
    if chapter_id is not None:
        findings = [f for f in findings if f["chapter_id"] == chapter_id]
    flagged = {f["chapter_id"] for f in findings if f["chapter_id"]}
    considered = (
        [p for p in profiles if p["chapter_id"] == chapter_id]
        if chapter_id is not None else profiles
    )
    total = len([p for p in considered if p["words"] > 0]) or 1
    story_level = any(f["chapter_id"] is None for f in findings)
    return {
        "verdict": "clean" if not findings else "detected",
        "score": round(1 - min(len(flagged) + (1 if story_level else 0), total) / total, 3),
        "evidence": {
            "chapters_analyzed": len(profiles),
            "scoped_chapter_id": chapter_id,
            "summary": summary,
        },
        "findings": findings,
    }
