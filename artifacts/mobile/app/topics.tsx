/**
 * Topics browser — /topics
 *
 * Lists all semantic topic clusters discovered by nightshift clustering.
 * Tapping a topic card expands it to show all documents in that cluster,
 * each with a link to the full document detail screen.
 *
 * Refreshes on every screen focus so post-nightshift updates appear automatically.
 */
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { mobileFetch } from '@/lib/api';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API    = `https://${DOMAIN}/api`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface Topic {
  id: string;
  name: string;
  kind: string;
  doc_count: number;
  what_it_is: string | null;
  purpose: string | null;
  created_at: string;
}

interface TopicDoc {
  id: string;
  title: string | null;
  kind: string | null;
  readiness: string | null;
  work_id: string | null;
  word_count: number;
  created_at: string;
}

interface TopicDetail {
  topic: { id: string; name: string; kind: string };
  profile: {
    what_it_is: string;
    purpose: string;
    connected: string[];
    gaps: string[];
  } | null;
  documents: TopicDoc[];
  doc_count: number;
}

// ── Kind icons ─────────────────────────────────────────────────────────────────

const KIND_ICON: Record<string, string> = {
  pdf: 'file-text', docx: 'file-text', csv: 'table', excel: 'bar-chart-2',
  pptx: 'monitor', text: 'align-left', markdown: 'hash', code: 'code', image: 'image',
};

// ── Topic card (collapsed + expanded) ─────────────────────────────────────────

