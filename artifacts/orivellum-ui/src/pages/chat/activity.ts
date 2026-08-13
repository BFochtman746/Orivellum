/**
 * Server-authored chat activity (WP4).
 *
 * The activity strip/drawer render ONLY events the server actually emitted —
 * `{activity: {...}}` SSE frames from the generation stream (journaled and
 * replayable) plus the code-generation pipeline's `code_progress` frames.
 * The client never invents steps: no event, no step.
 */

/** One activity event as emitted by the server (see _activity_frame). */
export interface ServerActivityEvent {
  stage: string;                       // retrieval | tool | deliberation | generation | verification
  state: "start" | "done" | "failed";
  action?: string;                     // e.g. "knowledge_search", "gate", "council", "continuation"
  tool?: string;                       // intent/tool name when stage === "tool"
  source_count?: number;
  elapsed_ms?: number;
  result?: string;                     // e.g. verification "passed" / "corrected", gate route
  reason?: string;                     // failure reason when state === "failed"
}

/** One row in the activity strip/drawer. */
export interface ActivityStep {
  id: string;
  label: string;
  icon: "search" | "read" | "think" | "write";
  startMs: number;
  endMs?: number;
  done: boolean;
  failed?: boolean;
  /** Server-measured duration — preferred over client wall-clock when present. */
  elapsedMs?: number;
  /** Short factual detail line (e.g. "3 sources", "verified: passed"). */
  detail?: string;
}

export function activityIcon(stage: string): ActivityStep["icon"] {
  if (stage === "retrieval") return "search";
  if (stage === "tool") return "search";
  if (stage === "deliberation") return "think";
  if (stage === "verification") return "read";
  return "write"; // generation
}

const TOOL_LABELS: Record<string, string> = {
  web_search: "Searching the web",
  weather: "Checking the weather",
  remember: "Saving a memory",
  recall: "Recalling memories",
  recall_output: "Recalling an earlier answer",
  image_gen: "Generating an image",
};

/** Human label for an event. Present tense while running, past when done. */
export function activityLabel(ev: ServerActivityEvent): string {
  const running = ev.state === "start";
  switch (ev.stage) {
    case "retrieval":
      return running ? "Searching your knowledge" : "Searched your knowledge";
    case "tool": {
      const base = TOOL_LABELS[ev.tool ?? ""] ?? `Running ${ev.tool ?? "a tool"}`;
      return running ? base : base.replace(/^(Searching|Checking|Saving|Recalling|Generating|Running)/,
        (m) => ({ Searching: "Searched", Checking: "Checked", Saving: "Saved", Recalling: "Recalled", Generating: "Generated", Running: "Ran" })[m] ?? m);
    }
    case "deliberation":
      if (ev.action === "council") return running ? "Deliberating (council)" : "Council deliberation finished";
      return running ? "Choosing an approach" : "Approach chosen";
    case "generation":
      if (ev.state === "failed") {
        return ev.reason === "timeout" ? "Generation stalled (timed out)" : "Generation failed";
      }
      return running ? "Writing response" : "Response written";
    case "verification":
      return running ? "Verifying against your canon" : "Checked against your canon";
    default:
      return ev.stage;
  }
}

/** Short factual detail line for a completed event, or undefined. */
export function activityDetail(ev: ServerActivityEvent): string | undefined {
  const parts: string[] = [];
  if (typeof ev.source_count === "number") {
    parts.push(ev.source_count === 1 ? "1 source" : `${ev.source_count} sources`);
  }
  if (ev.stage === "verification" && ev.result) {
    parts.push(ev.result === "corrected" ? "corrected a claim" : ev.result);
  }
  if (ev.stage === "deliberation" && ev.result) parts.push(`route: ${ev.result}`);
  if (ev.state === "failed" && ev.reason) parts.push(ev.reason);
  return parts.length ? parts.join(" · ") : undefined;
}

/** Stable step identity: one row per stage+action pairing. */
function stepId(ev: ServerActivityEvent): string {
  return `${ev.stage}:${ev.action ?? ev.tool ?? ""}`;
}

/**
 * Fold one server event into the step list. Pure — safe for setState updaters
 * and journal replay. A `done`/`failed` without a prior `start` (tool events,
 * verification) appends an already-completed row so nothing is ever invented
 * as "in progress".
 */
export function applyActivityEvent(
  steps: ActivityStep[],
  ev: ServerActivityEvent,
  now: number = Date.now(),
): ActivityStep[] {
  if (!ev || typeof ev.stage !== "string" || !ev.state) return steps;
  const id = stepId(ev);
  if (ev.state === "start") {
    // Re-started stage (e.g. continuation): update in place if still open.
    const openIdx = steps.findIndex((s) => s.id === id && !s.done);
    if (openIdx >= 0) return steps;
    return [
      ...steps,
      { id, label: activityLabel(ev), icon: activityIcon(ev.stage), startMs: now, done: false },
    ];
  }
  const failed = ev.state === "failed";
  const idx = steps.findIndex((s) => s.id === id && !s.done);
  const completed: ActivityStep = {
    id,
    label: activityLabel(ev),
    icon: activityIcon(ev.stage),
    startMs: idx >= 0 ? steps[idx].startMs : now - (ev.elapsed_ms ?? 0),
    endMs: now,
    done: true,
    failed: failed || undefined,
    elapsedMs: ev.elapsed_ms,
    detail: activityDetail(ev),
  };
  if (idx >= 0) {
    const next = steps.slice();
    next[idx] = completed;
    return next;
  }
  return [...steps, completed];
}

/** Rebuild the full step list from journaled events (reconnect replay). */
export function stepsFromEvents(
  events: ServerActivityEvent[],
  now: number = Date.now(),
): ActivityStep[] {
  return events.reduce<ActivityStep[]>((acc, ev) => applyActivityEvent(acc, ev, now), []);
}
