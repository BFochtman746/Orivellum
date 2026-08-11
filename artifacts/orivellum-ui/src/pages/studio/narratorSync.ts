/**
 * Per-Work default narrator — auto-persist logic, kept out of the component
 * so it's testable (mirrors spatialSettings.ts).
 *
 * Behavior contract:
 *  - Nothing saves until the Work's casting GET has resolved and
 *    `noteLoaded()` recorded a baseline — otherwise opening a Work would
 *    immediately overwrite its saved narrator with the picker's default.
 *  - `select()` debounces rapid picker changes; only the latest selection is
 *    persisted, and PUTs run strictly in order on a single promise chain.
 *  - Switching Works (`reset()` / a new `noteLoaded()`) cancels any pending
 *    save so a previous Work's narrator can never land on the new one.
 *  - `flush()` fires a pending debounced save immediately (page leave).
 *  - A failed PUT leaves the baseline unchanged, so the same selection can
 *    be retried by a later select()/flush().
 */

export type NarratorPutter = (workId: string, voiceId: string) => Promise<void>;

interface Pending {
  workId: string;
  voiceId: string;
  put: NarratorPutter;
}

export class NarratorSync {
  private workId: string | null = null;
  private saved: string | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pending: Pending | null = null;
  private chain: Promise<unknown> = Promise.resolve();
  private seq = 0;

  /**
   * Record the loaded baseline for a Work. `savedNarrator` is what the
   * server returned (null = none saved); `currentSelection` is what the
   * picker shows, so an unchanged picker never triggers a save.
   */
  noteLoaded(workId: string, savedNarrator: string | null, currentSelection: string): void {
    this.cancel();
    this.workId = workId;
    this.saved = savedNarrator ?? currentSelection;
  }

  /** A save that happened elsewhere (e.g. "Save voices") counts as persisted. */
  noteSaved(workId: string, voiceId: string): void {
    if (workId !== this.workId) return;
    this.cancel();
    this.saved = voiceId;
  }

  /** Call on Work switch or when leaving work mode. */
  reset(): void {
    this.cancel();
    this.workId = null;
    this.saved = null;
  }

  /** Debounced auto-save. No-op until noteLoaded() matched this Work. */
  select(workId: string, voiceId: string, put: NarratorPutter, debounceMs = 600): void {
    if (workId !== this.workId) return;
    this.cancel();
    if (voiceId === this.saved) return;
    const pending: Pending = { workId, voiceId, put };
    this.pending = pending;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.pending = null;
      this.fire(pending);
    }, debounceMs);
  }

  /** Fire any pending debounced save immediately. */
  flush(): void {
    if (!this.pending) return;
    const pending = this.pending;
    this.cancel();
    this.fire(pending);
  }

  private cancel(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.pending = null;
  }

  private fire(p: Pending): void {
    const seq = ++this.seq;
    this.chain = this.chain.then(async () => {
      try {
        await p.put(p.workId, p.voiceId);
        // Only advance the baseline when this save is still the latest and
        // the user is still on the same Work.
        if (seq === this.seq && p.workId === this.workId) {
          this.saved = p.voiceId;
        }
      } catch {
        /* baseline unchanged — a later select()/flush() retries */
      }
    });
  }
}
