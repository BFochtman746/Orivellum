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
      <div className="max-w-xl mx-auto py-20 text-center space-y-6 animate-in fade-in duration-300">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 border border-primary/20">
          <Inbox className="w-8 h-8 text-primary" />
        </div>
        <div className="space-y-2">
          <h1 className="text-2xl font-serif font-semibold">Load anything</h1>
          <p className="text-muted-foreground text-sm leading-relaxed max-w-sm mx-auto">
            Import a document to Orivellum and get an instant Intake Profile — what it is,
            where it belongs, and suggested next actions.
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <Button
            className="gap-2"
            onClick={() => setLocation("/library?import=1")}
          >
            <Upload className="w-4 h-4" />
            Import a Document
          </Button>
          <Button variant="outline" className="gap-2" asChild>
            <Link href="/library">
              <Library className="w-4 h-4" />
              Browse Library
            </Link>
          </Button>
        </div>
        <p className="text-xs text-muted-foreground font-mono">
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
        <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground" asChild>
          <Link href="/library">
            <ArrowLeft className="w-4 h-4" />
            Library
          </Link>
        </Button>
      </div>

      {/* Page header */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-primary" />
          <h1 className="text-xl font-serif font-semibold">Intake Profile</h1>
        </div>
        <p className="text-sm text-muted-foreground font-mono">
          doc: <code className="bg-muted px-1.5 py-0.5 rounded text-xs">{docId}</code>
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
