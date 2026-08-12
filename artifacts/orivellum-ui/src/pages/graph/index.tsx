/**
 * Global Knowledge Graph page — cross-work entity graph with Work and
 * entity-type filters.  Accessible via /graph; linked from the Library header.
 */
import { useState } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { KnowledgeGraph, GNode } from "@/components/knowledge-graph";
import { apiFetch } from "@/lib/auth";
import { useGdDark } from "@/lib/useGdDark";
import { useDomainKindChips } from "@/lib/ontology-kinds";
import { useListWorks } from "@workspace/api-client-react";

const API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export default function GraphPage() {
  const gdDark = useGdDark();
  const [, navigate]    = useLocation();
  const [workId,    setWorkId]    = useState<string>("all");
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());
  const [refreshKey,  setRefreshKey]  = useState(0);

  const { data: worksData } = useListWorks();
  const works = worksData?.works ?? [];

  // Domain-derived filter chips: when a ratified (domain-set) Work is
  // selected, its closed ontology drives the kinds; otherwise legacy set.
  const selectedDomain =
    workId !== "all" ? ((works.find(w => w.id === workId) as any)?.domain ?? null) : null;
  const kindChips = useDomainKindChips(selectedDomain);

  // Build query params
  const params = new URLSearchParams({ limit: "250" });
  if (workId !== "all") params.set("work_id", workId);

  const queryKey = ["globalGraph", workId, refreshKey];
  const { data: graphData, isLoading, error } = useQuery({
    queryKey,
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/graph?${params}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<{
        nodes: any[];
        edges: any[];
        node_count: number;
        edge_count: number;
      }>;
    },
    staleTime: 60_000,
  });

  const toggleKind = (kind: string) => {
    setHiddenKinds(prev => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind); else next.add(kind);
      return next;
    });
  };

  const handleNavigate = (node: GNode) => {
    if (node.type === "document") {
      navigate(`/library/${node.id}`);
    }
  };

  return (
    <div className={`space-y-6 animate-in fade-in duration-500 ${gdDark ? "dark text-foreground" : ""}`}>
      {/* Header — VELLUM page-head pattern */}
      <div className="flex items-start gap-3 flex-wrap">
        <Link href="/library">
          <Button variant="ghost" size="sm" className="gap-1.5 text-xs min-h-[44px]">
            <ArrowLeft className="w-3.5 h-3.5" />
            Library
          </Button>
        </Link>
        <div className="flex-1 min-w-0">
          <span className="eyebrow mb-1">Knowledge Map</span>
          <h1 className="vellum-h1">Graph</h1>
          <div className="gilt-rule w-20" />
          <p className="text-[13px] mt-1" style={{ color: 'var(--ink-soft)' }}>
            Entities, documents, and connections across your library
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 text-xs min-h-[44px] mt-1"
          onClick={() => setRefreshKey(k => k + 1)}
          disabled={isLoading}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap rounded-xl p-3"
           style={{ border: '1px solid var(--line)', background: 'hsl(var(--card) / 0.6)' }}>
        {/* Work selector */}
        <div className="flex items-center gap-2 text-xs shrink-0" style={{ color: 'var(--ink-soft)' }}>
          <span className="font-mono uppercase tracking-wider text-[10px]">Work</span>
          <Select value={workId} onValueChange={setWorkId}>
            <SelectTrigger className="h-8 w-[160px] text-xs">
              <SelectValue placeholder="All works" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Works</SelectItem>
              {works.filter(w => w.id).map(w => (
                <SelectItem key={w.id} value={w.id!}>
                  {w.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="h-5 w-px shrink-0" style={{ background: 'var(--line)' }} />

        {/* Entity type filter chips */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] font-mono uppercase tracking-wider shrink-0" style={{ color: 'var(--ink-faint)' }}>Show</span>
          {kindChips.map(({ value, label, color }) => {
            const on = !hiddenKinds.has(value);
            return (
              <button
                key={value}
                onClick={() => toggleKind(value)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-medium border transition-all min-h-[36px] touch-manipulation
                  ${on
                    ? "bg-background border-border text-foreground"
                    : "bg-transparent border-border/30 text-muted-foreground/50"
                  }`}
              >
                <span className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: on ? color : 'var(--ink-faint)' }} />
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Graph */}
      <KnowledgeGraph
        nodes={graphData?.nodes ?? []}
        edges={graphData?.edges ?? []}
        hiddenKinds={hiddenKinds}
        onNavigate={handleNavigate}
        height={560}
        loading={isLoading}
        error={error ? String(error) : ""}
        nodeCount={graphData?.node_count}
        edgeCount={graphData?.edge_count}
      />
    </div>
  );
}
