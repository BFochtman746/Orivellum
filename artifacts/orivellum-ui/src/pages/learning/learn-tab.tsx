/**
 * LearnTab — the Socratic study loop (question → assess → feedback) for one
 * Work, plus its analytics panel, concept map, and dependency map.
 *
 * Extracted from pages/works/detail.tsx and restyled with the GD-industrial
 * token layer (gd-panel / gd-chip / --gd-* colors) so it reads as one product
 * inside the dark Learning frame. Behavior is unchanged: auto-seeding,
 * blocked/interleaved modes, the 10-question interleaved cap, error-type
 * feedback cards, and the study/analytics section toggle.
 *
 * Imported by both the Works detail "Learn" tab and the focused session
 * screen at /learning/session/:workId.
 */
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { toast } from "sonner";
import {
  AlertCircle,
  AlertTriangle,
  BarChart2,
  BookOpen,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  GitBranch,
  HelpCircle,
  Loader2,
  Lock,
  RefreshCw,
  RotateCcw,
  Shuffle,
  TrendingUp,
  Trophy,
  Wrench,
  Zap,
} from "lucide-react";

const LEARN_API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ────────────────────────────────────────────────────────────────────

type LearnPhase = "loading" | "seeding" | "question" | "assessing" | "feedback" | "all_done";
type RouteAction = "STEP_FORWARD" | "STEP_BACKWARD" | "STAY_HERE";

type QuestionLevel = "recall" | "self_explanation" | "contrast" | "transfer";

interface LearningSession {
  concept_id: string;
  subject: string;
  description: string;
  question: string;
  context_snippet: string;
  question_type: QuestionLevel;
  contrast_subject: string | null;           // neighbour concept for contrast questions
  session_mode: "blocked" | "interleaved";   // mode that produced this question
}

interface RubricCriterion {
  criterion: string;
  met: boolean;
  quote: string;
}

interface TeachBackSession {
  concept_id: string;
  subject: string;
  prompt: string;
}

interface TeachBackResult {
  score: number;
  passed: boolean;
  graduated: boolean;
  feedback: string;
  student_followup: string | null;
  rubric: RubricCriterion[] | null;
  diagnosis: string | null;
  research_request_id: string | null;
}

type ErrorType = "careless_slip" | "procedural_gap" | "conceptual_misconception" | "knowledge_gap" | null;

interface AssessResult {
  score: number;
  feedback: string;
  route: RouteAction;
  graduated: boolean;
  next_concept_id: string | null;
  summary: { total: number; graduated: number; mastery_pct: number };
  // Error classification (v95)
  error_type: ErrorType;
  remediation_hint: string | null;
  deep_review_needed: boolean;
  socratic_followup: string | null;
  suggested_prereq_id: string | null;
  suggested_prereq_subject: string | null;
  question_type: QuestionLevel;
  // Depth ladder (v139)
  rubric: RubricCriterion[] | null;
  diagnosis: "never_learned" | "learned_and_decayed" | "corpus_insufficient" | null;
  research_request_id: string | null;
}

// ─── Shared GD styling helpers ────────────────────────────────────────────────

/** Score → semantic token (dual-coded everywhere with the numeric % label). */
const scoreColor = (score: number) =>
  score >= 0.75 ? "var(--gd-success)" : score >= 0.5 ? "var(--gd-caution)" : "var(--gd-danger)";

/** Accent-filled primary action button (48px thumb target). */
function GdPrimaryButton({
  onClick, disabled, children, testId,
}: { onClick: () => void; disabled?: boolean; children: React.ReactNode; testId?: string }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className="inline-flex items-center justify-center gap-2 rounded-[8px] px-5 min-h-12 text-[13px] font-semibold disabled:opacity-50 transition-transform active:scale-[0.97]"
      style={{ background: "var(--gd-accent)", color: "var(--gd-accent-ink)" }}
    >
      {children}
    </button>
  );
}

/** Bordered secondary action button. */
function GdOutlineButton({
  onClick, disabled, children, tone = "line", testId, className = "",
}: {
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  tone?: "line" | "caution" | "accent";
  testId?: string;
  className?: string;
}) {
  const color =
    tone === "caution" ? "var(--gd-caution)" : tone === "accent" ? "var(--gd-accent)" : "var(--gd-muted)";
  const border =
    tone === "caution" ? "var(--gd-caution)" : tone === "accent" ? "var(--gd-accent)" : "var(--gd-line)";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={`inline-flex items-center justify-center gap-2 rounded-[8px] px-4 min-h-12 text-[13px] font-medium disabled:opacity-50 transition-transform active:scale-[0.97] ${className}`}
      style={{ color, border: `1px solid ${border}`, background: "var(--gd-card)" }}
    >
      {children}
    </button>
  );
}

/** Small engineering-plate heading used inside panels. */
function PanelHeading({ icon, title, aside }: { icon: React.ReactNode; title: string; aside?: string }) {
  return (
    <div className="flex items-center gap-2">
      {icon}
      <h4
        className="text-[12px] font-semibold"
        style={{
          fontFamily: "var(--gd-display)",
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "var(--gd-text)",
        }}
      >
        {title}
      </h4>
      {aside && (
        <span className="ml-auto text-[10px]" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
          {aside}
        </span>
      )}
    </div>
  );
}

// ─── Error-type feedback sub-components ──────────────────────────────────────

function CarelessSlipCard({ feedback, onRetry }: { feedback: string; onRetry: () => void }) {
  return (
    <div className="space-y-3">
      <div
        className="flex items-start gap-3 p-4 rounded-[10px]"
        style={{ border: "1px solid var(--gd-caution)", background: "var(--gd-caution-soft)" }}
      >
        <div
          className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5"
          style={{ background: "var(--gd-caution-soft)" }}
        >
          <AlertCircle className="w-4 h-4" style={{ color: "var(--gd-caution)" }} aria-hidden />
        </div>
        <div className="flex-1 space-y-0.5">
          <p className="text-sm font-semibold" style={{ color: "var(--gd-caution)" }}>Almost — small slip</p>
          <p className="text-sm leading-relaxed" style={{ color: "var(--gd-text)" }}>{feedback}</p>
        </div>
      </div>
      <GdOutlineButton tone="caution" onClick={onRetry} testId="button-retry-slip">
        <RefreshCw className="w-3.5 h-3.5" aria-hidden /> Try once more
      </GdOutlineButton>
    </div>
  );
}

