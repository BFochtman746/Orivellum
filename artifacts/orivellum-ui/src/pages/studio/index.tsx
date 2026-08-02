import { useRef, useState } from "react";
import { useListVoices, useListStudioOutputs, useGetSystemHealth, useListLibrary } from "@workspace/api-client-react";
import { ErrorBoundary } from "@/components/error-boundary";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Mic, Play, Pause, Settings2, Video, Image as ImageIcon,
  FileAudio, Loader2, Volume2, Download, BookHeadphones, FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Link } from "wouter";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── TTS panel ─────────────────────────────────────────────────────────────────

function TTSPanel() {
  const { data: voicesResp, isLoading: loadingVoices } = useListVoices();
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
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
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

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 font-serif text-lg">
          <Volume2 className="w-5 h-5 text-muted-foreground" />
          Text to Speech
        </CardTitle>
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

  const selectedDoc = docs.find((d: any) => d.id === docId);

  async function handleGenerate() {
    if (!docId) return;
    setLoading(true);
    setAudioUrl(null);
    setPlaying(false);
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
      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      const name = `${selectedDoc?.title || "audiobook"}.mp3`;
      setAudioUrl(url);
      setAudioName(name);
      toast.success("Audiobook ready — tap play below");
    } catch (e: any) {
      toast.error(`Audiobook failed: ${e.message}`, { duration: 10_000 });
    } finally {
      setLoading(false);
    }
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

        <Button onClick={handleGenerate} disabled={!docId || loading || voicesError || voices.length === 0} className="w-full gap-2">
          {loading
            ? <><Loader2 className="w-4 h-4 animate-spin" />Converting to audio… (may take a few minutes)</>
            : <><BookHeadphones className="w-4 h-4" />Generate Audiobook</>}
        </Button>

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
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(512);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const { data: health } = useGetSystemHealth({ query: { staleTime: 15_000 } } as any);
  const aiOnline = (health as any)?.status === "ok";

  async function handleGenerate() {
    if (!prompt.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const resp = await apiFetch(`${BASE}/studio/image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt.trim(), width, height }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const url = data?.data?.[0]?.url ?? data?.data?.[0]?.b64_json
        ? `data:image/png;base64,${data.data[0].b64_json}`
        : null;
      if (!url) throw new Error("No image in response");
      setResult(url);
    } catch (e: any) {
      toast.error(`Image generation failed: ${e.message}`);
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
          {!aiOnline && (
            <Badge variant="outline" className="text-[9px] font-mono uppercase text-amber-600 border-amber-300 ml-1">
              AI offline
            </Badge>
          )}
        </CardTitle>
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

        <div className="grid grid-cols-2 gap-4">
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
            <img src={result} alt="Generated" className="w-full object-contain max-h-64" />
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
  const { data: outputsResp, isLoading } = useListStudioOutputs(
    { query: { refetchInterval: 15_000 } } as any
  );
  const outputs = outputsResp?.outputs ?? [];

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 font-serif text-lg">
          <Video className="w-5 h-5 text-muted-foreground" />
          Recent Outputs
          {outputs.length > 0 && (
            <Badge variant="secondary" className="text-[10px] font-mono ml-1">{outputs.length}</Badge>
          )}
        </CardTitle>
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
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {outputs.map((out: any, i: number) => (
              <div key={i}
                className="flex items-center gap-3 p-3 rounded-lg border border-border/50 bg-muted/10 hover:border-primary/30 transition-colors group">
                <div className="shrink-0 w-9 h-9 rounded-full bg-muted flex items-center justify-center">
                  {out.kind === "audio" ? <FileAudio className="w-4 h-4 text-muted-foreground" />
                    : out.kind === "image" ? <ImageIcon className="w-4 h-4 text-muted-foreground" />
                    : <Video className="w-4 h-4 text-muted-foreground" />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium truncate">{out.name}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <Badge variant="outline" className="text-[9px] font-mono uppercase">{out.kind}</Badge>
                    <span className="text-[10px] font-mono text-muted-foreground">
                      {out.size_bytes >= 1_048_576
                        ? `${(out.size_bytes / 1_048_576).toFixed(1)} MB`
                        : `${Math.round(out.size_bytes / 1024)} KB`}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Studio() {
  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">Studio</h1>
          <p className="text-muted-foreground mt-1 font-serif">
            Voice synthesis, image generation, and media outputs.
          </p>
        </div>
        <Button asChild variant="outline" className="gap-2">
          <Link href="/system"><Settings2 className="w-4 h-4" /> Engine Settings</Link>
        </Button>
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        <ErrorBoundary label="TTS panel"><TTSPanel /></ErrorBoundary>
        <ErrorBoundary label="image generation panel"><ImageGenPanel /></ErrorBoundary>
      </div>

      <ErrorBoundary label="audiobook panel"><AudiobookPanel /></ErrorBoundary>

      <ErrorBoundary label="outputs gallery"><OutputsGallery /></ErrorBoundary>
    </div>
  );
}
