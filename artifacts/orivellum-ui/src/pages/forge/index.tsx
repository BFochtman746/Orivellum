import { useState } from "react";
import { useLocation } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  Globe2, Plus, Loader2, ArrowRight, Clock, CheckCircle2, AlertCircle,
  HelpCircle, Hammer, Sparkles,
} from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";

const API = `${import.meta.env.BASE_URL}api/forge`.replace(/\/+/g, "/").replace(/\/$/, "");

type ForgeProject = {
  id: string;
  name: string;
  brief: string;
  status: string;
  work_id: string | null;
  created_at: string;
  updated_at: string;
};

const STATUS_META: Record<string, { label: string; color: string; icon: any }> = {
  active:   { label: "Active",   color: "text-emerald-600 bg-emerald-50 border-emerald-200 dark:text-emerald-400 dark:bg-emerald-950/30 dark:border-emerald-800", icon: CheckCircle2 },
  building: { label: "Building", color: "text-amber-600 bg-amber-50 border-amber-200 dark:text-amber-400 dark:bg-amber-950/30 dark:border-amber-800", icon: Hammer },
  released: { label: "Released", color: "text-primary bg-primary/5 border-primary/20", icon: Globe2 },
  archived: { label: "Archived", color: "text-muted-foreground bg-muted/50 border-border", icon: HelpCircle },
};

export default function ForgePage() {
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBrief, setNewBrief] = useState("");

  const { data, isLoading } = useQuery<{ projects: ForgeProject[] }>({
    queryKey: ["forge-projects"],
    queryFn: () => apiFetch(`${API}/projects`).then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 15_000,
  });

  const createProject = useMutation({
    mutationFn: (body: { name: string; brief: string }) =>
      apiFetch(`${API}/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(r => { if (!r.ok) throw new Error("Failed to create"); return r.json(); }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["forge-projects"] });
      toast.success("Project created");
      setShowNew(false);
      setNewName("");
      setNewBrief("");
      navigate(`/forge/${res.project.id}`);
    },
    onError: () => toast.error("Could not create project"),
  });

  const handleCreate = () => {
    if (!newName.trim()) return;
    createProject.mutate({ name: newName.trim(), brief: newBrief.trim() });
  };

  const projects = data?.projects ?? [];

  return (
    <div className="space-y-8 animate-in fade-in duration-500 pb-20">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Globe2 className="w-5 h-5 text-primary" />
            <h1 className="vellum-h1">Forge</h1>
          </div>
          <div className="gilt-rule w-24" />
          <p className="text-sm text-muted-foreground mt-2 max-w-lg">
            Governed website factory — plan, design, build, and release static sites
            with an AI agent working under quality-gate oversight.
          </p>
        </div>
        <Button
          onClick={() => setShowNew(true)}
          className="gap-1.5"
        >
          <Plus className="w-4 h-4" /> New project
        </Button>
      </div>

      {/* Project list */}
      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map(i => <Skeleton key={i} className="h-24 rounded-xl" />)}
        </div>
      ) : projects.length === 0 ? (
        <div className="border border-dashed border-border/60 rounded-xl p-12 text-center">
          <Globe2 className="w-8 h-8 text-muted-foreground/40 mx-auto mb-3" />
          <p className="text-sm text-muted-foreground">No projects yet.</p>
          <Button
            variant="outline"
            className="mt-4 gap-1.5"
            onClick={() => setShowNew(true)}
          >
            <Plus className="w-4 h-4" /> Create your first project
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map(proj => {
            const meta = STATUS_META[proj.status] ?? STATUS_META.active;
            const Icon = meta.icon;
            return (
              <button
                key={proj.id}
                onClick={() => navigate(`/forge/${proj.id}`)}
                className="group text-left border border-border/60 rounded-xl p-5 hover:border-primary/30 hover:shadow-sm transition-all bg-card"
              >
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-sm leading-tight truncate group-hover:text-primary transition-colors">
                      {proj.name}
                    </h3>
                  </div>
                  <span className={`flex items-center gap-1 text-[10px] font-mono uppercase px-2 py-0.5 rounded-full border ${meta.color}`}>
                    <Icon className="w-3 h-3" />
                    {meta.label}
                  </span>
                </div>
                {proj.brief && (
                  <p className="text-xs text-muted-foreground line-clamp-2 mb-3 leading-relaxed">
                    {proj.brief}
                  </p>
                )}
                <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {format(new Date(proj.created_at), "MMM d, yyyy")}
                  </span>
                  <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* New project dialog */}
      <Dialog open={showNew} onOpenChange={setShowNew}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Globe2 className="w-4 h-4 text-primary" />
              New Forge project
            </DialogTitle>
            <DialogDescription>
              Give your website a name and a brief. The AI will plan and build it
              under quality-gate oversight.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-1.5">
              <label className="text-xs font-mono uppercase text-muted-foreground">Project name</label>
              <Input
                placeholder="e.g. The Meridian Codex Fan Site"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => e.key === "Enter" && !e.shiftKey && handleCreate()}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-mono uppercase text-muted-foreground">Brief</label>
              <Textarea
                placeholder="Describe the website — its purpose, audience, and tone. The more detail you provide, the better the plan."
                value={newBrief}
                onChange={e => setNewBrief(e.target.value)}
                rows={4}
                className="resize-none"
              />
            </div>
            <div className="flex items-center gap-2 pt-1">
              <Button
                onClick={handleCreate}
                disabled={!newName.trim() || createProject.isPending}
                className="gap-1.5"
              >
                {createProject.isPending
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Creating…</>
                  : <><Sparkles className="w-3.5 h-3.5" /> Create project</>}
              </Button>
              <Button variant="ghost" onClick={() => setShowNew(false)}>
                Cancel
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
