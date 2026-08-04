import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Swipeable } from 'react-native-gesture-handler';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useListWorks, useCreateConversation, useDeleteWork } from '@workspace/api-client-react';
import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import type { Work } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';

const TEAL = '#14b8a6';

const TYPE_ICONS: Record<string, string> = {
  research: 'book-open',
  project: 'briefcase',
  review: 'eye',
  analysis: 'bar-chart-2',
  writing: 'edit-3',
};

function WorkCard({ work, onStartChat, onDelete }: { work: Work; onStartChat: () => void; onDelete?: (id: string) => void }) {
  const colors = useColors();
  const router = useRouter();
  const icon = TYPE_ICONS[work.work_type ?? ''] ?? 'file';

  const statusColor =
    work.status === 'active'
      ? colors.primary
      : work.status === 'complete'
      ? '#4A8C65'
      : work.status === 'archived'
      ? '#6b7280'
      : colors.mutedForeground;

  const swipeRef = useRef<Swipeable>(null);
  const isWeb = Platform.OS === 'web';

  const triggerHaptic = () => {
    if (!isWeb) Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
  };

  // Left-swipe reveals the teal "Chat" action (rendered on the right edge)
  const renderRightActions = () => (
    <View style={{ flexDirection: 'row', alignItems: 'center', paddingRight: 12, paddingLeft: 8, marginVertical: 6 }}>
      <Pressable
        onPress={() => { swipeRef.current?.close(); triggerHaptic(); onStartChat(); }}
        style={{
          backgroundColor: TEAL, borderRadius: 10,
          paddingHorizontal: 18, paddingVertical: 10,
          alignItems: 'center', justifyContent: 'center', gap: 3,
        }}
        hitSlop={4}
      >
        <Feather name="message-circle" size={16} color="#fff" />
        <Text style={{ color: '#fff', fontSize: 10, fontFamily: 'Inter_600SemiBold' }}>Chat</Text>
      </Pressable>
    </View>
  );

  // Right-swipe reveals the "Open" action (rendered on the left edge)
  const renderLeftActions = () => (
    <View style={{ flexDirection: 'row', alignItems: 'center', paddingLeft: 12, paddingRight: 8, marginVertical: 6 }}>
      <Pressable
        onPress={() => { swipeRef.current?.close(); triggerHaptic(); router.push(`/work/${work.id}`); }}
        style={{
          backgroundColor: colors.muted, borderRadius: 10,
          paddingHorizontal: 18, paddingVertical: 10,
          alignItems: 'center', justifyContent: 'center', gap: 3,
        }}
        hitSlop={4}
      >
        <Feather name="arrow-right" size={16} color={colors.foreground} />
        <Text style={{ color: colors.foreground, fontSize: 10, fontFamily: 'Inter_600SemiBold' }}>Open</Text>
      </Pressable>
    </View>
  );

  const handleLongPress = () => {
    triggerHaptic();
    Alert.alert(work.title ?? 'Work', '', [
      { text: 'Rename (open detail)', onPress: () => router.push(`/work/${work.id}`) },
      { text: 'Delete', style: 'destructive', onPress: () => {
        Alert.alert('Delete Work', `Delete "${work.title}"? This cannot be undone.`, [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Delete', style: 'destructive', onPress: () => onDelete?.(work.id ?? '') },
        ]);
      }},
      { text: 'Cancel', style: 'cancel' },
    ]);
  };

  const cardInner = (
    <Pressable
      onPress={() => router.push(`/work/${work.id}`)}
      onLongPress={handleLongPress}
      delayLongPress={450}
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: colors.card,
          borderColor: colors.border,
          opacity: pressed ? 0.75 : 1,
        },
      ]}
    >
      <View style={styles.cardTop}>
        <View style={[styles.iconWrap, { backgroundColor: colors.muted }]}>
          <Feather name={icon as any} size={18} color={colors.primary} />
        </View>
        <View style={styles.cardMeta}>
          <Text style={[styles.cardTitle, { color: colors.foreground }]} numberOfLines={2}>
            {work.title ?? 'Untitled'}
          </Text>
          {work.description ? (
            <Text style={[styles.cardDesc, { color: colors.mutedForeground }]} numberOfLines={2}>
              {work.description}
            </Text>
          ) : null}
        </View>
        <Feather name="chevron-right" size={16} color={colors.mutedForeground} style={{ marginTop: 2 }} />
      </View>

      {/* Footer stats */}
      <View style={[styles.cardFooter, { borderTopColor: colors.border }]}>
        <View style={styles.statChip}>
          <Feather name="file-text" size={11} color={colors.mutedForeground} />
          <Text style={[styles.chipText, { color: colors.mutedForeground }]}>
            {work.doc_count ?? 0} docs
          </Text>
        </View>
        <View style={styles.statChip}>
          <Feather name="cpu" size={11} color={colors.mutedForeground} />
          <Text style={[styles.chipText, { color: colors.mutedForeground }]}>
            {work.knowledge_count ?? 0} nodes
          </Text>
        </View>
        {(work.pending_tasks ?? 0) > 0 && (
          <View style={styles.statChip}>
            <Feather name="check-square" size={11} color={colors.mutedForeground} />
            <Text style={[styles.chipText, { color: colors.mutedForeground }]}>
              {work.pending_tasks} tasks
            </Text>
          </View>
        )}
        {((work as any).conv_count ?? 0) > 0 && (
          <View style={styles.statChip}>
            <Feather name="message-circle" size={11} color={colors.mutedForeground} />
            <Text style={[styles.chipText, { color: colors.mutedForeground }]}>
              {(work as any).conv_count} chats
            </Text>
          </View>
        )}
        <View style={[styles.statusBadge, { backgroundColor: statusColor + '22' }]}>
          <Text style={[styles.statusText, { color: statusColor }]}>
            {work.status ?? 'active'}
          </Text>
        </View>
        {(work as any).doc_count > 0 && (() => {
          const errs  = (work as any).error_doc_count   ?? 0;
          const proc  = (work as any).processing_doc_count ?? 0;
          const ready = (work as any).ready_doc_count   ?? 0;
          const total = (work as any).doc_count         ?? 0;
          if (errs > 0) {
            return (
              <View style={[styles.statusBadge, { backgroundColor: '#fee2e222' }]}>
                <Text style={[styles.statusText, { color: '#b91c1c' }]}>
                  {errs} error{errs !== 1 ? 's' : ''}
                </Text>
              </View>
            );
          }
          if (proc > 0) {
            return (
              <View style={[styles.statusBadge, { backgroundColor: '#fef3c722' }]}>
                <Text style={[styles.statusText, { color: '#92400e' }]}>Processing</Text>
              </View>
            );
          }
          if (ready === total) {
            return (
              <View style={[styles.statusBadge, { backgroundColor: '#d1fae522' }]}>
                <Text style={[styles.statusText, { color: '#065f46' }]}>Ready</Text>
              </View>
            );
          }
          return null;
        })()}
        <Pressable
          onPress={(e) => { e.stopPropagation(); onStartChat(); }}
          style={[styles.chatBtn, { backgroundColor: colors.primary + '18', borderColor: colors.primary + '44' }]}
          hitSlop={6}
        >
          <Feather name="message-circle" size={11} color={colors.primary} />
          <Text style={[styles.chatBtnText, { color: colors.primary }]}>Chat</Text>
        </Pressable>
      </View>
    </Pressable>
  );

  // No-op the Swipeable wrapper on web — swipe gestures are native-only here
  if (isWeb) return cardInner;

  return (
    <Swipeable
      ref={swipeRef}
      renderRightActions={renderRightActions}
      renderLeftActions={renderLeftActions}
      overshootRight={false}
      overshootLeft={false}
      friction={2}
    >
      {cardInner}
    </Swipeable>
  );
}

