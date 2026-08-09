import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import { font } from '@/lib/typography';
import { useVellumTokens, alpha } from '@/lib/tokens';
import { useSheetAnimation } from '@/lib/useSheetAnimation';
import { apiOrigin } from '@/lib/server';

const _DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const _API = () => `${_DOMAIN()}/api`;

// ─── Types ─────────────────────────────────────────────────────────────────────

interface BookEntry {
  id: string;
  title: string;
  description?: string;
  pipeline_status: string;
  stage_label: string;
  word_count: number;
  chapter_count: number;
  doc_count: number;
}

interface WorkEntry {
  id: string;
  title: string;
  work_type?: string;
  description?: string;
}

// ─── Stage colours ─────────────────────────────────────────────────────────────
// Mirrors the STAGE_COLOR map from the web books page, adapted to React Native.

interface StagePalette { bg: string; text: string }

// STAGE_PALETTE is built at runtime via buildStagePalette() so it uses tokens.
// It is populated once in BooksScreen and cached here for sub-components.
let _stagePaletteCache: Record<string, StagePalette> | null = null;

function buildStagePalette(T: ReturnType<typeof useVellumTokens>, primary: string): Record<string, StagePalette> {
  return {
    B0:  { bg: 'rgba(113,113,122,0.13)', text: '#71717a' },
    B1:  { bg: alpha(primary, 0.12),     text: primary },
    B2:  { bg: alpha(primary, 0.12),     text: primary },
    B3:  { bg: alpha(primary, 0.12),     text: primary },
    B4:  { bg: alpha(primary, 0.12),     text: primary },
    B5:  { bg: alpha(primary, 0.12),     text: primary },
    B6:  { bg: alpha(T.gilt, 0.12),      text: T.gilt },
    B7:  { bg: alpha(T.gilt, 0.12),      text: T.gilt },
    B8:  { bg: alpha(T.gilt, 0.12),      text: T.gilt },
    B9:  { bg: alpha(T.rust, 0.12),      text: T.rust },
    B10: { bg: alpha(T.rust, 0.12),      text: T.rust },
    B11: { bg: alpha(T.rust, 0.12),      text: T.rust },
    B12: { bg: alpha(T.green, 0.12),     text: T.green },
    B13: { bg: alpha(T.green, 0.12),     text: T.green },
    B14: { bg: alpha(T.green, 0.12),     text: T.green },
    B15: { bg: alpha(T.green, 0.12),     text: T.green },
    B16: { bg: alpha(T.green, 0.12),     text: T.green },
    B17: { bg: alpha(T.green, 0.15),     text: T.green },
  };
}

