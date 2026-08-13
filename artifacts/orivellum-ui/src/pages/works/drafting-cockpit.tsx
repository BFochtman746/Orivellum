import React, { useEffect, useState } from "react";
import { Link } from "wouter";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Feather,
  Loader2,
  Users,
  Plus,
  Sparkles,
  Play,
  CheckCircle2,
  CircleAlert,
  History,
  Globe2,
  ChevronDown,
  ChevronRight,
  ScrollText,
} from "lucide-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types (endpoints are not in the generated client) ───────────────────────

interface Contract {
  beat: string;
  word_range: [number, number];
  cast: string[];
  act: number;
  location?: string;
}

interface CockpitChapter {
  id: string;
  seq: number;
  title: string | null;
  status: string;
  has_text: boolean;
  contract: Contract | null;
  cast_status: { name: string; status: string }[];
  problems: string[];
  draft_ready: boolean;
  active_run_id: string | null;
}

interface Persona {
  id: string;
  name: string;
  status: string;
  payload: Record<string, unknown>;
}

interface LoomRun {
  id: string;
  chapter_id: string;
  status: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  evidence: Record<string, any>;
}

interface Escalation {
  id: string;
  description: string;
  severity: string;
  state: string;
  created_at: string;
}

interface RevisionRow {
  id: string;
  rev: number;
  word_count: number;
  origin: string;
  created_by: string;
  created_at: string;
  parent_rev: number | null;
}

const personaBadge = (status: string) => {
  if (status === "approved")
    return { color: "var(--gd-success)", bg: "color-mix(in srgb, var(--gd-success) 12%, transparent)" };
  if (status === "rejected") return { color: "var(--gd-danger)", bg: "var(--gd-danger-soft)" };
  return { color: "var(--gd-caution)", bg: "var(--gd-caution-soft)" };
};

// ─── Main panel ───────────────────────────────────────────────────────────────

