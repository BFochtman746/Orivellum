import React, { useState, useEffect, useCallback } from 'react';
import { mobileFetch } from '@/lib/api';
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
  TextInput,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import {
  useGetWork,
  useGetWorkDocuments,
  useGetWorkKnowledge,
  useGetWorkTasks,
  useCreateWorkTask,
  useCreateConversation,
  useListConversations,
  useUpdateWork,
  getListConversationsQueryKey,
  getGetWorkTasksQueryKey,
  getGetWorkStatsQueryKey,
} from '@workspace/api-client-react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import type { Document, KnowledgeItem, Task } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';

type Tab = 'overview' | 'docs' | 'knowledge' | 'tasks' | 'conversations' | 'learn' | 'gaps' | 'book';

function TabBar({ active, onSelect, colors, badges = {} }: { active: Tab; onSelect: (t: Tab) => void; colors: any; badges?: Partial<Record<Tab, number>> }) {
  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'docs', label: 'Docs' },
    { key: 'knowledge', label: 'Knowledge' },
    { key: 'tasks', label: 'Tasks' },
    { key: 'conversations', label: 'Chats' },
    { key: 'gaps', label: 'Gaps' },
    { key: 'learn', label: 'Learn' },
    { key: 'book', label: 'Book' },
  ];
  return (
    <View style={[styles.tabBar, { borderBottomColor: colors.border, backgroundColor: colors.background }]}>
      {tabs.map((t) => {
        const badge = badges[t.key];
        return (
        <Pressable
          key={t.key}
          onPress={() => onSelect(t.key)}
          style={[
            styles.tab,
            active === t.key && { borderBottomColor: colors.primary, borderBottomWidth: 2 },
          ]}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
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
            {badge != null && badge > 0 && (
              <View style={{ backgroundColor: colors.primary, borderRadius: 8, minWidth: 16, paddingHorizontal: 3, alignItems: 'center' }}>
                <Text style={{ color: colors.primaryForeground, fontSize: 9, fontFamily: 'Inter_700Bold', lineHeight: 14 }}>{badge}</Text>
              </View>
            )}
          </View>
        </Pressable>
        );
      })}
    </View>
  );
}

function DocItem({ doc }: { doc: Document }) {
  const colors = useColors();
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.push(`/library/${doc.id}` as any)}
      style={({ pressed }) => [styles.listItem, { borderColor: colors.border, opacity: pressed ? 0.7 : 1 }]}
    >
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
      <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
    </Pressable>
  );
}

function KnowledgeRow({ item, onReviewed, onDelete }: { item: KnowledgeItem; onReviewed?: () => void; onDelete?: () => void }) {
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
      const res = await mobileFetch(`https://${domain}/api/knowledge/${item.id}/review`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_status: action === 'approve' ? 'approved' : 'rejected' }),
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

  const handleLongPress = () => {
    if (!onDelete) return;
    Alert.alert('Delete Knowledge Item', 'Remove this item?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: onDelete },
    ]);
  };

  return (
    <Pressable
      onLongPress={handleLongPress}
      delayLongPress={400}
      style={[styles.listItem, { borderColor: colors.border, opacity: isRejected ? 0.45 : 1 }]}
    >
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
    </Pressable>
  );
}

