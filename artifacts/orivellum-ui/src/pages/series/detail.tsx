/**
 * Series detail — /series/:seriesId
 *
 * The trilogy control room: volumes in reading order with per-book canon
 * counts, continuity health, and cross-book findings (book N drifting from
 * the accumulated state of books 1..N-1), plus series-scoped canon totals.
 */
import { useState } from "react";
import { Link, useParams, useSearch } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { Page, ErrorState, LoadingState, ConfirmAction } from "@/components/primitives";
import {
  ArrowLeft, BookOpen, Plus, Loader2, X, ScrollText, ShieldAlert,
  ShieldCheck, GitBranch, ArrowRight,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface VolumeRow {
  work_id: string;
  work_title: string;
  volume: number;
  chapters: number;
  words: number;
  canon_facts: number;
  overrides: number;
  open_findings: number;
  open_severe_findings: number;
  cross_book_findings: number;
  continuity: "ok" | "warn" | "attention";
}

interface Overview {
  series: { id: string; title: string; description: string | null };
  volumes: VolumeRow[];
  series_canon_facts: number;
  total_overrides: number;
  total_cross_book_findings: number;
  continuity: "ok" | "warn" | "attention";
}

const HEALTH: Record<string, { label: string; icon: typeof ShieldCheck; style: React.CSSProperties }> = {
  ok: { label: "Continuity clean", icon: ShieldCheck, style: { color: "var(--gd-success)", background: "var(--gd-olive-soft)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" } },
  warn: { label: "Open findings", icon: ShieldAlert, style: { color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", borderColor: "color-mix(in srgb, var(--gd-bronze) 28%, transparent)" } },
  attention: { label: "Needs attention", icon: ShieldAlert, style: { color: "var(--gd-danger)", background: "var(--gd-danger-soft)", borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)" } },
};

function HealthBadge({ state }: { state: string }) {
  const meta = HEALTH[state] ?? HEALTH.ok;
  const Icon = meta.icon;
  return (
    <Badge variant="outline" className="gap-1" style={meta.style}>
      <Icon className="w-3.5 h-3.5" aria-hidden />
      {meta.label}
    </Badge>
  );
}

function AddVolumeDialog({
  seriesId, existing, open, onClose,
}: { seriesId: string; existing: VolumeRow[]; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [workId, setWorkId] = useState("");
  const [busy, setBusy] = useState(false);
  // Canon-binding confirmation: a domain serving this series requires an
  // explicit opt-in before a new volume inherits shared canon.
  const [canonPrompt, setCanonPrompt] = useState<string | null>(null);
  const { data: worksResp } = useQuery<{ works: { id: string; title: string }[] }>({
    queryKey: ["works-for-series"],
    enabled: open,
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/works`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
  });
  const memberIds = new Set(existing.map((v) => v.work_id));
  const candidates = (worksResp?.works ?? []).filter((w) => !memberIds.has(w.id));
  const nextVolume = existing.length ? Math.max(...existing.map((v) => v.volume)) + 1 : 1;

  const send = (confirm: boolean) =>
    apiFetch(`${BASE}/series/${seriesId}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        work_id: workId, volume: nextVolume, confirm_canon_binding: confirm,
      }),
    });

  async function finishAdd(resp: Response) {
    if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
    toast.success(`Added as volume ${nextVolume}`);
    queryClient.invalidateQueries({ queryKey: ["series-overview", seriesId] });
    setWorkId("");
    onClose();
  }

  async function handleAdd() {
    if (!workId) return;
    setBusy(true);
    try {
      const resp = await send(false);
      if (!resp.ok && resp.status === 422) {
        const detail = (await resp.clone().json())?.detail || "";
        // A canon domain serves this series — binding is explicit, never silent
        if (String(detail).includes("bind shared canon")) {
          setCanonPrompt(String(detail));
          setBusy(false);
          return;
        }
      }
      await finishAdd(resp);
    } catch (e: any) {
      toast.error(e?.message || "Failed to add volume");
    } finally {
      setBusy(false);
    }
  }

  async function confirmCanonBinding() {
    setCanonPrompt(null);
    setBusy(true);
    try {
      await finishAdd(await send(true));
    } catch (e: any) {
      toast.error(e?.message || "Failed to add volume");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add volume {nextVolume}</DialogTitle>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label>Work</Label>
          <Select value={workId} onValueChange={setWorkId}>
            <SelectTrigger data-testid="select-series-work">
              <SelectValue placeholder="Choose a Work…" />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((w) => (
                <SelectItem key={w.id} value={w.id}>{w.title}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {candidates.length === 0 && (
            <p className="text-xs text-muted-foreground">
              Every Work is already in a series (a Work belongs to one series at a time).
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" className="min-h-11" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button className="min-h-11" onClick={handleAdd} disabled={busy || !workId} data-testid="button-add-volume">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
      <ConfirmAction
        open={canonPrompt !== null}
        onOpenChange={(v) => !v && setCanonPrompt(null)}
        title="Bind shared canon to this book?"
        consequence={`${canonPrompt ?? ""}\n\nThe new volume will inherit this canon. You can review bound facts on the series page afterward.`}
        confirmLabel="Bind canon"
        onConfirm={confirmCanonBinding}
      />
    </Dialog>
  );
}

export default function SeriesDetail() {
  const { seriesId = "" } = useParams<{ seriesId: string }>();
  const search = useSearch();
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [pendingRemove, setPendingRemove] = useState<VolumeRow | null>(null);

  // When reached from a Work (deep link carries ?from=<workId>), offer a
  // direct way back to that owning book; otherwise back goes to the list.
  const fromWorkId = new URLSearchParams(search).get("from");
  const backHref = fromWorkId ? `/works/${fromWorkId}` : "/series";
  const backLabel = fromWorkId ? "Back to book" : "Series";

  const { data, isLoading, isError, error, refetch } = useQuery<Overview>({
    queryKey: ["series-overview", seriesId],
    enabled: !!seriesId,
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/series/${seriesId}/overview`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
  });

  async function removeVolume(workId: string, title: string) {
    try {
      const resp = await apiFetch(`${BASE}/series/${seriesId}/members/${workId}`, { method: "DELETE" });
      if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
      toast.success(`Removed “${title}” from the series`);
      queryClient.invalidateQueries({ queryKey: ["series-overview", seriesId] });
    } catch (e: any) {
      toast.error(e?.message || "Failed to remove volume");
    }
  }

  const BackLink = (
    <Link href={backHref}>
      <Button variant="ghost" size="sm" className="-ml-2 min-h-11" data-testid="button-series-back">
        <ArrowLeft className="w-4 h-4" /> {backLabel}
      </Button>
    </Link>
  );

  if (isLoading) {
    return (
      <Page>
        {BackLink}
        <LoadingState rows={3} label="Loading series" />
      </Page>
    );
  }
  if (isError || !data) {
    return (
      <Page>
        {BackLink}
        <ErrorState
          title="Couldn't load this series"
          detail={String((error as Error)?.message ?? "The series overview didn't come back.")}
          onRetry={() => refetch()}
        />
      </Page>
    );
  }

  return (
    <Page wide>
      {BackLink}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="page-h1 truncate" data-testid="text-series-title">
            {data.series.title}
          </h1>
          {data.series.description && (
            <p className="text-sm text-muted-foreground mt-1 max-w-xl">{data.series.description}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <HealthBadge state={data.continuity} />
          {data.volumes.length > 0 && (
            <Link href={`/works/${data.volumes[0].work_id}/continuity`}>
              <Button variant="outline" className="min-h-11" data-testid="button-series-continuity">
                Continuity review
              </Button>
            </Link>
          )}
          <Button onClick={() => setAddOpen(true)} className="min-h-11" data-testid="button-add-volume-open">
            <Plus className="w-4 h-4" /> Add volume
          </Button>
        </div>
      </div>

      {/* Series-level canon summary */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Card>
          <CardContent className="py-4">
            <div className="text-xs text-muted-foreground flex items-center gap-1.5">
              <ScrollText className="w-3.5 h-3.5" aria-hidden /> Series canon
            </div>
            <div className="text-2xl font-semibold" data-testid="text-series-canon-count">
              {data.series_canon_facts}
            </div>
            <div className="text-xs text-muted-foreground">facts binding every volume</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="text-xs text-muted-foreground flex items-center gap-1.5">
              <GitBranch className="w-3.5 h-3.5" aria-hidden /> Per-book overrides
            </div>
            <div className="text-2xl font-semibold">{data.total_overrides}</div>
            <div className="text-xs text-muted-foreground">explicit departures from series canon</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <div className="text-xs text-muted-foreground flex items-center gap-1.5">
              <ShieldAlert className="w-3.5 h-3.5" aria-hidden /> Cross-book findings
            </div>
            <div className="text-2xl font-semibold" data-testid="text-cross-book-count">
              {data.total_cross_book_findings}
            </div>
            <div className="text-xs text-muted-foreground">open drift against earlier volumes</div>
          </CardContent>
        </Card>
      </div>

      {/* Volumes in reading order */}
      <div className="space-y-3">
        {data.volumes.length === 0 && (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              No volumes yet — add your books in reading order.
            </CardContent>
          </Card>
        )}
        {data.volumes.map((v) => (
          <Card key={v.work_id} data-testid={`card-volume-${v.volume}`}>
            <CardContent className="py-4">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-3 min-w-0">
                  <Badge variant="outline" className="shrink-0">Vol {v.volume}</Badge>
                  <div className="min-w-0">
                    <Link href={`/works/${v.work_id}`} className="font-medium truncate hover:underline flex items-center gap-1">
                      <BookOpen className="w-4 h-4 shrink-0" aria-hidden />
                      <span className="truncate">{v.work_title}</span>
                      <ArrowRight className="w-3.5 h-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    </Link>
                    <div className="text-xs text-muted-foreground">
                      {v.chapters} chapters · {v.words.toLocaleString()} words · {v.canon_facts} canon facts
                      {v.overrides > 0 && <> · {v.overrides} override{v.overrides === 1 ? "" : "s"}</>}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {v.cross_book_findings > 0 && (
                    <Badge variant="outline" style={HEALTH.attention.style}>
                      {v.cross_book_findings} cross-book
                    </Badge>
                  )}
                  {v.open_findings > 0 && (
                    <Badge variant="outline">{v.open_findings} open</Badge>
                  )}
                  <HealthBadge state={v.continuity} />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="min-h-11 min-w-11"
                    aria-label={`Remove ${v.work_title} from series`}
                    onClick={() => setPendingRemove(v)}
                    data-testid={`button-remove-volume-${v.volume}`}
                  >
                    <X className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <AddVolumeDialog
        seriesId={seriesId}
        existing={data.volumes}
        open={addOpen}
        onClose={() => setAddOpen(false)}
      />

      <ConfirmAction
        open={pendingRemove !== null}
        onOpenChange={(v) => !v && setPendingRemove(null)}
        title="Remove this volume from the series?"
        consequence={`“${pendingRemove?.work_title ?? ""}” leaves the series’ reading order. The book itself is not deleted — you can add it back later.`}
        confirmLabel="Remove volume"
        destructive
        onConfirm={() => {
          if (pendingRemove) removeVolume(pendingRemove.work_id, pendingRemove.work_title);
          setPendingRemove(null);
        }}
      />
    </Page>
  );
}
