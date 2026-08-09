/**
 * Write Desk editor — full-screen markdown text editor with:
 * - Formatting toolbar (Bold, Italic, H1, H2, Bullet, Numbered, Code)
 * - Autosave (2 s debounce after last keystroke)
 * - Work linking via bottom-sheet picker
 * - AI Assist action sheet → streams from /api/write/documents/{id}/ai
 * - Export via native share sheet
 */
import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  ActionSheetIOS,
  ActivityIndicator,
  Alert,
  Animated,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { mobileFetch, mobileStreamFetch } from '@/lib/api';
import { font } from '@/lib/typography';
import { useSheetAnimation } from '@/lib/useSheetAnimation';
import { apiOrigin } from '@/lib/server';

const DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const API = () => `${DOMAIN()}/api`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface WriteDoc {
  id: string;
  title: string;
  content_text: string;
  word_count: number;
  work_id: string | null;
  is_pinned: number;
  updated_at: string;
}

interface Work {
  id: string;
  title: string;
}

type AICommand =
  | 'continue' | 'improve' | 'expand' | 'summarize' | 'rewrite'
  | 'fix' | 'shorten' | 'outline' | 'makeformal' | 'makecasual'
  | 'from_knowledge';

const AI_ACTIONS: { cmd: AICommand; label: string; description: string }[] = [
  { cmd: 'continue',     label: 'Continue writing',           description: 'Keep writing from where you left off' },
  { cmd: 'improve',      label: 'Improve this paragraph',     description: 'Improve clarity and flow of selection' },
  { cmd: 'expand',       label: 'Expand',                     description: 'Add more depth and detail' },
  { cmd: 'summarize',    label: 'Summarize',                  description: 'Condense to key points' },
  { cmd: 'rewrite',      label: 'Rewrite',                    description: 'Fresh rewrite, same meaning' },
  { cmd: 'fix',          label: 'Fix grammar',                description: 'Fix grammar and spelling' },
  { cmd: 'shorten',      label: 'Shorten',                    description: 'Make more concise' },
  { cmd: 'outline',      label: 'Outline',                    description: 'Generate a detailed outline' },
  { cmd: 'makeformal',   label: 'Formalize tone',             description: 'Professional / academic tone' },
  { cmd: 'makecasual',   label: 'Casual tone',                description: 'Friendly conversational tone' },
  { cmd: 'from_knowledge', label: 'From Work knowledge',      description: 'Insert from your knowledge base' },
];

// ── Markdown text helpers ─────────────────────────────────────────────────────

function wrapOrInsert(
  text: string,
  selection: { start: number; end: number },
  prefix: string,
  suffix: string = prefix,
): { text: string; selection: { start: number; end: number } } {
  const sel = text.slice(selection.start, selection.end);
  if (sel.length > 0) {
    // Wrap selection
    const newText =
      text.slice(0, selection.start) +
      prefix + sel + suffix +
      text.slice(selection.end);
    return {
      text: newText,
      selection: {
        start: selection.start,
        end: selection.start + prefix.length + sel.length + suffix.length,
      },
    };
  }
  // Insert at cursor with placeholder
  const placeholder = 'text';
  const newText =
    text.slice(0, selection.start) +
    prefix + placeholder + suffix +
    text.slice(selection.start);
  return {
    text: newText,
    selection: {
      start: selection.start + prefix.length,
      end: selection.start + prefix.length + placeholder.length,
    },
  };
}

function insertLinePrefix(
  text: string,
  selection: { start: number; end: number },
  prefix: string,
): { text: string; selection: { start: number; end: number } } {
  // Find start of current line
  let lineStart = selection.start;
  while (lineStart > 0 && text[lineStart - 1] !== '\n') {
    lineStart--;
  }
  const newText = text.slice(0, lineStart) + prefix + text.slice(lineStart);
  const offset = prefix.length;
  return {
    text: newText,
    selection: {
      start: selection.start + offset,
      end: selection.end + offset,
    },
  };
}

function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 10) return 'just saved';
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

// ── Formatting toolbar ────────────────────────────────────────────────────────

interface ToolbarProps {
  onFormat: (action: string) => void;
  onAIAssist: () => void;
  onExport: () => void;
  aiLoading: boolean;
}

