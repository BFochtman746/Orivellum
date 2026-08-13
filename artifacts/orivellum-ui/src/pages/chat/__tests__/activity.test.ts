/**
 * WP4 — truthful activity strip: the step list is a pure fold over
 * server-emitted events. These tests pin the truth contract:
 * no event → no step; failures are never shown as successes.
 */
import { describe, it, expect } from "vitest";
import {
  applyActivityEvent, stepsFromEvents, activityLabel, activityDetail,
  stepsFromCodeProgress,
  type ServerActivityEvent, type CodeProgressEvent,
} from "../activity";
import { foldEvents, emptyReplay } from "@/lib/gen-replay";

const NOW = 1_000_000;

describe("applyActivityEvent", () => {
  it("creates no steps without events", () => {
    expect(stepsFromEvents([])).toEqual([]);
  });

  it("start opens a step, done completes it in place", () => {
    const start: ServerActivityEvent = { stage: "retrieval", state: "start", action: "knowledge_search" };
    const done: ServerActivityEvent = { stage: "retrieval", state: "done", action: "knowledge_search", source_count: 3, elapsed_ms: 420 };
    let steps = applyActivityEvent([], start, NOW);
    expect(steps).toHaveLength(1);
    expect(steps[0].done).toBe(false);
    steps = applyActivityEvent(steps, done, NOW + 500);
    expect(steps).toHaveLength(1);
    expect(steps[0].done).toBe(true);
    expect(steps[0].elapsedMs).toBe(420);
    expect(steps[0].detail).toContain("3 sources");
  });

  it("done without a prior start appends an already-completed step (never in-progress)", () => {
    const ev: ServerActivityEvent = { stage: "tool", state: "done", tool: "web_search", elapsed_ms: 900 };
    const steps = applyActivityEvent([], ev, NOW);
    expect(steps).toHaveLength(1);
    expect(steps[0].done).toBe(true);
    expect(steps[0].startMs).toBe(NOW - 900);
  });

  it("failed events are marked failed, not done-successfully", () => {
    const start: ServerActivityEvent = { stage: "generation", state: "start" };
    const failed: ServerActivityEvent = { stage: "generation", state: "failed", reason: "timeout" };
    const steps = applyActivityEvent(applyActivityEvent([], start, NOW), failed, NOW + 100);
    expect(steps).toHaveLength(1);
    expect(steps[0].failed).toBe(true);
    expect(steps[0].label).toContain("timed out");
    expect(steps[0].detail).toContain("timeout");
  });

  it("duplicate start for an open step is a no-op", () => {
    const start: ServerActivityEvent = { stage: "generation", state: "start" };
    const once = applyActivityEvent([], start, NOW);
    const twice = applyActivityEvent(once, start, NOW + 10);
    expect(twice).toBe(once);
  });

  it("ignores malformed events", () => {
    expect(applyActivityEvent([], {} as ServerActivityEvent, NOW)).toEqual([]);
    expect(applyActivityEvent([], { stage: 5, state: "start" } as unknown as ServerActivityEvent, NOW)).toEqual([]);
  });

  it("distinct stage/action pairs get their own rows", () => {
    const evs: ServerActivityEvent[] = [
      { stage: "retrieval", state: "start", action: "knowledge_search" },
      { stage: "retrieval", state: "done", action: "knowledge_search", source_count: 2 },
      { stage: "deliberation", state: "start", action: "gate" },
      { stage: "deliberation", state: "done", action: "gate", result: "fast" },
      { stage: "generation", state: "start" },
      { stage: "generation", state: "done", elapsed_ms: 1200 },
      { stage: "verification", state: "done", result: "passed", elapsed_ms: 80 },
    ];
    const steps = stepsFromEvents(evs, NOW);
    expect(steps.map((s) => s.id)).toEqual([
      "retrieval:knowledge_search", "deliberation:gate", "generation:", "verification:",
    ]);
    expect(steps.every((s) => s.done)).toBe(true);
  });
});

