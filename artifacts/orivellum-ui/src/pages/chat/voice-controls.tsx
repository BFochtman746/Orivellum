/**
 * VoiceControls — microphone capture + hands-free toggle for the chat composer.
 *
 * Capture rules:
 *  - getUserMedia requires a secure context. The PWA often runs over plain
 *    HTTP via Tailscale, where isSecureContext is false — in that case we show
 *    a muted mic button that opens an explanation dialog (how to get HTTPS)
 *    instead of failing silently.
 *  - Recording is tap-to-toggle: tap to start, tap to stop. Stopping uploads
 *    the clip to POST /studio/voice/transcribe (synchronous, short clips only)
 *    and hands the transcript to the parent.
 *  - In hands-free mode the stop-tap also calls onPrimeSpeech() synchronously
 *    (inside the gesture) so the parent can unlock spoken-reply autoplay.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Mic, MicOff, Loader2, Headphones } from "lucide-react";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

const API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

/** Hard cap on a single utterance — long recordings belong in Studio. */
const MAX_RECORD_MS = 120_000;
/** Clips smaller than this are almost certainly an accidental double-tap. */
const MIN_CLIP_BYTES = 1_500;

/** Pick the best MediaRecorder container the browser supports. */
function pickMime(): { mime: string; filename: string } | null {
  if (typeof MediaRecorder === "undefined") return null;
  const candidates: Array<[string, string]> = [
    ["audio/webm;codecs=opus", "clip.webm"],
    ["audio/webm",             "clip.webm"],
    ["audio/mp4",              "clip.mp4"],   // Safari
    ["audio/ogg;codecs=opus",  "clip.ogg"],
  ];
  for (const [mime, filename] of candidates) {
    try { if (MediaRecorder.isTypeSupported(mime)) return { mime, filename }; } catch { /* ignore */ }
  }
  return null;
}

type Phase = "idle" | "recording" | "transcribing";

export interface VoiceControlsProps {
  /** Composer is busy (sending/importing) — block starting a new recording. */
  disabled?: boolean;
  handsFree: boolean;
  onHandsFreeChange: (v: boolean) => void;
  /** Transcript result. Empty string means "nothing usable" (silence, error) —
   *  the parent should clean up any primed speech session and do nothing else. */
  onTranscript: (text: string) => void;
  /** Called synchronously inside the stop-tap gesture, hands-free mode only,
   *  BEFORE transcription starts — lets the parent unlock reply audio. */
  onPrimeSpeech?: () => void;
  /** Increment to auto-start listening (hands-free conversation loop). */
  autoListenNonce?: number;
}

