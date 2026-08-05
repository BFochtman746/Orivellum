/**
 * KnowledgeGraph — shared interactive force-directed graph component.
 *
 * Renders nodes as colored shapes (circles = entities, rectangles = documents)
 * with pan, zoom, drag, click-to-select, and an optional detail panel.
 *
 * Consumers control filtering by passing `hiddenKinds` — a Set of node `kind`
 * values to suppress from the canvas.  Document nodes are never hidden.
 *
 * `onNavigate(node)` is called when the user clicks "Open" in the detail panel,
 * allowing parent pages to route without coupling the component to a router.
 */
import { useState, useEffect, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { Loader2, X, ChevronRight, ExternalLink, Share2 } from "lucide-react";

// ── Shared types ───────────────────────────────────────────────────────────────

export interface GNode {
  id: string;
  label: string;
  type: string;   // "document" | "entity"
  kind: string;   // entity subtype or doc kind
  x: number;
  y: number;
  vx: number;
  vy: number;
  work_id?: string;
  work_title?: string;
}

export interface GEdge {
  source: string;
  target: string;
  label: string;
  type: string;
}

export const NODE_COLORS: Record<string, string> = {
  person:    "#6366f1",
  place:     "#10b981",
  concept:   "#8b5cf6",
  theme:     "#f59e0b",
  scripture: "#ef4444",
  document:  "#64748b",
  file:      "#64748b",
  pdf:       "#94a3b8",
  default:   "#a855f7",
};

export function gNodeColor(n: GNode): string {
  if (n.type === "document") return NODE_COLORS.document;
  return NODE_COLORS[n.kind] ?? NODE_COLORS.default;
}

// ── Component ──────────────────────────────────────────────────────────────────

interface KnowledgeGraphProps {
  /** Raw node list from the API. The component positions and simulates them. */
  nodes: GNode[];
  edges: GEdge[];
  /** Set of node `kind` values to hide from the canvas. Document nodes are never hidden. */
  hiddenKinds?: Set<string>;
  /** Called when the user clicks "Open" in the detail panel. */
  onNavigate?: (node: GNode) => void;
  /** Canvas height in px. Default 480. */
  height?: number;
  loading?: boolean;
  error?: string;
  nodeCount?: number;
  edgeCount?: number;
}

export function KnowledgeGraph({
  nodes: rawNodes,
  edges: rawEdges,
  hiddenKinds,
  onNavigate,
  height = 480,
  loading,
  error,
  nodeCount,
  edgeCount,
}: KnowledgeGraphProps) {
  const svgRef   = useRef<SVGSVGElement>(null);
  const nodesRef = useRef<GNode[]>([]);
  const frameRef = useRef<number>(0);
  const panRef   = useRef<{ px: number; py: number; tx: number; ty: number } | null>(null);

  const [dims,      setDims]      = useState({ w: 900, h: height });
  const [simNodes,  setSimNodes]  = useState<GNode[]>([]);
  const [selected,  setSelected]  = useState<GNode | null>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const [dragging,  setDragging]  = useState<string | null>(null);

  // ── Filter nodes by hiddenKinds ──────────────────────────────────────────────
  const visibleNodes = rawNodes.filter(
    n => n.type === "document" || !hiddenKinds?.has(n.kind)
  );
  const visibleIds   = new Set(visibleNodes.map(n => n.id));
  const visibleEdges = rawEdges.filter(
    e => visibleIds.has(e.source) && visibleIds.has(e.target)
  );

  // ── Measure container ────────────────────────────────────────────────────────
  useEffect(() => {
    const parent = svgRef.current?.parentElement;
    if (!parent) return;
    const ro = new ResizeObserver(([e]) => {
      const w = e.contentRect.width || 900;
      setDims({ w, h: height });
    });
    ro.observe(parent);
    return () => ro.disconnect();
  }, [height]);

  // ── Initialise simulation positions when node list changes ──────────────────
  useEffect(() => {
    if (!visibleNodes.length) { nodesRef.current = []; setSimNodes([]); return; }
    const cx = dims.w / 2, cy = dims.h / 2;
    const count = visibleNodes.length;
    const init: GNode[] = visibleNodes.map((n, i) => ({
      ...n,
      x: cx + Math.cos((i / count) * Math.PI * 2) * 200,
      y: cy + Math.sin((i / count) * Math.PI * 2) * 200,
      vx: (Math.random() - 0.5) * 2,
      vy: (Math.random() - 0.5) * 2,
    }));
    nodesRef.current = init;
    setSimNodes([...init]);
    setSelected(null);
    setTransform({ x: 0, y: 0, scale: 1 });
  // Rerun only when the node set identity changes (length is a fast proxy)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleNodes.length, dims.w]);

  // ── Physics loop ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!nodesRef.current.length) return;
    const REPULSE   = 4200;
    const SPRING    = 0.035;
    const SPRING_LEN = 130;
    const DAMP      = 0.80;
    const GRAVITY   = 0.006;
    let active = true;

    const tick = () => {
      if (!active) return;
      const ns = nodesRef.current;
      const cx = dims.w / 2, cy = dims.h / 2;

      for (let i = 0; i < ns.length; i++) {
        const a = ns[i];
        a.vx += (cx - a.x) * GRAVITY;
        a.vy += (cy - a.y) * GRAVITY;
        for (let j = i + 1; j < ns.length; j++) {
          const b = ns[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          const d2 = dx * dx + dy * dy + 1;
          const d  = Math.sqrt(d2);
          const f  = REPULSE / d2;
          a.vx += (dx / d) * f; a.vy += (dy / d) * f;
          b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
        }
      }

      for (const e of visibleEdges) {
        const s = ns.find(n => n.id === e.source);
        const t = ns.find(n => n.id === e.target);
        if (!s || !t) continue;
        const dx = t.x - s.x, dy = t.y - s.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (dist - SPRING_LEN) * SPRING;
        s.vx += (dx / dist) * f; s.vy += (dy / dist) * f;
        t.vx -= (dx / dist) * f; t.vy -= (dy / dist) * f;
      }

      for (const n of ns) {
        n.vx *= DAMP; n.vy *= DAMP;
        n.x  += n.vx; n.y  += n.vy;
        n.x = Math.max(18, Math.min(dims.w  - 18, n.x));
        n.y = Math.max(18, Math.min(dims.h - 18, n.y));
      }

      setSimNodes([...ns]);
      frameRef.current = requestAnimationFrame(tick);
    };

    frameRef.current = requestAnimationFrame(tick);
    return () => { active = false; cancelAnimationFrame(frameRef.current); };
  // Rerun physics when edge set or node count changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleEdges.length, visibleNodes.length, dims.w, dims.h]);

  // ── Interaction handlers ─────────────────────────────────────────────────────
  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest(".gn")) return;
    panRef.current = { px: e.clientX, py: e.clientY, tx: transform.x, ty: transform.y };
  };
  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (panRef.current) {
      setTransform(t => ({
        ...t,
        x: panRef.current!.tx + (e.clientX - panRef.current!.px),
        y: panRef.current!.ty + (e.clientY - panRef.current!.py),
      }));
    }
    if (dragging) {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return;
      const x = (e.clientX - rect.left - transform.x) / transform.scale;
      const y = (e.clientY - rect.top  - transform.y) / transform.scale;
      const n = nodesRef.current.find(n => n.id === dragging);
      if (n) { n.x = x; n.y = y; n.vx = 0; n.vy = 0; }
    }
  };
  const handleMouseUp   = () => { panRef.current = null; setDragging(null); };
  const handleWheel     = (e: React.WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    setTransform(t => ({
      ...t,
      scale: Math.max(0.15, Math.min(5, t.scale * (e.deltaY > 0 ? 0.9 : 1.1))),
    }));
  };

  // ── States ────────────────────────────────────────────────────────────────────
  if (loading) return (
    <div className="flex items-center justify-center py-32 gap-3 text-muted-foreground">
      <Loader2 className="w-5 h-5 animate-spin" /> Building graph…
    </div>
  );
  if (error) return (
    <div className="flex items-center justify-center py-32 text-destructive text-sm">
      Failed to load graph — {error}
    </div>
  );
  if (!rawNodes.length) return (
    <div className="flex flex-col items-center justify-center py-24 gap-4">
      <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
        <Share2 className="w-8 h-8 text-primary" />
      </div>
      <div className="text-center space-y-1 max-w-sm">
        <h3 className="font-serif text-xl font-medium">No entities yet</h3>
        <p className="text-sm text-muted-foreground">
          Import and process documents — entities and their connections will
          appear here as the knowledge pipeline extracts them.
        </p>
      </div>
    </div>
  );

  const selectedEdges = selected
    ? visibleEdges.filter(e => e.source === selected.id || e.target === selected.id)
    : [];

  return (
    <div className="space-y-3">
      {/* Stats row */}
      <div className="flex gap-6 text-xs text-muted-foreground font-mono">
        <span><strong className="text-foreground">{nodeCount ?? visibleNodes.length}</strong> nodes</span>
        <span><strong className="text-foreground">{edgeCount ?? visibleEdges.length}</strong> edges</span>
        <span className="ml-auto hidden sm:block text-[10px]">
          scroll to zoom · drag canvas to pan · drag node to pin · click for details
        </span>
      </div>

      <div className="flex gap-4">
        {/* Canvas */}
        <div
          className="flex-1 border border-border/50 rounded-lg overflow-hidden bg-background/30"
          style={{ height }}
        >
          <svg
            ref={svgRef}
            width="100%" height="100%"
            className="select-none cursor-grab active:cursor-grabbing"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onWheel={handleWheel}
          >
            <defs>
              <marker id="kg-arrow" viewBox="0 0 10 10" refX="20" refY="5"
                markerWidth="5" markerHeight="5" orient="auto">
                <path d="M0 0 L10 5 L0 10z" fill="#6366f1" opacity="0.6" />
              </marker>
            </defs>
            <g transform={`translate(${transform.x},${transform.y}) scale(${transform.scale})`}>
              {/* Edges */}
              {visibleEdges.map((e, i) => {
                const s = simNodes.find(n => n.id === e.source);
                const t = simNodes.find(n => n.id === e.target);
                if (!s || !t) return null;
                const isMention = e.type === "MENTIONS";
                const mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2;
                return (
                  <g key={i}>
                    <line x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                      stroke={isMention ? "#94a3b8" : "#6366f1"}
                      strokeWidth={isMention ? 0.7 : 1.4}
                      strokeOpacity={isMention ? 0.35 : 0.65}
                      strokeDasharray={isMention ? "4 3" : undefined}
                      markerEnd={!isMention ? "url(#kg-arrow)" : undefined}
                    />
                    {!isMention && (
                      <text x={mx} y={my - 5} fontSize="8" fill="#94a3b8"
                        textAnchor="middle" style={{ pointerEvents: "none" }}>
                        {e.label?.length > 22 ? e.label.slice(0, 20) + "…" : e.label}
                      </text>
                    )}
                  </g>
                );
              })}

              {/* Nodes */}
              {simNodes.map(n => {
                const isDoc = n.type === "document";
                const r     = isDoc ? 10 : 8;
                const col   = gNodeColor(n);
                const isSel = selected?.id === n.id;
                return (
                  <g key={n.id} className="gn"
                    style={{ cursor: "pointer" }}
                    transform={`translate(${n.x},${n.y})`}
                    onClick={() => setSelected(prev => prev?.id === n.id ? null : n)}
                    onMouseDown={ev => { ev.stopPropagation(); setDragging(n.id); }}
                  >
                    {isDoc
                      ? <rect x={-r} y={-r} width={r * 2} height={r * 2} rx={2}
                          fill={col} fillOpacity={isSel ? 1 : 0.8}
                          stroke={isSel ? "#fff" : "none"} strokeWidth={2} />
                      : <circle r={r} fill={col} fillOpacity={isSel ? 1 : 0.8}
                          stroke={isSel ? "#fff" : "none"} strokeWidth={2} />
                    }
                    <text dy="1.9em" fontSize="9" fill="#94a3b8"
                      textAnchor="middle" style={{ pointerEvents: "none" }}>
                      {n.label.length > 16 ? n.label.slice(0, 14) + "…" : n.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>

        {/* Detail panel */}
        {selected && (
          <div className="w-52 border border-border/50 rounded-lg p-4 space-y-3 text-sm shrink-0">
            <div className="flex items-start justify-between gap-1">
              <span className="font-medium text-foreground break-words leading-snug">
                {selected.label}
              </span>
              <button
                onClick={() => setSelected(null)}
                className="text-muted-foreground hover:text-foreground shrink-0 mt-0.5"
              >
                <X className="w-3 h-3" />
              </button>
            </div>

            <div className="flex gap-1.5 flex-wrap">
              <Badge variant="outline" className="text-[10px] font-mono uppercase">
                {selected.type}
              </Badge>
              {selected.kind && selected.kind !== selected.type && (
                <Badge variant="outline" className="text-[10px] font-mono">
                  {selected.kind}
                </Badge>
              )}
            </div>

            {/* Navigate button for document nodes (or if caller supports entity nav) */}
            {onNavigate && (
              <button
                onClick={() => onNavigate(selected)}
                className="flex items-center gap-1.5 text-xs text-primary hover:text-primary/80 font-medium w-full"
              >
                <ExternalLink className="w-3 h-3 shrink-0" />
                Open{selected.type === "document" ? " document" : ""}
              </button>
            )}

            {selected.work_title && (
              <p className="text-[10px] text-muted-foreground truncate">
                Work: {selected.work_title}
              </p>
            )}

            {/* Connections list */}
            <div className="space-y-1">
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                Connections ({selectedEdges.length})
              </p>
              {selectedEdges.slice(0, 8).map((e, i) => {
                const otherId = e.source === selected.id ? e.target : e.source;
                const other   = simNodes.find(n => n.id === otherId);
                return (
                  <button key={i}
                    className="flex items-center gap-1.5 text-left text-xs text-muted-foreground hover:text-foreground w-full"
                    onClick={() => setSelected(other ?? null)}
                  >
                    <ChevronRight className="w-3 h-3 shrink-0" />
                    <span className="truncate">{other?.label ?? otherId}</span>
                  </button>
                );
              })}
              {selectedEdges.length === 0 && (
                <p className="text-xs text-muted-foreground italic">No visible connections</p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex gap-4 flex-wrap text-xs text-muted-foreground">
        {([
          { kind: "concept",   label: "Concept" },
          { kind: "person",    label: "Person"  },
          { kind: "place",     label: "Place"   },
          { kind: "theme",     label: "Theme"   },
          { kind: "scripture", label: "Scripture" },
          { kind: "document",  label: "Document" },
        ] as const).map(({ kind, label }) => (
          <span key={kind} className="flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-full inline-block shrink-0"
              style={{ background: NODE_COLORS[kind] }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
