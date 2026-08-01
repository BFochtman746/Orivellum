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
} from '@workspace/api-client-react';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useEffect } from 'react';
import * as Haptics from 'expo-haptics';
import type { Document, KnowledgeItem, Task } from '@workspace/api-client-react';

type Tab = 'overview' | 'docs' | 'knowledge' | 'tasks';

function TabBar({ active, onSelect, colors }: { active: Tab; onSelect: (t: Tab) => void; colors: any }) {
  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'docs', label: 'Docs' },
    { key: 'knowledge', label: 'Knowledge' },
    { key: 'tasks', label: 'Tasks' },
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

function KnowledgeRow({ item }: { item: KnowledgeItem }) {
  const colors = useColors();
  const conf = Math.round((item.confidence ?? 0) * 100);
  return (
    <View style={[styles.listItem, { borderColor: colors.border }]}>
      <View style={[styles.itemIcon, { backgroundColor: colors.muted }]}>
        <Feather name="cpu" size={14} color={colors.primary} />
      </View>
      <View style={styles.itemBody}>
        <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={3}>
          {item.text}
        </Text>
        <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
          {item.kind} · {conf}% confidence
        </Text>
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
  const { data: workData, isLoading, refetch } = useGetWork(workId);
  const work = workData?.work;

  if (isLoading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.primary} />
      </View>
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

  const { data: workData } = useGetWork(id, { query: { staleTime: 30_000 } } as any);
  const { data: docsData, isLoading: docsLoading, refetch: refetchDocs } = useGetWorkDocuments(id, { query: { staleTime: 20_000, refetchInterval: (q: any) => (q.state.data?.documents ?? []).some((d: any) => d.readiness === 'imported') ? 4_000 : false } } as any);
  const { data: knData, isLoading: knLoading, refetch: refetchKn } = useGetWorkKnowledge(id, { query: { staleTime: 30_000 } } as any);
  const { data: tasksData, isLoading: tasksLoading, refetch: refetchTasks } = useGetWorkTasks(id, { query: { staleTime: 30_000 } } as any);

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
        return (
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
        );
      case 'knowledge':
        return (
          <FlatList
            data={knowledge}
            keyExtractor={(k) => k.id ?? ''}
            renderItem={({ item }) => <KnowledgeRow item={item} />}
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
        );
      case 'tasks':
        return (
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
        );
    }
  };

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
