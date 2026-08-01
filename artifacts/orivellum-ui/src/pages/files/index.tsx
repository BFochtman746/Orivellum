import { useRef, useState } from "react";
import { useListFiles, useUploadFile, getListFilesQueryKey } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Folder, File, Upload, ChevronRight, Home, Download } from "lucide-react";
import { toast } from "sonner";

export default function Files() {
  const [currentPath, setCurrentPath] = useState<string>("");
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: filesResp, isLoading } = useListFiles(
    { subdir: currentPath },
    { query: { queryKey: getListFilesQueryKey({ subdir: currentPath }) } }
  );
  const uploadFile = useUploadFile();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const b64 = (reader.result as string).split(",")[1] ?? "";
      uploadFile.mutate(
        { data: { filename: file.name, content_b64: b64, subdir: currentPath } },
        {
          onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: getListFilesQueryKey({ subdir: currentPath }) });
            toast.success(`Uploaded ${file.name}`);
          },
          onError: () => toast.error("Upload failed"),
        }
      );
    };
    reader.readAsDataURL(file);
    // reset so the same file can be re-selected
    e.target.value = "";
  };

  const navigateUp = () => {
    if (!currentPath) return;
    const parts = currentPath.split("/");
    parts.pop();
    setCurrentPath(parts.join("/"));
  };

  const navigateTo = (dir: any) => {
    const dirName = typeof dir === "string" ? dir : dir.name || "";
    if (!dirName) return;
    setCurrentPath(currentPath ? `${currentPath}/${dirName}` : dirName);
  };

  const pathParts = currentPath.split("/").filter(Boolean);

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">Filesystem</h1>
          <p className="text-muted-foreground mt-1 font-serif">Direct access to raw workspace files.</p>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={handleFileChange}
        />
        <Button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploadFile.isPending}
          className="gap-2"
        >
          <Upload className="w-4 h-4" />
          {uploadFile.isPending ? "Uploading…" : "Upload File"}
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
                  <button
                    className={`hover:underline ${i === pathParts.length - 1 ? "text-foreground font-medium" : "text-muted-foreground"}`}
                    onClick={() => setCurrentPath(pathParts.slice(0, i + 1).join("/"))}
                  >
                    {part}
                  </button>
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
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-1/3" />)}
              </div>
            ) : (
              <>
                {filesResp?.dirs?.map((dir: any, i: number) => {
                  const dirName = typeof dir === "string" ? dir : dir.name;
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

                {filesResp?.files?.map((file: any, i: number) => {
                  const fileName = typeof file === "string" ? file : file.name;
                  const fileSize = file.size_bytes
                    ? file.size_bytes >= 1_048_576
                      ? `${(file.size_bytes / 1_048_576).toFixed(1)} MB`
                      : `${Math.round(file.size_bytes / 1024)} KB`
                    : "";
                  return (
                    <div
                      key={`file-${i}`}
                      className="flex items-center justify-between p-3 hover:bg-muted/30 transition-colors group"
                    >
                      <div className="flex items-center gap-3">
                        <File className="w-5 h-5 text-muted-foreground" />
                        <span className="text-sm">{fileName}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        {fileSize && <span className="text-xs font-mono text-muted-foreground">{fileSize}</span>}
                        <button
                          title="Download"
                          className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                          onClick={async () => {
                            const base = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
                            const filePath = currentPath ? `${currentPath}/${fileName}` : fileName;
                            try {
                              const resp = await apiFetch(`${base}/api/download/${encodeURIComponent(filePath)}`);
                              if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                              const blob = await resp.blob();
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement("a");
                              a.href = url;
                              a.download = fileName;
                              a.click();
                              setTimeout(() => URL.revokeObjectURL(url), 10_000);
                            } catch {
                              toast.error("Download failed");
                            }
                          }}
                        >
                          <Download className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  );
                })}

                {!filesResp?.dirs?.length && !filesResp?.files?.length && (
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
