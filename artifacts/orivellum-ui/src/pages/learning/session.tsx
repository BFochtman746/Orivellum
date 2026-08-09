/**
 * Learning session — full-screen focused study flow for one Work.
 *
 * Wraps the existing Socratic quiz/assess loop (LearnTab) with minimal
 * chrome: the Work name, an "End session" exit back to the hub, and nothing
 * else — a distraction-free study screen. Session-limit behavior lives
 * inside LearnTab and is untouched.
 */
import { useParams, useLocation } from "wouter";
import { useGetWork } from "@workspace/api-client-react";
import { X } from "lucide-react";
import { LearnTab } from "@/pages/learning/learn-tab";

export default function LearningSession() {
  const { workId } = useParams<{ workId: string }>();
  const [, setLocation] = useLocation();

  const { data: workResp } = useGetWork(workId ?? "", {
    query: { enabled: !!workId },
  } as any);
  const title = (workResp as any)?.work?.title;

  if (!workId) {
    return (
      <div className="gd-panel text-center py-12 mt-4">
        <p className="text-[15px]">No Work selected.</p>
        <button className="gd-chip mt-4" onClick={() => setLocation("/learning")}>
          Back to Learning
        </button>
      </div>
    );
  }

  return (
    <div className="pb-10">
      {/* Session header — the only chrome on this screen */}
      <div
        className="flex items-center gap-3 pt-2 pb-3 mb-4"
        style={{ borderBottom: "1px solid var(--gd-line)" }}
      >
        <div className="min-w-0 flex-1">
          <p className="gd-eyebrow">Study session</p>
          <h2
            className="mt-0.5 truncate"
            style={{
              fontFamily: "var(--gd-display)",
              fontSize: 18,
              fontWeight: 600,
              letterSpacing: "0.03em",
              color: "var(--gd-text)",
            }}
          >
            {title || "…"}
          </h2>
        </div>
        <button
          className="gd-chip"
          onClick={() => setLocation("/learning")}
          data-testid="button-end-session"
          aria-label="End session and return to Learning"
        >
          <X className="w-3.5 h-3.5" aria-hidden /> End session
        </button>
      </div>

      <LearnTab workId={workId} />
    </div>
  );
}
