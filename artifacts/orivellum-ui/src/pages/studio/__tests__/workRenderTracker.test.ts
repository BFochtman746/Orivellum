/**
 * WorkRenderTracker — reconnecting to a running Work render must never
 * double the progress stream or repeat terminal alerts.
 *
 * The Audiobook tab can attach to the same job from several directions at
 * once (discovery on Work selection, a 409 re-attach when Generate is
 * pressed on an already-rendering Work, a fresh start). These tests pin the
 * tracker's guarantees:
 *  - a discovery/409 race on the same job keeps exactly one polling stream
 *  - a terminal status stops the poller, clears the current job, and fires
 *    onTerminal exactly once (one "Audiobook ready" toast, one badge prune)
 *  - detaching (switching Works mid-poll) stops cleanly, and a response
 *    still in flight at detach time is discarded
 *  - a 404 (server restarted) fires onGone exactly once
 *  - pruneJobFromActiveMap clears every badge entry for the finished job
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { WorkRenderTracker, pruneJobFromActiveMap } from "../workRenderTracker";

const INTERVAL = 100;

function statusResponse(status: Record<string, unknown>): Response {
  return { ok: true, status: 200, json: async () => status } as unknown as Response;
}

function makeTracker(overrides: Partial<ConstructorParameters<typeof WorkRenderTracker>[0]> = {}) {
  const calls = {
    fetchStatus: vi.fn(async (_jobId: string) => statusResponse({ state: "running", segments_done: 1 })),
    onAttach: vi.fn(),
    onProgress: vi.fn(),
    onTerminal: vi.fn(),
    onGone: vi.fn(),
  };
  const tracker = new WorkRenderTracker({ ...calls, intervalMs: INTERVAL, ...overrides });
  return { tracker, calls };
}

/** Advance one poll period and let the async tick body settle. */
async function tick(times = 1) {
  for (let i = 0; i < times; i++) {
    await vi.advanceTimersByTimeAsync(INTERVAL);
  }
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("WorkRenderTracker", () => {
  it("discovery + 409 re-attach on the same job keep exactly one polling stream", async () => {
    const { tracker, calls } = makeTracker();

    // Discovery attaches first…
    expect(tracker.attach("job-1", { segments_done: 3 })).toBe(true);
    // …then Generate hits a 409 and re-attaches to the same job.
    expect(tracker.attach("job-1")).toBe(false); // no-op — progress not reset
    expect(calls.onAttach).toHaveBeenCalledTimes(1);
    expect(calls.onAttach).toHaveBeenCalledWith("job-1", { segments_done: 3 });

    await tick(3);
    // One timer → exactly one status request per period, not two.
    expect(calls.fetchStatus).toHaveBeenCalledTimes(3);
    expect(calls.onProgress).toHaveBeenCalledTimes(3);
  });

  it("attaching a different job clears the old poller first", async () => {
    const { tracker, calls } = makeTracker();
    tracker.attach("job-1");
    await tick(1);
    expect(calls.fetchStatus).toHaveBeenLastCalledWith("job-1");

    expect(tracker.attach("job-2")).toBe(true);
    expect(tracker.currentJobId).toBe("job-2");
    calls.fetchStatus.mockClear();
    await tick(2);
    // Only job-2 is polled — the old timer is gone, not doubled up.
    expect(calls.fetchStatus).toHaveBeenCalledTimes(2);
    expect(calls.fetchStatus.mock.calls.every(([id]) => id === "job-2")).toBe(true);
  });

  it("terminal state stops polling and fires onTerminal exactly once", async () => {
    const { tracker, calls } = makeTracker();
    calls.fetchStatus
      .mockResolvedValueOnce(statusResponse({ state: "running", segments_done: 5 }))
      .mockResolvedValue(statusResponse({ state: "done", result: { path: "out.mp3" } }));

    tracker.attach("job-1");
    await tick(2);
    expect(calls.onTerminal).toHaveBeenCalledTimes(1);
    expect(calls.onTerminal).toHaveBeenCalledWith("job-1", expect.objectContaining({ state: "done" }));
    expect(tracker.polling).toBe(false);
    expect(tracker.currentJobId).toBeNull();

    // Long after the render finished: no more polls, no duplicate alert.
    const fetches = calls.fetchStatus.mock.calls.length;
    await tick(5);
    expect(calls.fetchStatus).toHaveBeenCalledTimes(fetches);
    expect(calls.onTerminal).toHaveBeenCalledTimes(1);
  });

  it("switching Works mid-poll detaches cleanly", async () => {
    const { tracker, calls } = makeTracker();
    tracker.attach("job-1");
    await tick(2);

    tracker.detach();
    expect(tracker.polling).toBe(false);
    expect(tracker.currentJobId).toBeNull();

    const fetches = calls.fetchStatus.mock.calls.length;
    await tick(5);
    expect(calls.fetchStatus).toHaveBeenCalledTimes(fetches); // silence after detach
  });

  it("a response still in flight when the user switches Works is discarded", async () => {
    const { tracker, calls } = makeTracker();
    let resolveStatus!: (r: Response) => void;
    calls.fetchStatus.mockReturnValue(new Promise<Response>(res => { resolveStatus = res; }));

    tracker.attach("job-1");
    await tick(1); // fires the fetch, which is now hanging
    expect(calls.fetchStatus).toHaveBeenCalledTimes(1);

    tracker.detach(); // user switched Works while the response was in flight
    resolveStatus(statusResponse({ state: "done" }));
    await vi.advanceTimersByTimeAsync(0);

    // The stale response must not resurrect progress or fire a "ready" alert.
    expect(calls.onProgress).not.toHaveBeenCalled();
    expect(calls.onTerminal).not.toHaveBeenCalled();
  });

  it("a stale in-flight response is also discarded when superseded by another job", async () => {
    const { tracker, calls } = makeTracker();
    let resolveStatus!: (r: Response) => void;
    calls.fetchStatus.mockReturnValueOnce(new Promise<Response>(res => { resolveStatus = res; }));
    calls.fetchStatus.mockResolvedValue(statusResponse({ state: "running" }));

    tracker.attach("job-1");
    await tick(1); // job-1 fetch hanging
    tracker.attach("job-2");
    resolveStatus(statusResponse({ state: "done" }));
    await vi.advanceTimersByTimeAsync(0);

    expect(calls.onTerminal).not.toHaveBeenCalled(); // job-1's late "done" is noise
    expect(tracker.currentJobId).toBe("job-2");
    await tick(1);
    expect(calls.onProgress).toHaveBeenCalledTimes(1); // job-2 keeps polling
  });

  it("404 (server restarted) fires onGone exactly once and stops", async () => {
    const { tracker, calls } = makeTracker();
    calls.fetchStatus.mockResolvedValue({ ok: false, status: 404 } as unknown as Response);

    tracker.attach("job-1");
    await tick(3);
    expect(calls.onGone).toHaveBeenCalledTimes(1);
    expect(calls.onGone).toHaveBeenCalledWith("job-1");
    expect(tracker.polling).toBe(false);
    expect(tracker.currentJobId).toBeNull();
    expect(calls.onTerminal).not.toHaveBeenCalled();
  });

  it("non-404 errors are transient — polling continues", async () => {
    const { tracker, calls } = makeTracker();
    calls.fetchStatus
      .mockResolvedValueOnce({ ok: false, status: 500 } as unknown as Response)
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue(statusResponse({ state: "running" }));

    tracker.attach("job-1");
    await tick(3);
    expect(tracker.polling).toBe(true);
    expect(calls.onProgress).toHaveBeenCalledTimes(1); // the third tick succeeded
    expect(calls.onGone).not.toHaveBeenCalled();
  });
});

describe("pruneJobFromActiveMap", () => {
  it("removes every badge entry pointing at the finished job", () => {
    const map = {
      w1: { job_id: "job-1" },
      w2: { job_id: "job-2" },
      w3: { job_id: "job-1" }, // defensive: same job listed twice
    };
    expect(pruneJobFromActiveMap(map, "job-1")).toEqual({ w2: { job_id: "job-2" } });
  });

  it("returns the same reference when nothing matches (skips a re-render)", () => {
    const map = { w1: { job_id: "job-1" } };
    expect(pruneJobFromActiveMap(map, "job-9")).toBe(map);
  });
});
