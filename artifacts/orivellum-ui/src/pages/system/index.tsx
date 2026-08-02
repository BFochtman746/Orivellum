import { useGetSystemHealth, useListCapabilities, getGetSystemHealthQueryKey } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Activity, Database, Cpu, CheckCircle2, XCircle, AlertCircle, Terminal, Sparkles, Moon, Brain, Trash2, ScrollText, User, Settings } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

// ─── Nightshift card ──────────────────────────────────────────────────────────

function NightshiftCard() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["system", "jobs"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/jobs`);
      if (!r.ok) throw new Error("jobs fetch failed");
      return r.json() as Promise<{ nightshift: { ran_at: string; docs_processed: number; items_added: number; report_path: string | null } | null }>;
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const trigger = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/nightshift/run`, { method: "POST" });
      if (!r.ok) throw new Error("trigger failed");
      return r.json();
    },
    onSuccess: () => {
      toast.success("Nightshift started — re-processes documents with sparse knowledge");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["system", "jobs"] }), 5000);
    },
    onError: () => toast.error("Could not start nightshift"),
  });

  const ns = data?.nightshift;
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-3">
            <Moon className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-serif font-medium">Nightshift</h2>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5 text-xs"
            onClick={() => trigger.mutate()}
            disabled={trigger.isPending}
          >
            {trigger.isPending ? (
              <><Activity className="w-3 h-3 animate-spin" />Running…</>
            ) : (
              <><Moon className="w-3 h-3" />Run Now</>
            )}
          </Button>
        </div>
        {isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : ns ? (
          <div className="space-y-1 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Last run</span>
              <span className="font-mono text-xs">{new Date(ns.ran_at).toLocaleString()}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Documents processed</span>
              <Badge variant="secondary">{ns.docs_processed}</Badge>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Knowledge items added</span>
              <Badge variant="secondary">{ns.items_added}</Badge>
            </div>
            {ns.report_path && (
              <p className="text-[10px] font-mono text-muted-foreground pt-1 truncate">{ns.report_path}</p>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No nightshift runs yet — fires daily at 03:00 and re-processes documents with sparse knowledge. Use "Run Now" to trigger it manually.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ─── User memory card ─────────────────────────────────────────────────────────

function UserMemoryCard() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["system", "user-memory"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/user-memory`);
      if (!r.ok) throw new Error("memory fetch failed");
      return r.json() as Promise<{ memories: { id: string; key: string; value: string; created_at: string }[] }>;
    },
    staleTime: 30_000,
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      const r = await apiFetch(`${API_BASE}/api/system/user-memory/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error("delete failed");
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["system", "user-memory"] }); toast.success("Memory deleted"); },
    onError: () => toast.error("Could not delete"),
  });

  const memories = data?.memories ?? [];
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center gap-3 mb-4">
          <Brain className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-serif font-medium">My Memory</h2>
          <span className="text-xs text-muted-foreground">— facts Orivellum remembers about you</span>
        </div>
        {isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : memories.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No memories yet. Say things like "Remember that I prefer concise answers" and Orivellum will retain them across conversations.
          </p>
        ) : (
          <div className="space-y-2">
            {memories.map(m => (
              <div key={m.id} className="flex items-start gap-3 p-3 rounded-lg bg-muted/20 border border-border/40 group">
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-mono text-muted-foreground">{m.key}</p>
                  <p className="text-sm mt-0.5">{m.value}</p>
                </div>
                <button
                  onClick={() => del.mutate(m.id)}
                  disabled={del.isPending}
                  className="opacity-0 group-hover:opacity-60 hover:!opacity-100 p-1 text-destructive transition-opacity shrink-0"
                  title="Delete memory"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Version card ─────────────────────────────────────────────────────────────

function VersionCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["system", "version"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/version`);
      if (!r.ok) throw new Error("version fetch failed");
      return r.json() as Promise<{ version: string; product: string; python: string; platform: string }>;
    },
    staleTime: Infinity,
  });
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center gap-3 mb-3">
          <Terminal className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-serif font-medium">About</h2>
        </div>
        {isLoading ? <Skeleton className="h-8 w-40" /> : (
          <div className="space-y-1 font-mono text-xs text-muted-foreground">
            <p><span className="text-foreground font-semibold">{data?.product}</span> v{data?.version}</p>
            <p>Python {data?.python?.split(" ")[0]}</p>
            <p className="truncate">{data?.platform}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── AI Extraction toggle ─────────────────────────────────────────────────────

function useAiExtractionSetting() {
  return useQuery({
    queryKey: ["system", "ai-extraction"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/ai-extraction`);
      if (!r.ok) throw new Error("Failed to fetch AI extraction setting");
      return r.json() as Promise<{ enabled: boolean }>;
    },
    staleTime: 30_000,
  });
}

function useSetAiExtractionSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/ai-extraction`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error("Failed to update AI extraction setting");
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "ai-extraction"] });
    },
  });
}

export default function System() {
  const { data: health, isLoading: loadingHealth } = useGetSystemHealth({ query: { queryKey: getGetSystemHealthQueryKey(), refetchInterval: 10_000, staleTime: 8_000 } });
  const { data: capsResp, isLoading: loadingCaps } = useListCapabilities();
  const { data: aiExtraction, isLoading: loadingAiExt } = useAiExtractionSetting();
  const setAiExtraction = useSetAiExtractionSetting();

  const aiStatus = (health?.services?.ai as Record<string, string> | undefined)?.status;
  const aiEndpoint = (health?.services?.ai as Record<string, string> | undefined)?.endpoint;
  const dbStatus = (health?.services?.database as Record<string, string> | undefined)?.status;
  const aiOnline = aiStatus === "ok";

  return (
    <div className="space-y-8 animate-in fade-in duration-500 max-w-5xl mx-auto">
      <div className="border-b border-border/50 pb-4">
        <h1 className="text-3xl font-serif font-semibold tracking-tight">System Status</h1>
        <p className="text-muted-foreground mt-1 font-serif">Infrastructure health and local AI capabilities.</p>
      </div>

      <div className="grid md:grid-cols-3 gap-4">
        {/* Overall */}
        <Card className="bg-primary/5 border-primary/20">
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-mono text-sm uppercase tracking-wider">Overall Status</h3>
              <Activity className="w-5 h-5 text-primary" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                {health?.status === "ok" ? (
                  <CheckCircle2 className="w-6 h-6 text-emerald-500" />
                ) : (
                  <AlertCircle className="w-6 h-6 text-amber-500" />
                )}
                <span className="text-2xl font-serif font-semibold capitalize">
                  {health?.status || "Unknown"}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Database */}
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4 text-muted-foreground">
              <h3 className="font-mono text-sm uppercase tracking-wider">Database</h3>
              <Database className="w-5 h-5" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                {dbStatus === "ok" ? (
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                ) : (
                  <XCircle className="w-5 h-5 text-destructive" />
                )}
                <span className="text-xl font-medium">
                  {dbStatus === "ok" ? "Connected" : "Offline"}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* AI Engine */}
        <Card className={aiOnline ? "" : "border-amber-500/30 bg-amber-500/5"}>
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4 text-muted-foreground">
              <h3 className="font-mono text-sm uppercase tracking-wider">Local AI Engine</h3>
              <Cpu className="w-5 h-5" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  {aiOnline ? (
                    <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  ) : (
                    <XCircle className="w-5 h-5 text-amber-500" />
                  )}
                  <span className="text-xl font-medium">
                    {aiOnline ? "Connected" : "Unavailable"}
                  </span>
                </div>
                {aiEndpoint && (
                  <p className="text-[11px] font-mono text-muted-foreground truncate" title={aiEndpoint}>
                    {aiEndpoint}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* AI Extraction Setting */}
      <Card>
        <CardContent className="p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Sparkles className="w-5 h-5 text-primary mt-0.5 shrink-0" />
              <div className="space-y-1">
                <h3 className="font-medium text-sm">AI-Powered Knowledge Extraction</h3>
                <p className="text-sm text-muted-foreground max-w-xl">
                  When enabled, newly imported documents are analysed by your local AI to extract
                  named entities, key claims, and relationships — richer than what rule-based
                  harvesting alone can surface. Documents are marked ready first; extraction runs
                  afterwards and does not delay access to your files.
                </p>
                {!aiOnline && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                    Requires the local AI engine to be running. Enable it now and it will activate
                    automatically once the AI service is available.
                  </p>
                )}
              </div>
            </div>
            <div className="shrink-0 pt-0.5">
              {loadingAiExt ? (
                <Skeleton className="h-6 w-11 rounded-full" />
              ) : (
                <Switch
                  checked={aiExtraction?.enabled ?? false}
                  onCheckedChange={(checked) => setAiExtraction.mutate(checked)}
                  disabled={setAiExtraction.isPending}
                  aria-label="Enable AI-powered knowledge extraction"
                />
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* AI offline setup guide */}
      {!loadingHealth && !aiOnline && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Terminal className="w-4 h-4 text-amber-600" />
              <h3 className="font-mono text-sm font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wider">
                Local AI Setup
              </h3>
            </div>
            <p className="text-sm text-muted-foreground">
              Orivellum connects to a local AI server via the OpenAI-compatible API. No data leaves your machine.
              Choose one of the options below:
            </p>

            <div className="space-y-3">
              {/* Lemonade */}
              <div className="rounded-lg bg-background/60 border border-border/60 p-4 space-y-2">
                <p className="text-sm font-semibold">Option A — Lemonade (recommended)</p>
                <p className="text-xs text-muted-foreground">
                  Lemonade is a local model server tuned for Orivellum. It listens on port 13305 by default.
                </p>
                <div className="space-y-1.5">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">macOS / Linux</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`pip install lemonade-server
