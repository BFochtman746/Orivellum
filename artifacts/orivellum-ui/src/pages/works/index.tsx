import { useState } from "react";
import { Link } from "wouter";
import { useListWorks, useCreateWork, useGetWorkTypes, getListWorksQueryKey } from "@workspace/api-client-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { format } from "date-fns";
import { BookOpen, Plus, Search, Filter } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export default function WorksList() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "archived">("all");
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const queryClient = useQueryClient();
  
  const { data: worksResp, isLoading } = useListWorks();
  const { data: typesResp } = useGetWorkTypes();
  const createWork = useCreateWork();

  const [newWork, setNewWork] = useState({ title: "", description: "", work_type: "research" });

  const handleCreate = () => {
    if (!newWork.title) return;
    createWork.mutate({ data: newWork }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListWorksQueryKey() });
        setIsCreateOpen(false);
        setNewWork({ title: "", description: "", work_type: "research" });
        toast.success(`"${newWork.title}" created`);
      },
      onError: () => toast.error("Could not create work"),
    });
  };

  const filteredWorks = worksResp?.works?.filter(w => {
    const matchesSearch = !search || w.title?.toLowerCase().includes(search.toLowerCase()) || w.description?.toLowerCase().includes(search.toLowerCase());
    const matchesStatus = statusFilter === "all" || (w as any).status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">Works</h1>
          <p className="text-muted-foreground mt-1 font-serif">Manage your research, writing, and structured knowledge.</p>
        </div>

        <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <DialogTrigger asChild>
            <Button className="gap-2">
              <Plus className="w-4 h-4" />
              New Work
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[500px]">
            <DialogHeader>
              <DialogTitle className="font-serif text-2xl">Create a New Work</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="title" className="font-mono text-xs uppercase text-muted-foreground">Title</Label>
                <Input 
                  id="title" 
                  value={newWork.title} 
                  onChange={(e) => setNewWork({...newWork, title: e.target.value})} 
                  placeholder="e.g., The Architecture of Memory" 
                  className="font-serif text-lg py-6"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="type" className="font-mono text-xs uppercase text-muted-foreground">Work Type</Label>
                <Select value={newWork.work_type} onValueChange={(val) => setNewWork({...newWork, work_type: val})}>
                  <SelectTrigger id="type">
                    <SelectValue placeholder="Select type" />
                  </SelectTrigger>
                  <SelectContent>
                    {typesResp?.types?.map(t => (
                      <SelectItem key={t.id} value={t.id || ""}>{t.label}</SelectItem>
                    )) || (
                      <>
                        <SelectItem value="research">Research</SelectItem>
                        <SelectItem value="essay">Essay</SelectItem>
                        <SelectItem value="project">Project</SelectItem>
                      </>
                    )}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="description" className="font-mono text-xs uppercase text-muted-foreground">Description (Optional)</Label>
                <Textarea 
                  id="description" 
                  value={newWork.description} 
                  onChange={(e) => setNewWork({...newWork, description: e.target.value})} 
                  placeholder="Brief context or goals for this work..."
                  className="resize-none"
                  rows={3}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsCreateOpen(false)}>Cancel</Button>
              <Button onClick={handleCreate} disabled={!newWork.title || createWork.isPending}>
                {createWork.isPending ? "Creating..." : "Create Work"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input 
            placeholder="Search works..." 
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 bg-background/50"
          />
        </div>
        <div className="flex items-center gap-1 border border-border/50 rounded-lg p-0.5 bg-muted/20">
          {(["all", "active", "archived"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-3 py-1.5 rounded-md text-xs font-mono uppercase tracking-wider transition-colors ${statusFilter === s ? "bg-background shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-4">
          {[1, 2, 3].map(i => <Skeleton key={i} className="h-32 w-full" />)}
        </div>
      ) : filteredWorks && filteredWorks.length > 0 ? (
        <div className="grid gap-4">
          {filteredWorks.map((work) => (
            <Link key={work.id} href={`/works/${work.id}`}>
              <Card className="hover-elevate cursor-pointer transition-all hover:border-primary/50 group">
                <CardContent className="p-6">
                  <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div className="space-y-2 flex-1">
                      <div className="flex items-center gap-3">
                        <h2 className="text-2xl font-serif font-medium group-hover:text-primary transition-colors">{work.title}</h2>
                        <Badge variant="outline" className="font-mono text-[10px] uppercase tracking-wider bg-primary/5 text-primary border-primary/20">
                          {work.status}
                        </Badge>
                        <Badge variant="secondary" className="font-mono text-[10px] uppercase tracking-wider">
                          {work.work_type}
                        </Badge>
                      </div>
                      <p className="text-muted-foreground text-sm max-w-2xl leading-relaxed">
                        {work.description || <span className="italic opacity-50">No description provided.</span>}
                      </p>
                    </div>
                    
                    <div className="flex items-center gap-6 text-sm text-muted-foreground shrink-0 border-l border-border/50 pl-6">
                      <div className="space-y-1">
                        <div className="font-mono text-xs uppercase">Documents</div>
                        <div className="font-medium text-foreground text-base">{work.doc_count || 0}</div>
                      </div>
                      <div className="space-y-1">
                        <div className="font-mono text-xs uppercase">Knowledge</div>
                        <div className="font-medium text-foreground text-base">{work.knowledge_count || 0}</div>
                      </div>
                      <div className="space-y-1">
                        <div className="font-mono text-xs uppercase">Tasks</div>
                        <div className="font-medium text-foreground text-base">{work.pending_tasks || 0}</div>
                      </div>
                      {(work as any).obj_created && (
                        <div className="space-y-1">
                          <div className="font-mono text-xs uppercase">Created</div>
                          <div className="font-medium text-foreground text-base text-xs">
                            {format(new Date((work as any).obj_created), "MMM d")}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <div className="text-center py-20 bg-muted/10 rounded-lg border border-dashed border-border/50">
          <BookOpen className="w-10 h-10 text-muted-foreground mx-auto mb-4 opacity-50" />
          <h3 className="text-lg font-serif font-medium">No works found</h3>
          <p className="text-muted-foreground mt-1 max-w-sm mx-auto">
            {search ? "No works match your search criteria." : "Create a work to begin organizing your research."}
          </p>
          {!search && (
            <Button className="mt-6" onClick={() => setIsCreateOpen(true)}>
              <Plus className="w-4 h-4 mr-2" /> Create First Work
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