function TaskRow({ task, onDelete, onToggle }: { task: Task; onDelete?: () => void; onToggle?: () => void }) {
  const colors = useColors();
  const done = task.status === 'done' || task.status === 'complete' || task.status === 'completed';
  const handleLongPress = () => {
    if (!onDelete) return;
    Alert.alert('Delete Task', `Remove "${task.text}"?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: onDelete },
    ]);
  };
  return (
    <Pressable
      onLongPress={handleLongPress}
      style={[styles.listItem, { borderColor: colors.border }]}
      delayLongPress={400}
    >
      <Pressable onPress={onToggle} hitSlop={8}>
        <Feather
          name={done ? 'check-circle' : 'circle'}
          size={18}
          color={done ? colors.primary : colors.mutedForeground}
        />
      </Pressable>
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
          {task.status} · {
            task.priority === 1 ? 'P1' :
            task.priority === 2 ? 'P2' :
            task.priority === 3 ? 'P3' : 'No priority'
          }
        </Text>
      </View>
    </Pressable>
  );
}

// ─── Gaps tab — research gap summary from the intelligence pipeline ───────────

// Severity color coding tuned for the dark theme: red (high/critical),
// amber (medium), muted (low). `dot` drives the leading indicator + accent.
const GAP_SEVERITY: Record<string, { bg: string; text: string; dot: string }> = {
  critical: { bg: 'rgba(239,68,68,0.18)',  text: '#f87171', dot: '#ef4444' },
  high:     { bg: 'rgba(239,68,68,0.15)',  text: '#f87171', dot: '#ef4444' },
  medium:   { bg: 'rgba(245,158,11,0.15)', text: '#fbbf24', dot: '#f59e0b' },
  low:      { bg: 'rgba(148,163,184,0.15)',text: '#94a3b8', dot: '#94a3b8' },
};

const SEVERITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

function GapsTab({
  workId,
  colors,
  onResearch,
  onCreateTask,
}: {
  workId: string;
  colors: any;
  onResearch: (gapTitle: string) => void;
  onCreateTask: (taskText: string) => void;
}) {
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchGaps = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${workId}/gaps`);
      if (!res.ok) throw new Error('gaps error');
      setData(await res.json());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [workId, domain]);

  useEffect(() => { fetchGaps(); }, [fetchGaps]);

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }
  if (error) {
    return (
      <View style={styles.centered}>
        <Feather name="alert-circle" size={32} color={colors.mutedForeground} />
        <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load gaps</Text>
        <Pressable onPress={fetchGaps} style={[styles.retryBtn, { borderColor: colors.border }]}>
          <Text style={{ color: colors.primary, fontSize: 13, fontFamily: 'Inter_500Medium' }}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  const rawGaps: any[] = data?.gaps ?? [];
  // Rank high → medium → low (critical sorts above high).
  const gaps = [...rawGaps].sort(
    (a, b) =>
      (SEVERITY_RANK[a.severity ?? 'medium'] ?? 2) - (SEVERITY_RANK[b.severity ?? 'medium'] ?? 2),
  );
  const coverage: number | null = data?.coverage_pct != null ? Number(data.coverage_pct) : null;
  const isComplete = gaps.length === 0 || coverage === 100;

  return (
    <ScrollView
      contentContainerStyle={[styles.listPad, { paddingBottom: 32 }]}
      showsVerticalScrollIndicator={false}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={fetchGaps} tintColor={colors.primary} />}
    >
      {/* Coverage indicator */}
      <View style={{ marginBottom: 16 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 }}>
          <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
            Coverage · {gaps.length} gap{gaps.length !== 1 ? 's' : ''}
          </Text>
          <Text style={[styles.itemMeta, { color: colors.foreground, fontFamily: 'Inter_600SemiBold' }]}>
            {coverage != null ? `${coverage}%` : '—'}
          </Text>
        </View>
        <View style={{ height: 6, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
          <View
            style={{
              height: '100%',
              width: `${Math.max(0, Math.min(100, coverage ?? 0))}%` as any,
              backgroundColor: isComplete ? '#22c55e' : colors.primary,
              borderRadius: 3,
            }}
          />
        </View>
      </View>

      {isComplete ? (
        <View style={styles.centered}>
          <Feather name="check-circle" size={32} color="#22c55e" />
          <Text style={[styles.emptyText, { color: colors.foreground, fontFamily: 'Inter_500Medium' }]}>
            No gaps — coverage looks complete
          </Text>
        </View>
      ) : (
        gaps.map((g: any, i: number) => {
          const sev = (g.severity ?? 'medium') as string;
          const gCol = GAP_SEVERITY[sev] ?? GAP_SEVERITY.medium;
          const isHigh = sev === 'high' || sev === 'critical';
          const gapTitle = g.title ?? g.kind ?? 'Research gap';
          return (
            <View
              key={i}
              style={[
                styles.listItem,
                { borderColor: colors.border, flexDirection: 'column', gap: 6, alignItems: 'flex-start' },
              ]}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: gCol.dot }} />
                <View style={[styles.statusBadge, { backgroundColor: gCol.bg, paddingHorizontal: 8, paddingVertical: 2 }]}>
                  <Text style={[styles.statusText, { color: gCol.text }]}>{sev}</Text>
                </View>
                <Text style={[styles.itemTitle, { color: colors.foreground, flex: 1 }]} numberOfLines={2}>
                  {gapTitle}
                </Text>
              </View>
              {g.description ? (
                <Text style={[styles.itemMeta, { color: colors.mutedForeground, lineHeight: 16 }]} numberOfLines={4}>
                  {g.description}
                </Text>
              ) : null}
              {(
                <View style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
                  <Pressable
                    onPress={() => onCreateTask(`Research gap: ${gapTitle}`)}
                    style={({ pressed }) => ({
                      flexDirection: 'row', alignItems: 'center', gap: 4,
                      paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6,
                      backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1,
                    })}
                  >
                    <Feather name="plus" size={12} color={colors.primary} />
                    <Text style={{ fontSize: 12, color: colors.primary, fontFamily: 'Inter_600SemiBold' }}>
                      Add Task
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => onResearch(gapTitle)}
                    style={({ pressed }) => ({
                      flexDirection: 'row', alignItems: 'center', gap: 4,
                      paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6,
                      backgroundColor: colors.primary, opacity: pressed ? 0.7 : 1,
                    })}
                  >
                    <Feather name="search" size={12} color={colors.primaryForeground} />
                    <Text style={{ fontSize: 12, color: colors.primaryForeground, fontFamily: 'Inter_600SemiBold' }}>
                      Research →
                    </Text>
                  </Pressable>
                </View>
              )}
            </View>
          );
        })
      )}
    </ScrollView>
  );
}

// ─── Overview tab with "Start Discussion" CTA ────────────────────────────────