lemonade-server --port 13305`}
                  </pre>
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Windows (PowerShell)</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`pip install lemonade-server
lemonade-server --port 13305`}
                  </pre>
                </div>
              </div>

              {/* Ollama */}
              <div className="rounded-lg bg-background/60 border border-border/60 p-4 space-y-2">
                <p className="text-sm font-semibold">Option B — Ollama</p>
                <p className="text-xs text-muted-foreground">
                  Ollama listens on port 11434. Pull a model then point Orivellum at it.
                </p>
                <div className="space-y-1.5">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">macOS / Linux</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`ollama serve
ollama pull llama3.2
export ORIVELLUM_AI_URL=http://127.0.0.1:11434/v1`}
                  </pre>
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Windows (PowerShell)</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`ollama serve
ollama pull llama3.2
$env:ORIVELLUM_AI_URL="http://127.0.0.1:11434/v1"`}
                  </pre>
                </div>
              </div>

              {/* Custom */}
              <div className="rounded-lg bg-background/60 border border-border/60 p-4 space-y-2">
                <p className="text-sm font-semibold">Option C — Any OpenAI-compatible server</p>
                <p className="text-xs text-muted-foreground">
                  Set <code className="bg-muted px-1 rounded">ORIVELLUM_AI_URL</code> to the base URL of your server
                  (must expose <code className="bg-muted px-1 rounded">/chat/completions</code>).
                  Optionally set the model name in <code className="bg-muted px-1 rounded">config.yaml</code>.
                </p>
                <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`export ORIVELLUM_AI_URL=http://127.0.0.1:PORT/v1`}
                </pre>
              </div>
            </div>

            <p className="text-xs text-muted-foreground">
              After starting the server, reload this page — the AI status will update automatically.
              Your messages are always saved even when AI is offline.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Nightshift */}
      <NightshiftCard />

      {/* User Memory */}
      <UserMemoryCard />

      {/* Version info */}
      <VersionCard />

      {/* Capabilities */}
      <div className="space-y-4">
        <h2 className="text-xl font-serif font-medium border-b border-border/50 pb-2">Active Capabilities</h2>
        <div className="grid md:grid-cols-2 gap-4">
          {loadingCaps ? (
            [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)
          ) : (
            capsResp?.capabilities?.map((cap, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-4 rounded-lg bg-muted/20 border border-border/50"
              >
                <div className="font-medium font-mono text-sm">{cap.name}</div>
                <Badge
                  variant={cap.status === "active" ? "default" : "secondary"}
                  className="font-mono text-[10px] uppercase"
                >
                  {cap.status}
                </Badge>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Audit log */}
      <AuditLogCard />
    </div>
  );
}

// ─── Audit log card ───────────────────────────────────────────────────────────

const ACTOR_ICONS: Record<string, React.ElementType> = {
  pipeline: Cpu,
  system: Settings,
  user: User,
};

function AuditLogCard() {
  const { data, isLoading, refetch, isFetching } = useQuery<{
    entries: Array<{
      id: string; timestamp: string; actor: string; operation: string;
      object_id: string | null; object_type: string | null;
      result: string; detail: string | null;
    }>;
    count: number;
  }>({
    queryKey: ["system", "audit-log"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/audit-log?limit=50`);
      if (!r.ok) throw new Error("audit log fetch failed");
      return r.json();
    },
    staleTime: 60_000,
  });

  const entries = data?.entries ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <ScrollText className="w-5 h-5 text-muted-foreground" />
          Audit Log
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1"
        >
          {isFetching ? "refreshing…" : `${data?.count ?? 0} entries · refresh`}
        </button>
      </div>

      {isLoading ? (
        [1,2,3].map((i) => <Skeleton key={i} className="h-10 w-full" />)
      ) : entries.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground text-sm border border-dashed rounded-lg">
          No audit events recorded yet — actions will appear here as you use the system.
        </div>
      ) : (
        <div className="rounded-lg border border-border/50 overflow-hidden divide-y divide-border/30 max-h-80 overflow-y-auto">
          {entries.map((e) => {
            const ActorIcon = ACTOR_ICONS[e.actor] ?? Activity;
            const isOk = e.result === "ok";
            return (
              <div key={e.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/20 transition-colors">
                <ActorIcon className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-xs font-mono font-medium">{e.operation}</span>
                  {e.detail && (
                    <span className="text-[11px] font-mono text-muted-foreground ml-2">{e.detail}</span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`text-[10px] font-mono ${isOk ? "text-emerald-600" : "text-red-600"}`}>
                    {e.result}
                  </span>
                  <span className="text-[10px] font-mono text-muted-foreground/50">
                    {e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ""}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
