"""Topic graph API routes — /api/topics/*

GET  /api/topics                    → list all topics with document counts
GET  /api/topics/{topic_id}         → topic detail + member documents
POST /api/topics/rebuild            → trigger a clustering rebuild
GET  /api/library/{doc_id}/related  → related documents (semantic + topic)
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db

logger = logging.getLogger("orivellum.api.topics")

router = APIRouter(prefix="/api", tags=["topics"])


# ── GET /api/topics ──────────────────────────────────────────────────────────

@router.get("/topics")
def list_topics(with_docs: bool = False, q: str | None = None):
    """Return all topic clusters with document counts and profile summary.

    Pass ``with_docs=true`` to include ``doc_ids`` (list of document IDs) and
    ``doc_titles`` (doc_id → title mapping) in each topic — used by the library
    "By Topic" grouped view to avoid per-topic round-trips.

    Pass ``q=<term>`` to search across topic names, profile descriptions, and
    the titles/sources of member documents.  Each result includes a
    ``match_reason`` field: ``"name"``, ``"profile"``, or ``"document"``.
    """
    db = get_db()

    if q and q.strip():
        pat = f"%{q.strip()}%"
        with db._lock:
            rows = db._conn.execute(
                """SELECT t.id, t.name, t.kind, t.meta, t.created_at,
                          COUNT(tm.object_id) FILTER (WHERE tm.object_type='document') AS doc_count,
                          tp.what_it_is, tp.purpose,
                          CASE
                            WHEN t.name LIKE :pat THEN 'name'
                            WHEN tp.what_it_is LIKE :pat OR tp.purpose LIKE :pat THEN 'profile'
                            ELSE 'document'
                          END AS match_reason
                   FROM topics t
                   LEFT JOIN topic_members tm ON tm.topic_id = t.id
                   LEFT JOIN topic_profiles tp ON tp.topic_id = t.id
                   WHERE t.name LIKE :pat
                      OR tp.what_it_is LIKE :pat
                      OR tp.purpose LIKE :pat
                      OR EXISTS (
                           SELECT 1 FROM topic_members tm2
                           JOIN documents d ON d.id = tm2.object_id
                           WHERE tm2.topic_id = t.id AND tm2.object_type = 'document'
                             AND (d.title LIKE :pat OR d.source LIKE :pat)
                         )
                   GROUP BY t.id
                   ORDER BY
                     CASE WHEN t.name LIKE :pat THEN 0
                          WHEN tp.what_it_is LIKE :pat OR tp.purpose LIKE :pat THEN 1
                          ELSE 2 END,
                     doc_count DESC, t.name""",
                {"pat": pat},
            ).fetchall()
    else:
        with db._lock:
            rows = db._conn.execute(
                """SELECT t.id, t.name, t.kind, t.meta, t.created_at,
                          COUNT(tm.object_id) FILTER (WHERE tm.object_type='document') AS doc_count,
                          tp.what_it_is, tp.purpose
                   FROM topics t
                   LEFT JOIN topic_members tm ON tm.topic_id = t.id
                   LEFT JOIN topic_profiles tp ON tp.topic_id = t.id
                   GROUP BY t.id
                   ORDER BY doc_count DESC, t.name""",
            ).fetchall()

    # Optionally load full doc membership + titles in a single query
    doc_map: dict[str, list[str]] = {}  # topic_id → [doc_id, …]
    title_map: dict[str, str] = {}      # doc_id → title
    if with_docs:
        with db._lock:
            mem_rows = db._conn.execute(
                """SELECT tm.topic_id, tm.object_id, d.title
                   FROM topic_members tm
                   LEFT JOIN documents d ON d.id = tm.object_id
                   WHERE tm.object_type = 'document'"""
            ).fetchall()
        for mr in mem_rows:
            tid = mr["topic_id"]
            did = mr["object_id"]
            doc_map.setdefault(tid, []).append(did)
            if mr["title"]:
                title_map[did] = mr["title"]

    topics = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["meta"] or "{}")
        except Exception:
            pass
        entry: dict = {
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "doc_count": r["doc_count"] or 0,
            "what_it_is": r["what_it_is"] or None,
            "purpose": r["purpose"] or None,
            "meta": meta,
            "created_at": r["created_at"],
        }
        # Include match_reason when a search query was supplied
        if q and q.strip():
            keys = r.keys() if hasattr(r, "keys") else []
            if "match_reason" in keys:
                entry["match_reason"] = r["match_reason"]
        if with_docs:
            entry["doc_ids"] = doc_map.get(r["id"], [])
        topics.append(entry)

    result: dict = {"topics": topics, "total": len(topics)}
    if with_docs:
        result["doc_titles"] = title_map
    return result


# ── GET /api/topics/{topic_id} ───────────────────────────────────────────────

@router.get("/topics/{topic_id}")
def get_topic(topic_id: str):
    """Return a topic and its member documents."""
    db = get_db()
    with db._lock:
        topic_row = db._conn.execute(
            "SELECT * FROM topics WHERE id=?", (topic_id,)
        ).fetchone()
    if not topic_row:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Member documents
    with db._lock:
        doc_rows = db._conn.execute(
            """SELECT d.id, d.title, d.kind, d.readiness, d.work_id,
                      d.word_count, d.created_at
               FROM topic_members tm
               JOIN documents d ON d.id = tm.object_id
               WHERE tm.topic_id = ? AND tm.object_type = 'document'
               ORDER BY d.created_at DESC""",
            (topic_id,),
        ).fetchall()

    # Profile if available
    with db._lock:
        prof_row = db._conn.execute(
            "SELECT * FROM topic_profiles WHERE topic_id=?", (topic_id,)
        ).fetchone()

    profile = None
    if prof_row:
        profile = {
            "what_it_is": prof_row["what_it_is"],
            "purpose": prof_row["purpose"],
            "connected": json.loads(prof_row["connected"] or "[]"),
            "gaps": json.loads(prof_row["gaps"] or "[]"),
            "generated_at": prof_row["generated_at"],
        }

    meta = {}
    try:
        meta = json.loads(topic_row["meta"] or "{}")
    except Exception:
        pass

    return {
        "topic": {
            "id": topic_row["id"],
            "name": topic_row["name"],
            "kind": topic_row["kind"],
            "meta": meta,
            "created_at": topic_row["created_at"],
        },
        "profile": profile,
        "documents": [dict(r) for r in doc_rows],
        "doc_count": len(doc_rows),
    }


# ── POST /api/topics/rebuild ─────────────────────────────────────────────────

class RebuildRequest(BaseModel):
    run_profiles: bool = False  # also generate LLM topic profiles


@router.post("/topics/rebuild")
def rebuild_topics(body: RebuildRequest, background_tasks: BackgroundTasks):
    """Trigger a full topic clustering rebuild in the background."""
    db = get_db()

    run_profiles = body.run_profiles

    def _run():
        try:
            from orivellum.capabilities.cluster import run_clustering
            result = run_clustering(db)
            logger.info("Topics rebuild finished: %s", result)
        except Exception as exc:
            logger.exception("Topics rebuild failed: %s", exc)
            return  # skip profiles if clustering itself failed

        if run_profiles:
            try:
                # Honour the global AI opt-in gate before sending any content to the LLM.
                ai_enabled = db.get_setting("ai_extraction_enabled", "false").lower() == "true"
                if ai_enabled:
                    cfg = get_config()
                    from orivellum.capabilities.topic_profile import generate_topic_profiles
                    tp = generate_topic_profiles(db, cfg, force=True)
                    logger.info("Topic profiles generated: %s", tp)
                else:
                    logger.info("Topic profiles skipped — ai_extraction_enabled is not true")
            except Exception as exc:
                logger.warning("Topic profile generation failed: %s", exc)

    background_tasks.add_task(_run)
    msg = "Clustering rebuild started in background"
    if run_profiles:
        msg += " (topic profiles will be generated once clustering finishes)"
    return {"ok": True, "message": msg}


# ── GET /api/library/{doc_id}/related ────────────────────────────────────────

@router.get("/library/{doc_id}/related")
def get_related_documents(doc_id: str, limit: int = 12):
    """Return documents related to doc_id via semantic links and shared topics."""
    db = get_db()
    # Verify document exists
    with db._lock:
        exists = db._conn.execute(
            "SELECT id FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Document not found")

    related: dict[str, dict] = {}  # doc_id → entry

    # 1. Direct semantic similarity links (doc_links table)
    with db._lock:
        link_rows = db._conn.execute(
            """SELECT
                CASE WHEN dl.doc_a_id = ? THEN dl.doc_b_id ELSE dl.doc_a_id END AS other_id,
                dl.similarity,
                dl.link_type
               FROM doc_links dl
               WHERE (dl.doc_a_id = ? OR dl.doc_b_id = ?)
                 AND dl.similarity IS NOT NULL
               ORDER BY dl.similarity DESC
               LIMIT ?""",
            (doc_id, doc_id, doc_id, limit * 2),
        ).fetchall()
    for lr in link_rows:
        oid = lr["other_id"]
        if oid not in related:
            related[oid] = {
                "doc_id": oid,
                "similarity": round(float(lr["similarity"]), 3),
                "link_type": lr["link_type"],
                "shared_topics": [],
            }

    # 2. Shared topic membership
    with db._lock:
        topic_rows = db._conn.execute(
            """SELECT tm2.object_id AS other_id, t.name AS topic_name, t.id AS topic_id
               FROM topic_members tm1
               JOIN topic_members tm2 ON tm2.topic_id = tm1.topic_id
                   AND tm2.object_id != tm1.object_id
                   AND tm2.object_type = 'document'
               JOIN topics t ON t.id = tm1.topic_id
               WHERE tm1.object_id = ? AND tm1.object_type = 'document'""",
            (doc_id,),
        ).fetchall()
    for tr in topic_rows:
        oid = tr["other_id"]
        topic_entry = {"id": tr["topic_id"], "name": tr["topic_name"]}
        if oid not in related:
            related[oid] = {
                "doc_id": oid,
                "similarity": None,
                "link_type": "topic",
                "shared_topics": [topic_entry],
            }
        else:
            if topic_entry not in related[oid]["shared_topics"]:
                related[oid]["shared_topics"].append(topic_entry)

    if not related:
        return {"doc_id": doc_id, "related": []}

    # 3. Fetch document metadata for all related IDs
    other_ids = list(related.keys())[:limit]
    placeholders = ",".join(["?"] * len(other_ids))
    with db._lock:
        doc_rows = db._conn.execute(
            f"""SELECT id, title, kind, readiness, work_id, word_count, created_at
                FROM documents
                WHERE id IN ({placeholders})""",
            other_ids,
        ).fetchall()
    doc_meta = {r["id"]: dict(r) for r in doc_rows}

    # Build final list, sorted by similarity desc (None last)
    results = []
    for oid in other_ids:
        entry = related[oid]
        meta = doc_meta.get(oid, {})
        results.append({
            **entry,
            "title": meta.get("title") or "(untitled)",
            "kind": meta.get("kind"),
            "readiness": meta.get("readiness"),
            "work_id": meta.get("work_id"),
            "word_count": meta.get("word_count", 0),
        })

    results.sort(key=lambda x: (x["similarity"] is None, -(x["similarity"] or 0)))
    return {"doc_id": doc_id, "related": results[:limit]}
