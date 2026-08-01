import { useState } from "react";
import { useListProjects, useCreateProject, getListProjectsQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "wouter";
import { format } from "date-fns";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Target, Plus, TrendingUp } from "lucide-react";
import { Progress } from "@/components/ui/progress";

export default function Projects() {
  const [, navigate] = useLocation();
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const queryClient = useQueryClient();
  
  const { data: projResp, isLoading } = useListProjects();
  const createProj = useCreateProject();
  
  const [newProj, setNewProj] = useState({ name: "", description: "" });

  const handleCreate = () => {
    if (!newProj.name) return;
    createProj.mutate({ data: newProj }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListProjectsQueryKey() });
        setIsCreateOpen(false);
        toast.success(`"${newProj.name}" created`);
        setNewProj({ name: "", description: "" });
      },
      onError: () => toast.error("Could not create project"),
    });
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">Mastery Projects</h1>
          <p className="text-muted-foreground mt-1 font-serif">Track structured learning and concept mastery.</p>
        </div>

        <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <DialogTrigger asChild>
            <Button className="gap-2">
              <Plus className="w-4 h-4" />
              New Project
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle className="font-serif text-2xl">Create Mastery Project</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label className="font-mono text-xs uppercase text-muted-foreground">Project Name</Label>
                <Input 
                  value={newProj.name} 
                  onChange={(e) => setNewProj({...newProj, name: e.target.value})} 
                  placeholder="e.g., Information Theory" 
                />
              </div>
              <div className="space-y-2">
                <Label className="font-mono text-xs uppercase text-muted-foreground">Description</Label>
                <Textarea 
                  value={newProj.description} 
                  onChange={(e) => setNewProj({...newProj, description: e.target.value})} 
                  placeholder="Learning goals..."
                  rows={3}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsCreateOpen(false)}>Cancel</Button>
              <Button onClick={handleCreate} disabled={!newProj.name || createProj.isPending}>
                {createProj.isPending ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {isLoading ? (
          [1, 2, 3].map(i => <Skeleton key={i} className="h-40 w-full" />)
        ) : projResp?.projects && projResp.projects.length > 0 ? (
          projResp.projects.map((proj) => (
            <Card key={proj.id} className="hover-elevate cursor-pointer" onClick={() => navigate(`/projects/${proj.id}`)}>
              <CardContent className="p-6 space-y-4">
                <div className="flex justify-between items-start gap-4">
                  <div>
                    <h3 className="text-xl font-serif font-medium">{proj.name}</h3>
                    <p className="text-sm text-muted-foreground mt-1 line-clamp-2">{proj.description}</p>
                  </div>
                  <div className="w-12 h-12 rounded-full border-4 border-primary/20 flex items-center justify-center shrink-0">
                    <span className="font-mono font-bold text-sm">{Math.round((proj.mastery || 0) * 100)}%</span>
                  </div>
                </div>
                
                <div className="space-y-2 pt-2">
                  <div className="flex justify-between text-xs font-mono uppercase text-muted-foreground">
                    <span>Mastery Level</span>
                    {proj.last_review && <span>Reviewed: {format(new Date(proj.last_review), 'MMM d')}</span>}
                  </div>
                  <Progress value={(proj.mastery || 0) * 100} className="h-2" />
                </div>
              </CardContent>
            </Card>
          ))
        ) : (
          <div className="col-span-full text-center py-20 bg-muted/10 border border-dashed rounded-lg">
            <Target className="w-10 h-10 text-muted-foreground mx-auto mb-4 opacity-50" />
            <h3 className="text-lg font-serif font-medium">No projects active</h3>
            <p className="text-muted-foreground mt-1">Start a mastery project to track your learning progress.</p>
          </div>
        )}
      </div>
    </div>
  );
}
