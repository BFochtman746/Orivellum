/**
 * Series detail — /series/:seriesId
 *
 * The trilogy control room: volumes in reading order with per-book canon
 * counts, continuity health, and cross-book findings (book N drifting from
 * the accumulated state of books 1..N-1), plus series-scoped canon totals.
 */
import { useState } from "react";
import { Link, useParams } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
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
  ok: { label: "Continuity clean", icon: ShieldCheck, style: { color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" } },
  warn: { label: "Open findings", icon: ShieldAlert, style: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" } },
  attention: { label: "Needs attention", icon: ShieldAlert, style: { color: "var(--rust)", background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" } },
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

  async function handleAdd() {
    if (!workId) return;
    setBusy(true);
    try {
      const send = (confirm: boolean) =>
        apiFetch(`${BASE}/series/${seriesId}/members`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            work_id: workId, volume: nextVolume, confirm_canon_binding: confirm,
          }),
        });
      let resp = await send(false);
      if (!resp.ok && resp.status === 422) {
        const detail = (await resp.clone().json())?.detail || "";
        // A canon domain serves this series — binding is explicit, never silent
        if (String(detail).includes("bind shared canon")) {
          if (!window.confirm(`${detail}\n\nBind this canon to the new book?`)) {
            setBusy(false);
            return;
          }
          resp = await send(true);
        }
      }
      if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
      toast.success(`Added as volume ${nextVolume}`);
      queryClient.invalidateQueries({ queryKey: ["series-overview", seriesId] });
      setWorkId("");
      onClose();
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
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={handleAdd} disabled={busy || !workId} data-testid="button-add-volume">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function SeriesDetail() {
  const { seriesId = "" } = useParams<{ seriesId: string }>();
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);

  const { data, isLoading, error } = useQuery<Overview>({
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

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="space-y-3">
        <Link href="/series">
          <Button variant="ghost" size="sm"><ArrowLeft className="w-4 h-4" /> Series</Button>
        </Link>
        <p className="text-sm text-destructive">Couldn't load this series.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link href="/series">
            <Button variant="ghost" size="sm" className="-ml-2 mb-1">
              <ArrowLeft className="w-4 h-4" /> Series
            </Button>
          </Link>
          <h1 className="editorial-title truncate" data-testid="text-series-title">
            {data.series.title}
          </h1>
          {data.series.description && (
            <p className="text-sm text-muted-foreground mt-1 max-w-xl">{data.series.description}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <HealthBadge state={data.continuity} />
          <Button onClick={() => setAddOpen(true)} data-testid="button-add-volume-open">
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
                    aria-label={`Remove ${v.work_title} from series`}
                    onClick={() => removeVolume(v.work_id, v.work_title)}
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
    </div>
  );
}
