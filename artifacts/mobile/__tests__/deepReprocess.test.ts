import {
  DEEP_REPROCESS_WARNING,
  deepReprocessUrl,
  summarizeReprocess,
} from '../lib/deepReprocess';

describe('deepReprocessUrl', () => {
  it('builds the forced reprocess-all URL from the origin', () => {
    expect(deepReprocessUrl('http://100.92.116.70:8080')).toBe(
      'http://100.92.116.70:8080/api/library/reprocess-all?force=true',
    );
  });
});

describe('DEEP_REPROCESS_WARNING', () => {
  it('warns that ready documents are included', () => {
    expect(DEEP_REPROCESS_WARNING).toMatch(/EVERY document/);
    expect(DEEP_REPROCESS_WARNING).toMatch(/Nothing is deleted/);
  });
});

describe('summarizeReprocess', () => {
  it('reports nothing to do when queued is 0', () => {
    expect(summarizeReprocess({ queued: 0 })).toBe(
      'All documents are already fully processed.',
    );
  });

  it('handles a missing/empty payload', () => {
    expect(summarizeReprocess({})).toBe('All documents are already fully processed.');
  });

  it('reports queued docs with singular/plural forms', () => {
    expect(summarizeReprocess({ queued: 1 })).toBe(
      '1 document queued for re-extraction.',
    );
    expect(summarizeReprocess({ queued: 12 })).toBe(
      '12 documents queued for re-extraction.',
    );
  });

  it('reports skipped docs instead of claiming success when nothing was queued', () => {
    expect(summarizeReprocess({ queued: 0, skipped: 1 })).toBe(
      'Nothing queued — 1 document skipped because the source file is missing from disk.',
    );
    expect(summarizeReprocess({ queued: 0, skipped: 3 })).toBe(
      'Nothing queued — 3 documents skipped because the source file is missing from disk.',
    );
  });

  it('includes ZIP and skipped counts when present', () => {
    expect(summarizeReprocess({ queued: 5, queued_zips: 2, skipped: 1 })).toBe(
      '5 documents queued for re-extraction — 2 ZIPs will be re-exploded — 1 skipped (source file missing).',
    );
  });
});
