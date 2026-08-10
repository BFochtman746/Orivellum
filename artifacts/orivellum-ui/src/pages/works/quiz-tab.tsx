import { useState, useEffect, useRef, useMemo } from "react";
import { useParams, Link, useLocation, useSearch } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";
import {
  useGetWork,
  useGetWorkStats,
  useUpdateWork,
  useDeleteWork,
  useDeleteKnowledgeItem,
  useGetWorkDocuments,
  useGetWorkKnowledge,
  useGetWorkTasks,
  useGetWorkConversations,
  useCreateWorkTask,
  useUpdateWorkTask,
  useCreateConversation,
  useListLibrary,
  getGetWorkQueryKey,
  getGetWorkStatsQueryKey,
  getListWorksQueryKey,
  getGetWorkTasksQueryKey,
  getGetWorkDocumentsQueryKey,
  getGetWorkKnowledgeQueryKey,
  getGetWorkConversationsQueryKey,
  getListConversationsQueryKey,
  useGetEmbeddingsStatus,
  getGetEmbeddingsStatusQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient, useQuery, useMutation } from "@tanstack/react-query";
import { format } from "date-fns";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  ArrowLeft,
  FileText,
  Network,
  CheckSquare,
  MessageSquare,
  Plus,
  Clock,
  Loader2,
  Sparkles,
  ThumbsUp,
  ThumbsDown,
  Pencil,
  Check,
  X,
  Trash2,
  GraduationCap,
  RefreshCw,
  ChevronRight,
  MessageSquarePlus,
  Unlink,
  Search,
  BookOpen,
  ChevronDown,
  Trophy,
  BarChart2,
  AlertTriangle,
  TrendingUp,
  Lightbulb,
  Brain,
  Star,
  GitBranch,
  Share2,
  FileSpreadsheet,
  FileType,
  Presentation,
  Package,
  Download,
  Zap,
  Film,
  Scroll,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { BookTab }       from "./book-tab";
import { BrainstormTab } from "./brainstorm-tab";
import { TrailerTab }    from "./trailer-tab";
import { GenesisTab }    from "./genesis-tab";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { KnowledgeGraph, GNode } from "@/components/knowledge-graph";
import { LearnTab } from "@/pages/learning/learn-tab";


interface QuizQuestion {
  q: string;
  options: string[];
  answer: number;
  explanation: string;
  /** Concept this question tests — present when the Work has seeded concepts */
  concept_id?: string;
}

interface QuizFeedback {
  is_correct: boolean;
  feedback: string;
  score: number;
  route: string;
  /** True when correctness came from the backend /learning/assess call; false = local quiz-answer-index contract */
  assessed: boolean;
}

interface MasterySummary {
  total: number;
  graduated: number;
  mastery_pct: number;
}

type QuizPhase = "idle" | "loading" | "question" | "submitting" | "feedback" | "summary";

