"""RIPPLE — blast-radius simulation on the ATLAS-O world graph (E12 / M17).

Propose a change to a canon fact or a graph node and, BEFORE committing
anything, walk the typed world graph outward and report every downstream
chapter, character, and canon fact whose evidence depends on what you are
about to change — each with the evidence path (edge chain with verbatim
quotes) that connects it to the seed.

Everything here is read-only and deterministic: the simulation never
mutates the graph, canon, or prose.  It cannot tell you whether the change
is *right* — only what it would cost.

Two entry points:

* :func:`simulate_ripple` — seed by graph node id, canon fact id, or a
  node name; used by the canon-change preview and the works-level API.
* :func:`ripple_for_chapter` — seed with every node evidenced in one
  chapter; used by the BAND surgical-edit flow to show the blast radius
  of editing that chapter before the edit is applied.
"""

from __future__ import annotations

from collections import deque
from typing import Any

DEFAULT_DEPTH = 3
MAX_DEPTH = 6
MAX_NODES = 500
_NODE_LIMIT = 10_000
_EDGE_LIMIT = 40_000


class RippleError(ValueError):
    """The simulation cannot run as requested (bad seed / no graph)."""


def _load_graph(db: Any, work_id: str) -> tuple[dict[str, dict], list[dict]]:
    nodes = {n["id"]: n for n in db.list_graph_nodes(work_ids=[work_id], limit=_NODE_LIMIT)}
    edges = db.list_graph_edges(work_ids=[work_id], limit=_EDGE_LIMIT)
    return nodes, edges


def _resolve_seeds(
    nodes: dict[str, dict],
    *,
    node_id: str | None,
    canon_fact_id: str | None,
    name: str | None,
) -> list[dict]:
    if sum(x is not None for x in (node_id, canon_fact_id, name)) != 1:
        raise RippleError("provide exactly one of node_id, canon_fact_id, or name")
    if node_id is not None:
        node = nodes.get(node_id)
        if node is None:
            raise RippleError(f"graph node {node_id!r} not found in this work")
        return [node]
    if canon_fact_id is not None:
        seeds = [n for n in nodes.values() if n.get("canon_fact_id") == canon_fact_id]
        if not seeds:
            raise RippleError(
                "no graph nodes are linked to that canon fact in this work — "
                "the graph carries no evidence of it, so there is no ripple to walk"
            )
        return seeds
    low = (name or "").strip().lower()
    if not low:
        raise RippleError("name must not be empty")
    seeds = [n for n in nodes.values() if (n.get("name") or "").strip().lower() == low]
    if not seeds:
        raise RippleError(f"no graph node named {name!r} in this work")
    return seeds


def _walk(
    nodes: dict[str, dict],
    edges: list[dict],
    seed_ids: set[str],
    *,
    max_depth: int,
    max_nodes: int,
) -> tuple[dict[str, dict], bool]:
    """BFS outward from the seeds; shortest evidence path per reached node.

    Edges are walked in both directions (a change ripples to everything the
    evidence connects, regardless of who is src), but each hop records the
    stored direction so the path stays readable.
    """
    adjacency: dict[str, list[tuple[str, dict, str]]] = {}
    for e in edges:
        src, dst = e.get("src"), e.get("dst")
        if src in nodes and dst in nodes:
            adjacency.setdefault(src, []).append((dst, e, "out"))
            adjacency.setdefault(dst, []).append((src, e, "in"))
    reached: dict[str, dict] = {
        nid: {"depth": 0, "path": []} for nid in seed_ids if nid in nodes
    }
    queue: deque[str] = deque(reached)
    truncated = False
    while queue:
        cur = queue.popleft()
        entry = reached[cur]
        if entry["depth"] >= max_depth:
            continue
        for nxt, edge, direction in adjacency.get(cur, ()):
            if nxt in reached:
                continue
            if len(reached) >= max_nodes:
                truncated = True
                queue.clear()
                break
            hop = {
                "from_node_id": cur,
                "from_name": nodes[cur]["name"],
                "edge_type": edge["edge_type"],
                "direction": direction,
                "to_node_id": nxt,
                "to_name": nodes[nxt]["name"],
                "chapter_id": edge.get("chapter_id"),
                "evidence_quote": edge.get("evidence_quote") or "",
                "evidence_offset": edge.get("evidence_offset"),
            }
            reached[nxt] = {"depth": entry["depth"] + 1, "path": entry["path"] + [hop]}
            queue.append(nxt)
    return reached, truncated


