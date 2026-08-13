import { useState } from "react";
import { useParams, Link } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ArrowLeft,
  ArrowRight,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  ShieldCheck,
  ScrollText,
  Play,
  BookOpen,
  Link2,
  HelpCircle,
  Inbox,
} from "lucide-react";
import { toast } from "sonner";
import { Page, EmptyState, ErrorState, LoadingState } from "@/components/primitives";

const API = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ────────────────────────────────────────────────────────────────────

type PackageItem = {
  id: string;
  category: string;
  subject: string;
  claim: string;
  quote: string;
  offset: number | null;
  chapter_seq: number | null;
};

type Package = {
  id: string;
  work_id: string;
  version: number;
  status: "draft" | "ratified" | "superseded";
  payload: { items: PackageItem[]; ending_chapter_count?: number };
  extraction_meta: { items_extracted?: number; error?: string };
  author_intent: string;
  ratified_at: string | null;
  created_at: string;
};

type ContractItem = {
  id: string;
  category: string;
  subject: string;
  claim: string;
  quote: string;
  offset: number | null;
};

type Contract = {
  id: string;
  work_id: string;
  version: number;
  prior_package_id: string | null;
  payload: { items: ContractItem[]; opening_chapter_count?: number };
  extraction_meta: { items_extracted?: number; error?: string };
  created_at: string;
};

type EvidenceSpan = {
  role: "prior_book" | "successor_book";
  work_title: string;
  quote: string;
  offset: number | null;
};

type Finding = {
  id: string;
  audit_id: string;
  finding_type: string;
  severity: "low" | "medium" | "high" | "critical";
  subject: string;
  explanation: string;
  evidence: EvidenceSpan[];
  insufficient_evidence: boolean;
  status: "open" | "accepted" | "intentional" | "dismissed";
  resolution_note: string;
  resolved_at: string | null;
};

type Audit = {
  id: string;
  prior_work_id: string;
  successor_work_id: string;
  status: "pending" | "running" | "done" | "failed";
  coverage: {
    prior_work_title: string;
    successor_work_title: string;
    package_ratified: boolean;
    end_state_items: number;
    opening_items: number;
    opening_window_chars: number;
    partial: boolean;
  };
  error: string | null;
  created_at: string;
};

// ── Constants ────────────────────────────────────────────────────────────────

const FINDING_LABELS: Record<string, string> = {
  hard_contradiction: "Hard Contradiction",
  missing_bridge: "Missing Bridge",
  unexplained_time_jump: "Unexplained Time Jump",
  unexplained_location_change: "Unexplained Location Change",
  unexplained_knowledge_change: "Unexplained Knowledge Change",
  unexplained_injury_change: "Unexplained Injury Change",
  unexplained_object_change: "Unexplained Object Change",
  dropped_promise: "Dropped Promise",
  dropped_thread: "Dropped Thread",
  excessive_recap: "Excessive Recap",
  insufficient_reorientation: "Insufficient Reorientation",
  accidental_spoiler: "Accidental Spoiler",
  emotional_discontinuity: "Emotional Discontinuity",
  no_fresh_promise: "No Fresh Promise",
};

const SEVERITY_STYLES: Record<string, string> = {
  critical: "border-red-500 text-red-600 bg-red-50 dark:bg-red-950/30",
  high: "border-orange-400 text-orange-600 bg-orange-50 dark:bg-orange-950/30",
  medium: "border-yellow-400 text-yellow-600 bg-yellow-50 dark:bg-yellow-950/30",
  low: "border-slate-300 text-slate-500",
};

const CATEGORY_COLORS: Record<string, string> = {
  dramatic_question: "bg-purple-100 text-purple-700 dark:bg-purple-900/30",
  character_state: "bg-blue-100 text-blue-700 dark:bg-blue-900/30",
  injury: "bg-red-100 text-red-700 dark:bg-red-900/30",
  possession: "bg-amber-100 text-amber-700 dark:bg-amber-900/30",
  promise: "bg-green-100 text-green-700 dark:bg-green-900/30",
  thread: "bg-teal-100 text-teal-700 dark:bg-teal-900/30",
  emotional_tone: "bg-pink-100 text-pink-700 dark:bg-pink-900/30",
  world_state: "bg-slate-100 text-slate-700 dark:bg-slate-700/40",
  orientation: "bg-sky-100 text-sky-700 dark:bg-sky-900/30",
  character_reentry: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30",
  dramatic_question_new: "bg-violet-100 text-violet-700 dark:bg-violet-900/30",
};

