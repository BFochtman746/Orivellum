/**
 * Learning hub — entry screen of the Learning app (GD-industrial primitives).
 *
 * One study workspace: Works with mastery tracking (start/resume a Socratic
 * session), mastery Projects, and the AI-knowledge review queue. Reskin +
 * reorganization only — data comes from the existing /learn, /projects and
 * /review/queue endpoints.
 */
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { useListProjects } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import {
  GraduationCap,
  Play,
  Sprout,
  Target,
  Inbox,
  ChevronRight,
  Network,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface LearnWork {
  id: string;
  title?: string;
  concept_count: number;
  graduated_count: number;
  mastery_pct: number;
  knowledge_count?: number;
}

function masteryLabel(pct: number): string {
  if (pct >= 100) return "Mastered";
  if (pct >= 80) return "Almost there";
  if (pct >= 50) return "Making progress";
  if (pct > 0) return "Getting started";
  return "Not started";
}

/** Small numeric mastery display — number + label, never color alone. */
function MasteryStat({ pct }: { pct: number }) {
  return (
    <div className="shrink-0 text-right">
      <div style={{ fontFamily: "var(--gd-data)", fontSize: 18, fontWeight: 600 }}>
        {Math.round(pct)}%
      </div>
      <div className="gd-eyebrow">{masteryLabel(pct)}</div>
    </div>
  );
}

function ProgressBar({ pct, label }: { pct: number; label: string }) {
  return (
    <div
      className="h-1 rounded-full overflow-hidden"
      style={{ background: "var(--gd-line)" }}
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div
        className="h-full rounded-full"
        style={{ width: `${Math.min(100, Math.max(0, pct))}%`, background: "var(--gd-bronze)" }}
      />
    </div>
  );
}

function SectionHeader({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <div className="pt-2 pb-3">
      <p className="gd-eyebrow">{eyebrow}</p>
      <h2
        className="mt-1"
        style={{
          fontFamily: "var(--gd-display)",
          fontSize: 20,
          fontWeight: 600,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          color: "var(--gd-text)",
        }}
      >
        {title}
      </h2>
    </div>
  );
}

export default function LearningHub() {
  const [, setLocation] = useLocation();

  const { data: learnResp, isLoading: learnLoading } = useQuery<{ works: LearnWork[] }>({
    queryKey: ["learn"],
    queryFn: () => apiFetch(`${BASE}/learn`).then((r) => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const { data: projResp, isLoading: projLoading } = useListProjects();
  const { data: reviewResp } = useQuery<{ counts_by_type?: Record<string, number> }>({
    queryKey: ["review-queue"],
    queryFn: () => apiFetch(`${BASE}/review/queue?limit=1`).then((r) => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const pendingKnowledge = reviewResp?.counts_by_type?.knowledge ?? 0;

  const learnWorks = learnResp?.works ?? [];
  const studying = learnWorks
    .filter((w) => w.concept_count > 0)
    .sort((a, b) => a.mastery_pct - b.mastery_pct); // weakest first — where study helps most
  const seedable = learnWorks.filter(
    (w) => w.concept_count === 0 && (w.knowledge_count ?? 0) > 0,
  );
  const projects = projResp?.projects ?? [];

  const totalConcepts = learnWorks.reduce((a, w) => a + w.concept_count, 0);
  const totalGraduated = learnWorks.reduce((a, w) => a + w.graduated_count, 0);

  return (
    <div className="pb-10">
      {/* Overall stats */}
      <div className="flex items-end justify-between gap-3 pt-2 pb-4">
        <div>
          <p className="gd-eyebrow">Study &amp; mastery</p>
          <h2
            className="mt-1"
            style={{
              fontFamily: "var(--gd-display)",
              fontSize: 24,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--gd-text)",
            }}
          >
            Learning
          </h2>
        </div>
        {totalConcepts > 0 && (
          <div className="text-right">
            <div style={{ fontFamily: "var(--gd-data)", fontSize: 18, fontWeight: 600 }}>
              {totalGraduated}/{totalConcepts}
            </div>
            <div className="gd-eyebrow">Concepts mastered</div>
          </div>
        )}
      </div>

      {/* Review queue — triage entry, shown first when items are waiting */}
      {pendingKnowledge > 0 && (
        <button
          className="gd-row w-full mb-4"
          onClick={() => setLocation("/learning/review")}
          data-testid="row-review-queue"
        >
          <Inbox className="w-4 h-4" style={{ color: "var(--gd-bronze)" }} aria-hidden />
          <span className="flex-1 text-left text-[14px]">
            Review AI-suggested knowledge
          </span>
          <span
            className="text-[12px] px-2 py-0.5 rounded-full"
            style={{
              fontFamily: "var(--gd-data)",
              color: "var(--gd-bronze)",
              border: "1px solid var(--gd-bronze)",
            }}
          >
            {pendingKnowledge}
          </span>
          <ChevronRight className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
        </button>
      )}

      {/* Active study */}
      <SectionHeader eyebrow="Socratic sessions" title="Active study" />
      {learnLoading ? (
        <div className="grid gap-3">
          {[1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full rounded-[10px]" />
          ))}
        </div>
      ) : studying.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {studying.map((w) => (
            <Link
              key={w.id}
              href={`/learning/session/${w.id}`}
              className="gd-tile"
              data-testid={`tile-study-${w.id}`}
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div
                    className="truncate"
                    style={{ fontFamily: "var(--gd-display)", fontSize: 17, fontWeight: 600, letterSpacing: "0.02em" }}
                  >
                    {w.title || "Untitled"}
                  </div>
                  <div className="gd-eyebrow mt-1.5">
                    {w.graduated_count}/{w.concept_count} concepts
                  </div>
                </div>
                <MasteryStat pct={w.mastery_pct} />
              </div>
              <div className="mt-2">
                <ProgressBar pct={w.mastery_pct} label={`Mastery ${Math.round(w.mastery_pct)}%`} />
              </div>
              <div
                className="flex items-center gap-1.5 pt-2 mt-auto text-[13px]"
                style={{ borderTop: "1px solid var(--gd-line)", color: "var(--gd-bronze)" }}
              >
                <Play className="w-3.5 h-3.5" aria-hidden />
                {w.mastery_pct >= 100 ? "Review again" : w.mastery_pct > 0 ? "Resume session" : "Start session"}
              </div>
            </Link>
          ))}
        </div>
      ) : (
        <div className="gd-panel text-center py-10" style={{ borderStyle: "dashed" }}>
          <GraduationCap className="w-10 h-10 mx-auto mb-3" style={{ color: "var(--gd-dim)" }} aria-hidden />
          <p className="text-[15px] font-medium">Nothing under study yet</p>
          <p className="text-[13px] mt-1 max-w-sm mx-auto" style={{ color: "var(--gd-muted)" }}>
            Pick a Work below to turn its knowledge into study concepts, then start a session.
          </p>
        </div>
      )}

      {/* Ready to seed */}
      {seedable.length > 0 && (
        <>
          <div className="mt-6" />
          <SectionHeader eyebrow="Has knowledge, no concepts yet" title="Ready to study" />
          <div className="grid gap-2">
            {seedable.map((w) => (
              <button
                key={w.id}
                className="gd-row w-full"
                onClick={() => setLocation(`/learning/session/${w.id}`)}
                data-testid={`row-seed-${w.id}`}
              >
                <Sprout className="w-4 h-4" style={{ color: "var(--gd-success)" }} aria-hidden />
                <span className="flex-1 text-left text-[14px] truncate">{w.title || "Untitled"}</span>
                <span className="gd-eyebrow">{w.knowledge_count} knowledge items</span>
                <ChevronRight className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
              </button>
            ))}
          </div>
        </>
      )}

      {/* Mastery projects */}
      <div className="mt-6" />
      <SectionHeader eyebrow="Structured tracks" title="Projects" />
      {projLoading ? (
        <Skeleton className="h-20 w-full rounded-[10px]" />
      ) : projects.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {projects.map((p: any) => (
            <Link
              key={p.id}
              href={`/projects/${p.id}`}
              className="gd-tile"
              data-testid={`tile-project-${p.id}`}
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div
                    className="truncate"
                    style={{ fontFamily: "var(--gd-display)", fontSize: 17, fontWeight: 600, letterSpacing: "0.02em" }}
                  >
                    {p.name}
                  </div>
                  {p.description && (
                    <p className="text-[13px] mt-1 line-clamp-2" style={{ color: "var(--gd-muted)" }}>
                      {p.description}
                    </p>
                  )}
                </div>
                <MasteryStat pct={(p.mastery || 0) * 100} />
              </div>
              <div className="mt-2">
                <ProgressBar pct={(p.mastery || 0) * 100} label={`Project mastery ${Math.round((p.mastery || 0) * 100)}%`} />
              </div>
            </Link>
          ))}
        </div>
      ) : (
        <button
          className="gd-row w-full"
          onClick={() => setLocation("/projects")}
          data-testid="row-new-project"
        >
          <Target className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
          <span className="flex-1 text-left text-[14px]">Start a mastery project</span>
          <ChevronRight className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
        </button>
      )}

      {/* Secondary actions */}
      <div className="mt-6 grid gap-2">
        {pendingKnowledge === 0 && (
          <button
            className="gd-row w-full"
            onClick={() => setLocation("/learning/review")}
            data-testid="row-review-queue-empty"
          >
            <Inbox className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
            <span className="flex-1 text-left text-[14px]">Knowledge review queue</span>
            <span className="gd-eyebrow">All clear</span>
            <ChevronRight className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
          </button>
        )}
        <button
          className="gd-row w-full"
          onClick={() => setLocation("/topics")}
          data-testid="row-topics"
        >
          <Network className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
          <span className="flex-1 text-left text-[14px]">Browse topic clusters</span>
          <ChevronRight className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
        </button>
      </div>
    </div>
  );
}