describe("activityLabel / activityDetail", () => {
  it("uses present tense while running, past when done", () => {
    expect(activityLabel({ stage: "retrieval", state: "start" })).toMatch(/^Searching/);
    expect(activityLabel({ stage: "retrieval", state: "done" })).toMatch(/^Searched/);
    expect(activityLabel({ stage: "generation", state: "start" })).toBe("Writing response");
  });

  it("names the tool when known", () => {
    expect(activityLabel({ stage: "tool", state: "done", tool: "web_search" })).toContain("web");
    expect(activityLabel({ stage: "tool", state: "done", tool: "unknown_thing" })).toContain("unknown_thing");
  });

  it("surfaces verification result factually", () => {
    expect(activityDetail({ stage: "verification", state: "done", result: "corrected" })).toContain("corrected");
    expect(activityDetail({ stage: "verification", state: "done", result: "passed" })).toContain("passed");
  });
});

describe("stepsFromCodeProgress", () => {
  const FRAMES: CodeProgressEvent[] = [
    { stage: "planning", label: "Planning the program", n: 1, total: 4 },
    { stage: "generating", label: "Writing file 1/2", n: 2, total: 4 },
    { stage: "generating", label: "Writing file 2/2", n: 2, total: 4 },
    { stage: "testing", label: "Running tests", n: 3, total: 4 },
  ];

  it("a later stage frame completes the earlier stage (sequential pipeline)", () => {
    const steps = stepsFromCodeProgress(FRAMES, false, NOW);
    expect(steps.map((s) => s.id)).toEqual(["cg_planning", "cg_generating", "cg_testing"]);
    expect(steps[0].done).toBe(true);
    expect(steps[1].done).toBe(true);
    // Same-stage repeat updated the label in place, no extra row
    expect(steps[1].label).toBe("Writing file 2/2");
  });

  it("the final stage is done only when the job actually finished", () => {
    expect(stepsFromCodeProgress(FRAMES, false, NOW).at(-1)!.done).toBe(false);
    expect(stepsFromCodeProgress(FRAMES, true, NOW).at(-1)!.done).toBe(true);
  });

  it("empty input yields no steps", () => {
    expect(stepsFromCodeProgress([], true, NOW)).toEqual([]);
  });
});

describe("gen-replay activity folding", () => {
  it("collects journaled activity events in order", () => {
    const acc = foldEvents(emptyReplay(), [
      { seq: 1, kind: "activity", payload: JSON.stringify({ activity: { stage: "retrieval", state: "start" } }) },
      { seq: 2, kind: "token", payload: JSON.stringify({ token: "Hi" }) },
      { seq: 3, kind: "activity", payload: JSON.stringify({ activity: { stage: "retrieval", state: "done", source_count: 1 } }) },
    ]);
    expect(acc.text).toBe("Hi");
    expect(acc.activity).toHaveLength(2);
    expect((acc.activity[1] as { state: string }).state).toBe("done");
    // And the folded events rebuild a truthful step list
    const steps = stepsFromEvents(acc.activity as unknown as ServerActivityEvent[], NOW);
    expect(steps).toHaveLength(1);
    expect(steps[0].done).toBe(true);
  });

  it("does not treat arrays or non-objects as activity", () => {
    const acc = foldEvents(emptyReplay(), [
      { seq: 1, kind: "meta", payload: JSON.stringify({ activity: [1, 2] }) },
      { seq: 2, kind: "meta", payload: JSON.stringify({ activity: "nope" }) },
    ]);
    expect(acc.activity).toHaveLength(0);
  });

  it("collects code_progress frames for replay", () => {
    const acc = foldEvents(emptyReplay(), [
      { seq: 1, kind: "code_progress", payload: JSON.stringify({ code_progress: { stage: "planning", label: "Planning", n: 1, total: 4 } }) },
      { seq: 2, kind: "code_progress", payload: JSON.stringify({ code_progress: { stage: "generating", label: "Writing", n: 2, total: 4 } }) },
    ]);
    expect(acc.codeProgress).toHaveLength(2);
    const steps = stepsFromCodeProgress(acc.codeProgress as unknown as CodeProgressEvent[], false, NOW);
    expect(steps).toHaveLength(2);
    expect(steps[0].done).toBe(true);
    expect(steps[1].done).toBe(false);
  });
});
