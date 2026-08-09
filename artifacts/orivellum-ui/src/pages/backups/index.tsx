import { useListBackups, useCreateBackup, verifyBackup, getListBackupsQueryKey } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { HardDrive, ShieldCheck, Clock, Download, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { useGdDark } from "@/lib/useGdDark";

export default function Backups() {
  const gdDark = useGdDark();
  const queryClient = useQueryClient();
  const { data: backupsResp, isLoading } = useListBackups();
  const createBackup = useCreateBackup();
  
  const [verifying, setVerifying] = useState<string | null>(null);

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

  return (
    <div className={`space-y-6 animate-in fade-in duration-500 max-w-4xl mx-auto ${gdDark ? "dark text-foreground" : ""}`}>
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">System Backups</h1>
          <p className="text-muted-foreground mt-1 font-serif">Local, self-contained snapshots of your entire workspace.</p>
        </div>
        <Button onClick={handleCreate} disabled={createBackup.isPending} className="gap-2">
          <HardDrive className="w-4 h-4" />
          {createBackup.isPending ? "Creating..." : "Create Backup"}
        </Button>
      </div>

      <div className="grid gap-4">
        {isLoading ? (
          [1, 2, 3].map(i => <Skeleton key={i} className="h-20 w-full" />)
        ) : backupsResp?.backups && backupsResp.backups.length > 0 ? (
          backupsResp.backups.map((backup, i) => (
            <Card key={i}>
              <CardContent className="p-4 flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded bg-primary/10 flex items-center justify-center shrink-0">
                    <ShieldCheck className="w-5 h-5 text-primary" />
                  </div>
                  <div>
                    <h3 className="font-medium font-mono text-sm">{backup.name}</h3>
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
                <div className="flex items-center gap-2">
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => handleVerify(backup.name!)}
                    disabled={verifying === backup.name || !backup.name}
                  >
                    {verifying === backup.name ? <RefreshCw className="w-4 h-4 animate-spin" /> : "Verify"}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    className="gap-2"
                    onClick={async () => {
                      const base = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
                      try {
                        const resp = await apiFetch(`${base}/api/backups/${encodeURIComponent(backup.name!)}/download`);
                        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                        const blob = await resp.blob();
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = backup.name!;
                        a.click();
                        setTimeout(() => URL.revokeObjectURL(url), 10_000);
                      } catch {
                        toast.error("Download failed");
                      }
                    }}
                  >
                    <Download className="w-4 h-4" /> Download
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        ) : (
          <Card className="border-dashed bg-muted/10">
            <CardContent className="p-12 text-center">
              <HardDrive className="w-12 h-12 text-muted-foreground mx-auto mb-4 opacity-50" />
              <h3 className="text-lg font-serif font-medium">No backups found</h3>
              <p className="text-muted-foreground mt-1 mb-6">Keep your data safe by creating a snapshot.</p>
              <Button onClick={handleCreate}>Create First Backup</Button>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
