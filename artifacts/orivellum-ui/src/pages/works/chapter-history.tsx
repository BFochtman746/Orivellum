/**
 * ChapterBandDialog — surgical chapter edits (BAND) + revision history (LINEAGE).
 *
 * Edit tab: select a span in the chapter text, give an instruction, the
 * backend applies it surgically (text outside the band is preserved by code),
 * delta-verifies against canon + world state, and pairwise re-scores. A
 * refusal shows the gate reasons and offers an explicit author acceptance.
 *
 * History tab: full revision timeline (origin, author, edit scope), diff of
 * any revision against the current text, and append-only restore.
 */
import React, { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Loader2,
  History,
  Scissors,
  RotateCcw,
  AlertTriangle,
  CheckCircle2,
  Sparkles,
  User,
  Bookmark,
  Waves,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ────────────────────────────────────────────────────────────────────

interface Revision {
  id: string;
  rev: number;
  word_count: number;
  created_at: string;
  parent_rev: number | null;
  origin: string;
  created_by: string;
  edit_scope: { start: number; end: number; instruction: string } | null;
  meta: Record<string, any>;
}

interface ChapterOverview {
  chapter_id: string;
  work_id: string;
  seq: number;
  title: string | null;
  status: string;
  text: string;
  word_count: number;
  fingerprint: string;
  revisions: Revision[];
}

interface EditResult {
  committed: boolean;
  reasons?: string[];
  gates?: {
    pairwise?: { winner: string; rationale: string };
    delta?: {
      baseline: { count: number; critical_count: number; ced: number };
      candidate: { count: number; critical_count: number; ced: number };
    };
    regression_reasons?: string[];
  };
  revision?: { rev: number };
  demoted_from_approved?: boolean;
}

interface RippleChapter {
  chapter_id: string;
  seq: number | null;
  title: string;
  nodes: string[];
  evidence: string[];
}

interface RippleReport {
  seeds: { name: string }[];
  affected_chapters: RippleChapter[];
  affected_characters: { name: string; depth: number }[];
  affected_facts: { canon_fact_id: string; statement?: string; via_nodes: string[] }[];
  counts: { nodes: number; chapters: number; characters: number; facts: number };
  truncated: boolean;
  note?: string;
}

// ─── Sentence-level diff (LCS on sentence units — chapters stay tractable) ───

function sentences(text: string): string[] {
  return text.split(/(?<=[.!?…])\s+|\n+/).filter((s) => s.trim().length > 0);
}

type DiffOp = { op: "same" | "del" | "add"; text: string };

function diffSentences(a: string[], b: string[]): DiffOp[] {
  // Guard: LCS is O(n*m); beyond that just show remove-all/add-all.
  if (a.length * b.length > 400_000) {
    return [
      ...a.map((t) => ({ op: "del" as const, text: t })),
      ...b.map((t) => ({ op: "add" as const, text: t })),
    ];
  }
  const n = a.length, m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffOp[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ op: "same", text: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ op: "del", text: a[i] }); i++; }
    else { out.push({ op: "add", text: b[j] }); j++; }
  }
  while (i < n) out.push({ op: "del", text: a[i++] });
  while (j < m) out.push({ op: "add", text: b[j++] });
  return out;
}

function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const ops = useMemo(() => diffSentences(sentences(oldText), sentences(newText)), [oldText, newText]);
  const changed = ops.filter((o) => o.op !== "same").length;
  return (
    <div className="space-y-1">
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
        {changed === 0 ? "No differences" : `${changed} changed sentence${changed !== 1 ? "s" : ""}`}
      </div>
      <div className="font-serif text-sm leading-relaxed border border-border/40 rounded-lg p-3 bg-card/50 max-h-72 overflow-y-auto whitespace-pre-wrap">
        {ops.map((o, idx) =>
          o.op === "same" ? (
            <span key={idx}>{o.text} </span>
          ) : o.op === "del" ? (
            <span key={idx} className="line-through decoration-2" style={{ color: "var(--rust)", background: "var(--rust-soft)" }}>
              {o.text}{" "}
            </span>
          ) : (
            <span key={idx} style={{ color: "var(--moss, #3f6b3f)", background: "color-mix(in srgb, var(--moss, #3f6b3f) 12%, transparent)" }}>
              {o.text}{" "}
            </span>
          ),
        )}
      </div>
    </div>
  );
}