function FormattingToolbar({ onFormat, onAIAssist, onExport, aiLoading }: ToolbarProps) {
  const colors = useColors();

  const btn = (action: string, icon: string, label: string) => (
    <Pressable
      key={action}
      onPress={() => onFormat(action)}
      hitSlop={4}
      style={({ pressed }) => [
        styles.tbBtn,
        {
          backgroundColor: pressed ? `${colors.primary}20` : 'transparent',
          borderColor: colors.border,
        },
      ]}
      accessibilityLabel={label}
    >
      <Feather name={icon as any} size={16} color={colors.foreground} />
    </Pressable>
  );

  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={[styles.toolbar, { borderTopColor: colors.border, backgroundColor: colors.card }]}
      contentContainerStyle={styles.toolbarContent}
      keyboardShouldPersistTaps="always"
    >
      {btn('bold',     'bold',          'Bold')}
      {btn('italic',   'italic',        'Italic')}
      {btn('h1',       'type',          'Heading 1')}
      {btn('h2',       'hash',          'Heading 2')}
      {btn('bullet',   'list',          'Bullet list')}
      {btn('numbered', 'list',          'Numbered list')}
      {btn('code',     'code',          'Inline code')}
      {btn('quote',    'message-square','Block quote')}

      {/* Divider */}
      <View style={[styles.tbDivider, { backgroundColor: colors.border }]} />

      {/* AI Assist button */}
      <Pressable
        onPress={onAIAssist}
        disabled={aiLoading}
        style={({ pressed }) => [
          styles.tbAIBtn,
          { backgroundColor: colors.primary, opacity: pressed || aiLoading ? 0.75 : 1 },
        ]}
        accessibilityLabel="AI Assist"
      >
        {aiLoading ? (
          <ActivityIndicator size="small" color="#fff" />
        ) : (
          <Feather name="zap" size={14} color="#fff" />
        )}
        <Text style={styles.tbAILabel}>{aiLoading ? 'Thinking…' : 'AI Assist'}</Text>
      </Pressable>

      {/* Export */}
      <Pressable
        onPress={onExport}
        style={({ pressed }) => [
          styles.tbBtn,
          {
            backgroundColor: pressed ? `${colors.primary}20` : 'transparent',
            borderColor: colors.border,
          },
        ]}
        accessibilityLabel="Export"
      >
        <Feather name="share" size={16} color={colors.foreground} />
      </Pressable>
    </ScrollView>
  );
}

// ── AI result overlay ─────────────────────────────────────────────────────────

interface AIResultOverlayProps {
  visible: boolean;
  text: string;
  streaming: boolean;
  onAccept: () => void;
  onInsert: () => void;
  onDiscard: () => void;
}

function AIResultOverlay({
  visible, text, streaming, onAccept, onInsert, onDiscard,
}: AIResultOverlayProps) {
  const colors = useColors();
  const { rendered, slideAnim, fadeAnim, panHandlers, scrollHandler } = useSheetAnimation(visible, 480, onDiscard);
  if (!rendered) return null;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onDiscard}>
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.45)', opacity: fadeAnim }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={streaming ? undefined : onDiscard} />
      </Animated.View>
      <Animated.View {...panHandlers} style={[styles.overlayCard, { backgroundColor: colors.card, borderColor: colors.border, position: 'absolute', bottom: 0, left: 0, right: 0, transform: [{ translateY: slideAnim }] }]}>
          {/* Header */}
          <View style={styles.overlayHeader}>
            <View style={styles.overlayTitleRow}>
              <Feather name="zap" size={14} color={colors.primary} />
              <Text style={[styles.overlayTitle, { color: colors.primary }]}>
                {streaming ? 'Generating…' : 'AI suggestion'}
              </Text>
              {streaming && <ActivityIndicator size="small" color={colors.primary} style={{ marginLeft: 6 }} />}
            </View>
            {!streaming && (
              <Pressable onPress={onDiscard} hitSlop={8}>
                <Feather name="x" size={18} color={colors.mutedForeground} />
              </Pressable>
            )}
          </View>

          {/* Result text */}
          <ScrollView style={styles.overlayScroll} contentContainerStyle={{ paddingBottom: 8 }} onScroll={scrollHandler} scrollEventThrottle={16}>
            <Text style={[styles.overlayText, { color: colors.foreground }]}>
              {text}
              {streaming && (
                <Text style={{ color: colors.primary }}> ▌</Text>
              )}
            </Text>
          </ScrollView>

          {/* Actions */}
          {!streaming && text.trim().length > 0 && (
            <View style={[styles.overlayActions, { borderTopColor: colors.border }]}>
              <Pressable
                onPress={onAccept}
                style={({ pressed }) => [
                  styles.overlayBtn,
                  { backgroundColor: colors.primary, opacity: pressed ? 0.8 : 1 },
                ]}
              >
                <Feather name="check" size={15} color="#fff" />
                <Text style={styles.overlayBtnText}>Replace selection</Text>
              </Pressable>
              <Pressable
                onPress={onInsert}
                style={({ pressed }) => [
                  styles.overlayBtnSecondary,
                  { borderColor: colors.border, opacity: pressed ? 0.8 : 1 },
                ]}
              >
                <Feather name="plus" size={15} color={colors.foreground} />
                <Text style={[styles.overlayBtnSecondaryText, { color: colors.foreground }]}>Insert after</Text>
              </Pressable>
              <Pressable
                onPress={onDiscard}
                style={({ pressed }) => [
                  styles.overlayBtnGhost,
                  { opacity: pressed ? 0.8 : 1 },
                ]}
              >
                <Text style={[styles.overlayBtnGhostText, { color: colors.mutedForeground }]}>Discard</Text>
              </Pressable>
            </View>
          )}

          {streaming && (
            <View style={[styles.overlayActions, { borderTopColor: colors.border }]}>
              <Pressable
                onPress={onDiscard}
                style={({ pressed }) => [
                  styles.overlayBtnGhost,
                  { opacity: pressed ? 0.8 : 1 },
                ]}
              >
                <Text style={[styles.overlayBtnGhostText, { color: colors.mutedForeground }]}>Stop</Text>
              </Pressable>
            </View>
          )}
      </Animated.View>
    </Modal>
  );
}

