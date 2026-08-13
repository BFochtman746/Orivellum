/**
 * Home — the "Today" cockpit (WP1).
 *
 * Answers three questions in one glance: Where was I? What needs me?
 * What can I do now? Phone order (spec):
 *   1. Compact brand/status header — provided by the ResponsiveShell.
 *   2. Continue card (last conversation or Work) with one Resume action.
 *   3. Four 48px quick actions: Ask, Capture, Import, New Work.
 *   4. Active work — at most three items, then "See all."
 *   5. Needs review — the unified review inbox, ordered by consequence.
 *   6. Recent conversations, collapsed by default.
 *   7. Narrow saved/backup status ribbon.
 * Healthy system telemetry is NOT a card — only exceptions get space.
 */
import { useMemo, useState } from "react";
import { Link } from "wouter";
import {
  useGetBriefing,
  useGetReviewQueue,
  useListBackups,
  useListConversations,
  useListWorks,
} from "@workspace/api-client-react";
import type { Conversation, Work } from "@workspace/api-client-react";
import {
  ArchiveRestore,
  ChevronDown,
  ChevronRight,
  FilePlus2,
  MessageSquare,
  NotebookPen,
  Play,
  Upload,
} from "lucide-react";
import { WeatherCard } from "@/components/weather-card";

