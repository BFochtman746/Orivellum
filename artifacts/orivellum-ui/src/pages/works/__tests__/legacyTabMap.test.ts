/**
 * Legacy ?tab= deep-link mapping — every old flat-tab value must land on the
 * right primary view AND the right inner segment (regression: segments were
 * once initialized to each view's default, silently dropping the mapped
 * segment for ?tab=search/brainstorm/graph/completeness/quiz/tasks links).
 */
import { describe, it, expect } from "vitest";
import {
  LEGACY_TAB_MAP,
  VIEW_SEGMENTS,
  initialViewState,
  type PrimaryView,
} from "../detail";

const EXPECTED: Record<string, { view: PrimaryView; segment?: string; trailer?: boolean }> = {
  book:          { view: "create",    segment: "book" },
  brainstorm:    { view: "create",    segment: "brainstorm" },
  genesis:       { view: "create",    segment: "genesis" },
  documents:     { view: "overview" },
  docs:          { view: "overview" },
  knowledge:     { view: "knowledge", segment: "knowledge" },
  graph:         { view: "knowledge", segment: "graph" },
  search:        { view: "knowledge", segment: "search" },
  gaps:          { view: "review",    segment: "gaps" },
  completeness:  { view: "review",    segment: "completeness" },
  quiz:          { view: "review",    segment: "quiz" },
  learn:         { view: "review",    segment: "study" },
  conversations: { view: "activity",  segment: "conversations" },
  tasks:         { view: "activity",  segment: "tasks" },
  trailer:       { view: "overview",  trailer: true },
};

describe("LEGACY_TAB_MAP", () => {
  it("covers exactly the expected legacy tab values", () => {
    expect(Object.keys(LEGACY_TAB_MAP).sort()).toEqual(Object.keys(EXPECTED).sort());
  });

  it("every mapped segment exists in its view's segment list", () => {
    for (const [tab, m] of Object.entries(LEGACY_TAB_MAP)) {
      if (m.segment) {
        expect(VIEW_SEGMENTS[m.view], `?tab=${tab}`).toContain(m.segment);
      }
    }
  });
});

describe("initialViewState", () => {
  it("defaults to overview with per-view default segments", () => {
    const s = initialViewState(null);
    expect(s.view).toBe("overview");
    expect(s.trailer).toBe(false);
    for (const view of Object.keys(VIEW_SEGMENTS) as PrimaryView[]) {
      expect(s.segments[view]).toBe(VIEW_SEGMENTS[view][0] ?? "");
    }
  });

  it("unknown tab values fall back to overview", () => {
    expect(initialViewState("no-such-tab").view).toBe("overview");
  });

  it.each(Object.entries(EXPECTED))(
    "?tab=%s opens the mapped view and inner segment",
    (tab, want) => {
      const s = initialViewState(tab);
      expect(s.view).toBe(want.view);
      expect(s.trailer).toBe(want.trailer ?? false);
      if (want.segment) {
        // The regression this guards: the mapped view's segment must be the
        // mapped segment, not that view's first/default segment.
        expect(s.segments[want.view]).toBe(want.segment);
      }
      // Other views keep their defaults untouched.
      for (const view of Object.keys(VIEW_SEGMENTS) as PrimaryView[]) {
        if (view !== want.view) {
          expect(s.segments[view]).toBe(VIEW_SEGMENTS[view][0] ?? "");
        }
      }
    },
  );
});