def _chapter_meta(db: Any, chapter_ids: set[str]) -> dict[str, dict]:
    if not chapter_ids:
        return {}
    marks = ",".join("?" for _ in chapter_ids)
    rows = db.read_conn().execute(
        f"SELECT id, seq, title FROM book_chapters WHERE id IN ({marks})",
        tuple(chapter_ids),
    ).fetchall()
    return {r["id"]: {"seq": r["seq"], "title": r["title"] or ""} for r in rows}


def _fact_meta(db: Any, fact_ids: set[str]) -> dict[str, dict]:
    if not fact_ids:
        return {}
    marks = ",".join("?" for _ in fact_ids)
    rows = db.read_conn().execute(
        f"SELECT id, statement, classification, status FROM canon_fact "
        f"WHERE id IN ({marks})",
        tuple(fact_ids),
    ).fetchall()
    return {
        r["id"]: {
            "statement": r["statement"],
            "classification": r["classification"],
            "status": r["status"],
        }
        for r in rows
    }


def _clamp(value: int | None, default: int, ceiling: int) -> int:
    v = default if value is None else int(value)
    if v < 1:
        raise RippleError("depth must be at least 1")
    return min(v, ceiling)


def _report(
    db: Any,
    nodes: dict[str, dict],
    reached: dict[str, dict],
    seed_ids: set[str],
    *,
    truncated: bool,
    exclude_chapter_id: str | None = None,
) -> dict:
    """Assemble the blast-radius report from a completed walk."""
    affected = {nid: r for nid, r in reached.items() if nid not in seed_ids}

    def _node_view(nid: str) -> dict:
        n, r = nodes[nid], reached[nid]
        return {
            "node_id": nid,
            "name": n["name"],
            "node_type": n["node_type"],
            "chapter_id": n.get("chapter_id"),
            "canon_fact_id": n.get("canon_fact_id"),
            "depth": r["depth"],
            "path": r["path"],
        }

    # Affected chapters: every chapter that holds an affected node or an
    # evidence hop on a path — that prose depends on the changed thing.
    chapter_hits: dict[str, dict] = {}

    def _touch_chapter(cid: str | None, *, node_name: str | None, quote: str) -> None:
        if not cid or cid == exclude_chapter_id:
            return
        hit = chapter_hits.setdefault(cid, {"nodes": set(), "evidence": []})
        if node_name:
            hit["nodes"].add(node_name)
        if quote and len(hit["evidence"]) < 5 and quote not in hit["evidence"]:
            hit["evidence"].append(quote)

    for nid, r in affected.items():
        n = nodes[nid]
        _touch_chapter(n.get("chapter_id"), node_name=n["name"],
                       quote=n.get("evidence_quote") or "")
        for hop in r["path"]:
            _touch_chapter(hop["chapter_id"], node_name=hop["to_name"],
                           quote=hop["evidence_quote"])

    meta = _chapter_meta(db, set(chapter_hits))
    chapters = sorted(
        (
            {
                "chapter_id": cid,
                "seq": meta.get(cid, {}).get("seq"),
                "title": meta.get(cid, {}).get("title", ""),
                "nodes": sorted(hit["nodes"]),
                "evidence": hit["evidence"],
            }
            for cid, hit in chapter_hits.items()
        ),
        key=lambda c: (c["seq"] is None, c["seq"]),
    )

    characters = sorted(
        (_node_view(nid) for nid in affected if nodes[nid]["node_type"] == "Character"),
        key=lambda c: (c["depth"], c["name"]),
    )

    fact_ids = {
        nodes[nid]["canon_fact_id"]
        for nid in affected
        if nodes[nid].get("canon_fact_id")
    }
    seed_fact_ids = {
        nodes[nid]["canon_fact_id"] for nid in seed_ids
        if nid in nodes and nodes[nid].get("canon_fact_id")
    }
    fact_ids -= seed_fact_ids
    fmeta = _fact_meta(db, fact_ids)
    facts = sorted(
        (
            {
                "canon_fact_id": fid,
                **fmeta.get(fid, {}),
                "via_nodes": sorted(
                    nodes[nid]["name"] for nid in affected
                    if nodes[nid].get("canon_fact_id") == fid
                ),
            }
            for fid in fact_ids
        ),
        key=lambda f: f["canon_fact_id"],
    )

    return {
        "seeds": [_node_view(nid) for nid in seed_ids if nid in nodes],
        "affected_nodes": sorted(
            (_node_view(nid) for nid in affected),
            key=lambda v: (v["depth"], v["name"]),
        ),
        "affected_chapters": chapters,
        "affected_characters": characters,
        "affected_facts": facts,
        "counts": {
            "nodes": len(affected),
            "chapters": len(chapters),
            "characters": len(characters),
            "facts": len(facts),
        },
        "truncated": truncated,
    }


