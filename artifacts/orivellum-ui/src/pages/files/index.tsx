import { useState } from "react";
import { useListFiles, useUploadFile, getListFilesQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Folder, File, Upload, ChevronRight, Home } from "lucide-react";
import { toast } from "sonner";

export default function Files() {
  const [currentPath, setCurrentPath] = useState<string>("");
  const queryClient = useQueryClient();
  
  const { data: filesResp, isLoading } = useListFiles({ subdir: currentPath }, { query: { queryKey: getListFilesQueryKey({ subdir: currentPath }) }});
  const uploadFile = useUploadFile();

  const handleUpload = () => {
    // Simulated upload for UI
    const dummyContent = btoa("Dummy file content");
    uploadFile.mutate({ 
      data: { 
        filename: `uploaded_file_${Date.now()}.txt`, 
        content_b64: dummyContent,
        subdir: currentPath
      } 
    }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListFilesQueryKey({ subdir: currentPath }) });
        toast.success("File uploaded");
      }
    });
  };

  const navigateUp = () => {
    if (!currentPath) return;
    const parts = currentPath.split('/');
    parts.pop();
    setCurrentPath(parts.join('/'));
  };

  const navigateTo = (dir: any) => {
    // Ensure dir string
    const dirName = typeof dir === 'string' ? dir : dir.name || '';
    if (!dirName) return;
    
    if (currentPath) {
      setCurrentPath(`${currentPath}/${dirName}`);
    } else {
      setCurrentPath(dirName);
    }
  };

  const pathParts = currentPath.split('/').filter(Boolean);

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">Filesystem</h1>
          <p className="text-muted-foreground mt-1 font-serif">Direct access to raw workspace files.</p>
        </div>
        <Button onClick={handleUpload} disabled={uploadFile.isPending} className="gap-2">
          <Upload className="w-4 h-4" />
          {uploadFile.isPending ? "Uploading..." : "Upload File"}
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="flex items-center gap-2 p-4 border-b border-border/50 bg-muted/10 font-mono text-sm overflow-x-auto">
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setCurrentPath("")}>
              <Home className="w-4 h-4" />
            </Button>
            <ChevronRight className="w-4 h-4 text-muted-foreground" />
            {pathParts.length === 0 ? (
              <span className="text-foreground font-medium">root</span>
            ) : (
              pathParts.map((part, i) => (
                <span key={i} className="flex items-center gap-2">
                  <span className={i === pathParts.length - 1 ? "text-foreground font-medium" : "text-muted-foreground"}>
                    {part}
                  </span>
                  {i < pathParts.length - 1 && <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                </span>
              ))
            )}
          </div>

          <div className="divide-y divide-border/50">
            {currentPath && (
              <div 
                className="flex items-center gap-3 p-3 hover:bg-muted/30 cursor-pointer transition-colors"
                onClick={navigateUp}
              >
                <Folder className="w-5 h-5 text-muted-foreground" />
                <span className="font-medium text-sm">..</span>
              </div>
            )}

            {isLoading ? (
              <div className="p-4 space-y-3">
                {[1, 2, 3].map(i => <Skeleton key={i} className="h-6 w-1/3" />)}
              </div>
            ) : (
              <>
                {filesResp?.dirs?.map((dir: any, i) => {
                  const dirName = typeof dir === 'string' ? dir : dir.name;
                  return (
                    <div 
                      key={`dir-${i}`}
                      className="flex items-center gap-3 p-3 hover:bg-muted/30 cursor-pointer transition-colors group"
                      onClick={() => navigateTo(dir)}
                    >
                      <Folder className="w-5 h-5 text-primary/70 group-hover:text-primary fill-primary/10" />
                      <span className="font-medium text-sm">{dirName}</span>
                    </div>
                  );
                })}
                
                {filesResp?.files?.map((file: any, i) => {
                  const fileName = typeof file === 'string' ? file : file.name;
                  const fileSize = file.size_bytes ? `${Math.round(file.size_bytes / 1024)} KB` : '';
                  return (
                    <div 
                      key={`file-${i}`}
                      className="flex items-center justify-between p-3 hover:bg-muted/30 transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <File className="w-5 h-5 text-muted-foreground" />
                        <span className="text-sm">{fileName}</span>
                      </div>
                      {fileSize && <span className="text-xs font-mono text-muted-foreground">{fileSize}</span>}
                    </div>
                  );
                })}

                {(!filesResp?.dirs?.length && !filesResp?.files?.length) && (
                  <div className="p-8 text-center text-sm text-muted-foreground italic">
                    This directory is empty.
                  </div>
                )}
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