const HEALTH_CONFIG: Record<string, { label: string; icon: typeof CheckCircle2; cls: string }> = {
  healthy: { label: "Healthy", icon: CheckCircle2, cls: "text-green-600" },
  warnings: { label: "Warnings", icon: AlertTriangle, cls: "text-orange-500" },
  critical: { label: "Critical", icon: AlertTriangle, cls: "text-red-500" },
  not_audited: { label: "Not Audited", icon: HelpCircle, cls: "text-slate-400" },
  no_package: { label: "No Package", icon: HelpCircle, cls: "text-slate-400" },
  pending: { label: "Pending", icon: Loader2, cls: "text-blue-400" },
};

// ── Work selector helper ─────────────────────────────────────────────────────

function useWork(workId: string) {
  return useQuery({
    queryKey: ["work", workId],
    queryFn: () => apiFetch(`${API}/works/${workId}`).then((r) => r.json()),
  });
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function HandoffPage() {
  const { workId } = useParams<{ workId: string }>();
  const [activeTab, setActiveTab] = useState<"package" | "contract" | "audit">("package");

  const workQ = useWork(workId ?? "");
  const work = workQ.data?.work ?? workQ.data;

  return (
    <Page
      wide
      eyebrow="Handoff Contracts"
      title={work?.title || "Handoff Contracts"}
      actions={
        <>
          <Link href={`/works/${workId}`}>
            <Button variant="ghost" size="icon" className="min-h-11 min-w-11">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <span className="flex gap-1" aria-hidden>
            <BookOpen className="h-5 w-5 text-muted-foreground" />
            <ArrowRight className="h-5 w-5 text-muted-foreground" />
            <BookOpen className="h-5 w-5 text-muted-foreground" />
          </span>
        </>
      }
    >
      <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex gap-1 border-b">
        {(["package", "contract", "audit"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab === "package" ? "End-State Package" : tab === "contract" ? "Opening Contract" : "Handoff Audit"}
          </button>
        ))}
      </div>

      {workId && activeTab === "package" && <PackagePanel workId={workId} />}
      {workId && activeTab === "contract" && <ContractPanel workId={workId} />}
      {workId && activeTab === "audit" && <AuditPanel workId={workId} />}
      </div>
    </Page>
  );
}

// ── End-State Package panel ──────────────────────────────────────────────────

