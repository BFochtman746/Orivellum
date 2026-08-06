import { useRef, useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useListStudioOutputs, useListVoices, useListLibrary, useListWorks } from "@workspace/api-client-react";
import { ErrorBoundary } from "@/components/error-boundary";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Mic, Play, Pause, Settings2, Video, Image as ImageIcon,
  FileAudio, Loader2, Volume2, Download, BookHeadphones, FileText,
  X, Trash2, RefreshCw, Activity, Sparkles, FileSpreadsheet,
  Presentation, CheckCircle2, AlertTriangle, ChevronRight, Wand2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Link } from "wouter";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch } from "@/lib/auth";
import { VoiceStudio } from "./VoiceStudio";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Shared studio status query key ───────────────────────────────────────────
const STUDIO_STATUS_KEY = ["studio", "status"] as const;

type TtsStrategy = { name: string; key: string; available: boolean; latency_ms: number | null };
type ImgBackend = { name: string; url: string; online: boolean };
type StudioStatus = {
  tts: {
    available: boolean;
    best_strategy: string | null;
    /** True only when the Kokoro ONNX model is loaded in memory (neural quality). */
    kokoro_loaded: boolean;
    /** True when the kokoro_onnx Python package is installed (may still be model-missing). */
    kokoro_pkg_installed: boolean;
    strategies: TtsStrategy[];
    /**
     * Engine that will be used for voice *sample* synthesis.
     * "kokoro_onnx" = neural | "espeak_ng" = basic | null = no local sample engine (503).
     * NOTE: AI Server is NOT a valid fallback for the sample route — this field
     * reflects only local engine availability, independent of `best_strategy`.
     */
    sample_engine: "kokoro_onnx" | "espeak_ng" | null;
    sample_available: boolean;
  };
  image_gen: { available: boolean; backends: ImgBackend[] };
  ocr: { available: boolean; engine: string | null; missing: string[] };
  last_checked: string;
};

function useStudioStatus() {
  return useQuery<StudioStatus>({
    queryKey: STUDIO_STATUS_KEY,
    queryFn: () => apiFetch(`${BASE}/studio/status`).then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: 1,
  });
}

// ── Service status bar ─────────────────────────────────────────────────────────