function stagePalette(status: string): StagePalette {
  return (_stagePaletteCache ?? {})[status] ?? { bg: 'rgba(113,113,122,0.13)', text: '#71717a' };
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function stageProgress(status: string): number {
  const n = parseInt((status ?? '').replace('B', ''), 10);
  return isNaN(n) ? 0 : Math.round((n / 17) * 100);
}

function formatWords(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

// ─── ProgressBar ───────────────────────────────────────────────────────────────

function ProgressBar({ pct, published }: { pct: number; published: boolean }) {
  const colors = useColors();
  const T = useVellumTokens();
  return (
    <View style={[styles.progressTrack, { backgroundColor: colors.muted }]}>
      <View
        style={[
          styles.progressFill,
          { width: `${pct}%` as any, backgroundColor: published ? T.green : colors.primary },
        ]}
      />
    </View>
  );
}

// ─── BookCard ──────────────────────────────────────────────────────────────────

function BookCard({ book }: { book: BookEntry }) {
  const colors = useColors();
  const T = useVellumTokens();
  const router = useRouter();
  const pct = stageProgress(book.pipeline_status);
  const isPublished = book.pipeline_status === 'B17';
  const pal = stagePalette(book.pipeline_status);

  return (
    <Pressable
      onPress={() => router.push(`/works/${book.id}` as any)}
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.85 : 1 },
      ]}
    >
      {/* Header: title + stage badge */}
      <View style={styles.cardHeader}>
        <Text style={[styles.cardTitle, { color: colors.foreground }]} numberOfLines={1}>
          {book.title}
        </Text>
        {isPublished && <Feather name="star" size={13} color={T.green} />}
        <View style={[styles.stageBadge, { backgroundColor: pal.bg }]}>
          <Text style={[styles.stageBadgeText, { color: pal.text }]}>
            {book.pipeline_status}
          </Text>
        </View>
      </View>

      {book.description ? (
        <Text style={[styles.cardDesc, { color: colors.mutedForeground }]} numberOfLines={2}>
          {book.description}
        </Text>
      ) : null}

      {/* Stage label */}
      <Text style={[styles.stageLabel, { color: pal.text }]}>
        {book.stage_label}
      </Text>

      {/* Progress */}
      <ProgressBar pct={pct} published={isPublished} />

      {/* Stats row: words · chapters · docs */}
      <View style={styles.statsRow}>
        {book.word_count > 0 && (
          <View style={styles.statChip}>
            <Feather name="file-text" size={11} color={colors.mutedForeground} />
            <Text style={[styles.statText, { color: colors.mutedForeground }]}>
              {formatWords(book.word_count)}w
            </Text>
          </View>
        )}
        {book.chapter_count > 0 && (
          <View style={styles.statChip}>
            <Text style={[styles.statText, { color: colors.mutedForeground }]}>
              {book.chapter_count} ch
            </Text>
          </View>
        )}
        {book.doc_count > 0 && (
          <View style={styles.statChip}>
            <Feather name="paperclip" size={11} color={colors.mutedForeground} />
            <Text style={[styles.statText, { color: colors.mutedForeground }]}>
              {book.doc_count} doc{book.doc_count !== 1 ? 's' : ''}
            </Text>
          </View>
        )}
        <View style={{ flex: 1 }} />
        <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
      </View>
    </Pressable>
  );
}

// ─── OtherWorksSection ─────────────────────────────────────────────────────────

function OtherWorksSection({
  bookWorkIds,
  onPromoted,
}: {
  bookWorkIds: Set<string>;
  onPromoted: () => void;
}) {
  const colors = useColors();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [promoting, setPromoting] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery<{ works: WorkEntry[] }>({
    queryKey: ['mobile', 'works-all'],
    queryFn: () => mobileFetch(`${_API()}/works`).then(r => r.json()),
    staleTime: 30_000,
  });

  const eligible = (data?.works ?? []).filter(w => w.id && !bookWorkIds.has(w.id));
  if (isLoading || eligible.length === 0) return null;

  const handlePromote = async (work: WorkEntry) => {
    if (promoting.has(work.id)) return;
    setPromoting(prev => new Set([...prev, work.id]));
    try {
      const r = await mobileFetch(`${_API()}/works/${work.id}/pipeline`, { method: 'POST' });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        Alert.alert('Could not start pipeline', (body as any).detail ?? 'Please try again.');
        return;
      }
      onPromoted();
      router.push({ pathname: '/works/[id]', params: { id: work.id, tab: 'book' } } as any);
    } catch (e: any) {
      Alert.alert('Error', e?.message ?? 'Could not start pipeline');
    } finally {
      setPromoting(prev => { const s = new Set(prev); s.delete(work.id); return s; });
    }
  };

  return (
    <View style={{ marginTop: 20 }}>
      {/* Section header — tappable to collapse */}
      <Pressable
        onPress={() => setCollapsed(c => !c)}
        style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: collapsed ? 0 : 10 }}
      >
        <Text style={[styles.sectionLabel, { color: colors.mutedForeground, marginBottom: 0, marginTop: 0 }]}>
          OTHER WORKS ({eligible.length})
        </Text>
        <Feather name={collapsed ? 'chevron-down' : 'chevron-up'} size={12} color={colors.mutedForeground} />
      </Pressable>

      {!collapsed && eligible.map(work => (
        <View
          key={work.id}
          style={[styles.otherCard, { backgroundColor: colors.card, borderColor: colors.border }]}
        >
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={[styles.otherTitle, { color: colors.foreground }]} numberOfLines={1}>
              {work.title}
            </Text>
            {work.work_type && (
              <Text style={[styles.otherType, { color: colors.mutedForeground }]}>
                {work.work_type.toUpperCase()}
              </Text>
            )}
          </View>
          <Pressable
            onPress={() => handlePromote(work)}
            disabled={promoting.has(work.id)}
            style={({ pressed }) => [
              styles.promoteBtn,
              {
                borderColor: promoting.has(work.id) ? colors.border : colors.primary,
                backgroundColor: promoting.has(work.id)
                  ? 'transparent'
                  : pressed ? `${colors.primary}18` : 'transparent',
              },
            ]}
          >
            {promoting.has(work.id)
              ? <ActivityIndicator size="small" color={colors.primary} />
              : <>
                  <Feather name="plus" size={11} color={colors.primary} />
                  <Text style={[styles.promoteBtnText, { color: colors.primary }]}>Start Book</Text>
                </>}
          </Pressable>
        </View>
      ))}
    </View>
  );
}

