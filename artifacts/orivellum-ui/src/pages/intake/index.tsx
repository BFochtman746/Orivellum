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
import { Page, EmptyState, ErrorState } from "@/components/primitives";
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
      <Page eyebrow="Universal Intake" title="Load anything">
        <div className="animate-in fade-in duration-300">
          <EmptyState
            icon={<Inbox />}
            title="Import a document to get an Intake Profile"
            description="Orivellum tells you what it is, where it belongs, and suggested next actions. After import, a toast links here with the profile."
            action={
              <div className="flex flex-col sm:flex-row gap-3 justify-center">
                <Button className="gap-2 min-h-11" onClick={() => setLocation("/library?import=1")}>
                  <Upload className="w-4 h-4" />
                  Import a Document
                </Button>
                <Button variant="outline" className="gap-2 min-h-11" asChild>
                  <Link href="/library">
                    <Library className="w-4 h-4" />
                    Browse Library
                  </Link>
                </Button>
              </div>
            }
          />
        </div>
      </Page>
    );
  }

  // ── Has a doc ID ─────────────────────────────────────────────────────────
  return (
    <Page
      eyebrow="Universal Intake"
      title="Intake Profile"
      actions={
        <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground min-h-11" asChild>
          <Link href="/library">
            <ArrowLeft className="w-4 h-4" />
            Library
          </Link>
        </Button>
      }
    >
      <div className="space-y-6 animate-in fade-in duration-300">
        <p className="text-xs font-mono" style={{ color: "var(--gd-dim)" }}>
          <Sparkles className="w-3.5 h-3.5 inline mr-1.5" style={{ color: "var(--gd-bronze)" }} />
          doc:{" "}
          <code
            className="px-1.5 py-0.5 rounded text-xs"
            style={{ background: "var(--gd-primary-soft)", color: "var(--gd-primary)" }}
          >
            {docId}
          </code>
        </p>

        {loading && <IntakeProfileSkeleton />}

        {error && !loading && (
          <ErrorState
            title="Intake failed"
            detail={error}
            onRetry={() => fetchIntake(docId)}
          />
        )}

        {profile && !loading && (
          <>
            <IntakeProfileCard
              profile={profile}
              onLinkWork={handleLinkWork}
              onFindGaps={handleFindGaps}
              onRetry={() => fetchIntake(docId!)}
            />

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
    </Page>
  );
}