function StatusPill({ label, available, detail, note, warning }: {
  label: string;
  available: boolean;
  detail?: string;
  note?: string;
  /** When true, shows an amber pill even though `available` is true — used to
   *  signal degraded-but-functional state (e.g. espeak fallback active). */
  warning?: boolean;
}) {
  const color = (!available)
    ? "border-amber-200 text-amber-700 bg-amber-50/60"
    : warning
      ? "border-amber-200 text-amber-700 bg-amber-50/60"
      : "border-emerald-200 text-emerald-700 bg-emerald-50/60";
  const dot = (!available)
    ? "bg-amber-400 animate-pulse"
    : warning
      ? "bg-amber-400 animate-pulse"
      : "bg-emerald-500";
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-1 rounded-full border ${color}`}
      title={note}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
      <span className="font-semibold">{label}</span>
      {detail && <span className="opacity-70">— {detail}</span>}
    </span>
  );
}

function ServiceStatusBar() {
  const { data, isLoading, isError, refetch, isFetching } = useStudioStatus();

  return (
    <Card className="border-border/50 bg-muted/20">
      <CardContent className="py-3 px-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground shrink-0">
            <Activity className="w-3.5 h-3.5" />
            Services
          </div>

          {isLoading && (
            <div className="flex gap-2">
              {["TTS", "Image", "OCR"].map(s => (
                <Skeleton key={s} className="h-6 w-24 rounded-full" />
              ))}
            </div>
          )}

          {isError && (
            <span className="text-[11px] font-mono text-destructive">
              Could not reach API server
            </span>
          )}

          {data && (
            <div className="flex flex-wrap items-center gap-2">
              {/* General TTS — reflects overall synthesis availability (AI Server, Kokoro, espeak) */}
              <StatusPill
                label="TTS"
                available={data.tts.available}
                detail={data.tts.available ? data.tts.best_strategy ?? undefined : "no backend"}
                note={
                  data.tts.available
                    ? `Strategies available: ${data.tts.strategies.filter(s => s.available).map(s => s.name).join(", ")}`
                    : "All TTS strategies offline — check AI server, Kokoro ONNX, and espeak-ng"
                }
              />

              {/* Voice samples — separate from general TTS; only Kokoro or espeak can generate catalog samples */}
              <StatusPill
                label="Samples"
                available={data.tts.sample_available}
                warning={data.tts.sample_available && data.tts.sample_engine !== "kokoro_onnx"}
                detail={
                  !data.tts.sample_available
                    ? "unavailable"
                    : data.tts.sample_engine === "kokoro_onnx"
                      ? "Kokoro"
                      : "espeak (basic)"
                }
                note={
                  !data.tts.sample_available
                    ? "No local TTS engine can generate voice samples — install Kokoro ONNX or espeak-ng. Voice sample previews will return 503."
                    : data.tts.sample_engine === "kokoro_onnx"
                      ? "Kokoro ONNX is loaded — voice catalog samples use neural synthesis (premium quality)"
                      : data.tts.kokoro_pkg_installed
                        ? "Kokoro package installed but model not yet loaded — samples fall back to espeak (basic quality) until Kokoro initializes"
                        : "Kokoro ONNX not installed — samples use espeak (basic quality). Install kokoro-onnx for premium neural voices."
                }
              />

              {/* Image gen */}
              <StatusPill
                label="Image"
                available={data.image_gen.available}
                detail={
                  data.image_gen.available
                    ? (data.image_gen.backends.find(b => b.online)?.name ?? undefined)
                    : "no backend"
                }
                note={
                  data.image_gen.available
                    ? `Online: ${data.image_gen.backends.filter(b => b.online).map(b => b.name).join(", ")}`
                    : "No image backend reachable — install Automatic1111 or ComfyUI, or set a custom URL in System Settings"
                }
              />

              {/* OCR */}
              <StatusPill
                label="OCR"
                available={data.ocr.available}
                detail={data.ocr.available ? (data.ocr.engine ?? undefined) : "unavailable"}
                note={
                  data.ocr.available
                    ? "Tesseract OCR ready"
                    : `Missing: ${data.ocr.missing.join(", ")}`
                }
              />
            </div>
          )}

          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="ml-auto inline-flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
            title="Refresh service status"
          >
            <RefreshCw className={`w-3 h-3 ${isFetching ? "animate-spin" : ""}`} />
            {data && (
              <span className="hidden sm:inline">
                {new Date(data.last_checked).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            )}
          </button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── TTS panel ─────────────────────────────────────────────────────────────────

function TTSPanel() {
  const { data: voicesResp, isLoading: loadingVoices, isError: voicesError, refetch: refetchVoices } = useListVoices();
  const voices = voicesResp?.voices ?? [];

  const [text, setText] = useState("");
  const [voiceId, setVoiceId] = useState("af_heart");
  const [speed, setSpeed] = useState(1.0);
  const [loading, setLoading] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  async function handleSynthesize() {
    if (!text.trim()) return;
    setLoading(true);
    setAudioUrl(null);
    setPlaying(false);

    try {
      const resp = await apiFetch(`${BASE}/studio/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim(), voice: voiceId, speed }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        // detail may be a structured 503 object { detail, service, reason } or a plain string
        const raw = (err as any).detail;
        const msg = typeof raw === "object" && raw !== null
          ? (raw.reason ?? raw.detail ?? JSON.stringify(raw))
          : (raw ?? `HTTP ${resp.status}`);
        throw new Error(msg);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      setAudioUrl(url);
      // Do NOT autoplay — iOS Safari blocks audio from async code.
      // The audio player below has native controls; user taps play.
    } catch (e: any) {
      toast.error(`TTS failed: ${e.message}`, { duration: 8000 });
    } finally {
      setLoading(false);
    }
  }

  function togglePlay() {
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
      setPlaying(false);
    } else {
      el.play().catch(() => {});
      setPlaying(true);
    }
  }

  function handleDownload() {
    if (!audioUrl) return;
    const a = document.createElement("a");
    a.href = audioUrl;
    a.download = "speech.mp3";
    a.click();
  }

  const charCount = text.length;
  const overLimit = charCount > 10_000;

  const { data: studioStatus } = useStudioStatus();
  const ttsStrategies = studioStatus?.tts.strategies ?? [];
  const ttsAvailable = studioStatus?.tts.available ?? true; // optimistic until loaded

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 font-serif text-lg">
          <Volume2 className="w-5 h-5 text-muted-foreground" />
          Text to Speech
        </CardTitle>
        {/* TTS strategy status pills */}
        {ttsStrategies.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {ttsStrategies.map((s) => (
              <span key={s.key}
                className={`inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full border ${
                  s.available
                    ? "border-emerald-200 text-emerald-700 bg-emerald-50/60"
                    : "border-border/40 text-muted-foreground/50"
                }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${s.available ? "bg-emerald-500" : "bg-muted-foreground/30"}`} />
                {s.name}
                {s.available && s.latency_ms != null && (
                  <span className="opacity-60">{s.latency_ms}ms</span>
                )}
              </span>
            ))}
            {!ttsAvailable && ttsStrategies.length > 0 && (
              <span className="text-[10px] font-mono text-amber-600">
                No TTS backend available — check System Settings
              </span>
            )}
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-4">

        {/* Text input */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs font-mono uppercase text-muted-foreground">Text</label>
            <span className={`text-xs font-mono ${overLimit ? "text-destructive" : "text-muted-foreground"}`}>
              {charCount.toLocaleString()} / 10,000
            </span>
          </div>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Enter text to synthesize…"
            className="min-h-32 resize-y font-serif text-sm"
          />
        </div>

        {/* Voice + speed row */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-xs font-mono uppercase text-muted-foreground">Voice</label>
            {loadingVoices ? (
              <Skeleton className="h-9 w-full" />
            ) : voicesError ? (
              <button
                onClick={() => refetchVoices()}
                className="h-9 w-full text-xs font-mono text-red-600 bg-red-50 border border-red-200 rounded-md px-3 flex items-center gap-2 hover:bg-red-100 transition-colors"
              >
                <span>⚠</span> Could not load voices — click to retry
              </button>
            ) : (
              <Select value={voiceId} onValueChange={setVoiceId}>
                <SelectTrigger className="text-sm">
                  <SelectValue placeholder={voices.length === 0 ? "No voices configured" : "Select voice"} />
                </SelectTrigger>
                <SelectContent>
                  {voices.map((v) => (
                    <SelectItem key={v.id} value={v.id!} className="text-sm">
                      {v.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="space-y-2">
            <label className="text-xs font-mono uppercase text-muted-foreground">
              Speed — {speed.toFixed(1)}×
            </label>
            <div className="px-1 pt-1">
              <Slider
                min={0.5} max={2.0} step={0.1}
                value={[speed]}
                onValueChange={([v]) => setSpeed(v)}
                className="w-full"
              />
            </div>
            <div className="flex justify-between text-[10px] font-mono text-muted-foreground px-1">
              <span>0.5×</span><span>1.0×</span><span>2.0×</span>
            </div>
          </div>
        </div>

        {/* Synthesize button */}
        <Button
          onClick={handleSynthesize}
          disabled={!text.trim() || loading || overLimit || voicesError || voices.length === 0}
          className="w-full gap-2"
        >
          {loading ? (
            <><Loader2 className="w-4 h-4 animate-spin" /> Synthesizing…</>
          ) : (
            <><Mic className="w-4 h-4" /> Synthesize</>
          )}
        </Button>

        {/* Audio player */}
        {audioUrl && (
          <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border/50">
            <Button
              size="icon"
              variant="secondary"
              className="h-9 w-9 rounded-full shrink-0"
              onClick={togglePlay}
            >
              {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </Button>
            <audio
              ref={audioRef}
              src={audioUrl}
              onEnded={() => setPlaying(false)}
              onPause={() => setPlaying(false)}
              onPlay={() => setPlaying(true)}
              className="flex-1 h-8"
              controls
              style={{ minWidth: 0 }}
            />
            <Button size="icon" variant="ghost" className="shrink-0" onClick={handleDownload}>
              <Download className="w-4 h-4" />
            </Button>
          </div>
        )}

      </CardContent>
    </Card>
  );
}

// ── Image generation panel ─────────────────────────────────────────────────────

// ── Audiobook panel ───────────────────────────────────────────────────────────

function AudiobookPanel() {
  const { data: voicesResp, isLoading: loadingVoices, isError: voicesError, refetch: refetchVoices } = useListVoices();
  const voices = voicesResp?.voices ?? [];

  const { data: libResp, isLoading: loadingDocs } = useListLibrary(
    { readiness: "ready" } as any,
    { query: { staleTime: 20_000 } } as any,
  );
  const docs = ((libResp as any)?.documents ?? []) as any[];

  const [docId, setDocId]   = useState<string>("");
  const [voiceId, setVoiceId] = useState("af_heart");
  const [speed, setSpeed]   = useState(1.0);
  const [loading, setLoading] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [audioName, setAudioName] = useState("audiobook.mp3");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);

  // Async document-TTS job state
  const [abJobId, setAbJobId]       = useState<string | null>(null);
  const [abSegsDone, setAbSegsDone] = useState(0);
  const [abSegsTotal, setAbSegsTotal] = useState(0);
  const abJobIdRef = useRef<string | null>(null);
  const abPollRef  = useRef<ReturnType<typeof setInterval> | null>(null);

  const selectedDoc = docs.find((d: any) => d.id === docId);

  // Cancel any in-flight job on unmount.
  useEffect(() => {
    return () => {
      if (abPollRef.current) { clearInterval(abPollRef.current); abPollRef.current = null; }
      if (abJobIdRef.current) {
        apiFetch(`${BASE}/studio/tts/document/${abJobIdRef.current}`, { method: "DELETE" }).catch(() => {});
        abJobIdRef.current = null;
      }
    };
  }, []);

  async function handleGenerate() {
    if (!docId) return;
    setLoading(true);
    setAudioUrl(null);
    setPlaying(false);
    const toastId = toast.loading("Starting audiobook generation…");
    try {
      const resp = await apiFetch(`${BASE}/studio/tts/document`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: docId, voice: voiceId, speed }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const { job_id, total_segments } = await resp.json();
      toast.dismiss(toastId);
      abJobIdRef.current = job_id;
      setAbJobId(job_id);
      setAbSegsTotal(total_segments);
      setAbSegsDone(0);
      // loading stays true while polling
      abPollRef.current = setInterval(async () => {
        try {
          const sr = await apiFetch(`${BASE}/studio/tts/document/${job_id}/status`);
          if (!sr.ok) {
            if (sr.status === 404) {
              clearInterval(abPollRef.current!); abPollRef.current = null;
              abJobIdRef.current = null; setAbJobId(null); setLoading(false);
              toast.error("Server restarted — audiobook job was lost. Please try again.");
            }
            return;
          }
          const status = await sr.json();
          setAbSegsDone(status.segments_done ?? 0);
          const terminal = ["done", "failed", "cancelled"].includes(status.state);
          if (terminal) {
            clearInterval(abPollRef.current!); abPollRef.current = null;
            abJobIdRef.current = null; setAbJobId(null); setLoading(false);
            if (status.state === "done") {
              const serveUrl = `${BASE}/studio/outputs/serve?path=${encodeURIComponent(status.mp3_path)}`;
              setAudioUrl(serveUrl);
              setAudioName(`${selectedDoc?.title || "audiobook"}.mp3`);
              toast.success("Audiobook ready — tap play below");
            } else if (status.state === "failed") {
              toast.error(`Audiobook failed: ${status.error ?? "unknown error"}`, { duration: 10_000 });
            } else {
              toast("Audiobook generation cancelled.");
            }
          }
        } catch { /* transient poll errors */ }
      }, 2000);
    } catch (e: any) {
      toast.error(`Audiobook failed: ${e.message}`, { id: toastId, duration: 10_000 });
      setLoading(false);
    }
  }

  async function handleCancelGenerate() {
    if (!abJobIdRef.current) return;
    try {
      await apiFetch(`${BASE}/studio/tts/document/${abJobIdRef.current}`, { method: "DELETE" });
    } catch { /* best-effort */ }
  }

  function togglePlay() {
    const el = audioRef.current;
    if (!el) return;
    if (playing) { el.pause(); setPlaying(false); }
    else { el.play().catch(() => {}); setPlaying(true); }
  }

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 font-serif text-lg">
          <BookHeadphones className="w-5 h-5 text-muted-foreground" />
          Audiobook from Document
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Select a processed document from your Library — the full text will be synthesised
          into an MP3 you can play or download. Large books may take a few minutes.
        </p>

        {/* Document picker */}
        <div className="space-y-1">
          <label className="text-xs font-mono uppercase text-muted-foreground">Document</label>
          {loadingDocs ? <Skeleton className="h-9 w-full" /> : (
            <Select value={docId} onValueChange={setDocId}>
              <SelectTrigger className="text-sm">
                <SelectValue placeholder="Choose a document from your Library…" />
              </SelectTrigger>
              <SelectContent>
                {docs.length === 0 ? (
                  <div className="px-3 py-4 text-xs text-muted-foreground text-center">
                    No processed documents found. Import files in the Library first.
                  </div>
                ) : docs.map((d: any) => (
                  <SelectItem key={d.id} value={d.id} className="text-sm">
                    <span className="flex items-center gap-2">
                      <FileText className="w-3 h-3 shrink-0 text-muted-foreground" />
                      <span className="truncate max-w-[260px]">
                        {d.title || d.source?.split("/").pop() || d.id}
                      </span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        {/* Voice + speed */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-xs font-mono uppercase text-muted-foreground">Voice</label>
            {loadingVoices ? <Skeleton className="h-9 w-full" /> : voicesError ? (
              <button
                onClick={() => refetchVoices()}
                className="h-9 w-full text-xs font-mono text-red-600 bg-red-50 border border-red-200 rounded-md px-3 flex items-center gap-2 hover:bg-red-100 transition-colors"
              >
                <span>⚠</span> Could not load voices — click to retry
              </button>
            ) : (
              <Select value={voiceId} onValueChange={setVoiceId}>
                <SelectTrigger className="text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {voices.map((v: any) => (
                    <SelectItem key={v.id} value={v.id} className="text-sm">{v.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="space-y-2">
            <label className="text-xs font-mono uppercase text-muted-foreground">
              Speed — {speed.toFixed(1)}×
            </label>
            <div className="px-1 pt-1">
              <Slider min={0.5} max={2.0} step={0.1} value={[speed]}
                onValueChange={([v]) => setSpeed(v)} className="w-full" />
            </div>
            <div className="flex justify-between text-[10px] font-mono text-muted-foreground px-1">
              <span>0.5×</span><span>1.0×</span><span>2.0×</span>
            </div>
          </div>
        </div>

        {abJobId ? (
          <div className="flex items-center gap-3">
            <div className="flex-1 space-y-1.5">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Loader2 className="w-3 h-3 animate-spin" /> Generating audiobook…
                </span>
                <span>{abSegsDone}/{abSegsTotal}</span>
              </div>
              <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all duration-500"
                  style={{ width: abSegsTotal > 0 ? `${Math.round((abSegsDone / abSegsTotal) * 100)}%` : "0%" }}
                />
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleCancelGenerate}
              className="shrink-0 h-8 text-destructive border-destructive/40 hover:bg-destructive/10"
            >
              <X className="w-3.5 h-3.5 mr-1" /> Cancel
            </Button>
          </div>
        ) : (
          <Button onClick={handleGenerate} disabled={!docId || loading || voicesError || voices.length === 0} className="w-full gap-2">
            {loading
              ? <><Loader2 className="w-4 h-4 animate-spin" />Converting to audio… (may take a few minutes)</>
              : <><BookHeadphones className="w-4 h-4" />Generate Audiobook</>}
          </Button>
        )}

        {audioUrl && (
          <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border/50">
            <Button size="icon" variant="secondary" className="h-9 w-9 rounded-full shrink-0"
              onClick={togglePlay}>
              {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </Button>
            <audio ref={audioRef} src={audioUrl} onEnded={() => setPlaying(false)}
              onPause={() => setPlaying(false)} onPlay={() => setPlaying(true)}
              className="flex-1 h-8" controls style={{ minWidth: 0 }} />
            <Button size="icon" variant="ghost" className="shrink-0"
              onClick={() => { const a = document.createElement("a"); a.href = audioUrl; a.download = audioName; a.click(); }}>
              <Download className="w-4 h-4" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}


function ImageGenPanel() {
  const [prompt, setPrompt] = useState("");
  const [negPrompt, setNegPrompt] = useState("");
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(512);
  const [steps, setSteps] = useState(20);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  // Poll which image backends are reachable
  const { data: imgStatus } = useQuery({
    queryKey: ["studio", "image-status"],
    queryFn: () => apiFetch(`${BASE}/studio/image-status`).then(r => r.json()),
    staleTime: 15_000, refetchInterval: 30_000,
  });
  const backends: { name: string; online: boolean }[] = imgStatus?.backends ?? [];
  const anyOnline = imgStatus?.any_online ?? false;

  async function handleGenerate() {
    if (!prompt.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const resp = await apiFetch(`${BASE}/studio/image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: prompt.trim(),
          negative_prompt: negPrompt.trim(),
          width, height, steps,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const item = data?.data?.[0];
      const url = item?.url ?? (item?.b64_json
        ? `data:image/png;base64,${item.b64_json}` : null);
      if (!url) throw new Error("No image in response");
      setResult(url);
    } catch (e: any) {
      toast.error(`Image generation failed: ${e.message}`, { duration: 10000 });
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 font-serif text-lg">
          <ImageIcon className="w-5 h-5 text-muted-foreground" />
          Image Generation
        </CardTitle>
        {/* Backend status pills */}
        <div className="flex flex-wrap gap-1.5 pt-1">
          {backends.map((b) => (
            <span key={b.name}
              className={`inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full border ${
                b.online
                  ? "border-emerald-200 text-emerald-700 bg-emerald-50/60"
                  : "border-border/40 text-muted-foreground/50"
              }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${b.online ? "bg-emerald-500" : "bg-muted-foreground/30"}`} />
              {b.name}
            </span>
          ))}
          {!anyOnline && backends.length > 0 && (
            <span className="text-[10px] font-mono text-amber-600">
              No image backend online — install Automatic1111 or ComfyUI, or set a custom URL in System Settings
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1">
          <label className="text-xs font-mono uppercase text-muted-foreground">Prompt</label>
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe the image to generate…"
            className="min-h-24 resize-y font-serif text-sm"
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs font-mono uppercase text-muted-foreground">Negative Prompt <span className="text-muted-foreground/50">(optional)</span></label>
          <Textarea
            value={negPrompt}
            onChange={(e) => setNegPrompt(e.target.value)}
            placeholder="What to avoid…"
            className="min-h-12 resize-y font-serif text-sm"
          />
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1">
            <label className="text-xs font-mono uppercase text-muted-foreground">Width</label>
            <Select value={String(width)} onValueChange={(v) => setWidth(Number(v))}>
              <SelectTrigger className="text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {[256, 512, 768, 1024].map(s => (
                  <SelectItem key={s} value={String(s)}>{s}px</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-mono uppercase text-muted-foreground">Height</label>
            <Select value={String(height)} onValueChange={(v) => setHeight(Number(v))}>
              <SelectTrigger className="text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {[256, 512, 768, 1024].map(s => (
                  <SelectItem key={s} value={String(s)}>{s}px</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-mono uppercase text-muted-foreground">Steps</label>
            <Select value={String(steps)} onValueChange={(v) => setSteps(Number(v))}>
              <SelectTrigger className="text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {[10, 20, 30, 50].map(s => (
                  <SelectItem key={s} value={String(s)}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <Button
          onClick={handleGenerate}
          disabled={!prompt.trim() || loading}
          className="w-full gap-2"
        >
          {loading ? (
            <><Loader2 className="w-4 h-4 animate-spin" /> Generating…</>
          ) : (
            <><ImageIcon className="w-4 h-4" /> Generate Image</>
          )}
        </Button>

        {result && (
          <div className="rounded-lg overflow-hidden border border-border/50">
            <img src={result} alt="Generated" className="w-full object-contain max-h-96" />
            <div className="flex justify-end p-2 border-t border-border/50">
              <Button size="sm" variant="ghost" className="gap-2 text-xs"
                onClick={() => {
                  const a = document.createElement("a");
                  a.href = result;
                  a.download = "generated.png";
                  a.click();
                }}>
                <Download className="w-3.5 h-3.5" /> Download
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Outputs gallery ────────────────────────────────────────────────────────────

function OutputsGallery() {
  const qc = useQueryClient();
  const { data: outputsResp, isLoading } = useListStudioOutputs(
    { query: { refetchInterval: 15_000 } } as any
  );
  const outputs: any[] = outputsResp?.outputs ?? [];

  const [lightbox, setLightbox] = useState<string | null>(null);
  const [lightboxName, setLightboxName] = useState("");
  const [playing, setPlaying] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  // Real DOM audio element — required for iOS Safari autoplay policy
  const audioElRef = useRef<HTMLAudioElement | null>(null);

  function serveUrl(path: string) {
    return `${BASE}/studio/outputs/serve?path=${encodeURIComponent(path)}`;
  }

  function handlePlay(out: any) {
    const el = audioElRef.current;
    if (!el) return;
    if (playing === out.path) {
      el.pause();
      setPlaying(null);
      return;
    }
    el.pause();
    el.src = serveUrl(out.path);
    el.load();
    el.play().catch(() => toast.error("Could not play — tap again or download"));
    setPlaying(out.path);
  }

  function handleDownload(out: any) {
    // Use an anchor with download attr — works on desktop and iOS share sheet
    const a = document.createElement("a");
    a.href = serveUrl(out.path);
    a.download = out.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async function handleArchive(out: any, e: React.MouseEvent) {
    e.stopPropagation();
    if (playing === out.path) { audioElRef.current?.pause(); setPlaying(null); }
    try {
      const r = await apiFetch(`${BASE}/studio/outputs/archive?path=${encodeURIComponent(out.path)}`, { method: "DELETE" });
      if (!r.ok) throw new Error();
      qc.invalidateQueries({ queryKey: ["listStudioOutputs"] });
    } catch {
      toast.error("Could not remove output");
    }
  }

  async function handleClearAll() {
    if (!confirm(`Delete all ${outputs.length} outputs? This cannot be undone.`)) return;
    setClearing(true);
    audioElRef.current?.pause();
    setPlaying(null);
    try {
      await Promise.all(
        outputs.map(o =>
          apiFetch(`${BASE}/studio/outputs/archive?path=${encodeURIComponent(o.path)}`, { method: "DELETE" }).catch(() => null)
        )
      );
      qc.invalidateQueries({ queryKey: ["listStudioOutputs"] });
      toast.success("All outputs cleared");
    } catch {
      toast.error("Some outputs could not be removed");
    } finally {
      setClearing(false);
    }
  }

  function fmtSize(b: number) {
    return b >= 1_048_576 ? `${(b / 1_048_576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`;
  }

  const images = outputs.filter(o => o.kind === "image");
  const others = outputs.filter(o => o.kind !== "image");

  return (
    <>
      {/* Hidden DOM audio element — iOS Safari requires a real element, not new Audio() */}
      <audio
        ref={audioElRef}
        onEnded={() => setPlaying(null)}
        onError={() => { toast.error("Playback error"); setPlaying(null); }}
        className="hidden"
      />

      {/* Image lightbox */}
      {lightbox && (
        <div
          className="fixed inset-0 z-50 bg-black/85 flex items-center justify-center p-4"
          onClick={() => setLightbox(null)}
        >
          <div className="relative max-w-4xl w-full max-h-full" onClick={e => e.stopPropagation()}>
            <img src={lightbox} alt={lightboxName} className="max-h-[80vh] max-w-full mx-auto rounded-lg shadow-2xl object-contain" />
            <div className="absolute top-3 right-3 flex gap-2">
              <a
                href={lightbox}
                download={lightboxName}
                className="rounded-full bg-black/60 text-white p-2 hover:bg-black/80 transition-colors flex items-center justify-center"
                onClick={e => e.stopPropagation()}
              >
                <Download className="w-4 h-4" />
              </a>
              <button className="rounded-full bg-black/60 text-white p-2 hover:bg-black/80 transition-colors" onClick={() => setLightbox(null)}>
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

      <Card className="border-border/50">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2 font-serif text-lg">
              <Video className="w-5 h-5 text-muted-foreground" />
              Recent Outputs
              {outputs.length > 0 && (
                <Badge variant="secondary" className="text-[10px] font-mono ml-1">{outputs.length}</Badge>
              )}
            </CardTitle>
            {outputs.length > 0 && (
              <Button variant="outline" size="sm" className="gap-1.5 text-xs text-destructive border-destructive/30 hover:bg-destructive/5" onClick={handleClearAll} disabled={clearing}>
                <Trash2 className="w-3.5 h-3.5" />
                {clearing ? "Clearing…" : "Clear All"}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="grid sm:grid-cols-2 gap-3">
              {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24 w-full" />)}
            </div>
          ) : outputs.length === 0 ? (
            <div className="py-10 text-center border border-dashed rounded-lg bg-muted/5">
              <p className="text-sm text-muted-foreground">
                No outputs yet — synthesize speech or generate an image above.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Image grid */}
              {images.length > 0 && (
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                  {images.map((out: any) => (
                    <div
                      key={out.path}
                      className="relative group rounded-lg overflow-hidden border border-border/50 bg-muted/10 aspect-square cursor-pointer"
                      onClick={() => { setLightbox(serveUrl(out.path)); setLightboxName(out.name); }}
                    >
                      <img src={serveUrl(out.path)} alt={out.name} className="w-full h-full object-cover" loading="lazy" />
                      {/* Always-visible controls on mobile, hover on desktop */}
                      <div className="absolute top-1.5 right-1.5 flex gap-1 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                        <a
                          href={serveUrl(out.path)}
                          download={out.name}
                          className="rounded bg-black/50 hover:bg-black/70 p-1.5 text-white"
                          onClick={e => e.stopPropagation()}
                        >
                          <Download className="w-3 h-3" />
                        </a>
                        <button
                          className="rounded bg-black/50 hover:bg-red-600/80 p-1.5 text-white"
                          onClick={e => handleArchive(out, e)}
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Audio + other list */}
              {others.map((out: any) => (
                <div
                  key={out.path}
                  className="flex items-center gap-3 p-3 rounded-lg border border-border/50 bg-muted/10"
                >
                  {out.kind === "audio" ? (
                    <button
                      className="w-10 h-10 rounded-full bg-primary/10 active:bg-primary/30 flex items-center justify-center transition-colors shrink-0 touch-manipulation"
                      onClick={() => handlePlay(out)}
                    >
                      {playing === out.path
                        ? <Pause className="w-4 h-4 text-primary" />
                        : <Play className="w-4 h-4 text-primary ml-0.5" />}
                    </button>
                  ) : (
                    <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center shrink-0">
                      <Video className="w-4 h-4 text-muted-foreground" />
                    </div>
                  )}

                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium truncate">{out.name}</p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <Badge variant="outline" className="text-[9px] font-mono uppercase">{out.kind}</Badge>
                      <span className="text-[10px] font-mono text-muted-foreground">{fmtSize(out.size_bytes)}</span>
                      {playing === out.path && (
                        <span className="text-[10px] font-mono text-primary animate-pulse">▶ playing</span>
                      )}
                    </div>
                  </div>

                  {/* Controls — always visible on mobile */}
                  <div className="flex items-center gap-1 shrink-0">
                    <a
                      href={serveUrl(out.path)}
                      download={out.name}
                      className="inline-flex items-center justify-center h-8 w-8 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                      title="Download"
                      onClick={e => e.stopPropagation()}
                    >
                      <Download className="w-3.5 h-3.5" />
                    </a>
                    <button
                      className="inline-flex items-center justify-center h-8 w-8 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                      title="Remove"
                      onClick={e => handleArchive(out, e)}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}

// ── Document Workshop ─────────────────────────────────────────────────────────

type WsQuestion = { id: string; question: string; type: string; options?: string[]; hint?: string };
type WsSession = {
  id: string; request: string; format: string;
  detected_intent: string; questions: WsQuestion[];
};
type WsCritique = {
  scores?: Record<string, number>; overall?: number;
  strengths?: string[]; gaps?: string[]; suggestions?: string[]; verdict?: string;
};
type WsResult = {
  ok: boolean; doc_id?: string; filename?: string;
  download_url?: string; size_bytes?: number; critique?: WsCritique; error?: string;
};

const FORMAT_ICONS: Record<string, React.ReactNode> = {
  xlsx: <FileSpreadsheet className="w-4 h-4" />,
  docx: <FileText className="w-4 h-4" />,
  pdf:  <FileText className="w-4 h-4" />,
  pptx: <Presentation className="w-4 h-4" />,
};

const FORMAT_LABELS: Record<string, string> = {
  xlsx: "Excel Workbook",
  docx: "Word Document",
  pdf:  "PDF Report",
  pptx: "PowerPoint",
};

function ScoreBadge({ label, value }: { label: string; value: number }) {
  const color = value >= 8
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : value >= 6
    ? "bg-amber-50 text-amber-700 border-amber-200"
    : "bg-red-50 text-red-700 border-red-200";
  return (
    <span className={`inline-flex gap-1 items-center text-[11px] font-mono px-2 py-0.5 rounded-full border ${color}`}>
      <span className="font-semibold">{value}/10</span>
      <span className="opacity-70">{label}</span>
    </span>
  );
}

function DocumentWorkshopPanel() {
  const { data: worksResp } = useListWorks({});
  const works: any[] = (worksResp as any)?.works ?? [];

  const [step, setStep] = useState<"request" | "questions" | "generating" | "result">("request");
  const [request, setRequest] = useState("");
  const [format, setFormat] = useState("docx");
  const [workId, setWorkId] = useState("__none__");
  const [planning, setPlanning] = useState(false);
  const [session, setSession] = useState<WsSession | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<WsResult | null>(null);

  async function handlePlan() {
    if (!request.trim()) return;
    setPlanning(true);
    try {
      const resp = await apiFetch(`${BASE}/generate/workshop/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request: request.trim(),
          format,
          work_id: workId === "__none__" ? null : workId,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail || "Planning failed");
      }
      const data: WsSession = await resp.json();
      setSession(data);
      // Pre-fill detected format
      if (data.format && data.format !== format) setFormat(data.format);
      setAnswers({});
      setStep("questions");
    } catch (e: any) {
      toast.error(`Planner failed: ${e.message}`, { duration: 8000 });
    } finally {
      setPlanning(false);
    }
  }

  async function handleGenerate() {
    setStep("generating");
    setResult(null);
    try {
      const resp = await apiFetch(`${BASE}/generate/workshop/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session?.id ?? null,
          request: request.trim(),
          format,
          work_id: workId === "__none__" ? null : workId,
          answers,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail || "Generation failed");
      }
      const data: WsResult = await resp.json();
      setResult(data);
      setStep("result");
      toast.success("Document ready — download below");
    } catch (e: any) {
      toast.error(`Generation failed: ${e.message}`, { duration: 10_000 });
      setStep("questions");
    }
  }

  function reset() {
    setStep("request");
    setSession(null);
    setAnswers({});
    setResult(null);
  }

  const critique = result?.critique;
  const overallScore = critique?.overall ?? null;

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 font-serif text-lg">
            <Wand2 className="w-5 h-5 text-violet-500" />
            Document Workshop
            <Badge variant="secondary" className="text-[10px] font-mono">AI</Badge>
          </CardTitle>
          {step !== "request" && (
            <Button variant="ghost" size="sm" onClick={reset} className="text-xs gap-1.5">
              <RefreshCw className="w-3 h-3" /> Start Over
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Describe what you need — the AI asks targeted questions, writes code, executes it,
          and critiques its own output before delivering the file.
        </p>
      </CardHeader>

      <CardContent className="space-y-4">

        {/* ── Step 1: Request ─────────────────────────────────────────── */}
        {step === "request" && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold">What do you need?</Label>
              <Textarea
                value={request}
                onChange={e => setRequest(e.target.value)}
                placeholder="e.g. A PowerPoint presentation on Moses' leadership with timeline and charts, formal tone, 8 slides…"
                className="min-h-[90px] text-sm resize-none"
              />
            </div>

            <div className="grid sm:grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs font-semibold">Format</Label>
                <Select value={format} onValueChange={setFormat}>
                  <SelectTrigger className="h-9 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(FORMAT_LABELS).map(([k, v]) => (
                      <SelectItem key={k} value={k}>
                        <span className="flex items-center gap-2">{FORMAT_ICONS[k]}{v}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs font-semibold">Link to Work <span className="text-muted-foreground font-normal">(optional)</span></Label>
                <Select value={workId} onValueChange={setWorkId}>
                  <SelectTrigger className="h-9 text-sm">
                    <SelectValue placeholder="No work" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">No work</SelectItem>
                    {works.map((w: any) => (
                      <SelectItem key={w.id} value={w.id}>{w.title}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <Button
              onClick={handlePlan}
              disabled={!request.trim() || planning}
              className="w-full gap-2"
            >
              {planning
                ? <><Loader2 className="w-4 h-4 animate-spin" /> Planning…</>
                : <><Sparkles className="w-4 h-4" /> Plan Document</>}
            </Button>
            <p className="text-[11px] text-muted-foreground text-center">
              The AI will ask a few targeted questions before generating.
            </p>
          </div>
        )}

        {/* ── Step 2: Clarifying questions ────────────────────────────── */}
        {step === "questions" && session && (
          <div className="space-y-5">
            {/* Intent summary */}
            <div className="rounded-lg border border-violet-200 bg-violet-50/60 px-4 py-3 space-y-1">
              <p className="text-xs font-semibold text-violet-700 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5" /> Understood intent
              </p>
              <p className="text-sm text-violet-900">{session.detected_intent}</p>
              <div className="flex items-center gap-2 mt-1">
                <span className="flex items-center gap-1 text-[11px] text-violet-600">
                  {FORMAT_ICONS[session.format]}{FORMAT_LABELS[session.format] ?? session.format}
                </span>
              </div>
            </div>

            {/* Questions */}
            <div className="space-y-4">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                Clarifying questions
              </p>
              {session.questions.map((q, i) => (
                <div key={q.id} className="space-y-1.5">
                  <Label className="text-sm leading-snug">
                    <span className="text-muted-foreground font-mono mr-1">{i + 1}.</span>
                    {q.question}
                  </Label>
                  {q.hint && (
                    <p className="text-[11px] text-muted-foreground pl-4">{q.hint}</p>
                  )}
                  {q.type === "choice" && q.options?.length ? (
                    <Select
                      value={answers[q.id] ?? ""}
                      onValueChange={v => setAnswers(a => ({ ...a, [q.id]: v }))}
                    >
                      <SelectTrigger className="h-9 text-sm">
                        <SelectValue placeholder="Choose…" />
                      </SelectTrigger>
                      <SelectContent>
                        {q.options.map(o => (
                          <SelectItem key={o} value={o}>{o}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : q.type === "multiselect" && q.options?.length ? (
                    <div className="flex flex-wrap gap-2 pl-4">
                      {q.options.map(o => {
                        const sel = (answers[q.id] ?? "").split(",").filter(Boolean);
                        const active = sel.includes(o);
                        return (
                          <button
                            key={o}
                            onClick={() => {
                              const next = active ? sel.filter(x => x !== o) : [...sel, o];
                              setAnswers(a => ({ ...a, [q.id]: next.join(",") }));
                            }}
                            className={`text-xs px-3 py-1 rounded-full border transition-colors
                              ${active
                                ? "border-violet-400 bg-violet-100 text-violet-800"
                                : "border-border text-muted-foreground hover:border-violet-300"
                              }`}
                          >
                            {o}
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <Textarea
                      value={answers[q.id] ?? ""}
                      onChange={e => setAnswers(a => ({ ...a, [q.id]: e.target.value }))}
                      placeholder="Your answer…"
                      className="text-sm min-h-[60px] resize-none"
                    />
                  )}
                </div>
              ))}
            </div>

            <Button onClick={handleGenerate} className="w-full gap-2 bg-violet-600 hover:bg-violet-700">
              <Wand2 className="w-4 h-4" /> Generate Document
            </Button>
            <p className="text-[11px] text-muted-foreground text-center">
              Unanswered questions are fine — the AI will make sensible choices.
            </p>
          </div>
        )}

        {/* ── Step 3: Generating ──────────────────────────────────────── */}
        {step === "generating" && (
          <div className="py-10 text-center space-y-4">
            <div className="w-14 h-14 rounded-full bg-violet-100 flex items-center justify-center mx-auto">
              <Loader2 className="w-7 h-7 text-violet-600 animate-spin" />
            </div>
            <div>
              <p className="font-semibold text-sm">Generating your document…</p>
              <p className="text-xs text-muted-foreground mt-1">
                The AI is writing and executing Python code. This takes 30–90 seconds.
              </p>
            </div>
            <div className="flex justify-center gap-6 text-[11px] text-muted-foreground font-mono">
              <span className="flex items-center gap-1"><ChevronRight className="w-3 h-3 text-violet-500" />Writing code</span>
              <span className="flex items-center gap-1"><ChevronRight className="w-3 h-3 text-violet-500" />Executing</span>
              <span className="flex items-center gap-1"><ChevronRight className="w-3 h-3 text-violet-500" />Critiquing</span>
            </div>
          </div>
        )}

        {/* ── Step 4: Result ──────────────────────────────────────────── */}
        {step === "result" && result?.ok && (
          <div className="space-y-4">
            {/* Download card */}
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-4 flex items-center gap-4">
              <div className="w-10 h-10 rounded-full bg-emerald-100 flex items-center justify-center shrink-0">
                <CheckCircle2 className="w-5 h-5 text-emerald-600" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-emerald-800">Document ready</p>
                <p className="text-xs text-emerald-700 truncate mt-0.5">{result.filename}</p>
                {result.size_bytes && (
                  <p className="text-[10px] font-mono text-emerald-600 mt-0.5">
                    {result.size_bytes >= 1_048_576
                      ? `${(result.size_bytes / 1_048_576).toFixed(1)} MB`
                      : `${Math.round(result.size_bytes / 1024)} KB`}
                  </p>
                )}
              </div>
              <a
                href={`${BASE}${result.download_url}`}
                download={result.filename}
                className="shrink-0"
              >
                <Button size="sm" className="gap-2 bg-emerald-600 hover:bg-emerald-700">
                  <Download className="w-3.5 h-3.5" /> Download
                </Button>
              </a>
            </div>

            {/* Critique */}
            {critique && (
              <div className="rounded-lg border border-border/50 bg-muted/10 p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-violet-500" />
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Quality critique</p>
                  {overallScore !== null && (
                    <Badge
                      variant="secondary"
                      className={`ml-auto text-xs font-mono ${
                        overallScore >= 8 ? "text-emerald-700" : overallScore >= 6 ? "text-amber-700" : "text-red-700"
                      }`}
                    >
                      {overallScore}/10 overall
                    </Badge>
                  )}
                </div>

                {critique.verdict && (
                  <p className="text-sm text-foreground/80 italic">"{critique.verdict}"</p>
                )}

                {critique.scores && Object.keys(critique.scores).length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(critique.scores).map(([k, v]) => (
                      <ScoreBadge key={k} label={k} value={v} />
                    ))}
                  </div>
                )}

                {critique.strengths && critique.strengths.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-[11px] font-semibold text-emerald-700 uppercase tracking-wide">Strengths</p>
                    {critique.strengths.map((s, i) => (
                      <p key={i} className="text-xs text-foreground/70 flex gap-1.5">
                        <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0 mt-0.5" />{s}
                      </p>
                    ))}
                  </div>
                )}

                {critique.gaps && critique.gaps.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-[11px] font-semibold text-amber-700 uppercase tracking-wide">Gaps</p>
                    {critique.gaps.map((g, i) => (
                      <p key={i} className="text-xs text-foreground/70 flex gap-1.5">
                        <AlertTriangle className="w-3 h-3 text-amber-500 shrink-0 mt-0.5" />{g}
                      </p>
                    ))}
                  </div>
                )}

                {critique.suggestions && critique.suggestions.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-[11px] font-semibold text-violet-700 uppercase tracking-wide">Improvement suggestions</p>
                    {critique.suggestions.map((s, i) => (
                      <button
                        key={i}
                        onClick={() => {
                          setRequest(r => r + (r.endsWith(" ") ? "" : " ") + s);
                          reset();
                          setTimeout(() => setRequest(r => r), 50);
                        }}
                        className="w-full text-left text-xs text-foreground/70 flex gap-1.5 items-start rounded px-2 py-1 hover:bg-muted/30 transition-colors group"
                        title="Click to restart with this improvement"
                      >
                        <ChevronRight className="w-3 h-3 text-violet-400 shrink-0 mt-0.5 group-hover:text-violet-600 transition-colors" />{s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <Button variant="outline" onClick={reset} className="w-full gap-2 text-sm">
              <RefreshCw className="w-4 h-4" /> Generate Another
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Studio() {
  const [mainTab, setMainTab] = useState<"voice" | "image" | "workshop" | "outputs">("voice");

  const MAIN_TABS = [
    { id: "voice",    label: "Voice Studio",       icon: Volume2 },
    { id: "image",    label: "Image Generation",   icon: ImageIcon },
    { id: "workshop", label: "Document Workshop",  icon: Wand2 },
    { id: "outputs",  label: "Recent Outputs",     icon: Video },
  ] as const;

  return (
    <div className="flex-1 min-h-0 flex flex-col animate-in fade-in duration-500">
      {/* Page header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border/50 shrink-0">
        <div>
          <h1 className="text-2xl font-serif font-semibold tracking-tight">Studio</h1>
          <p className="text-sm text-muted-foreground font-serif">
            Voice narration · Image generation · Document workshop
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ErrorBoundary label="service status bar">
            <ServiceStatusBar />
          </ErrorBoundary>
          <Button asChild variant="outline" size="sm" className="gap-2 shrink-0">
            <Link href="/system"><Settings2 className="w-4 h-4" /> Settings</Link>
          </Button>
        </div>
      </div>

      {/* Main tab bar */}
      <div className="flex items-center gap-0 border-b border-border/50 px-6 shrink-0 bg-muted/20">
        {MAIN_TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setMainTab(tab.id)}
              className={`
                flex items-center gap-1.5 px-4 py-3 text-sm border-b-2 transition-colors whitespace-nowrap
                ${mainTab === tab.id
                  ? "border-primary text-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
                }
              `}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content — full height */}
      <div className="flex-1 overflow-hidden">
        {mainTab === "voice" && (
          <ErrorBoundary label="voice studio">
            <VoiceStudio />
          </ErrorBoundary>
        )}

        {mainTab === "image" && (
          <ScrollArea className="h-full">
            <div className="p-6 max-w-3xl mx-auto space-y-6">
              <ErrorBoundary label="image generation panel"><ImageGenPanel /></ErrorBoundary>
            </div>
          </ScrollArea>
        )}

        {mainTab === "workshop" && (
          <ScrollArea className="h-full">
            <div className="p-6 max-w-3xl mx-auto space-y-6">
              <ErrorBoundary label="document workshop"><DocumentWorkshopPanel /></ErrorBoundary>
            </div>
          </ScrollArea>
        )}

        {mainTab === "outputs" && (
          <ScrollArea className="h-full">
            <div className="p-6 space-y-6">
              <ErrorBoundary label="outputs gallery"><OutputsGallery /></ErrorBoundary>
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  );
}
