/**
 * Voice Studio — the complete voice narration workspace.
 *
 * Tabs:
 *   Browse    — full voice catalog grid with live sample preview
 *   Recommend — AI narrator recommendation from a Work
 *   Design    — describe a narrator in natural language → best match
 *   Audiobook — chapter-structured audiobook builder (Work or Document)
 */
import { useRef, useState, useCallback, useEffect } from "react";
import { useListVoices, useListWorks, useListLibrary } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Mic, Play, Pause, Loader2, Volume2, Download, BookHeadphones,
  FileText, Sparkles, Wand2, ChevronRight, Star, RotateCcw,
  Search, Filter, Users, Zap, CheckCircle2, AlertCircle, Info,
  AudioLines, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ──────────────────────────────────────────────────────────────────────

interface VoiceDimensions {
  warmth: number;
  authority: number;
  gravitas: number;
  pace: number;
  brightness: number;
  age: number;
}

interface VoiceEntry {
  id: string;
  name: string;
  accent?: string;
  gender?: string;
  description?: string;
  dimensions?: VoiceDimensions;
  tags?: string[];
  builtin?: boolean;
  engine?: string;
  custom?: boolean;
  /** Engine used to generate this voice's cached sample, if one exists.
   *  "kokoro" = neural quality; "espeak" = basic robotic fallback; null = not yet generated. */
  sample_engine?: "kokoro" | "espeak" | null;
}

interface Recommendation {
  voice_id: string;
  score: number;
  headline: string;
  rationale: string;
  dimension_match: string;
  voice: VoiceEntry;
}

interface RecommendResult {
  work_id: string;
  work_title: string;
  genre_analysis: string;
  narrator_profile: string;
  recommendations: Recommendation[];
}

interface DesignMatch {
  voice_id: string;
  match_score: number;
  why: string;
  voice: VoiceEntry;
}

interface DesignResult {
  description: string;
  target_dimensions: Partial<VoiceDimensions>;
  interpretation: string;
  matches: DesignMatch[];
}

// ── Shared audio player singleton ─────────────────────────────────────────────

function useGlobalAudio() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  // Maps voice ID → synthesis engine used for its sample ("kokoro" | "espeak").
  // Populated lazily as the user plays samples; persists for the session.
  const [sampleEngines, setSampleEngines] = useState<Record<string, string>>({});

  useEffect(() => {
    const el = new Audio();
    el.onended  = () => setPlayingId(null);
    el.onpause  = () => setPlayingId(null);
    el.onerror  = () => { setPlayingId(null); setLoadingId(null); };
    audioRef.current = el;
    return () => { el.pause(); el.src = ""; };
  }, []);

  const playVoiceSample = useCallback(async (voiceId: string) => {
    const el = audioRef.current;
    if (!el) return;

    if (playingId === voiceId) {
      el.pause();
      setPlayingId(null);
      return;
    }

    el.pause();
    setPlayingId(null);
    setLoadingId(voiceId);

    try {
      const resp = await apiFetch(`${BASE}/studio/voices/${voiceId}/sample`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      // Capture which engine generated this sample so VoiceCard can show a
      // "basic synthesis" badge when the espeak fallback was used instead of
      // neural Kokoro.
      const engine = resp.headers.get("X-TTS-Engine") ?? "kokoro";
      setSampleEngines(prev =>
        prev[voiceId] === engine ? prev : { ...prev, [voiceId]: engine }
      );

      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      el.src = url;
      await el.play();
      setPlayingId(voiceId);
    } catch (e: any) {
      toast.error(`Could not load sample for this voice`);
    } finally {
      setLoadingId(null);
    }
  }, [playingId]);

  const stopAll = useCallback(() => {
    audioRef.current?.pause();
    setPlayingId(null);
  }, []);

  return { playingId, loadingId, playVoiceSample, stopAll, sampleEngines };
}

// ── Dimension bar ─────────────────────────────────────────────────────────────

function DimensionBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] font-mono text-muted-foreground w-16 shrink-0 capitalize">{label}</span>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${(value / 10) * 100}%`, background: color || "var(--gilt)" }}
        />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground w-4 text-right">{value}</span>
    </div>
  );
}

const DIMENSION_COLORS: Record<string, string> = {
  warmth:    "var(--gilt)",
  authority: "var(--green-2)",
  gravitas:  "var(--rust)",
  pace:      "var(--green-2)",
  brightness:"var(--gilt)",
  age:       "#8A7A6A",
};

// ── Voice Card ────────────────────────────────────────────────────────────────