function ProceduralGapCard({ feedback, remediationHint }: { feedback: string; remediationHint: string | null }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="space-y-3">
      <div
        className="flex items-start gap-3 p-4 rounded-[10px]"
        style={{ border: "1px solid var(--gd-info)", background: "var(--gd-slate-soft)" }}
      >
        <div
          className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5"
          style={{ background: "var(--gd-slate-soft)" }}
        >
          <Wrench className="w-4 h-4" style={{ color: "var(--gd-info)" }} aria-hidden />
        </div>
        <div className="flex-1 space-y-0.5">
          <p className="text-sm font-semibold" style={{ color: "var(--gd-info)" }}>Procedural gap</p>
          <p className="text-sm leading-relaxed" style={{ color: "var(--gd-text)" }}>{feedback}</p>
        </div>
      </div>
      {remediationHint && (
        <div>
          <button
            onClick={() => setExpanded(e => !e)}
            className="flex items-center gap-1.5 text-xs min-h-9 transition-colors"
            style={{ fontFamily: "var(--gd-data)", color: "var(--gd-info)" }}
          >
            <ChevronRight
              className={`w-3.5 h-3.5 transition-transform duration-200 ${expanded ? "rotate-90" : ""}`}
              aria-hidden
            />
            {expanded ? "Hide" : "Show"} worked example
          </button>
          {expanded && (
            <div className="mt-2 gd-panel space-y-1">
              <p className="gd-eyebrow mb-2">Step-by-step hint</p>
              <p className="text-sm leading-relaxed">{remediationHint}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConceptualMisconceptionCard({
  feedback, remediationHint, socraticFollowup, deepReviewNeeded,
}: {
  feedback: string;
  remediationHint: string | null;
  socraticFollowup: string | null;
  deepReviewNeeded: boolean;
}) {
  return (
    <div className="space-y-3">
      {deepReviewNeeded && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-[8px] text-xs"
          style={{
            fontFamily: "var(--gd-data)",
            color: "var(--gd-caution)",
            border: "1px solid var(--gd-caution)",
            background: "var(--gd-caution-soft)",
          }}
        >
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" aria-hidden />
          Deep review needed — this misconception has appeared multiple times
        </div>
      )}
      <div
        className="flex items-start gap-3 p-4 rounded-[10px]"
        style={{ border: "1px solid var(--gd-violet)", background: "var(--gd-card)" }}
      >
        <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5"
          style={{ background: "var(--gd-card-hi)" }}>
          <HelpCircle className="w-4 h-4" style={{ color: "var(--gd-violet)" }} aria-hidden />
        </div>
        <div className="flex-1 space-y-0.5">
          <p className="text-sm font-semibold" style={{ color: "var(--gd-violet)" }}>Conceptual misconception</p>
          <p className="text-sm leading-relaxed" style={{ color: "var(--gd-text)" }}>{feedback}</p>
        </div>
      </div>
      {socraticFollowup && (
        <div
          className="p-4 rounded-[10px] space-y-2"
          style={{ border: "1px solid var(--gd-line)", background: "var(--gd-surface)" }}
        >
          <p className="gd-eyebrow" style={{ color: "var(--gd-violet)" }}>Socratic follow-up</p>
          {/* AI-written follow-up — violet per the machine-text rule */}
          <p className="text-sm font-medium leading-relaxed" style={{ color: "var(--gd-violet)" }}>
            {socraticFollowup}
          </p>
        </div>
      )}
      {!socraticFollowup && remediationHint && (
        <p className="text-sm italic px-1" style={{ color: "var(--gd-muted)" }}>{remediationHint}</p>
      )}
    </div>
  );
}

function KnowledgeGapCard({
  feedback, remediationHint, prereqSubject, prereqId, onStudyPrereq,
}: {
  feedback: string;
  remediationHint: string | null;
  prereqSubject: string | null;
  prereqId: string | null;
  onStudyPrereq: (id: string) => void;
}) {
  return (
    <div className="space-y-3">
      <div
        className="flex items-start gap-3 p-4 rounded-[10px]"
        style={{ border: "1px solid var(--gd-danger)", background: "var(--gd-card)" }}
      >
        <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5"
          style={{ background: "var(--gd-card-hi)" }}>
          <BookOpen className="w-4 h-4" style={{ color: "var(--gd-danger)" }} aria-hidden />
        </div>
        <div className="flex-1 space-y-0.5">
          <p className="text-sm font-semibold" style={{ color: "var(--gd-danger)" }}>Knowledge gap</p>
          <p className="text-sm leading-relaxed" style={{ color: "var(--gd-text)" }}>{feedback}</p>
        </div>
      </div>
      {prereqId && prereqSubject && (
        <div className="gd-row justify-between">
          <div className="min-w-0">
            <p className="gd-eyebrow">Suggested prerequisite</p>
            <p className="text-sm font-medium mt-0.5 truncate">{prereqSubject}</p>
          </div>
          <GdOutlineButton tone="accent" className="shrink-0 ml-3" onClick={() => onStudyPrereq(prereqId)}>
            Study first <ChevronRight className="w-3 h-3" aria-hidden />
          </GdOutlineButton>
        </div>
      )}
      {remediationHint && !prereqId && (
        <p className="text-sm italic px-1" style={{ color: "var(--gd-muted)" }}>{remediationHint}</p>
      )}
    </div>
  );
}

// ─── Analytics panel ──────────────────────────────────────────────────────────

const ERROR_TYPE_LABELS: Record<string, string> = {
  careless_slip:            "Careless slip",
  procedural_gap:           "Procedural gap",
  conceptual_misconception: "Misconception",
  knowledge_gap:            "Knowledge gap",
};

function VelocitySparkline({ weeks }: { weeks: { week: string; graduated: number }[] }) {
  const max = Math.max(...weeks.map(w => w.graduated), 1);
  const W = 220, H = 52, pad = 6;
  const step = (W - pad * 2) / Math.max(weeks.length - 1, 1);
  const pts = weeks.map((w, i) => ({
    x: pad + i * step,
    y: H - pad - ((w.graduated / max) * (H - pad * 2)),
    v: w.graduated,
    label: w.week,
  }));
  const polyline = pts.map(p => `${p.x},${p.y}`).join(" ");
  return (
    <div className="space-y-2">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="overflow-visible">
        {/* baseline */}
        <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad}
          stroke="var(--gd-line)" strokeWidth={1} />
        {/* sparkline */}
        <polyline points={polyline} fill="none"
          stroke="var(--gd-accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {/* dots */}
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r={3.5} fill="var(--gd-accent)" />
            {p.v > 0 && (
              <text x={p.x} y={p.y - 7} textAnchor="middle"
                fontSize={9} fill="var(--gd-text)" fontFamily="var(--gd-data)">{p.v}</text>
            )}
          </g>
        ))}
      </svg>
      <div className="flex justify-between text-[9px]" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
        {pts.map((p, i) => <span key={i}>{p.label}</span>)}
      </div>
    </div>
  );
}