export function DraftingCockpit({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [contractChapter, setContractChapter] = useState<CockpitChapter | null>(null);
  const [revisionsChapter, setRevisionsChapter] = useState<CockpitChapter | null>(null);
  const [watchRunId, setWatchRunId] = useState<string | null>(null);
  const [personaOpen, setPersonaOpen] = useState(false);
  const [worldOpen, setWorldOpen] = useState(false);

  const { data: chapterData, isLoading } = useQuery({
    queryKey: ["loom-chapters", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/loom/chapters`);
      if (!r.ok) throw new Error("Failed to load drafting chapters");
      return r.json() as Promise<{ chapters: CockpitChapter[] }>;
    },
    enabled: !!workId && open,
    staleTime: 10_000,
    refetchInterval: (query) => {
      const chapters = (query.state.data as { chapters: CockpitChapter[] } | undefined)?.chapters;
      return chapters?.some((c) => c.active_run_id) ? 4000 : false;
    },
  });

  const { data: overview } = useQuery({
    queryKey: ["loom-overview", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/loom`);
      if (!r.ok) throw new Error("Failed to load LOOM overview");
      return r.json() as Promise<{
        personas: Persona[];
        runs: LoomRun[];
        world_state: Record<string, { value: string; source_chapter_seq: number }>;
      }>;
    },
    enabled: !!workId && open,
    staleTime: 15_000,
  });

  const draftMutation = useMutation({
    mutationFn: async (chapterId: string) => {
      const r = await apiFetch(`${BASE}/works/${workId}/loom/draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chapter_id: chapterId }),
      });
      const json = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((json as { detail?: string }).detail ?? "Draft refused");
      return json as { run_id: string };
    },
    onSuccess: (json) => {
      setWatchRunId(json.run_id);
      queryClient.invalidateQueries({ queryKey: ["loom-chapters", workId] });
      toast.success("Drafting started");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const chapters = chapterData?.chapters ?? [];
  const personas = overview?.personas ?? [];
  const readyCount = chapters.filter((c) => c.draft_ready).length;
  const proposedPersonas = personas.filter((p) => p.status === "proposed").length;
  const worldEntries = Object.entries(overview?.world_state ?? {});

  return (
    <Card className="border-primary/20 bg-primary/[0.02]">
      <CardContent className="p-4 space-y-4">
        <button
          type="button"
          className="w-full flex items-center justify-between gap-3 text-left"
          onClick={() => setOpen((v) => !v)}
        >
          <div className="flex items-center gap-3">
            <Feather className="w-4 h-4 text-primary/60 shrink-0" />
            <div>
              <div className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
                Drafting Cockpit
              </div>
              <p className="text-sm text-muted-foreground mt-0.5">
                Author chapter contracts, manage the cast, and run the drafting engine.
              </p>
            </div>
          </div>
          {open ? (
            <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0" />
          )}
        </button>

        {open && (
          <>
            {/* Personas */}
            <div className="rounded-lg border border-border/40 bg-card/50 p-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Users className="w-3.5 h-3.5 text-muted-foreground" />
                  <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                    Cast Personas
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {proposedPersonas > 0 && (
                    <Link href="/review">
                      <Button size="sm" variant="ghost" className="h-7 text-[11px] gap-1">
                        <CircleAlert className="w-3 h-3" style={{ color: "var(--gd-caution)" }} />
                        {proposedPersonas} awaiting approval
                      </Button>
                    </Link>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-[11px] gap-1"
                    onClick={() => setPersonaOpen(true)}
                  >
                    <Plus className="w-3 h-3" /> New persona
                  </Button>
                </div>
              </div>
              {personas.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No personas yet. Every character named in a contract needs an approved
                  persona before drafting.
                </p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {personas.map((p) => {
                    const s = personaBadge(p.status);
                    return (
                      <span
                        key={p.id}
                        className="inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px]"
                        style={{ color: s.color, background: s.bg, borderColor: "transparent" }}
                        title={p.status}
                      >
                        {p.name}
                        <span className="opacity-70 text-[9px] uppercase tracking-wider">
                          {p.status}
                        </span>
                      </span>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Chapters */}
            {isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : chapters.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No chapters yet — extract or create chapters first, then contract them here.
              </p>
            ) : (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                    Chapters
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {readyCount} of {chapters.length} ready to draft
                  </span>
                </div>
                {chapters.map((ch) => (
                  <ChapterRow
                    key={ch.id}
                    chapter={ch}
                    onContract={() => setContractChapter(ch)}
                    onRevisions={() => setRevisionsChapter(ch)}
                    onDraft={() => draftMutation.mutate(ch.id)}
                    onWatch={(runId) => setWatchRunId(runId)}
                    drafting={draftMutation.isPending}
                  />
                ))}
              </div>
            )}

            {/* World state */}
            {worldEntries.length > 0 && (
              <div className="rounded-lg border border-border/40 bg-card/50 p-3 space-y-2">
                <button
                  type="button"
                  className="w-full flex items-center justify-between"
                  onClick={() => setWorldOpen((v) => !v)}
                >
                  <div className="flex items-center gap-2">
                    <Globe2 className="w-3.5 h-3.5 text-muted-foreground" />
                    <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                      World State · {worldEntries.length} facts
                    </span>
                  </div>
                  {worldOpen ? (
                    <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                  )}
                </button>
                {worldOpen && (
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {worldEntries.map(([key, v]) => (
                      <div key={key} className="text-[11px] leading-snug flex gap-2">
                        <span className="font-mono text-muted-foreground shrink-0">{key}</span>
                        <span className="text-foreground/80">{String(v.value)}</span>
                        <span className="text-muted-foreground/60 shrink-0 ml-auto">
                          ch. {v.source_chapter_seq}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </CardContent>

      {contractChapter && (
        <ContractDialog
          workId={workId}
          chapter={contractChapter}
          onClose={() => setContractChapter(null)}
        />
      )}
      {revisionsChapter && (
        <RevisionsDialog chapter={revisionsChapter} onClose={() => setRevisionsChapter(null)} />
      )}
      {watchRunId && (
        <RunDialog workId={workId} runId={watchRunId} onClose={() => setWatchRunId(null)} />
      )}
      {personaOpen && <PersonaDialog workId={workId} onClose={() => setPersonaOpen(false)} />}
    </Card>
  );
}

// ─── Chapter row ──────────────────────────────────────────────────────────────

function ChapterRow({
  chapter,
  onContract,
  onRevisions,
  onDraft,
  onWatch,
  drafting,
}: {
  chapter: CockpitChapter;
  onContract: () => void;
  onRevisions: () => void;
  onDraft: () => void;
  onWatch: (runId: string) => void;
  drafting: boolean;
}) {
  const [showProblems, setShowProblems] = useState(false);
  return (
    <div className="rounded-lg border border-border/40 bg-card/50 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-mono text-muted-foreground w-8 shrink-0">
          {chapter.seq}
        </span>
        <span className="text-sm truncate flex-1">
          {chapter.title || `Chapter ${chapter.seq}`}
        </span>
        {chapter.active_run_id ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-[11px] gap-1"
            onClick={() => onWatch(chapter.active_run_id!)}
          >
            <Loader2 className="w-3 h-3 animate-spin text-primary" /> Drafting…
          </Button>
        ) : chapter.draft_ready ? (
          <span
            className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider"
            style={{ color: "var(--gd-success)" }}
          >
            <CheckCircle2 className="w-3 h-3" /> Ready
          </span>
        ) : (
          <button
            type="button"
            className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider"
            style={{ color: "var(--gd-caution)" }}
            onClick={() => setShowProblems((v) => !v)}
          >
            <CircleAlert className="w-3 h-3" /> {chapter.problems.length} to fix
          </button>
        )}
        <Button size="sm" variant="outline" className="h-7 text-[11px]" onClick={onContract}>
          Contract
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-[11px] gap-1"
          onClick={onRevisions}
          title="Revisions"
        >
          <History className="w-3 h-3" />
        </Button>
        <Button
          size="sm"
          className="h-7 text-[11px] gap-1"
          disabled={!chapter.draft_ready || !!chapter.active_run_id || drafting}
          onClick={onDraft}
        >
          <Play className="w-3 h-3" /> Draft
        </Button>
      </div>
      {showProblems && chapter.problems.length > 0 && (
        <ul
          className="mt-2 p-2 rounded-md text-[11px] space-y-0.5"
          style={{ color: "var(--gd-caution)", background: "var(--gd-caution-soft)" }}
        >
          {chapter.problems.map((p, i) => (
            <li key={i}>· {p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ─── Contract editor ──────────────────────────────────────────────────────────

function ContractDialog({
  workId,
  chapter,
  onClose,
}: {
  workId: string;
  chapter: CockpitChapter;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const c = chapter.contract;
  const [beat, setBeat] = useState(c?.beat ?? "");
  const [minWords, setMinWords] = useState(String(c?.word_range?.[0] ?? 1500));
  const [maxWords, setMaxWords] = useState(String(c?.word_range?.[1] ?? 4000));
  const [cast, setCast] = useState((c?.cast ?? []).join(", "));
  const [act, setAct] = useState(String(c?.act ?? 1));
  const [location, setLocation] = useState(c?.location ?? "");
  const [suggestNote, setSuggestNote] = useState<string | null>(null);

  const suggestMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(
        `${BASE}/works/${workId}/loom/chapters/${chapter.id}/contract/suggest`,
        { method: "POST" },
      );
      if (!r.ok) throw new Error("Suggestion failed");
      return r.json() as Promise<{
        suggestion: Contract & { location: string };
        sources: Record<string, string>;
      }>;
    },
    onSuccess: ({ suggestion, sources }) => {
      setBeat(suggestion.beat ?? "");
      setMinWords(String(suggestion.word_range?.[0] ?? 1500));
      setMaxWords(String(suggestion.word_range?.[1] ?? 4000));
      setCast((suggestion.cast ?? []).join(", "));
      setAct(String(suggestion.act ?? 1));
      setLocation(suggestion.location ?? "");
      const parts: string[] = [];
      if (sources.beat === "chapter_outline") parts.push("beat from the outline");
      else if (sources.beat === "chapter_text") parts.push("beat from the chapter opening");
      if (sources.cast === "personas_in_text") parts.push("cast found in the text");
      else if (sources.cast === "approved_personas") parts.push("cast from approved personas");
      if (sources.word_range === "current_text") parts.push("length banded to the current text");
      setSuggestNote(
        parts.length
          ? `Suggestion filled in (${parts.join("; ")}). Review and save — nothing is stored until you do.`
          : "Nothing to suggest from — write the contract by hand.",
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const body = {
        beat,
        word_range: [Number(minWords), Number(maxWords)],
        cast: cast
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        act: Number(act) || 1,
        location,
      };
      const r = await apiFetch(`${BASE}/works/${workId}/loom/chapters/${chapter.id}/contract`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = (json as { detail?: unknown }).detail;
        throw new Error(typeof detail === "string" ? detail : "Contract rejected — check the fields");
      }
      return json;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["loom-chapters", workId] });
      toast.success("Contract saved");
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base">
            Contract — {chapter.title || `Chapter ${chapter.seq}`}
          </DialogTitle>
          <DialogDescription>
            The beat this chapter must accomplish, who is on stage, and how long it runs. The
            engine refuses to draft without it.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[11px] gap-1"
              onClick={() => suggestMutation.mutate()}
              disabled={suggestMutation.isPending}
            >
              {suggestMutation.isPending ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Sparkles className="w-3 h-3" />
              )}
              Suggest
            </Button>
          </div>
          {suggestNote && (
            <p
              className="text-[11px] p-2 rounded-md"
              style={{ color: "var(--gd-caution)", background: "var(--gd-caution-soft)" }}
            >
              {suggestNote}
            </p>
          )}
          <div className="space-y-1.5">
            <Label className="text-xs">Beat — what must happen</Label>
            <Textarea
              value={beat}
              onChange={(e) => setBeat(e.target.value)}
              rows={3}
              placeholder="Mara discovers the ledger is forged and confronts Tobin at the mill."
            />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Min words</Label>
              <Input value={minWords} onChange={(e) => setMinWords(e.target.value)} inputMode="numeric" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Max words</Label>
              <Input value={maxWords} onChange={(e) => setMaxWords(e.target.value)} inputMode="numeric" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Act</Label>
              <Input value={act} onChange={(e) => setAct(e.target.value)} inputMode="numeric" />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Cast — comma-separated character names</Label>
            <Input
              value={cast}
              onChange={(e) => setCast(e.target.value)}
              placeholder="Mara, Tobin"
            />
            <p className="text-[10px] text-muted-foreground">
              Each name needs an approved persona before drafting.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Location (optional)</Label>
            <Input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="the mill" />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
              {saveMutation.isPending && <Loader2 className="w-3 h-3 animate-spin mr-1" />}
              Save contract
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ─── Persona creator ──────────────────────────────────────────────────────────

function PersonaDialog({ workId, onClose }: { workId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [personality, setPersonality] = useState("");
  const [goals, setGoals] = useState("");
  const [description, setDescription] = useState("");

  const createMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/loom/personas`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, role, personality, goals, description }),
      });
      const json = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((json as { detail?: string }).detail ?? "Could not create persona");
      return json;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["loom-overview", workId] });
      queryClient.invalidateQueries({ queryKey: ["loom-chapters", workId] });
      toast.success("Persona proposed — approve it in the Review queue");
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog open onOpenChange={(v: boolean) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base">New persona</DialogTitle>
          <DialogDescription>
            Personas are proposed here and approved with your signature in the Review queue —
            the engine only drafts approved characters.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Mara" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Role</Label>
            <Input value={role} onChange={(e) => setRole(e.target.value)} placeholder="caravan mistress" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Personality</Label>
            <Textarea value={personality} onChange={(e) => setPersonality(e.target.value)} rows={2} />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Goals</Label>
            <Textarea value={goals} onChange={(e) => setGoals(e.target.value)} rows={2} />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Description</Label>
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !name.trim()}
            >
              {createMutation.isPending && <Loader2 className="w-3 h-3 animate-spin mr-1" />}
              Propose persona
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ─── Run watcher ──────────────────────────────────────────────────────────────

