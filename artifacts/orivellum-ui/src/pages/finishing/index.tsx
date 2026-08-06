/**
 * Finishing Suite — PRESS (manuscript finalization) + ATELIER (cover/series design).
 *
 * Tabbed interface:
 *   PRESS   — manage books: style lock, chapters, epigraphs, packages, ARC seals
 *   ATELIER — manage series brands and cover versions with spine math
 */
import { useState } from "react";
import { apiFetch } from "@/lib/auth";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  BookOpen, Package, Layers, Lock, Check, ChevronRight,
  Plus, FileText, Stamp, Sparkles, RefreshCw, AlertTriangle,
  Image, Shield, BookMarked, Palette,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const BASE = `${import.meta.env.BASE_URL}api/finishing`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── helpers ─────────────────────────────────────────────────────────────────

function api(path: string, opts?: RequestInit) {
  return apiFetch(`${BASE}${path}`, opts).then(async (r) => {
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail ?? "Request failed");
    return data;
  });
}

function useApi<T>(key: string[], path: string) {
  return useQuery<T>({ queryKey: key, queryFn: () => api(path) });
}

function StatusBadge({ passed }: { passed: boolean }) {
  return passed
    ? <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200 border">PASS</Badge>
    : <Badge className="bg-rose-50 text-rose-700 border-rose-200 border">FAIL</Badge>;
}

function SectionHeader({ title, icon: Icon }: { title: string; icon: React.ElementType }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
    </div>
  );
}

// ─── PRESS ────────────────────────────────────────────────────────────────────