// ── Work link picker ──────────────────────────────────────────────────────────

interface WorkPickerProps {
  visible: boolean;
  works: Work[];
  currentWorkId: string | null;
  onSelect: (workId: string | null) => void;
  onClose: () => void;
}

function WorkPickerSheet({ visible, works, currentWorkId, onSelect, onClose }: WorkPickerProps) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const { rendered, slideAnim, fadeAnim, panHandlers, scrollHandler } = useSheetAnimation(visible, 400, onClose);
  if (!rendered) return null;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose}>
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.4)', opacity: fadeAnim }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>
      <Animated.View
        {...panHandlers}
        style={[
          styles.sheet,
          { backgroundColor: colors.card, borderColor: colors.border, paddingBottom: insets.bottom + 16, position: 'absolute', bottom: 0, left: 0, right: 0, transform: [{ translateY: slideAnim }] },
        ]}
      >
          <View style={[styles.sheetHandle, { backgroundColor: colors.border }]} />
          <Text style={[styles.sheetTitle, { color: colors.foreground }]}>Link to Work</Text>

          <ScrollView style={{ maxHeight: 320 }} onScroll={scrollHandler} scrollEventThrottle={16}>
            {/* No work option */}
            <Pressable
              onPress={() => { onSelect(null); onClose(); }}
              style={[
                styles.workItem,
                { borderColor: !currentWorkId ? colors.primary : colors.border },
              ]}
            >
              <Feather
                name="slash"
                size={16}
                color={!currentWorkId ? colors.primary : colors.mutedForeground}
              />
              <Text
                style={[
                  styles.workItemText,
                  { color: !currentWorkId ? colors.primary : colors.foreground },
                ]}
              >
                No Work
              </Text>
              {!currentWorkId && (
                <Feather name="check" size={14} color={colors.primary} style={{ marginLeft: 'auto' }} />
              )}
            </Pressable>

            {works.map((w) => (
              <Pressable
                key={w.id}
                onPress={() => { onSelect(w.id); onClose(); }}
                style={[
                  styles.workItem,
                  { borderColor: currentWorkId === w.id ? colors.primary : colors.border },
                ]}
              >
                <Feather
                  name="book-open"
                  size={16}
                  color={currentWorkId === w.id ? colors.primary : colors.mutedForeground}
                />
                <Text
                  style={[
                    styles.workItemText,
                    { color: currentWorkId === w.id ? colors.primary : colors.foreground },
                  ]}
                  numberOfLines={1}
                >
                  {w.title}
                </Text>
                {currentWorkId === w.id && (
                  <Feather name="check" size={14} color={colors.primary} style={{ marginLeft: 'auto' }} />
                )}
              </Pressable>
            ))}
          </ScrollView>
      </Animated.View>
    </Modal>
  );
}