function RunDialog({
  workId,
  runId,
  onClose,
}: {
  workId: string;
  runId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["loom-run", runId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/loom/runs/${runId}`);
      if (!r.ok) throw new Error("Run not found");
      return r.json() as Promise<{ run: LoomRun; escalations: Escalation[] }>;
    },
    refetchInterval: (query) => {
      const run = (query.state.data as { run: LoomRun } | undefined)?.run;
      return run && run.status === "running" ? 2500 : false;
    },
  });

  const run = data?.run;
  const escalations = data?.escalations ?? [];
  const done = run && run.status !== "running";

  useEffect(() => {
    if (done) {
      queryClient.invalidateQueries({ queryKey: ["loom-chapters", workId] });
      queryClient.invalidateQueries({ queryKey: ["loom-overview", workId] });
    }
  }, [done, queryClient, workId]);

  const ev = run?.evidence ?? {};
  const wc = ev.word_count as { count?: number; range?: number[]; ok?: boolean } | undefined;
  const beat = ev.beat_check as { ok?: boolean; verdict?: string; reason?: string } | undefined;
  const accepted = (ev.accepted_actions ?? []) as { character: string; action: string }[];
  const stalled = (ev.stalled_characters ?? []) as string[];
  const revision = ev.revision as { rev?: number; word_count?: number } | undefined;
  const worldUpdates = (ev.world_updates ?? {}) as Record<string, unknown>;

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            {run?.status === "running" && <Loader2 className="w-4 h-4 animate-spin text-primary" />}
            Drafting run
          </DialogTitle>
          <DialogDescription>
            {run?.status === "running"
              ? "The engine is writing. This can take a few minutes."
              : run?.status === "done"
                ? "Draft finished."
                : run?.status
                  ? `Run ended: ${run.status}`
                  : "Loading…"}
          </DialogDescription>
        </DialogHeader>
        {run?.error && (
          <p
            className="text-xs p-2 rounded-md"
            style={{ color: "var(--gd-danger)", background: "var(--gd-danger-soft)" }}
          >
            {run.error}
          </p>
        )}
        {done && run?.status === "done" && (
          <div className="space-y-2 text-sm">
            {revision && (
              <p>
                Saved as revision <span className="font-mono">{revision.rev}</span>
                {revision.word_count ? ` — ${revision.word_count.toLocaleString()} words` : ""}.
              </p>
            )}
            {wc && (
              <p className="text-xs text-muted-foreground">
                Length {wc.count?.toLocaleString()} words{" "}
                {wc.ok ? "— inside the contracted range." : "— OUTSIDE the contracted range (escalated)."}
              </p>
            )}
            {beat && (
              <p className="text-xs text-muted-foreground">
                Beat check: {beat.ok ? "the chapter lands its beat." : beat.reason || "drift flagged (escalated)."}
              </p>
            )}
            {accepted.length > 0 && (
              <div>
                <p className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
                  Accepted character actions
                </p>
                <ul className="space-y-0.5 text-xs">
                  {accepted.map((a, i) => (
                    <li key={i}>
                      <span className="font-medium">{a.character}</span>: {a.action}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {stalled.length > 0 && (
              <p className="text-xs" style={{ color: "var(--gd-caution)" }}>
                Stalled characters (no action passed the critic): {stalled.join(", ")}
              </p>
            )}
            {Object.keys(worldUpdates).length > 0 && (
              <p className="text-xs text-muted-foreground">
                World state updated: {Object.keys(worldUpdates).join(", ")}
              </p>
            )}
          </div>
        )}
        {escalations.length > 0 && (
          <div className="space-y-1">
            <p className="text-[10px] font-mono uppercase tracking-widest" style={{ color: "var(--gd-caution)" }}>
              Escalations for this chapter
            </p>
            {escalations.map((e) => (
              <p
                key={e.id}
                className="text-[11px] p-2 rounded-md"
                style={{ color: "var(--gd-caution)", background: "var(--gd-caution-soft)" }}
              >
                {e.description} {e.state !== "open" && <span className="opacity-60">({e.state})</span>}
              </p>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ─── Revisions viewer ─────────────────────────────────────────────────────────

function RevisionsDialog({
  chapter,
  onClose,
}: {
  chapter: CockpitChapter;
  onClose: () => void;
}) {
  const [openRev, setOpenRev] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["loom-revisions", chapter.id],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/loom/chapters/${chapter.id}/revisions`);
      if (!r.ok) throw new Error("Failed to load revisions");
      return r.json() as Promise<{ revisions: RevisionRow[] }>;
    },
  });

  const { data: revDetail, isLoading: revLoading } = useQuery({
    queryKey: ["loom-revision", openRev],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/loom/revisions/${openRev}`);
      if (!r.ok) throw new Error("Failed to load revision");
      return r.json() as Promise<{ revision: RevisionRow & { text: string } }>;
    },
    enabled: !!openRev,
  });

  const revisions = data?.revisions ?? [];

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            <ScrollText className="w-4 h-4 text-muted-foreground" />
            Revisions — {chapter.title || `Chapter ${chapter.seq}`}
          </DialogTitle>
          <DialogDescription>
            Every draft is kept — history is append-only and never overwritten.
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : revisions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No revisions yet — run a draft first.</p>
        ) : (
          <div className="space-y-1.5">
            {revisions.map((r) => (
              <div key={r.id} className="rounded-lg border border-border/40 bg-card/50">
                <button
                  type="button"
                  className="w-full flex items-center gap-3 px-3 py-2 text-left"
                  onClick={() => setOpenRev(openRev === r.id ? null : r.id)}
                >
                  <span className="text-[11px] font-mono text-muted-foreground">rev {r.rev}</span>
                  <Badge variant="outline" className="text-[10px]">
                    {r.origin}
                  </Badge>
                  <span className="text-[11px] text-muted-foreground">
                    {r.word_count.toLocaleString()} words · {r.created_by}
                  </span>
                  <span className="text-[10px] text-muted-foreground/60 ml-auto">
                    {new Date(r.created_at).toLocaleString()}
                  </span>
                  {openRev === r.id ? (
                    <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                  )}
                </button>
                {openRev === r.id && (
                  <div className="px-3 pb-3">
                    {revLoading ? (
                      <Skeleton className="h-24 w-full" />
                    ) : (
                      <div className="text-sm leading-relaxed whitespace-pre-wrap max-h-96 overflow-y-auto border-t border-border/30 pt-2">
                        {revDetail?.revision.text ?? ""}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