function TopicCard({
  topic, expanded, onToggle, colors, onDocPress,
}: {
  topic: Topic;
  expanded: boolean;
  onToggle: () => void;
  colors: ReturnType<typeof useColors>;
  onDocPress: (docId: string) => void;
}) {
  const { data: detail, isLoading: detailLoading } = useQuery<TopicDetail>({
    queryKey: ['topic', topic.id],
    queryFn: async () => {
      const r = await mobileFetch(`${API}/topics/${topic.id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    enabled: expanded,
    staleTime: 120_000,
  });

  return (
    <View
      style={[
        s.card,
        {
          backgroundColor: colors.card,
          borderColor: expanded ? colors.primary + '55' : colors.border,
        },
      ]}
    >
      {/* ── Collapsed header row ── */}
      <Pressable
        onPress={onToggle}
        style={({ pressed }) => [s.cardHeader, { opacity: pressed ? 0.72 : 1 }]}
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        accessibilityLabel={`${topic.name}, ${topic.doc_count} document${topic.doc_count !== 1 ? 's' : ''}`}
      >
        <View style={[s.topicIconWrap, { backgroundColor: colors.primary + '18' }]}>
          <Feather name="layers" size={15} color={colors.primary} />
        </View>

        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={[s.topicName, { color: colors.foreground }]} numberOfLines={1}>
            {topic.name}
          </Text>
          {topic.what_it_is && !expanded && (
            <Text style={[s.topicDesc, { color: colors.mutedForeground }]} numberOfLines={1}>
              {topic.what_it_is}
            </Text>
          )}
        </View>

        <View style={[s.countBadge, { backgroundColor: colors.muted }]}>
          <Text style={[s.countText, { color: colors.mutedForeground }]}>
            {topic.doc_count}
          </Text>
        </View>

        <Feather
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={16}
          color={colors.mutedForeground}
        />
      </Pressable>

      {/* ── Expanded content ── */}
      {expanded && (
        <View style={[s.expandedBody, { borderTopColor: colors.border }]}>
          {/* Profile description */}
          {topic.what_it_is ? (
            <Text style={[s.profileText, { color: colors.mutedForeground }]}>
              {topic.what_it_is}
            </Text>
          ) : null}
          {detail?.profile?.purpose ? (
            <Text style={[s.profilePurpose, { color: colors.mutedForeground }]}>
              {detail.profile.purpose}
            </Text>
          ) : null}

          {/* Gaps callout */}
          {detail?.profile?.gaps && detail.profile.gaps.length > 0 && (
            <View style={[s.gapsBox, { backgroundColor: '#92400e10', borderColor: '#f59e0b33' }]}>
              <Feather name="alert-triangle" size={11} color="#d97706" style={{ marginTop: 1 }} />
              <Text style={[s.gapsText, { color: '#92400e' }]}>
                {detail.profile.gaps.slice(0, 2).join(' · ')}
              </Text>
            </View>
          )}

          {/* Documents */}
          {detailLoading ? (
            <View style={{ paddingVertical: 16, alignItems: 'center' }}>
              <ActivityIndicator color={colors.primary} size="small" />
            </View>
          ) : !detail?.documents.length ? (
            <Text style={[s.emptyInner, { color: colors.mutedForeground }]}>
              No documents in this topic yet.
            </Text>
          ) : (
            <>
              <Text style={[s.sectionLabel, { color: colors.mutedForeground }]}>
                {detail.doc_count} DOCUMENT{detail.doc_count !== 1 ? 'S' : ''}
              </Text>

              {detail.documents.map((doc, idx) => {
                const title = doc.title || '(untitled)';
                const icon  = KIND_ICON[doc.kind ?? ''] ?? 'file';
                const isLast = idx === detail.documents.length - 1;
                return (
                  <Pressable
                    key={doc.id}
                    onPress={() => onDocPress(doc.id)}
                    style={({ pressed }) => [
                      s.docRow,
                      {
                        borderBottomWidth: isLast ? 0 : StyleSheet.hairlineWidth,
                        borderBottomColor: colors.border,
                        backgroundColor: pressed ? colors.muted + '60' : 'transparent',
                      },
                    ]}
                    accessibilityRole="link"
                    accessibilityLabel={`Open document: ${title}`}
                  >
                    <Feather
                      name={icon as any}
                      size={14}
                      color={colors.mutedForeground}
                      style={{ flexShrink: 0 }}
                    />
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={[s.docTitle, { color: colors.foreground }]} numberOfLines={1}>
                        {title}
                      </Text>
                      <View style={s.docMetaRow}>
                        {doc.kind && (
                          <Text style={[s.docMeta, { color: colors.mutedForeground }]}>
                            {doc.kind.toUpperCase()}
                          </Text>
                        )}
                        {doc.word_count > 0 && (
                          <Text style={[s.docMeta, { color: colors.mutedForeground }]}>
                            {doc.word_count.toLocaleString()} words
                          </Text>
                        )}
                        {doc.readiness === 'ready' && (
                          <View style={[s.readyDot, { backgroundColor: '#22c55e' }]} />
                        )}
                      </View>
                    </View>
                    <Feather name="chevron-right" size={13} color={colors.mutedForeground} />
                  </Pressable>
                );
              })}
            </>
          )}
        </View>
      )}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function TopicsScreen() {
  const colors      = useColors();
  const insets      = useSafeAreaInsets();
  const router      = useRouter();
  const qc          = useQueryClient();
  const [search,     setSearch]     = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading, isError, refetch, isRefetching } = useQuery<{
    topics: Topic[];
    total: number;
  }>({
    queryKey: ['topics'],
    queryFn:  async () => {
      const r = await mobileFetch(`${API}/topics`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    staleTime: 60_000,
  });

  // Invalidate on every focus so nightshift-generated topics appear automatically.
  useFocusEffect(
    useCallback(() => {
      qc.invalidateQueries({ queryKey: ['topics'] });
    }, [qc]),
  );

  const allTopics: Topic[] = data?.topics ?? [];
  const visible: Topic[]   = search.trim()
    ? allTopics.filter((t) => t.name.toLowerCase().includes(search.toLowerCase()))
    : allTopics;

  const toggleExpand = (id: string) =>
    setExpandedId((prev) => (prev === id ? null : id));

  const goToDoc = (docId: string) => router.push(`/library/${docId}` as any);

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <View style={[s.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View style={[s.header, {
        paddingTop: insets.top + 10,
        backgroundColor: colors.background,
        borderBottomColor: colors.border,
      }]}>
        <Pressable
          onPress={() => router.back()}
          hitSlop={10}
          style={s.backBtn}
          accessibilityRole="button"
          accessibilityLabel="Back"
        >
          <Feather name="arrow-left" size={21} color={colors.foreground} />
        </Pressable>

        <View style={{ flex: 1 }}>
          <Text style={[s.headerTitle, { color: colors.foreground }]}>Topic Graph</Text>
          <Text style={[s.headerSub, { color: colors.mutedForeground }]}>
            {isLoading ? '…' : `${data?.total ?? 0} semantic cluster${(data?.total ?? 0) !== 1 ? 's' : ''}`}
          </Text>
        </View>
      </View>

      {/* Search bar */}
      <View style={[s.searchRow, {
        backgroundColor: colors.background,
        borderBottomColor: colors.border,
      }]}>
        <Feather name="search" size={15} color={colors.mutedForeground} />
        <TextInput
          style={[s.searchInput, { color: colors.foreground, fontFamily: 'Inter_400Regular' }]}
          placeholder="Filter topics…"
          placeholderTextColor={colors.mutedForeground}
          value={search}
          onChangeText={setSearch}
          returnKeyType="search"
          clearButtonMode="while-editing"
        />
        {search.length > 0 && (
          <Pressable onPress={() => setSearch('')} hitSlop={8}>
            <Feather name="x" size={15} color={colors.mutedForeground} />
          </Pressable>
        )}
      </View>

      {/* Body */}
      {isLoading && !data ? (
        <View style={s.center}>
          <ActivityIndicator color={colors.primary} size="large" />
          <Text style={[s.emptyText, { color: colors.mutedForeground }]}>Loading topics…</Text>
        </View>

      ) : isError ? (
        <View style={s.center}>
          <Feather name="wifi-off" size={38} color={colors.mutedForeground} style={{ opacity: 0.45 }} />
          <Text style={[s.emptyTitle, { color: colors.foreground }]}>Can't reach the server</Text>
          <Text style={[s.emptyText, { color: colors.mutedForeground }]}>
            Make sure Orivellum is running and your device is on the same network.
          </Text>
          <Pressable
            onPress={() => refetch()}
            style={[s.retryBtn, { borderColor: colors.border, backgroundColor: colors.muted }]}
          >
            <Feather name="refresh-cw" size={14} color={colors.foreground} />
            <Text style={[s.retryText, { color: colors.foreground }]}>Retry</Text>
          </Pressable>
        </View>

      ) : visible.length === 0 ? (
        <View style={s.center}>
          <Feather name="layers" size={44} color={colors.mutedForeground} style={{ opacity: 0.38 }} />
          <Text style={[s.emptyTitle, { color: colors.foreground }]}>
            {search ? 'No matching topics' : 'No topics yet'}
          </Text>
          <Text style={[s.emptyText, { color: colors.mutedForeground }]}>
            {search
              ? 'Try a different search term'
              : 'Topics are built automatically during the nightly maintenance pass.'}
          </Text>
        </View>

      ) : (
        <FlatList
          data={visible}
          keyExtractor={(t) => t.id}
          renderItem={({ item }) => (
            <TopicCard
              topic={item}
              expanded={expandedId === item.id}
              onToggle={() => toggleExpand(item.id)}
              colors={colors}
              onDocPress={goToDoc}
            />
          )}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
          contentContainerStyle={{
            paddingHorizontal: 16,
            paddingTop: 12,
            paddingBottom: insets.bottom + 24,
          }}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={isRefetching && !isLoading}
              onRefresh={refetch}
              tintColor={colors.primary}
            />
          }
        />
      )}
    </View>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  container: { flex: 1 },

  // Header
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  backBtn:     { padding: 2 },
  headerTitle: { fontSize: 20, fontFamily: 'Inter_700Bold', letterSpacing: -0.3 },
  headerSub:   { fontSize: 12, fontFamily: 'Inter_400Regular', marginTop: 1 },

  // Search
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 8,
  },
  searchInput: { flex: 1, fontSize: 15, paddingVertical: 0 },

  // Topic card
  card: {
    borderRadius: 12,
    borderWidth: 1,
    overflow: 'hidden',
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    gap: 12,
  },
  topicIconWrap: {
    width: 34,
    height: 34,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  topicName:  { fontSize: 14, fontFamily: 'Inter_600SemiBold', marginBottom: 1 },
  topicDesc:  { fontSize: 11, fontFamily: 'Inter_400Regular', lineHeight: 15 },
  countBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    flexShrink: 0,
  },
  countText: { fontSize: 11, fontFamily: 'Inter_600SemiBold' },

  // Expanded body
  expandedBody: {
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 10,
    gap: 4,
  },
  profileText:    { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17, marginBottom: 2 },
  profilePurpose: { fontSize: 11, fontFamily: 'Inter_400Regular', lineHeight: 16, marginBottom: 6 },

  gapsBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    borderWidth: 1,
    borderRadius: 8,
    padding: 8,
    marginBottom: 8,
  },
  gapsText: { fontSize: 11, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 15 },

  sectionLabel: { fontSize: 10, fontFamily: 'Inter_500Medium', letterSpacing: 0.8, marginTop: 4, marginBottom: 6 },
  emptyInner:   { fontSize: 12, fontFamily: 'Inter_400Regular', textAlign: 'center', paddingVertical: 12 },

  // Doc rows inside expanded card
  docRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 9,
    gap: 10,
  },
  docTitle:   { fontSize: 13, fontFamily: 'Inter_500Medium', marginBottom: 2 },
  docMetaRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  docMeta:    { fontSize: 10, fontFamily: 'Inter_400Regular' },
  readyDot:   { width: 5, height: 5, borderRadius: 3 },

  // Empty / error states
  center:     { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 32 },
  emptyTitle: { fontSize: 17, fontFamily: 'Inter_600SemiBold', textAlign: 'center' },
  emptyText:  { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 20 },
  retryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginTop: 4,
  },
  retryText: { fontSize: 13, fontFamily: 'Inter_500Medium' },
});
