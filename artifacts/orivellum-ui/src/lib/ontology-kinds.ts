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
  /** A CSS token reference (var(--gd-*)), never a raw color literal. */
  color: string;
}

/**
 * Ordered semantic accent palette (CSS token strings). Cycled when a domain
 * has more kinds than entries. Consumers that need a concrete color string
 * (e.g. canvas drawing) resolve these via getComputedStyle at draw time.
 */
const PALETTE = [
  "var(--gd-info)", "var(--gd-success)", "var(--gd-caution)", "var(--gd-violet)",
  "var(--gd-bronze)", "var(--gd-sonar)", "var(--gd-danger)", "var(--gd-olive)",
  "var(--gd-slate)",
];

/** Legacy entity-kind chips — fallback when no domain ontology applies. */
export const LEGACY_ENTITY_KINDS: KindChip[] = [
  { value: "concept",   label: "Concepts",  color: "var(--gd-violet)" },
  { value: "person",    label: "People",    color: "var(--gd-info)" },
  { value: "place",     label: "Places",    color: "var(--gd-success)" },
  { value: "theme",     label: "Themes",    color: "var(--gd-caution)" },
  { value: "scripture", label: "Scripture", color: "var(--gd-danger)" },
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
