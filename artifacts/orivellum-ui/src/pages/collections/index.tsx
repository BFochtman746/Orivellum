/**
 * Collections — /collections
 *
 * The reader/production layer: a collection groups whole series AND
 * standalone books for branding, metadata, and production — it never owns
 * canon or reading order. Shared facts live in canon domains, which are
 * listed here too so the three layers stay visibly distinct.
 */
import { useState } from "react";
import { Link } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { Page, EmptyState, ErrorState, LoadingState } from "@/components/primitives";
import {
  Boxes, Plus, Loader2, ArrowRight, Library, BookOpen, Globe2,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export const COLLECTION_TYPES: { value: string; label: string }[] = [
  { value: "branded-theme", label: "Branded theme" },
  { value: "shared-universe", label: "Shared universe" },
  { value: "anthology", label: "Anthology" },
  { value: "educational", label: "Educational" },
  { value: "author-backlist", label: "Author backlist" },
  { value: "other", label: "Other" },
];

export const DOMAIN_TYPES: { value: string; label: string }[] = [
  { value: "fictional", label: "Fictional" },
  { value: "historical", label: "Historical" },
  { value: "biblical", label: "Biblical" },
  { value: "mixed", label: "Mixed" },
  { value: "research", label: "Research" },
];

interface CollectionSummary {
  id: string;
  title: string;
  description: string;
  collection_type: string;
  status: string;
  series_count: number;
  work_count: number;
}

interface DomainSummary {
  id: string;
  title: string;
  description: string;
  domain_type: string;
  member_count: number;
  fact_count: number;
}

function CreateCollectionDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [ctype, setCtype] = useState("branded-theme");
  const [busy, setBusy] = useState(false);

  async function handleCreate() {
    if (!title.trim()) return;
    setBusy(true);
    try {
      const resp = await apiFetch(`${BASE}/collections`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          description: description.trim(),
          collection_type: ctype,
        }),
      });
      if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
      toast.success("Collection created");
      queryClient.invalidateQueries({ queryKey: ["collections-list"] });
      setTitle(""); setDescription("");
      onClose();
    } catch (e: any) {
      toast.error(e?.message || "Failed to create collection");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>New collection</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="coll-title">Title</Label>
            <Input id="coll-title" value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="The Complete Chronicles" data-testid="input-collection-title" />
          </div>
          <div className="space-y-1.5">
            <Label>What kind of family is this?</Label>
            <Select value={ctype} onValueChange={setCtype}>
              <SelectTrigger data-testid="select-collection-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                {COLLECTION_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="coll-desc">Description (optional)</Label>
            <Textarea id="coll-desc" value={description} rows={3}
              onChange={(e) => setDescription(e.target.value)}
              data-testid="input-collection-description" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={handleCreate} disabled={busy || !title.trim()} data-testid="button-create-collection">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreateDomainDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [dtype, setDtype] = useState("fictional");
  const [busy, setBusy] = useState(false);

  async function handleCreate() {
    if (!title.trim()) return;
    setBusy(true);
    try {
      const resp = await apiFetch(`${BASE}/canon-domains`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), domain_type: dtype }),
      });
      if (!resp.ok) throw new Error((await resp.json())?.detail || `HTTP ${resp.status}`);
      toast.success("Canon domain created");
      queryClient.invalidateQueries({ queryKey: ["canon-domains-list"] });
      setTitle("");
      onClose();
    } catch (e: any) {
      toast.error(e?.message || "Failed to create domain");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>New canon domain</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground">
          A shared universe or evidence domain. Facts scoped here bind every
          book it serves — one series, several series, or a whole collection.
        </p>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="dom-title">Title</Label>
            <Input id="dom-title" value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="The Shared World" data-testid="input-domain-title" />
          </div>
          <div className="space-y-1.5">
            <Label>Domain type</Label>
            <Select value={dtype} onValueChange={setDtype}>
              <SelectTrigger data-testid="select-domain-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                {DOMAIN_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={handleCreate} disabled={busy || !title.trim()} data-testid="button-create-domain">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function CollectionsPage() {
  const [createOpen, setCreateOpen] = useState(false);
  const [domainOpen, setDomainOpen] = useState(false);

  const { data, isLoading, isError, error, refetch } = useQuery<{ collections: CollectionSummary[] }>({
    queryKey: ["collections-list"],
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/collections`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
  });
  const { data: domainsData } = useQuery<{ domains: DomainSummary[] }>({
    queryKey: ["canon-domains-list"],
    queryFn: async () => {
      const resp = await apiFetch(`${BASE}/canon-domains`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    },
  });
  const collections = data?.collections ?? [];
  const domains = domainsData?.domains ?? [];
  const typeLabel = (v: string) => COLLECTION_TYPES.find((t) => t.value === v)?.label || v;

  return (
    <Page
      eyebrow="Reader & production"
      title="Collections"
      wide
      actions={
        <>
          <Button variant="outline" className="min-h-11" onClick={() => setDomainOpen(true)} data-testid="button-new-domain">
            <Globe2 className="w-4 h-4" /> New domain
          </Button>
          <Button className="min-h-11" onClick={() => setCreateOpen(true)} data-testid="button-new-collection">
            <Plus className="w-4 h-4" /> New collection
          </Button>
        </>
      }
    >
      <p className="flex items-start gap-2 text-sm text-muted-foreground -mt-2 max-w-xl">
        <Boxes className="w-4 h-4 shrink-0 mt-0.5" aria-hidden />
        <span>
          Group series and standalone books into reader and production
          families. Collections carry branding and metadata — shared facts
          live in canon domains, and reading order stays with each series.
        </span>
      </p>

      {isLoading && <LoadingState rows={3} label="Loading collections" />}
      {isError && (
        <ErrorState
          title="Couldn't load your collections"
          detail={String((error as Error)?.message ?? "The collections list didn't come back.")}
          onRetry={() => refetch()}
        />
      )}
      {!isLoading && !isError && collections.length === 0 && (
        <EmptyState
          icon={<Boxes />}
          title="No collections yet"
          description="Create a collection, then add series and standalone books to it."
          action={
            <Button onClick={() => setCreateOpen(true)} data-testid="button-new-collection-empty">
              <Plus className="w-4 h-4" /> New collection
            </Button>
          }
        />
      )}

      {!isLoading && !isError && collections.length > 0 && (
      <div className="space-y-3">
        {collections.map((c) => (
          <Link key={c.id} href={`/collections/${c.id}`}>
            <Card className="cursor-pointer hover-elevate" data-testid={`card-collection-${c.id}`}>
              <CardContent className="py-4 min-h-11 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="font-medium truncate">{c.title}</div>
                  <div className="text-sm text-muted-foreground truncate">
                    {typeLabel(c.collection_type)}
                    {c.description ? ` — ${c.description}` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Badge variant="outline" className="gap-1">
                    <Library className="w-3.5 h-3.5" aria-hidden />
                    {c.series_count} series
                  </Badge>
                  <Badge variant="outline" className="gap-1">
                    <BookOpen className="w-3.5 h-3.5" aria-hidden />
                    {c.work_count} {c.work_count === 1 ? "book" : "books"}
                  </Badge>
                  <ArrowRight className="w-4 h-4 text-muted-foreground" aria-hidden />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
      )}

      <div className="space-y-3">
        <h2 className="text-sm font-medium flex items-center gap-2 text-muted-foreground">
          <Globe2 className="w-4 h-4" aria-hidden /> Canon domains
        </h2>
        {domains.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No shared universes yet. A domain lets one set of facts serve
            several series or a whole collection.
          </p>
        )}
        {domains.map((d) => (
          <Card key={d.id} data-testid={`card-domain-${d.id}`}>
            <CardContent className="py-3 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="font-medium truncate">{d.title}</div>
                <div className="text-sm text-muted-foreground truncate">
                  {DOMAIN_TYPES.find((t) => t.value === d.domain_type)?.label || d.domain_type}
                  {d.description ? ` — ${d.description}` : ""}
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <Badge variant="outline">{d.member_count} member{d.member_count === 1 ? "" : "s"}</Badge>
                <Badge variant="outline">{d.fact_count} fact{d.fact_count === 1 ? "" : "s"}</Badge>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <CreateCollectionDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      <CreateDomainDialog open={domainOpen} onClose={() => setDomainOpen(false)} />
    </Page>
  );
}