// ─── Revision row helpers ─────────────────────────────────────────────────────

function revisionLabel(r: Revision): { label: string; Icon: React.ComponentType<any> } {
  if (r.meta?.checkpoint) return { label: "Checkpoint", Icon: Bookmark };
  if (r.meta?.restored_from_rev != null)
    return { label: `Restored from rev ${r.meta.restored_from_rev}`, Icon: RotateCcw };
  if (r.meta?.band_edit) return { label: "Surgical edit", Icon: Scissors };
  if (r.origin === "ai_generated") return { label: "Drafted", Icon: Sparkles };
  return { label: "Revision", Icon: User };
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// ─── Main dialog ──────────────────────────────────────────────────────────────

export function ChapterBandDialog({
  chapterId,
  workId,
  open,
  onOpenChange,
}: {
  chapterId: string;
  workId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"edit" | "history">("edit");
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["band-chapter", chapterId] });
    qc.invalidateQueries({ queryKey: ["book-intelligence", workId] });
  };

  const { data, isLoading } = useQuery<ChapterOverview>({
    queryKey: ["band-chapter", chapterId],
    enabled: open,
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/band/chapters/${chapterId}`);
      if (!r.ok) throw new Error(`chapter load failed (${r.status})`);
      return r.json();
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[88vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-serif flex items-center gap-2">
            <Scissors className="w-4 h-4 text-primary" />
            {data?.title || "Chapter"}
            {data?.status === "approved" && (
              <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded border" style={{ color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" }}>
                approved
              </span>
            )}
          </DialogTitle>
          <DialogDescription className="text-xs">
            Surgical edits change only the selected span; everything outside it is preserved
            exactly. Every change lands as a new revision — nothing is ever lost.
          </DialogDescription>
        </DialogHeader>

        <div className="flex gap-1 border-b border-border/40">
          {(["edit", "history"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-xs font-mono uppercase tracking-widest border-b-2 -mb-px transition-colors ${
                tab === t ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t === "edit" ? "Surgical edit" : `History${data ? ` (${data.revisions.length})` : ""}`}
            </button>
          ))}
        </div>

        {isLoading || !data ? (
          <div className="py-10 flex justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        ) : tab === "edit" ? (
          <EditPanel data={data} onDone={invalidate} />
        ) : (
          <HistoryPanel data={data} onDone={invalidate} />
        )}
      </DialogContent>
    </Dialog>
  );
}

// ─── Edit panel ───────────────────────────────────────────────────────────────

/** Python slices strings by Unicode code points; textarea selections report
 *  UTF-16 code units. Convert so the server edits exactly what was selected
 *  (the server additionally verifies via the band_text echo). */
function utf16ToCodePoints(text: string, utf16Offset: number): number {
  let cp = 0;
  for (let i = 0; i < utf16Offset && i < text.length; ) {
    const code = text.codePointAt(i)!;
    i += code > 0xffff ? 2 : 1;
    cp++;
  }
  return cp;
}

/** Blast radius of editing this chapter (RIPPLE, E12) — shown BEFORE the
 *  edit is applied so the author can see what downstream prose depends on
 *  what they are about to change. Read-only; failure to load never blocks
 *  the edit itself. */
function RippleSummary({ chapterId }: { chapterId: string }) {
  const [open, setOpen] = useState(false);
  const { data: ripple, isLoading, isError } = useQuery<RippleReport>({
    queryKey: ["band-ripple", chapterId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/band/chapters/${chapterId}/ripple`);
      if (!r.ok) throw new Error(`ripple failed (${r.status})`);
      return r.json();
    },
    staleTime: 60_000,
  });

  if (isLoading) {
    return (
      <div className="text-[11px] font-mono text-muted-foreground flex items-center gap-1.5">
        <Loader2 className="w-3 h-3 animate-spin" /> Simulating ripple…
      </div>
    );
  }
  if (isError || !ripple) {
    return (
      <div className="text-[11px] text-muted-foreground flex items-center gap-1.5">
        <Waves className="w-3 h-3" /> Ripple simulation unavailable — editing is not blocked.
      </div>
    );
  }
  const { counts } = ripple;
  const empty = counts.chapters === 0 && counts.characters === 0 && counts.facts === 0;
  return (
    <div className="border rounded-lg p-2.5 space-y-1.5 bg-card/40">
      <button
        type="button"
        className="w-full flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest text-muted-foreground"
        onClick={() => setOpen((o) => !o)}
      >
        <Waves className="w-3.5 h-3.5" style={{ color: "var(--gilt, #b8952e)" }} />
        Blast radius
        <span className="normal-case tracking-normal font-sans text-[11px] text-foreground/80 ml-1">
          {empty
            ? ripple.note
              ? "no graph evidence for this chapter"
              : "nothing outside this chapter depends on it"
            : `${counts.chapters} chapter${counts.chapters === 1 ? "" : "s"} · ${counts.characters} character${counts.characters === 1 ? "" : "s"} · ${counts.facts} fact${counts.facts === 1 ? "" : "s"}`}
          {ripple.truncated ? " (truncated)" : ""}
        </span>
        {!empty && (open
          ? <ChevronUp className="w-3 h-3 ml-auto" />
          : <ChevronDown className="w-3 h-3 ml-auto" />)}
      </button>
      {open && !empty && (
        <div className="space-y-1.5 pl-5">
          {ripple.affected_chapters.length > 0 && (
            <div className="text-[11px]">
              <span className="font-mono uppercase tracking-widest text-[9px] text-muted-foreground">Chapters · </span>
              {ripple.affected_chapters.slice(0, 12).map((c) => (
                <span key={c.chapter_id} className="mr-2">
                  {c.seq != null ? `Ch. ${c.seq}` : c.title || c.chapter_id.slice(0, 6)}
                  <span className="text-muted-foreground"> ({c.nodes.slice(0, 3).join(", ")}{c.nodes.length > 3 ? "…" : ""})</span>
                </span>
              ))}
              {ripple.affected_chapters.length > 12 && (
                <span className="text-muted-foreground">+{ripple.affected_chapters.length - 12} more</span>
              )}
            </div>
          )}
          {ripple.affected_characters.length > 0 && (
            <div className="text-[11px]">
              <span className="font-mono uppercase tracking-widest text-[9px] text-muted-foreground">Characters · </span>
              {ripple.affected_characters.slice(0, 10).map((c) => c.name).join(", ")}
              {ripple.affected_characters.length > 10 ? "…" : ""}
            </div>
          )}
          {ripple.affected_facts.length > 0 && (
            <div className="text-[11px] space-y-0.5">
              <span className="font-mono uppercase tracking-widest text-[9px] text-muted-foreground">Canon facts</span>
              {ripple.affected_facts.slice(0, 5).map((f) => (
                <div key={f.canon_fact_id} className="font-serif italic text-muted-foreground truncate">
                  “{f.statement || f.canon_fact_id}”
                </div>
              ))}
              {ripple.affected_facts.length > 5 && (
                <div className="text-muted-foreground">+{ripple.affected_facts.length - 5} more</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EditPanel({ data, onDone }: { data: ChapterOverview; onDone: () => void }) {
  const [sel, setSel] = useState<{ start: number; end: number } | null>(null);
  const [instruction, setInstruction] = useState("");
  const [author, setAuthor] = useState("");
  const [refusal, setRefusal] = useState<EditResult | null>(null);

  const needsAuthor = data.status === "approved";
  const bandLen = sel ? sel.end - sel.start : 0;

  const edit = useMutation({
    mutationFn: async (acceptRegression: boolean) => {
      const r = await apiFetch(`${BASE}/band/chapters/${data.chapter_id}/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: utf16ToCodePoints(data.text, sel!.start),
          end: utf16ToCodePoints(data.text, sel!.end),
          instruction: instruction.trim(),
          base_fingerprint: data.fingerprint,
          band_text: data.text.slice(sel!.start, sel!.end),
          author: author.trim(),
          accept_regression: acceptRegression,
        }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body?.detail || `edit failed (${r.status})`);
      return body as EditResult;
    },
    onSuccess: (res) => {
      if (res.committed) {
        toast.success(
          `Edit committed as revision ${res.revision?.rev}` +
            (res.demoted_from_approved ? " — chapter demoted to drafted" : ""),
        );
        setRefusal(null);
        setSel(null);
        setInstruction("");
        onDone();
      } else {
        setRefusal(res);
        toast.warning("Edit refused — the verification gates found regressions");
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const onSelect = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget;
    if (el.selectionStart !== el.selectionEnd) {
      setSel({ start: el.selectionStart, end: el.selectionEnd });
    }
  };

  const canSubmit = !!sel && bandLen > 0 && instruction.trim().length > 0 &&
    (!needsAuthor || author.trim().length > 0) && !edit.isPending;

  return (
    <div className="space-y-3">
      <RippleSummary chapterId={data.chapter_id} />
      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
          1 · Select the passage to change (the band)
        </div>
        <Textarea
          readOnly
          value={data.text}
          onSelect={onSelect}
          className="font-serif text-sm leading-relaxed h-56 resize-none bg-card/50"
        />
        <div className="mt-1 text-[11px] font-mono text-muted-foreground">
          {sel
            ? `Band: characters ${sel.start}–${sel.end} (${bandLen.toLocaleString()} chars)`
            : "Highlight text above to define the edit boundaries."}
        </div>
        {sel && (
          <div className="mt-1 font-serif text-xs italic text-muted-foreground border-l-2 border-primary/40 pl-2 max-h-16 overflow-y-auto">
            “{data.text.slice(sel.start, Math.min(sel.end, sel.start + 400))}
            {bandLen > 400 ? "…" : ""}”
          </div>
        )}
      </div>

      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">
          2 · Instruction
        </div>
        <Textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="e.g. Make Mara's reaction colder — she should not forgive him here."
          className="text-sm h-20 resize-none"
          maxLength={2000}
        />
      </div>

      <div className="flex items-center gap-2">
        <Input
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          placeholder={needsAuthor ? "Author signature (required — chapter is approved)" : "Author signature (optional)"}
          className="text-sm h-8 max-w-xs"
          maxLength={200}
        />
        <Button size="sm" disabled={!canSubmit} onClick={() => edit.mutate(false)}>
          {edit.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : <Scissors className="w-3.5 h-3.5 mr-1.5" />}
          Apply surgical edit
        </Button>
      </div>
      {needsAuthor && (
        <div className="text-[11px] text-muted-foreground flex items-center gap-1.5">
          <AlertTriangle className="w-3 h-3" style={{ color: "var(--gilt)" }} />
          This chapter is approved — editing it requires your signature and returns it to drafted.
        </div>
      )}

      {refusal && (
        <div className="border rounded-lg p-3 space-y-2" style={{ borderColor: "color-mix(in srgb, var(--rust) 40%, transparent)", background: "var(--rust-soft)" }}>
          <div className="text-xs font-mono uppercase tracking-widest flex items-center gap-1.5" style={{ color: "var(--rust)" }}>
            <AlertTriangle className="w-3.5 h-3.5" /> Edit refused — regressions detected
          </div>
          <ul className="text-sm font-serif list-disc pl-5 space-y-0.5">
            {(refusal.reasons || []).map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
          {refusal.gates?.pairwise && (
            <div className="text-[11px] text-muted-foreground">
              Critic verdict: prefers the {refusal.gates.pairwise.winner === "old" ? "previous" : "new"} text — {refusal.gates.pairwise.rationale}
            </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              variant="outline"
              disabled={!author.trim() || edit.isPending}
              onClick={() => edit.mutate(true)}
            >
              Accept anyway (signed)
            </Button>
            {!author.trim() && (
              <span className="text-[11px] text-muted-foreground">Enter your author signature to accept the regression.</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── History panel ────────────────────────────────────────────────────────────

function HistoryPanel({ data, onDone }: { data: ChapterOverview; onDone: () => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [author, setAuthor] = useState("");
  const needsAuthor = data.status === "approved";

  const { data: detail, isLoading: detailLoading } = useQuery<Revision & { text: string }>({
    queryKey: ["band-revision", selectedId],
    enabled: !!selectedId,
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/band/revisions/${selectedId}`);
      if (!r.ok) throw new Error(`revision load failed (${r.status})`);
      return r.json();
    },
  });

  const restore = useMutation({
    mutationFn: async (rev: number) => {
      const r = await apiFetch(`${BASE}/band/chapters/${data.chapter_id}/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rev, author: author.trim() }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body?.detail || `restore failed (${r.status})`);
      return body;
    },
    onSuccess: (res) => {
      toast.success(`Restored revision ${res.restored_from_rev} as new revision ${res.revision?.rev}`);
      setSelectedId(null);
      onDone();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const revs = [...data.revisions].reverse();
  if (revs.length === 0) {
    return (
      <div className="text-sm text-muted-foreground italic font-serif py-8 text-center border border-dashed border-border/60 rounded-lg">
        No revisions yet — the first surgical edit or draft will start the history.
      </div>
    );
  }

  const selected = revs.find((r) => r.id === selectedId) || null;
  const isCurrent = detail != null && detail.text === data.text;

  return (
    <div className="space-y-3">
      <ScrollArea className="max-h-56">
        <div className="space-y-1 pr-2">
          {revs.map((r) => {
            const { label, Icon } = revisionLabel(r);
            const active = r.id === selectedId;
            return (
              <button
                key={r.id}
                onClick={() => setSelectedId(active ? null : r.id)}
                className={`w-full flex items-center gap-2.5 py-1.5 px-2.5 rounded-lg border text-left transition-colors ${
                  active ? "border-primary/50 bg-primary/[0.05]" : "border-border/40 bg-card/50 hover:border-border"
                }`}
              >
                <Icon className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                <span className="text-xs font-mono shrink-0">rev {r.rev}</span>
                <span className="font-serif text-sm truncate flex-1">
                  {label}
                  {r.edit_scope?.instruction ? ` — “${r.edit_scope.instruction.slice(0, 60)}${r.edit_scope.instruction.length > 60 ? "…" : ""}”` : ""}
                </span>
                {r.meta?.accepted_regression && (
                  <span className="text-[9px] font-mono uppercase px-1 py-0.5 rounded border shrink-0" style={{ color: "var(--gilt)", borderColor: "var(--gilt-line)" }}>
                    accepted regression
                  </span>
                )}
                <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                  {r.word_count.toLocaleString()} w · {r.created_by} · {fmtTime(r.created_at)}
                </span>
              </button>
            );
          })}
        </div>
      </ScrollArea>

      {selected && (
        <div className="space-y-2 border-t border-border/40 pt-3">
          {detailLoading || !detail ? (
            <div className="py-4 flex justify-center">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <>
              <DiffView oldText={detail.text} newText={data.text} />
              <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                Struck = only in rev {selected.rev} · highlighted = only in current text
              </div>
              <div className="flex items-center gap-2">
                {needsAuthor && (
                  <Input
                    value={author}
                    onChange={(e) => setAuthor(e.target.value)}
                    placeholder="Author signature (required — chapter is approved)"
                    className="text-sm h-8 max-w-xs"
                    maxLength={200}
                  />
                )}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={isCurrent || restore.isPending || (needsAuthor && !author.trim())}
                  onClick={() => restore.mutate(selected.rev)}
                >
                  {restore.isPending ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />
                  ) : (
                    <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
                  )}
                  {isCurrent ? "Already current" : `Restore rev ${selected.rev}`}
                </Button>
                {!isCurrent && (
                  <span className="text-[11px] text-muted-foreground flex items-center gap-1">
                    <CheckCircle2 className="w-3 h-3" /> Restore adds a new revision — history is never rewritten.
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