function VoiceCard({
  voice,
  selected,
  onSelect,
  playingId,
  loadingId,
  onPlay,
  sampleEngines = {},
}: {
  voice: VoiceEntry;
  selected: boolean;
  onSelect: (v: VoiceEntry) => void;
  playingId: string | null;
  loadingId: string | null;
  onPlay: (id: string) => void;
  /** Engine recorded for each voice's sample — keyed by voice ID.
   *  Populated lazily as the user plays samples this session. */
  sampleEngines?: Record<string, string>;
}) {
  const isPlaying = playingId === voice.id;
  const isLoading = loadingId === voice.id;
  const dims = voice.dimensions;

  // Show a "basic" badge when the espeak fallback was used for this voice's
  // sample — either discovered this session (sampleEngines) or from a previous
  // session persisted in the DB (voice.sample_engine).
  const isEspeak =
    sampleEngines[voice.id] === "espeak" || voice.sample_engine === "espeak";

  const accentColor = voice.accent === "british"
    ? "border-border/60 bg-muted/20"
    : "";

  const genderIcon = voice.gender === "feminine" ? "♀" : voice.gender === "masculine" ? "♂" : "◆";

  return (
    <div
      onClick={() => onSelect(voice)}
      className={`
        relative group cursor-pointer rounded-xl border-2 p-4 transition-all
        hover:shadow-md hover:-translate-y-0.5
        ${selected
          ? "border-primary bg-primary/5 shadow-md ring-1 ring-primary/20"
          : "border-border/50 hover:border-primary/40 bg-card"
        }
      `}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div>
          <div className="flex items-center gap-1.5">
            <span className="font-semibold text-sm leading-tight">{voice.name}</span>
            <span className="text-[11px] text-muted-foreground">{genderIcon}</span>
            {voice.builtin && (
              <span className="text-[9px] font-mono px-1 py-0.5 rounded" style={{ background: "var(--green-soft)", color: "var(--green-2)" }}>
                ✓
              </span>
            )}
            {isEspeak && (
              <span
                className="text-[9px] font-mono px-1 py-0.5 rounded" style={{ background: "var(--gilt-soft)", color: "var(--gilt)" }}
                title="Sample uses basic espeak synthesis — Kokoro neural model not yet loaded. Install kokoro-onnx for premium audio."
              >
                basic
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 mt-0.5">
            {voice.accent && (
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded-full border capitalize ${accentColor}`}>
                {voice.accent}
              </span>
            )}
          </div>
        </div>

        {/* Play button */}
        <button
          onClick={e => { e.stopPropagation(); onPlay(voice.id); }}
          disabled={isLoading}
          className={`
            w-9 h-9 rounded-full flex items-center justify-center shrink-0 transition-all
            ${isPlaying
              ? "bg-primary text-primary-foreground shadow-sm"
              : "bg-muted hover:bg-primary/10 hover:text-primary text-muted-foreground"
            }
            disabled:opacity-50
          `}
          title={isPlaying ? "Stop" : "Preview voice sample"}
        >
          {isLoading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : isPlaying ? (
            <Pause className="w-3.5 h-3.5" />
          ) : (
            <Play className="w-3.5 h-3.5 ml-0.5" />
          )}
        </button>
      </div>

      {/* Description */}
      {voice.description && (
        <p className="text-xs text-muted-foreground line-clamp-2 mb-3 leading-relaxed">
          {voice.description}
        </p>
      )}

      {/* Key dimensions — top 3 */}
      {dims && (
        <div className="space-y-1.5 mb-3">
          {(["warmth", "authority", "gravitas"] as const).map(key => (
            <DimensionBar
              key={key}
              label={key}
              value={dims[key]}
              color={DIMENSION_COLORS[key]}
            />
          ))}
        </div>
      )}

      {/* Genre tags */}
      {voice.tags && voice.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {voice.tags.slice(0, 3).map(tag => (
            <span key={tag}
              className="text-[9px] font-mono px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
              {tag}
            </span>
          ))}
        </div>
      )}

      {selected && (
        <div className="absolute top-2 right-2 w-4 h-4 rounded-full bg-primary flex items-center justify-center">
          <CheckCircle2 className="w-3 h-3 text-primary-foreground" />
        </div>
      )}
    </div>
  );
}

// ── Voice detail panel ────────────────────────────────────────────────────────

function VoiceDetailPanel({
  voice,
  onClose,
  playingId,
  loadingId,
  onPlay,
  onUseVoice,
}: {
  voice: VoiceEntry;
  onClose: () => void;
  playingId: string | null;
  loadingId: string | null;
  onPlay: (id: string) => void;
  onUseVoice: (voice: VoiceEntry) => void;
}) {
  const dims = voice.dimensions;
  const isPlaying = playingId === voice.id;
  const isLoading = loadingId === voice.id;

  return (
    <div className="border-l border-border/50 bg-muted/10 flex flex-col h-full">
      <div className="flex items-center justify-between p-4 border-b border-border/50">
        <div>
          <h3 className="font-semibold text-base">{voice.name}</h3>
          <p className="text-xs text-muted-foreground capitalize">
            {voice.accent} · {voice.gender}
          </p>
        </div>
        <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-muted transition-colors">
          <X className="w-4 h-4 text-muted-foreground" />
        </button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-5">
          {/* Description */}
          <p className="text-sm text-muted-foreground leading-relaxed">
            {voice.description}
          </p>

          {/* Sample player */}
          <div>
            <p className="text-xs font-mono uppercase text-muted-foreground mb-2">Voice Sample</p>
            <button
              onClick={() => onPlay(voice.id)}
              disabled={isLoading}
              className={`
                w-full flex items-center gap-3 p-3 rounded-lg border transition-all
                ${isPlaying
                  ? "border-primary bg-primary/5 text-primary"
                  : "border-border/50 bg-card hover:border-primary/40 hover:bg-muted/30"
                }
              `}
            >
              <div className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 ${
                isPlaying ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
              }`}>
                {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> :
                 isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </div>
              <div className="text-left">
                <p className="text-sm font-medium">{isPlaying ? "Playing…" : "Preview Sample"}</p>
                <p className="text-xs text-muted-foreground">~8 second clip · Standardized text</p>
              </div>
              {isPlaying && (
                <div className="ml-auto flex gap-0.5">
                  {[1,2,3,4].map(i => (
                    <div key={i}
                      className="w-0.5 bg-primary rounded-full animate-pulse"
                      style={{ height: `${8 + i * 4}px`, animationDelay: `${i * 100}ms` }}
                    />
                  ))}
                </div>
              )}
            </button>
          </div>

          {/* All dimensions */}
          {dims && (
            <div>
              <p className="text-xs font-mono uppercase text-muted-foreground mb-3">Voice Dimensions</p>
              <div className="space-y-2.5">
                {(["warmth", "authority", "gravitas", "pace", "brightness", "age"] as const).map(key => (
                  <DimensionBar key={key} label={key} value={dims[key]} color={DIMENSION_COLORS[key]} />
                ))}
              </div>
              <div className="mt-3 text-[10px] text-muted-foreground/60 space-y-0.5 font-mono">
                <p>warmth — cold/clinical → warm/intimate</p>
                <p>authority — gentle → commanding</p>
                <p>gravitas — light → solemn weight</p>
                <p>pace — fast/urgent → slow/measured</p>
              </div>
            </div>
          )}

          {/* Genre tags */}
          {voice.tags && voice.tags.length > 0 && (
            <div>
              <p className="text-xs font-mono uppercase text-muted-foreground mb-2">Best For</p>
              <div className="flex flex-wrap gap-1.5">
                {voice.tags.map(tag => (
                  <span key={tag}
                    className="text-xs px-2 py-1 rounded-full bg-muted text-muted-foreground capitalize">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* CTA */}
      <div className="p-4 border-t border-border/50 space-y-2">
        <Button className="w-full gap-2" onClick={() => onUseVoice(voice)}>
          <CheckCircle2 className="w-4 h-4" />
          Use {voice.name} for Audiobook
        </Button>
      </div>
    </div>
  );
}

// ── TTS Engine Badge (Audiobook tab) ─────────────────────────────────────────
// Small indicator showing which synthesis engine will be used for generation.
// Fetches /api/studio/status once and caches for the session.

function _AudiobookEngineBadge() {
  const [engine, setEngine] = useState<string | null>(null);
  const [isPremium, setIsPremium] = useState(false);
  const [premiumEngine, setPremiumEngine] = useState<string | null>(null);

  useEffect(() => {
    apiFetch(`${BASE}/studio/status`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data?.tts) return;
        const best: string | null = data.tts.best_strategy ?? null;
        setIsPremium(data.tts.premium_tts_active === true);
        setPremiumEngine(data.tts.premium_tts_engine ?? null);
        setEngine(best);
      })
      .catch(() => {});
  }, []);

  if (!engine) return null;

  if (isPremium) {
    const engineName = premiumEngine
      ? premiumEngine.charAt(0).toUpperCase() + premiumEngine.slice(1)
      : null;
    return (
      <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border" style={{ background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }}>
        <Sparkles className="w-3 h-3 shrink-0" style={{ color: "var(--gilt)" }} />
        <span className="text-[10px] font-mono" style={{ color: "var(--gilt)" }}>
          {engineName
            ? `Studio engine active — ${engineName} on your GPU`
            : "Premium TTS active — hero narration quality"}
        </span>
      </div>
    );
  }

  const label =
    engine === "Kokoro ONNX" ? "Kokoro neural TTS" :
    engine === "AI Server"   ? "AI server TTS" :
    engine === "espeak-ng"   ? "espeak-ng (basic)" :
    engine;

  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-muted/40 border border-border/50">
      <AudioLines className="w-3 h-3 text-muted-foreground shrink-0" />
      <span className="text-[10px] font-mono text-muted-foreground">
        Engine: {label}
      </span>
      {engine === "espeak-ng" && (
        <span className="text-[9px] font-mono px-1 py-0.5 rounded ml-auto" style={{ background: "var(--gilt-soft)", color: "var(--gilt)" }}>
          basic
        </span>
      )}
    </div>
  );
}

// ── Clone Tab ─────────────────────────────────────────────────────────────────
// Consent-gated voice cloning managed by the loopback premium sidecar.
// A cloned voice cannot speak until its consent statement is acknowledged.

interface CloneEntry {
  id: string;
  name: string;
  sha256: string;
  size_bytes: number;
  created_at: number;
  consent_acknowledged: boolean;
  usable: boolean;
}

interface CloneListResp {
  configured: boolean;
  reachable: boolean;
  voices: CloneEntry[];
  consent_statement: string | null;
}

function CloneTab({ onUseVoice }: { onUseVoice: (v: VoiceEntry) => void }) {
  const [data, setData] = useState<CloneListResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await apiFetch(`${BASE}/studio/voice-clones`);
      if (r.ok) setData(await r.json());
    } catch { /* leave previous state */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function handleUpload() {
    if (!file || !name.trim()) {
      toast.error("Pick a reference clip and give the voice a name");
      return;
    }
    if (!consent) {
      toast.error("You must confirm the consent statement before cloning");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("name", name.trim());
      fd.append("consent_ack", "true");
      const r = await apiFetch(`${BASE}/studio/voice-clones`, { method: "POST", body: fd });
      if (!r.ok) {
        const err = await r.json().catch(() => null);
        throw new Error(err?.detail || `Upload failed (${r.status})`);
      }
      toast.success(`Voice "${name.trim()}" cloned`);
      setName(""); setFile(null); setConsent(false);
      if (fileRef.current) fileRef.current.value = "";
      await refresh();
    } catch (e: any) {
      toast.error(e?.message || "Upload failed");
    } finally { setBusy(false); }
  }

  async function handleConsent(v: CloneEntry) {
    try {
      const r = await apiFetch(`${BASE}/studio/voice-clones/${v.id}/consent`, { method: "POST" });
      if (!r.ok) throw new Error();
      toast.success(`Consent recorded for "${v.name}"`);
      await refresh();
    } catch { toast.error("Could not record consent"); }
  }

  async function handleDelete(v: CloneEntry) {
    try {
      const r = await apiFetch(`${BASE}/studio/voice-clones/${v.id}`, { method: "DELETE" });
      if (!r.ok) throw new Error();
      toast.success(`Deleted "${v.name}"`);
      await refresh();
    } catch { toast.error("Could not delete voice"); }
  }

  if (loading) {
    return <div className="p-4"><Skeleton className="h-40 w-full rounded-xl" /></div>;
  }

  if (!data?.configured) {
    return (
      <ScrollArea className="h-full">
        <div className="p-4 max-w-2xl">
          <Card>
            <CardHeader><CardTitle className="text-base flex items-center gap-2"><Mic className="w-4 h-4" /> Voice cloning is not set up yet</CardTitle></CardHeader>
            <CardContent className="text-sm text-muted-foreground space-y-2">
              <p>Cloning runs on a private engine on this computer's GPU — your voice recordings never leave the machine.</p>
              <p>To turn it on:</p>
              <ol className="list-decimal list-inside space-y-1">
                <li>Run <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">scripts\start-voice-sidecar.ps1 -Setup</code> once, then <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">scripts\start-voice-sidecar.ps1</code></li>
                <li>In <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">config.yaml</code>, set <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">tts_premium_url: "http://127.0.0.1:9883"</code> and <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">tts_premium_ack_license: true</code></li>
                <li>Restart Orivellum</li>
              </ol>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    );
  }

  if (!data.reachable) {
    return (
      <div className="p-4 max-w-2xl">
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-destructive/70" />
            <div>
              <p className="text-foreground font-medium mb-1">The voice engine isn't running</p>
              <p>Start it on this computer with <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">scripts\start-voice-sidecar.ps1</code>, then reopen this tab.</p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-4 max-w-3xl">
        {/* Upload form */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2"><Mic className="w-4 h-4" /> Clone a voice</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Upload 5–30 seconds of clean, single-speaker audio. The recording stays on this computer.
            </p>
            <div className="grid sm:grid-cols-2 gap-3">
              <Input
                placeholder="Voice name (e.g. Brian — narrator)"
                value={name}
                onChange={e => setName(e.target.value)}
                maxLength={80}
              />
              <Input
                ref={fileRef}
                type="file"
                accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg"
                onChange={e => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
            <label className="flex items-start gap-2 text-xs text-muted-foreground cursor-pointer select-none">
              <input
                type="checkbox"
                checked={consent}
                onChange={e => setConsent(e.target.checked)}
                className="mt-0.5 accent-[var(--gilt)]"
              />
              <span>{data.consent_statement || "I confirm I am the speaker, or have the speaker's explicit permission to clone this voice."}</span>
            </label>
            <Button onClick={handleUpload} disabled={busy || !file || !name.trim() || !consent} size="sm">
              {busy ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5 mr-1.5" />}
              Clone voice
            </Button>
          </CardContent>
        </Card>

        {/* Cloned voices list */}
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Your cloned voices</h3>
          {data.voices.length === 0 && (
            <p className="text-xs text-muted-foreground">No cloned voices yet.</p>
          )}
          {data.voices.map(v => (
            <Card key={v.id}>
              <CardContent className="py-3 flex items-center gap-3 flex-wrap">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{v.name}</span>
                    {v.usable ? (
                      <Badge variant="outline" className="text-[10px] gap-1"><CheckCircle2 className="w-3 h-3" /> Consented</Badge>
                    ) : (
                      <Badge variant="outline" className="text-[10px] gap-1 border-destructive/40 text-destructive"><AlertCircle className="w-3 h-3" /> Consent needed</Badge>
                    )}
                  </div>
                  <p className="text-[10px] font-mono text-muted-foreground truncate">
                    fingerprint {v.sha256.slice(0, 16)} · {(v.size_bytes / 1024 / 1024).toFixed(1)} MB
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  {!v.usable && (
                    <Button size="sm" variant="outline" onClick={() => handleConsent(v)}>
                      Acknowledge consent
                    </Button>
                  )}
                  {v.usable && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onUseVoice({ id: `clone:${v.id}`, name: v.name, custom: true, engine: "premium" })}
                    >
                      <BookHeadphones className="w-3.5 h-3.5 mr-1.5" /> Use for audiobook
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => handleDelete(v)}>
                    <X className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </ScrollArea>
  );
}

// ── Browse Tab ────────────────────────────────────────────────────────────────

function BrowseTab({
  voices,
  selectedVoice,
  onSelectVoice,
  onUseVoice,
  globalAudio,
}: {
  voices: VoiceEntry[];
  selectedVoice: VoiceEntry | null;
  onSelectVoice: (v: VoiceEntry) => void;
  onUseVoice: (v: VoiceEntry) => void;
  globalAudio: ReturnType<typeof useGlobalAudio>;
}) {
  const [search, setSearch] = useState("");
  const [filterGender, setFilterGender] = useState<string>("all");
  const [filterAccent, setFilterAccent] = useState<string>("all");
  const [filterTone, setFilterTone] = useState<string>("all");

  const filtered = voices.filter(v => {
    if (search) {
      const q = search.toLowerCase();
      const hit =
        v.name?.toLowerCase().includes(q) ||
        v.description?.toLowerCase().includes(q) ||
        v.tags?.some(t => t.toLowerCase().includes(q)) ||
        v.accent?.toLowerCase().includes(q);
      if (!hit) return false;
    }
    if (filterGender !== "all" && v.gender !== filterGender) return false;
    if (filterAccent !== "all" && v.accent !== filterAccent) return false;
    if (filterTone !== "all" && v.dimensions) {
      const d = v.dimensions;
      if (filterTone === "warm"      && d.warmth < 7)     return false;
      if (filterTone === "authority" && d.authority < 7)  return false;
      if (filterTone === "dramatic"  && d.gravitas < 7)   return false;
      if (filterTone === "bright"    && d.brightness < 7) return false;
    }
    return true;
  });

  const showDetail = !!selectedVoice;

  return (
    <div className="flex gap-0 h-full" style={{ minHeight: 0 }}>
      {/* Main grid area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Filter bar */}
        <div className="flex flex-wrap items-center gap-2 p-4 border-b border-border/50 bg-muted/10">
          <div className="relative flex-1 min-w-40">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
            <Input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search voices…"
              className="pl-8 h-8 text-sm"
            />
          </div>

          <Select value={filterGender} onValueChange={setFilterGender}>
            <SelectTrigger className="h-8 w-32 text-xs">
              <SelectValue placeholder="Gender" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All genders</SelectItem>
              <SelectItem value="masculine">Masculine</SelectItem>
              <SelectItem value="feminine">Feminine</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filterAccent} onValueChange={setFilterAccent}>
            <SelectTrigger className="h-8 w-32 text-xs">
              <SelectValue placeholder="Accent" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All accents</SelectItem>
              <SelectItem value="american">American</SelectItem>
              <SelectItem value="british">British</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filterTone} onValueChange={setFilterTone}>
            <SelectTrigger className="h-8 w-36 text-xs">
              <SelectValue placeholder="Tone" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All tones</SelectItem>
              <SelectItem value="warm">Warm / Intimate</SelectItem>
              <SelectItem value="authority">Authoritative</SelectItem>
              <SelectItem value="dramatic">Dramatic / Heavy</SelectItem>
              <SelectItem value="bright">Bright / Clear</SelectItem>
            </SelectContent>
          </Select>

          <span className="text-xs font-mono text-muted-foreground shrink-0">
            {filtered.length} voice{filtered.length !== 1 ? "s" : ""}
          </span>
        </div>

        {/* Voice grid */}
        <ScrollArea className="flex-1">
          <div className={`grid gap-3 p-4 ${showDetail ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"}`}>
            {filtered.map(v => (
              <VoiceCard
                key={v.id}
                voice={v}
                selected={selectedVoice?.id === v.id}
                onSelect={onSelectVoice}
                playingId={globalAudio.playingId}
                loadingId={globalAudio.loadingId}
                onPlay={globalAudio.playVoiceSample}
                sampleEngines={globalAudio.sampleEngines}
              />
            ))}
            {filtered.length === 0 && (
              <div className="col-span-full py-16 text-center text-muted-foreground">
                <Volume2 className="w-8 h-8 mx-auto mb-3 opacity-30" />
                <p className="text-sm">No voices match your filters.</p>
                <button
                  onClick={() => { setSearch(""); setFilterGender("all"); setFilterAccent("all"); setFilterTone("all"); }}
                  className="mt-2 text-xs text-primary hover:underline"
                >
                  Clear filters
                </button>
              </div>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Detail panel */}
      {selectedVoice && (
        <div className="w-72 shrink-0 flex flex-col" style={{ minHeight: 0 }}>
          <VoiceDetailPanel
            voice={selectedVoice}
            onClose={() => onSelectVoice(selectedVoice)} // toggle off
            playingId={globalAudio.playingId}
            loadingId={globalAudio.loadingId}
            onPlay={globalAudio.playVoiceSample}
            onUseVoice={onUseVoice}
          />
        </div>
      )}
    </div>
  );
}

// ── Recommend Tab ─────────────────────────────────────────────────────────────

function RecommendTab({
  onUseVoice,
  globalAudio,
}: {
  onUseVoice: (v: VoiceEntry) => void;
  globalAudio: ReturnType<typeof useGlobalAudio>;
}) {
  const { data: worksResp } = useListWorks();
  const works = (worksResp as any)?.works ?? [];

  const [workId, setWorkId] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RecommendResult | null>(null);

  async function handleRecommend() {
    if (!workId) return;
    setLoading(true);
    setResult(null);
    try {
      const resp = await apiFetch(`${BASE}/studio/voices/recommend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ work_id: workId, top_n: 5 }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      setResult(await resp.json());
    } catch (e: any) {
      toast.error(`Recommendation failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Controls */}
      <div className="p-5 border-b border-border/50 space-y-4">
        <div className="space-y-1.5">
          <p className="text-xs font-mono uppercase text-muted-foreground">Select a Work to analyze</p>
          <Select value={workId} onValueChange={setWorkId}>
            <SelectTrigger>
              <SelectValue placeholder="Choose a Work from your library…" />
            </SelectTrigger>
            <SelectContent>
              {works.map((w: any) => (
                <SelectItem key={w.id} value={w.id}>{w.title}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          onClick={handleRecommend}
          disabled={!workId || loading}
          className="w-full gap-2"
        >
          {loading
            ? <><Loader2 className="w-4 h-4 animate-spin" /> Analyzing your book…</>
            : <><Sparkles className="w-4 h-4" /> Analyze &amp; Recommend Voices</>}
        </Button>
        {!workId && (
          <p className="text-xs text-muted-foreground text-center">
            The AI reads your book's knowledge base, tone, and genre to rank narrator voices.
          </p>
        )}
      </div>

      <ScrollArea className="flex-1">
        {loading && (
          <div className="p-6 space-y-3">
            {[1, 2, 3].map(i => <Skeleton key={i} className="h-28 w-full rounded-xl" />)}
            <p className="text-center text-xs text-muted-foreground pt-2">
              Reading your book and matching narrator voices…
            </p>
          </div>
        )}

        {result && (
          <div className="p-5 space-y-5">
            {/* Analysis summary */}
            <div className="rounded-xl border border-border/50 bg-muted/10 p-4 space-y-3">
              <div className="flex items-center gap-2">
                <AudioLines className="w-4 h-4 shrink-0" style={{ color: "var(--gilt)" }} />
                <span className="text-xs font-mono uppercase text-muted-foreground">Genre Analysis</span>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">{result.genre_analysis}</p>
              {result.narrator_profile && (
                <>
                  <div className="flex items-center gap-2 pt-1">
                    <Mic className="w-4 h-4 text-primary shrink-0" />
                    <span className="text-xs font-mono uppercase text-muted-foreground">Ideal Narrator Profile</span>
                  </div>
                  <p className="text-sm leading-relaxed">{result.narrator_profile}</p>
                </>
              )}
            </div>

            {/* Recommendations */}
            <div className="space-y-3">
              <p className="text-xs font-mono uppercase text-muted-foreground">
                Top {result.recommendations.length} Recommendations
              </p>
              {result.recommendations.map((rec, idx) => (
                <div key={rec.voice_id}
                  className="rounded-xl border border-border/50 bg-card p-4 space-y-3">
                  <div className="flex items-start gap-3">
                    {/* Rank badge */}
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                      idx === 0
                        ? ""
                        : "bg-muted text-muted-foreground"
                    }`}>
                      {idx + 1}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-sm">{rec.voice?.name ?? rec.voice_id}</span>
                        {rec.voice?.accent && (
                          <span className="text-[10px] font-mono text-muted-foreground capitalize">
                            {rec.voice.accent}
                          </span>
                        )}
                        <span className="ml-auto text-xs font-mono font-bold" style={{ color: "var(--green-2)" }}>
                          {rec.score}%
                        </span>
                      </div>
                      <p className="text-xs text-primary font-medium mt-0.5">{rec.headline}</p>
                    </div>
                  </div>

                  <p className="text-xs text-muted-foreground leading-relaxed">{rec.rationale}</p>

                  {rec.dimension_match && (
                    <p className="text-[10px] font-mono text-muted-foreground/70 italic">
                      {rec.dimension_match}
                    </p>
                  )}

                  <div className="flex items-center gap-2 pt-1">
                    <button
                      onClick={() => globalAudio.playVoiceSample(rec.voice_id)}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                        globalAudio.playingId === rec.voice_id
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted hover:bg-muted/80 text-muted-foreground"
                      }`}
                    >
                      {globalAudio.loadingId === rec.voice_id ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : globalAudio.playingId === rec.voice_id ? (
                        <Pause className="w-3 h-3" />
                      ) : (
                        <Play className="w-3 h-3" />
                      )}
                      {globalAudio.playingId === rec.voice_id ? "Stop" : "Preview"}
                    </button>

                    {rec.voice && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="ml-auto text-xs h-7 gap-1"
                        onClick={() => onUseVoice(rec.voice)}
                      >
                        <CheckCircle2 className="w-3 h-3" />
                        Use for Audiobook
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!loading && !result && (
          <div className="flex flex-col items-center justify-center h-64 text-center px-8 text-muted-foreground">
            <Sparkles className="w-10 h-10 mb-3 opacity-20" />
            <p className="text-sm font-medium">AI Narrator Recommendation</p>
            <p className="text-xs mt-1 leading-relaxed">
              Select a Work and the AI will read your book's knowledge base, analyze the genre and
              tone, then rank every narrator voice by how well it suits your specific content.
            </p>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

// ── Design Tab ────────────────────────────────────────────────────────────────

function DesignTab({
  onUseVoice,
  globalAudio,
}: {
  onUseVoice: (v: VoiceEntry) => void;
  globalAudio: ReturnType<typeof useGlobalAudio>;
}) {
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DesignResult | null>(null);

  const EXAMPLES = [
    "A deep, ancient male voice with gravitas and reverence — like a prophet recounting sacred history",
    "Warm, intimate female narrator with natural storytelling quality and unhurried pace",
    "Authoritative British male voice — scholarly, distinguished, trustworthy",
    "Bright, youthful female voice with energy and forward momentum",
    "Dramatic, rich female voice capable of handling emotional complexity",
  ];

  async function handleDesign() {
    if (!description.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const resp = await apiFetch(`${BASE}/studio/voices/design`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: description.trim() }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      setResult(await resp.json());
    } catch (e: any) {
      toast.error(`Voice design failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="p-5 border-b border-border/50 space-y-4">
        <div className="space-y-1.5">
          <p className="text-xs font-mono uppercase text-muted-foreground">
            Describe the narrator you want
          </p>
          <Textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="e.g. &quot;A deep, ancient male voice with gravitas and reverence — like a prophet recounting sacred history&quot;"
            className="min-h-20 resize-none text-sm"
            maxLength={500}
          />
          <p className="text-[10px] text-muted-foreground text-right">
            {description.length}/500
          </p>
        </div>

        {/* Example prompts */}
        <div className="space-y-1.5">
          <p className="text-[10px] font-mono text-muted-foreground uppercase">Try an example</p>
          <div className="space-y-1.5">
            {EXAMPLES.map(ex => (
              <button
                key={ex}
                onClick={() => setDescription(ex)}
                className="w-full text-left text-xs text-muted-foreground hover:text-foreground p-2 rounded-lg border border-border/30 hover:border-border hover:bg-muted/30 transition-all leading-relaxed"
              >
                "{ex}"
              </button>
            ))}
          </div>
        </div>

        <Button
          onClick={handleDesign}
          disabled={!description.trim() || loading}
          className="w-full gap-2"
        >
          {loading
            ? <><Loader2 className="w-4 h-4 animate-spin" /> Finding best match…</>
            : <><Wand2 className="w-4 h-4" /> Find Matching Voice</>}
        </Button>
      </div>

      <ScrollArea className="flex-1">
        {result && (
          <div className="p-5 space-y-5">
            {/* Interpretation */}
            <div className="rounded-xl border border-border/50 bg-muted/10 p-4">
              <div className="flex items-center gap-2 mb-2">
                <Info className="w-4 h-4" style={{ color: "var(--gilt)" }} />
                <span className="text-xs font-mono uppercase text-muted-foreground">Interpretation</span>
              </div>
              <p className="text-sm text-muted-foreground">{result.interpretation}</p>

              {/* Target dimension preview */}
              {Object.keys(result.target_dimensions).length > 0 && (
                <div className="mt-4 space-y-2">
                  <p className="text-[10px] font-mono text-muted-foreground uppercase">Target Profile</p>
                  {(Object.entries(result.target_dimensions) as [string, number][]).map(([k, v]) => (
                    <DimensionBar key={k} label={k} value={v} color={DIMENSION_COLORS[k] ?? "bg-primary"} />
                  ))}
                </div>
              )}
            </div>

            {/* Matches */}
            <div className="space-y-3">
              <p className="text-xs font-mono uppercase text-muted-foreground">Best Matches</p>
              {result.matches.map((match, idx) => (
                <div key={match.voice_id}
                  className={`rounded-xl border p-4 space-y-3 ${
                    idx === 0 ? "border-primary/30 bg-primary/5" : "border-border/50 bg-card"
                  }`}>
                  <div className="flex items-center gap-2">
                    {idx === 0 && <Star className="w-3.5 h-3.5" style={{ color: "var(--gilt)", fill: "var(--gilt)" }} />}
                    <span className="font-semibold text-sm">{match.voice?.name ?? match.voice_id}</span>
                    {match.voice?.accent && (
                      <span className="text-[10px] text-muted-foreground capitalize">{match.voice.accent}</span>
                    )}
                    <span className="ml-auto text-xs font-mono font-bold" style={{ color: "var(--green-2)" }}>
                      {match.match_score}%
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed">{match.why}</p>

                  {match.voice?.dimensions && (
                    <div className="space-y-1.5">
                      {(["warmth", "authority", "gravitas"] as const).map(k => (
                        <DimensionBar key={k} label={k} value={match.voice!.dimensions![k]} color={DIMENSION_COLORS[k]} />
                      ))}
                    </div>
                  )}

                  <div className="flex gap-2">
                    <button
                      onClick={() => globalAudio.playVoiceSample(match.voice_id)}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                        globalAudio.playingId === match.voice_id
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted hover:bg-muted/80 text-muted-foreground"
                      }`}
                    >
                      {globalAudio.playingId === match.voice_id
                        ? <><Pause className="w-3 h-3" /> Stop</>
                        : <><Play className="w-3 h-3" /> Preview</>}
                    </button>
                    {match.voice && (
                      <Button size="sm" variant="outline" className="ml-auto text-xs h-7 gap-1"
                        onClick={() => onUseVoice(match.voice)}>
                        <CheckCircle2 className="w-3 h-3" />
                        Use Voice
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!loading && !result && (
          <div className="flex flex-col items-center justify-center h-64 text-center px-8 text-muted-foreground">
            <Wand2 className="w-10 h-10 mb-3 opacity-20" />
            <p className="text-sm font-medium">Voice Designer</p>
            <p className="text-xs mt-1 leading-relaxed">
              Describe the narrator you want in plain English. The AI maps your
              description to voice dimensions and finds the closest real match in the catalog.
            </p>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

// ── Audiobook Tab ─────────────────────────────────────────────────────────────

function AudiobookTab({
  selectedVoice,
  voices,
  globalAudio,
}: {
  selectedVoice: VoiceEntry | null;
  voices: VoiceEntry[];
  globalAudio: ReturnType<typeof useGlobalAudio>;
}) {
  const { data: worksResp } = useListWorks();
  const works = (worksResp as any)?.works ?? [];

  const { data: libResp } = useListLibrary({ readiness: "ready" } as any, { query: { staleTime: 20_000 } } as any);
  const docs = ((libResp as any)?.documents ?? []) as any[];

  const [mode, setMode] = useState<"work" | "document">("work");
  const [workId, setWorkId]   = useState("");
  const [docId, setDocId]     = useState("");
  const [voiceId, setVoiceId] = useState(selectedVoice?.id ?? "bm_george");
  const [speed, setSpeed]     = useState(1.0);
  const [credits, setCredits] = useState(true);
  const [acx, setAcx]         = useState(true);
  const [loading, setLoading] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [audioName, setAudioName] = useState("audiobook.mp3");
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Async document-TTS job state
  const [vsAbJobId, setVsAbJobId] = useState<string | null>(null);
  const [vsAbSegsDone, setVsAbSegsDone] = useState(0);
  const [vsAbSegsTotal, setVsAbSegsTotal] = useState(0);
  const vsAbJobIdRef = useRef<string | null>(null);
  const vsAbPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Per-chapter voice casting ────────────────────────────────────────────────
  const [castDocs, setCastDocs] = useState<{ id: string; title: string }[]>([]);
  const [castMap, setCastMap] = useState<Record<string, string>>({});
  const [castDirty, setCastDirty] = useState(false);
  const [castSaving, setCastSaving] = useState(false);

  useEffect(() => {
    if (!workId || mode !== "work") {
      setCastDocs([]); setCastMap({}); setCastDirty(false);
      return;
    }
    let cancelled = false;
    apiFetch(`${BASE}/studio/works/${workId}/casting`)
      .then(async r => {
        if (cancelled || !r.ok) return;
        const data = await r.json();
        if (cancelled) return;
        setCastDocs(data.documents ?? []);
        setCastMap(data.sections ?? {});
        setCastDirty(false);
      })
      .catch(() => {/* casting is optional — narrator voice still works */});
    return () => { cancelled = true; };
  }, [workId, mode]);

  async function handleSaveCasting() {
    setCastSaving(true);
    try {
      const resp = await apiFetch(`${BASE}/studio/works/${workId}/casting`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sections: castMap }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      setCastDirty(false);
      toast.success("Chapter voices saved");
    } catch (e: any) {
      toast.error(`Couldn't save chapter voices: ${e.message}`);
    } finally {
      setCastSaving(false);
    }
  }

  // ── Proactive AI voice suggestion ────────────────────────────────────────────
  // Fires in the background whenever a Work is selected; the card appears
  // only when the result is ready — nothing blocks the form.
  const [suggestion, setSuggestion] = useState<Recommendation | null>(null);

  useEffect(() => {
    if (!workId || mode !== "work") {
      setSuggestion(null);
      return;
    }
    let cancelled = false;
    setSuggestion(null);
    apiFetch(`${BASE}/studio/voices/recommend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ work_id: workId, top_n: 1 }),
    })
      .then(async r => {
        if (cancelled || !r.ok) return;
        const data = await r.json();
        const top = data.recommendations?.[0] ?? null;
        if (!cancelled) setSuggestion(top);
      })
      .catch(() => {/* silently suppress — suggestion is optional */});
    return () => { cancelled = true; };
  }, [workId, mode]);

  // Sync voice picker when parent selects a voice from another tab
  useEffect(() => {
    if (selectedVoice) setVoiceId(selectedVoice.id);
  }, [selectedVoice?.id]);

  const selectedVoiceMeta = voices.find(v => v.id === voiceId);

  // Cleanup: cancel any in-flight document job when the component unmounts.
  useEffect(() => {
    return () => {
      if (vsAbPollRef.current) { clearInterval(vsAbPollRef.current); vsAbPollRef.current = null; }
      if (vsAbJobIdRef.current) {
        apiFetch(`${BASE}/studio/tts/document/${vsAbJobIdRef.current}`, { method: "DELETE" }).catch(() => {});
        vsAbJobIdRef.current = null;
      }
    };
  }, []);

  async function handleGenerate() {
    const hasTarget = mode === "work" ? !!workId : !!docId;
    if (!hasTarget) return;
    setLoading(true);
    setAudioUrl(null);
    setPlaying(false);

    if (mode === "work") {
      // Work audiobook: synchronous endpoint (tts/work unchanged)
      try {
        const resp = await apiFetch(`${BASE}/studio/tts/work`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            work_id: workId, voice: voiceId, speed,
            include_credits: credits, acx_mastering: acx,
          }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
        }
        const blob = await resp.blob();
        const url  = URL.createObjectURL(blob);
        const name = `${works.find((w: any) => w.id === workId)?.title ?? "audiobook"}.mp3`;
        setAudioUrl(url);
        setAudioName(name);
        toast.success("Audiobook ready — tap play below");
      } catch (e: any) {
        toast.error(`Audiobook failed: ${e.message}`, { duration: 10_000 });
      } finally {
        setLoading(false);
      }
      return;
    }

    // Document mode: async job flow
    const toastId = toast.loading("Starting audiobook generation…");
    try {
      const resp = await apiFetch(`${BASE}/studio/tts/document`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: docId, voice: voiceId, speed, acx_mastering: acx }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const { job_id, total_segments } = await resp.json();
      toast.dismiss(toastId);
      vsAbJobIdRef.current = job_id;
      setVsAbJobId(job_id);
      setVsAbSegsTotal(total_segments);
      setVsAbSegsDone(0);
      // loading stays true while polling
      vsAbPollRef.current = setInterval(async () => {
        try {
          const sr = await apiFetch(`${BASE}/studio/tts/document/${job_id}/status`);
          if (!sr.ok) {
            if (sr.status === 404) {
              clearInterval(vsAbPollRef.current!); vsAbPollRef.current = null;
              vsAbJobIdRef.current = null; setVsAbJobId(null); setLoading(false);
              toast.error("Server restarted — audiobook job was lost. Please try again.");
            }
            return;
          }
          const status = await sr.json();
          setVsAbSegsDone(status.segments_done ?? 0);
          const terminal = ["done", "failed", "cancelled"].includes(status.state);
          if (terminal) {
            clearInterval(vsAbPollRef.current!); vsAbPollRef.current = null;
            vsAbJobIdRef.current = null; setVsAbJobId(null); setLoading(false);
            if (status.state === "done") {
              const serveUrl = `${BASE}/studio/outputs/serve?path=${encodeURIComponent(status.mp3_path)}`;
              setAudioUrl(serveUrl);
              setAudioName(`${docs.find((d: any) => d.id === docId)?.title ?? "audiobook"}.mp3`);
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

  async function handleCancelDocGenerate() {
    if (!vsAbJobIdRef.current) return;
    try {
      await apiFetch(`${BASE}/studio/tts/document/${vsAbJobIdRef.current}`, { method: "DELETE" });
    } catch { /* best-effort */ }
  }

  function togglePlay() {
    const el = audioRef.current;
    if (!el) return;
    if (playing) { el.pause(); setPlaying(false); }
    else { el.play().catch(() => {}); setPlaying(true); }
  }

  const hasTarget = mode === "work" ? !!workId : !!docId;

  return (
    <ScrollArea className="h-full">
      <div className="p-5 space-y-6 max-w-2xl">

        {/* Source selector */}
        <div className="space-y-3">
          <p className="text-xs font-mono uppercase text-muted-foreground">Source</p>
          <div className="grid grid-cols-2 gap-2">
            {(["work", "document"] as const).map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`flex items-center gap-2 p-3 rounded-xl border text-sm transition-all ${
                  mode === m
                    ? "border-primary bg-primary/5 text-primary font-medium"
                    : "border-border/50 text-muted-foreground hover:border-border"
                }`}
              >
                {m === "work" ? <BookHeadphones className="w-4 h-4" /> : <FileText className="w-4 h-4" />}
                {m === "work" ? "Entire Work" : "Single Document"}
              </button>
            ))}
          </div>

          {mode === "work" ? (
            <Select value={workId} onValueChange={setWorkId}>
              <SelectTrigger>
                <SelectValue placeholder="Select a Work…" />
              </SelectTrigger>
              <SelectContent>
                {works.map((w: any) => (
                  <SelectItem key={w.id} value={w.id}>{w.title}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Select value={docId} onValueChange={setDocId}>
              <SelectTrigger>
                <SelectValue placeholder="Select a document…" />
              </SelectTrigger>
              <SelectContent>
                {docs.map((d: any) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.title || d.source?.split("/").pop() || d.id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        {/* ── AI suggests card — appears silently when a Work is chosen ── */}
        {mode === "work" && suggestion && suggestion.voice && (
          <div className="rounded-xl border p-3.5 space-y-2.5" style={{ borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" }}>
            {/* Header */}
            <div className="flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gilt)" }} />
              <span className="text-[10px] font-mono uppercase tracking-wide" style={{ color: "var(--gilt)" }}>
                AI suggests
              </span>
              <span className="ml-auto text-[10px] font-mono font-bold" style={{ color: "var(--green-2)" }}>
                {suggestion.score}% match
              </span>
            </div>

            {/* Voice identity */}
            <div className="flex items-center gap-2 min-w-0">
              <div className="flex-1 min-w-0">
                <span className="text-sm font-semibold">{suggestion.voice.name}</span>
                {suggestion.voice.accent && (
                  <span className="text-xs text-muted-foreground ml-1.5 capitalize">
                    {suggestion.voice.accent}
                  </span>
                )}
                {suggestion.voice.gender && (
                  <span className="text-xs text-muted-foreground ml-0.5">
                    · {suggestion.voice.gender}
                  </span>
                )}
              </div>

              {/* Sample preview button */}
              <button
                onClick={() => globalAudio.playVoiceSample(suggestion.voice_id)}
                title={globalAudio.playingId === suggestion.voice_id ? "Stop" : "Preview sample"}
                className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 transition-colors ${
                  globalAudio.playingId === suggestion.voice_id
                    ? ""
                    : "text-muted-foreground"
                }`}
              >
                {globalAudio.loadingId === suggestion.voice_id ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : globalAudio.playingId === suggestion.voice_id ? (
                  <Pause className="w-3 h-3" />
                ) : (
                  <Play className="w-3 h-3 ml-px" />
                )}
              </button>
            </div>

            {/* One-line rationale */}
            <p className="text-xs text-muted-foreground leading-relaxed">
              {suggestion.headline}
            </p>

            {/* CTA */}
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1" style={{ borderColor: "var(--gilt-line)", color: "var(--gilt)" }}
              onClick={() => setVoiceId(suggestion.voice_id)}
              disabled={voiceId === suggestion.voice_id}
            >
              <CheckCircle2 className="w-3 h-3" />
              {voiceId === suggestion.voice_id ? "Voice selected ✓" : "Use this voice"}
            </Button>
          </div>
        )}

        {/* Voice selector */}
        <div className="space-y-3">
          <p className="text-xs font-mono uppercase text-muted-foreground">Narrator Voice</p>
          <Select value={voiceId} onValueChange={setVoiceId}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {voices.map(v => (
                <SelectItem key={v.id} value={v.id}>
                  <span className="flex items-center gap-2">
                    <span>{v.name}</span>
                    {v.accent && (
                      <span className="text-xs text-muted-foreground capitalize">{v.accent}</span>
                    )}
                    {v.builtin && (
                      <span className="text-[10px]" style={{ color: "var(--green-2)" }}>✓</span>
                    )}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedVoiceMeta?.description && (
            <p className="text-xs text-muted-foreground leading-relaxed">
              {selectedVoiceMeta.description}
            </p>
          )}
        </div>

        {/* Per-chapter voice casting */}
        {mode === "work" && workId && castDocs.length > 0 && (
          <div className="space-y-3">
            <p className="text-xs font-mono uppercase text-muted-foreground">Chapter Voices</p>
            <div className="rounded-xl border border-border/50 divide-y divide-border/40">
              {castDocs.map(d => (
                <div key={d.id} className="flex items-center gap-3 p-2.5">
                  <span className="text-sm flex-1 min-w-0 truncate" title={d.title}>{d.title}</span>
                  <Select
                    value={castMap[d.id] ?? "__default"}
                    onValueChange={v => {
                      setCastMap(prev => {
                        const next = { ...prev };
                        if (v === "__default") delete next[d.id];
                        else next[d.id] = v;
                        return next;
                      });
                      setCastDirty(true);
                    }}
                  >
                    <SelectTrigger className="w-44 h-8 text-xs shrink-0">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default">Narrator (default)</SelectItem>
                      {voices.map(v => (
                        <SelectItem key={v.id} value={v.id}>{v.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                Give chapters their own voice — unset chapters use the narrator above.
              </p>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs shrink-0"
                onClick={handleSaveCasting}
                disabled={!castDirty || castSaving}
              >
                {castSaving ? <Loader2 className="w-3 h-3 animate-spin" /> : castDirty ? "Save voices" : "Saved ✓"}
              </Button>
            </div>
          </div>
        )}

        {/* Speed */}
        <div className="space-y-3">
          <p className="text-xs font-mono uppercase text-muted-foreground">
            Narration Speed — {speed.toFixed(1)}×
          </p>
          <Slider min={0.5} max={2.0} step={0.1} value={[speed]}
            onValueChange={([v]) => setSpeed(v)} />
          <div className="flex justify-between text-[10px] font-mono text-muted-foreground">
            <span>0.5× Slow</span><span>1.0× Normal</span><span>2.0× Fast</span>
          </div>
        </div>

        {/* Options */}
        {mode === "work" && (
          <div className="space-y-2">
            <p className="text-xs font-mono uppercase text-muted-foreground">Options</p>
            <div className="space-y-2">
              {([
                { key: "credits", label: "Opening & closing credits", desc: "ACX-style title + narrator announcement", value: credits, set: setCredits },
                { key: "acx", label: "Loudness mastering", desc: "Two-pass normalization to -23 LUFS, -3 dBTP peak ceiling, 192 kbps", value: acx, set: setAcx },
              ] as const).map(opt => (
                <label key={opt.key}
                  className="flex items-start gap-3 p-3 rounded-xl border border-border/50 hover:border-border cursor-pointer transition-colors">
                  <input
                    type="checkbox"
                    checked={opt.value}
                    onChange={e => opt.set(e.target.checked)}
                    className="mt-0.5"
                  />
                  <div>
                    <p className="text-sm font-medium leading-tight">{opt.label}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{opt.desc}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>
        )}

        {/* TTS engine badge */}
        <_AudiobookEngineBadge />

        {/* Generate button / progress bar */}
        {mode === "document" && vsAbJobId ? (
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="flex-1 space-y-1.5">
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <Loader2 className="w-3 h-3 animate-spin" /> Generating audiobook…
                  </span>
                  <span>{vsAbSegsDone}/{vsAbSegsTotal}</span>
                </div>
                <div className="h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full transition-all duration-500"
                    style={{ width: vsAbSegsTotal > 0 ? `${Math.round((vsAbSegsDone / vsAbSegsTotal) * 100)}%` : "0%" }}
                  />
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleCancelDocGenerate}
                className="shrink-0 h-8 text-destructive border-destructive/40 hover:bg-destructive/10"
              >
                <X className="w-3.5 h-3.5 mr-1" /> Cancel
              </Button>
            </div>
          </div>
        ) : (
          <Button
            onClick={handleGenerate}
            disabled={!hasTarget || loading}
            className="w-full gap-2 h-11"
            size="lg"
          >
            {loading ? (
              <><Loader2 className="w-4 h-4 animate-spin" /> Generating audiobook… (may take several minutes)</>
            ) : (
              <><BookHeadphones className="w-5 h-5" /> Generate Audiobook</>
            )}
          </Button>
        )}

        {mode === "work" && (
          <p className="text-xs text-muted-foreground text-center -mt-2">
            Processes all ready documents in the Work, chapter by chapter.
            Large books may take 10–30 minutes.
          </p>
        )}

        {/* Audio player */}
        {audioUrl && (
          <div className="rounded-xl border border-border/50 bg-muted/10 p-4 space-y-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4" style={{ color: "var(--green-2)" }} />
              <span className="text-sm font-medium">Audiobook ready</span>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={togglePlay}
                className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 ${
                  playing ? "bg-primary text-primary-foreground" : "bg-muted hover:bg-muted/80"
                }`}
              >
                {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </button>
              <audio
                ref={audioRef}
                src={audioUrl}
                onEnded={() => setPlaying(false)}
                onPause={() => setPlaying(false)}
                onPlay={() => setPlaying(true)}
                controls
                className="flex-1 h-8"
                style={{ minWidth: 0 }}
              />
              <button
                onClick={() => {
                  const a = document.createElement("a");
                  a.href = audioUrl;
                  a.download = audioName;
                  a.click();
                }}
                className="w-9 h-9 flex items-center justify-center rounded-lg border border-border/50 hover:bg-muted transition-colors"
                title="Download"
              >
                <Download className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
          </div>
        )}
      </div>
    </ScrollArea>
  );
}

// ── Main Voice Studio ─────────────────────────────────────────────────────────

export function VoiceStudio() {
  const { data: voicesResp, isLoading, isError } = useListVoices();
  const voices: VoiceEntry[] = (voicesResp as any)?.voices ?? [];

  const [activeTab, setActiveTab] = useState<"browse" | "recommend" | "design" | "clone" | "audiobook">("browse");
  const [selectedVoice, setSelectedVoice] = useState<VoiceEntry | null>(null);
  const [audiobookVoice, setAudiobookVoice] = useState<VoiceEntry | null>(null);

  const globalAudio = useGlobalAudio();

  function handleSelectVoice(v: VoiceEntry) {
    setSelectedVoice(prev => prev?.id === v.id ? null : v);
  }

  function handleUseVoice(v: VoiceEntry) {
    setAudiobookVoice(v);
    setActiveTab("audiobook");
    toast.success(`${v.name} selected — set up your audiobook below`);
  }

  const TABS = [
    { id: "browse",    label: "Browse Voices", icon: Volume2 },
    { id: "recommend", label: "AI Recommend",  icon: Sparkles },
    { id: "design",    label: "Voice Designer",icon: Wand2 },
    { id: "clone",     label: "Clone Voice",   icon: Mic },
    { id: "audiobook", label: "Build Audiobook",icon: BookHeadphones },
  ] as const;

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 p-4">
        {Array.from({ length: 9 }).map((_, i) => (
          <Skeleton key={i} className="h-48 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center text-muted-foreground">
        <AlertCircle className="w-8 h-8 mb-3 text-destructive/60" />
        <p className="text-sm">Could not load voice catalog</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full" style={{ minHeight: 0 }}>
      {/* Tab bar */}
      <div className="flex items-center gap-0 border-b border-border/50 px-4 overflow-x-auto shrink-0">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`
                flex items-center gap-1.5 px-3 py-3 text-sm border-b-2 transition-colors whitespace-nowrap shrink-0
                ${activeTab === tab.id
                  ? "border-primary text-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
                }
              `}
            >
              <Icon className="w-3.5 h-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1" style={{ minHeight: 0, overflow: "hidden" }}>
        {activeTab === "browse" && (
          <BrowseTab
            voices={voices}
            selectedVoice={selectedVoice}
            onSelectVoice={handleSelectVoice}
            onUseVoice={handleUseVoice}
            globalAudio={globalAudio}
          />
        )}
        {activeTab === "recommend" && (
          <RecommendTab
            onUseVoice={handleUseVoice}
            globalAudio={globalAudio}
          />
        )}
        {activeTab === "design" && (
          <DesignTab
            onUseVoice={handleUseVoice}
            globalAudio={globalAudio}
          />
        )}
        {activeTab === "clone" && (
          <CloneTab onUseVoice={handleUseVoice} />
        )}
        {activeTab === "audiobook" && (
          <AudiobookTab
            selectedVoice={audiobookVoice}
            voices={voices}
            globalAudio={globalAudio}
          />
        )}
      </div>
    </div>
  );
}
