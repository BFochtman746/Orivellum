import { useGetSystemHealth, useListCapabilities } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Activity, Database, Cpu, CheckCircle2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export default function System() {
  const { data: health, isLoading: loadingHealth } = useGetSystemHealth();
  const { data: capsResp, isLoading: loadingCaps } = useListCapabilities();

  return (
    <div className="space-y-8 animate-in fade-in duration-500 max-w-5xl mx-auto">
      <div className="border-b border-border/50 pb-4">
        <h1 className="text-3xl font-serif font-semibold tracking-tight">System Status</h1>
        <p className="text-muted-foreground mt-1 font-serif">Infrastructure health and local AI capabilities.</p>
      </div>

      <div className="grid md:grid-cols-3 gap-4">
        <Card className="bg-primary/5 border-primary/20">
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-mono text-sm uppercase tracking-wider">Overall Status</h3>
              <Activity className="w-5 h-5 text-primary" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                {health?.status === 'ok' ? (
                  <CheckCircle2 className="w-6 h-6 text-primary" />
                ) : (
                  <XCircle className="w-6 h-6 text-destructive" />
                )}
                <span className="text-2xl font-serif font-semibold capitalize">{health?.status || 'Unknown'}</span>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4 text-muted-foreground">
              <h3 className="font-mono text-sm uppercase tracking-wider">Database</h3>
              <Database className="w-5 h-5" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="text-xl font-medium">
                {health?.services?.database ? 'Connected' : 'Offline'}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4 text-muted-foreground">
              <h3 className="font-mono text-sm uppercase tracking-wider">Local AI Engine</h3>
              <Cpu className="w-5 h-5" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="text-xl font-medium">
                {health?.services?.ai ? 'Available' : 'Unavailable'}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        <h2 className="text-xl font-serif font-medium border-b border-border/50 pb-2">Active Capabilities</h2>
        <div className="grid md:grid-cols-2 gap-4">
          {loadingCaps ? (
            [1, 2, 3, 4].map(i => <Skeleton key={i} className="h-16 w-full" />)
          ) : capsResp?.capabilities?.map((cap, i) => (
            <div key={i} className="flex items-center justify-between p-4 rounded-lg bg-muted/20 border border-border/50">
              <div className="font-medium font-mono text-sm">{cap.name}</div>
              <Badge variant={cap.status === 'active' ? 'default' : 'secondary'} className="font-mono text-[10px] uppercase">
                {cap.status}
              </Badge>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
