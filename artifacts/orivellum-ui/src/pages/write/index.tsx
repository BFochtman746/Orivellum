/**
 * Write Desk — AI-powered document drafting workspace.
 *
 * Features:
 * - TipTap rich-text editor (headings, bold, italic, lists, tables, code, links)
 * - Floating AI toolbar on text selection (Improve, Expand, Rewrite, Fix, Summarize)
 * - Bottom AI panel: Continue writing, custom Ask, quick commands
 * - Left sidebar: document list with new/pin/delete
 * - Auto-save on change (debounced 1.5 s)
 * - Word count and reading-time indicator
 * - Export as plain text
 * - Link documents to Works
 */
import React, {
  useCallback, useEffect, useRef, useState,
} from 'react';
import { apiFetch } from '@/lib/auth';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Typography from '@tiptap/extension-typography';
import Placeholder from '@tiptap/extension-placeholder';
import CharacterCount from '@tiptap/extension-character-count';
import TextAlign from '@tiptap/extension-text-align';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import Highlight from '@tiptap/extension-highlight';
import Color from '@tiptap/extension-color';
import { TextStyle } from '@tiptap/extension-text-style';
import { Table } from '@tiptap/extension-table';
import { TableRow } from '@tiptap/extension-table';
import { TableCell } from '@tiptap/extension-table';
import { TableHeader } from '@tiptap/extension-table';

import { format } from 'date-fns';
import { useLocation } from 'wouter';
import {
  Bold, Italic, UnderlineIcon, Strikethrough,
  Heading1, Heading2, Heading3,
  List, ListOrdered, Quote, Code,
  AlignLeft, AlignCenter, AlignRight,
  Table as TableIcon,
  Sparkles, Wand2, Zap, Scissors, FileText,
  RotateCcw, Maximize2, Minimize2,
  Pin, PinOff, Trash2, Plus, Download,
  ChevronRight, ChevronDown, Loader2, MoreHorizontal,
  BookOpen, MessageSquare, ImageIcon, X as XIcon,
  PanelLeft,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useToast } from '@/hooks/use-toast';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, '/').replace(/\/$/, '');

// ── API helpers ──────────────────────────────────────────────────────────────

