/**
 * Collection detail — /collections/:collectionId
 *
 * Membership management for one reader/production family: add whole
 * series or standalone books, see linked canon domains, and edit the
 * collection's type/status/promise. Removing a member never touches the
 * member itself — a collection is a grouping, not an owner.
 */
import { useState } from "react";
import { Link, useRoute, useSearch } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { Page, ErrorState, LoadingState, ConfirmAction } from "@/components/primitives";
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
  // Canon-binding confirmation prompt (a domain serves this collection).
  const [canonPrompt, setCanonPrompt] = useState<string | null>(null);

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

  const send = (confirm: boolean) =>
    apiFetch(`${BASE}/collections/${collectionId}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        member_kind: kind, member_id: memberId, confirm_canon_binding: confirm,
      }),
    });

  async function finishAdd(resp: Response) {
    if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
    toast.success("Added to collection");
    queryClient.invalidateQueries({ queryKey: ["collection-detail", collectionId] });
    queryClient.invalidateQueries({ queryKey: ["collections-list"] });
    setMemberId("");
    onClose();
  }

  async function handleAdd() {
    if (!memberId) return;
    setBusy(true);
    try {
      const resp = await send(false);
      if (!resp.ok) {
        const detail = (await resp.clone().json())?.detail || `HTTP ${resp.status}`;
        // A canon domain serves this collection — binding is explicit, never silent
        if (resp.status === 422 && String(detail).includes("bind shared canon")) {
          setCanonPrompt(String(detail));
          setBusy(false);
          return;
        }
        throw new Error(detail);
      }
      await finishAdd(resp);
    } catch (e: any) {
      toast.error(e?.message || "Failed to add member");
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
          <Button variant="outline" className="min-h-11" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button className="min-h-11" onClick={handleAdd} disabled={busy || !memberId} data-testid="button-add-member">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
      <ConfirmAction
        open={canonPrompt !== null}
        onOpenChange={(v) => !v && setCanonPrompt(null)}
        title="Bind shared canon to this member?"
        consequence={`${canonPrompt ?? ""}\n\nThe new member's books will inherit this canon. You can review bound domains on the collection afterward.`}
        confirmLabel="Bind canon"
        onConfirm={confirmCanonBinding}
      />
    </Dialog>
  );
}

export default function CollectionDetail() {
  const [, params] = useRoute("/collections/:collectionId");
  const collectionId = params?.collectionId ?? "";
  const search = useSearch();
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [pendingRemove, setPendingRemove] = useState<Member | null>(null);

  // Reached from a Work/Library context (deep link carries ?from=<workId>):
  // offer a direct route back to that owning book; otherwise back to the list.
  const fromWorkId = new URLSearchParams(search).get("from");
  const backHref = fromWorkId ? `/works/${fromWorkId}` : "/collections";
  const backLabel = fromWorkId ? "Back to book" : "Collections";

  const { data, isLoading, isError, error, refetch } = useQuery<CollectionDetailData>({
    queryKey: ["collection-detail", collectionId],
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/collections/${collectionId}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
    enabled: !!collectionId,
  });

  const BackLink = (
    <Link href={backHref} className="text-sm text-muted-foreground inline-flex items-center gap-1 min-h-11" data-testid="button-collection-back">
      <ArrowLeft className="w-4 h-4" /> {backLabel}
    </Link>
  );

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
      <Page>
        {BackLink}
        <LoadingState rows={3} label="Loading collection" />
      </Page>
    );
  }
  if (isError || !data) {
    return (
      <Page>
        {BackLink}
        <ErrorState
          title="Couldn't load this collection"
          detail={String((error as Error)?.message ?? "The collection didn't come back.")}
          onRetry={() => refetch()}
        />
      </Page>
    );
  }

  const seriesMembers = data.members.filter((m) => m.member_kind === "series");
  const workMembers = data.members.filter((m) => m.member_kind === "work");

  return (
    <Page wide>
      <div>
        {BackLink}
        <div className="flex items-start justify-between gap-4 mt-2">
          <div className="min-w-0">
            <h1 className="page-h1 flex items-center gap-2.5 truncate">
              <Boxes className="w-6 h-6 shrink-0" aria-hidden />
              <span className="truncate">{data.title}</span>
            </h1>
            {data.description && (
              <p className="text-sm text-muted-foreground mt-1 max-w-xl">{data.description}</p>
            )}
          </div>
          <Button onClick={() => setAddOpen(true)} className="min-h-11 shrink-0" data-testid="button-add-to-collection">
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
                className="min-h-11 min-w-11 shrink-0 opacity-60 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
                onClick={() => setPendingRemove(m)}
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
                className="min-h-11 min-w-11 shrink-0 opacity-60 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
                onClick={() => setPendingRemove(m)}
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

      <ConfirmAction
        open={pendingRemove !== null}
        onOpenChange={(v) => !v && setPendingRemove(null)}
        title="Remove from this collection?"
        consequence={`“${pendingRemove?.title ?? ""}” leaves this collection. The ${pendingRemove?.member_kind === "series" ? "series" : "book"} itself is not deleted — you can add it back later.`}
        confirmLabel="Remove"
        destructive
        onConfirm={() => {
          if (pendingRemove) removeMember(pendingRemove);
          setPendingRemove(null);
        }}
      />
    </Page>
  );
}