function OverviewTab({ workId, onStartDiscussion, starting, onNavigateToTab }: {
  workId: string;
  onStartDiscussion: () => void;
  starting: boolean;
  onNavigateToTab?: (tab: Tab) => void;
}) {
  const colors = useColors();
  const { data: workData, isLoading, isError, refetch } = useGetWork(workId);
  const work = workData?.work;
  const queryClient = useQueryClient();
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState('');
  const { mutate: updateWork } = useUpdateWork();

  const startDescEdit = () => {
    setDescDraft(work?.description ?? '');
    setEditingDesc(true);
  };

  const saveDesc = () => {
    setEditingDesc(false);
    const trimmed = descDraft.trim();
    if (trimmed === (work?.description ?? '')) return;
    updateWork({ workId, data: { title: work?.title ?? '', description: trimmed || null } }, {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: [workId] }),
    });
  };

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
      {editingDesc ? (
        <View style={{ marginBottom: 16 }}>
          <TextInput
            style={[styles.description, { color: colors.foreground, borderWidth: 1, borderColor: colors.primary, borderRadius: 6, padding: 8 }]}
            value={descDraft}
            onChangeText={setDescDraft}
            multiline
            autoFocus
            onBlur={saveDesc}
            returnKeyType="done"
            placeholder="Work description…"
            placeholderTextColor={colors.mutedForeground}
          />
        </View>
      ) : (
        <Pressable onPress={startDescEdit} style={{ marginBottom: 0 }}>
          {work?.description ? (
            <Text style={[styles.description, { color: colors.foreground }]}>{work.description}</Text>
          ) : (
            <Text style={[styles.description, { color: colors.mutedForeground, fontStyle: 'italic' }]}>Tap to add a description…</Text>
          )}
        </Pressable>
      )}

      <View style={[styles.infoGrid, { borderColor: colors.border }]}>
        {[
          { label: 'Type', value: work?.work_type ?? '—', tab: undefined },
          { label: 'Status', value: work?.status ?? '—', tab: undefined },
          { label: 'Documents', value: String((work as any)?.doc_count ?? 0), tab: 'docs' as Tab },
          ...((): { label: string; value: string; tab?: Tab }[] => {
            const ready = (work as any)?.ready_doc_count ?? 0;
            const errs  = (work as any)?.error_doc_count ?? 0;
            const proc  = (work as any)?.processing_doc_count ?? 0;
            const total = (work as any)?.doc_count ?? 0;
            if (total === 0) return [];
            const parts: string[] = [];
            if (ready > 0) parts.push(`${ready} ready`);
            if (proc > 0)  parts.push(`${proc} processing`);
            if (errs > 0)  parts.push(`${errs} error${errs !== 1 ? 's' : ''}`);
            return parts.length ? [{ label: 'Readiness', value: parts.join(' · '), tab: 'docs' as Tab }] : [];
          })(),
          { label: 'Knowledge', value: String((work as any)?.knowledge_count ?? 0), tab: 'knowledge' as Tab },
          { label: 'Pending Tasks', value: String((work as any)?.pending_tasks ?? 0), tab: 'tasks' as Tab },
          { label: 'Conversations', value: String((work as any)?.conv_count ?? 0), tab: 'conversations' as Tab },
          {
            label: 'Updated',
            value: work?.updated_at ? new Date(work.updated_at).toLocaleDateString() : '—',
            tab: undefined,
          },
        ].map((row) => (
          <Pressable
            key={row.label}
            onPress={row.tab ? () => onNavigateToTab?.(row.tab!) : undefined}
            style={({ pressed }) => [
              styles.infoRow,
              { borderBottomColor: colors.border, opacity: (row.tab && pressed) ? 0.7 : 1 },
            ]}
          >
            <Text style={[styles.infoLabel, { color: colors.mutedForeground }]}>{row.label}</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
              <Text style={[styles.infoValue, { color: row.tab ? colors.primary : colors.foreground }]}>{row.value}</Text>
              {row.tab && <Feather name="chevron-right" size={12} color={colors.primary} />}
            </View>
          </Pressable>
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

// ─── Mobile Learn tab ─────────────────────────────────────────────────────────

type MobileLearnPhase = 'loading' | 'seeding' | 'question' | 'assessing' | 'feedback' | 'all_done' | 'error';

interface MobileSession {
  concept_id: string;
  subject: string;
  description: string;
  question: string;
  context_snippet: string;
}

interface MobileAssessResult {
  score: number;
  feedback: string;
  route: 'STEP_FORWARD' | 'STEP_BACKWARD' | 'STAY_HERE';
  graduated: boolean;
  next_concept_id: string | null;
  summary: { total: number; graduated: number; mastery_pct: number };
}

function MobileLearnTab({ workId, colors }: { workId: string; colors: any }) {
  const [phase, setPhase]       = useState<MobileLearnPhase>('loading');
  const [session, setSession]   = useState<MobileSession | null>(null);
  const [answer, setAnswer]     = useState('');
  const [result, setResult]     = useState<MobileAssessResult | null>(null);
  const [summary, setSummary]   = useState<{ total: number; graduated: number; mastery_pct: number } | null>(null);
  const [errorMsg, setErrorMsg] = useState('');

  const domain = process.env.EXPO_PUBLIC_DOMAIN;
  const apiBase = `https://${domain}/api`;

  const fetchSummary = async () => {
    const r = await mobileFetch(`${apiBase}/works/${workId}/learning/summary`);
    if (!r.ok) throw new Error('Could not load summary');
    return r.json();
  };

  const loadQuestion = async (conceptId?: string | null) => {
    setAnswer('');
    setResult(null);
    setPhase('question');
    const url = conceptId
      ? `${apiBase}/works/${workId}/learning/question?concept_id=${conceptId}`
      : `${apiBase}/works/${workId}/learning/question`;
    const r = await mobileFetch(url);
    if (r.status === 422) { setPhase('all_done'); return; }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    setSession({
      concept_id:      d.concept_id,
      subject:         d.subject ?? 'Concept',
      description:     d.description ?? '',
      question:        d.question,
      context_snippet: d.context_snippet ?? '',
    });
  };

  const init = async () => {
    setPhase('loading');
    setErrorMsg('');
    try {
      const data = await fetchSummary();
      setSummary(data);
      if (data.total === 0) {
        setPhase('seeding');
        const sr = await mobileFetch(`${apiBase}/works/${workId}/learning/seed`, { method: 'POST' });
        if (!sr.ok) throw new Error('Could not seed concepts');
        const sd = await sr.json();
        if (!(sd.concepts ?? []).length) throw new Error('No knowledge found — import documents first.');
        const s2 = await fetchSummary();
        setSummary(s2);
      }
      if (data.mastery_pct === 100 && data.total > 0) { setPhase('all_done'); return; }
      await loadQuestion(null);
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not start session');
      setPhase('error');
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { init(); }, [workId]);

  const submitAnswer = async () => {
    if (!session || !answer.trim()) return;
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setPhase('assessing');
    try {
      const r = await mobileFetch(`${apiBase}/works/${workId}/learning/assess`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          concept_id: session.concept_id,
          question:   session.question,
          answer:     answer.trim(),
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: MobileAssessResult = await r.json();
      setResult(d);
      setSummary(d.summary);
      setPhase('feedback');
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not assess answer');
      setPhase('error');
    }
  };

  const next = async () => {
    if (!result) { await loadQuestion(null); return; }
    if (result.summary.mastery_pct === 100) { setPhase('all_done'); return; }
    try { await loadQuestion(result.next_concept_id); }
    catch (e: any) { setErrorMsg(e.message ?? 'Error loading next question'); setPhase('error'); }
  };

  const scoreBg    = (s: number) => s >= 0.75 ? '#dcfce7' : s >= 0.5 ? '#fef3c7' : '#fee2e2';
  const scoreColor = (s: number) => s >= 0.75 ? '#16a34a' : s >= 0.5 ? '#d97706' : '#dc2626';

  // ── Loading / seeding ────────────────────────────────────────────────────
  if (phase === 'loading' || phase === 'seeding') {
    return (
      <View style={[styles.centered, { flex: 1 }]}>
        <ActivityIndicator color={colors.primary} />
        <Text style={[styles.emptyText, { color: colors.mutedForeground, marginTop: 10 }]}>
          {phase === 'seeding' ? 'Seeding concepts from your knowledge…' : 'Loading session…'}
        </Text>
      </View>
    );
  }

  // ── All done ────────────────────────────────────────────────────────────
  if (phase === 'all_done') {
    const handleReset = async () => {
      try {
        await mobileFetch(`${apiBase}/works/${workId}/learning/reset`, { method: 'POST' });
        init();
      } catch { init(); }
    };
    return (
      <View style={[styles.centered, { flex: 1, padding: 32 }]}>
        <Feather name="award" size={48} color={colors.primary} />
        <Text style={[styles.workTitle, { color: colors.foreground, textAlign: 'center', marginTop: 16, fontSize: 20 }]}>
          All concepts mastered!
        </Text>
        <Text style={[styles.description, { color: colors.mutedForeground, textAlign: 'center', marginTop: 8 }]}>
          Add more documents to unlock new material, or reset your streaks to study again.
        </Text>
        {summary && (
          <Text style={[styles.itemMeta, { color: colors.mutedForeground, marginTop: 12 }]}>
            {summary.graduated}/{summary.total} concepts · {summary.mastery_pct}%
          </Text>
        )}
        <Pressable
          onPress={handleReset}
          style={({ pressed }) => [
            styles.discussBtn,
            { backgroundColor: colors.muted, marginTop: 24, paddingHorizontal: 24, opacity: pressed ? 0.7 : 1 },
          ]}
        >
          <Feather name="refresh-cw" size={14} color={colors.foreground} />
          <Text style={[styles.discussBtnText, { color: colors.foreground }]}>Reset &amp; study again</Text>
        </Pressable>
      </View>
    );
  }

  // ── Error ───────────────────────────────────────────────────────────────
  if (phase === 'error') {
    return (
      <View style={[styles.centered, { flex: 1, padding: 32 }]}>
        <Feather name="alert-circle" size={40} color="#dc2626" />
        <Text style={[styles.itemTitle, { color: '#dc2626', textAlign: 'center', marginTop: 12 }]}>{errorMsg}</Text>
        <Pressable
          onPress={init}
          style={({ pressed }) => [
            styles.discussBtn,
            { backgroundColor: colors.primary, marginTop: 20, paddingHorizontal: 28, opacity: pressed ? 0.7 : 1 },
          ]}
        >
          <Feather name="refresh-cw" size={14} color={colors.primaryForeground} />
          <Text style={[styles.discussBtnText, { color: colors.primaryForeground }]}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  // ── Active session ──────────────────────────────────────────────────────
  return (
    <ScrollView
      contentContainerStyle={[styles.listPad, { paddingTop: 16, paddingBottom: 80 }]}
      keyboardShouldPersistTaps="handled"
      showsVerticalScrollIndicator={false}
    >
      {/* Mastery bar */}
      {summary && (
        <View style={{ marginBottom: 20 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 5 }}>
            <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
              {summary.graduated}/{summary.total} graduated
            </Text>
            <Text style={[styles.itemMeta, { color: colors.foreground, fontFamily: 'Inter_600SemiBold' }]}>
              {summary.mastery_pct}%
            </Text>
          </View>
          <View style={{ height: 6, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
            <View
              style={{
                height: '100%',
                width: `${summary.mastery_pct}%` as any,
                backgroundColor: colors.primary,
                borderRadius: 3,
              }}
            />
          </View>
        </View>
      )}

      {/* Concept chip */}
      {session && (
        <View style={{
          borderWidth: 1, borderColor: colors.border, borderRadius: 10,
          padding: 14, marginBottom: 16, backgroundColor: colors.background,
        }}>
          <Text style={[styles.itemMeta, {
            color: colors.mutedForeground, textTransform: 'uppercase',
            letterSpacing: 0.8, marginBottom: 4,
          }]}>
            Studying
          </Text>
          <Text style={[styles.itemTitle, { color: colors.foreground, fontSize: 16 }]}>
            {session.subject}
          </Text>
          {session.description ? (
            <Text style={[styles.itemMeta, { color: colors.mutedForeground, marginTop: 4 }]}>
              {session.description}
            </Text>
          ) : null}
        </View>
      )}

      {/* Question card */}
      {session && (
        <View style={{
          borderWidth: 1, borderColor: colors.border, borderRadius: 12,
          padding: 16, marginBottom: 16, backgroundColor: colors.background,
        }}>
          {session.context_snippet ? (
            <Text style={[styles.itemMeta, {
              color: colors.mutedForeground, fontStyle: 'italic',
              marginBottom: 12, borderLeftWidth: 2, borderLeftColor: colors.border, paddingLeft: 10,
            }]}>
              {session.context_snippet}
            </Text>
          ) : null}

          <Text style={[styles.itemTitle, { color: colors.foreground, fontSize: 15, lineHeight: 23, marginBottom: 16 }]}>
            {session.question}
          </Text>

          {phase !== 'feedback' ? (
            <>
              <TextInput
                value={answer}
                onChangeText={setAnswer}
                multiline
                numberOfLines={5}
                placeholder="Write your answer here…"
                placeholderTextColor={colors.mutedForeground}
                editable={phase === 'question'}
                style={{
                  borderWidth: 1, borderColor: colors.border, borderRadius: 8,
                  padding: 12, color: colors.foreground, fontSize: 14,
                  fontFamily: 'Inter_400Regular', minHeight: 110,
                  textAlignVertical: 'top', backgroundColor: colors.background,
                  marginBottom: 12, opacity: phase === 'assessing' ? 0.6 : 1,
                }}
              />
              <Pressable
                onPress={submitAnswer}
                disabled={!answer.trim() || phase === 'assessing'}
                style={({ pressed }) => ({
                  flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                  gap: 8, paddingVertical: 12, borderRadius: 10,
                  backgroundColor: colors.primary,
                  opacity: (!answer.trim() || phase === 'assessing' || pressed) ? 0.6 : 1,
                })}
              >
                {phase === 'assessing'
                  ? <ActivityIndicator size="small" color={colors.primaryForeground} />
                  : <Feather name="send" size={14} color={colors.primaryForeground} />}
                <Text style={{ color: colors.primaryForeground, fontFamily: 'Inter_600SemiBold', fontSize: 14 }}>
                  {phase === 'assessing' ? 'Assessing…' : 'Submit Answer'}
                </Text>
              </Pressable>
            </>
          ) : result ? (
            /* Feedback */
            <View style={{ gap: 12 }}>
              {/* User's answer (dimmed) */}
              <Text style={[styles.itemMeta, {
                color: colors.mutedForeground, fontStyle: 'italic',
                padding: 10, borderRadius: 6, backgroundColor: colors.muted,
              }]}>
                {answer}
              </Text>

              {/* Score */}
              <View style={{
                flexDirection: 'row', alignItems: 'center', gap: 12,
                padding: 12, borderRadius: 10, backgroundColor: scoreBg(result.score),
              }}>
                <Text style={{ fontSize: 22, fontFamily: 'Inter_700Bold', color: scoreColor(result.score) }}>
                  {Math.round(result.score * 100)}%
                </Text>
                <Text style={{ flex: 1, fontSize: 13, color: scoreColor(result.score), lineHeight: 19 }}>
                  {result.feedback}
                </Text>
                {result.graduated && (
                  <View style={{
                    flexDirection: 'row', alignItems: 'center', gap: 4,
                    paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20,
                    backgroundColor: '#dcfce7',
                  }}>
                    <Feather name="award" size={12} color="#16a34a" />
                    <Text style={{ fontSize: 11, color: '#16a34a', fontFamily: 'Inter_600SemiBold' }}>Graduated!</Text>
                  </View>
                )}
              </View>

              {/* Routing hint */}
              <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
                → {result.route === 'STEP_FORWARD'
                  ? 'Moving to the next concept'
                  : result.route === 'STEP_BACKWARD'
                  ? 'Revisiting a foundational concept'
                  : 'Keep practising this concept'}
              </Text>

              <Pressable
                onPress={next}
                style={({ pressed }) => ({
                  flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                  gap: 8, paddingVertical: 12, borderRadius: 10,
                  backgroundColor: colors.primary, opacity: pressed ? 0.7 : 1,
                })}
              >
                <Feather
                  name={result.summary.mastery_pct === 100 ? 'award' : result.route === 'STEP_FORWARD' ? 'chevron-right' : 'refresh-cw'}
                  size={14}
                  color={colors.primaryForeground}
                />
                <Text style={{ color: colors.primaryForeground, fontFamily: 'Inter_600SemiBold', fontSize: 14 }}>
                  {result.summary.mastery_pct === 100
                    ? 'Done!'
                    : result.route === 'STEP_FORWARD'
                    ? 'Next Concept'
                    : 'Try Again'}
                </Text>
              </Pressable>
            </View>
          ) : null}
        </View>
      )}
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
  const [newTaskText, setNewTaskText] = useState('');
  const [newTaskPriority, setNewTaskPriority] = useState(0);
  const [addingTask, setAddingTask] = useState(false);

  // ── Book / Pipeline tab state ──────────────────────────────────────────────
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const [pipeline, setPipeline] = useState<any>(null);
  const [pipelineLoading, setPipelineLoading] = useState(false);
  const [advancingPipeline, setAdvancingPipeline] = useState(false);
  const [chapters, setChapters] = useState<any[]>([]);
  const [chaptersLoading, setChaptersLoading] = useState(false);

  const fetchPipeline = useCallback(async () => {
    if (!id) return;
    setPipelineLoading(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/pipeline`);
      if (res.ok) setPipeline(await res.json());
      else if (res.status === 404) setPipeline(null);
    } catch { /* non-fatal */ }
    finally { setPipelineLoading(false); }
  }, [id, domain]);

  const startPipeline = async () => {
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/pipeline`, { method: 'POST' });
      if (res.ok) fetchPipeline();
    } catch { Alert.alert('Error', 'Could not start pipeline'); }
  };

  const advancePipeline = async () => {
    setAdvancingPipeline(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/pipeline/advance`, { method: 'POST' });
      if (res.ok) { fetchPipeline(); }
      else {
        const json = await res.json().catch(() => ({}));
        Alert.alert('Cannot advance', json.detail ?? 'Open blockers must be resolved first.');
      }
    } catch { Alert.alert('Error', 'Could not advance pipeline'); }
    finally { setAdvancingPipeline(false); }
  };

  const fetchChapters = useCallback(async () => {
    if (!id) return;
    setChaptersLoading(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/chapters`);
      if (res.ok) {
        const json = await res.json();
        // Flatten: [{doc_title, chapters:[...]}] → flat list annotated with doc_title
        const flat: any[] = [];
        for (const doc of json.documents ?? []) {
          for (const ch of doc.chapters ?? []) {
            flat.push({ ...ch, doc_title: doc.doc_title });
          }
        }
        setChapters(flat);
      }
    } catch { /* non-fatal */ }
    finally { setChaptersLoading(false); }
  }, [id, domain]);

  useEffect(() => {
    if (activeTab === 'book') { fetchPipeline(); fetchChapters(); }
  }, [activeTab, fetchPipeline, fetchChapters]);
  const queryClient = useQueryClient();
  const { mutateAsync: createTask } = useCreateWorkTask();

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
      navigation.setOptions({
        title: work.title,
        headerRight: () => (
          <Pressable
            onPress={() => router.push(`/work/${id}/intelligence` as any)}
            hitSlop={8}
            style={{ paddingHorizontal: 10, paddingVertical: 6 }}
          >
            <Feather name="cpu" size={18} color={colors.primary} />
          </Pressable>
        ),
      });
    }
  }, [work?.title, navigation, id, router, colors.primary]);

  // Work title inline editing
  const [editingWorkTitle, setEditingWorkTitle] = useState(false);
  const [workTitleDraft, setWorkTitleDraft] = useState('');
  const { mutate: updateWorkTitle } = useUpdateWork();

  const saveWorkTitle = () => {
    setEditingWorkTitle(false);
    const trimmed = workTitleDraft.trim();
    if (!trimmed || trimmed === work?.title) return;
    updateWorkTitle({ workId: id, data: { title: trimmed, description: (work as any)?.description ?? null } }, {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: [id] }),
    });
  };

  // Tasks search state
  const [taskSearch, setTaskSearch] = useState('');

  // Knowledge search + kind filter
  const [knSearch, setKnSearch] = useState('');
  const [knKindFilter, setKnKindFilter] = useState<'all' | 'entity' | 'claim' | 'relationship' | 'summary'>('all');

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

  // Toggle task status between pending/completed.
  const handleToggleTask = async (taskId: string, currentStatus: string | undefined) => {
    const next = (currentStatus === 'done' || currentStatus === 'complete' || currentStatus === 'completed') ? 'pending' : 'completed';
    try {
      await mobileFetch(`https://${domain}/api/works/${id}/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: next }),
      });
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(id) });
      refetchTasks();
    } catch {
      Alert.alert('Error', 'Could not update task');
    }
  };

  // Delete a knowledge item by id (called from KnowledgeRow long-press).
  const handleDeleteKnowledge = async (itemId: string) => {
    try {
      await mobileFetch(`https://${domain}/api/knowledge/${itemId}`, { method: 'DELETE' });
      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(id) });
      refetchKn();
    } catch {
      Alert.alert('Error', 'Could not delete knowledge item');
    }
  };

  // Delete a task by id (called from TaskRow long-press).
  const handleDeleteTask = async (taskId: string) => {
    try {
      await mobileFetch(`https://${domain}/api/works/${id}/tasks/${taskId}`, { method: 'DELETE' });
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(id) });
      refetchTasks();
    } catch {
      Alert.alert('Error', 'Could not delete task');
    }
  };

  // Add Task from Gap: create a Work task pre-filled with the gap title.
  const handleCreateTaskFromGap = async (taskText: string) => {
    try {
      await createTask({ workId: id, data: { text: taskText } });
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
    } catch {
      Alert.alert('Error', 'Could not create task');
    }
  };

  // Research → : open a work-linked conversation pre-seeded with the gap title.
  const handleResearchGap = async (gapTitle: string) => {
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    try {
      const result = await createConversation({
        data: {
          title: gapTitle ? `Research: ${gapTitle}` : 'Research gap',
          work_id: id,
        },
      });
      const convoId = result?.conversation?.id;
      if (convoId) {
        const draft = gapTitle
          ? `Help me research this gap: ${gapTitle}`
          : undefined;
        router.push({
          pathname: '/chat/[id]',
          params: draft ? { id: convoId, draft } : { id: convoId },
        } as any);
      }
    } catch {
      Alert.alert(
        'Could not start research',
        'Make sure the Orivellum server is running and try again.',
        [{ text: 'OK' }]
      );
    }
  };

  const handleAddTask = async () => {
    const trimmed = newTaskText.trim();
    if (!trimmed) return;
    setAddingTask(true);
    try {
      await createTask({ workId: id, data: { text: trimmed, priority: newTaskPriority || undefined } });
      setNewTaskText('');
      setNewTaskPriority(0);
      await refetchTasks();
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
    } catch {
      Alert.alert('Could not add task', 'Check your connection and try again.', [{ text: 'OK' }]);
    } finally {
      setAddingTask(false);
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
            onNavigateToTab={setActiveTab}
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
            {/* Search + kind filter */}
            <View style={{ paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border, gap: 6, backgroundColor: colors.background }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Feather name="search" size={13} color={colors.mutedForeground} />
                <TextInput
                  style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground }}
                  placeholder="Search knowledge…"
                  placeholderTextColor={colors.mutedForeground}
                  value={knSearch}
                  onChangeText={setKnSearch}
                />
                {knSearch.length > 0 && (
                  <Pressable onPress={() => setKnSearch('')} hitSlop={8}>
                    <Feather name="x" size={13} color={colors.mutedForeground} />
                  </Pressable>
                )}
              </View>
              <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
                {(['all', 'entity', 'claim', 'relationship', 'summary'] as const).map((k) => (
                  <Pressable
                    key={k}
                    onPress={() => setKnKindFilter(k)}
                    style={{
                      paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10,
                      backgroundColor: knKindFilter === k ? colors.primary : colors.muted,
                      borderWidth: 1,
                      borderColor: knKindFilter === k ? colors.primary : colors.border,
                    }}
                  >
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: knKindFilter === k ? colors.primaryForeground : colors.mutedForeground, textTransform: 'capitalize' }}>{k}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
            <FlatList
              data={knowledge.filter((k: any) => {
                const matchesKind = knKindFilter === 'all' || k.kind === knKindFilter;
                const matchesSearch = !knSearch.trim() || (k.text ?? '').toLowerCase().includes(knSearch.toLowerCase());
                return matchesKind && matchesSearch;
              })}
              keyExtractor={(k) => k.id ?? ''}
              renderItem={({ item }) => (
                <KnowledgeRow
                  item={item}
                  onReviewed={refetchKn}
                  onDelete={() => handleDeleteKnowledge((item as any).id)}
                />
              )}
              contentContainerStyle={styles.listPad}
              refreshControl={
                <RefreshControl refreshing={knLoading} onRefresh={refetchKn} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                <View style={styles.centered}>
                  <Feather name="cpu" size={36} color={colors.mutedForeground} />
                  <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
                    {knSearch.trim() || knKindFilter !== 'all' ? 'No matching knowledge items' : 'No knowledge nodes'}
                  </Text>
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
            {/* Search tasks */}
            <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, paddingVertical: 6, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border, backgroundColor: colors.background, gap: 6 }}>
              <Feather name="search" size={13} color={colors.mutedForeground} />
              <TextInput
                style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground }}
                placeholder="Search tasks…"
                placeholderTextColor={colors.mutedForeground}
                value={taskSearch}
                onChangeText={setTaskSearch}
              />
              {taskSearch.length > 0 && (
                <Pressable onPress={() => setTaskSearch('')} hitSlop={8}>
                  <Feather name="x" size={13} color={colors.mutedForeground} />
                </Pressable>
              )}
            </View>
            {/* Add task input */}
            <View style={[styles.taskInputRow, { borderBottomColor: colors.border, backgroundColor: colors.background }]}>
              <TextInput
                style={[styles.taskInput, { backgroundColor: colors.card, borderColor: colors.border, color: colors.foreground }]}
                placeholder="Add a task…"
                placeholderTextColor={colors.mutedForeground}
                value={newTaskText}
                onChangeText={setNewTaskText}
                onSubmitEditing={handleAddTask}
                returnKeyType="done"
                editable={!addingTask}
              />
              <Pressable
                onPress={handleAddTask}
                disabled={!newTaskText.trim() || addingTask}
                style={[styles.taskAddBtn, { backgroundColor: newTaskText.trim() && !addingTask ? colors.primary : colors.muted }]}
              >
                {addingTask
                  ? <ActivityIndicator size="small" color={colors.primaryForeground} />
                  : <Feather name="plus" size={18} color={newTaskText.trim() ? colors.primaryForeground : colors.mutedForeground} />
                }
              </Pressable>
            </View>
            {/* Priority picker */}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.border }}>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: colors.mutedForeground, marginRight: 4 }}>Priority</Text>
              {([0, 1, 2, 3] as const).map((p) => {
                const labels = ['None', 'P3', 'P2', 'P1'];
                const active = newTaskPriority === p;
                return (
                  <Pressable
                    key={p}
                    onPress={() => setNewTaskPriority(p)}
                    style={{
                      paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12,
                      backgroundColor: active ? colors.primary : colors.muted,
                      borderWidth: 1,
                      borderColor: active ? colors.primary : colors.border,
                    }}
                  >
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: active ? colors.primaryForeground : colors.mutedForeground }}>
                      {labels[p]}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            <FlatList
              data={taskSearch.trim() ? tasks.filter((t: any) => (t.text ?? '').toLowerCase().includes(taskSearch.toLowerCase())) : tasks}
              keyExtractor={(t) => t.id ?? ''}
              renderItem={({ item }) => (
                <TaskRow
                  task={item}
                  onDelete={() => handleDeleteTask((item as any).id)}
                  onToggle={() => handleToggleTask((item as any).id, (item as any).status)}
                />
              )}
              contentContainerStyle={styles.listPad}
              refreshControl={
                <RefreshControl refreshing={tasksLoading} onRefresh={refetchTasks} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                <View style={styles.centered}>
                  <Feather name="check-square" size={36} color={colors.mutedForeground} />
                  <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
                    {taskSearch.trim() ? `No tasks matching "${taskSearch}"` : 'No tasks yet — add one above'}
                  </Text>
                </View>
              }
            />
          </>
        );
      case 'gaps':
        return <GapsTab workId={id} colors={colors} onResearch={handleResearchGap} onCreateTask={handleCreateTaskFromGap} />;
      case 'learn':
        return <MobileLearnTab workId={id} colors={colors} />;
      case 'book':
        return (
          <ScrollView contentContainerStyle={{ padding: 16 }}>
            {pipelineLoading ? (
              <ActivityIndicator color={colors.primary} style={{ marginTop: 32 }} />
            ) : !pipeline ? (
              <View style={{ alignItems: 'center', paddingVertical: 40, gap: 16 }}>
                <Feather name="book" size={36} color={colors.mutedForeground} />
                <Text style={{ fontSize: 15, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
                  No book pipeline yet
                </Text>
                <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center', maxWidth: 260 }}>
                  Start a pipeline to track this Work through the full book production lifecycle.
                </Text>
                <Pressable
                  onPress={startPipeline}
                  style={[styles.newChatBtn, { backgroundColor: colors.primary }]}
                >
                  <Feather name="play" size={14} color="#fff" />
                  <Text style={styles.newChatBtnText}>Start Pipeline</Text>
                </Pressable>
              </View>
            ) : (
              <View style={{ gap: 14 }}>
                {/* Stage badge */}
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                  <View style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6, backgroundColor: colors.primary + '18', borderWidth: 1, borderColor: colors.primary + '44' }}>
                    <Text style={{ fontSize: 13, fontFamily: 'Inter_700Bold', color: colors.primary }}>
                      {pipeline.status ?? 'B0'}
                    </Text>
                  </View>
                  <Text style={{ fontSize: 15, fontFamily: 'Inter_600SemiBold', color: colors.foreground, flex: 1 }}>
                    {pipeline.stage_label ?? pipeline.status}
                  </Text>
                </View>

                {/* Chapter stats */}
                {pipeline.chapters_total > 0 && (
                  <View style={{ backgroundColor: colors.muted + '44', borderRadius: 10, padding: 14, gap: 8 }}>
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8 }}>CHAPTERS</Text>
                    {[
                      { label: 'Total', value: pipeline.chapters_total },
                      { label: 'Extracted', value: pipeline.chapters_extracted },
                      { label: 'Drafted', value: pipeline.chapters_drafted },
                      { label: 'Approved', value: pipeline.chapters_approved },
                    ].map(({ label, value }) => (
                      <View key={label} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                        <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground }}>{label}</Text>
                        <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>{value ?? 0}</Text>
                      </View>
                    ))}
                  </View>
                )}

                {/* Advance button */}
                {pipeline.next_status && (
                  <Pressable
                    onPress={advancePipeline}
                    disabled={advancingPipeline}
                    style={({ pressed }) => [
                      styles.newChatBtn,
                      { backgroundColor: pressed ? colors.primary + 'cc' : colors.primary, opacity: advancingPipeline ? 0.6 : 1 },
                    ]}
                  >
                    {advancingPipeline
                      ? <ActivityIndicator size="small" color="#fff" />
                      : <Feather name="arrow-right" size={14} color="#fff" />}
                    <Text style={styles.newChatBtnText}>
                      {advancingPipeline ? 'Advancing…' : `Advance to ${pipeline.next_status}`}
                    </Text>
                  </Pressable>
                )}

                {/* Chapter list */}
                {chaptersLoading ? (
                  <ActivityIndicator color={colors.primary} style={{ marginTop: 8 }} />
                ) : chapters.length > 0 ? (
                  <View style={{ gap: 6, marginTop: 4 }}>
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8 }}>
                      CHAPTERS ({chapters.length})
                    </Text>
                    {chapters.map((ch: any, i: number) => {
                      const statusColor = ch.status === 'approved' ? '#16a34a' : ch.status === 'drafted' ? colors.primary : colors.mutedForeground;
                      return (
                        <View key={ch.id ?? i} style={[styles.listItem, { borderColor: colors.border, paddingVertical: 8 }]}>
                          <View style={{ flex: 1 }}>
                            <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }} numberOfLines={2}>
                              {ch.title ?? `Chapter ${ch.seq ?? i + 1}`}
                            </Text>
                            <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 2 }}>
                              {ch.doc_title} · {ch.word_count ?? 0} words
                            </Text>
                          </View>
                          <View style={{ paddingHorizontal: 7, paddingVertical: 3, borderRadius: 5, borderWidth: 1, borderColor: statusColor + '44', backgroundColor: statusColor + '12' }}>
                            <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: statusColor }}>{ch.status ?? 'pending'}</Text>
                          </View>
                        </View>
                      );
                    })}
                  </View>
                ) : null}
              </View>
            )}
          </ScrollView>
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
        {editingWorkTitle ? (
          <TextInput
            autoFocus
            style={[styles.workTitle, { color: colors.foreground, borderBottomWidth: 2, borderBottomColor: colors.primary, marginBottom: 6 }]}
            value={workTitleDraft}
            onChangeText={setWorkTitleDraft}
            onBlur={saveWorkTitle}
            onSubmitEditing={saveWorkTitle}
            returnKeyType="done"
          />
        ) : (
          <Pressable onLongPress={() => { setWorkTitleDraft(work?.title ?? ''); setEditingWorkTitle(true); }} delayLongPress={500}>
            <Text style={[styles.workTitle, { color: colors.foreground }]} numberOfLines={2}>
              {work?.title ?? ''}
            </Text>
          </Pressable>
        )}
        <View style={[styles.typeBadge, { backgroundColor: colors.muted }]}>
          <Text style={[styles.typeBadgeText, { color: colors.mutedForeground }]}>
            {work?.work_type ?? 'research'}
          </Text>
        </View>
      </View>

      <TabBar
        active={activeTab}
        onSelect={setActiveTab}
        colors={colors}
        badges={{
          tasks: (tasksData?.tasks ?? []).filter((t: any) => t.status !== 'completed').length || undefined,
          conversations: (convsData?.conversations ?? []).length || undefined,
        }}
      />

      <View style={{ flex: 1 }}>{renderTabContent()}</View>

      {/* Floating quick-add task button — visible from all tabs except Tasks */}
      {activeTab !== 'tasks' && (
        <Pressable
          onPress={() => setActiveTab('tasks')}
          style={{
            position: 'absolute',
            bottom: insets.bottom + 20,
            right: 20,
            width: 50,
            height: 50,
            borderRadius: 25,
            backgroundColor: colors.primary,
            alignItems: 'center',
            justifyContent: 'center',
            shadowColor: '#000',
            shadowOffset: { width: 0, height: 2 },
            shadowOpacity: 0.25,
            shadowRadius: 4,
            elevation: 5,
          }}
        >
          <Feather name="plus" size={22} color="#fff" />
        </Pressable>
      )}
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
  taskInputRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1,
  },
  taskInput: {
    flex: 1, height: 38, borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 10, fontSize: 14, fontFamily: 'Inter_400Regular',
  },
  taskAddBtn: {
    width: 38, height: 38, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },
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
  retryBtn: {
    marginTop: 12,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
  },
  statusBadge: {
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  statusText: {
    fontSize: 10,
    fontFamily: 'Inter_600SemiBold',
    textTransform: 'capitalize',
  },
});