async function apiGet(path: string) {
  const r = await apiFetch(`${BASE}${path}`, { credentials: 'include' });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiPost(path: string, body: unknown) {
  const r = await apiFetch(`${BASE}${path}`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiPatch(path: string, body: unknown) {
  const r = await apiFetch(`${BASE}${path}`, {
    method: 'PATCH', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiDelete(path: string) {
  const r = await apiFetch(`${BASE}${path}`, { method: 'DELETE', credentials: 'include' });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── Types ────────────────────────────────────────────────────────────────────

interface WriteDoc {
  id: string;
  title: string;
  content_json: Record<string, unknown>;
  content_text: string;
  word_count: number;
  work_id: string | null;
  is_pinned: number;
  created_at: string;
  updated_at: string;
}

type AICommand =
  | 'continue' | 'improve' | 'expand' | 'summarize' | 'rewrite'
  | 'fix' | 'shorten' | 'outline' | 'makeformal' | 'makecasual'
  | 'explain' | 'ask' | 'from_knowledge';

// ── Toolbar button ────────────────────────────────────────────────────────────

function TBBtn({
  onClick, active, disabled, title, children,
}: {
  onClick: () => void; active?: boolean; disabled?: boolean;
  title: string; children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          onMouseDown={(e) => { e.preventDefault(); onClick(); }}
          disabled={disabled}
          className={`p-1.5 rounded text-sm transition-colors ${
            active
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
          } disabled:opacity-30 disabled:cursor-not-allowed`}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="text-xs">{title}</TooltipContent>
    </Tooltip>
  );
}

// ── Formatting toolbar ────────────────────────────────────────────────────────

function FormattingToolbar({ editor }: { editor: ReturnType<typeof useEditor> | null }) {
  if (!editor) return null;
  const e = editor;

  return (
    <div className="flex items-center gap-0.5 flex-wrap px-2 py-1 border-b border-border/50 bg-background/80 backdrop-blur-sm sticky top-0 z-10">
      {/* Text style */}
      <TBBtn onClick={() => e.chain().focus().toggleBold().run()} active={e.isActive('bold')} title="Bold (Ctrl+B)">
        <Bold className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleItalic().run()} active={e.isActive('italic')} title="Italic (Ctrl+I)">
        <Italic className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleUnderline().run()} active={e.isActive('underline')} title="Underline (Ctrl+U)">
        <UnderlineIcon className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleStrike().run()} active={e.isActive('strike')} title="Strikethrough">
        <Strikethrough className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleHighlight().run()} active={e.isActive('highlight')} title="Highlight">
        <span className="w-3.5 h-3.5 text-xs font-bold leading-none" style={{ background: 'linear-gradient(transparent 60%, #fde047 60%)' }}>A</span>
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleCode().run()} active={e.isActive('code')} title="Inline code">
        <Code className="w-3.5 h-3.5" />
      </TBBtn>

      <div className="w-px h-4 bg-border/60 mx-0.5" />

      {/* Headings */}
      <TBBtn onClick={() => e.chain().focus().toggleHeading({ level: 1 }).run()} active={e.isActive('heading', { level: 1 })} title="Heading 1">
        <Heading1 className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleHeading({ level: 2 }).run()} active={e.isActive('heading', { level: 2 })} title="Heading 2">
        <Heading2 className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleHeading({ level: 3 }).run()} active={e.isActive('heading', { level: 3 })} title="Heading 3">
        <Heading3 className="w-3.5 h-3.5" />
      </TBBtn>

      <div className="w-px h-4 bg-border/60 mx-0.5" />

      {/* Lists */}
      <TBBtn onClick={() => e.chain().focus().toggleBulletList().run()} active={e.isActive('bulletList')} title="Bullet list">
        <List className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleOrderedList().run()} active={e.isActive('orderedList')} title="Numbered list">
        <ListOrdered className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleBlockquote().run()} active={e.isActive('blockquote')} title="Quote">
        <Quote className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().toggleCodeBlock().run()} active={e.isActive('codeBlock')} title="Code block">
        <Code className="w-3.5 h-3.5 opacity-60" />
      </TBBtn>

      <div className="w-px h-4 bg-border/60 mx-0.5" />

      {/* Table */}
      <TBBtn
        onClick={() => e.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
        title="Insert table"
      >
        <TableIcon className="w-3.5 h-3.5" />
      </TBBtn>

      <div className="w-px h-4 bg-border/60 mx-0.5" />

      {/* Alignment */}
      <TBBtn onClick={() => e.chain().focus().setTextAlign('left').run()} active={e.isActive({ textAlign: 'left' })} title="Align left">
        <AlignLeft className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().setTextAlign('center').run()} active={e.isActive({ textAlign: 'center' })} title="Center">
        <AlignCenter className="w-3.5 h-3.5" />
      </TBBtn>
      <TBBtn onClick={() => e.chain().focus().setTextAlign('right').run()} active={e.isActive({ textAlign: 'right' })} title="Align right">
        <AlignRight className="w-3.5 h-3.5" />
      </TBBtn>

      <div className="w-px h-4 bg-border/60 mx-0.5" />

      {/* Undo/Redo */}
      <TBBtn onClick={() => e.chain().focus().undo().run()} disabled={!e.can().undo()} title="Undo (Ctrl+Z)">
        <RotateCcw className="w-3.5 h-3.5" />
      </TBBtn>
    </div>
  );
}

// ── AI Panel ─────────────────────────────────────────────────────────────────