// ─── CreateWorkModal ────────────────────────────────────────────────────────────

function CreateWorkModal({ visible, onClose, onCreated }: {
  visible: boolean;
  onClose: () => void;
  onCreated: (workId: string) => void;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  const { rendered, slideAnim, fadeAnim, panHandlers } = useSheetAnimation(visible, 420, onClose);

  const handleCreate = async () => {
    if (!title.trim()) {
      Alert.alert('Title required', 'Give your book a title to get started.');
      return;
    }
    setSaving(true);
    try {
      const r = await mobileFetch(`${_API()}/works`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title.trim(), work_type: 'writing', description: description.trim() || undefined }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        Alert.alert('Could not create work', (body as any).detail ?? 'Please try again.');
        return;
      }
      const data = await r.json();
      const workId: string = data.work?.id ?? data.id;
      setTitle('');
      setDescription('');
      onCreated(workId);
    } catch (e: any) {
      Alert.alert('Error', e?.message ?? 'Create failed');
    } finally {
      setSaving(false);
    }
  };

  if (!rendered) return null;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose}>
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.4)', opacity: fadeAnim }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>
      <Animated.View
        {...panHandlers}
        style={{
          position: 'absolute', bottom: 0, left: 0, right: 0,
          backgroundColor: colors.card,
          borderTopLeftRadius: 20,
          borderTopRightRadius: 20,
          borderTopWidth: StyleSheet.hairlineWidth,
          borderColor: colors.border,
          paddingHorizontal: 20,
          paddingTop: 20,
          paddingBottom: insets.bottom + 28,
          gap: 14,
          transform: [{ translateY: slideAnim }],
        }}>
          {/* Handle + header */}
          <View style={{ alignItems: 'center', marginBottom: 2 }}>
            <View style={{ width: 36, height: 4, borderRadius: 2, backgroundColor: colors.border }} />
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Feather name="book" size={16} color={colors.primary} style={{ marginRight: 8 }} />
            <Text style={{ fontSize: 16, ...font('bold'), color: colors.foreground, flex: 1 }}>
              New Book
            </Text>
            <Pressable onPress={onClose} hitSlop={8}>
              <Feather name="x" size={18} color={colors.mutedForeground} />
            </Pressable>
          </View>

          {/* Type badge — locked to Writing */}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <Text style={{ fontSize: 11, ...font('regular'), color: colors.mutedForeground }}>Type:</Text>
            <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, backgroundColor: `${colors.primary}18` }}>
              <Text style={{ fontSize: 11, ...font('semibold'), color: colors.primary }}>Writing</Text>
            </View>
          </View>

          {/* Title */}
          <TextInput
            style={[styles.input, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.background }]}
            placeholder="Book title"
            placeholderTextColor={colors.mutedForeground}
            value={title}
            onChangeText={setTitle}
            returnKeyType="next"
            autoFocus
          />

          {/* Description */}
          <TextInput
            style={[styles.input, styles.inputMulti, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.background }]}
            placeholder="Short description (optional)"
            placeholderTextColor={colors.mutedForeground}
            value={description}
            onChangeText={setDescription}
            multiline
            returnKeyType="done"
          />

          {/* Create button */}
          <Pressable
            onPress={handleCreate}
            disabled={saving || !title.trim()}
            style={({ pressed }) => ({
              flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
              paddingVertical: 14, borderRadius: 12,
              backgroundColor: !title.trim() || saving ? colors.muted : pressed ? `${colors.primary}cc` : colors.primary,
            })}
          >
            {saving
              ? <ActivityIndicator size="small" color="#fff" />
              : <Feather name="plus" size={16} color="#fff" />}
            <Text style={{ fontSize: 14, ...font('bold'), color: '#fff' }}>
              {saving ? 'Creating…' : 'Create Book Work'}
            </Text>
          </Pressable>
      </Animated.View>
    </Modal>
  );
}