function PackagePanel({ workId }: { workId: string }) {
  const qc = useQueryClient();
  const [intent, setIntent] = useState("");
  const [editingIntent, setEditingIntent] = useState(false);

  const pkgQ = useQuery({
    queryKey: ["handoff-package", workId],
    queryFn: () =>
      apiFetch(`${API}/works/${workId}/handoff/package`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null),
  });

  const build = useMutation({
    mutationFn: () =>
      apiFetch(`${API}/works/${workId}/handoff/package`, { method: "POST" }).then((r) => r.json()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["handoff-package", workId] });
      toast.success("End-State Package built");
    },
    onError: () => toast.error("Build failed"),
  });

  const ratify = useMutation({
    mutationFn: (pkgId: string) =>
      apiFetch(`${API}/works/${workId}/handoff/package/${pkgId}/ratify`, { method: "POST" }).then(
        (r) => r.json()
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["handoff-package", workId] });
      toast.success("Package ratified — successor can now be audited against this");
    },
    onError: () => toast.error("Ratification failed"),
  });

  const saveIntent = useMutation({
    mutationFn: ({ pkgId, text }: { pkgId: string; text: string }) =>
      apiFetch(`${API}/works/${workId}/handoff/package/${pkgId}/intent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ author_intent: text }),
      }).then((r) => r.json()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["handoff-package", workId] });
      setEditingIntent(false);
      toast.success("Intent saved");
    },
  });

  const pkg: Package | null = pkgQ.data?.package ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          An End-State Package captures what this book leaves behind. The author ratifies it
          before it can bind the successor.
        </p>
        <Button onClick={() => build.mutate()} disabled={build.isPending} size="sm">
          {build.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
          Build Package
        </Button>
      </div>

      {pkgQ.isLoading && <LoadingState rows={3} label="Loading package" />}
      {!pkgQ.isLoading && !pkg && (
        <EmptyState
          icon={<Inbox />}
          title="No End-State Package yet"
          description="Build one from the final chapters."
        />
      )}

      {pkg && (
        <div className="space-y-3">
          {/* Status bar */}
          <Card>
            <CardContent className="py-3 flex items-center gap-4 flex-wrap">
              <Badge variant="outline" className={pkg.status === "ratified" ? "border-green-500 text-green-600" : ""}>
                {pkg.status === "ratified" ? "Ratified ✓" : pkg.status === "draft" ? "Draft" : "Superseded"}
              </Badge>
              <span className="text-sm text-muted-foreground">v{pkg.version}</span>
              <span className="text-sm text-muted-foreground">
                {pkg.payload.items?.length ?? 0} items •{" "}
                {pkg.payload.ending_chapter_count ?? "?"} ending chapters scanned
              </span>
              {pkg.status === "draft" && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => ratify.mutate(pkg.id)}
                  disabled={ratify.isPending}
                  className="ml-auto"
                >
                  <ShieldCheck className="h-4 w-4 mr-1" />
                  Ratify
                </Button>
              )}
            </CardContent>
          </Card>

          {/* Author intent */}
          <Card>
            <CardHeader className="py-3 pb-1">
              <CardTitle className="text-sm font-medium flex items-center justify-between">
                <span>Handoff Intent</span>
                {!editingIntent && (
                  <button
                    className="text-xs text-primary hover:underline"
                    onClick={() => { setIntent(pkg.author_intent); setEditingIntent(true); }}
                  >
                    {pkg.author_intent ? "Edit" : "Add"}
                  </button>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="py-2">
              {editingIntent ? (
                <div className="space-y-2">
                  <Textarea
                    value={intent}
                    onChange={(e) => setIntent(e.target.value)}
                    placeholder="What does this book owe and not owe the successor? (Author's statement)"
                    rows={3}
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={() => saveIntent.mutate({ pkgId: pkg.id, text: intent })}
                      disabled={saveIntent.isPending}
                    >
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditingIntent(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : pkg.author_intent ? (
                <p className="text-sm italic text-muted-foreground">"{pkg.author_intent}"</p>
              ) : (
                <p className="text-xs text-muted-foreground">No intent statement yet.</p>
              )}
            </CardContent>
          </Card>

          {/* Items */}
          <div className="space-y-2">
            {(pkg.payload.items ?? []).map((it) => (
              <Card key={it.id} className="border-l-2 border-l-muted-foreground/20">
                <CardContent className="py-3">
                  <div className="flex items-start gap-2 flex-wrap">
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${CATEGORY_COLORS[it.category] ?? "bg-muted text-muted-foreground"}`}
                    >
                      {it.category.replace(/_/g, " ")}
                    </span>
                    <span className="text-sm font-medium">{it.subject}</span>
                  </div>
                  <p className="text-sm mt-1">{it.claim}</p>
                  {it.quote && (
                    <blockquote className="mt-1 border-l-2 pl-2 text-xs text-muted-foreground italic">
                      "{it.quote}"
                      {it.chapter_seq != null && (
                        <span className="ml-2 not-italic">ch. {it.chapter_seq}</span>
                      )}
                    </blockquote>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>

          {pkg.extraction_meta.error && (
            <p className="text-xs text-destructive">Extraction error: {pkg.extraction_meta.error}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Opening Contract panel ───────────────────────────────────────────────────

function ContractPanel({ workId }: { workId: string }) {
  const qc = useQueryClient();

  const contractQ = useQuery({
    queryKey: ["opening-contract", workId],
    queryFn: () =>
      apiFetch(`${API}/works/${workId}/handoff/contract`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null),
  });

  const build = useMutation({
    mutationFn: () =>
      apiFetch(`${API}/works/${workId}/handoff/contract`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }).then(
        (r) => r.json()
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opening-contract", workId] });
      toast.success("Opening Contract built");
    },
    onError: () => toast.error("Build failed"),
  });

  const contract: Contract | null = contractQ.data?.contract ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          An Opening Contract captures how this book's opening handles states inherited from its
          predecessor.
        </p>
        <Button onClick={() => build.mutate()} disabled={build.isPending} size="sm">
          {build.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
          Build Contract
        </Button>
      </div>

      {contractQ.isLoading && <LoadingState rows={3} label="Loading contract" />}
      {!contractQ.isLoading && !contract && (
        <EmptyState
          icon={<Inbox />}
          title="No Opening Contract yet"
          description="Build one from the opening chapters."
        />
      )}

      {contract && (
        <div className="space-y-3">
          <Card>
            <CardContent className="py-3 flex items-center gap-4 flex-wrap">
              <Badge variant="outline">v{contract.version}</Badge>
              <span className="text-sm text-muted-foreground">
                {contract.payload.items?.length ?? 0} items •{" "}
                {Math.round((contract.extraction_meta as any).window_chars / 5)} words scanned
              </span>
            </CardContent>
          </Card>

          <div className="space-y-2">
            {(contract.payload.items ?? []).map((it) => (
              <Card key={it.id} className="border-l-2 border-l-muted-foreground/20">
                <CardContent className="py-3">
                  <div className="flex items-start gap-2 flex-wrap">
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${CATEGORY_COLORS[it.category] ?? "bg-muted text-muted-foreground"}`}
                    >
                      {it.category.replace(/_/g, " ")}
                    </span>
                    <span className="text-sm font-medium">{it.subject}</span>
                  </div>
                  <p className="text-sm mt-1">{it.claim}</p>
                  {it.quote && (
                    <blockquote className="mt-1 border-l-2 pl-2 text-xs text-muted-foreground italic">
                      "{it.quote}"
                    </blockquote>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>

          {contract.extraction_meta.error && (
            <p className="text-xs text-destructive">
              Extraction error: {contract.extraction_meta.error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Handoff Audit panel ──────────────────────────────────────────────────────

function AuditPanel({ workId }: { workId: string }) {
  const qc = useQueryClient();
  const [selectedAuditId, setSelectedAuditId] = useState<string | null>(null);

  // Audits where this work is the PRIOR book.
  const auditsQ = useQuery({
    queryKey: ["handoff-audits-prior", workId],
    queryFn: () =>
      apiFetch(`${API}/handoff-audits?prior_work_id=${workId}&limit=10`).then((r) => r.json()),
  });

  // Audits where this work is the SUCCESSOR.
  const auditsSuccQ = useQuery({
    queryKey: ["handoff-audits-succ", workId],
    queryFn: () =>
      apiFetch(`${API}/handoff-audits?successor_work_id=${workId}&limit=10`).then((r) => r.json()),
  });

  const auditsError = auditsQ.isError || auditsSuccQ.isError;
  const auditsLoading = auditsQ.isLoading || auditsSuccQ.isLoading;
  const retryAudits = () => {
    auditsQ.refetch();
    auditsSuccQ.refetch();
  };

  const allAudits: Audit[] = [
    ...(auditsQ.data?.audits ?? []),
    ...(auditsSuccQ.data?.audits ?? []),
  ].sort((a, b) => (b.created_at > a.created_at ? 1 : -1));

  const activeAudit = allAudits.find((a) => a.id === selectedAuditId) ?? allAudits[0] ?? null;

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        The Handoff Audit compares the End-State Package against the successor's opening text.
        Start an audit from the Series page, or view existing audits below.
      </p>

      {allAudits.length > 1 && (
        <Select
          value={activeAudit?.id ?? ""}
          onValueChange={(v) => setSelectedAuditId(v)}
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder="Select audit run" />
          </SelectTrigger>
          <SelectContent>
            {allAudits.map((a) => (
              <SelectItem key={a.id} value={a.id}>
                {a.coverage.prior_work_title} → {a.coverage.successor_work_title}{" "}
                ({new Date(a.created_at).toLocaleDateString()})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {auditsLoading && allAudits.length === 0 && !auditsError && (
        <LoadingState rows={3} label="Loading audits" />
      )}

      {auditsError && allAudits.length === 0 && (
        <ErrorState
          title="Couldn't load handoff audits"
          detail="The handoff audits failed to load."
          onRetry={retryAudits}
        />
      )}

      {activeAudit && (
        <AuditDetail audit={activeAudit} />
      )}

      {allAudits.length === 0 && !auditsLoading && !auditsError && (
        <EmptyState
          icon={<Inbox />}
          title="No handoff audits yet"
          description="Visit the Series page to run one."
        />
      )}
    </div>
  );
}

function AuditDetail({ audit }: { audit: Audit }) {
  const qc = useQueryClient();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const findingsQ = useQuery({
    queryKey: ["handoff-findings", audit.id],
    queryFn: () =>
      apiFetch(`${API}/handoff-audits/${audit.id}/findings`).then((r) => r.json()),
  });

  const resolve = useMutation({
    mutationFn: ({ id, status, note }: { id: string; status: string; note: string }) =>
      apiFetch(`${API}/handoff-findings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, resolution_note: note }),
      }).then((r) => r.json()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["handoff-findings", audit.id] });
      toast.success("Finding updated");
    },
  });

  const findings: Finding[] = findingsQ.data?.findings ?? [];
  const open = findings.filter((f) => f.status === "open");

  return (
    <div className="space-y-3">
      {/* Coverage banner */}
      <Card className={audit.coverage.partial ? "border-yellow-400" : ""}>
        <CardContent className="py-3 flex items-center gap-4 flex-wrap text-sm">
          <span className="font-medium">
            {audit.coverage.prior_work_title} → {audit.coverage.successor_work_title}
          </span>
          {audit.coverage.partial && (
            <Badge variant="outline" className="border-yellow-400 text-yellow-600">
              ⚠ Partial — package not ratified
            </Badge>
          )}
          <span className="text-muted-foreground ml-auto">
            {audit.coverage.end_state_items} end-state items •{" "}
            {audit.coverage.opening_items} opening items
          </span>
        </CardContent>
      </Card>

      {/* Summary */}
      {audit.status === "done" && (
        <div className="flex items-center gap-3 text-sm">
          <span>{open.length} open findings</span>
          {open.length === 0 && (
            <span className="flex items-center gap-1 text-green-600">
              <CheckCircle2 className="h-4 w-4" /> Seam looks clean
            </span>
          )}
        </div>
      )}

      {audit.status === "failed" && (
        <p className="text-xs text-destructive">Audit failed: {audit.error}</p>
      )}

      {/* Findings */}
      {findingsQ.isLoading && <LoadingState rows={3} label="Loading findings" />}
      {findingsQ.isError && (
        <ErrorState
          title="Couldn't load findings"
          detail="The audit findings failed to load."
          onRetry={() => findingsQ.refetch()}
        />
      )}
      <div className="space-y-2">
        {findings.map((f) => (
          <FindingCard
            key={f.id}
            finding={f}
            expanded={expandedId === f.id}
            onToggle={() => setExpandedId(expandedId === f.id ? null : f.id)}
            onResolve={(status, note) => resolve.mutate({ id: f.id, status, note })}
          />
        ))}
      </div>
    </div>
  );
}

function FindingCard({
  finding,
  expanded,
  onToggle,
  onResolve,
}: {
  finding: Finding;
  expanded: boolean;
  onToggle: () => void;
  onResolve: (status: string, note: string) => void;
}) {
  const [note, setNote] = useState(finding.resolution_note);
  const [status, setStatus] = useState(finding.status);

  const isResolved = finding.status !== "open";

  return (
    <Card
      className={`cursor-pointer transition-opacity ${isResolved ? "opacity-60" : ""}`}
      onClick={onToggle}
    >
      <CardContent className="py-3">
        <div className="flex items-start gap-2">
          <Badge
            variant="outline"
            className={`text-[10px] shrink-0 ${SEVERITY_STYLES[finding.severity] ?? ""}`}
          >
            {finding.severity.toUpperCase()}
          </Badge>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium">
                {FINDING_LABELS[finding.finding_type] ?? finding.finding_type}
              </span>
              {finding.insufficient_evidence && (
                <Badge variant="outline" className="text-[10px]">
                  Insufficient evidence
                </Badge>
              )}
              <span className="text-xs text-muted-foreground">{finding.subject}</span>
            </div>
            <p className="text-sm text-muted-foreground mt-0.5">{finding.explanation}</p>
          </div>
        </div>

        {expanded && (
          <div className="mt-3 space-y-2" onClick={(e) => e.stopPropagation()}>
            {/* Evidence spans */}
            {finding.evidence.map((e, i) => (
              <div key={i} className="rounded-md bg-muted/50 p-2 text-xs">
                <Badge variant="outline" className="mr-2 text-[10px]">
                  {e.role === "prior_book" ? "Book N" : "Book N+1"}
                </Badge>
                <span className="font-medium">{e.work_title}</span>
                {e.quote && (
                  <span className="ml-2 italic">"{e.quote}"</span>
                )}
              </div>
            ))}

            {/* Resolution */}
            {!isResolved && (
              <div className="space-y-2 pt-2 border-t">
                <Select value={status} onValueChange={(v) => setStatus(v as Finding["status"])}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="open">Open</SelectItem>
                    <SelectItem value="accepted">Accept (fix planned)</SelectItem>
                    <SelectItem value="intentional">Intentional (author choice)</SelectItem>
                    <SelectItem value="dismissed">Dismiss</SelectItem>
                  </SelectContent>
                </Select>
                <Textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Resolution note (optional)"
                  rows={2}
                  className="text-xs"
                />
                <Button
                  size="sm"
                  onClick={() => onResolve(status, note)}
                  disabled={status === "open"}
                >
                  Save resolution
                </Button>
              </div>
            )}
            {isResolved && (
              <div className="pt-2 border-t text-xs text-muted-foreground">
                <span className="font-medium capitalize">{finding.status}</span>
                {finding.resolution_note && <span> — {finding.resolution_note}</span>}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
