import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import {
  useGetWork,
  useGetWorkDocuments,
  useGetWorkKnowledge,
  useGetWorkTasks,
  useCreateConversation,
  useListConversations,
  getListConversationsQueryKey,
} from '@workspace/api-client-react';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useEffect } from 'react';
import * as Haptics from 'expo-haptics';
import type { Document, KnowledgeItem, Task } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';

type Tab = 'overview' | 'docs' | 'knowledge' | 'tasks' | 'conversations';

function TabBar({ active, onSelect, colors }: { active: Tab; onSelect: (t: Tab) => void; colors: any }) {
  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'docs', label: 'Docs' },
    { key: 'knowledge', label: 'Knowledge' },
    { key: 'tasks', label: 'Tasks' },
    { key: 'conversations', label: 'Chats' },
  ];
  return (
    <View style={[styles.tabBar, { borderBottomColor: colors.border, backgroundColor: colors.background }]}>
      {tabs.map((t) => (
        <Pressable
          key={t.key}
          onPress={() => onSelect(t.key)}
          style={[
            styles.tab,
            active === t.key && { borderBottomColor: colors.primary, borderBottomWidth: 2 },
          ]}
        >
          <Text
            style={[
              styles.tabLabel,
              {
                color: active === t.key ? colors.primary : colors.mutedForeground,
                fontFamily: active === t.key ? 'Inter_600SemiBold' : 'Inter_400Regular',
              },
            ]}
          >
            {t.label}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

function DocItem({ doc }: { doc: Document }) {
  const colors = useColors();
  return (
    <View style={[styles.listItem, { borderColor: colors.border }]}>
      <View style={[styles.itemIcon, { backgroundColor: colors.muted }]}>
        <Feather name="file-text" size={14} color={colors.primary} />
      </View>
      <View style={styles.itemBody}>
        <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={1}>
          {doc.title ?? doc.source ?? 'Document'}
        </Text>
        <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
          {doc.kind ?? 'file'} · {doc.readiness ?? 'pending'}
        </Text>
      </View>
    </View>
  );
}

function KnowledgeRow({ item, onReviewed }: { item: KnowledgeItem; onReviewed?: () => void }) {
  const colors = useColors();
  const conf = Math.round((item.confidence ?? 0) * 100);
  const [localStatus, setLocalStatus] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);

  const status = localStatus ?? (item as any).review_status ?? 'auto';
  const isAiAuto = (item as any).review_status === 'ai_auto' || (item as any).source === 'llm';
  const isRejected = status === 'rejected';

  const review = async (action: 'approve' | 'reject') => {
    setReviewing(true);
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const res = await fetch(`https://${domain}/api/knowledge/${item.id}/review`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: action === 'approve' ? 'approved' : 'rejected' }),
      });
      if (res.ok) {
        setLocalStatus(action === 'approve' ? 'approved' : 'rejected');
        onReviewed?.();
      }
    } catch (_) {
      // silent — network error
    } finally {
      setReviewing(false);
    }
  };

  return (
    <View style={[styles.listItem, { borderColor: colors.border, opacity: isRejected ? 0.45 : 1 }]}>
      <View style={[styles.itemIcon, { backgroundColor: colors.muted }]}>
        <Feather name="cpu" size={14} color={isAiAuto ? '#8b5cf6' : colors.primary} />
      </View>
      <View style={styles.itemBody}>
        <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={3}>
          {item.text}
        </Text>
        <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
          {item.kind} · {conf}% · {isAiAuto ? '✦ AI' : 'rule'}
          {status === 'approved' ? ' · ✓ approved' : status === 'rejected' ? ' · ✗ rejected' : ''}
        </Text>
        {isAiAuto && status !== 'approved' && status !== 'rejected' && (
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
            <Pressable
              onPress={() => review('approve')}
              disabled={reviewing}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
                paddingHorizontal: 10,
                paddingVertical: 4,
                borderRadius: 6,
                backgroundColor: '#dcfce7',
                opacity: reviewing ? 0.5 : 1,
              }}
            >
              <Feather name="thumbs-up" size={12} color="#16a34a" />
              <Text style={{ fontSize: 11, color: '#16a34a', fontFamily: 'Inter_600SemiBold' }}>Approve</Text>
            </Pressable>
            <Pressable
              onPress={() => review('reject')}
              disabled={reviewing}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
                paddingHorizontal: 10,
                paddingVertical: 4,
                borderRadius: 6,
                backgroundColor: '#fee2e2',
                opacity: reviewing ? 0.5 : 1,
              }}
            >
              <Feather name="thumbs-down" size={12} color="#dc2626" />
              <Text style={{ fontSize: 11, color: '#dc2626', fontFamily: 'Inter_600SemiBold' }}>Reject</Text>
            </Pressable>
          </View>
        )}
      </View>
    </View>
  );
}