// ─── BooksScreen ───────────────────────────────────────────────────────────────

export default function BooksScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);

  // Build (and cache) the stage palette with runtime tokens
  _stagePaletteCache = buildStagePalette(T, colors.primary);

  const { data, isLoading, isError, refetch } = useQuery<{ books: BookEntry[] }>({
    queryKey: ['mobile', 'books'],
    queryFn: () => mobileFetch(`${_API()}/books`).then(r => r.json()),
    staleTime: 20_000,
    refetchInterval: 60_000,
  });

  const books = data?.books ?? [];
  const active = books.filter(b => b.pipeline_status !== 'B17');
  const published = books.filter(b => b.pipeline_status === 'B17');
  const bookWorkIds = new Set(books.map(b => b.id));

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['mobile', 'books'] });
    queryClient.invalidateQueries({ queryKey: ['mobile', 'works-all'] });
    refetch();
  }, [queryClient, refetch]);

  const handleCreated = useCallback((workId: string) => {
    setCreateOpen(false);
    invalidate();
    router.push({ pathname: '/works/[id]', params: { id: workId } } as any);
  }, [invalidate, router]);

  return (
    <>
      <ScrollView
        style={[styles.root, { backgroundColor: colors.background }]}
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 24 }]}
        refreshControl={
          <RefreshControl
            refreshing={isLoading}
            onRefresh={invalidate}
            tintColor={colors.primary}
          />
        }
        keyboardShouldPersistTaps="handled"
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <View style={styles.headerRow}>
          <Feather name="book" size={20} color={colors.primary} />
          <Text style={[styles.pageTitle, { color: colors.foreground }]}>Books</Text>
          <View style={{ flex: 1 }} />
          {/* New Book FAB */}
          <Pressable
            onPress={() => setCreateOpen(true)}
            style={({ pressed }) => ({
              flexDirection: 'row', alignItems: 'center', gap: 5,
              paddingHorizontal: 12, paddingVertical: 7, borderRadius: 20,
              backgroundColor: pressed ? `${colors.primary}cc` : colors.primary,
            })}
          >
            <Feather name="plus" size={14} color="#fff" />
            <Text style={{ fontSize: 12, ...font('semibold'), color: '#fff' }}>New Book</Text>
          </Pressable>
        </View>
        <Text style={[styles.pageSubtitle, { color: colors.mutedForeground }]}>
          Long-form projects through the 17-stage pipeline
        </Text>

        {/* ── Content ────────────────────────────────────────────────────── */}
        {isLoading ? (
          <ActivityIndicator color={colors.primary} style={{ marginTop: 48 }} />
        ) : isError ? (
          <View style={styles.emptyBox}>
            <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
            <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load books</Text>
          </View>
        ) : books.length === 0 ? (
          <View style={styles.emptyBox}>
            <Feather name="book-open" size={36} color={colors.mutedForeground} />
            <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No books yet</Text>
            <Text style={[styles.emptyHint, { color: colors.mutedForeground }]}>
              Create a new book work or promote an existing one to start the pipeline.
            </Text>
            <Pressable
              onPress={() => setCreateOpen(true)}
              style={({ pressed }) => ({
                flexDirection: 'row', alignItems: 'center', gap: 6,
                paddingHorizontal: 16, paddingVertical: 10, borderRadius: 10, marginTop: 4,
                backgroundColor: pressed ? `${colors.primary}cc` : colors.primary,
              })}
            >
              <Feather name="plus" size={15} color="#fff" />
              <Text style={{ fontSize: 14, ...font('semibold'), color: '#fff' }}>New Book</Text>
            </Pressable>
          </View>
        ) : (
          <>
            {active.length > 0 && (
              <>
                <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>IN PROGRESS</Text>
                {active.map(b => <BookCard key={b.id} book={b} />)}
              </>
            )}
            {published.length > 0 && (
              <>
                <Text style={[styles.sectionLabel, { color: T.green }]}>PUBLISHED</Text>
                {published.map(b => <BookCard key={b.id} book={b} />)}
              </>
            )}
          </>
        )}

        {/* ── Other Works (not yet promoted) ─────────────────────────────── */}
        {!isLoading && !isError && (
          <OtherWorksSection bookWorkIds={bookWorkIds} onPromoted={invalidate} />
        )}
      </ScrollView>

      {/* Create Work bottom-sheet */}
      <CreateWorkModal
        visible={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
      />
    </>
  );
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 16 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  pageTitle: { fontSize: 26, fontFamily: 'Merriweather_700Bold' },
  pageSubtitle: { fontSize: 13, ...font('regular'), marginBottom: 20 },
  sectionLabel: {
    fontSize: 10,
    ...font('semibold'),
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    marginBottom: 8,
    marginTop: 8,
  },
  card: {
    borderRadius: 12,
    borderWidth: 1,
    padding: 14,
    marginBottom: 10,
    gap: 8,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  cardTitle: { flex: 1, fontSize: 15, ...font('semibold') },
  stageBadge: { borderRadius: 6, paddingHorizontal: 7, paddingVertical: 2 },
  stageBadgeText: { fontSize: 10, ...font('semibold'), letterSpacing: 0.5 },
  cardDesc: { fontSize: 12, ...font('regular'), lineHeight: 17 },
  stageLabel: { fontSize: 11, ...font('medium') },
  progressTrack: { height: 4, borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: '100%', borderRadius: 2 },
  statsRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 2 },
  statChip: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  statText: { fontSize: 11, ...font('regular') },
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, ...font('medium') },
  emptyHint: {
    fontSize: 12, ...font('regular'),
    textAlign: 'center', maxWidth: 260, lineHeight: 18,
  },
  // Other Works section
  otherCard: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    borderRadius: 10, borderWidth: 1,
    padding: 12, marginBottom: 8,
  },
  otherTitle: { fontSize: 13, ...font('medium') },
  otherType: { fontSize: 10, ...font('semibold'), letterSpacing: 0.6, marginTop: 2 },
  promoteBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 8, borderWidth: 1,
    minWidth: 80, justifyContent: 'center',
  },
  promoteBtnText: { fontSize: 11, ...font('semibold') },
  // Create Work modal
  input: {
    borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 11,
    fontSize: 14, ...font('regular'),
  },
  inputMulti: { minHeight: 72, textAlignVertical: 'top' },
});
