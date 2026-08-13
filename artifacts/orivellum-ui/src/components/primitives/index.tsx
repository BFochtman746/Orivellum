/**
 * Shared UI primitives (WP2) — the twelve building blocks every migrated
 * screen composes instead of hand-rolling layout.
 *
 * Rules:
 * - Semantic tokens only (Tailwind semantic classes + --gd-* variables).
 *   No hex, no raw palette values, no per-page appearance decisions.
 * - Every interactive element is ≥44px and dual-codes state (never color
 *   alone). Empty/error/loading are explicit designed states.
 */
export { Page } from "./page";
export { Section } from "./section";
export { Panel } from "./panel";
export { ListRow } from "./list-row";
export { Field } from "./field";
export { ActionBar } from "./action-bar";
export { Status, type StatusKind } from "./status";
export { EmptyState } from "./empty-state";
export { ErrorState } from "./error-state";
export { LoadingState } from "./loading-state";
export { FilterSheet } from "./filter-sheet";
export { ConfirmAction } from "./confirm-action";
