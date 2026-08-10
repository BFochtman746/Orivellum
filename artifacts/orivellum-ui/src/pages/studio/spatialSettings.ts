/**
 * Per-Work spatial audio settings — client-side ordering logic.
 *
 * Two races this solves (kept out of the component so they're testable):
 *
 * 1. Load-vs-save: switching Works starts a GET; if the user saves before it
 *    resolves, the stale GET response must be discarded — otherwise it would
 *    overwrite the newer optimistic state and the next render would send a
 *    stale override.  `beginLoad()` hands out a token; `shouldApplyLoad()`
 *    rejects it once any save has started since.
 *
 * 2. Save-vs-save: rapid successive edits must reach the server in user-action
 *    order.  PUTs are chained on a single promise queue, so the final user
 *    action is always the last one persisted, even when the network would
 *    otherwise complete them out of order.
 */

export interface SpatialSettings {
  enabled: boolean;
  mode: "subtle" | "wide";
  ambience_doc_id: string | null;
}

export const DEFAULT_SPATIAL: SpatialSettings = {
  enabled: false,
  mode: "subtle",
  ambience_doc_id: null,
};

export type SpatialPutter = (workId: string, s: SpatialSettings) => Promise<void>;

export interface SaveResult {
  /** PUT succeeded */
  ok: boolean;
  /** no newer save has started — rollback on failure is only valid when true */
  latest: boolean;
}

/**
 * Whether a failed save should roll component state back to its pre-save
 * snapshot.  Rollback is only correct when:
 *  - the save actually failed,
 *  - no newer save superseded it (its optimistic state is current), and
 *  - the user is STILL on the Work the save targeted — after switching
 *    Works, the state belongs to the new Work and must not be clobbered
 *    with the old Work's values.
 */
export function shouldRollback(
  result: SaveResult,
  targetWork: string,
  currentWork: string | null,
): boolean {
  return !result.ok && result.latest && currentWork === targetWork;
}

export class SpatialSettingsSync {
  private chain: Promise<unknown> = Promise.resolve();
  private saveSeq = 0;

  /** Call when a load (GET) starts; keep the returned token. */
  beginLoad(): number {
    return this.saveSeq;
  }

  /** True when no save has started since the matching beginLoad(). */
  shouldApplyLoad(token: number): boolean {
    return this.saveSeq === token;
  }

  /**
   * Queue a save.  PUTs run strictly in the order save() was called.
   * Never rejects — failures are reported via `ok: false`.
   */
  save(workId: string, next: SpatialSettings, put: SpatialPutter): Promise<SaveResult> {
    const seq = ++this.saveSeq;
    const run = this.chain.then(async (): Promise<SaveResult> => {
      try {
        await put(workId, next);
        return { ok: true, latest: seq === this.saveSeq };
      } catch {
        return { ok: false, latest: seq === this.saveSeq };
      }
    });
    this.chain = run;
    return run;
  }
}