const QUICK_COMMANDS: { cmd: AICommand; label: string; icon: React.ComponentType<{ className?: string }>; desc: string }[] = [
  { cmd: 'continue',   label: 'Continue',    icon: ChevronRight, desc: 'Keep writing from where you left off' },
  { cmd: 'improve',    label: 'Improve',     icon: Wand2,        desc: 'Improve clarity and flow of selection' },
  { cmd: 'expand',     label: 'Expand',      icon: Maximize2,    desc: 'Add more depth and detail' },
  { cmd: 'summarize',  label: 'Summarize',   icon: Minimize2,    desc: 'Condense to key points' },
  { cmd: 'rewrite',    label: 'Rewrite',     icon: RotateCcw,    desc: 'Fresh rewrite, same meaning' },
  { cmd: 'fix',        label: 'Fix grammar', icon: Zap,          desc: 'Fix grammar and spelling' },
  { cmd: 'shorten',    label: 'Shorten',     icon: Scissors,     desc: 'Make more concise' },
  { cmd: 'outline',    label: 'Outline',     icon: List,         desc: 'Generate a detailed outline' },
  { cmd: 'makeformal', label: 'Formalize',   icon: FileText,     desc: 'Professional/academic tone' },
  { cmd: 'makecasual', label: 'Casualize',   icon: MessageSquare,desc: 'Friendly conversational tone' },
  { cmd: 'from_knowledge', label: 'From Knowledge', icon: BookOpen, desc: 'Insert from your knowledge base' },
];

