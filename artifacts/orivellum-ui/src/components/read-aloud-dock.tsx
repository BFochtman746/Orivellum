/**
 * ReadAloudDock — the compact persistent player docked at the bottom of the
 * viewport whenever a Read Aloud session is active. Rendered once at the app
 * root (inside ReadAloudProvider), so it survives route changes; the <audio>
 * element is ALWAYS mounted (even with no session) so play() can be called
 * synchronously inside a tap gesture (iOS Safari autoplay policy).
 *
 * Styled as an OS-level player surface using semantic accent tokens so it
 * reads as one persistent player over every page.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "wouter";
import {
  Play, Pause, X, Loader2, SkipBack, SkipForward, Settings2, BookHeadphones,
} from "lucide-react";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { useReadAloud, TTS_VOICE_OPTIONS, TTS_SPEED_OPTIONS } from "@/lib/read-aloud";

// Player chrome, expressed as design tokens (never raw literals).
const CHROME = {
  bg: "var(--gd-surface)",
  bgHi: "var(--gd-raised)",
  line: "var(--gd-line)",
  text: "var(--gd-text)",
  muted: "var(--gd-muted)",
  accent: "var(--gd-accent)",
  accentInk: "var(--gd-accent-ink)",
};

export function ReadAloudDock() {
  const ra = useReadAloud();
  const [showSettings, setShowSettings] = useState(false);
  const open = ra.nowPlaying !== null;
  const dockRef = useRef<HTMLDivElement | null>(null);

  // Publish the dock's height as --ra-dock-h so the app shells (.gd-scroll /
  // .gd-content) reserve space and page bottoms (e.g. the chat composer)
  // aren't hidden behind the player.
  useEffect(() => {
    const root = document.documentElement;
    if (!open) {
      root.style.setProperty("--ra-dock-h", "0px");
      return;
    }
    const el = dockRef.current;
    if (!el) return;
    const update = () => root.style.setProperty("--ra-dock-h", `${el.offsetHeight}px`);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.setProperty("--ra-dock-h", "0px");
    };
  }, [open, showSettings]);

  return (
    <>
      {/* Persistent audio element — must stay mounted across sessions */}
      <audio
        ref={ra.audioRef}
        onEnded={ra.onEnded}
        onPlay={ra.onPlay}
        onPause={ra.onPause}
        onError={() => { if (open) ra.onError("Audio playback error"); }}
        className="hidden"
        preload="auto"
      />
      {open && (
        <div
          ref={dockRef}
          data-testid="dock-read-aloud"
          className="fixed inset-x-0 z-40"
          style={{
            // Sit above the mobile bottom tab bar (0px when the rail shows).
            bottom: "var(--shell-tabbar-h, 0px)",
            background: CHROME.bg,
            borderTop: `1px solid ${CHROME.line}`,
            color: CHROME.text,
            // The tab bar already absorbs the safe-area inset when raised.
            paddingBottom:
              "max(0px, calc(env(safe-area-inset-bottom) - var(--shell-tabbar-h, 0px)))",
            boxShadow: "var(--gd-shadow)",
          }}
        >
          <div className="max-w-[1400px] mx-auto px-3 py-2">
            {/* Resume offer — saved position from an earlier listen */}
            {ra.resumeOffer && (
              <div
                className="flex items-center gap-2 flex-wrap mb-2 pb-2 text-xs"
                style={{ borderBottom: `1px solid ${CHROME.line}` }}
                data-testid="row-dock-resume"
              >
                <span className="min-w-0 truncate" style={{ color: CHROME.muted }}>
                  Pick up where you left off — Part {ra.resumeOffer.part + 1} of {ra.chunkCount}
                  {ra.resumeOffer.time > 3 ? ` at ${fmt(ra.resumeOffer.time)}` : ""}?
                </span>
                <button
                  onClick={ra.acceptResume}
                  disabled={ra.loading}
                  data-testid="button-dock-resume"
                  className="px-2.5 rounded font-mono font-semibold disabled:opacity-50 shrink-0"
                  style={{ height: 26, background: CHROME.accent, color: CHROME.accentInk }}
                >
                  Resume
                </button>
                <button
                  onClick={ra.declineResume}
                  data-testid="button-dock-start-over"
                  className="px-2.5 rounded font-mono shrink-0"
                  style={{ height: 26, background: CHROME.bgHi, color: CHROME.muted }}
                >
                  Start from beginning
                </button>
              </div>
            )}
            <div className="flex items-center gap-2">
              {/* Play / pause */}
              <button
                onClick={ra.toggle}
                disabled={ra.loading}
                aria-label={ra.playing ? "Pause" : "Play"}
                data-testid="button-dock-play"
                className="inline-flex items-center justify-center rounded-full shrink-0 disabled:opacity-50"
                style={{ width: 40, height: 40, background: CHROME.accent, color: CHROME.accentInk }}
              >
                {ra.loading
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : ra.playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </button>

              {/* Title + status — links back to the source page */}
              <div className="flex-1 min-w-0">
                {ra.nowPlaying?.href ? (
                  <Link
                    href={ra.nowPlaying.href}
                    className="block text-sm font-medium truncate hover:underline"
                    data-testid="link-dock-source"
                    style={{ color: CHROME.text }}
                  >
                    {ra.nowPlaying.title}
                  </Link>
                ) : (
                  <p className="text-sm font-medium truncate">{ra.nowPlaying?.title}</p>
                )}
                <p className="text-[10px] font-mono truncate" style={{ color: CHROME.muted }}>
                  <BookHeadphones className="w-3 h-3 inline -mt-0.5 mr-1" aria-hidden />
                  {ra.loading
                    ? "Preparing audio…"
                    : ra.chunkCount > 1
                      ? `Part ${ra.index + 1} of ${ra.chunkCount}`
                      : ra.playing ? "Playing" : "Paused"}
                </p>
              </div>

              {/* Part skip (TTS sessions with multiple parts) */}
              {ra.chunkCount > 1 && (
                <div className="flex items-center gap-0.5 shrink-0">
                  <button
                    onClick={() => ra.goToPart(ra.index - 1, ra.playing)}
                    disabled={ra.loading || ra.index === 0}
                    aria-label="Previous part"
                    data-testid="button-dock-prev"
                    className="inline-flex items-center justify-center rounded disabled:opacity-30"
                    style={{ width: 34, height: 34, color: CHROME.text }}
                  >
                    <SkipBack className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => ra.goToPart(ra.index + 1, ra.playing)}
                    disabled={ra.loading || ra.index >= ra.chunkCount - 1}
                    aria-label="Next part"
                    data-testid="button-dock-next"
                    className="inline-flex items-center justify-center rounded disabled:opacity-30"
                    style={{ width: 34, height: 34, color: CHROME.text }}
                  >
                    <SkipForward className="w-4 h-4" />
                  </button>
                </div>
              )}

              {/* Voice/speed settings (TTS sessions only) */}
              {ra.nowPlaying?.kind === "tts" && (
                <button
                  onClick={() => setShowSettings((s) => !s)}
                  aria-label="Voice and speed settings"
                  aria-expanded={showSettings}
                  data-testid="button-dock-settings"
                  className="inline-flex items-center justify-center rounded shrink-0"
                  style={{
                    width: 34, height: 34,
                    color: showSettings ? CHROME.accent : CHROME.muted,
                    background: showSettings ? CHROME.bgHi : "transparent",
                  }}
                >
                  <Settings2 className="w-4 h-4" />
                </button>
              )}

              {/* Close */}
              <button
                onClick={() => { setShowSettings(false); ra.close(); }}
                aria-label="Stop and close"
                data-testid="button-dock-close"
                className="inline-flex items-center justify-center rounded shrink-0"
                style={{ width: 34, height: 34, color: CHROME.muted }}
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Voice + speed row */}
            {showSettings && ra.nowPlaying?.kind === "tts" && (
              <div
                className="flex items-center gap-3 flex-wrap mt-2 pt-2"
                style={{ borderTop: `1px solid ${CHROME.line}` }}
                data-testid="row-dock-settings"
              >
                <span className="text-[10px] font-mono font-semibold uppercase tracking-widest shrink-0" style={{ color: CHROME.muted }}>
                  Voice
                </span>
                <Select value={ra.voice} onValueChange={(v) => ra.applySettings(v, ra.speed)}>
                  <SelectTrigger
                    className="h-8 text-xs font-mono w-36 shrink-0 border"
                    style={{ background: CHROME.bgHi, borderColor: CHROME.line, color: CHROME.text }}
                    data-testid="select-dock-voice"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="max-h-56">
                    {TTS_VOICE_OPTIONS.map((v) => (
                      <SelectItem key={v.id} value={v.id} className="text-xs font-mono">
                        {v.label}
                        <span className="ml-1.5 text-muted-foreground text-[10px]">{v.accent}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="text-[10px] font-mono font-semibold uppercase tracking-widest shrink-0 ml-1" style={{ color: CHROME.muted }}>
                  Speed
                </span>
                <div className="flex items-center gap-1">
                  {TTS_SPEED_OPTIONS.map((s) => (
                    <button
                      key={s.value}
                      onClick={() => ra.applySettings(ra.voice, s.value)}
                      data-testid={`button-dock-speed-${s.value}`}
                      className="px-2 rounded text-[11px] font-mono transition-colors"
                      style={{
                        height: 28,
                        background: ra.speed === s.value ? CHROME.accent : CHROME.bgHi,
                        color: ra.speed === s.value ? CHROME.accentInk : CHROME.muted,
                        fontWeight: ra.speed === s.value ? 600 : 400,
                      }}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Seek bar — native controls for scrubbing within the part */}
            <SeekRow />
          </div>
        </div>
      )}
    </>
  );
}

/** Thin scrub row bound to the shared audio element via rAF-free polling. */
function SeekRow() {
  const ra = useReadAloud();
  const barRef = useRef<HTMLDivElement | null>(null);
  const fillRef = useRef<HTMLDivElement | null>(null);
  const timeRef = useRef<HTMLSpanElement | null>(null);

  // Poll position at 500 ms — cheap, and avoids re-rendering the dock tree.
  useEffect(() => {
    const id = setInterval(() => {
      const el = ra.audioRef.current;
      if (!el || !fillRef.current || !timeRef.current) return;
      const dur = el.duration;
      const pct = isFinite(dur) && dur > 0 ? (el.currentTime / dur) * 100 : 0;
      fillRef.current.style.width = `${pct}%`;
      timeRef.current.textContent = isFinite(dur) && dur > 0
        ? `${fmt(el.currentTime)} / ${fmt(dur)}`
        : "";
    }, 500);
    return () => clearInterval(id);
  }, [ra.audioRef]);

  const seek = (clientX: number) => {
    const el = ra.audioRef.current;
    const bar = barRef.current;
    if (!el || !bar || !isFinite(el.duration) || el.duration <= 0) return;
    const rect = bar.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    el.currentTime = frac * el.duration;
  };

  return (
    <div className="flex items-center gap-2 mt-1.5">
      <div
        ref={barRef}
        role="slider"
        aria-label="Seek"
        tabIndex={0}
        data-testid="slider-dock-seek"
        className="flex-1 cursor-pointer py-1.5"
        onClick={(e) => seek(e.clientX)}
        onKeyDown={(e) => {
          const el = ra.audioRef.current;
          if (!el) return;
          if (e.key === "ArrowRight") el.currentTime = Math.min(el.duration || 0, el.currentTime + 10);
          if (e.key === "ArrowLeft") el.currentTime = Math.max(0, el.currentTime - 10);
        }}
      >
        <div className="h-1 rounded-full overflow-hidden" style={{ background: CHROME.bgHi }}>
          <div ref={fillRef} className="h-full rounded-full" style={{ width: "0%", background: CHROME.accent }} />
        </div>
      </div>
      <span ref={timeRef} className="text-[10px] font-mono tabular-nums shrink-0 min-w-[72px] text-right" style={{ color: CHROME.muted }} />
    </div>
  );
}

function fmt(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
