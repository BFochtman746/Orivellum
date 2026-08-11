/**
 * WorkRenderTracker — single-poller lifecycle for a long-running Work
 * audiobook render.
 *
 * The Audiobook tab can learn about a running render from three directions
 * at once: the discovery effect on Work selection, a 409 re-attach when
 * Generate is pressed on an already-rendering Work, and a freshly started
 * job. This class guarantees that no matter how those race, at most ONE
 * polling timer exists, and every callback fires against the job that is
 * still current:
 *
 *  - `attach` to the job already being polled is a no-op (returns false) —
 *    a discovery/Generate(409) race can never double the progress stream.
 *  - `attach` to a different job clears the old timer first.
 *  - a terminal status (done/failed/cancelled) fires `onTerminal` exactly
 *    once and stops polling; a 404 fires `onGone` once (server restarted).
 *  - `detach` (Work/mode switch, unmount) stops the timer immediately, and
 *    any response still in flight is discarded — a stale poll can never
 *    update state or re-toast after the UI has moved on.
 *
 * UI concerns (progress state, toasts, the activeWorkJobs badge map) stay in
 * the component via the callbacks; this class owns only timing and identity.
 */

export interface WorkRenderSnapshot {
  chapter_idx?: number;
  total_chapters?: number;
  chapter_title?: string;
  segments_done?: number;
  total_segments?: number;
  cached_segments?: number;
}

export interface WorkRenderStatus extends WorkRenderSnapshot {
  state: string;
  error?: string | null;
  quality_report?: unknown;
  result?: { path?: string; filename?: string } | null;
}

export interface WorkRenderTrackerOptions {
  /** Fetch the job's status — typically apiFetch(`…/tts/work/${id}/status`). */
  fetchStatus: (jobId: string) => Promise<Response>;
  /** A job became current (fresh start, discovery, or 409 re-attach). */
  onAttach: (jobId: string, snap: WorkRenderSnapshot) => void;
  /** A non-terminal status arrived for the current job. */
  onProgress: (status: WorkRenderStatus) => void;
  /** The current job reached done/failed/cancelled. Fires exactly once. */
  onTerminal: (jobId: string, status: WorkRenderStatus) => void;
  /** The server no longer knows the job (404 — restarted mid-render). */
  onGone: (jobId: string) => void;
  /** Poll period in ms (default 2000). Tests inject a small value. */
  intervalMs?: number;
}

const TERMINAL_STATES = ["done", "failed", "cancelled"];

export class WorkRenderTracker {
  private jobId: string | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly opts: WorkRenderTrackerOptions) {}

  /** The job currently shown in the UI, or null when detached/finished. */
  get currentJobId(): string | null {
    return this.jobId;
  }

  /** Whether a polling timer is live right now. */
  get polling(): boolean {
    return this.timer !== null;
  }

  /**
   * Point the tracker at a job and start polling it. Returns false (no-op)
   * when that exact job is already being polled — the caller must not reset
   * progress state in that case.
   */
  attach(jobId: string, snap: WorkRenderSnapshot = {}): boolean {
    if (this.jobId === jobId && this.timer) return false;
    this.stopTimer();
    this.jobId = jobId;
    this.opts.onAttach(jobId, snap);
    this.startTimer(jobId);
    return true;
  }

  /**
   * Stop polling and forget the job. The server render keeps going; only the
   * UI lets go. In-flight responses for the old job are discarded.
   */
  detach(): void {
    this.stopTimer();
    this.jobId = null;
  }

  private stopTimer(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private startTimer(jobId: string): void {
    const iv: ReturnType<typeof setInterval> = setInterval(async () => {
      // Detached or superseded — this closure must never touch anything.
      if (this.jobId !== jobId) {
        clearInterval(iv);
        return;
      }
      const stopThis = () => {
        clearInterval(iv);
        if (this.timer === iv) this.timer = null;
      };
      try {
        const resp = await this.opts.fetchStatus(jobId);
        if (this.jobId !== jobId) {
          clearInterval(iv);
          return;
        }
        if (!resp.ok) {
          if (resp.status === 404) {
            stopThis();
            this.jobId = null;
            this.opts.onGone(jobId);
          }
          return; // other errors: transient, keep polling
        }
        const status = (await resp.json()) as WorkRenderStatus;
        if (this.jobId !== jobId) {
          clearInterval(iv);
          return;
        }
        this.opts.onProgress(status);
        if (TERMINAL_STATES.includes(status.state)) {
          stopThis();
          this.jobId = null;
          this.opts.onTerminal(jobId, status);
        }
      } catch {
        /* transient poll errors — next tick retries */
      }
    }, this.opts.intervalMs ?? 2000);
    this.timer = iv;
  }
}

/**
 * Remove every entry pointing at `jobId` from the activeWorkJobs badge map.
 * Returns the same reference when nothing matched so React state updates
 * can skip a re-render.
 */
export function pruneJobFromActiveMap<T extends { job_id: string }>(
  map: Record<string, T>,
  jobId: string,
): Record<string, T> {
  if (!Object.values(map).some(j => j.job_id === jobId)) return map;
  const next: Record<string, T> = {};
  for (const [k, v] of Object.entries(map)) {
    if (v.job_id !== jobId) next[k] = v;
  }
  return next;
}
