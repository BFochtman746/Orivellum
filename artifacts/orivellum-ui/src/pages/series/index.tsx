/**
 * Series — /series
 *
 * Ordered groups of Works (a trilogy). A series is the scope that lets
 * canon, voice, and continuity span multiple books: facts established in
 * book 1 bind book 3, personas and voice baselines carry forward, and the
 * continuity checker verifies each volume against the accumulated state of
 * the earlier ones.
 */
import { useState } from "react";
import { Link } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { Library, Plus, Loader2, ArrowRight, BookOpen } from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export interface SeriesSummary {
  id: string;
  title: string;
  description: string | null;
  member_count: number;
  created_at: string;
}

function CreateSeriesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleCreate() {
    if (!title.trim()) return;
    setBusy(true);
    try {
      const resp = await apiFetch(`${BASE}/series`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), description: description.trim() }),
      });
      if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
      toast.success("Series created");
      queryClient.invalidateQueries({ queryKey: ["series-list"] });
      setTitle("");
      setDescription("");
      onClose();
    } catch (e: any) {
      toast.error(e?.message || "Failed to create series");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New series</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="series-title">Title</Label>
            <Input
              id="series-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="The Trilogy"
              data-testid="input-series-title"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="series-desc">Description (optional)</Label>
            <Textarea
              id="series-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              data-testid="input-series-description"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={handleCreate} disabled={busy || !title.trim()} data-testid="button-create-series">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function SeriesList() {
  const [createOpen, setCreateOpen] = useState(false);
  const { data, isLoading, error } = useQuery<{ series: SeriesSummary[] }>({
    queryKey: ["series-list"],
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/series`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
  });
  const series = data?.series ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="editorial-title flex items-center gap-2.5">
            <Library className="w-6 h-6" aria-hidden />
            Series
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-xl">
            Group your books in reading order. Canon established in an earlier
            volume binds every later one; voice and personas carry forward.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)} data-testid="button-new-series">
          <Plus className="w-4 h-4" /> New series
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}
      {error && (
        <p className="text-sm text-destructive">Couldn't load series: {String((error as Error).message)}</p>
      )}
      {!isLoading && !error && series.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No series yet. Create one, then add your books as volumes.
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {series.map((s) => (
          <Link key={s.id} href={`/series/${s.id}`}>
            <Card className="cursor-pointer hover-elevate" data-testid={`card-series-${s.id}`}>
              <CardContent className="py-4 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="font-medium truncate">{s.title}</div>
                  {s.description && (
                    <div className="text-sm text-muted-foreground truncate">{s.description}</div>
                  )}
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Badge variant="outline" className="gap-1">
                    <BookOpen className="w-3.5 h-3.5" aria-hidden />
                    {s.member_count} {s.member_count === 1 ? "volume" : "volumes"}
                  </Badge>
                  <ArrowRight className="w-4 h-4 text-muted-foreground" aria-hidden />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      <CreateSeriesDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