function AIPanel({
  docId, editor, onInsert,
}: {
  docId: string;
  editor: ReturnType<typeof useEditor>;
  onInsert: (text: string) => void;
}) {
  const [activeCmd, setActiveCmd] = useState<AICommand | null>(null);
  const [customAsk, setCustomAsk] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [preview, setPreview] = useState('');
  const [pendingImage, setPendingImage] = useState<{ data: string; type: string } | null>(null);
  const imgInputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const abortRef = useRef<AbortController | null>(null);

  const getContext = useCallback(() => {
    if (!editor) return { selection: '', doc: '' };
    const { from, to } = editor.state.selection;
    const selection = from === to ? '' : editor.state.doc.textBetween(from, to, '\n');
    const doc = editor.getText();
    return { selection, doc };
  }, [editor]);

  const handleImageFile = useCallback((file: File) => {
    if (!file.type.startsWith('image/')) {
      toast({ title: 'Not an image', description: 'Please select an image file.', variant: 'destructive' });
      return;
    }
    const reader = new FileReader();
    reader.onload = (ev) => {
      const dataUrl = ev.target?.result as string;
      const comma = dataUrl.indexOf(',');
      setPendingImage({ data: dataUrl.slice(comma + 1), type: file.type });
    };
    reader.readAsDataURL(file);
  }, [toast]);

  const runCommand = useCallback(async (cmd: AICommand, instruction = '', imgB64?: string, imgType?: string) => {
    if (!docId || streaming) return;
    const { selection, doc } = getContext();

    setActiveCmd(cmd);
    setStreaming(true);
    setPreview('');
    setPendingImage(null); // clear after sending

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const resp = await apiFetch(`${BASE}/write/documents/${docId}/ai`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command: cmd, selection, document_text: doc, instruction,
          ...(imgB64 ? { image_b64: imgB64, image_media_type: imgType ?? 'image/jpeg' } : {}),
        }),
        signal: ctrl.signal,
      });

      if (!resp.ok || !resp.body) throw new Error(await resp.text());

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let result = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (data === '[DONE]') break;
          try {
            const chunk = JSON.parse(data);
            // Handle both our token format and raw OpenAI/Lemonade format
            const token =
              chunk?.token ??
              chunk?.choices?.[0]?.delta?.content ??
              '';
            if (token) { result += token; setPreview(result); }
          } catch { /* skip malformed */ }
        }
      }

      if (result.trim()) setPreview(result.trim());
    } catch (err: unknown) {
      if ((err as Error).name !== 'AbortError') {
        toast({ title: 'AI assist error', description: String(err), variant: 'destructive' });
        setPreview('');
      }
    } finally {
      setStreaming(false);
    }
  }, [docId, streaming, getContext, toast]);

  const handleInsert = () => {
    if (!preview.trim()) return;
    onInsert(preview.trim());
    setPreview('');
    setActiveCmd(null);
  };

  const handleReplace = () => {
    if (!editor || !preview.trim()) return;
    const { from, to } = editor.state.selection;
    if (from === to) {
      // Append after cursor
      editor.chain().focus().insertContentAt(to, preview.trim() + '\n').run();
    } else {
      editor.chain().focus().deleteSelection().insertContent(preview.trim()).run();
    }
    setPreview('');
    setActiveCmd(null);
  };

  const handleDiscard = () => {
    abortRef.current?.abort();
    setPreview('');
    setActiveCmd(null);
    setStreaming(false);
  };

  return (
    <div className="border-t border-border/50 bg-muted/10">
      {/* Preview pane */}
      {(streaming || preview) && (
        <div className="p-3 border-b border-border/40 bg-primary/5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-primary flex items-center gap-1.5">
              <Sparkles className="w-3 h-3" />
              {streaming ? 'Generating…' : 'AI suggestion'}
            </span>
            <div className="flex gap-1.5">
              {!streaming && preview && (
                <>
                  <Button size="sm" className="h-6 text-xs px-2" onClick={handleReplace}>
                    Replace
                  </Button>
                  <Button size="sm" variant="outline" className="h-6 text-xs px-2" onClick={handleInsert}>
                    Insert after
                  </Button>
                </>
              )}
              <Button size="sm" variant="ghost" className="h-6 text-xs px-2 text-muted-foreground" onClick={handleDiscard}>
                {streaming ? 'Stop' : 'Discard'}
              </Button>
            </div>
          </div>
          <div className="text-sm font-mono text-foreground/80 whitespace-pre-wrap max-h-48 overflow-y-auto rounded bg-background/50 p-2 border border-border/30">
            {preview}
            {streaming && <span className="inline-block w-1.5 h-3.5 bg-primary animate-pulse ml-0.5 align-middle" />}
          </div>
        </div>
      )}

      {/* Command grid */}
      <div className="p-2">
        <div className="flex flex-wrap gap-1 mb-2">
          {QUICK_COMMANDS.map(({ cmd, label, icon: Icon, desc }) => (
            <Tooltip key={cmd}>
              <TooltipTrigger asChild>
                <button
                  onClick={() => runCommand(cmd)}
                  disabled={streaming}
                  className={`flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors border ${
                    activeCmd === cmd && streaming
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border/50 bg-background hover:bg-muted/50 text-muted-foreground hover:text-foreground'
                  } disabled:opacity-40`}
                >
                  {activeCmd === cmd && streaming
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Icon className="w-3 h-3" />}
                  {label}
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">{desc}</TooltipContent>
            </Tooltip>
          ))}
        </div>

        {/* Image preview thumbnail */}
        {pendingImage && (
          <div className="flex items-center gap-2 mb-1.5 p-1.5 rounded-lg bg-primary/5 border border-primary/20">
            <img
              src={`data:${pendingImage.type};base64,${pendingImage.data}`}
              alt="Attached"
              className="h-10 w-10 object-cover rounded border border-border/40 shrink-0"
            />
            <span className="text-[11px] text-primary/70 flex-1 truncate">Image attached — ask AI about it</span>
            <button
              onClick={() => setPendingImage(null)}
              className="p-0.5 text-muted-foreground hover:text-destructive transition-colors"
              title="Remove image"
            >
              <XIcon className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* Custom ask */}
        <div className="flex gap-1.5">
          {/* Hidden file input for image upload */}
          <input
            ref={imgInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleImageFile(file);
              e.target.value = '';
            }}
          />
          {/* Image attach button */}
          <button
            type="button"
            title="Attach image for AI analysis"
            disabled={streaming}
            onClick={() => imgInputRef.current?.click()}
            className={`h-7 w-7 flex items-center justify-center rounded border shrink-0 transition-colors
              ${pendingImage
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border/50 bg-background text-muted-foreground hover:text-foreground hover:bg-muted/50'
              } disabled:opacity-40`}
          >
            <ImageIcon className="w-3.5 h-3.5" />
          </button>
          <Input
            value={customAsk}
            onChange={(e) => setCustomAsk(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && (customAsk.trim() || pendingImage)) {
                e.preventDefault();
                runCommand('ask', customAsk.trim(), pendingImage?.data, pendingImage?.type);
                setCustomAsk('');
              }
            }}
            placeholder={pendingImage ? 'Ask AI about this image…' : 'Ask AI anything about this document… (Enter to send)'}
            className="h-7 text-xs"
            disabled={streaming}
          />
          <Button
            size="sm"
            className="h-7 px-2.5 shrink-0"
            disabled={streaming || (!customAsk.trim() && !pendingImage)}
            onClick={() => {
              runCommand('ask', customAsk.trim(), pendingImage?.data, pendingImage?.type);
              setCustomAsk('');
            }}
          >
            <Sparkles className="w-3 h-3" />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Document list sidebar ─────────────────────────────────────────────────────

