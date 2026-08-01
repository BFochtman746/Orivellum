import { useListVoices, useListStudioOutputs } from "@workspace/api-client-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Mic, Play, Settings2, Video, Image as ImageIcon, FileAudio } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Link } from "wouter";

export default function Studio() {
  const { data: voicesResp, isLoading: loadingVoices } = useListVoices();
  const { data: outputsResp, isLoading: loadingOutputs } = useListStudioOutputs();

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-center justify-between border-b border-border/50 pb-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight">Studio</h1>
          <p className="text-muted-foreground mt-1 font-serif">Media generation, voice synthesis, and outputs.</p>
        </div>
        <Button asChild variant="outline" className="gap-2">
          <Link href="/system"><Settings2 className="w-4 h-4" /> Engine Settings</Link>
        </Button>
      </div>

      <div className="grid md:grid-cols-3 gap-8">
        <div className="md:col-span-1 space-y-4">
          <div className="flex items-center gap-2">
            <Mic className="w-5 h-5 text-muted-foreground" />
            <h2 className="text-xl font-serif font-semibold">Available Voices</h2>
          </div>
          
          <div className="grid gap-3">
            {loadingVoices ? (
              [1, 2, 3].map(i => <Skeleton key={i} className="h-16 w-full" />)
            ) : voicesResp?.voices?.map((voice) => (
              <Card key={voice.id} className="bg-muted/10 border-border/50">
                <CardContent className="p-4 flex items-center justify-between">
                  <div>
                    <h3 className="font-medium text-sm">{voice.name}</h3>
                    <div className="flex gap-2 mt-1">
                      <Badge variant="outline" className="text-[9px] font-mono uppercase">{voice.engine}</Badge>
                      {voice.builtin && <Badge variant="secondary" className="text-[9px] font-mono uppercase">Built-in</Badge>}
                    </div>
                  </div>
                  <Button size="icon" variant="ghost" className="h-8 w-8 rounded-full">
                    <Play className="w-4 h-4" />
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>

        <div className="md:col-span-2 space-y-4">
          <div className="flex items-center gap-2">
            <Video className="w-5 h-5 text-muted-foreground" />
            <h2 className="text-xl font-serif font-semibold">Recent Outputs</h2>
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            {loadingOutputs ? (
              [1, 2, 3, 4].map(i => <Skeleton key={i} className="h-48 w-full" />)
            ) : outputsResp?.outputs && outputsResp.outputs.length > 0 ? (
              outputsResp.outputs.map((out: any, i) => (
                <Card key={i} className="overflow-hidden group cursor-pointer hover:border-primary/50 transition-colors">
                  <div className="aspect-video bg-muted flex items-center justify-center border-b border-border/50 relative">
                    {out.kind === 'audio' ? <FileAudio className="w-8 h-8 opacity-20" /> : out.type === 'video' ? <Video className="w-8 h-8 opacity-20" /> : <ImageIcon className="w-8 h-8 opacity-20" />}
                    <div className="absolute inset-0 bg-black/5 group-hover:bg-transparent transition-colors" />
                  </div>
                  <CardContent className="p-3">
                    <h3 className="font-medium text-sm truncate">{out.name || 'Untitled Generation'}</h3>
                    <div className="flex items-center justify-between mt-1">
                      <Badge variant="outline" className="text-[9px] font-mono uppercase">{out.kind || 'file'}</Badge>
                      {out.size_bytes && <span className="text-xs font-mono text-muted-foreground">{out.size_bytes >= 1_048_576 ? `${(out.size_bytes / 1_048_576).toFixed(1)} MB` : `${Math.round(out.size_bytes / 1024)} KB`}</span>}
                    </div>
                  </CardContent>
                </Card>
              ))
            ) : (
              <div className="col-span-full py-12 text-center border border-dashed rounded-lg bg-muted/5">
                <p className="text-muted-foreground">No media generated yet.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