const API_BASE_QUIZ = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export function QuizTab({ workId, workTitle }: { workId: string; workTitle: string }) {
  const [phase, setPhase]           = useState<QuizPhase>("idle");
  const [questions, setQuestions]   = useState<QuizQuestion[]>([]);
  const [conceptIds, setConceptIds] = useState<string[]>([]);
  const [current, setCurrent]       = useState(0);
  const [selected, setSelected]     = useState<number | null>(null);
  const [feedback, setFeedback]     = useState<QuizFeedback | null>(null);
  const [sessionResults, setSessionResults] = useState<{ correct: boolean }[]>([]);
  const [masterySummary, setMasterySummary] = useState<MasterySummary | null>(null);
  const [error, setError]           = useState<string | null>(null);

  const reset = () => {
    setPhase("idle");
    setQuestions([]);
    setConceptIds([]);
    setCurrent(0);
    setSelected(null);
    setFeedback(null);
    setSessionResults([]);
    setMasterySummary(null);
    setError(null);
  };

  const generate = async () => {
    setPhase("loading");
    setError(null);
    try {
      // Fetch quiz questions and concepts in parallel
      const [quizResp, conceptsResp] = await Promise.all([
        apiFetch(`${API_BASE_QUIZ}/works/${workId}/quiz`, { method: "POST" }),
        apiFetch(`${API_BASE_QUIZ}/works/${workId}/learning/concepts`),
      ]);
      if (!quizResp.ok) {
        const body = await quizResp.json().catch(() => ({}));
        throw new Error((body as any).detail ?? `HTTP ${quizResp.status}`);
      }
      const quizData = await quizResp.json();
      const qs: QuizQuestion[] = quizData.questions ?? [];
      if (qs.length === 0) throw new Error("No questions returned — try again");

      // Extract concept IDs for mastery submission (may be empty)
      const cIds: string[] = conceptsResp.ok
        ? ((await conceptsResp.json()).concepts ?? []).map((c: any) => c.id as string)
        : [];

      setQuestions(qs);
      setConceptIds(cIds);
      setCurrent(0);
      setSelected(null);
      setFeedback(null);
      setSessionResults([]);
      setPhase("question");
    } catch (err: any) {
      setError(err.message ?? "Failed to generate quiz");
      setPhase("idle");
    }
  };

  const submitAnswer = async (optionIndex: number) => {
    const q = questions[current];
    if (!q || phase === "submitting") return;
    setSelected(optionIndex);
    setPhase("submitting");

    // Graduation / pass threshold — must match learning.py _GRAD_THRESHOLD = 0.75
    const PASS_THRESHOLD = 0.75;

    // Local fallback — quiz generator's canonical answer is authoritative.
    // assessed:false so the render path uses q.answer for option highlighting.
    let fb: QuizFeedback = {
      is_correct: optionIndex === q.answer,
      feedback: q.explanation,
      score: optionIndex === q.answer ? 1.0 : 0.0,
      route: optionIndex === q.answer ? "STEP_FORWARD" : "STAY_HERE",
      assessed: false,
    };

    // Only call assess when the backend has tagged this question with a validated concept_id.
    // Round-robin assignment is intentionally absent — an untagged question skips mastery.
    if (q.concept_id) {
      try {
        const r = await apiFetch(`${API_BASE_QUIZ}/works/${workId}/learning/assess`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            concept_id: q.concept_id,
            question: q.q,
            answer: q.options[optionIndex],
          }),
        });
        if (r.ok) {
          const data = await r.json();
          // Replace the local fallback entirely — assessed:true signals the render path
          // to use backend score as the single correctness contract (no q.answer mixing).
          fb = {
            is_correct: (data.score ?? 0) >= PASS_THRESHOLD,
            feedback: data.feedback || q.explanation,
            score: data.score ?? fb.score,
            route: data.route ?? fb.route,
            assessed: true,
          };
          if (data.summary) setMasterySummary(data.summary);
        }
        // If r.ok is false: leave local fb (assessed:false), do not update mastery
      } catch {
        // Network/parse error — leave local fb, never block the quiz
      }
    }

    setFeedback(fb);
    setSessionResults(prev => [...prev, { correct: fb.is_correct }]);
    setPhase("feedback");
  };

  const nextQuestion = () => {
    if (current + 1 >= questions.length) {
      setPhase("summary");
    } else {
      setCurrent(c => c + 1);
      setSelected(null);
      setFeedback(null);
      setPhase("question");
    }
  };

  // ── Idle / Loading ──────────────────────────────────────────────────────────
  if (phase === "idle" || phase === "loading") {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-6">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
          <GraduationCap className="w-8 h-8 text-primary" />
        </div>
        <div className="text-center space-y-1 max-w-sm">
          <h3 className="font-serif text-xl font-medium">Adaptive Quiz</h3>
          <p className="text-sm text-muted-foreground">
            Test your understanding of <span className="font-medium text-foreground">{workTitle}</span>.
            Orivellum generates 5 questions from your knowledge base and updates your mastery score as you go.
          </p>
        </div>
        {error && (
          <div className="px-4 py-3 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive max-w-sm text-center">
            {error}
          </div>
        )}
        <Button onClick={generate} disabled={phase === "loading"} className="gap-2 px-8">
          {phase === "loading"
            ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating…</>
            : <><Sparkles className="w-4 h-4" /> Generate Quiz</>}
        </Button>
      </div>
    );
  }

  // ── Summary ─────────────────────────────────────────────────────────────────
  if (phase === "summary") {
    const correctCount = sessionResults.filter(r => r.correct).length;
    const total = sessionResults.length;
    const pct = total > 0 ? Math.round((correctCount / total) * 100) : 0;
    const tier = pct >= 80 ? "excellent" : pct >= 60 ? "good" : "review";
    return (
      <div className="max-w-lg mx-auto py-12 space-y-6 text-center">
        <div
          className="p-6 rounded-2xl border space-y-3"
          style={
            tier === "excellent" ? { background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 30%, transparent)" }
            : tier === "good"    ? { background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }
            : { background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 30%, transparent)" }
          }
        >
          <Trophy className="w-10 h-10 mx-auto" style={{ color: tier === "excellent" ? "var(--green-2)" : tier === "good" ? "var(--gilt)" : "var(--rust)" }} />
          <p className="text-3xl font-serif font-bold">{correctCount}/{total}</p>
          <p className="text-sm text-muted-foreground">
            {tier === "excellent" ? "Excellent! You've mastered this material." : tier === "good" ? "Good effort — a bit more practice and you'll have it." : "Keep studying — review the knowledge items for this Work."}
          </p>
        </div>
        {masterySummary && (
          <div className="p-4 rounded-xl bg-muted/30 border border-border/40 space-y-2">
            <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">Mastery updated</p>
            <div className="flex items-center gap-3">
              <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${masterySummary.mastery_pct}%`, background: "color-mix(in srgb, var(--green-2) 70%, transparent)" }}
                />
              </div>
              <span className="text-sm font-semibold font-mono tabular-nums">
                {masterySummary.mastery_pct}%
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {masterySummary.graduated}/{masterySummary.total} concepts graduated
            </p>
          </div>
        )}
        {!masterySummary && conceptIds.length === 0 && (
          <p className="text-xs text-muted-foreground italic">
            Seed learning concepts in the Learn tab to track mastery progress.
          </p>
        )}
        <Button onClick={reset} variant="outline" className="gap-2">
          <RefreshCw className="w-4 h-4" /> New Quiz
        </Button>
      </div>
    );
  }

  // ── Active question / feedback ───────────────────────────────────────────────
  const q = questions[current];
  if (!q) return null;
  const isFeedback = phase === "feedback";
  const isSubmitting = phase === "submitting";

  return (
    <div className="max-w-2xl mx-auto py-6 space-y-5">
      {/* Progress bar */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-primary/60 rounded-full transition-all duration-300"
            style={{ width: `${(current / questions.length) * 100}%` }}
          />
        </div>
        <span className="text-xs font-mono text-muted-foreground shrink-0">
          {current + 1} / {questions.length}
        </span>
      </div>

      {/* Question card */}
      <Card className="p-6 space-y-4">
        <p className="font-medium text-base leading-relaxed">{q.q}</p>

        <div className="space-y-2.5">
          {q.options.map((opt, oi) => {
            const isChosen = selected === oi;
            // Single correctness contract:
            // - assessed:true  → backend score is authoritative; only highlight the chosen option
            // - assessed:false → quiz answer index is authoritative; also highlight the canonical answer
            const showCorrect = isFeedback && feedback
              ? feedback.assessed
                ? isChosen && feedback.is_correct           // backend says we got it right
                : oi === q.answer                           // quiz canonical answer
              : false;
            const showWrong = isFeedback && feedback
              ? isChosen && !feedback.is_correct            // same contract: chosen was wrong
              : false;

            let cls = "flex items-center gap-3 px-4 py-3 rounded-lg border text-sm transition-colors ";
            let optStyle: React.CSSProperties | undefined;
            if (isFeedback) {
              if (showCorrect)        optStyle = { background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 40%, transparent)", color: "var(--green-2)" };
              else if (showWrong)     optStyle = { background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 40%, transparent)", color: "var(--rust)" };
              else               cls += "border-border/30 text-muted-foreground/60";
            } else {
              cls += isChosen
                ? "bg-primary/10 border-primary/50 text-primary cursor-pointer"
                : "border-border/50 hover:bg-muted/40 hover:border-border cursor-pointer";
              if (isSubmitting) cls += " opacity-60 pointer-events-none";
            }
            return (
              <div key={oi} className={cls} style={optStyle} onClick={() => !isFeedback && !isSubmitting && submitAnswer(oi)}>
                <span className="w-6 h-6 rounded-full border border-current flex items-center justify-center text-[10px] font-mono shrink-0">
                  {String.fromCharCode(65 + oi)}
                </span>
                <span className="flex-1">{opt}</span>
                {showCorrect && <Check className="w-4 h-4 shrink-0" style={{ color: "var(--green-2)" }} />}
                {showWrong   && <X    className="w-4 h-4 shrink-0" style={{ color: "var(--rust)" }} />}
              </div>
            );
          })}
        </div>

        {isSubmitting && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground pt-1">
            <Loader2 className="w-3 h-3 animate-spin" />
            <span>Scoring…</span>
          </div>
        )}

        {/* Feedback block */}
        {isFeedback && feedback && (
          <div
            className="rounded-lg border p-3 space-y-1"
            style={feedback.is_correct
              ? { background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 30%, transparent)" }
              : { background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }}
          >
            <p className="text-xs font-semibold" style={{ color: feedback.is_correct ? "var(--green-2)" : "var(--gilt)" }}>
              {feedback.is_correct ? "✓ Correct" : "✗ Incorrect"}
            </p>
            {feedback.feedback && (
              <p className="text-xs text-muted-foreground leading-relaxed">{feedback.feedback}</p>
            )}
          </div>
        )}
      </Card>

      {/* Next button */}
      {isFeedback && (
        <div className="flex justify-end">
          <Button onClick={nextQuestion} className="gap-2">
            {current + 1 >= questions.length ? <><Trophy className="w-4 h-4" /> See Results</> : <><ChevronRight className="w-4 h-4" /> Next Question</>}
          </Button>
        </div>
      )}
    </div>
  );
}