function PressBookCard({ book, onSelect, selected }: { book: any; onSelect: () => void; selected: boolean }) {
  const style = book.style ?? {};
  const chapCount = (book.chapters ?? []).length;
  const totalWords = (book.chapters ?? []).reduce((s: number, c: any) => s + (c.words ?? 0), 0);
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left rounded-lg border p-4 transition-all hover:border-primary/50 ${selected ? "border-primary bg-primary/5" : "border-border"}`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="font-medium text-sm leading-snug">{book.title}</div>
        {book.style_locked ? (
          <Lock className="h-3 w-3 text-muted-foreground shrink-0 mt-0.5" />
        ) : null}
      </div>
      {book.series && <div className="text-xs text-muted-foreground mb-2">{book.series}</div>}
      <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
        {chapCount > 0 && <span>{chapCount} ch · {totalWords.toLocaleString()} words</span>}
        {style.chapter_style && <span>{style.chapter_style}</span>}
        {style.trim && <span>{style.trim}</span>}
      </div>
    </button>
  );
}

function PressVerifyCard({ slug }: { slug: string }) {
  const { data, refetch, isFetching } = useQuery({
    queryKey: ["press-verify", slug],
    queryFn: () => api(`/press/books/${slug}/verify`),
    enabled: !!slug,
  });
  if (!data) return null;
  const checks = data.checks ?? {};
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Pre-flight</CardTitle>
          <div className="flex items-center gap-2">
            <StatusBadge passed={data.passed} />
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={`h-3 w-3 ${isFetching ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-1 text-xs">
        {Object.entries(checks).map(([k, v]) => (
          <div key={k} className="flex items-center gap-2">
            {v ? <Check className="h-3 w-3 text-emerald-500" /> : <AlertTriangle className="h-3 w-3 text-rose-500" />}
            <span className={v ? "text-foreground" : "text-rose-600"}>{k.replace(/_/g, " ")}</span>
          </div>
        ))}
        {data.word_count != null && (
          <div className="pt-2 text-muted-foreground">
            {data.word_count.toLocaleString()} words · ~{data.estimated_pages} pages
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PressDetail({ slug }: { slug: string }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["press-book", slug],
    queryFn: () => api(`/press/books/${slug}`),
  });
  const [addingChapter, setAddingChapter] = useState(false);
  const [chForm, setChForm] = useState({ number: "", title: "", words: "", has_epigraph: false });
  const [lockAuthor, setLockAuthor] = useState("");
  const [sealForm, setSealForm] = useState({ pkg_type: "publisher", target: "production", author: "", recipient: "" });

  const lockStyle = useMutation({
    mutationFn: () => api(`/press/books/${slug}/style/lock`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ author: lockAuthor }),
    }),
    onSuccess: () => { toast.success("Style locked"); qc.invalidateQueries({ queryKey: ["press-book", slug] }); },
    onError: (e: any) => toast.error(e.message),
  });

  const addChapter = useMutation({
    mutationFn: () => api(`/press/books/${slug}/chapters`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        number: parseInt(chForm.number), title: chForm.title,
        words: parseInt(chForm.words) || 0, has_epigraph: chForm.has_epigraph,
      }),
    }),
    onSuccess: () => {
      toast.success("Chapter added");
      setAddingChapter(false);
      setChForm({ number: "", title: "", words: "", has_epigraph: false });
      qc.invalidateQueries({ queryKey: ["press-book", slug] });
      qc.invalidateQueries({ queryKey: ["press-verify", slug] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  const setMatter = useMutation({
    mutationFn: (body: { front: boolean; back: boolean }) =>
      api(`/press/books/${slug}/matter`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => { toast.success("Matter updated"); qc.invalidateQueries({ queryKey: ["press-verify", slug] }); },
  });

  const sealPackage = useMutation({
    mutationFn: () => api(`/press/books/${slug}/seal`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sealForm),
    }),
    onSuccess: (d) => {
      toast.success(`Package sealed · SHA ${d.manifest?.package_sha256?.slice(0, 10)}…`);
      qc.invalidateQueries({ queryKey: ["press-book", slug] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  if (isLoading) return <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-16" /></div>;
  if (!data) return null;
  const b = data.book;
  const style = b.style ?? {};
  const chapters: any[] = b.chapters ?? [];

  return (
    <div className="space-y-4">
      {/* Style card */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <FileText className="h-3.5 w-3.5" /> Style
            </CardTitle>
            {b.style_locked
              ? <Badge variant="secondary" className="text-xs"><Lock className="h-2.5 w-2.5 mr-1" />Locked</Badge>
              : <Badge variant="outline" className="text-xs">Unlocked</Badge>}
          </div>
        </CardHeader>
        <CardContent className="text-xs space-y-1">
          {Object.entries(style as Record<string, string>).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <span className="text-muted-foreground">{k.replace(/_/g, " ")}</span>
              <span className="font-medium">{String(v)}</span>
            </div>
          ))}
          {!b.style_locked && (
            <div className="flex gap-2 pt-2">
              <Input placeholder="Author sign-off" value={lockAuthor} onChange={e => setLockAuthor(e.target.value)}
                className="h-7 text-xs" />
              <Button size="sm" className="h-7 text-xs" onClick={() => lockStyle.mutate()}
                disabled={!lockAuthor || lockStyle.isPending}>
                <Lock className="h-3 w-3 mr-1" /> Lock
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Chapters */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Chapters ({chapters.length})</CardTitle>
            <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setAddingChapter(v => !v)}>
              <Plus className="h-3 w-3 mr-1" /> Add
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {addingChapter && (
            <div className="border border-dashed rounded-lg p-3 space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <Label className="text-xs">Number</Label>
                  <Input className="h-7 text-xs" value={chForm.number} onChange={e => setChForm(f => ({ ...f, number: e.target.value }))} placeholder="#" />
                </div>
                <div className="col-span-2">
                  <Label className="text-xs">Title</Label>
                  <Input className="h-7 text-xs" value={chForm.title} onChange={e => setChForm(f => ({ ...f, title: e.target.value }))} placeholder="Chapter title" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label className="text-xs">Word count</Label>
                  <Input className="h-7 text-xs" value={chForm.words} onChange={e => setChForm(f => ({ ...f, words: e.target.value }))} placeholder="0" />
                </div>
                <div className="flex items-end gap-2 pb-0.5">
                  <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                    <input type="checkbox" checked={chForm.has_epigraph}
                      onChange={e => setChForm(f => ({ ...f, has_epigraph: e.target.checked }))} />
                    Epigraph slot
                  </label>
                </div>
              </div>
              <Button size="sm" className="h-7 text-xs w-full" onClick={() => addChapter.mutate()}
                disabled={!chForm.number || !chForm.title || addChapter.isPending}>
                Add chapter
              </Button>
            </div>
          )}
          {chapters.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-2">No chapters yet</p>
          ) : chapters.map((ch: any) => (
            <div key={ch.number} className="flex items-center justify-between text-xs border rounded px-2 py-1.5">
              <span className="font-medium">{ch.number}. {ch.title}</span>
              <div className="flex items-center gap-2 text-muted-foreground">
                {ch.words > 0 && <span>{ch.words.toLocaleString()} w</span>}
                {ch.has_epigraph && (
                  <Badge variant="outline" className="text-[10px] py-0 px-1">
                    {ch.epigraph_status === "APPROVED" ? "✓ epi" : "epi"}
                  </Badge>
                )}
              </div>
            </div>
          ))}
          {/* Front / back matter */}
          <div className="flex gap-3 pt-2 text-xs">
            {["front", "back"].map(side => {
              const key = `has_${side}` as "has_front" | "has_back";
              const active = b[key];
              return (
                <button key={side}
                  onClick={() => setMatter.mutate({ front: side === "front" ? !active : !!b.has_front, back: side === "back" ? !active : !!b.has_back })}
                  className={`flex items-center gap-1 rounded px-2 py-1 border transition-colors ${active ? "bg-emerald-50 border-emerald-200 text-emerald-700" : "border-border text-muted-foreground"}`}
                >
                  {active ? <Check className="h-3 w-3" /> : <Plus className="h-3 w-3" />}
                  {side} matter
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Pre-flight */}
      <PressVerifyCard slug={slug} />

      {/* Seal package */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Stamp className="h-3.5 w-3.5" /> Seal Package
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs">Type</Label>
              <select className="w-full h-7 text-xs border border-border rounded px-2 bg-background"
                value={sealForm.pkg_type} onChange={e => setSealForm(f => ({ ...f, pkg_type: e.target.value }))}>
                <option value="publisher">Publisher</option>
                <option value="test-reader">Test-reader (ARC)</option>
              </select>
            </div>
            {sealForm.pkg_type === "publisher" && (
              <div>
                <Label className="text-xs">Target</Label>
                <select className="w-full h-7 text-xs border border-border rounded px-2 bg-background"
                  value={sealForm.target} onChange={e => setSealForm(f => ({ ...f, target: e.target.value }))}>
                  <option value="production">Production</option>
                  <option value="submission">Submission (MS format)</option>
                </select>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs">Author sign-off</Label>
              <Input className="h-7 text-xs" value={sealForm.author} onChange={e => setSealForm(f => ({ ...f, author: e.target.value }))} placeholder="Your name" />
            </div>
            {sealForm.pkg_type === "test-reader" && (
              <div>
                <Label className="text-xs">Recipient (watermark)</Label>
                <Input className="h-7 text-xs" value={sealForm.recipient} onChange={e => setSealForm(f => ({ ...f, recipient: e.target.value }))} placeholder="Recipient name" />
              </div>
            )}
          </div>
          <Button size="sm" className="w-full h-8 text-xs" onClick={() => sealPackage.mutate()}
            disabled={!sealForm.author || sealPackage.isPending}>
            <Stamp className="h-3 w-3 mr-1" />
            {sealPackage.isPending ? "Sealing…" : "Seal package"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function PressTab() {
  const qc = useQueryClient();
  const { data, isLoading } = useApi<any>(["press-books"], "/press/books");
  const [selected, setSelected] = useState<string | null>(null);
  const [newBook, setNewBook] = useState({ title: "", author_name: "", series: "" });
  const [creating, setCreating] = useState(false);

  const createBook = useMutation({
    mutationFn: () => api("/press/books", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newBook),
    }),
    onSuccess: (d) => {
      toast.success(`Book "${d.book.title}" created`);
      setCreating(false);
      setNewBook({ title: "", author_name: "", series: "" });
      qc.invalidateQueries({ queryKey: ["press-books"] });
      setSelected(d.book.slug);
    },
    onError: (e: any) => toast.error(e.message),
  });

  const books: any[] = data?.books ?? [];

  return (
    <div className="grid grid-cols-[260px,1fr] gap-6 h-full">
      {/* Sidebar */}
      <div className="space-y-3 overflow-y-auto pr-1">
        <SectionHeader title="Press books" icon={BookOpen} />
        <Button variant="outline" size="sm" className="w-full h-8 text-xs" onClick={() => setCreating(v => !v)}>
          <Plus className="h-3 w-3 mr-1" /> New book
        </Button>
        {creating && (
          <Card>
            <CardContent className="pt-4 space-y-2">
              <div>
                <Label className="text-xs">Title</Label>
                <Input className="h-7 text-xs" value={newBook.title} onChange={e => setNewBook(f => ({ ...f, title: e.target.value }))} placeholder="Book One" />
              </div>
              <div>
                <Label className="text-xs">Author name</Label>
                <Input className="h-7 text-xs" value={newBook.author_name} onChange={e => setNewBook(f => ({ ...f, author_name: e.target.value }))} />
              </div>
              <div>
                <Label className="text-xs">Series (optional)</Label>
                <Input className="h-7 text-xs" value={newBook.series} onChange={e => setNewBook(f => ({ ...f, series: e.target.value }))} />
              </div>
              <Button size="sm" className="w-full h-7 text-xs" onClick={() => createBook.mutate()}
                disabled={!newBook.title || !newBook.author_name || createBook.isPending}>
                Create
              </Button>
            </CardContent>
          </Card>
        )}
        {isLoading
          ? [1,2].map(i => <Skeleton key={i} className="h-24 rounded-lg" />)
          : books.length === 0
          ? <p className="text-xs text-muted-foreground text-center py-8">No books yet</p>
          : books.map((b: any) => (
            <PressBookCard key={b.slug} book={b} selected={selected === b.slug} onSelect={() => setSelected(b.slug)} />
          ))}
      </div>

      {/* Detail */}
      <div className="overflow-y-auto">
        {selected
          ? <PressDetail slug={selected} />
          : (
            <div className="flex flex-col items-center justify-center h-64 gap-2 text-muted-foreground">
              <BookOpen className="h-8 w-8 opacity-30" />
              <p className="text-sm">Select a book to manage it</p>
            </div>
          )}
      </div>
    </div>
  );
}

// ─── ATELIER ──────────────────────────────────────────────────────────────────

function SpineCard({ spec }: { spec: any }) {
  if (!spec) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2"><Layers className="h-3.5 w-3.5" /> Product spec</CardTitle>
      </CardHeader>
      <CardContent className="text-xs space-y-1">
        {[
          ["Trim", `${spec.trim}  (${spec.trim_w}" × ${spec.trim_h}")`],
          ["Paper", spec.paper],
          ["Pages", spec.pages],
          ["Spine width", `${spec.spine_width}"`],
          ["Spine text", spec.spine_text_allowed ? "Allowed" : "Too short (<79 pp)"],
          ["Full cover wrap", `${spec.full_cover_width}" × ${spec.full_cover_height}"`],
          ["Bleed", `${spec.bleed}"`],
          ["Min DPI", spec.min_dpi],
          ["Barcode zone", spec.barcode_zone],
        ].map(([k, v]) => (
          <div key={k as string} className="flex justify-between">
            <span className="text-muted-foreground">{k}</span>
            <span className="font-medium text-right max-w-[60%] truncate" title={String(v)}>{v}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function AtelierBookDetail({ book }: { book: any }) {
  const qc = useQueryClient();
  const [coverForm, setCoverForm] = useState({ versions: 3, mood: "" });
  const [sealForm, setSealForm] = useState({ author: "", choose_version: "" });

  const genCovers = useMutation({
    mutationFn: () => api(`/atelier/books/${book.slug}/cover`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ versions: coverForm.versions, mood: coverForm.mood, gateway: "mock" }),
    }),
    onSuccess: () => { toast.success("Cover versions generated"); qc.invalidateQueries({ queryKey: ["atelier-series-books"] }); },
    onError: (e: any) => toast.error(e.message),
  });

  const sealDesign = useMutation({
    mutationFn: () => api(`/atelier/books/${book.slug}/seal`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sealForm),
    }),
    onSuccess: (d) => { toast.success(`Cover sealed · SHA ${d.manifest?.package_sha256?.slice(0, 10)}…`); qc.invalidateQueries({ queryKey: ["atelier-series-books"] }); },
    onError: (e: any) => toast.error(e.message),
  });

  const covers: any[] = book.cover_versions ?? [];

  return (
    <div className="space-y-4">
      <SpineCard spec={book.spec} />

      {/* Cover generation */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2"><Image className="h-3.5 w-3.5" /> Cover versions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label className="text-xs">Versions</Label>
              <Input type="number" min={1} max={8} className="h-7 text-xs" value={coverForm.versions}
                onChange={e => setCoverForm(f => ({ ...f, versions: parseInt(e.target.value) || 3 }))} />
            </div>
            <div>
              <Label className="text-xs">Mood / tagline</Label>
              <Input className="h-7 text-xs" value={coverForm.mood}
                onChange={e => setCoverForm(f => ({ ...f, mood: e.target.value }))} placeholder="e.g. grief that will not be silenced" />
            </div>
          </div>
          <Button size="sm" className="w-full h-7 text-xs" onClick={() => genCovers.mutate()} disabled={genCovers.isPending}>
            <Sparkles className="h-3 w-3 mr-1" /> {genCovers.isPending ? "Generating…" : "Generate covers"}
          </Button>
          {covers.length > 0 && (
            <div className="space-y-2 mt-1">
              {covers.map((v: any) => (
                <div key={v.version_id}
                  onClick={() => setSealForm(f => ({ ...f, choose_version: v.version_id }))}
                  className={`rounded border p-2 cursor-pointer text-xs transition-colors ${sealForm.choose_version === v.version_id ? "border-primary bg-primary/5" : "border-border"}`}
                >
                  <div className="font-mono text-[10px] text-muted-foreground mb-1">{v.version_id} · {v.status}</div>
                  <div className="text-foreground leading-relaxed">{v.prompt}</div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Seal design */}
      {covers.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2"><Shield className="h-3.5 w-3.5" /> Seal cover</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-xs text-muted-foreground">
              Select a version above, then sign off to seal.
            </p>
            <div>
              <Label className="text-xs">Author sign-off</Label>
              <Input className="h-7 text-xs" value={sealForm.author}
                onChange={e => setSealForm(f => ({ ...f, author: e.target.value }))} placeholder="Your name" />
            </div>
            <Button size="sm" className="w-full h-7 text-xs" onClick={() => sealDesign.mutate()}
              disabled={!sealForm.author || !sealForm.choose_version || sealDesign.isPending}>
              <Stamp className="h-3 w-3 mr-1" /> {sealDesign.isPending ? "Sealing…" : "Seal cover"}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function AtelierSeriesCard({ series, selected, onSelect }: { series: any; selected: boolean; onSelect: () => void }) {
  const brand = series.brand ?? {};
  const tokenCount = Object.values(brand).filter(Boolean).length;
  return (
    <button onClick={onSelect}
      className={`w-full text-left rounded-lg border p-4 transition-all hover:border-primary/50 ${selected ? "border-primary bg-primary/5" : "border-border"}`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="font-medium text-sm">{series.name}</div>
        {series.locked ? <Lock className="h-3 w-3 text-muted-foreground shrink-0 mt-0.5" /> : null}
      </div>
      <div className="text-xs text-muted-foreground">
        {series.books} book{series.books !== 1 ? "s" : ""} · {tokenCount}/8 brand tokens
      </div>
    </button>
  );
}

const BRAND_KEYS = ["body_font","heading_font","palette","imagery","composition","title_pos","author_pos","logo"] as const;

function AtelierSeriesDetail({ seriesSlug }: { seriesSlug: string }) {
  const qc = useQueryClient();
  const { data: serData } = useQuery({
    queryKey: ["atelier-series-detail", seriesSlug],
    queryFn: () => api(`/atelier/series/${seriesSlug}`),
  });
  const { data: booksData } = useQuery({
    queryKey: ["atelier-series-books", seriesSlug],
    queryFn: () => api(`/atelier/series/${seriesSlug}/books`),
  });
  const [brand, setBrand] = useState<Record<string, string>>({});
  const [lockAuthor, setLockAuthor] = useState("");
  const [selectedBook, setSelectedBook] = useState<any>(null);
  const [newBook, setNewBook] = useState({ title: "", number: "1", pages: "300", paper: "cream", trim: "6x9" });
  const [addingBook, setAddingBook] = useState(false);

  const series = serData?.series;
  const books: any[] = booksData?.books ?? [];

  const updateBrand = useMutation({
    mutationFn: () => api(`/atelier/series/${seriesSlug}/brand`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(Object.entries(brand).filter(([,v]) => v))),
    }),
    onSuccess: () => { toast.success("Brand updated"); qc.invalidateQueries({ queryKey: ["atelier-series-detail", seriesSlug] }); },
    onError: (e: any) => toast.error(e.message),
  });

  const lockSeries = useMutation({
    mutationFn: () => api(`/atelier/series/${seriesSlug}/lock`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ author: lockAuthor }),
    }),
    onSuccess: () => { toast.success("Series LOCKED"); qc.invalidateQueries({ queryKey: ["atelier-series-detail", seriesSlug] }); },
    onError: (e: any) => toast.error(e.message),
  });

  const createBook = useMutation({
    mutationFn: () => api(`/atelier/series/${seriesSlug}/books`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...newBook, number: parseInt(newBook.number), pages: parseInt(newBook.pages) }),
    }),
    onSuccess: (d) => {
      toast.success(`Book "${d.book.title}" added`);
      setAddingBook(false);
      qc.invalidateQueries({ queryKey: ["atelier-series-books", seriesSlug] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  if (!series) return <Skeleton className="h-48" />;
  const existingBrand = series.brand ?? {};

  return (
    <div className="grid grid-cols-[1fr,1fr] gap-4">
      {/* Brand tokens */}
      <div className="space-y-3">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm flex items-center gap-2"><Palette className="h-3.5 w-3.5" /> Brand tokens</CardTitle>
              {series.locked
                ? <Badge variant="secondary" className="text-xs"><Lock className="h-2.5 w-2.5 mr-1" />Locked</Badge>
                : <Badge variant="outline" className="text-xs">Editable</Badge>}
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {BRAND_KEYS.map(k => (
              <div key={k}>
                <Label className="text-xs">{k.replace(/_/g, " ")}</Label>
                {series.locked
                  ? <p className="text-xs text-foreground mt-0.5">{existingBrand[k] || <span className="text-muted-foreground italic">—</span>}</p>
                  : <Input className="h-7 text-xs" defaultValue={existingBrand[k] ?? ""}
                    onChange={e => setBrand(b => ({ ...b, [k]: e.target.value }))}
                    placeholder={k.replace(/_/g, " ")} />}
              </div>
            ))}
            {!series.locked && (
              <div className="flex gap-2 pt-1">
                <Button size="sm" variant="outline" className="h-7 text-xs flex-1" onClick={() => updateBrand.mutate()} disabled={updateBrand.isPending}>
                  Save
                </Button>
                <div className="flex gap-2 flex-1">
                  <Input placeholder="Author sign-off" value={lockAuthor} onChange={e => setLockAuthor(e.target.value)} className="h-7 text-xs" />
                  <Button size="sm" className="h-7 text-xs whitespace-nowrap" onClick={() => lockSeries.mutate()} disabled={!lockAuthor || lockSeries.isPending}>
                    <Lock className="h-3 w-3 mr-1" /> Lock
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Books */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Books</h3>
          <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setAddingBook(v => !v)}>
            <Plus className="h-3 w-3 mr-1" /> Add book
          </Button>
        </div>
        {addingBook && (
          <Card>
            <CardContent className="pt-4 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label className="text-xs">Title</Label>
                  <Input className="h-7 text-xs" value={newBook.title} onChange={e => setNewBook(f => ({ ...f, title: e.target.value }))} />
                </div>
                <div>
                  <Label className="text-xs">Number</Label>
                  <Input type="number" className="h-7 text-xs" value={newBook.number} onChange={e => setNewBook(f => ({ ...f, number: e.target.value }))} />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <Label className="text-xs">Pages</Label>
                  <Input type="number" className="h-7 text-xs" value={newBook.pages} onChange={e => setNewBook(f => ({ ...f, pages: e.target.value }))} />
                </div>
                <div>
                  <Label className="text-xs">Paper</Label>
                  <select className="w-full h-7 text-xs border border-border rounded px-2 bg-background"
                    value={newBook.paper} onChange={e => setNewBook(f => ({ ...f, paper: e.target.value }))}>
                    <option value="cream">Cream</option>
                    <option value="white">White</option>
                    <option value="color">Color</option>
                  </select>
                </div>
                <div>
                  <Label className="text-xs">Trim</Label>
                  <select className="w-full h-7 text-xs border border-border rounded px-2 bg-background"
                    value={newBook.trim} onChange={e => setNewBook(f => ({ ...f, trim: e.target.value }))}>
                    {["5x8","5.25x8","5.5x8.5","6x9","7x10"].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              </div>
              <Button size="sm" className="w-full h-7 text-xs" onClick={() => createBook.mutate()}
                disabled={!newBook.title || createBook.isPending}>
                Add to series
              </Button>
            </CardContent>
          </Card>
        )}
        {books.length === 0
          ? <p className="text-xs text-muted-foreground text-center py-6">No books in this series</p>
          : books.map((b: any) => (
            <div key={b.slug}>
              <button onClick={() => setSelectedBook(selectedBook?.slug === b.slug ? null : b)}
                className={`w-full text-left rounded-lg border p-3 text-xs transition-all hover:border-primary/50 ${selectedBook?.slug === b.slug ? "border-primary bg-primary/5" : "border-border"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">Book {b.number}: {b.title}</span>
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <span>{b.pages} pp · {b.trim} · {b.paper}</span>
                    {b.state === "SEALED" && <Lock className="h-3 w-3" />}
                    <ChevronRight className={`h-3 w-3 transition-transform ${selectedBook?.slug === b.slug ? "rotate-90" : ""}`} />
                  </div>
                </div>
                {(b.cover_versions ?? []).length > 0 && (
                  <div className="mt-1 text-muted-foreground">
                    {b.cover_versions.length} cover version{b.cover_versions.length !== 1 ? "s" : ""}
                    {b.sealed_version ? ` · sealed: ${b.sealed_version}` : ""}
                  </div>
                )}
              </button>
              {selectedBook?.slug === b.slug && (
                <div className="mt-2 ml-4">
                  <AtelierBookDetail book={b} />
                </div>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}

function AtelierTab() {
  const qc = useQueryClient();
  const { data, isLoading } = useApi<any>(["atelier-series"], "/atelier/series");
  const [selected, setSelected] = useState<string | null>(null);
  const [newSeries, setNewSeries] = useState({ name: "", books: "1" });
  const [creating, setCreating] = useState(false);

  const createSeries = useMutation({
    mutationFn: () => api("/atelier/series", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newSeries.name, books: parseInt(newSeries.books) || 1 }),
    }),
    onSuccess: (d) => {
      toast.success(`Series "${d.series.name}" created`);
      setCreating(false);
      setNewSeries({ name: "", books: "1" });
      qc.invalidateQueries({ queryKey: ["atelier-series"] });
      setSelected(d.series.slug);
    },
    onError: (e: any) => toast.error(e.message),
  });

  const series: any[] = data?.series ?? [];

  return (
    <div className="grid grid-cols-[220px,1fr] gap-6 h-full">
      <div className="space-y-3 overflow-y-auto pr-1">
        <SectionHeader title="Series" icon={BookMarked} />
        <Button variant="outline" size="sm" className="w-full h-8 text-xs" onClick={() => setCreating(v => !v)}>
          <Plus className="h-3 w-3 mr-1" /> New series
        </Button>
        {creating && (
          <Card>
            <CardContent className="pt-4 space-y-2">
              <div>
                <Label className="text-xs">Series name</Label>
                <Input className="h-7 text-xs" value={newSeries.name} onChange={e => setNewSeries(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Unhindered" />
              </div>
              <div>
                <Label className="text-xs">Number of books</Label>
                <Input type="number" min={1} className="h-7 text-xs" value={newSeries.books} onChange={e => setNewSeries(f => ({ ...f, books: e.target.value }))} />
              </div>
              <Button size="sm" className="w-full h-7 text-xs" onClick={() => createSeries.mutate()}
                disabled={!newSeries.name || createSeries.isPending}>
                Create series
              </Button>
            </CardContent>
          </Card>
        )}
        {isLoading
          ? [1,2].map(i => <Skeleton key={i} className="h-20 rounded-lg" />)
          : series.length === 0
          ? <p className="text-xs text-muted-foreground text-center py-8">No series yet</p>
          : series.map((s: any) => (
            <AtelierSeriesCard key={s.slug} series={s} selected={selected === s.slug} onSelect={() => setSelected(s.slug)} />
          ))}
      </div>
      <div className="overflow-y-auto">
        {selected
          ? <AtelierSeriesDetail seriesSlug={selected} />
          : (
            <div className="flex flex-col items-center justify-center h-64 gap-2 text-muted-foreground">
              <BookMarked className="h-8 w-8 opacity-30" />
              <p className="text-sm">Select a series to manage it</p>
            </div>
          )}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const TABS = [
  { key: "press",   label: "PRESS",   icon: FileText },
  { key: "atelier", label: "ATELIER", icon: Palette  },
] as const;
type TabKey = typeof TABS[number]["key"];

export default function FinishingPage() {
  const [tab, setTab] = useState<TabKey>("press");

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="border-b bg-background px-6 py-4 shrink-0">
        <div className="flex items-center gap-3 mb-3">
          <Package className="h-5 w-5 text-muted-foreground" />
          <div>
            <h1 className="text-base font-semibold">Finishing Suite</h1>
            <p className="text-xs text-muted-foreground">
              PRESS — manuscript finalization &amp; delivery · ATELIER — cover, spine &amp; series design
            </p>
          </div>
        </div>
        <div className="flex gap-1">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium transition-colors ${
                tab === t.key
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              }`}
            >
              <t.icon className="h-3 w-3" />
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden p-6">
        {tab === "press"   && <PressTab />}
        {tab === "atelier" && <AtelierTab />}
      </div>
    </div>
  );
}
