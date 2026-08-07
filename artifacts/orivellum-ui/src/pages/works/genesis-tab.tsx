/**
 * genesis-tab.tsx — GENESIS Book Origination System
 *
 * Full origination workflow: G0 Spark → G9 Ready-to-Write Seal.
 * Each stage has a markdown editor, gate pass/fail controls,
 * fill-placeholder highlighting, and a brainstorm codex drawer.
 */

import { useState, useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import {
  BookOpen, CheckCircle, XCircle, Clock, ChevronRight,
  ChevronLeft, Lightbulb, Shield, ShieldCheck, AlertCircle,
  Save, CheckSquare, XSquare, Sparkles, Scroll, Lock,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ────────────────────────────────────────────────────────────────────

interface GenesisStage {
  code: string;
  name: string;
  status: "PENDING" | "PASSED" | "FAILED";
  gate_description: string;
  is_current: boolean;
}

interface GenesisBook {
  id: string;
  work_id: string;
  mode: string;
  length: number;
  acts: number;
  state: string;
  sealed: boolean;
  manifest: string | null;
  created_at: string;
  updated_at: string;
  stages: GenesisStage[];
  next_stage: string | null;
  ledger_entries: number;
}

interface StageDetail {
  code: string;
  name: string;
  gate_description: string;
  status: "PENDING" | "PASSED" | "FAILED";
  content: string;
  has_unfilled_placeholders: boolean;
  sha256: string;
  updated_at: string | null;
}

// ─── Gate status icon ─────────────────────────────────────────────────────────

function GateIcon({ status, size = 4 }: { status: string; size?: number }) {
  const cls = `w-${size} h-${size} shrink-0`;
  if (status === "PASSED") return <CheckCircle className={cls} style={{ color: "var(--green-2)" }} />;
  if (status === "FAILED") return <XCircle className={`${cls} text-destructive`} />;
  return <Clock className={`${cls} text-muted-foreground`} />;
}

// ─── Gate progress strip ──────────────────────────────────────────────────────

function GateStrip({
  stages,
  activeCode,
  onSelect,
}: {
  stages: GenesisStage[];
  activeCode: string;
  onSelect: (code: string) => void;
}) {
  return (
    <div className="flex items-center gap-0.5 flex-wrap">
      {stages.map((s, i) => {
        const isActive = s.code === activeCode;
        return (
          <button
            key={s.code}
            onClick={() => onSelect(s.code)}
            className={`group flex flex-col items-center px-2 py-1.5 rounded-md transition-colors text-center min-w-[52px] ${
              isActive
                ? "bg-primary/10 border border-primary/30"
                : "hover:bg-muted/50 border border-transparent"
            }`}
          >
            <span className={`text-[9px] font-mono font-bold ${
              isActive ? "text-primary" : "text-muted-foreground"
            }`}>
              {s.code}
            </span>
            <GateIcon status={s.status} size={3} />
            {/* connector line */}
            {i < stages.length - 1 && (
              <span className="hidden" aria-hidden />
            )}
          </button>
        );
      })}
    </div>
  );
}

// ─── Codex drawer ─────────────────────────────────────────────────────────────

function CodexDrawer({
  workId,
  stageCode,
  open,
  onClose,
}: {
  workId: string;
  stageCode: string;
  open: boolean;
  onClose: () => void;
}) {
  const { data } = useQuery<{ stage?: string; techniques: string }>({
    queryKey: ["genesis-codex", workId, stageCode],
    queryFn: async () => {
      const r = await apiFetch(
        `${BASE}/works/${workId}/genesis/techniques?stage=${stageCode}`
      );
      if (!r.ok) throw new Error("Failed");
      return r.json();
    },
    enabled: open,
    staleTime: Infinity,
  });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-serif">
            <Lightbulb className="w-4 h-4" style={{ color: "var(--gilt)" }} />
            Brainstorm Codex — {stageCode}
          </DialogTitle>
        </DialogHeader>
        <div className="flex-1 overflow-y-auto pr-2 text-sm font-serif leading-relaxed whitespace-pre-wrap text-muted-foreground">
          {data ? data.techniques : "Loading…"}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ─── Init dialog ──────────────────────────────────────────────────────────────

function InitDialog({
  workId,
  open,
  onClose,
}: {
  workId: string;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"cold" | "library">("cold");
  const [length, setLength] = useState(80);
  const [acts, setActs] = useState(4);

  const initMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/genesis`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, length, acts }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as any)?.detail ?? "Failed");
      return body;
    },
    onSuccess: () => {
      toast.success("GENESIS book initialized");
      queryClient.invalidateQueries({ queryKey: ["genesis", workId] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className="font-serif">Start Origination</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div>
            <Label className="text-xs font-mono uppercase tracking-widest">Mode</Label>
            <div className="flex gap-2 mt-1.5">
              {(["cold", "library"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`flex-1 py-2 rounded-md border text-sm font-mono transition-colors ${
                    mode === m
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:border-muted-foreground"
                  }`}
                >
                  {m === "cold" ? "COLD (fresh idea)" : "LIBRARY (from corpus)"}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs font-mono uppercase tracking-widest">
                Target Chapters
              </Label>
              <Input
                type="number"
                value={length}
                min={10}
                max={500}
                onChange={(e) => setLength(Number(e.target.value))}
                className="mt-1.5 font-mono"
              />
            </div>
            <div>
              <Label className="text-xs font-mono uppercase tracking-widest">Acts</Label>
              <div className="flex gap-1.5 mt-1.5">
                {[3, 4, 5].map((a) => (
                  <button
                    key={a}
                    onClick={() => setActs(a)}
                    className={`flex-1 py-1.5 rounded border text-sm font-mono transition-colors ${
                      acts === a
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:border-muted-foreground"
                    }`}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => initMutation.mutate()} disabled={initMutation.isPending}>
            {initMutation.isPending ? "Starting…" : "Start GENESIS"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Gate decision dialog ─────────────────────────────────────────────────────

function GateDialog({
  workId,
  code,
  decision,
  open,
  onClose,
}: {
  workId: string;
  code: string;
  decision: "pass" | "fail";
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [author, setAuthor] = useState("");
  const [note, setNote] = useState("");

  const gateMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(
        `${BASE}/works/${workId}/genesis/stages/${code}/gate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, author, note }),
        }
      );
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as any)?.detail ?? "Failed");
      return body;
    },
    onSuccess: (data) => {
      const action = decision === "pass" ? "passed" : "failed";
      toast.success(`${code} ${action}`);
      queryClient.invalidateQueries({ queryKey: ["genesis", workId] });
      queryClient.invalidateQueries({ queryKey: ["genesis-stage", workId, code] });
      onClose();
      setAuthor("");
      setNote("");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const isPass = decision === "pass";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className={`flex items-center gap-2 font-serif ${isPass ? "" : "text-destructive"}`} style={isPass ? { color: "var(--green-2)" } : undefined}>
            {isPass ? <CheckSquare className="w-4 h-4" /> : <XSquare className="w-4 h-4" />}
            {isPass ? "Pass" : "Fail"} Gate {code}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <div>
            <Label className="text-xs font-mono uppercase tracking-widest">Author *</Label>
            <Input
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder="Your name"
              className="mt-1.5 font-mono"
            />
          </div>
          <div>
            <Label className="text-xs font-mono uppercase tracking-widest">Note</Label>
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={isPass ? "Rationale for passing…" : "What needs to be fixed…"}
              rows={3}
              className="mt-1.5 text-sm"
            />
          </div>
          {isPass && (
            <p className="text-[10px] font-mono text-muted-foreground">
              Gate decisions are append-only. A pass records your sign-off in the tamper-evident ledger.
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            onClick={() => gateMutation.mutate()}
            disabled={gateMutation.isPending || !author.trim()}
            variant={isPass ? "default" : "destructive"}
          >
            {gateMutation.isPending ? "Recording…" : `Record ${isPass ? "Pass" : "Fail"}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Seal dialog ──────────────────────────────────────────────────────────────

function SealDialog({
  workId,
  open,
  onClose,
}: {
  workId: string;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [author, setAuthor] = useState("");

  const sealMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/genesis/seal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ author }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as any)?.detail ?? "Failed");
      return body;
    },
    onSuccess: () => {
      toast.success("Book sealed — READY_FOR_B0");
      queryClient.invalidateQueries({ queryKey: ["genesis", workId] });
      onClose();
      setAuthor("");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-serif" style={{ color: "var(--green-2)" }}>
            <ShieldCheck className="w-4 h-4" />
            Seal the Origination Package
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <p className="text-sm text-muted-foreground font-serif">
            Sealing locks all ten gates, computes the manifest hash, and marks this Work
            <strong className="text-foreground"> READY_FOR_B0</strong>.
            The ledger entry is tamper-evident and append-only.
          </p>
          <div>
            <Label className="text-xs font-mono uppercase tracking-widest">Author Sign-off *</Label>
            <Input
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder="Your name"
              className="mt-1.5 font-mono"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            onClick={() => sealMutation.mutate()}
            disabled={sealMutation.isPending || !author.trim()}
            className="text-white hover:opacity-90"
            style={{ background: "var(--green-2)" }}
          >
            {sealMutation.isPending ? "Sealing…" : "Seal Package"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Stage editor ─────────────────────────────────────────────────────────────

function StageEditor({
  workId,
  book,
  code,
  onNavigate,
}: {
  workId: string;
  book: GenesisBook;
  code: string;
  onNavigate: (code: string) => void;
}) {
  const queryClient = useQueryClient();
  const [codexOpen, setCodexOpen] = useState(false);
  const [passOpen, setPassOpen] = useState(false);
  const [failOpen, setFailOpen] = useState(false);
  const [dirty, setDirty] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { data: stage, isLoading } = useQuery<StageDetail>({
    queryKey: ["genesis-stage", workId, code],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/genesis/stages/${code}`);
      if (!r.ok) throw new Error("Failed");
      return r.json();
    },
    staleTime: 30_000,
  });

  const [localContent, setLocalContent] = useState<string | null>(null);
  const displayContent = localContent ?? stage?.content ?? "";

  const saveMutation = useMutation({
    mutationFn: async (content: string) => {
      const r = await apiFetch(`${BASE}/works/${workId}/genesis/stages/${code}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as any)?.detail ?? "Failed");
      return body;
    },
    onSuccess: () => {
      toast.success(`${code} saved`);
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["genesis-stage", workId, code] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setLocalContent(e.target.value);
      setDirty(true);
    },
    []
  );

  const idx = book.stages.findIndex((s) => s.code === code);
  const prevCode = idx > 0 ? book.stages[idx - 1].code : null;
  const nextCode = idx < book.stages.length - 1 ? book.stages[idx + 1].code : null;

  const stageInfo = book.stages.find((s) => s.code === code);
  const status = stageInfo?.status ?? "PENDING";
  const sealed = book.sealed;

  const hasPlaceholders = displayContent.includes("<<FILL>>");

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Stage header */}
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">{code}</span>
            <span className="font-serif font-semibold">{stageInfo?.name}</span>
            {status === "PASSED" && (
              <span
                className="flex items-center gap-1 text-[10px] font-mono rounded-full px-2 py-0.5 border"
                style={{ color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" }}
              >
                <CheckCircle className="w-3 h-3" /> PASSED
              </span>
            )}
            {status === "FAILED" && (
              <span className="flex items-center gap-1 text-[10px] font-mono text-destructive bg-destructive/5 border border-destructive/20 rounded-full px-2 py-0.5">
                <XCircle className="w-3 h-3" /> FAILED
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground font-serif italic">
            Gate: {stageInfo?.gate_description}
          </p>
        </div>

        <Button
          size="sm"
          variant="outline"
          className="gap-1.5 h-7 text-xs"
          onClick={() => setCodexOpen(true)}
        >
          <Lightbulb className="w-3 h-3" style={{ color: "var(--gilt)" }} />
          Codex
        </Button>
      </div>

      {/* Fill warning */}
      {hasPlaceholders && (
        <div
          className="flex items-start gap-2 px-3 py-2 rounded border text-xs"
          style={{ borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" }}
        >
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          This artifact still has <code className="font-mono" style={{ color: "var(--gilt)" }}>{"<<FILL>>"}</code> placeholders. Replace all of them before passing the gate.
        </div>
      )}

      {/* Editor */}
      <div className="relative">
        <Textarea
          ref={textareaRef}
          value={displayContent}
          onChange={handleChange}
          disabled={sealed}
          rows={24}
          className={`font-mono text-xs leading-relaxed resize-y ${
            sealed ? "opacity-60" : ""
          }`}
          style={hasPlaceholders ? { borderColor: "var(--gilt-line)" } : undefined}
          placeholder="Write your artifact content here…"
          spellCheck={false}
        />
        {sealed && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground bg-background/80 px-3 py-1.5 rounded-full border border-border/50">
              <Lock className="w-3 h-3" /> Sealed — read only
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        {/* Navigation */}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            className="gap-1 h-7 text-xs"
            disabled={!prevCode}
            onClick={() => prevCode && onNavigate(prevCode)}
          >
            <ChevronLeft className="w-3 h-3" />
            {prevCode}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="gap-1 h-7 text-xs"
            disabled={!nextCode}
            onClick={() => nextCode && onNavigate(nextCode)}
          >
            {nextCode}
            <ChevronRight className="w-3 h-3" />
          </Button>
        </div>

        {/* Save + gate controls */}
        <div className="flex items-center gap-2">
          {!sealed && dirty && (
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5 h-7 text-xs"
              onClick={() => saveMutation.mutate(displayContent)}
              disabled={saveMutation.isPending}
            >
              <Save className="w-3 h-3" />
              {saveMutation.isPending ? "Saving…" : "Save"}
            </Button>
          )}
          {!sealed && !dirty && (
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5 h-7 text-xs"
              onClick={() => saveMutation.mutate(displayContent)}
              disabled={saveMutation.isPending}
            >
              <Save className="w-3 h-3" />
              Save
            </Button>
          )}
          {!sealed && (
            <>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 h-7 text-xs border-destructive/50 text-destructive hover:bg-destructive/10"
                onClick={() => setFailOpen(true)}
              >
                <XSquare className="w-3 h-3" />
                Fail Gate
              </Button>
              <Button
                size="sm"
                className="gap-1.5 h-7 text-xs text-white hover:opacity-90"
                style={{ background: "var(--green-2)" }}
                disabled={hasPlaceholders}
                onClick={() => setPassOpen(true)}
              >
                <CheckSquare className="w-3 h-3" />
                Pass Gate
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Dialogs */}
      <CodexDrawer
        workId={workId}
        stageCode={code}
        open={codexOpen}
        onClose={() => setCodexOpen(false)}
      />
      <GateDialog
        workId={workId}
        code={code}
        decision="pass"
        open={passOpen}
        onClose={() => setPassOpen(false)}
      />
      <GateDialog
        workId={workId}
        code={code}
        decision="fail"
        open={failOpen}
        onClose={() => setFailOpen(false)}
      />
    </div>
  );
}

// ─── Main tab ─────────────────────────────────────────────────────────────────

export function GenesisTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [initOpen, setInitOpen] = useState(false);
  const [sealOpen, setSealOpen] = useState(false);
  const [activeCode, setActiveCode] = useState("G0");

  const { data: book, isLoading, error } = useQuery<GenesisBook>({
    queryKey: ["genesis", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/genesis`);
      if (r.status === 404) return null as any;
      if (!r.ok) throw new Error("Failed");
      return r.json();
    },
    staleTime: 30_000,
    retry: false,
  });

  // Verify ledger (on demand)
  const verifyQuery = useQuery<{ ok: boolean; message: string }>({
    queryKey: ["genesis-verify", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/genesis/verify`);
      if (!r.ok) throw new Error("Failed");
      return r.json();
    },
    enabled: false,
  });

  const passedCount = book?.stages.filter((s) => s.status === "PASSED").length ?? 0;
  const allGatesPassed = passedCount === 10;

  // ── Not yet initialized ──────────────────────────────────────────────────────
  if (!isLoading && !book) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-lg font-serif font-semibold flex items-center gap-2">
            <Scroll className="w-5 h-5 text-muted-foreground" />
            GENESIS — Book Origination System
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Ten-gate pre-writing workflow: Spark → Premise → Viability → Canon → Character → Structure → Voice → Standard Binding → Blueprint → Seal
          </p>
        </div>

        <Card className="border-dashed border-border/50 bg-muted/10">
          <CardContent className="py-10 text-center space-y-4">
            <Scroll className="w-8 h-8 text-muted-foreground/40 mx-auto" />
            <p className="text-sm text-muted-foreground font-serif italic">
              No origination package yet.
            </p>
            <p className="text-xs text-muted-foreground max-w-md mx-auto">
              GENESIS walks you through ten locked gates — G0 Spark Slate through G9 Ready-to-Write Seal.
              Each gate requires your explicit sign-off before the next opens.
              The tamper-evident ledger records every decision.
            </p>
            <Button onClick={() => setInitOpen(true)} className="gap-2 mt-2">
              <Sparkles className="w-4 h-4" />
              Start Origination
            </Button>
          </CardContent>
        </Card>

        <InitDialog workId={workId} open={initOpen} onClose={() => setInitOpen(false)} />
      </div>
    );
  }

  // ── Loading ──────────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const b = book!;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-serif font-semibold flex items-center gap-2">
            <Scroll className="w-5 h-5 text-muted-foreground" />
            GENESIS — Book Origination
          </h2>
          <div className="flex items-center gap-3 mt-1 text-xs font-mono text-muted-foreground flex-wrap">
            <span className="uppercase">{b.mode}</span>
            <span>{b.length} chapters · {b.acts} acts</span>
            <span className={b.sealed ? "" : "text-foreground font-semibold"} style={b.sealed ? { color: "var(--green-2)", fontWeight: 600 } : undefined}>
              {b.sealed ? "🔒 READY_FOR_B0" : b.state}
            </span>
            <span>{b.ledger_entries} ledger entries</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Ledger verify */}
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5 h-7 text-xs"
            onClick={() => verifyQuery.refetch()}
            disabled={verifyQuery.isFetching}
          >
            <Shield className="w-3 h-3" />
            {verifyQuery.isFetching ? "Verifying…" : "Verify Ledger"}
          </Button>

          {/* Seal */}
          {!b.sealed && allGatesPassed && (
            <Button
              size="sm"
              className="gap-1.5 h-7 text-xs text-white hover:opacity-90"
              style={{ background: "var(--green-2)" }}
              onClick={() => setSealOpen(true)}
            >
              <ShieldCheck className="w-3 h-3" />
              Seal Package
            </Button>
          )}
        </div>
      </div>

      {/* Ledger verify result */}
      {verifyQuery.data && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded border text-xs font-mono"
          style={verifyQuery.data.ok
            ? { borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)", background: "var(--green-soft)", color: "var(--green-2)" }
            : undefined}
        >
          {verifyQuery.data.ok
            ? <CheckCircle className="w-3.5 h-3.5 shrink-0" />
            : <AlertCircle className="w-3.5 h-3.5 shrink-0" />}
          {verifyQuery.data.message}
        </div>
      )}

      {/* Progress: pass count */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs font-mono text-muted-foreground">
          <span>Gates passed</span>
          <span>{passedCount} / 10</span>
        </div>
        <div className="h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ background: "var(--green-2)", opacity: 0.65, width: `${passedCount * 10}%` }}
          />
        </div>
      </div>

      {/* Gate strip */}
      <div className="space-y-1">
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
          Gates — click to open
        </div>
        <GateStrip
          stages={b.stages}
          activeCode={activeCode}
          onSelect={setActiveCode}
        />
      </div>

      {/* Stage legend */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-1.5">
        {b.stages.map((s) => (
          <button
            key={s.code}
            onClick={() => setActiveCode(s.code)}
            className={`flex items-center gap-1.5 px-2 py-1.5 rounded border text-left transition-colors ${
              s.code === activeCode
                ? "border-primary/40 bg-primary/5"
                : "border-border/40 hover:border-border"
            } ${s.is_current && !b.sealed ? "ring-1 ring-primary/30" : ""}`}
          >
            <GateIcon status={s.status} size={3} />
            <div className="min-w-0">
              <div className="text-[9px] font-mono font-bold text-muted-foreground">{s.code}</div>
              <div className="text-[10px] font-serif truncate">{s.name}</div>
            </div>
          </button>
        ))}
      </div>

      {/* Active stage editor */}
      <div className="border-t border-border/30 pt-5">
        <StageEditor
          workId={workId}
          book={b}
          code={activeCode}
          onNavigate={(code) => setActiveCode(code)}
        />
      </div>

      {/* Sealed manifest */}
      {b.sealed && b.manifest && (
        <div className="border-t pt-4" style={{ borderColor: "color-mix(in srgb, var(--green-2) 20%, transparent)" }}>
          <div className="text-[10px] font-mono uppercase tracking-widest mb-2" style={{ color: "var(--green-2)" }}>
            Sealed Manifest
          </div>
          <pre className="text-[10px] font-mono bg-muted/40 rounded-lg p-3 overflow-x-auto text-muted-foreground max-h-48">
            {typeof b.manifest === "string"
              ? JSON.stringify(JSON.parse(b.manifest), null, 2)
              : JSON.stringify(b.manifest, null, 2)}
          </pre>
        </div>
      )}

      {/* Dialogs */}
      <SealDialog workId={workId} open={sealOpen} onClose={() => setSealOpen(false)} />
    </div>
  );
}