def simulate_ripple(
    db: Any,
    work_id: str,
    *,
    node_id: str | None = None,
    canon_fact_id: str | None = None,
    name: str | None = None,
    depth: int | None = None,
    max_nodes: int = MAX_NODES,
) -> dict:
    """Blast radius of changing one node / canon fact / named entity."""
    max_depth = _clamp(depth, DEFAULT_DEPTH, MAX_DEPTH)
    nodes, edges = _load_graph(db, work_id)
    if not nodes:
        raise RippleError(
            "this work has no ATLAS graph yet — build the world graph first"
        )
    seeds = _resolve_seeds(nodes, node_id=node_id, canon_fact_id=canon_fact_id, name=name)
    seed_ids = {s["id"] for s in seeds}
    reached, truncated = _walk(nodes, edges, seed_ids, max_depth=max_depth,
                               max_nodes=max_nodes)
    report = _report(db, nodes, reached, seed_ids, truncated=truncated)
    report["work_id"] = work_id
    report["depth"] = max_depth
    return report


def ripple_for_chapter(
    db: Any,
    work_id: str,
    chapter_id: str,
    *,
    depth: int | None = None,
    max_nodes: int = MAX_NODES,
) -> dict:
    """Blast radius of editing one chapter: seed with every node evidenced
    there and report only what lies OUTSIDE the chapter being edited."""
    max_depth = _clamp(depth, 2, MAX_DEPTH)
    nodes, edges = _load_graph(db, work_id)
    seeds = [n for n in nodes.values() if n.get("chapter_id") == chapter_id]
    if not seeds:
        return {
            "work_id": work_id,
            "chapter_id": chapter_id,
            "depth": max_depth,
            "seeds": [],
            "affected_nodes": [],
            "affected_chapters": [],
            "affected_characters": [],
            "affected_facts": [],
            "counts": {"nodes": 0, "chapters": 0, "characters": 0, "facts": 0},
            "truncated": False,
            "note": (
                "no graph nodes are evidenced in this chapter — "
                "either the world graph has not been built or the chapter "
                "introduces nothing the graph tracks"
            ),
        }
    seed_ids = {s["id"] for s in seeds}
    reached, truncated = _walk(nodes, edges, seed_ids, max_depth=max_depth,
                               max_nodes=max_nodes)
    report = _report(db, nodes, reached, seed_ids, truncated=truncated,
                     exclude_chapter_id=chapter_id)
    report["work_id"] = work_id
    report["chapter_id"] = chapter_id
    report["depth"] = max_depth
    return report