export function VoiceControls({
  disabled, handsFree, onHandsFreeChange, onTranscript, onPrimeSpeech, autoListenNonce,
}: VoiceControlsProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [elapsedS, setElapsedS] = useState(0);
  const [insecureOpen, setInsecureOpen] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mimeRef = useRef<{ mime: string; filename: string } | null>(null);
  const maxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // True when the current recording should be discarded (mode turned off etc.)
  const discardRef = useRef(false);

  const secure = typeof window !== "undefined" && window.isSecureContext;
  const hasMediaDevices =
    typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
  const canCapture = secure && hasMediaDevices && pickMime() !== null;

  // Speech-to-text engine availability (AI-server Whisper or local faster-whisper).
  const { data: mediaStatus } = useQuery({
    queryKey: ["studio-status-voice"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/studio/status`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    enabled: canCapture,
    staleTime: 60_000,
    retry: 1,
  });
  // Unknown (still loading) counts as available — the endpoint gives a clear
  // 503 if it turns out not to be.
  const sttAvailable: boolean = mediaStatus?.asr?.available !== false;

  const clearTimers = () => {
    if (maxTimerRef.current) { clearTimeout(maxTimerRef.current); maxTimerRef.current = null; }
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
  };

  const releaseStream = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  };

  const transcribe = useCallback(async (blob: Blob, filename: string) => {
    setPhase("transcribing");
    try {
      const form = new FormData();
      form.append("file", blob, filename);
      const resp = await apiFetch(`${API_BASE}/studio/voice/transcribe`, {
        method: "POST",
        body: form,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const detail = (err as any).detail ?? `HTTP ${resp.status}`;
        toast.error(
          resp.status === 503
            ? "Transcription isn't available — start the AI server or install faster-whisper (System page)."
            : `Could not transcribe: ${detail}`,
        );
        onTranscript("");
        return;
      }
      const data = await resp.json();
      const text = (data.text ?? "").trim();
      if (!text) {
        toast.info("Didn't catch any speech — try again closer to the mic.");
        onTranscript("");
        return;
      }
      onTranscript(text);
    } catch {
      toast.error("Could not reach the server to transcribe.");
      onTranscript("");
    } finally {
      setPhase("idle");
    }
  }, [onTranscript]);

  const startRecording = useCallback(async () => {
    if (phase !== "idle" || disabled) return;
    // Contract: every failure path that won't produce a transcript emits
    // onTranscript("") so the parent's voice state machine always terminates.
    if (!sttAvailable) {
      toast.error("Speech-to-text isn't set up — start the AI server or install faster-whisper (System page).");
      onTranscript("");
      return;
    }
    const picked = pickMime();
    if (!picked) {
      toast.error("This browser can't record audio.");
      onTranscript("");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      mimeRef.current = picked;
      chunksRef.current = [];
      discardRef.current = false;
      const rec = new MediaRecorder(stream, { mimeType: picked.mime });
      recorderRef.current = rec;
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = () => {
        clearTimers();
        releaseStream();
        const blob = new Blob(chunksRef.current, { type: picked.mime });
        chunksRef.current = [];
        recorderRef.current = null;
        if (discardRef.current) { setPhase("idle"); return; }
        if (blob.size < MIN_CLIP_BYTES) {
          toast.info("Recording was too short — hold on a moment before stopping.");
          setPhase("idle");
          onTranscript("");
          return;
        }
        void transcribe(blob, picked.filename);
      };
      rec.start(250);
      setPhase("recording");
      setElapsedS(0);
      const startedAt = Date.now();
      tickRef.current = setInterval(
        () => setElapsedS(Math.floor((Date.now() - startedAt) / 1000)), 1000);
      maxTimerRef.current = setTimeout(() => {
        // Auto-stop at the cap. No user gesture here, so we do NOT prime
        // speech — the reply may need a manual play tap in that edge case.
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      }, MAX_RECORD_MS);
    } catch (e: any) {
      releaseStream();
      toast.error(
        e?.name === "NotAllowedError"
          ? "Microphone access was denied — allow the mic for this site in your browser settings."
          : `Could not start the microphone: ${e?.message ?? "unknown error"}`,
      );
      onTranscript("");
    }
  }, [phase, disabled, sttAvailable, transcribe, onTranscript]);

  /** Tap handler — toggles recording. Runs inside the user gesture. */
  const handleMicTap = useCallback(() => {
    if (!canCapture) { setInsecureOpen(true); return; }
    if (phase === "recording") {
      // Prime reply audio inside THIS gesture (hands-free only) before any
      // async work — autoplay unlock requires a synchronous play() call.
      if (handsFree) onPrimeSpeech?.();
      recorderRef.current?.stop();
    } else if (phase === "idle") {
      void startRecording();
    }
  }, [canCapture, phase, handsFree, onPrimeSpeech, startRecording]);

  // Hands-free loop: parent bumps the nonce when the spoken reply finishes.
  const lastNonceRef = useRef(autoListenNonce ?? 0);
  useEffect(() => {
    const nonce = autoListenNonce ?? 0;
    if (nonce === lastNonceRef.current) return;
    lastNonceRef.current = nonce;
    if (handsFree && canCapture && phase === "idle" && !disabled) {
      void startRecording();
    }
  }, [autoListenNonce, handsFree, canCapture, phase, disabled, startRecording]);

  // Turning hands-free off mid-recording discards the take; unmount cleans up.
  useEffect(() => () => {
    discardRef.current = true;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    clearTimers();
    releaseStream();
  }, []);

  // Hide entirely only when the browser genuinely has no capture API in a
  // secure context (nothing actionable to explain).
  if (secure && !hasMediaDevices) return null;

  const micTitle = !canCapture
    ? "Voice input needs HTTPS — tap for details"
    : phase === "recording"
      ? "Stop and transcribe"
      : phase === "transcribing"
        ? "Transcribing…"
        : handsFree
          ? "Tap to talk — reply will be spoken"
          : "Dictate a message";

  return (
    <>
      {/* Mic button */}
      <button
        type="button"
        onClick={handleMicTap}
        disabled={phase === "transcribing" || (!!disabled && phase !== "recording")}
        title={micTitle}
        data-testid="button-voice-mic"
        className={`chat-icon-btn h-8 rounded flex items-center justify-center gap-1 transition-colors px-1.5
          ${phase === "recording"
            ? "text-destructive bg-destructive/10 border border-destructive/40 animate-pulse"
            : !canCapture
              ? "text-muted-foreground/30 hover:text-muted-foreground/60"
              : "text-muted-foreground/50 hover:text-muted-foreground"}`}
      >
        {phase === "transcribing"
          ? <Loader2 className="w-4 h-4 animate-spin" />
          : !canCapture
            ? <MicOff className="w-4 h-4" />
            : <Mic className="w-4 h-4" />}
        {phase === "recording" && (
          <span className="text-[10px] font-mono tabular-nums">{elapsedS}s</span>
        )}
      </button>

      {/* Hands-free toggle — only when capture is possible */}
      {canCapture && (
        <button
          type="button"
          onClick={() => onHandsFreeChange(!handsFree)}
          title={handsFree
            ? "Hands-free voice chat is ON — messages auto-send and replies are spoken (tap to turn off)"
            : "Turn on hands-free voice chat — speak, hear the reply, then it listens again"}
          data-testid="button-voice-handsfree"
          className={`chat-icon-btn h-8 w-8 rounded flex items-center justify-center transition-colors
            ${handsFree ? "text-primary bg-primary/10 border border-primary/30" : "text-muted-foreground/50 hover:text-muted-foreground"}`}
        >
          <Headphones className="w-4 h-4" />
        </button>
      )}

      {/* Insecure-context explanation */}
      <Dialog open={insecureOpen} onOpenChange={setInsecureOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 font-serif">
              <MicOff className="w-4 h-4" /> Microphone needs a secure connection
            </DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-3 pt-2 text-sm text-muted-foreground">
                <p>
                  Browsers only allow microphone access on secure (HTTPS) pages.
                  You're viewing Orivellum over plain HTTP, so voice input is
                  blocked by the browser — not by Orivellum.
                </p>
                <div>
                  <p className="font-medium text-foreground mb-1">Ways to enable it:</p>
                  <ul className="list-disc pl-5 space-y-1">
                    <li>
                      <span className="text-foreground">Tailscale HTTPS:</span> run{" "}
                      <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">tailscale cert</code>{" "}
                      on the server and open the app via its{" "}
                      <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">https://…ts.net</code> address.
                    </li>
                    <li>
                      <span className="text-foreground">Same machine:</span> open the app at{" "}
                      <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">http://localhost</code>{" "}
                      — localhost counts as secure.
                    </li>
                  </ul>
                </div>
                <p>
                  Everything else keeps working over HTTP — you can still type,
                  and replies can still be read aloud.
                </p>
              </div>
            </DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    </>
  );
}
