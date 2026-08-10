/**
 * A-01 Mail Steward — /mail/compose/:actionRequestId
 * Compose and optionally send a reply draft.
 */
import { useState, useEffect } from "react";
import { useParams, useSearch, useLocation } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  ArrowLeft, Send, Save, Shield, ShieldCheck, Loader2, AlertTriangle,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Exported send-flow (injectable for unit tests) ────────────────────────────

export interface WebSendFlowResponse {
  ok: boolean;
  statusText: string;
  json: () => Promise<unknown>;
}
export type WebSendFlowFetch = (url: string, opts?: RequestInit) => Promise<WebSendFlowResponse>;

export interface WebSendFlowResult {
  success: boolean;
  error: string | null;
  /** Every URL called, in order. Lets tests assert absent calls. */
  calledUrls: string[];
}

/**
 * Executes the ordered 3-step send chain for the web compose screen:
 *   1. PATCH /mail/drafts/:actionId     — persist current editor text
 *   2. POST  /mail/decisions/:recordId/send-nonce — fresh single-use token
 *   3. POST  /mail/decisions/:recordId/send       — deliver
 *
 * Exported so unit tests can import this function directly.
 * The component's handleSend calls it with apiFetch as fetchFn.
 * fetchFn must return a Response-like object with .ok and .statusText.
 */
export async function executeWebSendFlow(
  actionId: string,
  recordId: string,
  bodyText: string | null,
  fetchFn: WebSendFlowFetch,
  base: string,
): Promise<WebSendFlowResult> {
  const calledUrls: string[] = [];

  // Step 1 — persist latest edits; abort on any error (HTTP or network)
  const patchUrl = `${base}/mail/drafts/${actionId}`;
  calledUrls.push(patchUrl);
  let saveRes: WebSendFlowResponse;
  try {
    saveRes = await fetchFn(patchUrl, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body_text: bodyText }),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Network error saving draft";
    return { success: false, error: msg, calledUrls };
  }
  if (!saveRes.ok) {
    return {
      success: false,
      error: "Draft save failed: " + saveRes.statusText,
      calledUrls,
    };
  }

  // Step 2 — fresh single-use nonce; abort on any error or bad JSON
  const nonceUrl = `${base}/mail/decisions/${recordId}/send-nonce`;
  calledUrls.push(nonceUrl);
  let nonceRes: WebSendFlowResponse;
  try {
    nonceRes = await fetchFn(nonceUrl, { method: "POST" });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Network error fetching nonce";
    return { success: false, error: msg, calledUrls };
  }
  if (!nonceRes.ok) {
    return {
      success: false,
      error: "Could not obtain send nonce: " + nonceRes.statusText,
      calledUrls,
    };
  }
  let nonce: string;
  try {
    const nonceJson = (await nonceRes.json()) as { nonce?: unknown };
    if (typeof nonceJson.nonce !== "string" || nonceJson.nonce.length === 0) {
      return { success: false, error: "Server returned an invalid nonce", calledUrls };
    }
    nonce = nonceJson.nonce;
  } catch {
    return { success: false, error: "Invalid nonce response from server", calledUrls };
  }

  // Step 3 — deliver; abort on any error
  const sendUrl = `${base}/mail/decisions/${recordId}/send`;
  calledUrls.push(sendUrl);
  let sendRes: WebSendFlowResponse;
  try {
    sendRes = await fetchFn(sendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_request_id: actionId, nonce }),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Network error sending";
    return { success: false, error: msg, calledUrls };
  }
  if (!sendRes.ok) {
    const detail = (await sendRes.json().catch(() => ({}))) as { detail?: string };
    return { success: false, error: detail?.detail ?? "Send failed", calledUrls };
  }
  return { success: true, error: null, calledUrls };
}

function parseSearch(search: string): Record<string, string> {
  const out: Record<string, string> = {};
  const q = search.startsWith("?") ? search.slice(1) : search;
  for (const part of q.split("&")) {
    const [k, v] = part.split("=");
    if (k) out[decodeURIComponent(k)] = decodeURIComponent(v ?? "");
  }
  return out;
}