export default function WorksScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const router = useRouter();

  const createConv = useCreateConversation();
  const { mutate: deleteMutate } = useDeleteWork();
  const queryClient = useQueryClient();

  const handleStartChat = (workId: string, workTitle: string) => {
    createConv.mutate(
      { data: { title: `Discussion: ${workTitle}`, work_id: workId } as any },
      {
        onSuccess: (res: any) => {
          const id = res?.conversation?.id;
          if (id) {
            // Navigate to web route — works inside Expo web; on native opens the web app
            if (Platform.OS === 'web') {
              (router as any).push(`/chat?id=${id}`);
            } else {
              router.push(`/chat/${id}` as any);
            }
          }
        },
      }
    );
  };

  const [search, setSearch] = useState('');
  const { data, isLoading, isError, refetch } = useListWorks({ query: { refetchInterval: 30_000, staleTime: 20_000 } } as any);
  const allWorks = data?.works ?? [];
  const works = useMemo(() => {
    if (!search.trim()) return allWorks;
    const q = search.toLowerCase();
    return allWorks.filter((w: any) =>
      (w.title ?? '').toLowerCase().includes(q) ||
      (w.description ?? '').toLowerCase().includes(q) ||
      (w.work_type ?? '').toLowerCase().includes(q)
    );
  }, [allWorks, search]);
  const hasData = allWorks.length > 0;

  const handleDeleteWork = useCallback((workId: string) => {
    deleteMutate({ workId }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['getListWorks'] });
        refetch();
      },
      onError: () => Alert.alert('Error', 'Could not delete work'),
    });
  }, [deleteMutate, queryClient, refetch]);

  const topPad = isWeb ? 67 : insets.top;

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View
        style={[
          styles.header,
          {
            paddingTop: topPad + 12,
            borderBottomColor: colors.border,
            backgroundColor: colors.background,
          },
        ]}
      >
        <Text style={[styles.title, { color: colors.foreground }]}>Works</Text>
        <Text style={[styles.count, { color: colors.mutedForeground }]}>
          {works.length}{search.trim() ? ` / ${allWorks.length}` : ''} total
        </Text>
      </View>

      {/* Search bar */}
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border, backgroundColor: colors.background, gap: 8 }}>
        <Feather name="search" size={15} color={colors.mutedForeground} />
        <TextInput
          style={{ flex: 1, fontSize: 14, fontFamily: 'Inter_400Regular', color: colors.foreground }}
          placeholder="Search works…"
          placeholderTextColor={colors.mutedForeground}
          value={search}
          onChangeText={setSearch}
          returnKeyType="search"
        />
        {search.length > 0 && (
          <Pressable onPress={() => setSearch('')} hitSlop={8}>
            <Feather name="x" size={15} color={colors.mutedForeground} />
          </Pressable>
        )}
      </View>

      {/* Offline banner — shown only when we have cached data */}
      {isError && hasData && (
        <OfflineBanner
          message="Showing cached works — server unreachable"
          onRetry={refetch}
        />
      )}

      {/* List */}
      {isLoading && !hasData ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : isError && !hasData ? (
        <ErrorScreen
          message="Can't reach the server"
          detail="Make sure Orivellum is running on your local machine and your device is on the same network."
          onRetry={refetch}
        />
      ) : works.length === 0 ? (
        <View style={styles.centered}>
          <Feather name="book-open" size={44} color={colors.mutedForeground} />
          <Text style={[styles.emptyTitle, { color: colors.foreground }]}>No works yet</Text>
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
            Works you create in the web app will appear here
          </Text>
        </View>
      ) : (
        <FlatList
          data={works}
          keyExtractor={(item) => item.id ?? ''}
          renderItem={({ item }) => (
            <WorkCard
              work={item}
              onStartChat={() => handleStartChat(item.id ?? '', item.title ?? '')}
              onDelete={handleDeleteWork}
            />
          )}
          refreshControl={
            <RefreshControl refreshing={isLoading} onRefresh={refetch} tintColor={colors.primary} />
          }
          contentContainerStyle={{
            paddingHorizontal: 16,
            paddingTop: 12,
            paddingBottom: isWeb ? 34 + 50 : insets.bottom + 100,
          }}
          showsVerticalScrollIndicator={false}
          ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 14,
    borderBottomWidth: 1,
  },
  title: { fontSize: 26, fontFamily: 'Inter_700Bold' },
  count: { fontSize: 13, fontFamily: 'Inter_400Regular' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, paddingHorizontal: 32 },
  emptyTitle: { fontSize: 17, fontFamily: 'Inter_600SemiBold' },
  emptyText: { fontSize: 14, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 20 },
  card: {
    borderRadius: 8,
    borderWidth: 1,
    overflow: 'hidden',
  },
  cardTop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    padding: 14,
    gap: 12,
  },
  iconWrap: {
    width: 40,
    height: 40,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardMeta: { flex: 1 },
  cardTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold', lineHeight: 20, marginBottom: 3 },
  cardDesc: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  cardFooter: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderTopWidth: 1,
  },
  statChip: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  chipText: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  statusBadge: {
    marginLeft: 'auto',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
  },
  statusText: { fontSize: 11, fontFamily: 'Inter_500Medium', textTransform: 'capitalize' },
  chatBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 5,
    borderWidth: 1,
    marginLeft: 'auto',
  },
  chatBtnText: { fontSize: 11, fontFamily: 'Inter_600SemiBold' },
});
