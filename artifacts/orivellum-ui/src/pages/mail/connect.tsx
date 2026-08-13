/**
 * A-01 Mail Steward — /mail/connect
 * Microsoft device-code OAuth flow.
 */
import { useState, useEffect, useRef } from "react";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { useLocation } from "wouter";
import { Mail, Copy, ExternalLink, Loader2, ArrowLeft } from "lucide-react";
import { Status, ErrorState } from "@/components/primitives";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

type Step = "idle" | "pending" | "polling" | "done" | "error";

export default function MailConnectPage() {
  const [, navigate] = useLocation();
  const [step, setStep]           = useState<Step>("idle");
  const [userCode, setUserCode]   = useState("");
  const [verifyUrl, setVerifyUrl] = useState("");
  const [handle, setHandle]       = useState("");
  const [error, setError]         = useState("");
  const pollRef                   = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  useEffect(() => () => stopPoll(), []);

  const start = async () => {
    setStep("pending");
    setError("");
    try {
      const r = await apiFetch(`${BASE}/mail/connect/start`, { method: "POST" });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error((e as any).detail || "Failed to start device flow");
      }
      const data = await r.json();
      setUserCode(data.user_code);
      setVerifyUrl(data.verification_uri);
      setHandle(data.handle);
      setStep("polling");
      pollRef.current = setInterval(() => poll(data.handle), 5000);
    } catch (e: any) {
      setError(e.message || "Request failed");
      setStep("error");
    }
  };

  const poll = async (h: string) => {
    try {
      const r = await apiFetch(`${BASE}/mail/connect/poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: h }),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        const msg = (e as any).detail || "";
        if (msg.includes("not found")) {
          stopPoll();
          setError("Session expired. Please try again.");
          setStep("error");
        }
        return;
      }
      const data = await r.json();
      if (data.status === "connected") {
        stopPoll();
        setStep("done");
        toast.success(`Connected as ${data.display_name || data.mail || "your account"}`);
        setTimeout(() => navigate("/mail"), 1500);
      }
      // status === "pending" → keep polling
    } catch {
      // network hiccup — keep polling
    }
  };

  const copy = () => {
    navigator.clipboard?.writeText(userCode).then(() => toast("Code copied"));
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8">
      <div className="rounded-xl border border-card-border bg-card p-8 max-w-md w-full space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-accent/40 flex items-center justify-center">
            <Mail size={20} style={{ color: "var(--gd-bronze)" }} />
          </div>
          <div>
            <h1 className="text-base font-semibold">Connect Outlook</h1>
            <p className="text-xs text-muted-foreground">Microsoft account required</p>
          </div>
        </div>

        {step === "idle" && (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground leading-relaxed">
              Orivellum uses a device-code flow — no password is ever stored.
              You'll be shown a code to enter on Microsoft's site.
            </p>
            <ul className="text-xs text-muted-foreground space-y-1">
              <li>• Only subject, sender domain, and assessment metadata are stored</li>
              <li>• Message body is never persisted — only AI analysis is kept</li>
              <li>• You can disconnect at any time from settings</li>
            </ul>
            <Button className="w-full gap-2 min-h-11" onClick={start} data-testid="button-start-connection">
              <Mail size={14} />
              Start connection
            </Button>
          </div>
        )}

        {step === "pending" && (
          <div className="flex items-center justify-center gap-2 py-6 text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            <span className="text-sm">Requesting device code…</span>
          </div>
        )}

        {step === "polling" && (
          <div className="space-y-5">
            <p className="text-sm text-muted-foreground">
              1. Go to <strong>microsoft.com/devicelogin</strong> (or click below)
            </p>
            <p className="text-sm text-muted-foreground">
              2. Enter this code:
            </p>
            <div className="flex items-center gap-2">
              <div className="flex-1 text-center py-3 rounded-lg font-mono text-xl font-bold tracking-[0.25em] border border-border/50 bg-muted/30">
                {userCode}
              </div>
              <Button variant="outline" size="icon" className="min-h-11 min-w-11" onClick={copy} title="Copy code">
                <Copy size={14} />
              </Button>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                className="flex-1 gap-2 min-h-11"
                onClick={() => window.open(verifyUrl, "_blank")}
              >
                <ExternalLink size={13} />
                Open Microsoft
              </Button>
            </div>
            <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <Loader2 size={12} className="animate-spin" />
              Waiting for sign-in…
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="w-full gap-1 min-h-11 text-muted-foreground"
              onClick={() => { stopPoll(); setStep("idle"); }}
            >
              Cancel
            </Button>
          </div>
        )}

        {step === "done" && (
          <div className="flex flex-col items-center gap-3 py-4">
            <Status kind="ok" label="Connected successfully" />
            <p className="text-xs text-muted-foreground">Redirecting to Mail…</p>
          </div>
        )}

        {step === "error" && (
          <ErrorState
            title="Connection failed"
            detail={error}
            onRetry={start}
          />
        )}

        {step !== "polling" && step !== "done" && (
          <Button
            variant="ghost"
            size="sm"
            className="w-full gap-1 min-h-11 text-muted-foreground"
            onClick={() => navigate("/mail")}
          >
            <ArrowLeft size={12} />
            Back to Mail
          </Button>
        )}
      </div>
    </div>
  );
}
