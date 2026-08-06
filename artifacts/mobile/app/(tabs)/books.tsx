import React from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import { font } from '@/lib/typography';

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

function stageProgress(status: string): number {
  const n = parseInt((status ?? '').replace('B', ''), 10);
  return isNaN(n) ? 0 : Math.round((n / 17) * 100);
}

function formatWords(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function ProgressBar({ pct, published }: { pct: number; published: boolean }) {
  const colors = useColors();
  return (
    <View style={[styles.progressTrack, { backgroundColor: colors.muted }]}>
      <View
        style={[
          styles.progressFill,
          {
            width: `${pct}%` as any,
            backgroundColor: published ? '#22c55e' : colors.primary,
          },
        ]}
      />
    </View>
  );
}

function BookCard({ book }: { book: BookEntry }) {
  const colors = useColors();
  const router = useRouter();
  const pct = stageProgress(book.pipeline_status);
  const isPublished = book.pipeline_status === 'B17';

  return (
    <Pressable
      onPress={() => router.push(`/works/${book.id}` as any)}
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: colors.card,
          borderColor: colors.border,
          opacity: pressed ? 0.85 : 1,
        },
      ]}
    >
      {/* Stage badge */}
      <View style={styles.cardHeader}>
        <Text style={[styles.cardTitle, { color: colors.foreground }]} numberOfLines={1}>
          {book.title}
        </Text>
        <View
          style={[
            styles.stageBadge,
            { backgroundColor: isPublished ? '#dcfce7' : `${colors.primary}18` },
          ]}
        >
          <Text
            style={[
              styles.stageBadgeText,
              { color: isPublished ? '#16a34a' : colors.primary },
            ]}
          >
            {book.pipeline_status}
          </Text>
        </View>
      </View>

      {book.description ? (
        <Text style={[styles.cardDesc, { color: colors.mutedForeground }]} numberOfLines={2}>
          {book.description}
        </Text>
      ) : null}

      {/* Stage label + progress */}
      <Text style={[styles.stageLabel, { color: colors.mutedForeground }]}>
        {book.stage_label}
      </Text>
      <ProgressBar pct={pct} published={isPublished} />

      {/* Stats row */}
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
        <View style={{ flex: 1 }} />
        <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
      </View>
    </Pressable>
  );
}

export default function BooksScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery<{ books: BookEntry[] }>({
    queryKey: ['mobile', 'books'],
    queryFn: () => mobileFetch('/api/books').then(r => r.json()),
    staleTime: 20_000,
    refetchInterval: 60_000,
  });

  const books = data?.books ?? [];
  const active = books.filter(b => b.pipeline_status !== 'B17');
  const published = books.filter(b => b.pipeline_status === 'B17');

  return (
    <ScrollView
      style={[styles.root, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 24 }]}
      refreshControl={
        <RefreshControl
          refreshing={isLoading}
          onRefresh={() => { refetch(); queryClient.invalidateQueries({ queryKey: ['mobile', 'books'] }); }}
          tintColor={colors.primary}
        />
      }
    >
      {/* Header */}
      <View style={styles.headerRow}>
        <Feather name="book" size={20} color={colors.primary} />
        <Text style={[styles.pageTitle, { color: colors.foreground }]}>Books</Text>
      </View>
      <Text style={[styles.pageSubtitle, { color: colors.mutedForeground }]}>
        Long-form projects through the production pipeline
      </Text>

      {isLoading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 48 }} />
      ) : isError ? (
        <View style={styles.emptyBox}>
          <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
            Could not load books
          </Text>
        </View>
      ) : books.length === 0 ? (
        <View style={styles.emptyBox}>
          <Feather name="book-open" size={36} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No books yet</Text>
          <Text style={[styles.emptyHint, { color: colors.mutedForeground }]}>
            Promote a Work to start the book pipeline
          </Text>
        </View>
      ) : (
        <>
          {active.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
                IN PROGRESS
              </Text>
              {active.map(b => <BookCard key={b.id} book={b} />)}
            </>
          )}
          {published.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: '#16a34a' }]}>PUBLISHED</Text>
              {published.map(b => <BookCard key={b.id} book={b} />)}
            </>
          )}
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 16 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  pageTitle: { fontSize: 26, fontFamily: 'Merriweather_700Bold' },
  pageSubtitle: { fontSize: 13, fontFamily: 'Inter_400Regular', marginBottom: 20 },
  sectionLabel: {
    fontSize: 10,
    fontFamily: 'Inter_600SemiBold',
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
  cardTitle: { flex: 1, fontSize: 15, fontFamily: 'Inter_600SemiBold' },
  stageBadge: {
    borderRadius: 6,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  stageBadgeText: { fontSize: 10, fontFamily: 'Inter_600SemiBold', letterSpacing: 0.5 },
  cardDesc: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  stageLabel: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  progressTrack: { height: 4, borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: '100%', borderRadius: 2 },
  statsRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 2 },
  statChip: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  statText: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, fontFamily: 'Inter_500Medium' },
  emptyHint: { fontSize: 12, fontFamily: 'Inter_400Regular', textAlign: 'center', maxWidth: 240 },
});
