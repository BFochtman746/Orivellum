/**
 * /intake — Universal Intake page.
 *
 * Accepts a ?doc=<id> query param. If present, immediately runs the intake
 * pipeline and shows the Intake Profile card. Without a param, shows an
 * upload prompt that redirects to the Library.
 *
 * This is the "Load anything" destination: linked from library upload toasts
 * and the dashboard quick-action area.
 */
import { useState, useEffect } from "react";
import { useSearch, useLocation } from "wouter";
import { Loader2, Upload, Inbox, Library, ArrowLeft, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { IntakeProfileCard, IntakeProfileSkeleton, type IntakeProfile } from "@/components/intake-profile-card";
import { apiFetch } from "@/lib/auth";
import { toast } from "sonner";
import { Link } from "wouter";
import { useQueryClient } from "@tanstack/react-query";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export default function IntakePage() {
  const searchStr = useSearch();
  const [, setLocation] = useLocation();
  const docId = new URLSearchParams(searchStr).get("doc") ?? "";

  const [profile, setProfile] = useState<IntakeProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchIntake = (id: string) => {
    setLoading(true);
    setError(null);
    apiFetch(`${BASE}/intake`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id: id }),
    })
      .then(async r => {
        if (!r.ok) {
          const e = await r.json().catch(() => ({}));
          throw new Error((e as any).detail ?? `HTTP ${r.status}`);
        }
        return r.json() as Promise<IntakeProfile>;
      })
      .then(setProfile)
      .catch(e => setError(e.message ?? "Intake failed"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!docId) return;
    fetchIntake(docId);
  }, [docId]);

  const handleLinkWork = (dId: string) => {
    setLocation(`/library/${dId}?tab=overview`);
    toast.info("Use the 'Link to Work' dropdown on the Overview tab");
  };

  const handleFindGaps = (dId: string) => {
    if (profile?.filed_to_id) {
      setLocation(`/works/${profile.filed_to_id}?tab=gaps`);
    } else {
      setLocation("/works");
      toast.info("Link this document to a Work first, then visit the Gaps tab");
    }
  };

  // ── No doc ID — show the empty state / prompt ──────────────────────────────
  if (!docId) {
    return (
      <div className="max-w-xl mx-auto py-16 text-center space-y-6 animate-in fade-in duration-300">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl"
             style={{ background: 'var(--green-soft)', border: '1px solid var(--line)' }}>
          <Inbox className="w-8 h-8" style={{ color: 'var(--green-raw)' }} />
        </div>
        <div className="space-y-0">
          <span className="eyebrow">Universal Intake</span>
          <h1 className="vellum-h1">Load anything</h1>
          <div className="gilt-rule w-24 mx-auto" />
          <p className="text-[13px] leading-relaxed max-w-sm mx-auto mt-2.5" style={{ color: 'var(--ink-soft)' }}>
            Import a document to Orivellum and get an instant Intake Profile — what it is,
            where it belongs, and suggested next actions.
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <Button
            className="gap-2 min-h-[44px]"
            onClick={() => setLocation("/library?import=1")}
          >
            <Upload className="w-4 h-4" />
            Import a Document
          </Button>
          <Button variant="outline" className="gap-2 min-h-[44px]" asChild>
            <Link href="/library">
              <Library className="w-4 h-4" />
              Browse Library
            </Link>
          </Button>
        </div>
        <p className="eyebrow" style={{ letterSpacing: '0.12em', opacity: 0.7 }}>
          After import, a toast will link here with the Intake Profile.
        </p>
      </div>
    );
  }

  // ── Has a doc ID ─────────────────────────────────────────────────────────
  return (
    <div className="max-w-2xl mx-auto space-y-6 animate-in fade-in duration-300">
      {/* Back nav */}
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground min-h-[44px]" asChild>
          <Link href="/library">
            <ArrowLeft className="w-4 h-4" />
            Library
          </Link>
        </Button>
      </div>

      {/* Page header — VELLUM pattern */}
      <div>
        <span className="eyebrow mb-1">Universal Intake</span>
        <h1 className="vellum-h1 flex items-center gap-2">
          <Sparkles className="w-5 h-5 shrink-0" style={{ color: 'var(--gilt)' }} />
          Intake Profile
        </h1>
        <div className="gilt-rule w-28" />
        <p className="text-[12px] mt-1 font-mono" style={{ color: 'var(--ink-faint)' }}>
          doc:{' '}
          <code className="px-1.5 py-0.5 rounded text-xs"
                style={{ background: 'var(--green-soft)', color: 'var(--green-raw)' }}>
            {docId}
          </code>
        </p>
      </div>

      {/* Profile card */}
      {loading && <IntakeProfileSkeleton />}

      {error && !loading && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      {profile && !loading && (
        <>
          <IntakeProfileCard
            profile={profile}
            onLinkWork={handleLinkWork}
            onFindGaps={handleFindGaps}
            onRetry={() => fetchIntake(docId!)}
          />

          {/* Document link */}
          <div className="flex justify-center">
            <Button variant="ghost" size="sm" className="text-muted-foreground gap-2 text-xs" asChild>
              <Link href={`/library/${docId}`}>
                <Library className="w-3.5 h-3.5" />
                View full document details
              </Link>
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
