import { useListBackups, useCreateBackup, verifyBackup, getListBackupsQueryKey } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { HardDrive, ShieldCheck, Clock, Download, RefreshCw, History, X } from "lucide-react";
import { toast } from "sonner";
import { useEffect, useState } from "react";
import { Page, LoadingState, EmptyState, ErrorState, ConfirmAction } from "@/components/primitives";

const BASE = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");

export default function Backups() {
  const queryClient = useQueryClient();
  const { data: backupsResp, isLoading, isError, refetch } = useListBackups();
  const createBackup = useCreateBackup();

  const [verifying, setVerifying] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [restorePending, setRestorePending] = useState(false);

  useEffect(() => {
    apiFetch(`${BASE}/api/backups/restore/pending`)
      .then(r => r.json())
      .then(d => setRestorePending(!!d.pending))
      .catch(() => {});
  }, []);

  const handleRestore = async (name: string) => {
    setRestoring(name);
    try {
      const resp = await apiFetch(`${BASE}/api/backups/${encodeURIComponent(name)}/restore`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      setRestorePending(true);
      toast.success("Restore staged — it will apply the next time the server starts.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Restore failed");
    } finally {
      setRestoring(null);
    }
  };

  const handleCancelRestore = async () => {
    try {
      await apiFetch(`${BASE}/api/backups/restore/pending`, { method: "DELETE" });
      setRestorePending(false);
      toast.success("Pending restore cancelled");
    } catch {
      toast.error("Could not cancel the restore");
    }
  };

  const handleCreate = () => {
    createBackup.mutate(undefined, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListBackupsQueryKey() });
        toast.success("Backup created successfully");
      },
      onError: () => {
        toast.error("Failed to create backup");
      }
    });
  };

  const handleVerify = async (name: string) => {
    setVerifying(name);
    try {
      const data = await verifyBackup(name);
      setVerifying(null);
      if (data.ok) {
        toast.success(`Backup valid: ${data.member_count} files, DB present: ${data.has_db}`);
      } else {
        toast.error("Backup verification failed");
      }
    } catch (err) {
      setVerifying(null);
      toast.error("Verification error");
    }
  };

  const handleDownload = async (name: string) => {
    try {
      const resp = await apiFetch(`${BASE}/api/backups/${encodeURIComponent(name)}/download`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch {
      toast.error("Download failed");
    }
  };

  const backups = backupsResp?.backups ?? [];

  return (
    <Page
      wide
      eyebrow="Local, self-contained snapshots"
      title="System Backups"
      actions={
        <Button onClick={handleCreate} disabled={createBackup.isPending} className="min-h-11 gap-2">
          <HardDrive className="w-4 h-4" />
          {createBackup.isPending ? "Creating..." : "Create Backup"}
        </Button>
      }
    >
      <p className="text-muted-foreground -mt-2 font-serif">Snapshots of your entire workspace.</p>

      {restorePending && (
        <Card style={{ borderColor: "var(--gd-caution)", background: "var(--gd-caution-soft)" }}>
          <CardContent className="p-4 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <History className="w-5 h-5 shrink-0" style={{ color: "var(--gd-caution)" }} />
              <div className="text-sm">
                <span className="font-medium">A restore is staged.</span>{" "}
                <span className="text-muted-foreground">
                  It will replace your current data the next time the server starts. A safety copy is kept.
                </span>
              </div>
            </div>
            <Button variant="outline" size="sm" className="min-h-11 gap-1 shrink-0" onClick={handleCancelRestore}>
              <X className="w-3.5 h-3.5" /> Cancel restore
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4">
        {isLoading ? (
          <LoadingState rows={3} label="Loading backups" />
        ) : isError ? (
          <ErrorState
            title="Could not load backups"
            detail="The backup list failed to load."
            onRetry={() => refetch()}
          />
        ) : backups.length > 0 ? (
          backups.map((backup, i) => (
            <Card key={i}>
              <CardContent className="p-4 flex items-center justify-between gap-3">
                <div className="flex items-center gap-4 min-w-0">
                  <div className="w-10 h-10 rounded bg-primary/10 flex items-center justify-center shrink-0">
                    <ShieldCheck className="w-5 h-5 text-primary" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="font-medium font-mono text-sm truncate">{backup.name}</h3>
                    <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground font-mono">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {backup.created_at ? format(new Date(backup.created_at), 'MMM d, yyyy HH:mm') : 'Unknown'}
                      </span>
                      <span>•</span>
                      <span>{backup.size_bytes ? `${(backup.size_bytes / (1024 * 1024)).toFixed(2)} MB` : 'Unknown size'}</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    className="min-h-11"
                    onClick={() => handleVerify(backup.name!)}
                    disabled={verifying === backup.name || !backup.name}
                  >
                    {verifying === backup.name ? <RefreshCw className="w-4 h-4 animate-spin" /> : "Verify"}
                  </Button>
                  <ConfirmAction
                    title="Restore this backup?"
                    consequence={`Restoring "${backup.name}" replaces your current data with this backup the next time the server starts. A safety copy of today's data is kept automatically, so this can be undone.`}
                    confirmLabel="Restore"
                    onConfirm={() => { void handleRestore(backup.name!); }}
                    trigger={
                      <Button
                        variant="outline"
                        size="sm"
                        className="min-h-11 gap-1"
                        disabled={restoring === backup.name || !backup.name}
                      >
                        {restoring === backup.name
                          ? <RefreshCw className="w-4 h-4 animate-spin" />
                          : <><History className="w-4 h-4" /> Restore</>}
                      </Button>
                    }
                  />
                  <Button
                    variant="secondary"
                    size="sm"
                    className="min-h-11 gap-2"
                    onClick={() => handleDownload(backup.name!)}
                    disabled={!backup.name}
                  >
                    <Download className="w-4 h-4" /> Download
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        ) : (
          <EmptyState
            icon={<HardDrive />}
            title="No backups found"
            description="Keep your data safe by creating a snapshot."
            action={<Button onClick={handleCreate}>Create First Backup</Button>}
          />
        )}
      </div>
    </Page>
  );
}