// ── Main editor screen ────────────────────────────────────────────────────────

export default function WriteEditorScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const colors = useColors();
  const insets = useSafeAreaInsets();

  // ── Doc state ───────────────────────────────────────────────────────────────
  const [doc, setDoc] = useState<WriteDoc | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [wordCount, setWordCount] = useState(0);
  const [lastSaved, setLastSaved] = useState('');
  const [loadError, setLoadError] = useState(false);

  // ── Editor state ────────────────────────────────────────────────────────────
  const inputRef = useRef<TextInput>(null);
  const selectionRef = useRef({ start: 0, end: 0 });
  const [selection, setSelection] = useState({ start: 0, end: 0 });

  // ── Autosave ────────────────────────────────────────────────────────────────
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isSavingRef = useRef(false);
  const pendingSaveRef = useRef<{ title: string; content: string } | null>(null);

  // ── Works ───────────────────────────────────────────────────────────────────
  const [works, setWorks] = useState<Work[]>([]);
  const [workPickerOpen, setWorkPickerOpen] = useState(false);
  const [linkedWorkId, setLinkedWorkId] = useState<string | null>(null);
  const [linkedWorkTitle, setLinkedWorkTitle] = useState<string | null>(null);

  // ── AI ──────────────────────────────────────────────────────────────────────
  const [aiLoading, setAILoading] = useState(false);
  const [aiResult, setAIResult] = useState('');
  const [aiStreaming, setAIStreaming] = useState(false);
  const aiAbortRef = useRef<AbortController | null>(null);
  const [aiOverlayVisible, setAIOverlayVisible] = useState(false);
  const aiSelectionRef = useRef({ start: 0, end: 0 });

  // ── Load document ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (!id) return;
    (async () => {
      try {
        const r = await mobileFetch(`${API()}/write/documents/${id}`);
        if (!r.ok) throw new Error('Not found');
        const data: WriteDoc = await r.json();
        setDoc(data);
        setTitle(data.title ?? '');
        setContent(data.content_text ?? '');
        setWordCount(data.word_count ?? 0);
        setLastSaved(data.updated_at);
        setLinkedWorkId(data.work_id);
      } catch {
        setLoadError(true);
      }
    })();
  }, [id]);

  // ── Load works ──────────────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const r = await mobileFetch(`${API()}/works?limit=200`);
        if (r.ok) {
          const data = await r.json();
          const list: Work[] = (data.works ?? data.items ?? []).map((w: any) => ({
            id: w.id,
            title: w.title,
          }));
          setWorks(list);
          if (linkedWorkId) {
            const linked = list.find((w) => w.id === linkedWorkId);
            if (linked) setLinkedWorkTitle(linked.title);
          }
        }
      } catch {
        // works are optional
      }
    })();
  }, []);

  // Update linked work title when works or linkedWorkId changes
  useEffect(() => {
    if (!linkedWorkId) {
      setLinkedWorkTitle(null);
      return;
    }
    const found = works.find((w) => w.id === linkedWorkId);
    if (found) setLinkedWorkTitle(found.title);
  }, [linkedWorkId, works]);

  // ── Autosave ────────────────────────────────────────────────────────────────
  const scheduleSave = useCallback(
    (newTitle: string, newContent: string) => {
      pendingSaveRef.current = { title: newTitle, content: newContent };
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(async () => {
        if (isSavingRef.current || !id || !pendingSaveRef.current) return;
        const { title: t, content: c } = pendingSaveRef.current;
        pendingSaveRef.current = null;
        isSavingRef.current = true;
        try {
          const r = await mobileFetch(`${API()}/write/documents/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              title: t,
              content_text: c,
              content_json: {},
            }),
          });
          if (r.ok) {
            const data = await r.json();
            setLastSaved(data.updated_at ?? new Date().toISOString());
          }
        } catch {
          // fail silently — will retry on next keystroke
        } finally {
          isSavingRef.current = false;
        }
      }, 2000);
    },
    [id]
  );

  const handleTitleChange = useCallback(
    (t: string) => {
      setTitle(t);
      scheduleSave(t, content);
    },
    [content, scheduleSave]
  );

  const handleContentChange = useCallback(
    (c: string) => {
      setContent(c);
      setWordCount(countWords(c));
      scheduleSave(title, c);
    },
    [title, scheduleSave]
  );

  // ── Formatting ───────────────────────────────────────────────────────────────
  const handleFormat = useCallback(
    (action: string) => {
      const sel = selectionRef.current;
      let result: { text: string; selection: { start: number; end: number } };

      switch (action) {
        case 'bold':
          result = wrapOrInsert(content, sel, '**');
          break;
        case 'italic':
          result = wrapOrInsert(content, sel, '_');
          break;
        case 'code':
          result = wrapOrInsert(content, sel, '`');
          break;
        case 'h1':
          result = insertLinePrefix(content, sel, '# ');
          break;
        case 'h2':
          result = insertLinePrefix(content, sel, '## ');
          break;
        case 'bullet':
          result = insertLinePrefix(content, sel, '- ');
          break;
        case 'numbered':
          result = insertLinePrefix(content, sel, '1. ');
          break;
        case 'quote':
          result = insertLinePrefix(content, sel, '> ');
          break;
        default:
          return;
      }

      setContent(result.text);
      setWordCount(countWords(result.text));
      setSelection(result.selection);
      selectionRef.current = result.selection;
      scheduleSave(title, result.text);

      // Restore focus + selection on native
      if (Platform.OS !== 'web') {
        requestAnimationFrame(() => {
          inputRef.current?.focus();
          inputRef.current?.setNativeProps?.({ selection: result.selection });
        });
      }
    },
    [content, title, scheduleSave]
  );

  // ── Work linking ──────────────────────────────────────────────────────────
  const handleWorkLink = useCallback(
    async (workId: string | null) => {
      setLinkedWorkId(workId);
      if (!id) return;
      try {
        await mobileFetch(`${API()}/write/documents/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ work_id: workId ?? '__none__' }),
        });
      } catch {
        Alert.alert('Could not update Work link');
      }
    },
    [id]
  );

  // ── AI assist ─────────────────────────────────────────────────────────────
  const showAIActions = useCallback(() => {
    const labels = AI_ACTIONS.map((a) => a.label);

    if (Platform.OS === 'ios') {
      ActionSheetIOS.showActionSheetWithOptions(
        {
          title: 'AI Assist',
          message: 'Choose an action to apply to the selected text or document',
          options: [...labels, 'Cancel'],
          cancelButtonIndex: labels.length,
        },
        (idx) => {
          if (idx < AI_ACTIONS.length) {
            runAICommand(AI_ACTIONS[idx].cmd);
          }
        }
      );
    } else {
      Alert.alert(
        'AI Assist',
        'Choose an action',
        [
          ...AI_ACTIONS.map((a) => ({
            text: a.label,
            onPress: () => runAICommand(a.cmd),
          })),
          { text: 'Cancel', style: 'cancel' as const },
        ]
      );
    }
  }, [content, selectionRef.current]);

  const runAICommand = useCallback(
    async (cmd: AICommand) => {
      if (aiLoading || !id) return;

      const sel = selectionRef.current;
      const selectedText = content.slice(sel.start, sel.end);
      aiSelectionRef.current = sel;

      setAIResult('');
      setAIStreaming(true);
      setAILoading(true);
      setAIOverlayVisible(true);

      const ctrl = new AbortController();
      aiAbortRef.current = ctrl;

      try {
        // Streaming-capable fetch: RN's built-in fetch has no readable body
        // on device, which made this AI stream fail with "AI request failed".
        const resp = await mobileStreamFetch(`${API()}/write/documents/${id}/ai`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            command: cmd,
            selection: selectedText,
            document_text: content.slice(0, 4000),
          }),
          signal: ctrl.signal,
        });

        if (!resp.ok || !resp.body) throw new Error('AI request failed');

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
            const raw = line.slice(6).trim();
            if (raw === '[DONE]') break;
            try {
              const chunk = JSON.parse(raw);
              const token =
                chunk?.token ??
                chunk?.choices?.[0]?.delta?.content ??
                '';
              if (token) {
                result += token;
                setAIResult(result);
              }
            } catch {
              // skip malformed
            }
          }
        }

        if (result.trim()) setAIResult(result.trim());
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          Alert.alert('AI error', err?.message ?? 'Something went wrong');
          setAIOverlayVisible(false);
        }
      } finally {
        setAIStreaming(false);
        setAILoading(false);
      }
    },
    [aiLoading, id, content]
  );

  const handleAIAccept = useCallback(() => {
    const sel = aiSelectionRef.current;
    const result = aiResult.trim();
    if (!result) return;

    let newContent: string;
    if (sel.start === sel.end) {
      // Insert at cursor
      newContent = content.slice(0, sel.start) + result + '\n' + content.slice(sel.start);
    } else {
      // Replace selection
      newContent = content.slice(0, sel.start) + result + content.slice(sel.end);
    }
    setContent(newContent);
    setWordCount(countWords(newContent));
    scheduleSave(title, newContent);
    setAIOverlayVisible(false);
    setAIResult('');
  }, [aiResult, content, title, scheduleSave]);

  const handleAIInsert = useCallback(() => {
    const sel = aiSelectionRef.current;
    const result = aiResult.trim();
    if (!result) return;
    // Always insert after current position
    const insertAt = Math.max(sel.start, sel.end);
    const newContent = content.slice(0, insertAt) + '\n' + result + '\n' + content.slice(insertAt);
    setContent(newContent);
    setWordCount(countWords(newContent));
    scheduleSave(title, newContent);
    setAIOverlayVisible(false);
    setAIResult('');
  }, [aiResult, content, title, scheduleSave]);

  const handleAIDiscard = useCallback(() => {
    aiAbortRef.current?.abort();
    setAIOverlayVisible(false);
    setAIResult('');
    setAIStreaming(false);
    setAILoading(false);
  }, []);

  // ── Export ────────────────────────────────────────────────────────────────
  const handleExport = useCallback(async () => {
    if (Platform.OS === 'web') {
      const blob = new Blob([content], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${title || 'document'}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      return;
    }
    try {
      await Share.share({
        title: title || 'Document',
        message: `${title ? `# ${title}\n\n` : ''}${content}`,
      });
    } catch {
      // user cancelled
    }
  }, [title, content]);

  // ── Render ────────────────────────────────────────────────────────────────
  if (loadError) {
    return (
      <View style={[styles.errorWrap, { backgroundColor: colors.background }]}>
        <Feather name="alert-circle" size={32} color={colors.mutedForeground} />
        <Text style={[styles.errorText, { color: colors.mutedForeground }]}>
          Document not found
        </Text>
        <Pressable onPress={() => router.back()} style={styles.backBtn}>
          <Text style={{ color: colors.primary }}>Go back</Text>
        </Pressable>
      </View>
    );
  }

  if (!doc) {
    return (
      <View style={[styles.errorWrap, { backgroundColor: colors.background }]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View
        style={[
          styles.header,
          {
            paddingTop: insets.top + 8,
            borderBottomColor: colors.border,
            backgroundColor: colors.card,
          },
        ]}
      >
        <Pressable onPress={() => router.back()} hitSlop={8} style={styles.backPressable}>
          <Feather name="chevron-left" size={22} color={colors.primary} />
        </Pressable>

        {/* Title input */}
        <TextInput
          style={[styles.titleInput, { color: colors.foreground }]}
          value={title}
          onChangeText={handleTitleChange}
          placeholder="Untitled"
          placeholderTextColor={colors.mutedForeground}
          returnKeyType="done"
          blurOnSubmit
          maxLength={200}
        />

        {/* Work link chip */}
        <Pressable
          onPress={() => setWorkPickerOpen(true)}
          style={[
            styles.workChip,
            {
              backgroundColor: linkedWorkId ? `${colors.primary}18` : colors.muted,
              borderColor: linkedWorkId ? colors.primary : colors.border,
            },
          ]}
        >
          <Feather
            name="book-open"
            size={11}
            color={linkedWorkId ? colors.primary : colors.mutedForeground}
          />
          <Text
            style={[
              styles.workChipText,
              { color: linkedWorkId ? colors.primary : colors.mutedForeground },
            ]}
            numberOfLines={1}
          >
            {linkedWorkTitle ?? 'Link Work'}
          </Text>
        </Pressable>
      </View>

      {/* Word count + save status */}
      <View style={[styles.statusBar, { backgroundColor: colors.background, borderBottomColor: colors.border }]}>
        <Text style={[styles.statusText, { color: colors.mutedForeground }]}>
          {wordCount} {wordCount === 1 ? 'word' : 'words'}
        </Text>
        {lastSaved ? (
          <Text style={[styles.statusText, { color: colors.mutedForeground }]}>
            {relativeTime(lastSaved)}
          </Text>
        ) : null}
      </View>

      {/* Editor area */}
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={0}
      >
        <TextInput
          ref={inputRef}
          style={[styles.editor, { color: colors.foreground, backgroundColor: colors.background }]}
          value={content}
          onChangeText={handleContentChange}
          onSelectionChange={(e) => {
            selectionRef.current = e.nativeEvent.selection;
            setSelection(e.nativeEvent.selection);
          }}
          selection={selection}
          multiline
          textAlignVertical="top"
          placeholder="Start writing…"
          placeholderTextColor={colors.mutedForeground}
          autoCorrect
          autoCapitalize="sentences"
          scrollEnabled
          keyboardType="default"
          returnKeyType="default"
          blurOnSubmit={false}
        />

        {/* Formatting toolbar — anchored above keyboard */}
        <FormattingToolbar
          onFormat={handleFormat}
          onAIAssist={showAIActions}
          onExport={handleExport}
          aiLoading={aiLoading}
        />
      </KeyboardAvoidingView>

      {/* AI result overlay */}
      <AIResultOverlay
        visible={aiOverlayVisible}
        text={aiResult}
        streaming={aiStreaming}
        onAccept={handleAIAccept}
        onInsert={handleAIInsert}
        onDiscard={handleAIDiscard}
      />

      {/* Work picker */}
      <WorkPickerSheet
        visible={workPickerOpen}
        works={works}
        currentWorkId={linkedWorkId}
        onSelect={handleWorkLink}
        onClose={() => setWorkPickerOpen(false)}
      />
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1 },

  // Header
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingBottom: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 8,
  },
  backPressable: {
    width: 36,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  titleInput: {
    flex: 1,
    fontSize: 17,
    fontWeight: '600',
    padding: 0,
  },
  workChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    maxWidth: 120,
  },
  workChipText: {
    fontSize: 11,
    fontWeight: '500',
    maxWidth: 90,
  },

  // Status bar
  statusBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingVertical: 5,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  statusText: { fontSize: 11 },

  // Editor
  editor: {
    flex: 1,
    fontSize: 16,
    lineHeight: 26,
    padding: 16,
    fontFamily: Platform.OS === 'ios' ? undefined : 'Inter_400Regular',
  },

  // Formatting toolbar
  toolbar: {
    borderTopWidth: StyleSheet.hairlineWidth,
    flexShrink: 0,
  },
  toolbarContent: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 8,
    gap: 4,
  },
  tbBtn: {
    width: 36,
    height: 34,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: StyleSheet.hairlineWidth,
  },
  tbDivider: {
    width: 1,
    height: 22,
    marginHorizontal: 4,
  },
  tbAIBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 8,
    minWidth: 80,
    justifyContent: 'center',
  },
  tbAILabel: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '600',
  },

  // AI overlay
  overlayBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'flex-end',
  },
  overlayCard: {
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    maxHeight: '70%',
  },
  overlayHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  overlayTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  overlayTitle: {
    fontSize: 14,
    fontWeight: '600',
  },
  overlayScroll: {
    paddingHorizontal: 16,
    maxHeight: 280,
  },
  overlayText: {
    fontSize: 15,
    lineHeight: 24,
    fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
  },
  overlayActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    padding: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    flexWrap: 'wrap',
  },
  overlayBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 9,
    borderRadius: 10,
    flex: 1,
    justifyContent: 'center',
  },
  overlayBtnText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },
  overlayBtnSecondary: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 9,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    justifyContent: 'center',
  },
  overlayBtnSecondaryText: {
    fontSize: 13,
    fontWeight: '600',
  },
  overlayBtnGhost: {
    paddingHorizontal: 10,
    paddingVertical: 9,
    borderRadius: 10,
  },
  overlayBtnGhostText: {
    fontSize: 13,
  },

  // Work picker sheet
  sheetBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'flex-end',
  },
  sheet: {
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    paddingTop: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.12,
    shadowRadius: 14,
    elevation: 24,
  },
  sheetHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    alignSelf: 'center',
    marginBottom: 14,
    marginTop: 6,
  },
  sheetTitle: {
    fontSize: 17,
    fontWeight: '600',
    marginBottom: 12,
    paddingHorizontal: 4,
  },
  workItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 12,
    paddingHorizontal: 10,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    marginBottom: 8,
  },
  workItemText: {
    fontSize: 15,
    fontWeight: '500',
    flex: 1,
  },

  // Error state
  errorWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  errorText: { fontSize: 16 },
  backBtn: { paddingVertical: 8 },
});
