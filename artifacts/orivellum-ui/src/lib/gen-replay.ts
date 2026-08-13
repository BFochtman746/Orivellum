/**
 * Generation replay client — iPhone continuity core.
 *
 * The live SSE stream announces a `job_id` as its first frame; we persist it
 * so that after an iOS suspension, tab kill, or network drop the client can
 * discover the job and replay its journalled events
 * (`GET /api/conversations/jobs/{job_id}/events?after=<seq>`) instead of
 * depending on the SSE connection surviving the background.
 *
 * Messages rebuilt this way are labeled "Recovered response" — the recovered
 * ids are remembered (localStorage, capped) so the badge survives the
 * conversation refetch that replaces local bubbles with server rows.
 */

import { apiFetch } from "@/lib/auth";

const PENDING_KEY = "oriv-pending-gen";
const RECOVERED_KEY = "oriv-recovered-msgs";
const RECOVERED_CAP = 50;

export interface PendingGen {
  jobId: string;
  convId: string;
  startedAt: number;
}

export interface GenJob {
  id: string;
  conversation_id: string;
  message_id: string | null;
  state: "running" | "done" | "failed";
}

export interface GenEvent {
  seq: number;
  kind: string;
  payload: string;
}

const API_BASE = `${import.meta.env.BASE_URL?.replace(/\/$/, "") || ""}/api`;

// ── Pending-generation record ────────────────────────────────────────────────

export function getPendingGen(): PendingGen | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as PendingGen;
    // Journal rows are pruned after 24 h; a record older than that is dead.
    if (!p.jobId || Date.now() - p.startedAt > 24 * 3600_000) {
      localStorage.removeItem(PENDING_KEY);
      return null;
    }
    return p;
  } catch {
    return null;
  }
}

export function setPendingGen(p: PendingGen): void {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(p));
  } catch {
    /* storage full/blocked — recovery simply won't be available */
  }
}

export function clearPendingGen(jobId?: string): void {
  try {
    if (jobId) {
      const cur = getPendingGen();
      if (cur && cur.jobId !== jobId) return; // a newer send owns the slot
    }
    localStorage.removeItem(PENDING_KEY);
  } catch {
    /* ignore */
  }
}

// ── "Recovered response" badge persistence ──────────────────────────────────

function readRecovered(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECOVERED_KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

export function markRecovered(messageId: string): void {
  try {
    const ids = readRecovered().filter((id) => id !== messageId);
    ids.push(messageId);
    localStorage.setItem(RECOVERED_KEY, JSON.stringify(ids.slice(-RECOVERED_CAP)));
  } catch {
    /* ignore */
  }
}

export function isRecovered(messageId: string | undefined): boolean {
  if (!messageId) return false;
  return readRecovered().includes(messageId);
}

// ── Journal fetch ────────────────────────────────────────────────────────────

export async function fetchJobEvents(
  jobId: string,
  after: number
): Promise<{ job: GenJob; events: GenEvent[] } | null> {
  const r = await apiFetch(`${API_BASE}/conversations/jobs/${jobId}/events?after=${after}`);
  if (r.status === 404) return null; // pruned or unknown — nothing to recover
  if (!r.ok) throw new Error(`journal fetch failed: ${r.status}`);
  return (await r.json()) as { job: GenJob; events: GenEvent[] };
}

/** Parsed accumulation of journal events into message parts. */
export interface ReplayedReply {
  text: string;
  thinking: string;
  messageId: string | null;
  sources: unknown[] | null;
  /** Server-authored activity events, in journal order (WP4 replay). */
  activity: Record<string, unknown>[];
  /** Code-generation pipeline progress frames, in journal order. */
  codeProgress: Record<string, unknown>[];
  done: boolean;
  failed: boolean;
  lastSeq: number;
}

export function foldEvents(acc: ReplayedReply, events: GenEvent[]): ReplayedReply {
  const next = { ...acc };
  for (const ev of events) {
    next.lastSeq = Math.max(next.lastSeq, ev.seq);
    if (ev.kind === "done") {
      next.done = true;
      continue;
    }
    if (ev.kind === "failed") {
      next.failed = true;
      continue;
    }
    if (!ev.payload) continue;
    try {
      const p = JSON.parse(ev.payload) as Record<string, unknown>;
      if (typeof p.token === "string") next.text += p.token;
      if (typeof p.thinking === "string") next.thinking += p.thinking;
      if (typeof p.message_id === "string") next.messageId = p.message_id;
      if (Array.isArray(p.sources)) next.sources = p.sources;
      if (p.activity && typeof p.activity === "object" && !Array.isArray(p.activity)) {
        next.activity = [...next.activity, p.activity as Record<string, unknown>];
      }
      if (p.code_progress && typeof p.code_progress === "object" && !Array.isArray(p.code_progress)) {
        next.codeProgress = [...next.codeProgress, p.code_progress as Record<string, unknown>];
      }
    } catch {
      /* malformed event — skip */
    }
  }
  return next;
}

export function emptyReplay(): ReplayedReply {
  return {
    text: "",
    thinking: "",
    messageId: null,
    sources: null,
    activity: [],
    codeProgress: [],
    done: false,
    failed: false,
    lastSeq: 0,
  };
}