export default function ComposePage() {
  const params     = useParams<{ actionRequestId: string }>();
  const search     = useSearch();
  const qs         = parseSearch(search);
  const recordId   = qs["recordId"] || "";
  // sendNonce is NOT stored in the URL. A fresh nonce is fetched at send time
  // via POST /api/mail/decisions/{id}/send-nonce to avoid exposing single-use
  // authorization values in browser history or server logs.
  const actionId   = params.actionRequestId;
  const [, navigate] = useLocation();
  const qc = useQueryClient();

  const [bodyText, setBodyText]   = useState<string | null>(null);
  const [saving,   setSaving]     = useState(false);
  const [sending,  setSending]    = useState(false);

  // Load decision detail for context (subject, assessment, send_enabled)
  const { data: detail, isLoading } = useQuery<any>({
    queryKey: ["mail-decision", recordId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/mail/decisions/${recordId}`);
      if (!r.ok) throw new Error("Decision not found");
      return r.json();
    },
    enabled: !!recordId,
  });

  const { data: summary } = useQuery<{ send_enabled: boolean }>({
    queryKey: ["mail-summary"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/mail/summary`);
      return r.json();
    },
  });

  // Initialise body from suggested reply
  useEffect(() => {
    if (bodyText === null && detail?.assessment?.suggested_reply) {
      setBodyText(detail.assessment.suggested_reply);
    }
  }, [detail, bodyText]);

  const handleSave = async () => {
    if (!actionId) return;
    setSaving(true);
    try {
      const r = await apiFetch(`${BASE}/mail/drafts/${actionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body_text: bodyText }),
      });
      if (!r.ok) throw new Error("Save failed");
      toast.success("Draft updated in Outlook");
    } catch (e: any) {
      toast.error(e.message || "Failed to save draft");
    } finally {
      setSaving(false);
    }
  };

  const handleSend = async () => {
    if (!actionId || !recordId) return;
    setSending(true);
    try {
      // Delegates to the exported executeWebSendFlow: PATCH → nonce → send.
      // Aborts at the first non-ok response so we never deliver a stale draft.
      const result = await executeWebSendFlow(
        actionId, recordId, bodyText, apiFetch as WebSendFlowFetch, BASE,
      );
      if (!result.success) {
        toast.error(result.error ?? "Failed to send");
        return;
      }
      toast.success("Reply sent via Outlook");
      qc.invalidateQueries({ queryKey: ["mail-attention"] });
      qc.invalidateQueries({ queryKey: ["mail-summary"] });
      navigate("/mail");
    } finally {
      setSending(false);
    }
  };

  const record     = detail?.record;
  const assessment = detail?.assessment;
  const sendEnabled = !!summary?.send_enabled;

  return (
    <div className="flex-1 flex flex-col overflow-hidden max-w-4xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-border/40">
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => navigate("/mail")}>
          <ArrowLeft size={14} />
        </Button>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold truncate">
            Re: {record?.subject || "…"}
          </p>
          {record && (
            <p className="text-xs text-muted-foreground">
              to @{record.sender_domain}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={handleSave}
            disabled={saving || !actionId}
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            Save draft
          </Button>
          {sendEnabled && (
            <Button
              size="sm"
              className="gap-2"
              onClick={handleSend}
              disabled={sending || !actionId}
            >
              {sending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
              Send
            </Button>
          )}
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Editor */}
        <div className="flex-1 flex flex-col overflow-hidden p-5">
          {isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-48 w-full" />
            </div>
          ) : (
            <>
              {!sendEnabled && (
                <div className="mb-3 rounded p-2.5 text-xs border flex items-start gap-2"
                  style={{ borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" }}>
                  <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                  <span>
                    Send is disabled. This draft will be saved in Outlook and you can send it from there.
                    Enable send in <button className="underline" onClick={() => navigate("/mail/settings")}>Mail settings</button>.
                  </span>
                </div>
              )}
              <Textarea
                className="flex-1 resize-none font-mono text-sm min-h-[280px]"
                placeholder="Write your reply…"
                value={bodyText ?? ""}
                onChange={e => setBodyText(e.target.value)}
              />
            </>
          )}
        </div>

        {/* Sidebar */}
        <div className="w-56 shrink-0 border-l border-border/40 overflow-y-auto p-4 space-y-4">
          {assessment && (
            <div className="glass-card rounded-lg p-3 space-y-2">
              <div className="flex items-center gap-1.5">
                <Shield size={12} style={{ color: "var(--gilt)" }} />
                <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--gilt)" }}>Assessment</span>
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">{assessment.rationale}</p>
              {assessment.is_high_risk && (
                <Badge variant="destructive" className="text-[10px]">High risk</Badge>
              )}
              {assessment.injection_flagged && (
                <Badge variant="outline" className="text-[10px]" style={{ color: "var(--gilt)", borderColor: "var(--gilt)" }}>Injection flag</Badge>
              )}
            </div>
          )}
          <div className="glass-card rounded-lg p-3 space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Audit</p>
            <p className="text-xs text-muted-foreground">Draft created in Outlook. Edit freely before sending.</p>
            {sendEnabled
              ? <div className="flex items-center gap-1 text-xs" style={{ color: "var(--green-2)" }}><ShieldCheck size={11} />Send enabled</div>
              : <div className="flex items-center gap-1 text-xs text-muted-foreground"><Shield size={11} />Send disabled</div>
            }
          </div>
        </div>
      </div>
    </div>
  );
}
