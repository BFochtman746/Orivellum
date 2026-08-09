/**
 * Deep Reprocess — force re-extraction of every library document.
 *
 * Mirrors the web Library page's "Deep Reprocess" button: POST
 * /api/library/reprocess-all?force=true re-runs extraction on ALL documents,
 * including ones already marked ready. Nothing is deleted; ZIPs are
 * re-exploded (SHA dedup prevents duplicates).
 */

export const DEEP_REPROCESS_WARNING =
  'This re-extracts EVERY document — including ones that already processed fine. ' +
  'Nothing is deleted, but a large library can take a long time to churn through. Continue?';

export function deepReprocessUrl(origin: string): string {
  return `${origin}/api/library/reprocess-all?force=true`;
}

export interface ReprocessSummary {
  queued?: number;
  queued_zips?: number;
  skipped?: number;
  message?: string;
}

/** Human-readable one-liner for the completion alert. */
export function summarizeReprocess(data: ReprocessSummary): string {
  const queued = data.queued ?? 0;
  const skipped = data.skipped ?? 0;
  if (queued === 0) {
    // Never claim success when candidates were skipped — their source files
    // are missing from disk and nothing was actually re-extracted.
    if (skipped > 0) {
      return `Nothing queued — ${skipped} document${skipped === 1 ? '' : 's'} skipped because the source file is missing from disk.`;
    }
    return 'All documents are already fully processed.';
  }
  const parts = [`${queued} document${queued === 1 ? '' : 's'} queued for re-extraction`];
  const zips = data.queued_zips ?? 0;
  if (zips > 0) parts.push(`${zips} ZIP${zips === 1 ? '' : 's'} will be re-exploded`);
  if (skipped > 0) parts.push(`${skipped} skipped (source file missing)`);
  return `${parts.join(' — ')}.`;
}