/** Compact relative time — "now", "12m", "3h", "2d", else a short date. */
function relTime(iso?: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function newest<T>(items: T[], at: (x: T) => string | undefined): T | null {
  let best: T | null = null;
  let bestT = -1;
  for (const it of items) {
    const t = Date.parse(at(it) ?? "");
    if (!Number.isNaN(t) && t > bestT) {
      bestT = t;
      best = it;
    }
  }
  return best;
}

const QUICK_ACTIONS = [
  { id: "ask", label: "Ask", icon: MessageSquare, href: "/chat" },
  { id: "capture", label: "Capture", icon: NotebookPen, href: "/notes" },
  { id: "import", label: "Import", icon: Upload, href: "/library" },
  { id: "new-work", label: "New Work", icon: FilePlus2, href: "/works?create=1" },
] as const;

export default function HomeScreen() {
  const { data: briefing } = useGetBriefing();
  const { data: convResp } = useListConversations({ archived: false, limit: 10 });
  const { data: worksResp } = useListWorks();
  const { data: reviewResp } = useGetReviewQueue({ limit: 5 });
  const { data: backupsResp } = useListBackups();
  const [convsOpen, setConvsOpen] = useState(false);

  const conversations = useMemo(
    () => (convResp?.conversations ?? []).filter((c) => !c.archived),
    [convResp],
  );
  const works = useMemo(() => {
    const list = [...(worksResp?.works ?? [])];
    list.sort((a, b) => Date.parse(b.updated_at ?? "") - Date.parse(a.updated_at ?? ""));
    return list;
  }, [worksResp]);

  // Continue = the most recently touched thing: conversation or Work.
  const lastConv = newest(conversations, (c) => c.updated_at);
  const lastWork = works[0] ?? null;
  const continueConv =
    lastConv &&
    (!lastWork ||
      Date.parse(lastConv.updated_at ?? "") >= Date.parse(lastWork.updated_at ?? ""));

  const reviewItems = reviewResp?.items ?? [];
  const reviewTotal = reviewResp?.total ?? 0;

  const lastBackup = newest(backupsResp?.backups ?? [], (b) => b.created_at);

  return (
    <div className="flex flex-col gap-5 pb-4" data-testid="home-cockpit">
      {briefing?.greeting && (
        <p className="gd-eyebrow" style={{ paddingTop: 4 }} data-testid="text-greeting">
          {briefing.greeting}
        </p>
      )}

      {/* 2 — Continue */}
      {(lastConv || lastWork) && (
        <ContinueCard conv={continueConv ? lastConv : null} work={continueConv ? null : lastWork} />
      )}

      {/* 3 — Quick actions (48px thumb targets) */}
      <div className="grid grid-cols-4 gap-2">
        {QUICK_ACTIONS.map((qa) => {
          const Icon = qa.icon;
          return (
            <Link
              key={qa.id}
              href={qa.href}
              className="gd-tile items-center justify-center text-center"
              style={{ minHeight: 64, gap: 6, padding: 8 }}
              data-testid={`action-${qa.id}`}
            >
              <Icon className="w-5 h-5" strokeWidth={1.75} aria-hidden style={{ color: "var(--gd-accent, var(--gd-sonar))" }} />
              <span className="text-[11px] font-medium" style={{ color: "var(--gd-text)" }}>
                {qa.label}
              </span>
            </Link>
          );
        })}
      </div>

      {/* 4 — Active work (max 3, then See all) */}
      <section>
        <div className="flex items-baseline justify-between" style={{ padding: "0 4px 8px" }}>
          <p className="gd-eyebrow">Active work</p>
          <Link href="/writing" className="text-[12px]" style={{ color: "var(--gd-muted)" }} data-testid="link-see-all-works">
            See all
          </Link>
        </div>
        {works.length === 0 ? (
          <Link href="/works?create=1" className="gd-row" data-testid="row-no-works">
            <FilePlus2 className="w-4 h-4" style={{ color: "var(--gd-muted)" }} aria-hidden />
            <span className="text-[13px]" style={{ color: "var(--gd-muted)" }}>
              No Works yet — start one
            </span>
          </Link>
        ) : (
          <div className="flex flex-col gap-2">
            {works.slice(0, 3).map((w: Work) => (
              <Link key={w.id} href={`/works/${w.id}`} className="gd-row" data-testid={`row-work-${w.id}`}>
                <div className="min-w-0 flex-1">
                  <p className="text-[14px] truncate" style={{ color: "var(--gd-text)" }}>
                    {w.title}
                  </p>
                  <p className="text-[11.5px]" style={{ color: "var(--gd-dim)" }}>
                    {w.doc_count ?? 0} docs · {w.knowledge_count ?? 0} knowledge
                    {(w.pending_tasks ?? 0) > 0 ? ` · ${w.pending_tasks} tasks` : ""}
                  </p>
                </div>
                <span className="text-[11px] shrink-0" style={{ color: "var(--gd-dim)" }}>
                  {relTime(w.updated_at)}
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* 5 — Needs review (only exceptions deserve dashboard space) */}
      {reviewTotal > 0 && (
        <section data-testid="section-needs-review">
          <div className="flex items-baseline justify-between" style={{ padding: "0 4px 8px" }}>
            <p className="gd-eyebrow">Needs review</p>
            <Link href="/review" className="text-[12px]" style={{ color: "var(--gd-muted)" }} data-testid="link-review-all">
              All {reviewTotal}
            </Link>
          </div>
          <div className="flex flex-col gap-2">
            {reviewItems.slice(0, 3).map((item) => (
              <Link key={item.id} href="/review" className="gd-row" data-testid={`row-review-${item.id}`}>
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{
                    background:
                      item.severity === "high" || item.severity === "critical"
                        ? "var(--gd-danger)"
                        : item.severity === "medium"
                          ? "var(--gd-caution)"
                          : "var(--gd-slate)",
                  }}
                  aria-hidden
                />
                <div className="min-w-0 flex-1">
                  <p className="text-[13.5px] truncate" style={{ color: "var(--gd-text)" }}>
                    {item.title}
                  </p>
                  {item.item_type && (
                    <p className="text-[11px] uppercase tracking-wide" style={{ color: "var(--gd-dim)" }}>
                      {item.item_type.replace(/_/g, " ")}
                    </p>
                  )}
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* 6 — Recent conversations, collapsed by default */}
      {conversations.length > 0 && (
        <section>
          <button
            type="button"
            className="flex items-center gap-1 w-full"
            style={{ padding: "0 4px 8px", minHeight: 32 }}
            onClick={() => setConvsOpen((v) => !v)}
            aria-expanded={convsOpen}
            data-testid="button-toggle-conversations"
          >
            <p className="gd-eyebrow">Recent conversations</p>
            {convsOpen ? (
              <ChevronDown className="w-3.5 h-3.5" style={{ color: "var(--gd-dim)" }} aria-hidden />
            ) : (
              <ChevronRight className="w-3.5 h-3.5" style={{ color: "var(--gd-dim)" }} aria-hidden />
            )}
            <span className="ml-auto text-[11px]" style={{ color: "var(--gd-dim)" }}>
              {conversations.length}
            </span>
          </button>
          {convsOpen && (
            <div className="flex flex-col gap-2">
              {conversations.slice(0, 5).map((c: Conversation) => (
                <Link key={c.id} href={`/chat?id=${c.id}`} className="gd-row" data-testid={`row-conv-${c.id}`}>
                  <div className="min-w-0 flex-1">
                    <p className="text-[13.5px] truncate" style={{ color: "var(--gd-text)" }}>
                      {c.title || "Untitled conversation"}
                    </p>
                    {c.last_message && (
                      <p className="text-[11.5px] truncate" style={{ color: "var(--gd-dim)" }}>
                        {c.last_message}
                      </p>
                    )}
                  </div>
                  <span className="text-[11px] shrink-0" style={{ color: "var(--gd-dim)" }}>
                    {relTime(c.updated_at)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </section>
      )}

      <WeatherCard />

      {/* 7 — Narrow backup status ribbon */}
      <Link
        href="/backups"
        className="flex items-center gap-2"
        style={{
          minHeight: 40,
          padding: "0 12px",
          borderRadius: "var(--gd-r-sm)",
          border: "1px solid var(--gd-line)",
          background: "var(--gd-surface)",
        }}
        data-testid="ribbon-backup"
      >
        <ArchiveRestore
          className="w-4 h-4 shrink-0"
          style={{ color: lastBackup ? "var(--gd-success)" : "var(--gd-caution)" }}
          aria-hidden
        />
        <span className="text-[12px]" style={{ color: "var(--gd-muted)" }}>
          {lastBackup ? `Last backup ${relTime(lastBackup.created_at)} ago` : "No backups yet"}
        </span>
      </Link>
    </div>
  );
}

function ContinueCard({ conv, work }: { conv: Conversation | null; work: Work | null }) {
  const href = conv ? `/chat?id=${conv.id}` : work ? `/works/${work.id}` : "/";
  const kind = conv ? (conv.work_title ? `Conversation · ${conv.work_title}` : "Conversation") : "Work";
  const title = conv ? conv.title || "Untitled conversation" : work?.title ?? "";
  const sub = conv ? conv.last_message : work ? `${work.doc_count ?? 0} docs · ${work.knowledge_count ?? 0} knowledge` : "";
  return (
    <Link href={href} className="gd-tile" style={{ minHeight: 92 }} data-testid="card-continue">
      <div className="flex items-center gap-2">
        <p className="gd-eyebrow">Continue</p>
        <span className="text-[10.5px] uppercase tracking-wide ml-auto" style={{ color: "var(--gd-dim)" }}>
          {kind}
        </span>
      </div>
      <div className="flex items-center gap-3 mt-auto">
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-medium truncate" style={{ color: "var(--gd-text)" }}>
            {title}
          </p>
          {sub && (
            <p className="text-[12px] truncate" style={{ color: "var(--gd-muted)" }}>
              {sub}
            </p>
          )}
        </div>
        <span
          className="inline-flex items-center gap-1.5 shrink-0 text-[12px] font-semibold"
          style={{
            minHeight: 40,
            padding: "0 14px",
            borderRadius: "var(--gd-r-sm)",
            background: "var(--gd-accent-soft)",
            border: "1px solid var(--gd-accent)",
            color: "var(--gd-accent)",
          }}
        >
          <Play className="w-3.5 h-3.5" aria-hidden />
          Resume
        </span>
      </div>
    </Link>
  );
}
