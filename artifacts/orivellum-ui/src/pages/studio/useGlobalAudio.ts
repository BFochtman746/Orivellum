/**
 * Shared single-audio-element preview hook for the Voice Studio.
 *
 * One <audio> element for the whole studio — playing any sample stops the
 * previous one. A monotonic request token guards every async step so rapid
 * clicks can never let a stale fetch overwrite the current selection.
 */
import { useRef, useState, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export function useGlobalAudio() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  // Monotonic request token: only the most recent preview request may touch
  // the audio element or the loading/playing state. Without this, two rapid
  // clicks on different samples could let the earlier (slower) fetch
  // overwrite the later selection or clear its spinner.
  const reqGenRef = useRef(0);
  // Last object URL handed to the audio element — revoked when replaced so
  // repeated previews don't leak blobs for the whole session.
  const lastUrlRef = useRef<string | null>(null);
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

    const token = ++reqGenRef.current;
    el.pause();
    setPlayingId(null);
    setLoadingId(voiceId);

    try {
      const resp = await apiFetch(`${BASE}/studio/voices/${encodeURIComponent(voiceId)}/sample`);
      if (token !== reqGenRef.current) return; // superseded by a newer click
      if (!resp.ok) {
        // Surface the server's explanation (e.g. "the voice sidecar is not
        // running") instead of a generic failure — clone samples especially
        // depend on an external service the user may need to start.
        let msg = "";
        try { msg = (await resp.json())?.detail ?? ""; } catch { /* not JSON */ }
        throw new Error(msg || `HTTP ${resp.status}`);
      }

      // Capture which engine generated this sample (always neural now — the
      // robotic espeak fallback was removed by policy).
      const engine = resp.headers.get("X-TTS-Engine") ?? "kokoro";
      setSampleEngines(prev =>
        prev[voiceId] === engine ? prev : { ...prev, [voiceId]: engine }
      );

      const blob = await resp.blob();
      if (token !== reqGenRef.current) return;
      if (lastUrlRef.current) URL.revokeObjectURL(lastUrlRef.current);
      const url = URL.createObjectURL(blob);
      lastUrlRef.current = url;
      el.src = url;
      await el.play();
      if (token === reqGenRef.current) setPlayingId(voiceId);
    } catch (e: any) {
      if (token !== reqGenRef.current) return; // a stale request's failure is noise
      const detail = typeof e?.message === "string" && e.message && !/^HTTP \d+$/.test(e.message)
        ? e.message
        : "Could not load sample for this voice";
      toast.error(detail);
    } finally {
      if (token === reqGenRef.current) setLoadingId(null);
    }
  }, [playingId]);

  // One-off "try your own line" preview: synthesizes arbitrary text with the
  // selected voice via POST /studio/tts. Deliberately NOT the /sample endpoint —
  // custom lines are never written to the voice_samples cache. Clone voices go
  // through the premium sidecar inside the same route and fail closed (the
  // server's 503 detail is surfaced verbatim).
  const playCustomLine = useCallback(async (voiceId: string, text: string) => {
    const el = audioRef.current;
    if (!el) return;
    const key = `custom:${voiceId}`;

    if (playingId === key) {
      el.pause();
      setPlayingId(null);
      return;
    }

    const token = ++reqGenRef.current;
    el.pause();
    setPlayingId(null);
    setLoadingId(key);

    try {
      const resp = await apiFetch(`${BASE}/studio/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // "draft" keeps catalog previews instant (Kokoro ~1 s); clones always
        // route to the premium engine server-side regardless of quality.
        body: JSON.stringify({ text: text.slice(0, 200), voice: voiceId, quality: "draft" }),
      });
      if (token !== reqGenRef.current) return;
      if (!resp.ok) {
        let msg = "";
        try { msg = (await resp.json())?.detail ?? ""; } catch { /* not JSON */ }
        throw new Error(msg || `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      if (token !== reqGenRef.current) return;
      if (lastUrlRef.current) URL.revokeObjectURL(lastUrlRef.current);
      const url = URL.createObjectURL(blob);
      lastUrlRef.current = url;
      el.src = url;
      await el.play();
      if (token === reqGenRef.current) setPlayingId(key);
    } catch (e: any) {
      if (token !== reqGenRef.current) return;
      const detail = typeof e?.message === "string" && e.message && !/^HTTP \d+$/.test(e.message)
        ? e.message
        : "Could not synthesize your line with this voice";
      toast.error(detail);
    } finally {
      if (token === reqGenRef.current) setLoadingId(null);
    }
  }, [playingId]);

  const stopAll = useCallback(() => {
    // Invalidate any in-flight preview fetch too — stopping means nothing
    // already requested should start playing afterwards.
    reqGenRef.current++;
    audioRef.current?.pause();
    setPlayingId(null);
    setLoadingId(null);
  }, []);

  return { playingId, loadingId, playVoiceSample, playCustomLine, stopAll, sampleEngines };
}
