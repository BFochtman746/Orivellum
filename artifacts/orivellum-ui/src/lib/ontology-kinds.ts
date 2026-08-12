/**
 * Domain-derived graph filter chips (THE RE-PROJECTION Phases 5-6).
 *
 * A ratified Work has a closed domain ontology served by GET /api/ontology.
 * Graph views derive their kind-filter chips from that ontology when a
 * domain-set Work is selected; the legacy hardcoded set remains the fallback
 * for "all works" views and Works without a ratified domain.
 */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";

const API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export interface KindChip {
  value: string;
  label: string;
  color: string;
}

/** Legacy entity-kind chips — fallback when no domain ontology applies. */
export const LEGACY_ENTITY_KINDS: KindChip[] = [
  { value: "concept",   label: "Concepts",  color: "#8b5cf6" },
  { value: "person",    label: "People",    color: "#6366f1" },
  { value: "place",     label: "Places",    color: "#10b981" },
  { value: "theme",     label: "Themes",    color: "#f59e0b" },
  { value: "scripture", label: "Scripture", color: "#ef4444" },
];

// Stable palette for domain-derived kinds (cycled when a domain has more).
const PALETTE = [
  "#8b5cf6", "#6366f1", "#10b981", "#f59e0b", "#ef4444",
  "#06b6d4", "#ec4899", "#84cc16",
];

interface OntologyResponse {
  domains: Record<string, string[]>;
  permitted_doc_types: Record<string, string[]>;
}

export function useOntology() {
  return useQuery({
    queryKey: ["ontology"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/ontology`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<OntologyResponse>;
    },
    staleTime: Infinity,
  });
}

function titleCase(kind: string): string {
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

/**
 * Kind-filter chips for a Work: its domain ontology when ratified,
 * the legacy entity set otherwise.
 */
export function useDomainKindChips(domain: string | null | undefined): KindChip[] {
  const { data } = useOntology();
  const kinds = domain ? data?.domains?.[domain] : undefined;
  if (!kinds?.length) return LEGACY_ENTITY_KINDS;
  return kinds.map((k, i) => ({
    value: k,
    label: titleCase(k),
    color: PALETTE[i % PALETTE.length],
  }));
}