function TaskRow({ task }: { task: Task }) {
  const colors = useColors();
  const done = task.status === 'done' || task.status === 'complete' || task.status === 'completed';
  return (
    <View style={[styles.listItem, { borderColor: colors.border }]}>
      <Feather
        name={done ? 'check-circle' : 'circle'}
        size={18}
        color={done ? colors.primary : colors.mutedForeground}
      />
      <View style={styles.itemBody}>
        <Text
          style={[
            styles.itemTitle,
            {
              color: done ? colors.mutedForeground : colors.foreground,
              textDecorationLine: done ? 'line-through' : 'none',
            },
          ]}
        >
          {task.text}
        </Text>
        <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
          {task.status} · priority {task.priority}
        </Text>
      </View>
    </View>
  );
}

// ─── Overview tab with "Start Discussion" CTA ────────────────────────────────

function OverviewTab({ workId, onStartDiscussion, starting }: {
  workId: string;
  onStartDiscussion: () => void;
  starting: boolean;
}) {
  const colors = useColors();
  const { data: workData, isLoading, isError, refetch } = useGetWork(workId);
  const work = workData?.work;

  if (isLoading && !work) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (isError && !work) {
    return (
      <ErrorScreen
        message="Can't load work details"
        detail="Check your connection and try again."
        onRetry={refetch}
      />
    );
  }

  return (
    <ScrollView
      contentContainerStyle={styles.overviewPad}
      showsVerticalScrollIndicator={false}
      refreshControl={<RefreshControl refreshing={isLoading} onRefresh={refetch} tintColor={colors.primary} />}
    >
      {work?.description ? (
        <Text style={[styles.description, { color: colors.foreground }]}>{work.description}</Text>
      ) : (
        <Text style={[styles.description, { color: colors.mutedForeground }]}>No description.</Text>
      )}

      <View style={[styles.infoGrid, { borderColor: colors.border }]}>
        {[
          { label: 'Type', value: work?.work_type ?? '—' },
          { label: 'Status', value: work?.status ?? '—' },
          { label: 'Documents', value: String((work as any)?.doc_count ?? 0) },
          { label: 'Knowledge', value: String((work as any)?.knowledge_count ?? 0) },
          { label: 'Pending Tasks', value: String((work as any)?.pending_tasks ?? 0) },
          { label: 'Conversations', value: String((work as any)?.conv_count ?? 0) },
          {
            label: 'Updated',
            value: work?.updated_at ? new Date(work.updated_at).toLocaleDateString() : '—',
          },
        ].map((row) => (
          <View key={row.label} style={[styles.infoRow, { borderBottomColor: colors.border }]}>
            <Text style={[styles.infoLabel, { color: colors.mutedForeground }]}>{row.label}</Text>
            <Text style={[styles.infoValue, { color: colors.foreground }]}>{row.value}</Text>
          </View>
        ))}
      </View>

      {/* Start Discussion CTA */}
      <Pressable
        onPress={onStartDiscussion}
        disabled={starting}
        style={({ pressed }) => [
          styles.discussBtn,
          { backgroundColor: colors.primary, opacity: pressed || starting ? 0.7 : 1 },
        ]}
      >
        {starting ? (
          <ActivityIndicator size="small" color={colors.primaryForeground} />
        ) : (
          <Feather name="message-circle" size={16} color={colors.primaryForeground} />
        )}
        <Text style={[styles.discussBtnText, { color: colors.primaryForeground }]}>
          {starting ? 'Starting…' : 'Start a Discussion'}
        </Text>
      </Pressable>
    </ScrollView>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────

export default function WorkDetailScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const { id } = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<Tab>('overview');

  const { data: workData, isError: workError, refetch: refetchWork } = useGetWork(id, { query: { staleTime: 30_000 } } as any);
  const { data: docsData, isLoading: docsLoading, isError: docsError, refetch: refetchDocs } = useGetWorkDocuments(id, { query: { staleTime: 20_000, refetchInterval: (q: any) => (q.state.data?.documents ?? []).some((d: any) => d.readiness === 'imported') ? 4_000 : false } } as any);
  const { data: knData, isLoading: knLoading, isError: knError, refetch: refetchKn } = useGetWorkKnowledge(id, { query: { staleTime: 30_000 } } as any);
  const { data: tasksData, isLoading: tasksLoading, isError: tasksError, refetch: refetchTasks } = useGetWorkTasks(id, { query: { staleTime: 30_000 } } as any);
  const { data: convsData, isLoading: convsLoading, isError: convsError, refetch: refetchConvs } = useListConversations(
    { work_id: id, limit: 50 } as any,
    { query: { staleTime: 20_000, refetchInterval: 30_000 } } as any,
  );

  const { mutateAsync: createConversation, isPending: startingConvo } = useCreateConversation();

  const work = workData?.work;

  useEffect(() => {
    if (work?.title) {
      navigation.setOptions({ title: work.title });
    }
  }, [work?.title, navigation]);

  // Task #13 — start a conversation linked to this work
  const handleStartDiscussion = async () => {
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    try {
      const result = await createConversation({
        data: { title: work?.title ? `Discussion: ${work.title}` : 'New Discussion', work_id: id },
      });
      const convoId = result?.conversation?.id;
      if (convoId) {
        router.push(`/chat/${convoId}`);
      }
    } catch {
      Alert.alert(
        'Could not start discussion',
        'Make sure the Orivellum server is running and try again.',
        [{ text: 'OK' }]
      );
    }
  };

  const topPad = isWeb ? 67 : insets.top + 44;

  const docs = docsData?.documents ?? [];
  const knowledge = knData?.knowledge ?? [];
  const tasks = tasksData?.tasks ?? [];

  const renderTabContent = () => {
    switch (activeTab) {
      case 'overview':
        return (
          <OverviewTab
            workId={id}
            onStartDiscussion={handleStartDiscussion}
            starting={startingConvo}
          />
        );
      case 'docs':
        if (docsError && docs.length === 0) {
          return (
            <ErrorScreen
              message="Can't load documents"
              detail="Check your connection and try again."
              onRetry={refetchDocs}
            />
          );
        }
        return (
          <>
            {docsError && docs.length > 0 && (
              <OfflineBanner message="Showing cached documents — server unreachable" onRetry={refetchDocs} />
            )}
            <FlatList
              data={docs}
              keyExtractor={(d) => d.id ?? ''}
              renderItem={({ item }) => <DocItem doc={item} />}
              contentContainerStyle={styles.listPad}
              refreshControl={
                <RefreshControl refreshing={docsLoading} onRefresh={refetchDocs} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                <View style={styles.centered}>
                  <Feather name="file-text" size={36} color={colors.mutedForeground} />
                  <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No documents</Text>
                </View>
              }
            />
          </>
        );
      case 'knowledge':
        if (knError && knowledge.length === 0) {
          return (
            <ErrorScreen
              message="Can't load knowledge"
              detail="Check your connection and try again."
              onRetry={refetchKn}
            />
          );
        }
        return (
          <>
            {knError && knowledge.length > 0 && (
              <OfflineBanner message="Showing cached knowledge — server unreachable" onRetry={refetchKn} />
            )}
            <FlatList
              data={knowledge}
              keyExtractor={(k) => k.id ?? ''}
              renderItem={({ item }) => <KnowledgeRow item={item} onReviewed={refetchKn} />}
              contentContainerStyle={styles.listPad}
              refreshControl={
                <RefreshControl refreshing={knLoading} onRefresh={refetchKn} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                <View style={styles.centered}>
                  <Feather name="cpu" size={36} color={colors.mutedForeground} />
                  <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No knowledge nodes</Text>
                </View>
              }
            />
          </>
        );
      case 'tasks':
        if (tasksError && tasks.length === 0) {
          return (
            <ErrorScreen
              message="Can't load tasks"
              detail="Check your connection and try again."
              onRetry={refetchTasks}
            />
          );
        }
        return (
          <>
            {tasksError && tasks.length > 0 && (
              <OfflineBanner message="Showing cached tasks — server unreachable" onRetry={refetchTasks} />
            )}
            <FlatList
              data={tasks}
              keyExtractor={(t) => t.id ?? ''}
              renderItem={({ item }) => <TaskRow task={item} />}
              contentContainerStyle={styles.listPad}
              refreshControl={
                <RefreshControl refreshing={tasksLoading} onRefresh={refetchTasks} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                <View style={styles.centered}>
                  <Feather name="check-square" size={36} color={colors.mutedForeground} />
                  <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No tasks</Text>
                </View>
              }
            />
          </>
        );
      case 'conversations': {
        const convs = convsData?.conversations ?? [];
        if (convsError && convs.length === 0) {
          return (
            <ErrorScreen
              message="Can't load conversations"
              detail="Check your connection and try again."
              onRetry={refetchConvs}
            />
          );
        }
        return (
          <>
            {convsError && convs.length > 0 && (
              <OfflineBanner message="Showing cached conversations" onRetry={refetchConvs} />
            )}
            <FlatList
              data={convs}
              keyExtractor={(c) => (c as any).id ?? ''}
              renderItem={({ item: c }) => (
                <Pressable
                  onPress={() => router.push(`/chat/${(c as any).id}` as any)}
                  style={({ pressed }) => [
                    styles.listItem,
                    { borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
                  ]}
                >
                  <View style={[styles.itemIcon, { backgroundColor: colors.muted }]}>
                    <Feather name="message-circle" size={14} color={colors.primary} />
                  </View>
                  <View style={styles.itemBody}>
                    <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={1}>
                      {(c as any).title || 'Untitled'}
                    </Text>
                    <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
                      {(c as any).message_count ?? 0} messages
                      {(c as any).updated_at ? ` · ${new Date((c as any).updated_at).toLocaleDateString()}` : ''}
                    </Text>
                  </View>
                  <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
                </Pressable>
              )}
              contentContainerStyle={styles.listPad}
              refreshControl={
                <RefreshControl refreshing={convsLoading} onRefresh={refetchConvs} tintColor={colors.primary} />
              }
              ListHeaderComponent={
                <Pressable
                  onPress={handleStartDiscussion}
                  style={[styles.newChatBtn, { backgroundColor: colors.primary, borderColor: colors.primary }]}
                >
                  <Feather name="plus" size={14} color="#fff" />
                  <Text style={styles.newChatBtnText}>Start New Discussion</Text>
                </Pressable>
              }
              ListEmptyComponent={
                <View style={styles.centered}>
                  <Feather name="message-circle" size={36} color={colors.mutedForeground} />
                  <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No conversations yet</Text>
                </View>
              }
            />
          </>
        );
      }
    }
  };

  // Full-screen error when the work itself can't be loaded
  if (workError && !work) {
    return (
      <View style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}>
        <ErrorScreen
          message="Can't reach your workspace"
          detail="Check your connection and make sure the Orivellum server is running."
          onRetry={refetchWork}
        />
      </View>
    );
  }

  return (
    <View style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}>
      {/* Work title + type badge */}
      <View style={[styles.workHeader, { paddingHorizontal: 16, paddingBottom: 10 }]}>
        <Text style={[styles.workTitle, { color: colors.foreground }]} numberOfLines={2}>
          {work?.title ?? ''}
        </Text>
        <View style={[styles.typeBadge, { backgroundColor: colors.muted }]}>
          <Text style={[styles.typeBadgeText, { color: colors.mutedForeground }]}>
            {work?.work_type ?? 'research'}
          </Text>
        </View>
      </View>

      <TabBar active={activeTab} onSelect={setActiveTab} colors={colors} />

      <View style={{ flex: 1 }}>{renderTabContent()}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  workHeader: {},
  workTitle: { fontSize: 22, fontFamily: 'Inter_700Bold', marginBottom: 6 },
  typeBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 12,
  },
  typeBadgeText: { fontSize: 12, fontFamily: 'Inter_500Medium', textTransform: 'capitalize' },
  tabBar: { flexDirection: 'row', borderBottomWidth: 1 },
  tab: { flex: 1, alignItems: 'center', paddingVertical: 12 },
  tabLabel: { fontSize: 13 },
  overviewPad: { padding: 16, paddingBottom: 80 },
  listPad: { padding: 16, paddingBottom: 80 },
  description: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 22, marginBottom: 20 },
  infoGrid: { borderWidth: 1, borderRadius: 6, overflow: 'hidden' },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderBottomWidth: 1,
  },
  infoLabel: { fontSize: 13, fontFamily: 'Inter_400Regular' },
  infoValue: { fontSize: 13, fontFamily: 'Inter_500Medium', textTransform: 'capitalize' },
  listItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    borderBottomWidth: 1,
    paddingVertical: 12,
  },
  itemIcon: {
    width: 32,
    height: 32,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
  },
  itemBody: { flex: 1 },
  itemTitle: { fontSize: 14, fontFamily: 'Inter_500Medium', lineHeight: 19 },
  itemMeta: { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 2 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 40 },
  emptyText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  newChatBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    marginHorizontal: 16,
    marginBottom: 12,
    marginTop: 4,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
  },
  newChatBtnText: { fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#fff' },
  // Start Discussion button
  discussBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 24,
    paddingVertical: 14,
    borderRadius: 12,
  },
  discussBtnText: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
});
