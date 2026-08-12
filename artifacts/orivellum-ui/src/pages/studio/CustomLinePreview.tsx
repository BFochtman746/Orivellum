/**
 * "Try your own line" — type up to 200 characters and hear the voice speak it.
 *
 * Rendered in the Browse detail panel, on each usable cloned voice, and (as
 * the inline CastCustomLine variant) in the Audiobook tab's Chapter Voices
 * rows. Lives in its own module so component tests can import it without
 * pulling in the whole VoiceStudio tree.
 */
import { useState } from "react";
import { Play, Pause, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { useGlobalAudio } from "./useGlobalAudio";

export const CUSTOM_LINE_MAX = 200;

export function CustomLinePreview({ voiceId, globalAudio }: {
  voiceId: string;
  globalAudio: ReturnType<typeof useGlobalAudio>;
}) {
  const [line, setLine] = useState("");
  const key = `custom:${voiceId}`;
  const isLoading = globalAudio.loadingId === key;
  const isPlaying = globalAudio.playingId === key;
  const trimmed = line.trim();

  function play() {
    if (!trimmed && !isPlaying) return;
    globalAudio.playCustomLine(voiceId, trimmed);
  }

  return (
    <div>
      <p className="text-xs font-mono uppercase text-muted-foreground mb-2">Try your own line</p>
      <div className="flex items-center gap-2">
        <Input
          value={line}
          maxLength={CUSTOM_LINE_MAX}
          placeholder="Type a sentence from your book…"
          onChange={e => setLine(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") play(); }}
          className="h-8 text-sm"
        />
        <Button
          size="sm"
          variant="outline"
          onClick={play}
          disabled={isLoading || (!trimmed && !isPlaying)}
          title="Hear this voice speak your line"
          className="shrink-0"
        >
          {isLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> :
           isPlaying ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground mt-1">
        {line.length}/{CUSTOM_LINE_MAX} — one-off preview, not saved
      </p>
    </div>
  );
}
