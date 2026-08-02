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
import { useGetDashboardSummary, useGetDashboardActivity, useGetBriefing } from '@workspace/api-client-react';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { Work, ActivityItem } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';

function StatCard({ label, value, icon }: { label: string; value: number | undefined; icon: string }) {
  const colors = useColors();
  return (
    <View style={[styles.statCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <Feather name={icon as any} size={18} color={colors.primary} />
      <Text style={[styles.statValue, { color: colors.foreground }]}>
        {value ?? '—'}
      </Text>
      <Text style={[styles.statLabel, { color: colors.mutedForeground }]}>{label}</Text>
    </View>
  );
}

function WorkRow({ work }: { work: Work }) {
  const colors = useColors();
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.push(`/work/${work.id}`)}
      style={({ pressed }) => [
        styles.workRow,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
      ]}
    >
      <View style={styles.workRowLeft}>
        <Text style={[styles.workTitle, { color: colors.foreground }]} numberOfLines={1}>
          {work.title ?? 'Untitled'}
        </Text>
        <Text style={[styles.workMeta, { color: colors.mutedForeground }]}>
          {work.work_type ?? 'research'} · {work.doc_count ?? 0} docs · {work.knowledge_count ?? 0} nodes
        </Text>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

function ActivityRow({ item }: { item: ActivityItem }) {
  const colors = useColors();
  const router = useRouter();
  const iconMap: Record<string, string> = {
    work: 'book-open',
    document: 'file-text',
    conversation: 'message-circle',
    knowledge: 'cpu',
  };
  const icon = iconMap[item.kind ?? ''] ?? 'activity';
  const when = item.created_at ? new Date(item.created_at).toLocaleDateString() : '';

  const handlePress = () => {
    if (item.kind === 'work' && item.id) router.push(`/work/${item.id}`);
    else if (item.kind === 'conversation' && item.id) router.push(`/chat/${item.id}`);
  };

  const tappable = item.kind === 'work' || item.kind === 'conversation';

  return (
    <Pressable
      onPress={tappable ? handlePress : undefined}
      style={({ pressed }) => [
        styles.activityRow,
        { borderColor: colors.border, opacity: pressed && tappable ? 0.6 : 1 },
      ]}
    >
      <View style={[styles.activityIcon, { backgroundColor: colors.muted }]}>
        <Feather name={icon as any} size={13} color={colors.primary} />
      </View>
      <Text style={[styles.activityLabel, { color: colors.foreground }]} numberOfLines={1}>
        {item.label ?? item.kind}
      </Text>
      <Text style={[styles.activityDate, { color: colors.mutedForeground }]}>{when}</Text>
      {tappable && <Feather name="chevron-right" size={13} color={colors.mutedForeground} />}
    </Pressable>
  );
}

function StudioCard() {
  const colors = useColors();
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.push('/studio')}
      style={({ pressed }) => [
        styles.studioCard,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.75 : 1 },
      ]}
    >
      <View style={[styles.studioIcon, { backgroundColor: colors.primary + '22' }]}>
        <Feather name="mic" size={18} color={colors.primary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={[styles.studioTitle, { color: colors.foreground }]}>Studio</Text>
        <Text style={[styles.studioSub, { color: colors.mutedForeground }]}>
          Text-to-speech & image generation
        </Text>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

export default function DashboardScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';

  const {
    data: summary,
    isLoading: summaryLoading,
    isError: summaryError,
    refetch: refetchSummary,
  } = useGetDashboardSummary({ query: { refetchInterval: 30_000, staleTime: 20_000 } } as any);

  const {
    data: activityData,
    isLoading: activityLoading,
    isError: activityError,
    refetch: refetchActivity,
  } = useGetDashboardActivity({ limit: 10 }, { query: { refetchInterval: 30_000, staleTime: 20_000 } } as any);

  const { data: briefing } = useGetBriefing({ query: { staleTime: 300_000 } } as any);

  const isLoading = summaryLoading || activityLoading;
  const isError = summaryError || activityError;
  const recentWorks = summary?.recent_works ?? [];
  const activity = activityData?.activity ?? [];
  const hasData = recentWorks.length > 0 || activity.length > 0;

  const topPad = isWeb ? 67 : insets.top;
  const botPad = isWeb ? 34 : 0;

  const handleRefresh = () => {
    refetchSummary();
    refetchActivity();
  };

  // Full-screen error when there's no cached data to show
  if (!isLoading && isError && !hasData) {
    return (
      <View style={[styles.container, { backgroundColor: colors.background, paddingTop: topPad + 16 }]}>
        <View style={styles.header}>
          <Text style={[styles.brand, { color: colors.foreground }]}>Orivellum</Text>
        </View>
        <ErrorScreen
          message="Can't reach your workspace"
          detail="Check your connection and make sure the Orivellum server is running."
          onRetry={handleRefresh}
        />
      </View>
    );
  }

  return (
    <FlatList
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={{
        paddingTop: topPad + 16,
        paddingBottom: botPad + 100,
        paddingHorizontal: 16,
      }}
      scrollEnabled
      keyboardShouldPersistTaps="handled"
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl
          refreshing={isLoading}
          onRefresh={handleRefresh}
          tintColor={colors.primary}
        />
      }
      data={[]}
      renderItem={null}
      ListHeaderComponent={
        <>
          {/* Header */}
          <View style={styles.header}>
            <Text style={[styles.brand, { color: colors.foreground }]}>Orivellum</Text>
            <Text style={[styles.subtitle, { color: colors.mutedForeground }]}>
              {briefing?.greeting ?? 'Your research workspace'}
            </Text>
          </View>

          {/* Studio quick action */}
          <StudioCard />

          {/* Offline banner — shown when we have cached data but server is unreachable */}
          {isError && hasData && (
            <OfflineBanner
              message="Can't reach your workspace — showing cached data"
              onRetry={handleRefresh}
            />
          )}

          {/* Stats */}
          {summaryLoading ? (
            <ActivityIndicator color={colors.primary} style={styles.loader} />
          ) : (
            <View style={styles.statsGrid}>
              <StatCard label="Works" value={summary?.work_count} icon="book-open" />
              <StatCard label="Docs" value={summary?.document_count} icon="file-text" />
              <StatCard label="Nodes" value={summary?.knowledge_count} icon="cpu" />
              <StatCard label="Chats" value={summary?.conversation_count} icon="message-circle" />
            </View>
          )}

          {/* Recent Works */}
          {recentWorks.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
                RECENT WORKS
              </Text>
              {recentWorks.map((w) => (
                <WorkRow key={w.id} work={w} />
              ))}
            </>
          )}

          {/* Activity */}
          {activity.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
                RECENT ACTIVITY
              </Text>
              {activity.map((item, i) => (
                <ActivityRow key={item.id ?? i} item={item} />
              ))}
            </>
          )}

          {!isLoading && !isError && recentWorks.length === 0 && activity.length === 0 && (
            <View style={styles.emptyState}>
              <Feather name="inbox" size={40} color={colors.mutedForeground} />
              <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
                No activity yet.{'\n'}Create a work to get started.
              </Text>
            </View>
          )}
        </>
      }
    />
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: { marginBottom: 24 },
  brand: { fontSize: 28, fontFamily: 'Inter_700Bold', letterSpacing: -0.5 },
  subtitle: { fontSize: 14, fontFamily: 'Inter_400Regular', marginTop: 2 },
  loader: { marginVertical: 24 },
  studioCard: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    gap: 12,
    marginBottom: 24,
  },
  studioIcon: {
    width: 40,
    height: 40,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  studioTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold', marginBottom: 2 },
  studioSub: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 28,
  },
  statCard: {
    flex: 1,
    minWidth: '44%',
    borderRadius: 6,
    borderWidth: 1,
    padding: 14,
    alignItems: 'flex-start',
    gap: 6,
  },
  statValue: { fontSize: 24, fontFamily: 'Inter_700Bold' },
  statLabel: { fontSize: 11, fontFamily: 'Inter_500Medium', textTransform: 'uppercase', letterSpacing: 0.5 },
  sectionLabel: {
    fontSize: 11,
    fontFamily: 'Inter_600SemiBold',
    letterSpacing: 1,
    textTransform: 'uppercase',
    marginBottom: 10,
    marginTop: 24,
  },
  workRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 6,
    borderWidth: 1,
    padding: 14,
    marginBottom: 8,
  },
  workRowLeft: { flex: 1, marginRight: 8 },
  workTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold', marginBottom: 3 },
  workMeta: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  activityRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderBottomWidth: 1,
    paddingVertical: 10,
    gap: 10,
  },
  activityIcon: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  activityLabel: { flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular' },
  activityDate: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  emptyState: {
    alignItems: 'center',
    paddingVertical: 60,
    gap: 12,
  },
  emptyText: {
    fontSize: 14,
    fontFamily: 'Inter_400Regular',
    textAlign: 'center',
    lineHeight: 22,
  },
});
