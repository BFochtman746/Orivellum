/**
 * Collection detail — /collections/:collectionId
 *
 * Membership management for one reader/production family: add whole
 * series or standalone books, see linked canon domains, and edit the
 * collection's type/status/promise. Removing a member never touches the
 * member itself — a collection is a grouping, not an owner.
 */
import { useState } from "react";
import { Link, useRoute } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  Boxes, Plus, Loader2, ArrowLeft, Library, BookOpen, Globe2, X,
} from "lucide-react";
import { COLLECTION_TYPES } from "./index";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface Member {
  member_kind: "series" | "work";
  member_id: string;
  title: string;
  book_count?: number;
}

interface CollectionDetailData {
  id: string;
  title: string;
  description: string;
  collection_type: string;
  status: string;
  reader_promise: string;
  members: Member[];
  domains: { id: string; title: string; domain_type: string }[];
}

function AddMemberDialog({
  collectionId, open, onClose,
}: { collectionId: string; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<"series" | "work">("series");
  const [memberId, setMemberId] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: seriesData } = useQuery<{ series: { id: string; title: string }[] }>({
    queryKey: ["series-list"],
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/series`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
    enabled: open,
  });
  const { data: worksData } = useQuery<{ works: { id: string; title: string }[] }>({
    queryKey: ["works-list-for-collection"],
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/works`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
    enabled: open,
  });
  const options =
    kind === "series" ? (seriesData?.series ?? []) : (worksData?.works ?? []);

  async function handleAdd() {
    if (!memberId) return;
    setBusy(true);
    try {
      const send = (confirm: boolean) =>
        apiFetch(`${BASE}/collections/${collectionId}/members`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            member_kind: kind, member_id: memberId, confirm_canon_binding: confirm,
          }),
        });
      let resp = await send(false);
      if (!resp.ok) {
        const detail = (await resp.json())?.detail || `HTTP ${resp.status}`;
        // A canon domain serves this collection — binding is explicit, never silent
        if (resp.status === 422 && String(detail).includes("bind shared canon")) {
          if (!window.confirm(`${detail}\n\nBind this canon to the new member's books?`)) {
            setBusy(false);
            return;
          }
          resp = await send(true);
          if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
        } else {
          throw new Error(detail);
        }
      }
      toast.success("Added to collection");
      queryClient.invalidateQueries({ queryKey: ["collection-detail", collectionId] });
      queryClient.invalidateQueries({ queryKey: ["collections-list"] });
      setMemberId("");
      onClose();
    } catch (e: any) {
      toast.error(e?.message || "Failed to add member");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Add to collection</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>What are you adding?</Label>
            <Select value={kind} onValueChange={(v) => { setKind(v as any); setMemberId(""); }}>
              <SelectTrigger data-testid="select-member-kind"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="series">A whole series</SelectItem>
                <SelectItem value="work">A standalone book</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>{kind === "series" ? "Series" : "Book"}</Label>
            <Select value={memberId} onValueChange={setMemberId}>
              <SelectTrigger data-testid="select-member-id">
                <SelectValue placeholder={`Pick a ${kind === "series" ? "series" : "book"}…`} />
              </SelectTrigger>
              <SelectContent>
                {options.map((o) => (
                  <SelectItem key={o.id} value={o.id}>{o.title}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={handleAdd} disabled={busy || !memberId} data-testid="button-add-member">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function CollectionDetail() {
  const [, params] = useRoute("/collections/:collectionId");
  const collectionId = params?.collectionId ?? "";
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);

  const { data, isLoading, error } = useQuery<CollectionDetailData>({
    queryKey: ["collection-detail", collectionId],
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/collections/${collectionId}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
    enabled: !!collectionId,
  });

  async function removeMember(m: Member) {
    try {
      const resp = await apiFetch(
        `${BASE}/collections/${collectionId}/members/${m.member_kind}/${m.member_id}`,
        { method: "DELETE" },
      );
      if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
      toast.success("Removed from collection");
      queryClient.invalidateQueries({ queryKey: ["collection-detail", collectionId] });
      queryClient.invalidateQueries({ queryKey: ["collections-list"] });
    } catch (e: any) {
      toast.error(e?.message || "Failed to remove member");
    }
  }

  async function patchCollection(body: Record<string, string>) {
    try {
      const resp = await apiFetch(`${BASE}/collections/${collectionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
      toast.success("Saved");
      queryClient.invalidateQueries({ queryKey: ["collection-detail", collectionId] });
      queryClient.invalidateQueries({ queryKey: ["collections-list"] });
    } catch (e: any) {
      toast.error(e?.message || "Failed to save");
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="space-y-3">
        <Link href="/collections" className="text-sm text-muted-foreground inline-flex items-center gap-1">
          <ArrowLeft className="w-4 h-4" /> Collections
        </Link>
        <p className="text-sm text-destructive">
          Couldn't load this collection{error ? `: ${String((error as Error).message)}` : ""}.
        </p>
      </div>
    );
  }

  const seriesMembers = data.members.filter((m) => m.member_kind === "series");
  const workMembers = data.members.filter((m) => m.member_kind === "work");

  return (
    <div className="space-y-6">
      <div>
        <Link href="/collections" className="text-sm text-muted-foreground inline-flex items-center gap-1">
          <ArrowLeft className="w-4 h-4" /> Collections
        </Link>
        <div className="flex items-start justify-between gap-4 mt-2">
          <div>
            <h1 className="editorial-title flex items-center gap-2.5">
              <Boxes className="w-6 h-6" aria-hidden />
              {data.title}
            </h1>
            {data.description && (
              <p className="text-sm text-muted-foreground mt-1 max-w-xl">{data.description}</p>
            )}
          </div>
          <Button onClick={() => setAddOpen(true)} data-testid="button-add-to-collection">
            <Plus className="w-4 h-4" /> Add member
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <div className="space-y-1.5 w-44">
          <Label className="text-xs text-muted-foreground">Family type</Label>
          <Select
            value={data.collection_type}
            onValueChange={(v) => patchCollection({ collection_type: v })}
          >
            <SelectTrigger data-testid="select-detail-type"><SelectValue /></SelectTrigger>
            <SelectContent>
              {COLLECTION_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5 w-40">
          <Label className="text-xs text-muted-foreground">Status</Label>
          <Select value={data.status} onValueChange={(v) => patchCollection({ status: v })}>
            <SelectTrigger data-testid="select-detail-status"><SelectValue /></SelectTrigger>
            <SelectContent>
              {["concept", "active", "paused", "archived"].map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {data.domains.length > 0 && (
        <Card>
          <CardContent className="py-3 flex items-center gap-3 flex-wrap">
            <span className="text-sm text-muted-foreground inline-flex items-center gap-1.5">
              <Globe2 className="w-4 h-4" aria-hidden /> Shared canon via
            </span>
            {data.domains.map((d) => (
              <Badge key={d.id} variant="outline">{d.title}</Badge>
            ))}
            <span className="text-xs text-muted-foreground">
              Facts in these domains bind every book in this collection.
            </span>
          </CardContent>
        </Card>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-medium flex items-center gap-2 text-muted-foreground">
          <Library className="w-4 h-4" aria-hidden /> Series ({seriesMembers.length})
        </h2>
        {seriesMembers.length === 0 && (
          <p className="text-sm text-muted-foreground">No series in this collection yet.</p>
        )}
        {seriesMembers.map((m) => (
          <Card key={m.member_id} className="group" data-testid={`member-series-${m.member_id}`}>
            <CardContent className="py-3 flex items-center justify-between gap-3">
              <Link href={`/series/${m.member_id}`} className="min-w-0 flex-1">
                <div className="font-medium truncate">{m.title}</div>
                <div className="text-xs text-muted-foreground">
                  {m.book_count ?? 0} {m.book_count === 1 ? "volume" : "volumes"} — keeps its own reading order
                </div>
              </Link>
              <Button
                variant="ghost" size="icon"
                className="opacity-0 group-hover:opacity-100"
                onClick={() => removeMember(m)}
                aria-label={`Remove ${m.title}`}
                data-testid={`button-remove-${m.member_id}`}
              >
                <X className="w-4 h-4" />
              </Button>
            </CardContent>
          </Card>
        ))}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium flex items-center gap-2 text-muted-foreground">
          <BookOpen className="w-4 h-4" aria-hidden /> Standalone books ({workMembers.length})
        </h2>
        {workMembers.length === 0 && (
          <p className="text-sm text-muted-foreground">No standalone books in this collection yet.</p>
        )}
        {workMembers.map((m) => (
          <Card key={m.member_id} className="group" data-testid={`member-work-${m.member_id}`}>
            <CardContent className="py-3 flex items-center justify-between gap-3">
              <Link href={`/works/${m.member_id}`} className="min-w-0 flex-1">
                <div className="font-medium truncate">{m.title}</div>
              </Link>
              <Button
                variant="ghost" size="icon"
                className="opacity-0 group-hover:opacity-100"
                onClick={() => removeMember(m)}
                aria-label={`Remove ${m.title}`}
                data-testid={`button-remove-${m.member_id}`}
              >
                <X className="w-4 h-4" />
              </Button>
            </CardContent>
          </Card>
        ))}
      </section>

      <AddMemberDialog collectionId={collectionId} open={addOpen} onClose={() => setAddOpen(false)} />
    </div>
  );
}
