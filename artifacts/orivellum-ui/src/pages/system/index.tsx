import { useGetSystemHealth, useListCapabilities, getGetSystemHealthQueryKey } from "@workspace/api-client-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Activity, Database, Cpu, CheckCircle2, XCircle, AlertCircle, Terminal, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

function useAiExtractionSetting() {
  return useQuery({
    queryKey: ["system", "ai-extraction"],
    queryFn: async () => {
      const r = await fetch(`${API_BASE}/api/system/settings/ai-extraction`);
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
      const r = await fetch(`${API_BASE}/api/system/settings/ai-extraction`, {
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
                <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`# Install Lemonade (once)
pip install lemonade-server

# Start the server
lemonade-server --port 13305`}
                </pre>
              </div>

              {/* Ollama */}
              <div className="rounded-lg bg-background/60 border border-border/60 p-4 space-y-2">
                <p className="text-sm font-semibold">Option B — Ollama</p>
                <p className="text-xs text-muted-foreground">
                  Ollama listens on port 11434. Point Orivellum at it with the env var below, then pull a model.
                </p>
                <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`# Start Ollama
ollama serve

# Pull a model (one-time)
ollama pull llama3.2

# Tell Orivellum where to find it
export ORIVELLUM_AI_URL=http://127.0.0.1:11434/v1`}
                </pre>
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
    </div>
  );
}