function DocSidebar({
  docs, activeId, onSelect, onNew, onDelete, onTogglePin, loading,
}: {
  docs: WriteDoc[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
  loading: boolean;
}) {
  const pinned   = docs.filter((d) => d.is_pinned);
  const unpinned = docs.filter((d) => !d.is_pinned);

  const DocRow = ({ doc }: { doc: WriteDoc }) => (
    <div
      onClick={() => onSelect(doc.id)}
      className={`group flex items-start gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
        activeId === doc.id
          ? 'bg-primary/10 text-primary'
          : 'hover:bg-muted/50 text-foreground/80'
      }`}
    >
      <FileText className="w-3.5 h-3.5 mt-0.5 shrink-0 opacity-60" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium truncate">{doc.title || 'Untitled'}</p>
        <p className="text-[10px] text-muted-foreground">{doc.word_count.toLocaleString()} words</p>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            onClick={(e) => e.stopPropagation()}
            className="opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 p-0.5 hover:bg-muted rounded transition-opacity"
          >
            <MoreHorizontal className="w-3 h-3 text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="text-xs">
          <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onTogglePin(doc.id, !doc.is_pinned); }}>
            {doc.is_pinned ? <><PinOff className="w-3 h-3 mr-2" />Unpin</> : <><Pin className="w-3 h-3 mr-2" />Pin</>}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={(e) => { e.stopPropagation(); onDelete(doc.id); }}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="w-3 h-3 mr-2" />Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );

  return (
    <div className="w-52 shrink-0 border-r border-border/50 flex flex-col bg-muted/10">
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50">
        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Documents</span>
        <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={onNew} disabled={loading}>
          <Plus className="w-3.5 h-3.5" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
        {loading && <p className="text-xs text-muted-foreground p-3">Loading…</p>}

        {pinned.length > 0 && (
          <>
            <p className="text-[10px] text-muted-foreground uppercase tracking-wide px-2 pt-1 pb-0.5">Pinned</p>
            {pinned.map((d) => <DocRow key={d.id} doc={d} />)}
            {unpinned.length > 0 && <Separator className="my-1" />}
          </>
        )}

        {unpinned.map((d) => <DocRow key={d.id} doc={d} />)}

        {!loading && docs.length === 0 && (
          <div className="text-center py-8 px-4">
            <FileText className="w-6 h-6 text-muted-foreground/40 mx-auto mb-2" />
            <p className="text-xs text-muted-foreground">No documents yet</p>
            <Button size="sm" variant="outline" className="mt-3 h-6 text-xs" onClick={onNew}>
              <Plus className="w-3 h-3 mr-1" />New
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function WriteDeskPage() {
  const [docs, setDocs]           = useState<WriteDoc[]>([]);
  const [activeDoc, setActiveDoc] = useState<WriteDoc | null>(null);
  const [title, setTitle]         = useState('');
  const [loading, setLoading]     = useState(true);
  const [saving, setSaving]       = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [aiPanelOpen, setAiPanelOpen] = useState(() => {
    if (typeof window === 'undefined') return true;
    const stored = localStorage.getItem('write_ai_panel_open');
    if (stored !== null) return stored === 'true';
    return window.innerWidth >= 640; // width-based default when no preference stored
  });
  // mobileSidebarOpen: controls the slide-over on viewports < 640 px.
  // Defaults to false — on narrow phones the sidebar auto-hides so the editor
  // gets the full width without the user needing to find focus mode.
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const saveTimer                 = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { toast } = useToast();
  const [, navigate] = useLocation();

  // ── Editor ────────────────────────────────────────────────────────────────

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      // Disable Link and Underline inside StarterKit so our custom configs below
      // are the only registered instances (avoids "duplicate extension" crash in v3).
      StarterKit.configure({ link: false, underline: false }),
      Typography,
      Underline,
      Highlight.configure({ multicolor: true }),
      TextStyle,
      Color,
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
      CharacterCount,
      Link.configure({ openOnClick: false }),
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      Placeholder.configure({ placeholder: 'Start writing here… or use an AI command below to get started.' }),
    ],
    content: '',
    onUpdate: ({ editor: ed }) => {
      scheduleSave(ed);
    },
    editorProps: {
      attributes: {
        class: 'prose prose-sm dark:prose-invert max-w-none focus:outline-none min-h-[400px] px-8 py-6',
      },
    },
  });

  // ── Load docs ─────────────────────────────────────────────────────────────

  const loadDocs = useCallback(async () => {
    try {
      const data = await apiGet('/write/documents');
      const list: WriteDoc[] = data.documents ?? [];
      setDocs(list);
      return list;
    } catch (err) {
      toast({ title: 'Could not load documents', description: String(err), variant: 'destructive' });
      return [] as WriteDoc[];
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    loadDocs().then((list) => {
      if (list.length > 0) openDoc(list[0]);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist the AI panel open/closed preference so it survives page reloads
  // and navigation.  Written on every toggle; read back in the useState initializer.
  useEffect(() => {
    try {
      localStorage.setItem('write_ai_panel_open', String(aiPanelOpen));
    } catch {
      // localStorage unavailable (private browsing, quota exceeded) — silently skip
    }
  }, [aiPanelOpen]);

  // ── Open a doc ────────────────────────────────────────────────────────────

  const openDoc = useCallback(async (doc: WriteDoc) => {
    if (!editor) return;
    setActiveDoc(doc);
    setTitle(doc.title);
    try {
      const full = await apiGet(`/write/documents/${doc.id}`);
      const json = full.content_json;
      if (json && typeof json === 'object' && json.type === 'doc') {
        editor.commands.setContent(json);
      } else {
        editor.commands.setContent(full.content_text || '');
      }
    } catch {
      editor.commands.setContent(doc.content_text || '');
    }
  }, [editor]);

  const handleSelectDoc = useCallback(async (id: string) => {
    const doc = docs.find((d) => d.id === id);
    if (doc) await openDoc(doc);
  }, [docs, openDoc]);

  // ── Auto-save ─────────────────────────────────────────────────────────────

  const scheduleSave = useCallback((ed: typeof editor) => {
    if (!activeDoc || !ed) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      const json = ed.getJSON();
      const text = ed.getText();
      setSaving(true);
      try {
        const updated = await apiPatch(`/write/documents/${activeDoc.id}`, {
          content_json: json,
          content_text: text,
        });
        setDocs((prev) => prev.map((d) => d.id === updated.id ? updated : d));
        setActiveDoc((prev) => prev?.id === updated.id ? updated : prev);
      } catch { /* silent — next save will retry */ }
      finally { setSaving(false); }
    }, 1500);
  }, [activeDoc]);

  // ── Title save ────────────────────────────────────────────────────────────

  const saveTitle = useCallback(async (newTitle: string) => {
    if (!activeDoc || newTitle === activeDoc.title) return;
    try {
      const updated = await apiPatch(`/write/documents/${activeDoc.id}`, { title: newTitle });
      setDocs((prev) => prev.map((d) => d.id === updated.id ? updated : d));
      setActiveDoc(updated);
    } catch { /* ignore */ }
  }, [activeDoc]);

  // ── New doc ───────────────────────────────────────────────────────────────

  const handleNew = useCallback(async () => {
    try {
      const newDoc = await apiPost('/write/documents', { title: 'Untitled' });
      setDocs((prev) => [newDoc, ...prev]);
      await openDoc(newDoc);
    } catch (err) {
      toast({ title: 'Could not create document', description: String(err), variant: 'destructive' });
    }
  }, [openDoc, toast]);

  // ── Delete doc ────────────────────────────────────────────────────────────

  const handleDelete = useCallback(async (id: string) => {
    try {
      await apiDelete(`/write/documents/${id}`);
      const next = docs.filter((d) => d.id !== id);
      setDocs(next);
      if (activeDoc?.id === id) {
        if (next.length > 0) await openDoc(next[0]);
        else { setActiveDoc(null); editor?.commands.setContent(''); setTitle(''); }
      }
    } catch (err) {
      toast({ title: 'Delete failed', description: String(err), variant: 'destructive' });
    }
  }, [docs, activeDoc, openDoc, editor, toast]);

  // ── Pin ───────────────────────────────────────────────────────────────────

  const handleTogglePin = useCallback(async (id: string, pinned: boolean) => {
    try {
      const updated = await apiPatch(`/write/documents/${id}`, { is_pinned: pinned });
      setDocs((prev) => prev.map((d) => d.id === id ? updated : d));
    } catch { /* ignore */ }
  }, []);

  // ── Export ────────────────────────────────────────────────────────────────

  const handleExport = useCallback(async () => {
    if (!activeDoc) return;
    try {
      const r = await apiFetch(`${BASE}/write/documents/${activeDoc.id}/export/txt`, { credentials: 'include' });
      const blob = await r.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href     = url;
      a.download = `${activeDoc.title || 'document'}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast({ title: 'Export failed', description: String(err), variant: 'destructive' });
    }
  }, [activeDoc, toast]);

  // ── AI insert helper ──────────────────────────────────────────────────────

  const handleAIInsert = useCallback((text: string) => {
    if (!editor) return;
    const { to } = editor.state.selection;
    editor.chain().focus().insertContentAt(to, '\n' + text).run();
  }, [editor]);

  // ── iOS keyboard: keep cursor above keyboard ──────────────────────────────
  //
  // When the iPhone software keyboard opens, the visual viewport shrinks.
  // TipTap's ProseMirror editor doesn't automatically re-scroll to the cursor
  // in response — so if the cursor was near the bottom, it ends up hidden
  // beneath the keyboard. We listen to visualViewport "resize" events and call
  // scrollIntoView() whenever the viewport height decreases (keyboard opening).
  // This has no effect on desktop or Android where visualViewport is absent or
  // doesn't trigger on keyboard events.
  useEffect(() => {
    const vv = typeof window !== 'undefined' ? window.visualViewport : null;
    if (!editor || !vv) return;

    let prevHeight = vv.height;

    const handleResize = () => {
      const newHeight = vv.height;
      if (newHeight < prevHeight) {
        // Viewport shrank → software keyboard just opened (or expanded).
        // Ask ProseMirror to scroll the cursor into the visible area.
        editor.commands.scrollIntoView();
      }
      prevHeight = newHeight;
    };

    vv.addEventListener('resize', handleResize);
    return () => { vv.removeEventListener('resize', handleResize); };
  }, [editor]);

  // ── Auto-collapse sidebar on narrow viewports (<640 px) ──────────────────
  //
  // When the user rotates to portrait or opens the page on a phone, close the
  // sidebar so the editor has the full width.  Widening back to ≥640 px
  // (tablet / desktop) does NOT re-open it — the user controls that threshold
  // themselves — but the sidebar is always visible there via CSS (sm:translate-x-0).
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 639px)');
    const close = (e: MediaQueryListEvent) => { if (e.matches) setMobileSidebarOpen(false); };
    mq.addEventListener('change', close);
    return () => mq.removeEventListener('change', close);
  }, []);

  // ── Word count display ────────────────────────────────────────────────────

  const wordCount = editor?.storage.characterCount?.words() ?? 0;
  const readTime  = Math.max(1, Math.ceil(wordCount / 200));

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden bg-background">
      {/* Sidebar — static on ≥sm (640 px); slide-over on narrow phones */}
      {!focusMode && (
        <>
          {/* Tap-outside backdrop — mobile only */}
          {mobileSidebarOpen && (
            <div
              className="fixed inset-0 z-20 bg-black/40 sm:hidden"
              onClick={() => setMobileSidebarOpen(false)}
              aria-hidden="true"
            />
          )}

          {/*
           * Positioning wrapper:
           *   • mobile (<sm): fixed slide-in from the left, above content (z-30)
           *   • desktop (≥sm): normal flex child, always visible
           *
           * The translate trick keeps the sidebar in the DOM so TipTap focus
           * events aren't disrupted when toggling on desktop.
           */}
          <div className={[
            'fixed sm:relative inset-y-0 left-0 z-30 sm:z-auto',
            'transition-transform duration-200 ease-in-out',
            mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full sm:translate-x-0',
          ].join(' ')}>
            <DocSidebar
              docs={docs}
              activeId={activeDoc?.id ?? null}
              onSelect={(id) => { handleSelectDoc(id); setMobileSidebarOpen(false); }}
              onNew={() => { handleNew(); setMobileSidebarOpen(false); }}
              onDelete={handleDelete}
              onTogglePin={handleTogglePin}
              loading={loading}
            />
          </div>
        </>
      )}

      {/* Editor area */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">

        {/* Header bar */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-border/50 bg-background/80 backdrop-blur-sm shrink-0">
          {/* Documents list toggle — only visible on narrow phones (hidden on sm+) */}
          {!focusMode && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm" variant="ghost"
                  className="h-7 w-7 p-0 shrink-0 sm:hidden"
                  onClick={() => setMobileSidebarOpen((v) => !v)}
                  aria-label="Toggle document list"
                >
                  <PanelLeft className="w-3.5 h-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent className="text-xs">Documents</TooltipContent>
            </Tooltip>
          )}
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => saveTitle(title)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.currentTarget.blur(); } }}
            placeholder="Document title…"
            className="border-none shadow-none text-base font-semibold h-8 px-0 focus-visible:ring-0 bg-transparent flex-1"
            disabled={!activeDoc}
          />

          {activeDoc && (
            <div className="flex items-center gap-2 shrink-0">
              {saving && <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />}
              <span className="text-xs text-muted-foreground">
                {wordCount.toLocaleString()} words · {readTime} min read
              </span>
              <Badge variant="outline" className="text-[10px] h-5 font-mono hidden sm:flex">
                {activeDoc.updated_at
                  ? `Saved ${format(new Date(activeDoc.updated_at), 'HH:mm')}`
                  : 'New'}
              </Badge>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => setFocusMode((f) => !f)}>
                    {focusMode ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent className="text-xs">{focusMode ? 'Exit focus mode' : 'Focus mode'}</TooltipContent>
              </Tooltip>

              <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={handleExport}>
                <Download className="w-3.5 h-3.5" />
              </Button>
            </div>
          )}
        </div>

        {activeDoc ? (
          <>
            {/* Formatting toolbar */}
            <FormattingToolbar editor={editor} />

            {/* Editor */}
            <div className="flex-1 overflow-y-auto">
              <div className={`mx-auto ${focusMode ? 'max-w-2xl' : 'max-w-3xl'} py-2`}>
                <EditorContent editor={editor} />
              </div>
            </div>

            {/* AI Panel — collapsible */}
            {editor && (
              <div className="shrink-0 border-t border-border/50">
                <button
                  onClick={() => setAiPanelOpen(v => !v)}
                  className="w-full px-3 py-1.5 flex items-center justify-between text-xs text-muted-foreground hover:bg-muted/30 transition-colors bg-muted/10"
                  title={aiPanelOpen ? "Collapse AI panel" : "Expand AI panel"}
                >
                  <span className="flex items-center gap-1.5 font-medium">
                    <Sparkles className="w-3 h-3 text-primary/60" />
                    AI Assistant
                  </span>
                  <ChevronDown className={`w-3 h-3 transition-transform duration-150 ${aiPanelOpen ? "" : "-rotate-90"}`} />
                </button>
                {aiPanelOpen && (
                  <AIPanel
                    docId={activeDoc.id}
                    editor={editor}
                    onInsert={handleAIInsert}
                  />
                )}
              </div>
            )}
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center space-y-4">
              <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto">
                <Sparkles className="w-8 h-8 text-primary" />
              </div>
              <div>
                <h2 className="text-lg font-semibold">Write Desk</h2>
                <p className="text-sm text-muted-foreground mt-1 max-w-sm">
                  Draft documents with AI assistance. Continue writing, improve your prose,
                  or pull insights directly from your knowledge base.
                </p>
              </div>
              <Button onClick={handleNew}>
                <Plus className="w-4 h-4 mr-2" />New document
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
