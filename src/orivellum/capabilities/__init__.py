"""Orivellum capabilities — module registry.

Every capability module is listed here with its human-readable metadata.
``/system/tools`` and ``/system/capabilities`` read this registry at
request time so their responses are always up-to-date.

To add a new capability: append an entry to CAPABILITY_REGISTRY.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Static registry — one entry per capability module
# ---------------------------------------------------------------------------
# Keys:
#   id          short machine identifier (matches module filename without .py)
#   name        human-readable name shown in the UI
#   description one-sentence description of what it does
#   category    "ingestion" | "analysis" | "generation" | "search" | "system"
#   requires    list of external dependencies needed (empty = always available)
# ---------------------------------------------------------------------------
CAPABILITY_REGISTRY: list[dict] = [
    {
        "id": "extraction",
        "name": "Document Extraction",
        "description": "Extracts text from PDFs, DOCX, XLSX, images, and ZIP archives.",
        "category": "ingestion",
        "requires": [],
    },
    {
        "id": "chunking",
        "name": "Text Chunking",
        "description": "Splits extracted text into overlapping semantic chunks for retrieval.",
        "category": "ingestion",
        "requires": [],
    },
    {
        "id": "pipeline",
        "name": "Processing Pipeline",
        "description": "Orchestrates extraction → chunking → harvesting → embedding for each document.",
        "category": "ingestion",
        "requires": [],
    },
    {
        "id": "dedup",
        "name": "Near-Duplicate Detection",
        "description": "Detects exact and near-duplicate documents using SHA-256 and MinHash.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "knowledge_harvest",
        "name": "Knowledge Harvest",
        "description": "Rule-based extraction of facts, definitions, and claims from document chunks.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "evidence",
        "name": "Evidence Scoring",
        "description": "Scores knowledge items by corroboration strength and detects contradictions.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "completeness",
        "name": "Completeness Analysis",
        "description": "Measures how well a Work's documents cover its declared scope.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "gaps",
        "name": "Gap Detection",
        "description": "Identifies topics or questions that are under-covered across a Work's library.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "chapters",
        "name": "Chapter Analysis",
        "description": "Detects and structures chapter/section boundaries in book-length documents.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "book_intelligence",
        "name": "Book Intelligence",
        "description": "Computes readiness scores, coverage, and research counts across book chapters.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "embeddings",
        "name": "Semantic Embeddings",
        "description": "Generates vector embeddings for semantic search and similarity matching.",
        "category": "search",
        "requires": ["lemonade"],
    },
    {
        "id": "cognition",
        "name": "Deep Mode Cognition",
        "description": "Extended reasoning pass that enriches chat responses with structured thinking.",
        "category": "generation",
        "requires": ["lemonade"],
    },
    {
        "id": "intent",
        "name": "Intent Classification",
        "description": "Classifies incoming messages as recall, search, creation, or conversation.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "learning",
        "name": "Learning Concepts",
        "description": "Tracks mastery of key concepts within a Work using spaced-repetition scoring.",
        "category": "analysis",
        "requires": [],
    },
    {
        "id": "llm",
        "name": "LLM Gateway",
        "description": "Unified interface for all language-model calls with retry and token tracking.",
        "category": "system",
        "requires": ["lemonade"],
    },
    {
        "id": "mcos",
        "name": "MCOS Calibration",
        "description": "Benchmark-driven prompt health monitoring and RAG configuration sweeps.",
        "category": "system",
        "requires": [],
    },
    {
        "id": "nightshift",
        "name": "Nightshift Daemon",
        "description": "Scheduled background runner that executes all maintenance and analysis passes.",
        "category": "system",
        "requires": [],
    },
    {
        "id": "websearch",
        "name": "Web Search",
        "description": "Real-time web search to ground answers in current public information.",
        "category": "search",
        "requires": ["tavily"],
    },
    {
        "id": "weather",
        "name": "Weather",
        "description": "Fetches current weather and forecast data for location-based queries.",
        "category": "search",
        "requires": ["external_api"],
    },
]


def get_capability(capability_id: str) -> dict | None:
    """Return a single capability entry by id, or None."""
    for cap in CAPABILITY_REGISTRY:
        if cap["id"] == capability_id:
            return cap
    return None
