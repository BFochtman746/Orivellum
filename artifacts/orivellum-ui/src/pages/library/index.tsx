import { useState } from "react";
import { useListLibrary, useSearchLibrary, useImportDocument, getListLibraryQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Search, Upload, FileText, Database, Filter, Library as LibraryIcon } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";

export default function Library() {
  const [search, setSearch] = useState("");
  const [isImportOpen, setIsImportOpen] = useState(false);
  const queryClient = useQueryClient();
  
  // Use search if query exists, else list
  const { data: listResp, isLoading: loadingList } = useListLibrary({}, { query: { enabled: !search, queryKey: getListLibraryQueryKey({}) } });
  const { data: searchResp, isLoading: loadingSearch } = useSearchLibrary({ q: search }, { query: { enabled: !!search, queryKey: ['librarySearch', search] } });
  
  const importDoc = useImportDocument();
  
  const [importData, setImportData] = useState({ filename: "", content: "", kind: "article" });

  const isLoading = search ? loadingSearch : loadingList;
  // Fallback to empty array if no results to avoid errors
  const docs = search ? (searchResp?.results || []) : (listResp?.documents || []);

  const handleImport = () => {
    if (!importData.filename || !importData.content) return;
    
    // In a real app, content would be base64 encoded file data
    // Here we simulate it by encoding the text input
    const b64 = btoa(unescape(encodeURIComponent(importData.content)));
    
    importDoc.mutate({ 
      data: { 
        filename: importData.filename, 
        content_b64: b64,
        meta: { kind: importData.kind }
      } 
    }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListLibraryQueryKey() });
        setIsImportOpen(false);
        setImportData({ filename: "", content: "", kind: "article" });
        toast.success("Document imported successfully");
      },
      onError: () => {
        toast.error("Failed to import document");
      }
    });
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">Library</h1>
          <p className="text-muted-foreground mt-1 font-serif">All imported documents, articles, and references.</p>
        </div>

        <Dialog open={isImportOpen} onOpenChange={setIsImportOpen}>
          <DialogTrigger asChild>
            <Button className="gap-2">
              <Upload className="w-4 h-4" />
              Import Document
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle className="font-serif text-2xl">Import Document</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label className="font-mono text-xs uppercase text-muted-foreground">Filename</Label>
                <Input 
                  value={importData.filename} 
                  onChange={(e) => setImportData({...importData, filename: e.target.value})} 
                  placeholder="e.g., paper.pdf" 
                />
              </div>
              <div className="space-y-2">
                <Label className="font-mono text-xs uppercase text-muted-foreground">Kind</Label>
                <Select value={importData.kind} onValueChange={(val) => setImportData({...importData, kind: val})}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select kind" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="article">Article</SelectItem>
                    <SelectItem value="book">Book</SelectItem>
                    <SelectItem value="note">Note</SelectItem>
                    <SelectItem value="webpage">Webpage</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label className="font-mono text-xs uppercase text-muted-foreground">Raw Content (Simulated for UI)</Label>
                <Input 
                  value={importData.content} 
                  onChange={(e) => setImportData({...importData, content: e.target.value})} 
                  placeholder="Paste text content here..." 
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsImportOpen(false)}>Cancel</Button>
              <Button onClick={handleImport} disabled={!importData.filename || !importData.content || importDoc.isPending}>
                {importDoc.isPending ? "Importing..." : "Import"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input 
            placeholder="Search all documents (full-text)..." 
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 bg-background/50"
          />
        </div>
        <Button variant="outline" size="icon" className="shrink-0">
          <Filter className="w-4 h-4" />
        </Button>
      </div>

      <div className="grid gap-3">
        {isLoading ? (
          [1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24 w-full" />)
        ) : docs.length > 0 ? (
          docs.map((doc: any) => (
            <Card key={doc.id} className="hover-elevate cursor-pointer transition-colors group">
              <CardContent className="p-4 sm:p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-start gap-4">
                  <div className="w-10 h-10 rounded bg-muted/50 flex items-center justify-center shrink-0 border border-border/50">
                    <FileText className="w-5 h-5 text-muted-foreground group-hover:text-primary transition-colors" />
                  </div>
                  <div>
                    <h3 className="font-medium text-lg group-hover:text-primary transition-colors">
                      {doc.title || doc.source || doc.filename || 'Untitled Document'}
                    </h3>
                    <div className="flex flex-wrap items-center gap-2 mt-1">
                      <Badge variant="secondary" className="font-mono text-[10px] uppercase">{doc.kind || 'Unknown'}</Badge>
                      <Badge variant="outline" className="font-mono text-[10px] uppercase">{doc.readiness || 'Raw'}</Badge>
                      {doc.work_id && (
                        <span className="text-xs text-muted-foreground flex items-center gap-1 font-mono">
                          <Database className="w-3 h-3" /> Linked to Work
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="text-right sm:shrink-0 text-xs font-mono text-muted-foreground space-y-1">
                  <div>{doc.created_at ? format(new Date(doc.created_at), 'MMM d, yyyy') : ''}</div>
                  <div className="truncate w-24 ml-auto opacity-50" title={doc.sha256}>{doc.sha256?.substring(0, 8)}</div>
                </div>
              </CardContent>
            </Card>
          ))
        ) : (
          <div className="text-center py-20 bg-muted/10 border border-dashed rounded-lg">
            <LibraryIcon className="w-10 h-10 text-muted-foreground mx-auto mb-4 opacity-50" />
            <h3 className="text-lg font-serif font-medium">No documents found</h3>
            <p className="text-muted-foreground mt-1">
              {search ? "No matches for your query." : "Import documents to start building your library."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