function AnalyticsPanel({ workId }: { workId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["learnAnalytics", workId],
    queryFn: () =>
      apiFetch(`${LEARN_API_BASE}/works/${workId}/learning/analytics`)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  if (isLoading) return (
    <div className="flex items-center justify-center py-16 gap-3 text-sm"
      style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
      <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> Loading analytics…
    </div>
  );

  if (error || !data) return (
    <div className="py-8 text-center text-sm" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
      Could not load analytics — study a few concepts first.
    </div>
  );

  const { velocity, stuck, retention_forecast, session_history, distribution } = data as any;
  const totalDistrib = distribution.total || 1;

  return (
    <div className="space-y-4">

      {/* Mastery distribution bar */}
      <div className="gd-panel space-y-3">
        <PanelHeading
          icon={<BarChart2 className="w-4 h-4" style={{ color: "var(--gd-accent)" }} aria-hidden />}
          title="Mastery Distribution"
        />
        <div className="h-2.5 rounded-full overflow-hidden flex gap-0.5" style={{ background: "var(--gd-surface)" }}>
          {distribution.graduated > 0 && (
            <div className="transition-all" style={{ background: "var(--gd-success)", width: `${distribution.graduated / totalDistrib * 100}%` }} title={`Graduated: ${distribution.graduated}`} />
          )}
          {distribution.due_for_review > 0 && (
            <div className="transition-all" style={{ background: "var(--gd-caution)", width: `${distribution.due_for_review / totalDistrib * 100}%` }} title={`Due for review: ${distribution.due_for_review}`} />
          )}
          {distribution.in_progress > 0 && (
            <div className="transition-all" style={{ background: "var(--gd-accent)", width: `${distribution.in_progress / totalDistrib * 100}%` }} title={`In progress: ${distribution.in_progress}`} />
          )}
          {distribution.not_started > 0 && (
            <div className="transition-all" style={{ background: "var(--gd-line-2)", width: `${distribution.not_started / totalDistrib * 100}%` }} title={`Not started: ${distribution.not_started}`} />
          )}
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px]" style={{ fontFamily: "var(--gd-data)" }}>
          {[
            { label: "Graduated",      value: distribution.graduated,      color: "var(--gd-success)" },
            { label: "Due for review", value: distribution.due_for_review, color: "var(--gd-caution)" },
            { label: "In progress",    value: distribution.in_progress,    color: "var(--gd-accent)" },
            { label: "Not started",    value: distribution.not_started,    color: "var(--gd-muted)" },
          ].map(({ label, value, color }) => (
            <div key={label} className="flex items-center justify-between gap-2">
              <span style={{ color: "var(--gd-muted)" }}>{label}</span>
              <span className="font-semibold" style={{ color }}>{value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Velocity sparkline */}
      <div className="gd-panel space-y-3">
        <PanelHeading
          icon={<TrendingUp className="w-4 h-4" style={{ color: "var(--gd-accent)" }} aria-hidden />}
          title="Graduation Velocity"
          aside="concepts / week"
        />
        <VelocitySparkline weeks={velocity} />
        {(() => {
          const thisWeek = velocity.find((w: any) => w.week === "This week")?.graduated ?? 0;
          const lastWeek = velocity.find((w: any) => w.week === "Last week")?.graduated ?? 0;
          const trend = thisWeek >= lastWeek
            ? { label: "On track", color: "var(--gd-success)" }
            : { label: "Falling behind", color: "var(--gd-caution)" };
          return (
            <p className="text-[11px] font-semibold" style={{ fontFamily: "var(--gd-data)", color: trend.color }}>
              {trend.label} — {thisWeek} graduated this week
              {lastWeek > 0 ? ` vs ${lastWeek} last week` : ""}
            </p>
          );
        })()}
      </div>

      {/* Stuck concepts */}
      <div className="gd-panel space-y-3">
        <PanelHeading
          icon={<AlertTriangle className="w-4 h-4" style={{ color: "var(--gd-caution)" }} aria-hidden />}
          title="Stuck Concepts"
          aside="3+ failures, last 7 days"
        />
        {stuck.length === 0 ? (
          <p className="text-sm" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
            No stuck concepts — you're making consistent progress. ✓
          </p>
        ) : (
          <div className="space-y-2">
            {stuck.map((s: any) => (
              <div key={s.concept_id}
                className="flex items-start justify-between gap-3 py-2 last:border-0"
                style={{ borderBottom: "1px solid var(--gd-line)" }}>
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{s.subject}</p>
                  {s.error_types.length > 0 && (
                    <p className="text-[10px] mt-0.5" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                      {s.error_types.map((e: any) =>
                        `${ERROR_TYPE_LABELS[e.error_type] ?? e.error_type} ×${e.count}`
                      ).join(" · ")}
                    </p>
                  )}
                </div>
                <span className="shrink-0 text-xs font-semibold px-1.5 py-0.5 rounded"
                  style={{
                    fontFamily: "var(--gd-data)",
                    color: "var(--gd-caution)",
                    background: "var(--gd-caution-soft)",
                    border: "1px solid var(--gd-caution)",
                  }}>
                  {s.fail_count}✗
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Retention forecast */}
      {retention_forecast.length > 0 && (
        <div className="gd-panel space-y-3">
          <PanelHeading
            icon={<Clock className="w-4 h-4" style={{ color: "var(--gd-caution)" }} aria-hidden />}
            title="Retention Forecast"
            aside="most overdue first"
          />
          <div className="space-y-1.5">
            {retention_forecast.map((f: any) => (
              <div key={f.concept_id}
                className="flex items-center justify-between gap-3 py-1.5 last:border-0"
                style={{ borderBottom: "1px solid var(--gd-line)" }}>
                <p className="text-sm truncate flex-1">{f.subject}</p>
                <div className="flex items-center gap-2 shrink-0 text-[10px]" style={{ fontFamily: "var(--gd-data)" }}>
                  <span className="font-semibold" style={{ color: "var(--gd-caution)" }}>
                    {f.days_overdue < 1
                      ? `<1 day overdue`
                      : `${f.days_overdue.toFixed(0)}d overdue`}
                  </span>
                  <span style={{ color: "var(--gd-muted)" }}>HL {f.half_life_days}d</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Session history */}
      <div className="gd-panel space-y-3">
        <PanelHeading
          icon={<Brain className="w-4 h-4" style={{ color: "var(--gd-accent)" }} aria-hidden />}
          title="Recent Sessions"
          aside="last 10 assessments"
        />
        {session_history.length === 0 ? (
          <p className="text-sm" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
            No assessments yet — start studying to see your history.
          </p>
        ) : (
          <div className="space-y-0">
            {session_history.map((h: any, i: number) => (
              <div key={i} className="flex items-center gap-3 py-2 text-sm last:border-0"
                style={{ borderBottom: "1px solid var(--gd-line)" }}>
                <span className="w-10 text-right font-semibold shrink-0 text-xs"
                  style={{ fontFamily: "var(--gd-data)", color: scoreColor(h.score) }}>
                  {Math.round(h.score * 100)}%
                </span>
                <span className="flex-1 truncate text-xs">{h.subject}</span>
                {h.question_type === "transfer" && (
                  <Zap className="w-3 h-3 shrink-0" style={{ color: "var(--gd-caution)" }} aria-hidden />
                )}
                <span className="text-[10px] shrink-0" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                  {h.date ? new Date(h.date).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Learn section switcher ───────────────────────────────────────────────────

function LearnSectionPills({
  active,
  onChange,
}: { active: "study" | "analytics"; onChange: (v: "study" | "analytics") => void }) {
  return (
    <div className="flex items-center gap-2 w-fit mb-4">
      {(["study", "analytics"] as const).map(v => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className="gd-chip"
          data-active={active === v}
          data-testid={`pill-learn-${v}`}
        >
          {v === "analytics" ? (
            <><BarChart2 className="w-3 h-3" aria-hidden /> Analytics</>
          ) : (
            <><BookOpen className="w-3 h-3" aria-hidden /> Study</>
          )}
        </button>
      ))}
    </div>
  );
}

// ─── Dependency map SVG (layered DAG) ────────────────────────────────────────

function DepMapSVG({
  nodes,
  edges,
  onStudy,
}: {
  nodes: any[];
  edges: any[];
  onStudy: (id: string) => void;
}) {
  // Compute BFS depth: nodes with no prereqs = layer 0, dependents below
  const depth: Record<string, number> = {};
  const computeDepth = (id: string, visiting = new Set<string>()): number => {
    if (depth[id] !== undefined) return depth[id];
    if (visiting.has(id)) { depth[id] = 0; return 0; } // cycle guard
    visiting.add(id);
    const prereqs: string[] = nodes.find(n => n.id === id)?.prereq_ids ?? [];
    depth[id] = prereqs.length === 0
      ? 0
      : Math.max(...prereqs.map(p => computeDepth(p, new Set(visiting)))) + 1;
    return depth[id];
  };
  nodes.forEach(n => computeDepth(n.id));

  const maxDepth = Math.max(0, ...Object.values(depth));
  const layers: any[][] = Array.from({ length: maxDepth + 1 }, () => []);
  nodes.forEach(n => layers[depth[n.id] ?? 0].push(n));

  const R     = 32;
  const H_GAP = 100; // horizontal spacing (centre-to-centre)
  const V_GAP = 90;  // vertical spacing (centre-to-centre)
  const PAD   = 50;

  const maxLayerSize = Math.max(...layers.map(l => l.length), 1);
  const svgW = Math.max(520, maxLayerSize * H_GAP + PAD * 2);
  const svgH = (maxDepth + 1) * V_GAP + PAD * 2;

  const pos: Record<string, { x: number; y: number }> = {};
  layers.forEach((layer, li) => {
    const rowW    = (layer.length - 1) * H_GAP;
    const startX  = (svgW - rowW) / 2;
    layer.forEach((n, ni) => {
      pos[n.id] = { x: startX + ni * H_GAP, y: PAD + li * V_GAP };
    });
  });

  // GD semantic tokens — resolved by CSS at paint time (SVG accepts var()).
  const nodeColor = (n: any) => {
    if (n.graduated)                        return "var(--gd-success)";
    if ((n.consecutive_passes ?? 0) > 0)    return "var(--gd-caution)";
    if (n.prereqs_met !== false)            return "var(--gd-info)";
    return "var(--gd-dim)"; // locked
  };

  return (
    <div style={{ overflowX: "auto" }}>
      <svg
        width={svgW}
        height={svgH}
        viewBox={`0 0 ${svgW} ${svgH}`}
        style={{ display: "block", minWidth: "100%" }}
      >
        {/* Arrow marker */}
        <defs>
          <marker id="dep-arrow" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill="var(--gd-line-2)" />
          </marker>
        </defs>
        {/* Edges: prereq (top) → dependent (bottom) */}
        {edges.map((e, i) => {
          const pre = pos[e.target]; // target = prerequisite
          const dep = pos[e.source]; // source = dependent
          if (!pre || !dep) return null;
          // cubic bezier from bottom of prereq to top of dependent
          const x1 = pre.x, y1 = pre.y + R + 2;
          const x2 = dep.x, y2 = dep.y - R - 8;
          const cy = (y1 + y2) / 2;
          return (
            <path
              key={i}
              d={`M${x1},${y1} C${x1},${cy} ${x2},${cy} ${x2},${y2}`}
              fill="none"
              stroke="var(--gd-line-2)"
              strokeWidth={1.5}
              markerEnd="url(#dep-arrow)"
            />
          );
        })}
        {/* Nodes */}
        {nodes.map(n => {
          const p = pos[n.id];
          if (!p) return null;
          const col = nodeColor(n);
          // Split subject into up to 2 wrapped lines
          const words = n.subject.split(" ");
          const line1 = words.slice(0, 2).join(" ");
          const line2 = words.length > 2 ? words.slice(2, 4).join(" ") + (words.length > 4 ? "…" : "") : "";
          const l1 = line1.length > 13 ? line1.slice(0, 12) + "…" : line1;
          const l2 = line2.length > 13 ? line2.slice(0, 12) + "…" : line2;
          return (
            <g
              key={n.id}
              onClick={() => onStudy(n.id)}
              style={{ cursor: "pointer" }}
              role="button"
              aria-label={`Study ${n.subject}`}
            >
              <circle cx={p.x} cy={p.y} r={R} fill={col} fillOpacity={0.15} stroke={col} strokeWidth={2} />
              <text
                x={p.x} y={p.y + (l2 ? -7 : 1)}
                textAnchor="middle"
                fontSize="8.5"
                fontFamily="var(--gd-body)"
                fontWeight="600"
                fill={col}
              >{l1}</text>
              {l2 && (
                <text
                  x={p.x} y={p.y + 9}
                  textAnchor="middle"
                  fontSize="8.5"
                  fontFamily="var(--gd-body)"
                  fontWeight="600"
                  fill={col}
                >{l2}</text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ─── LearnTab — the Socratic loop ─────────────────────────────────────────────

export function LearnTab({ workId }: { workId: string }) {
  const [phase, setPhase]       = useState<LearnPhase>("loading");
  const [session, setSession]   = useState<LearningSession | null>(null);
  const [answer, setAnswer]     = useState("");
  const [result, setResult]     = useState<AssessResult | null>(null);
  const [summary, setSummary]   = useState<{ total: number; graduated: number; mastery_pct: number; due_count?: number } | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [showConcepts, setShowConcepts] = useState(false);
  const [concepts, setConcepts] = useState<any[]>([]);
  const [resettingConcept, setResettingConcept] = useState<string | null>(null);
  const [learnSection, setLearnSection] = useState<"study" | "analytics">("study");
  const [interleavedMode, setInterleavedMode] = useState(false);
  const [interleavedHistory, setInterleavedHistory] = useState<{concept_id: string; subject: string; score: number}[]>([]);
  const [showInterleavedSummary, setShowInterleavedSummary] = useState(false);
  const [showDepMap, setShowDepMap]     = useState(false);
  const [depGraph, setDepGraph]         = useState<{ nodes: any[]; edges: any[] } | null>(null);
  const [depGraphLoading, setDepGraphLoading] = useState(false);

  const apiBase = LEARN_API_BASE;

  const loadSummary = async () => {
    const r = await apiFetch(`${apiBase}/works/${workId}/learning/summary`);
    if (!r.ok) throw new Error("Could not load learning summary");
    return r.json();
  };

  const startOrContinue = async (
    conceptId?: string | null,
    overrideMode?: "blocked" | "interleaved",
  ) => {
    // Use the explicitly requested mode; never fall back to interleavedMode from closure,
    // because setState calls preceding this function do not commit before the function runs.
    const mode = overrideMode ?? (interleavedMode ? "interleaved" : "blocked");
    setError(null);
    setAnswer("");
    setResult(null);
    setPhase("question");
    try {
      const params = new URLSearchParams({ type: "auto" });
      if (conceptId) params.set("concept_id", conceptId);
      params.set("mode", mode);
      const url = `${apiBase}/works/${workId}/learning/question?${params}`;
      const r = await apiFetch(url);
      if (r.status === 422) {
        setPhase("all_done");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setSession({
        concept_id:      data.concept_id,
        subject:         data.subject ?? "Concept",
        description:     data.description ?? "",
        question:        data.question,
        context_snippet: data.context_snippet ?? "",
        question_type:   data.question_type ?? "recall",
        contrast_subject: data.contrast_subject ?? null,
        session_mode:    (data.session_mode === "interleaved" ? "interleaved" : "blocked"),
      });
    } catch (e: any) {
      setError(e.message ?? "Could not load question");
      setPhase("feedback");
    }
  };

  const init = async () => {
    setPhase("loading");
    setError(null);
    try {
      const data = await loadSummary();
      setSummary(data);
      setConcepts(data.concepts ?? []);
      if (data.total === 0) {
        // Auto-seed
        setPhase("seeding");
        const sr = await apiFetch(`${apiBase}/works/${workId}/learning/seed`, { method: "POST" });
        if (!sr.ok) throw new Error("Could not seed concepts");
        const sd = await sr.json();
        if ((sd.concepts ?? []).length === 0) {
          setError("No knowledge items found. Import and process documents first.");
          setPhase("feedback");
          return;
        }
        const sumData = await loadSummary();
        setSummary(sumData);
        setConcepts(sumData.concepts ?? []);
      }
      if (data.mastery_pct === 100 && data.total > 0) {
        setPhase("all_done");
        return;
      }
      await startOrContinue(null);
    } catch (e: any) {
      setError(e.message ?? "Could not initialise learning");
      setPhase("feedback");
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { let active = true; void init().then(() => {}).catch(() => {}); return () => { active = false; }; }, [workId]);

  // Clear dep-map cache whenever the Work changes so we never show stale data
  useEffect(() => {
    setDepGraph(null);
    setShowDepMap(false);
    setDepGraphLoading(false);
  }, [workId]);

  // Lazily load the prerequisite graph when the dep-map section is expanded;
  // uses an AbortController so an in-flight request for a previous Work is discarded.
  useEffect(() => {
    if (!showDepMap || depGraph !== null) return;
    const controller = new AbortController();
    setDepGraphLoading(true);
    apiFetch(`${apiBase}/works/${workId}/learning/graph`, { signal: controller.signal })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!controller.signal.aborted) setDepGraph(d); })
      .catch(e => { if (!controller.signal.aborted) setDepGraph({ nodes: [], edges: [] }); void e; })
      .finally(() => { if (!controller.signal.aborted) setDepGraphLoading(false); });
    return () => controller.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showDepMap, workId]);

  const submitAnswer = async () => {
    if (!session || !answer.trim()) return;
    setPhase("assessing");
    setError(null);
    try {
      const r = await apiFetch(`${apiBase}/works/${workId}/learning/assess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concept_id:    session.concept_id,
          question:      session.question,
          answer:        answer.trim(),
          question_type: session.question_type ?? "recall",
          session_mode:  session.session_mode,   // use the mode that produced this question
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: AssessResult = await r.json();
      setResult(data);
      setSummary(data.summary);
      // Interleaved-specific tracking: use the session's recorded mode, not the toggle state.
      // This ensures a blocked question fetched before Mix was toggled is never mislabelled.
      if (session.session_mode === "interleaved") {
        const newHistory = [...interleavedHistory, {
          concept_id: session.concept_id,
          subject:    session.subject,
          score:      data.score,
        }];
        setInterleavedHistory(newHistory);
        if (newHistory.length >= 10) {
          setShowInterleavedSummary(true);
          return;
        }
      }
      setPhase("feedback");
    } catch (e: any) {
      setError(e.message ?? "Could not assess answer");
      setPhase("feedback");
    }
  };

  // ── Teach-back mode (graduated concepts) ────────────────────────────────────
  const [teachBack, setTeachBack]               = useState<TeachBackSession | null>(null);
  const [teachExplanation, setTeachExplanation] = useState("");
  const [teachResult, setTeachResult]           = useState<TeachBackResult | null>(null);
  const [teachPhase, setTeachPhase]             = useState<"writing" | "grading" | "feedback">("writing");

  const startTeachBack = async (conceptId: string) => {
    setError(null);
    setTeachResult(null);
    setTeachExplanation("");
    try {
      const params = new URLSearchParams({ concept_id: conceptId });
      const r = await apiFetch(`${apiBase}/works/${workId}/learning/teach-back?${params}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setTeachBack({
        concept_id: data.concept_id,
        subject:    data.subject ?? "Concept",
        prompt:     data.prompt,
      });
      setTeachPhase("writing");
    } catch (e: any) {
      setError(e.message ?? "Could not start teach-back");
    }
  };

  const submitTeachBack = async () => {
    if (!teachBack || !teachExplanation.trim()) return;
    setTeachPhase("grading");
    setError(null);
    try {
      const r = await apiFetch(`${apiBase}/works/${workId}/learning/teach-back/assess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concept_id:  teachBack.concept_id,
          explanation: teachExplanation.trim(),
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setTeachResult(data);
      if (data.summary) setSummary(data.summary);
      setTeachPhase("feedback");
    } catch (e: any) {
      setError(e.message ?? "Could not grade teach-back");
      setTeachPhase("writing");
    }
  };

  const closeTeachBack = () => {
    setTeachBack(null);
    setTeachResult(null);
    setTeachExplanation("");
    setTeachPhase("writing");
  };

  const next = async () => {
    if (!result) { await startOrContinue(null); return; }
    if (result.summary.mastery_pct === 100) { setPhase("all_done"); return; }
    if (session?.session_mode === "interleaved") {
      // In interleaved mode next_concept_id comes from blocked routing — ignore it.
      // Let select_interleaved_concept pick the next random weighted concept instead.
      await startOrContinue(null, "interleaved");
    } else {
      await startOrContinue(result.next_concept_id);
    }
  };

  const resetConcept = async (conceptId: string, subject: string) => {
    if (!confirm(`Reset the mastery streak for "${subject}"? It will re-enter the study queue.`)) return;
    setResettingConcept(conceptId);
    try {
      const r = await apiFetch(`${apiBase}/works/${workId}/learning/concepts/${conceptId}/reset`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Refresh concept list in-place
      const sumR = await apiFetch(`${apiBase}/works/${workId}/learning/summary`);
      if (sumR.ok) {
        const d = await sumR.json();
        setSummary(d);
        setConcepts(d.concepts ?? []);
      }
      toast.success("Streak reset — concept re-enters the study queue");
    } catch {
      toast.error("Could not reset concept streak");
    } finally {
      setResettingConcept(null);
    }
  };

  const routeLabel: Record<RouteAction, string> = {
    STEP_FORWARD:  "Great — moving to the next concept",
    STEP_BACKWARD: "Let's revisit a foundational concept first",
    STAY_HERE:     "Keep practising this concept",
  };

  // ── Mastery bar ────────────────────────────────────────────────────────────
  const MasteryBar = () => {
    if (!summary) return null;
    const pct = summary.mastery_pct;
    return (
      <div className="space-y-1.5 mb-6">
        <div className="flex items-center justify-between text-xs"
          style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
          <span>{summary.graduated}/{summary.total} concepts graduated</span>
          <span className="font-semibold" style={{ color: "var(--gd-text)" }}>{pct}%</span>
        </div>
        <div className="h-2 w-full rounded-full overflow-hidden" style={{ background: "var(--gd-line)" }}
          role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Mastery">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ width: `${pct}%`, background: "var(--gd-accent)" }}
          />
        </div>
      </div>
    );
  };

  // ── Interleaved session summary (shown after 10 questions) ───────────────────
  if (showInterleavedSummary) {
    const byConceptId: Record<string, {subject: string; scores: number[]}> = {};
    for (const h of interleavedHistory) {
      if (!byConceptId[h.concept_id]) byConceptId[h.concept_id] = { subject: h.subject, scores: [] };
      byConceptId[h.concept_id].scores.push(h.score);
    }
    const conceptStats = Object.values(byConceptId).map(c => ({
      subject:    c.subject,
      questions:  c.scores.length,
      avg_score:  c.scores.reduce((a, b) => a + b, 0) / c.scores.length,
    })).sort((a, b) => b.avg_score - a.avg_score);
    const overallAvg = interleavedHistory.reduce((a, h) => a + h.score, 0) / Math.max(interleavedHistory.length, 1);

    const exitInterleaved = () => {
      setShowInterleavedSummary(false);
      setInterleavedHistory([]);
      setInterleavedMode(false);
      // Pass "blocked" explicitly — setInterleavedMode hasn't committed yet when startOrContinue runs
      void startOrContinue(null, "blocked");
    };
    const anotherSession = () => {
      setShowInterleavedSummary(false);
      setInterleavedHistory([]);
      // interleavedMode is still true here; pass explicitly to be safe
      void startOrContinue(null, "interleaved");
    };

    return (
      <div className="max-w-2xl mx-auto py-4 space-y-4">
        <LearnSectionPills active="study" onChange={setLearnSection} />
        <div className="gd-panel space-y-5">
          {/* Header */}
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-[10px] flex items-center justify-center"
              style={{ background: "var(--gd-accent-soft)" }}>
              <Shuffle className="w-5 h-5" style={{ color: "var(--gd-accent)" }} aria-hidden />
            </div>
            <div>
              <h3 className="text-lg font-semibold" style={{ fontFamily: "var(--gd-display)", letterSpacing: "0.03em" }}>
                Interleaved session complete
              </h3>
              <p className="text-xs" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                {interleavedHistory.length} questions · {conceptStats.length} concepts · avg {Math.round(overallAvg * 100)}%
              </p>
            </div>
          </div>

          {/* Per-concept breakdown */}
          <div className="space-y-0">
            {conceptStats.map((c, i) => (
              <div key={i} className="flex items-center gap-3 py-2.5 last:border-0"
                style={{ borderBottom: "1px solid var(--gd-line)" }}>
                <span className="flex-1 text-sm truncate">{c.subject}</span>
                <span className="text-[10px] shrink-0" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                  {c.questions}q
                </span>
                <span className="w-12 text-right text-sm font-bold shrink-0"
                  style={{ fontFamily: "var(--gd-data)", color: scoreColor(c.avg_score) }}>
                  {Math.round(c.avg_score * 100)}%
                </span>
              </div>
            ))}
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-1">
            <GdOutlineButton className="flex-1" onClick={exitInterleaved}>
              Exit interleaved
            </GdOutlineButton>
            <GdPrimaryButton onClick={anotherSession}>
              <Shuffle className="w-3.5 h-3.5" aria-hidden /> New session
            </GdPrimaryButton>
          </div>
        </div>
      </div>
    );
  }

  // ── Section: Analytics panel (always reachable, independent of study phase) ──
  if (learnSection === "analytics") {
    return (
      <div className="max-w-2xl mx-auto py-4 space-y-4">
        <LearnSectionPills active="analytics" onChange={setLearnSection} />
        <AnalyticsPanel workId={workId} />
      </div>
    );
  }

  // ── All done ───────────────────────────────────────────────────────────────
  if (phase === "all_done") {
    const handleReset = async () => {
      try {
        await apiFetch(`${apiBase}/works/${workId}/learning/reset`, { method: "POST" });
        await init();
      } catch {/* init handles errors */}
    };
    return (
      <div className="max-w-2xl mx-auto py-4 space-y-6">
        <LearnSectionPills active="study" onChange={setLearnSection} />
        <div className="flex flex-col items-center justify-center py-16 gap-6">
          <div className="w-16 h-16 rounded-[14px] flex items-center justify-center"
            style={{ background: "var(--gd-accent-soft)" }}>
            <Trophy className="w-8 h-8" style={{ color: "var(--gd-success)" }} aria-hidden />
          </div>
          <div className="text-center space-y-2">
            <h3 className="text-2xl font-semibold"
              style={{ fontFamily: "var(--gd-display)", letterSpacing: "0.03em" }}>
              All concepts mastered!
            </h3>
            <p className="text-sm max-w-xs" style={{ color: "var(--gd-muted)" }}>
              You've graduated every concept in this Work. Add more documents to unlock new material,
              or reset your streaks to study it all again.
            </p>
          </div>
          {summary && <MasteryBar />}
          <GdOutlineButton className="mt-2" onClick={handleReset} testId="button-reset-streaks">
            <RefreshCw className="w-3.5 h-3.5" aria-hidden /> Reset streaks &amp; study again
          </GdOutlineButton>
        </div>
      </div>
    );
  }

  // ── Loading / seeding ──────────────────────────────────────────────────────
  if (phase === "loading" || phase === "seeding") {
    return (
      <div className="max-w-2xl mx-auto py-4 space-y-6">
        <LearnSectionPills active="study" onChange={setLearnSection} />
        <div className="flex flex-col items-center justify-center py-16 gap-4">
          <Loader2 className="w-8 h-8 animate-spin" style={{ color: "var(--gd-accent)" }} aria-hidden />
          <p className="text-sm" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
            {phase === "seeding" ? "Seeding concepts from your knowledge base…" : "Loading your learning session…"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto py-4 space-y-6">
      <LearnSectionPills active="study" onChange={setLearnSection} />
      <MasteryBar />

      {/* Interleaved mode toggle — shown when ≥3 in-progress concepts */}
      {(() => {
        const inProgressCount = concepts.filter((c: any) => c.consecutive_passes > 0 && !c.graduated).length;
        if (inProgressCount < 3 && !interleavedMode) return null;
        return (
          <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-[10px] transition-all"
            style={interleavedMode
              ? { background: "var(--gd-accent-soft)", border: "1px solid var(--gd-accent)" }
              : { background: "var(--gd-card)", border: "1px solid var(--gd-line)" }}>
            <div className="flex items-center gap-2">
              <Shuffle className="w-4 h-4"
                style={{ color: interleavedMode ? "var(--gd-accent)" : "var(--gd-muted)" }} aria-hidden />
              <div>
                <p className="text-xs font-semibold"
                  style={{ color: interleavedMode ? "var(--gd-accent)" : "var(--gd-text)" }}>
                  Interleaved practice
                </p>
                <p className="text-[10px]" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                  {interleavedMode ? "Mixing concepts — concept revealed after each answer" : `${inProgressCount} concepts in progress`}
                </p>
              </div>
            </div>
            <button
              className="gd-chip shrink-0"
              data-active={interleavedMode}
              data-testid="button-toggle-interleaved"
              onClick={() => {
                if (interleavedMode) {
                  setInterleavedMode(false);
                  setInterleavedHistory([]);
                  void startOrContinue(null, "blocked");     // explicit — setState hasn't committed
                } else {
                  setInterleavedMode(true);
                  setInterleavedHistory([]);
                  void startOrContinue(null, "interleaved"); // explicit
                }
              }}
            >
              <Shuffle className="w-3 h-3" aria-hidden />
              {interleavedMode ? "Exit" : "Start"}
            </button>
          </div>
        );
      })()}

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-[10px] text-sm"
          style={{ color: "var(--gd-danger)", border: "1px solid var(--gd-danger)", background: "var(--gd-card)" }}>
          <span className="flex-1">{error}</span>
          <button
            className="gd-chip shrink-0"
            onClick={init}
            data-testid="button-retry-init"
          >
            Retry
          </button>
        </div>
      )}

      {/* Active concept header — masked in interleaved mode until after submission */}
      {session && (
        session.session_mode === "interleaved" && phase === "question" ? (
          <div className="gd-panel space-y-1" style={{ borderColor: "var(--gd-accent)" }}>
            <div className="flex items-center gap-2">
              <Shuffle className="w-4 h-4" style={{ color: "var(--gd-accent)" }} aria-hidden />
              <span className="gd-eyebrow" style={{ color: "var(--gd-accent)" }}>Interleaved</span>
              <span className="ml-auto text-[10px]" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                {interleavedHistory.length + 1}/10
              </span>
            </div>
            <h3 className="text-lg font-semibold" style={{ fontFamily: "var(--gd-display)", letterSpacing: "0.02em" }}>
              Which concept does this test?
            </h3>
            <p className="text-sm" style={{ color: "var(--gd-muted)" }}>
              Identify the concept and answer — it will be revealed after you submit.
            </p>
          </div>
        ) : (
          <div className="gd-panel space-y-1">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4" style={{ color: "var(--gd-accent)" }} aria-hidden />
                <span className="gd-eyebrow">
                  {session.session_mode === "interleaved" ? "Revealed concept" : "Studying"}
                </span>
              </div>
              {session.session_mode === "interleaved" && phase === "feedback" && (
                <span className="text-[10px]" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                  {interleavedHistory.length}/10
                </span>
              )}
            </div>
            <h3 className="text-lg font-semibold" style={{ fontFamily: "var(--gd-display)", letterSpacing: "0.02em" }}>
              {session.subject}
            </h3>
            {session.description && session.session_mode !== "interleaved" && (
              <p className="text-sm leading-relaxed" style={{ color: "var(--gd-muted)" }}>{session.description}</p>
            )}
          </div>
        )
      )}

      {/* Teach-back panel — replaces the question panel while active */}
      {teachBack && (
        <div className="gd-panel space-y-4" data-testid="panel-teach-back">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold"
              style={{
                fontFamily: "var(--gd-data)",
                color: "var(--gd-accent)",
                background: "var(--gd-accent-soft)",
                border: "1px solid var(--gd-accent)",
              }}
              title="Teach-back: explain the concept to a curious student who has never heard of it. Graded criterion by criterion against your own words."
            >
              <BookOpen className="w-3 h-3" aria-hidden /> Teach-back · {teachBack.subject}
            </span>
          </div>
          <p className="font-medium leading-relaxed text-base">{teachBack.prompt}</p>

          {teachPhase !== "feedback" ? (
            <>
              <textarea
                className="w-full rounded-[8px] p-3 text-sm leading-relaxed resize-none focus:outline-none min-h-[140px]"
                style={{ background: "var(--gd-card)", border: "1px solid var(--gd-line)", color: "var(--gd-text)" }}
                onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gd-accent)"; }}
                onBlur={(e) => { e.currentTarget.style.borderColor = "var(--gd-line)"; }}
                placeholder="Teach it in your own words…"
                value={teachExplanation}
                onChange={(e) => setTeachExplanation(e.target.value)}
                disabled={teachPhase === "grading"}
                data-testid="input-teach-back"
              />
              <div className="flex justify-between gap-2">
                <GdOutlineButton onClick={closeTeachBack} testId="button-teach-back-cancel">
                  Cancel
                </GdOutlineButton>
                <GdPrimaryButton
                  onClick={submitTeachBack}
                  disabled={!teachExplanation.trim() || teachPhase === "grading"}
                  testId="button-teach-back-submit"
                >
                  {teachPhase === "grading"
                    ? <><Loader2 className="w-4 h-4 animate-spin" aria-hidden /> Grading…</>
                    : <><ChevronRight className="w-4 h-4" aria-hidden /> Submit Lesson</>}
                </GdPrimaryButton>
              </div>
            </>
          ) : teachResult ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl font-bold"
                  style={{ fontFamily: "var(--gd-data)", color: scoreColor(teachResult.score) }}
                  data-testid="text-teach-back-score">
                  {Math.round(teachResult.score * 100)}%
                </span>
                <span className="px-2 py-1 rounded-full text-xs font-semibold"
                  style={{
                    fontFamily: "var(--gd-data)",
                    color: teachResult.passed ? "var(--gd-success)" : "var(--gd-danger)",
                    border: `1px solid ${teachResult.passed ? "var(--gd-success)" : "var(--gd-danger)"}`,
                    background: "var(--gd-card-hi)",
                  }}>
                  {teachResult.passed ? "Your student got it" : "Your student is confused"}
                </span>
                {!teachResult.graduated && !teachResult.passed && (
                  <span className="text-[10px]" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-danger)" }}>
                    Graduation revoked — back to practice
                  </span>
                )}
              </div>

              {teachResult.rubric && teachResult.rubric.length > 0 && (
                <div className="space-y-1.5 p-3 rounded-[10px]"
                  style={{ border: "1px solid var(--gd-line)", background: "var(--gd-card)" }}>
                  <p className="text-[10px] uppercase tracking-wider font-semibold"
                    style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                    Teaching rubric
                  </p>
                  {teachResult.rubric.map((c, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                      <span className="shrink-0 mt-0.5 font-bold"
                        style={{ color: c.met ? "var(--gd-success)" : "var(--gd-danger)" }}>
                        {c.met ? "✓" : "✗"}
                      </span>
                      <div className="min-w-0">
                        <span>{c.criterion}</span>
                        {c.met && c.quote && (
                          <span className="block text-xs italic truncate" style={{ color: "var(--gd-muted)" }}>
                            “{c.quote}”
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {teachResult.student_followup && (
                <div className="flex items-start gap-2 px-3 py-2 rounded-[8px] text-sm"
                  style={{ background: "var(--gd-accent-soft)", border: "1px solid var(--gd-accent)" }}
                  data-testid="text-student-followup">
                  <span className="shrink-0 mt-0.5" aria-hidden>🙋</span>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider font-semibold"
                      style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                      Your student asks
                    </p>
                    <p className="italic">{teachResult.student_followup}</p>
                  </div>
                </div>
              )}

              <p className="text-sm leading-relaxed">{teachResult.feedback}</p>

              <div className="flex justify-end gap-2">
                {!teachResult.passed && (
                  <GdOutlineButton
                    tone="accent"
                    onClick={() => { const cid = teachBack.concept_id; closeTeachBack(); void startOrContinue(cid); }}
                    testId="button-teach-back-practice"
                  >
                    <RefreshCw className="w-4 h-4" aria-hidden /> Practise Again
                  </GdOutlineButton>
                )}
                <GdPrimaryButton onClick={closeTeachBack} testId="button-teach-back-done">
                  Done
                </GdPrimaryButton>
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* Question */}
      {(phase === "question" || phase === "assessing" || phase === "feedback") && session && !teachBack && (
        <div className="gd-panel space-y-4">
          {/* Depth-ladder level badge (recall gets no badge — it is the baseline) */}
          {session.question_type !== "recall" && (() => {
            const meta = {
              self_explanation: {
                label: "Explain it yourself",
                hint: "From memory, in your own words — no notes shown",
                title: "Self-explanation questions ask you to reconstruct the idea in your own words. The source material is deliberately withheld.",
              },
              contrast: {
                label: session.contrast_subject
                  ? `Contrast vs ${session.contrast_subject}`
                  : "Contrast question",
                hint: "How does this differ from its neighbour concept?",
                title: "Contrast questions test whether you can tell this concept apart from a related one.",
              },
              transfer: {
                label: "Application question",
                hint: "Novel scenario — apply what you know",
                title: "Transfer questions test whether you can apply the concept to a novel situation — not just recall what you read.",
              },
            }[session.question_type];
            return meta ? (
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold"
                  style={{
                    fontFamily: "var(--gd-data)",
                    color: "var(--gd-caution)",
                    background: "var(--gd-caution-soft)",
                    border: "1px solid var(--gd-caution)",
                  }}
                  title={meta.title}
                  data-testid="badge-question-level"
                >
                  <Zap className="w-3 h-3" aria-hidden /> {meta.label}
                </span>
                <span className="text-[10px]" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                  {meta.hint}
                </span>
              </div>
            ) : null;
          })()}
          {session.context_snippet && (
            <div className="text-xs pl-3 italic leading-relaxed"
              style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)", borderLeft: "2px solid var(--gd-line)" }}>
              {session.context_snippet}
            </div>
          )}
          <p className="font-medium leading-relaxed text-base">{session.question}</p>

          {phase !== "feedback" ? (
            <>
              <textarea
                className="w-full rounded-[8px] p-3 text-sm leading-relaxed resize-none focus:outline-none min-h-[100px]"
                style={{
                  background: "var(--gd-card)",
                  border: "1px solid var(--gd-line)",
                  color: "var(--gd-text)",
                }}
                onFocus={(e) => { e.currentTarget.style.borderColor = "var(--gd-accent)"; }}
                onBlur={(e) => { e.currentTarget.style.borderColor = "var(--gd-line)"; }}
                placeholder="Write your answer here…"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                disabled={phase === "assessing"}
                data-testid="input-answer"
              />
              <div className="flex justify-end">
                <GdPrimaryButton
                  onClick={submitAnswer}
                  disabled={!answer.trim() || phase === "assessing"}
                  testId="button-submit-answer"
                >
                  {phase === "assessing"
                    ? <><Loader2 className="w-4 h-4 animate-spin" aria-hidden /> Assessing…</>
                    : <><ChevronRight className="w-4 h-4" aria-hidden /> Submit Answer</>}
                </GdPrimaryButton>
              </div>
            </>
          ) : result ? (
            /* Differentiated feedback by error type */
            <div className="space-y-4">
              {/* User's answer (dimmed) */}
              <div className="px-3 py-2 rounded-[8px] text-sm italic"
                style={{ background: "var(--gd-surface)", color: "var(--gd-muted)" }}>
                {answer}
              </div>

              {/* Score badge (always shown) */}
              <div className="flex items-center gap-3">
                <span className="text-2xl font-bold"
                  style={{ fontFamily: "var(--gd-data)", color: scoreColor(result.score) }}
                  data-testid="text-score">
                  {Math.round(result.score * 100)}%
                </span>
                {result.graduated && (
                  <span className="flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold"
                    style={{
                      fontFamily: "var(--gd-data)",
                      color: "var(--gd-success)",
                      border: "1px solid var(--gd-success)",
                      background: "var(--gd-card-hi)",
                    }}>
                    <Trophy className="w-3 h-3" aria-hidden /> Graduated!
                  </span>
                )}
              </div>

              {/* Interleaved concept reveal — bound to session's recorded mode, not toggle state */}
              {session?.session_mode === "interleaved" && session && (
                <div className="flex items-center gap-2 px-3 py-2 rounded-[8px] text-sm"
                  style={{ background: "var(--gd-accent-soft)", border: "1px solid var(--gd-accent)" }}>
                  <Shuffle className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-accent)" }} aria-hidden />
                  <span style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>This tested:</span>
                  <span className="font-semibold">{session.subject}</span>
                </div>
              )}

              {/* Error-type differentiated feedback card */}
              {result.error_type === "careless_slip" ? (
                <CarelessSlipCard
                  feedback={result.feedback}
                  onRetry={() => {
                    // In interleaved mode, pick any in-progress concept (not the same one again)
                    if (session?.session_mode === "interleaved") {
                      void startOrContinue(null, "interleaved");
                    } else {
                      void startOrContinue(session?.concept_id);
                    }
                  }}
                />
              ) : result.error_type === "procedural_gap" ? (
                <ProceduralGapCard
                  feedback={result.feedback}
                  remediationHint={result.remediation_hint}
                />
              ) : result.error_type === "conceptual_misconception" ? (
                <ConceptualMisconceptionCard
                  feedback={result.feedback}
                  remediationHint={result.remediation_hint}
                  socraticFollowup={result.socratic_followup}
                  deepReviewNeeded={result.deep_review_needed}
                />
              ) : result.error_type === "knowledge_gap" ? (
                <KnowledgeGapCard
                  feedback={result.feedback}
                  remediationHint={result.remediation_hint}
                  prereqId={result.suggested_prereq_id}
                  prereqSubject={result.suggested_prereq_subject}
                  onStudyPrereq={(id) => startOrContinue(id, "blocked")}  // exit interleaved to drill specific prereq
                />
              ) : (
                /* Correct answer — simple success card */
                <div className="flex items-start gap-3 p-4 rounded-[10px]"
                  style={{ border: `1px solid ${scoreColor(result.score)}`, background: "var(--gd-card)" }}>
                  <div className="flex-1">
                    <p className="text-sm leading-relaxed">{result.feedback}</p>
                    {result.remediation_hint && result.score < 0.75 && (
                      <p className="text-xs mt-1.5 italic" style={{ color: "var(--gd-muted)" }}>
                        {result.remediation_hint}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Rubric breakdown — criterion-by-criterion grading with extractive quotes */}
              {result.rubric && result.rubric.length > 0 && (
                <div className="space-y-1.5 p-3 rounded-[10px]"
                  style={{ border: "1px solid var(--gd-line)", background: "var(--gd-card)" }}
                  data-testid="panel-rubric">
                  <p className="text-[10px] uppercase tracking-wider font-semibold"
                    style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                    Grading rubric
                  </p>
                  {result.rubric.map((c, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                      <span className="shrink-0 mt-0.5 font-bold"
                        style={{ color: c.met ? "var(--gd-success)" : "var(--gd-danger)" }}
                        aria-label={c.met ? "met" : "not met"}>
                        {c.met ? "✓" : "✗"}
                      </span>
                      <div className="min-w-0">
                        <span>{c.criterion}</span>
                        {c.met && c.quote && (
                          <span className="block text-xs italic truncate" style={{ color: "var(--gd-muted)" }}>
                            “{c.quote}”
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Failure diagnosis / research-request notice */}
              {result.diagnosis && (
                <div className="flex items-start gap-2 px-3 py-2 rounded-[8px] text-sm"
                  style={{
                    border: "1px solid var(--gd-caution)",
                    background: "var(--gd-caution-soft)",
                  }}
                  data-testid="panel-diagnosis">
                  <BookOpen className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "var(--gd-caution)" }} aria-hidden />
                  <div>
                    <p className="font-medium">
                      {result.diagnosis === "corpus_insufficient"
                        ? "Your library is thin on this topic."
                        : result.diagnosis === "learned_and_decayed"
                        ? "You knew this once — it has faded."
                        : "This concept hasn't stuck yet."}
                    </p>
                    <p className="text-xs mt-0.5" style={{ color: "var(--gd-muted)" }}>
                      {result.research_request_id
                        ? "A research request was queued — the next research run will gather more material on this."
                        : result.diagnosis === "learned_and_decayed"
                        ? "A refresher pass through the ladder will bring it back."
                        : "Keep practising — the material is there, it just needs more passes."}
                    </p>
                  </div>
                </div>
              )}

              {/* Teach-back invitation for graduated concepts */}
              {result.graduated && session && (
                <div className="flex justify-end">
                  <GdOutlineButton
                    tone="accent"
                    onClick={() => void startTeachBack(session.concept_id)}
                    testId="button-teach-back"
                  >
                    <BookOpen className="w-4 h-4" aria-hidden /> Teach it back
                  </GdOutlineButton>
                </div>
              )}

              {/* Routing hint (for non-careless-slip errors) */}
              {result.error_type !== "careless_slip" && (
                <p className="text-xs" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                  → {routeLabel[result.route]}
                </p>
              )}

              {/* Navigation button (skip for careless_slip — its card has its own retry button) */}
              {result.error_type !== "careless_slip" && (
                <div className="flex justify-end">
                  <GdPrimaryButton onClick={next} testId="button-next">
                    {result.summary.mastery_pct === 100
                      ? <><Trophy className="w-4 h-4" aria-hidden /> Done!</>
                      : result.route === "STEP_FORWARD"
                      ? <><ChevronRight className="w-4 h-4" aria-hidden /> Next Concept</>
                      : result.error_type === "knowledge_gap" && result.suggested_prereq_id
                      ? <><BookOpen className="w-4 h-4" aria-hidden /> Review Prerequisite</>
                      : <><RefreshCw className="w-4 h-4" aria-hidden /> Keep Practising</>}
                  </GdPrimaryButton>
                </div>
              )}
            </div>
          ) : null}
        </div>
      )}

      {/* Review due banner — shown above concept map when concepts are overdue */}
      {(() => {
        const dueConcepts = concepts.filter((c: any) => c.is_due);
        if (dueConcepts.length === 0) return null;
        return (
          <div className="flex items-center justify-between px-4 py-3 rounded-[10px]"
            style={{ border: "1px solid var(--gd-caution)", background: "var(--gd-caution-soft)" }}>
            <div className="flex items-center gap-2 flex-wrap">
              <Clock className="w-4 h-4 shrink-0" style={{ color: "var(--gd-caution)" }} aria-hidden />
              <span className="text-sm font-medium" style={{ color: "var(--gd-text)" }}>
                {dueConcepts.length} concept{dueConcepts.length !== 1 ? "s" : ""} due for review
              </span>
              <span className="text-xs" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                — spaced-repetition schedule
              </span>
            </div>
            <button
              onClick={() => startOrContinue(dueConcepts[0]?.id)}
              className="text-xs font-semibold hover:underline shrink-0 ml-2 min-h-9"
              style={{ fontFamily: "var(--gd-data)", color: "var(--gd-caution)" }}
              data-testid="button-start-review"
            >
              Start review →
            </button>
          </div>
        );
      })()}

      {/* Concept map (collapsible) */}
      {concepts.length > 0 && (
        <div className="rounded-[10px] overflow-hidden" style={{ border: "1px solid var(--gd-line)" }}>
          <button
            onClick={() => setShowConcepts(!showConcepts)}
            className="w-full flex items-center justify-between px-4 py-3 min-h-12 text-xs uppercase tracking-wider transition-colors"
            style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)", background: "var(--gd-card)" }}
            data-testid="button-toggle-concepts"
          >
            <span>Concept map ({concepts.length})</span>
            {summary?.due_count ? (
              <span className="flex items-center gap-1.5 font-semibold" style={{ color: "var(--gd-caution)" }}>
                <Clock className="w-3 h-3" aria-hidden />
                {summary.due_count} due
              </span>
            ) : (
              <ChevronDown className={`w-4 h-4 transition-transform ${showConcepts ? "rotate-180" : ""}`} aria-hidden />
            )}
          </button>
          {showConcepts && (
            <div>
              {concepts.map((c: any) => {
                const hasProgress    = c.consecutive_passes > 0 || c.graduated;
                const isResetting    = resettingConcept === c.id;
                const isDue          = c.is_due && c.graduated;
                const isLocked       = !c.prereqs_met && !c.graduated;
                const prereqLabels: string[] = c.prereq_labels ?? [];
                return (
                  <div
                    key={c.id}
                    className={`flex items-center justify-between px-4 py-2.5 text-sm group transition-opacity ${isLocked ? "opacity-50" : ""}`}
                    style={{ borderTop: "1px solid var(--gd-line)", background: "var(--gd-surface)" }}
                  >
                    <div className="flex-1 min-w-0 flex items-start gap-2">
                      {/* Status icon */}
                      {isLocked
                        ? <Lock className="w-3.5 h-3.5 shrink-0 mt-0.5" style={{ color: "var(--gd-dim)" }} aria-hidden />
                        : isDue
                        ? <Clock className="w-3.5 h-3.5 shrink-0 mt-0.5" style={{ color: "var(--gd-caution)" }} aria-hidden />
                        : c.graduated
                        ? <Check className="w-3.5 h-3.5 shrink-0 mt-0.5" style={{ color: "var(--gd-success)" }} aria-hidden />
                        : c.consecutive_passes > 0
                        ? <div className="w-3.5 h-3.5 rounded-full shrink-0 mt-0.5" style={{ border: "2px solid var(--gd-caution)" }} />
                        : <div className="w-3.5 h-3.5 rounded-full shrink-0 mt-0.5" style={{ border: "1px solid var(--gd-line-2)" }} />}
                      <div className="min-w-0">
                        <span style={
                          isDue ? { color: "var(--gd-caution)", fontWeight: 500 }
                          : c.graduated ? { color: "var(--gd-success)", fontWeight: 500 }
                          : isLocked ? { color: "var(--gd-muted)" }
                          : undefined
                        }>{c.subject}</span>
                        {/* Prerequisite chip labels */}
                        {isLocked && prereqLabels.length > 0 && (
                          <p className="text-[10px] mt-0.5 truncate" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}>
                            Requires: {prereqLabels.join(", ")}
                          </p>
                        )}
                        {/* Depth-ladder progress chips */}
                        {!isLocked && !c.graduated && c.consecutive_passes > 0 && (
                          <div className="flex gap-1 mt-0.5 flex-wrap">
                            {(["recall", "self_explanation", "contrast", "transfer"] as const).map((lvl) => {
                              const passed = (c.levels_passed ?? []).includes(lvl);
                              const label = { recall: "recall", self_explanation: "explain", contrast: "contrast", transfer: "apply" }[lvl];
                              return (
                                <span key={lvl}
                                  className="text-[9px] px-1 py-px rounded"
                                  style={{
                                    fontFamily: "var(--gd-data)",
                                    color: passed ? "var(--gd-success)" : "var(--gd-dim)",
                                    border: `1px solid ${passed ? "var(--gd-success)" : "var(--gd-line)"}`,
                                  }}>
                                  {passed ? "✓ " : ""}{label}
                                </span>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      {isDue ? (
                        <span className="text-xs font-semibold px-1.5 py-0.5 rounded"
                          style={{
                            fontFamily: "var(--gd-data)",
                            color: "var(--gd-caution)",
                            background: "var(--gd-caution-soft)",
                            border: "1px solid var(--gd-caution)",
                          }}>
                          Due
                        </span>
                      ) : isLocked ? (
                        <button
                          onClick={() => startOrContinue(c.id)}
                          title="Study anyway — prerequisites recommended but not required"
                          className="text-[10px] min-h-9 transition-colors hover:underline"
                          style={{ fontFamily: "var(--gd-data)", color: "var(--gd-dim)" }}
                        >
                          study anyway
                        </button>
                      ) : (
                        <span className="text-xs" style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                          {c.graduated
                            ? (c.half_life_days > 7 ? "✓ durable" : `HL ${c.half_life_days?.toFixed(1)}d`)
                            : c.consecutive_passes > 0 ? `${c.consecutive_passes}/3` : "—"}
                        </span>
                      )}
                      {c.graduated && !isDue && (
                        <button
                          onClick={() => void startTeachBack(c.id)}
                          title="Teach this concept back to a curious student"
                          className="text-[10px] min-h-9 transition-colors hover:underline"
                          style={{ fontFamily: "var(--gd-data)", color: "var(--gd-accent)" }}
                          data-testid={`button-teach-back-${c.id}`}
                        >
                          teach back
                        </button>
                      )}
                      {hasProgress && (
                        <button
                          onClick={() => resetConcept(c.id, c.subject)}
                          disabled={isResetting}
                          title="Reset streak — re-enter study queue"
                          className="opacity-0 group-hover:opacity-60 hover:!opacity-100 focus-visible:opacity-100 p-1 rounded transition-all"
                          style={{ color: "var(--gd-muted)" }}
                        >
                          {isResetting
                            ? <Loader2 className="w-3 h-3 animate-spin" aria-hidden />
                            : <RotateCcw className="w-3 h-3" aria-hidden />}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Dependency map (collapsible) */}
      {concepts.length > 0 && (
        <div className="rounded-[10px] overflow-hidden mt-2" style={{ border: "1px solid var(--gd-line)" }}>
          <button
            onClick={() => setShowDepMap(v => !v)}
            className="w-full flex items-center justify-between px-4 py-3 min-h-12 text-xs uppercase tracking-wider transition-colors"
            style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)", background: "var(--gd-card)" }}
            data-testid="button-toggle-depmap"
          >
            <span className="flex items-center gap-1.5">
              <GitBranch className="w-3 h-3" aria-hidden />
              Dependency map
            </span>
            <ChevronDown className={`w-4 h-4 transition-transform ${showDepMap ? "rotate-180" : ""}`} aria-hidden />
          </button>

          {showDepMap && (
            <div className="p-4" style={{ borderTop: "1px solid var(--gd-line)", background: "var(--gd-surface)" }}>
              {depGraphLoading ? (
                <div className="flex items-center gap-2 text-sm py-4" style={{ color: "var(--gd-muted)" }}>
                  <Loader2 className="w-4 h-4 animate-spin" aria-hidden />
                  Loading dependency graph…
                </div>
              ) : depGraph && depGraph.nodes.length > 0 ? (
                <>
                  {/* Legend */}
                  <div className="flex flex-wrap items-center gap-4 mb-3 text-[10px]"
                    style={{ fontFamily: "var(--gd-data)", color: "var(--gd-muted)" }}>
                    {[
                      { color: "var(--gd-success)", label: "Graduated" },
                      { color: "var(--gd-caution)", label: "In progress" },
                      { color: "var(--gd-info)",    label: "Eligible" },
                      { color: "var(--gd-dim)",     label: "Locked" },
                    ].map(({ color, label }) => (
                      <span key={label} className="flex items-center gap-1.5">
                        <span style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: color, display: "inline-block", flexShrink: 0 }} />
                        {label}
                      </span>
                    ))}
                    <span className="ml-auto opacity-60">click a node to study it</span>
                  </div>
                  <DepMapSVG
                    nodes={depGraph.nodes}
                    edges={depGraph.edges}
                    onStudy={(id) => { startOrContinue(id); setShowDepMap(false); }}
                  />
                </>
              ) : depGraph ? (
                <p className="text-xs py-2" style={{ color: "var(--gd-muted)" }}>
                  No prerequisite relationships defined yet — they appear as concepts build on each other.
                </p>
              ) : null}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
