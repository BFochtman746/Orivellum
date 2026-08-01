import React from 'react';
import {
  ActivityIndicator,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useListWorks } from '@workspace/api-client-react';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { Work } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';

const TYPE_ICONS: Record<string, string> = {
  research: 'book-open',
  project: 'briefcase',
  review: 'eye',
  analysis: 'bar-chart-2',
  writing: 'edit-3',
};

function WorkCard({ work }: { work: Work }) {
  const colors = useColors();
  const router = useRouter();
  const icon = TYPE_ICONS[work.work_type ?? ''] ?? 'file';

  const statusColor =
    work.status === 'active'
      ? colors.primary
      : work.status === 'complete'
      ? '#4A8C65'
      : colors.mutedForeground;

  return (
    <Pressable
      onPress={() => router.push(`/work/${work.id}`)}
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
      </View>
    </Pressable>
  );
}

export default function WorksScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';

  const { data, isLoading, isError, refetch } = useListWorks({ query: { refetchInterval: 30_000, staleTime: 20_000 } } as any);
  const works = data?.works ?? [];
  const hasData = works.length > 0;

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
          {works.length} total
        </Text>
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
          renderItem={({ item }) => <WorkCard work={item} />}
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
});
