/**
 * Mobile Intelligence screen — /work/[id]/intelligence
 *
 * Mirrors the web /works/:id/intelligence page:
 *  • Completeness + gap metrics
 *  • Work stats strip with evidence-rescore button
 *  • Pipeline banner (stage label, advance readiness, findings)
 *  • Low-research coverage CTA
 *  • Gap list with "Track as task" and "Find sources" per-card actions
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  findNodeHandle,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { mobileFetch } from '@/lib/api';
import { ErrorScreen } from '@/components/OfflineBanner';
import { apiOrigin } from '@/lib/server';

// ─── severity colours ─────────────────────────────────────────────────────────

const SEV: Record<string, { bg: string; text: string; border: string }> = {
  critical: { bg: '#fee2e2', text: '#b91c1c', border: '#fca5a5' },
  high:     { bg: '#fef3c7', text: '#92400e', border: '#fcd34d' },
  medium:   { bg: '#e0f2fe', text: '#0369a1', border: '#7dd3fc' },
  low:      { bg: '#f0fdf4', text: '#166534', border: '#86efac' },
};

const WORKER_STAGES = new Set(['B0', 'B1', 'B2', 'B3', 'B4', 'B5']);

// ─── chapter kind colours (mirrors web KIND_COLOR) ────────────────────────────

const KIND_COLOR_RN: Record<string, { bg: string; text: string; border: string }> = {
  character:     { bg: '#f5f3ff', text: '#6d28d9', border: '#ddd6fe' },
  event:         { bg: '#eff6ff', text: '#1d4ed8', border: '#bfdbfe' },
  setting:       { bg: '#ecfdf5', text: '#065f46', border: '#a7f3d0' },
  theme:         { bg: '#fff1f2', text: '#9f1239', border: '#fecdd3' },
  foreshadowing: { bg: '#eef2ff', text: '#3730a3', border: '#c7d2fe' },
};

// ─── helpers ──────────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const colors = useColors();
  return (
    <View style={[s.section, { borderColor: colors.border }]}>
      <Text style={[s.sectionTitle, { color: colors.mutedForeground }]}>{title.toUpperCase()}</Text>
      {children}
    </View>
  );
}

function MetricRow({ label, value, unit }: { label: string; value: number; unit?: string }) {
  const colors = useColors();
  const pct = Math.min(100, Math.max(0, value));
  const barColor = pct >= 80 ? '#16a34a' : pct >= 50 ? '#d97706' : '#dc2626';
  return (
    <View style={s.metricRow}>
      <View style={s.metricHeader}>
        <Text style={[s.metricLabel, { color: colors.mutedForeground }]}>{label}</Text>
        <Text style={[s.metricValue, { color: barColor }]}>{Math.round(pct)}%</Text>
      </View>
      <View style={[s.barTrack, { backgroundColor: colors.muted }]}>
        <View style={[s.barFill, { width: `${pct}%` as any, backgroundColor: barColor }]} />
      </View>
    </View>
  );
}

function ActionButton({
  icon, label, color, onPress, disabled,
}: {
  icon: string; label: string; color?: string; onPress: () => void; disabled?: boolean;
}) {
  const colors = useColors();
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        s.actionBtn,
        { backgroundColor: colors.muted, opacity: disabled ? 0.4 : pressed ? 0.7 : 1 },
      ]}
    >
      <Feather name={icon as any} size={11} color={color ?? colors.primary} />
      <Text style={[s.actionBtnLabel, { color: color ?? colors.primary }]}>{label}</Text>
    </Pressable>
  );
}

// ─── chapter knowledge panel ─────────────────────────────────────────────────

function ChapterKnowledgePanel({
  knowledge,
  loading,
}: {
  knowledge: any[] | null;
  loading: boolean;
}) {
  const colors = useColors();

  if (loading) {
    return (
      <ActivityIndicator size="small" color={colors.primary} style={{ marginVertical: 8 }} />
    );
  }
  if (!knowledge || knowledge.length === 0) {
    return (
      <Text style={[s.emptyText, { color: colors.mutedForeground, fontSize: 12, marginTop: 0 }]}>
        No knowledge items extracted for this chapter yet.
      </Text>
    );
  }

  // Group by kind, preserve consistent order
  const KIND_ORDER = ['character', 'event', 'setting', 'theme', 'foreshadowing'];
  const grouped: Record<string, any[]> = {};
  for (const item of knowledge) {
    if (!grouped[item.kind]) grouped[item.kind] = [];
    grouped[item.kind].push(item);
  }
  const kinds = [
    ...KIND_ORDER.filter(k => grouped[k]),
    ...Object.keys(grouped).filter(k => !KIND_ORDER.includes(k)).sort(),
  ];

  return (
    <View style={{ gap: 8 }}>
      {kinds.map(kind => {
        const kc = KIND_COLOR_RN[kind] ?? { bg: '#f9fafb', text: '#374151', border: '#e5e7eb' };
        return (
          <View key={kind} style={{ gap: 4 }}>
            <Text style={[s.kindLabel, { color: kc.text }]}>{kind.toUpperCase()}</Text>
            {grouped[kind].map((item: any, i: number) => (
              <View key={i} style={[s.knowledgeItem, { backgroundColor: kc.bg, borderColor: kc.border }]}>
                <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6 }}>
                  <Text
                    style={[s.knowledgeText, { color: colors.foreground, flex: 1 }]}
                    numberOfLines={4}
                  >
                    {item.subject ? `${item.subject}: ${item.text}` : item.text}
                  </Text>
                  {item.confidence != null && (
                    <Text
                      style={[
                        s.confidenceBadge,
                        { color: kc.text, backgroundColor: kc.bg, borderColor: kc.border },
                      ]}
                    >
                      {Math.round(item.confidence * 100)}%
                    </Text>
                  )}
                </View>
              </View>
            ))}
          </View>
        );
      })}
    </View>
  );
}

// ─── pipeline banner ──────────────────────────────────────────────────────────

function PipelineBanner({
  pipeline, colors, onAdvance, advancing,
}: {
  pipeline: any; colors: any;
  onAdvance: () => void;
  advancing: boolean;
}) {
  const pipelineStage = pipeline?.status ?? null;
  const stageLabel = pipeline?.stage_label ?? pipelineStage ?? 'Unknown';
  const findings: any[] = pipeline?.open_findings ?? [];
  const artifact = pipeline?.stage_artifact ?? null;
  const artifactDone = artifact?.status === 'done';
  const isWorkerStage = WORKER_STAGES.has(pipelineStage ?? '');
  const needsArtifact = isWorkerStage && !artifactDone;
  const hasBlockers = findings.length > 0;
  const readyToAdvance = pipeline && !isWorkerStage && !hasBlockers;
  const readyWorker = pipeline && isWorkerStage && artifactDone && !hasBlockers;
  const canAdvance = readyToAdvance || readyWorker;

  const bannerColor = hasBlockers
    ? { bg: '#fef2f2', border: '#fca5a5', text: '#991b1b' }
    : canAdvance
    ? { bg: '#f0fdf4', border: '#86efac', text: '#166534' }
    : { bg: '#eff6ff', border: '#93c5fd', text: '#1d4ed8' };

  return (
    <View style={[s.pipelineBanner, { backgroundColor: bannerColor.bg, borderColor: bannerColor.border }]}>
      <View style={s.pipelineRow}>
        <Feather
          name={hasBlockers ? 'alert-triangle' : canAdvance ? 'check-circle' : 'layers'}
          size={14}
          color={bannerColor.text}
        />
        <Text style={[s.pipelineStage, { color: bannerColor.text }]}>
          Pipeline · {stageLabel}
        </Text>
      </View>
      {needsArtifact && (
        <Text style={[s.pipelineSub, { color: bannerColor.text }]}>
          Waiting for AI worker artifact to complete.
        </Text>
      )}
      {hasBlockers && (
        <>
          <Text style={[s.pipelineSub, { color: bannerColor.text }]}>
            {findings.length} finding{findings.length !== 1 ? 's' : ''} blocking advance:
          </Text>
          {findings.slice(0, 3).map((f: any, i: number) => (
            <Text key={i} style={[s.pipelineFinding, { color: bannerColor.text }]}>
              · {f.description}
            </Text>
          ))}
        </>
      )}
      {canAdvance && (
        <View style={s.pipelineAdvanceRow}>
          <Text style={[s.pipelineSub, { color: bannerColor.text, flex: 1 }]}>
            Ready to advance to the next stage.
          </Text>
          <Pressable
            onPress={onAdvance}
            disabled={advancing}
            style={({ pressed }) => [
              s.advanceBtn,
              { opacity: advancing || pressed ? 0.6 : 1 },
            ]}
          >
            {advancing
              ? <ActivityIndicator size={11} color="#fff" />
              : <Feather name="chevrons-right" size={12} color="#fff" />}
            <Text style={s.advanceBtnLabel}>
              {advancing ? 'Advancing…' : 'Advance'}
            </Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

// ─── main screen ─────────────────────────────────────────────────────────────

export default function WorkIntelligenceScreen() {
  const colors   = useColors();
  const insets   = useSafeAreaInsets();
  const router   = useRouter();
  const isWeb    = Platform.OS === 'web';
  const { id, chapterId: targetChapterId } = useLocalSearchParams<{ id: string; chapterId?: string }>();
  const navigation = useNavigation();
  const domain = apiOrigin();
  const base     = `${domain}/api`;

  // Refs for scroll-to-chapter deep-link
  const scrollViewRef   = useRef<ScrollView>(null);
  const chapterRowRefs  = useRef<Record<string, View | null>>({});

  const [completeness, setCompleteness] = useState<any>(null);
  const [gaps,         setGaps]         = useState<any>(null);
  const [stats,        setStats]        = useState<any>(null);
  const [pipeline,     setPipeline]     = useState<any>(undefined); // undefined = not fetched
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState(false);

  // Per-gap tracking
  const [trackedGaps,  setTrackedGaps]  = useState<Set<string>>(new Set());
  const [trackingGap,  setTrackingGap]  = useState<string | null>(null);

  // Rescore state
  const [rescoring,    setRescoring]    = useState(false);
  const [lastRescored, setLastRescored] = useState<string | null>(null);

  // Pipeline advance state
  const [advancing, setAdvancing] = useState(false);

  // Chapter structure + per-chapter knowledge (lazy-loaded on expand)
  const [chapters,               setChapters]               = useState<any>(null);
  const [expandedChapters,       setExpandedChapters]       = useState<Set<string>>(new Set());
  const [chapterKnowledge,       setChapterKnowledge]       = useState<Record<string, any[]>>({});
  const [loadingChapterKnowledge,setLoadingChapterKnowledge]= useState<Record<string, boolean>>({});
  // Track which chapters we've already started a fetch for (avoids duplicate requests)
  const fetchedChaptersRef = React.useRef<Set<string>>(new Set());

  // ── Chapter search / filter ────────────────────────────────────────────────
  const [chapterQuery, setChapterQuery] = useState('');
  const normalizedQuery = chapterQuery.trim().toLowerCase();

  // Tracks how many background chapter-knowledge fetches are currently in
  // flight so the search bar can show an ActivityIndicator while they run.
  const [searchFetchingCount, setSearchFetchingCount] = useState(0);

  // When the query changes, auto-expand any chapter whose already-loaded
  // knowledge contains a match so the user immediately sees the items.
  useEffect(() => {
    if (!normalizedQuery) return;
    const toExpand = new Set<string>();
    for (const [chId, items] of Object.entries(chapterKnowledge)) {
      if (items.some(
        (item: any) =>
          (item.text    ?? '').toLowerCase().includes(normalizedQuery) ||
          (item.subject ?? '').toLowerCase().includes(normalizedQuery),
      )) {
        toExpand.add(chId);
      }
    }
    if (toExpand.size > 0) {
      setExpandedChapters(prev => {
        const next = new Set(prev);
        toExpand.forEach(chId => next.add(chId));
        return next;
      });
    }
  }, [normalizedQuery, chapterKnowledge]);

  // ── Debounced background fetch for search ──────────────────────────────────
  // When the user types a query and pauses for 400 ms, fetch knowledge for
  // every chapter that hasn't been loaded yet in parallel so the filter can
  // reflect the entire book, not just already-expanded chapters.
  useEffect(() => {
    if (!normalizedQuery || !chapters) return;

    const timer = setTimeout(() => {
      // Collect every chapter id across all documents that hasn't been fetched
      const allChapterIds: string[] = [];
      for (const doc of (chapters.documents as any[])) {
        for (const ch of (doc.chapters as any[])) {
          if (!fetchedChaptersRef.current.has(ch.id)) {
            allChapterIds.push(ch.id);
          }
        }
      }
      if (allChapterIds.length === 0) return;

      // Claim slots up-front so toggleChapter knows they're in-flight
      for (const cid of allChapterIds) {
        fetchedChaptersRef.current.add(cid);
      }
      setSearchFetchingCount(prev => prev + allChapterIds.length);

      // Fire all fetches in parallel; update state as each resolves
      for (const cid of allChapterIds) {
        setLoadingChapterKnowledge(prev => ({ ...prev, [cid]: true }));
        mobileFetch(`${base}/works/${id}/chapters/${cid}/knowledge`)
          .then(res => res.ok ? res.json() : null)
          .then(data => {
            setChapterKnowledge(prev => ({ ...prev, [cid]: data?.knowledge ?? [] }));
          })
          .catch(() => {
            setChapterKnowledge(prev => ({ ...prev, [cid]: [] }));
          })
          .finally(() => {
            setLoadingChapterKnowledge(prev => ({ ...prev, [cid]: false }));
            setSearchFetchingCount(prev => Math.max(0, prev - 1));
          });
      }
    }, 400);

    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [normalizedQuery, chapters]);

  // Returns true when a chapter row should be visible for the current query.
  // While background fetches are in flight (searchFetchingCount > 0) every
  // chapter stays visible so we don't prematurely hide chapters whose content
  // hasn't loaded yet. Hiding only happens once all fetches have resolved.
  const chapterMatchesQuery = (ch: any): boolean => {
    if (!normalizedQuery) return true;
    if (searchFetchingCount > 0) return true; // defer until all knowledge is loaded
    if ((ch.title ?? '').toLowerCase().includes(normalizedQuery)) return true;
    const items = chapterKnowledge[ch.id];
    if (items) {
      return items.some(
        (item: any) =>
          (item.text    ?? '').toLowerCase().includes(normalizedQuery) ||
          (item.subject ?? '').toLowerCase().includes(normalizedQuery),
      );
    }
    return false;
  };

  // Filters knowledge items within an expanded panel to matching items only.
  const filterKnowledge = (items: any[]): any[] => {
    if (!normalizedQuery) return items;
    return items.filter(
      (item: any) =>
        (item.text    ?? '').toLowerCase().includes(normalizedQuery) ||
        (item.subject ?? '').toLowerCase().includes(normalizedQuery),
    );
  };

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [cRes, gRes, stRes, plRes, chRes] = await Promise.all([
        mobileFetch(`${base}/works/${id}/completeness`),
        mobileFetch(`${base}/works/${id}/gaps`),
        mobileFetch(`${base}/works/${id}/stats`),
        mobileFetch(`${base}/works/${id}/pipeline`),
        mobileFetch(`${base}/works/${id}/chapters`),
      ]);
      const [cData, gData, stData, plData, chData] = await Promise.all([
        cRes.ok  ? cRes.json()  : null,
        gRes.ok  ? gRes.json()  : null,
        stRes.ok ? stRes.json() : null,
        plRes.ok ? plRes.json() : null,
        chRes.ok ? chRes.json() : null,
      ]);
      setCompleteness(cData);
      setGaps(gData);
      setStats(stData);
      setPipeline(plData?.pipeline ?? null);
      setChapters(chData);
      // Reset chapter expansion on full refresh so stale knowledge is re-fetched
      setExpandedChapters(new Set());
      fetchedChaptersRef.current.clear();
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [id, base]);

  useEffect(() => {
    navigation.setOptions({ title: 'Intelligence' });
    fetchAll();
  }, [fetchAll, navigation]);

  // ── Track gap as task ──────────────────────────────────────────────────────
  const handleTrackGap = async (gapTitle: string) => {
    if (trackedGaps.has(gapTitle) || trackingGap === gapTitle) return;
    setTrackingGap(gapTitle);
    try {
      const res = await mobileFetch(`${base}/works/${id}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: gapTitle }),
      });
      if (!res.ok) throw new Error('failed');
      setTrackedGaps(prev => new Set(prev).add(gapTitle));
    } catch {
      Alert.alert('Could not create task', 'Check your connection and try again.');
    } finally {
      setTrackingGap(null);
    }
  };

  // ── Evidence rescore ───────────────────────────────────────────────────────
  const handleRescore = async () => {
    if (rescoring) return;
    setRescoring(true);
    try {
      const res = await mobileFetch(`${base}/works/${id}/evidence/rescore`, { method: 'POST' });
      if (!res.ok) throw new Error('failed');
      const data = await res.json();
      const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      setLastRescored(`${data.rescored_count ?? 0} items at ${ts}`);
    } catch {
      Alert.alert('Rescore failed', 'Check your connection and try again.');
    } finally {
      setRescoring(false);
    }
  };

  // ── Chapter expand/collapse (lazy-loads knowledge on first open) ───────────
  const toggleChapter = async (chapterId: string) => {
    setExpandedChapters(prev => {
      const next = new Set(prev);
      if (next.has(chapterId)) { next.delete(chapterId); } else { next.add(chapterId); }
      return next;
    });
    if (fetchedChaptersRef.current.has(chapterId)) return; // already fetched or in flight
    fetchedChaptersRef.current.add(chapterId);
    setLoadingChapterKnowledge(prev => ({ ...prev, [chapterId]: true }));
    try {
      const res = await mobileFetch(`${base}/works/${id}/chapters/${chapterId}/knowledge`);
      const data = res.ok ? await res.json() : null;
      setChapterKnowledge(prev => ({ ...prev, [chapterId]: data?.knowledge ?? [] }));
    } catch {
      setChapterKnowledge(prev => ({ ...prev, [chapterId]: [] }));
    } finally {
      setLoadingChapterKnowledge(prev => ({ ...prev, [chapterId]: false }));
    }
  };

  // ── Chapter deep-link: auto-expand + scroll when chapterId is in route ────
  // Runs once after chapters load (or immediately if they're already loaded).
  // Expands the target chapter, kicks off its knowledge fetch if needed, then
  // scrolls the ScrollView to that row after a short layout delay.
  useEffect(() => {
    if (!targetChapterId || !chapters) return;

    // Expand the target chapter
    setExpandedChapters(prev => {
      if (prev.has(targetChapterId)) return prev;
      const next = new Set(prev);
      next.add(targetChapterId);
      return next;
    });

    // Fetch knowledge for this chapter if it hasn't been loaded yet
    if (!fetchedChaptersRef.current.has(targetChapterId)) {
      fetchedChaptersRef.current.add(targetChapterId);
      setLoadingChapterKnowledge(prev => ({ ...prev, [targetChapterId]: true }));
      mobileFetch(`${base}/works/${id}/chapters/${targetChapterId}/knowledge`)
        .then(r => r.ok ? r.json() : null)
        .then(data => setChapterKnowledge(prev => ({ ...prev, [targetChapterId]: data?.knowledge ?? [] })))
        .catch(() => setChapterKnowledge(prev => ({ ...prev, [targetChapterId]: [] })))
        .finally(() => setLoadingChapterKnowledge(prev => ({ ...prev, [targetChapterId]: false })));
    }

    // Wait one frame for the expanded row to render, then measure + scroll
    const t = setTimeout(() => {
      const row = chapterRowRefs.current[targetChapterId];
      const sv  = scrollViewRef.current;
      if (!row || !sv) return;
      const nodeHandle = findNodeHandle(sv);
      if (nodeHandle == null) return;
      row.measureLayout(
        nodeHandle,
        (_x, y) => sv.scrollTo({ y: Math.max(0, y - 80), animated: true }),
        () => { /* unmeasurable — ignore */ },
      );
    }, 350);

    return () => clearTimeout(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetChapterId, chapters]);

  // ── Pipeline advance ───────────────────────────────────────────────────────
  const handleAdvance = async () => {
    if (advancing) return;
    setAdvancing(true);
    // Optimistic stage label update while the request is in flight
    try {
      const res = await mobileFetch(`${base}/works/${id}/pipeline/advance`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        // Refresh pipeline state from server response
        if (data?.pipeline) setPipeline(data.pipeline);
        else fetchAll();                       // fallback: re-fetch all
      } else {
        // 409 = gate failure or blockers
        const reason: string =
          data?.detail ??
          (data?.blockers as any[])?.map((b: any) => b.description ?? String(b)).join('\n') ??
          'Advance blocked — check the pipeline findings.';
        Alert.alert('Cannot advance', reason);
      }
    } catch {
      Alert.alert('Advance failed', 'Check your connection and try again.');
    } finally {
      setAdvancing(false);
    }
  };

  const topPad = isWeb ? 67 : insets.top + 44;

  if (loading) {
    return (
      <View style={[s.centered, { paddingTop: topPad }]}>
        <ActivityIndicator color={colors.primary} size="large" />
        <Text style={[s.emptyText, { color: colors.mutedForeground }]}>Analysing…</Text>
      </View>
    );
  }

  if (error || (!completeness && !gaps)) {
    return (
      <ErrorScreen
        message="Could not load intelligence"
        detail="Make sure the server is reachable and try again."
        onRetry={fetchAll}
      />
    );
  }

  // ── Derived data ───────────────────────────────────────────────────────────
  const dims: any[]    = completeness?.dimensions ?? [];
  const overallScore   = completeness?.overall ?? 0;
  const allGaps: any[] = gaps?.gaps ?? [];
  const coveragePct    = gaps?.coverage_pct ?? null;

  const totalDocs     = Object.values<number>(stats?.documents_by_kind   ?? {}).reduce((a, b) => a + b, 0);
  const totalKn       = Object.values<number>(stats?.knowledge_by_kind   ?? {}).reduce((a, b) => a + b, 0);
  const readyDocs     = stats?.documents_by_readiness?.['ready'] ?? 0;
  const pendingTasks  = stats?.pending_task_count ?? 0;

  const highGaps   = allGaps.filter(g => g.severity === 'high');
  const medGaps    = allGaps.filter(g => g.severity === 'medium');
  const lowGaps    = allGaps.filter(g => g.severity === 'low');

  const researchDim = dims.find(d => d.name === 'research');
  const researchLow = researchDim != null && researchDim.score < 40;

  return (
    <ScrollView
      ref={scrollViewRef}
      style={{ flex: 1, backgroundColor: colors.background }}
      contentContainerStyle={{ paddingTop: topPad + 16, paddingBottom: 40, paddingHorizontal: 16 }}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl refreshing={loading} onRefresh={fetchAll} tintColor={colors.primary} />
      }
    >
      {/* ── Stats strip ──────────────────────────────────────────────────── */}
      {stats && (
        <View style={[s.statsStrip, { borderColor: colors.border }]}>
          <View style={s.statCell}>
            <Text style={[s.statValue, { color: colors.foreground }]}>{totalDocs}</Text>
            <Text style={[s.statLabel, { color: colors.mutedForeground }]}>docs ({readyDocs} ready)</Text>
          </View>
          <View style={[s.statDivider, { backgroundColor: colors.border }]} />
          {/* Knowledge cell with rescore button */}
          <View style={[s.statCell, { flex: 1.4 }]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Text style={[s.statValue, { color: colors.foreground }]}>{totalKn}</Text>
              <Pressable
                onPress={handleRescore}
                disabled={rescoring}
                style={({ pressed }) => [s.rescoreBtn, { opacity: rescoring || pressed ? 0.6 : 1 }]}
              >
                {rescoring
                  ? <ActivityIndicator size={10} color={colors.primary} />
                  : <Feather name="zap" size={11} color={colors.primary} />}
                <Text style={[s.rescoreBtnLabel, { color: colors.primary }]}>Rescore</Text>
              </Pressable>
            </View>
            <Text style={[s.statLabel, { color: colors.mutedForeground }]}>
              {lastRescored ? `rescored ${lastRescored}` : 'knowledge items'}
            </Text>
          </View>
          <View style={[s.statDivider, { backgroundColor: colors.border }]} />
          <View style={s.statCell}>
            <Text style={[s.statValue, { color: pendingTasks > 0 ? '#d97706' : colors.foreground }]}>
              {pendingTasks}
            </Text>
            <Text style={[s.statLabel, { color: colors.mutedForeground }]}>tasks</Text>
          </View>
        </View>
      )}

      {/* ── Pipeline banner ───────────────────────────────────────────────── */}
      {pipeline !== undefined && (
        pipeline ? (
          <PipelineBanner pipeline={pipeline} colors={colors} onAdvance={handleAdvance} advancing={advancing} />
        ) : (
          <View style={[s.pipelineBanner, { backgroundColor: colors.muted + '30', borderColor: colors.border }]}>
            <Text style={[s.pipelineSub, { color: colors.mutedForeground }]}>
              No production pipeline started for this Work yet.
            </Text>
          </View>
        )
      )}

      {/* ── Low research CTA ──────────────────────────────────────────────── */}
      {researchLow && (
        <View style={[s.researchBanner, { borderColor: '#fcd34d', backgroundColor: '#fefce8' }]}>
          <Feather name="trending-up" size={14} color="#92400e" />
          <Text style={[s.researchBannerText, { color: '#92400e', flex: 1 }]}>
            Research coverage is low ({Math.round(researchDim!.score)}%). Import more primary sources.
          </Text>
        </View>
      )}

      {/* ── Graph entry point ─────────────────────────────────────────────── */}
      <Pressable
        onPress={() => router.push(`/graph?work_id=${id}` as any)}
        style={({ pressed }) => [
          s.graphBtn,
          { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
        ]}
      >
        <View style={[s.graphBtnIcon, { backgroundColor: colors.primary + '18' }]}>
          <Feather name="share-2" size={16} color={colors.primary} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={[s.graphBtnTitle, { color: colors.foreground }]}>Knowledge Graph</Text>
          <Text style={[s.graphBtnSub, { color: colors.mutedForeground }]}>
            Explore concept relationships visually
          </Text>
        </View>
        <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
      </Pressable>

      {/* ── Completeness ──────────────────────────────────────────────────── */}
      <Section title="Completeness">
        <MetricRow label="Overall" value={overallScore} />
        {dims.map((d: any) => (
          <MetricRow key={d.name} label={d.label ?? d.name} value={d.score ?? 0} />
        ))}
        {completeness?.summary ? (
          <Text style={[s.summary, { color: colors.mutedForeground }]}>
            {completeness.summary}
          </Text>
        ) : null}
        {completeness?.evaluated_at && (
          <Text style={[s.evalAt, { color: colors.mutedForeground + '80' }]}>
            Evaluated {new Date(completeness.evaluated_at).toLocaleString()}
          </Text>
        )}
      </Section>

      {/* ── Research Gaps ─────────────────────────────────────────────────── */}
      <Section title={`Research Gaps${allGaps.length ? ` (${allGaps.length})` : ''}`}>
        {coveragePct != null && (
          <Text style={[s.metaLine, { color: colors.mutedForeground }]}>
            Coverage: {coveragePct}%
          </Text>
        )}
        {allGaps.length === 0 ? (
          <Text style={[s.emptyText, { color: colors.mutedForeground }]}>No gaps detected</Text>
        ) : (
          [
            { label: 'Critical',      items: allGaps.filter(g => g.severity === 'critical'), sev: 'critical' },
            { label: 'High priority', items: highGaps,  sev: 'high'   },
            { label: 'Medium',        items: medGaps,   sev: 'medium' },
            { label: 'Low',           items: lowGaps,   sev: 'low'    },
          ]
            .filter(g => g.items.length > 0)
            .map(({ label, items, sev }) => (
              <View key={sev} style={{ gap: 8 }}>
                <Text style={[s.sevGroupLabel, { color: colors.mutedForeground }]}>
                  {label.toUpperCase()} · {items.length}
                </Text>
                {items.map((g: any, i: number) => {
                  const gc = SEV[sev] ?? SEV.medium;
                  const isHighMed = sev === 'high' || sev === 'medium';
                  const alreadyTracked = trackedGaps.has(g.title);
                  const isTracking = trackingGap === g.title;

                  return (
                    <View
                      key={i}
                      style={[s.gapCard, { borderColor: gc.border, backgroundColor: gc.bg }]}
                    >
                      <View style={s.gapCardHeader}>
                        <View style={[s.sevBadge, { backgroundColor: gc.bg }]}>
                          <Text style={[s.sevText, { color: gc.text }]}>{sev}</Text>
                        </View>
                        <Text style={[s.gapTitle, { color: colors.foreground, flex: 1 }]} numberOfLines={2}>
                          {g.title ?? g.kind}
                        </Text>
                      </View>
                      {g.description ? (
                        <Text style={[s.gapDesc, { color: colors.mutedForeground }]} numberOfLines={4}>
                          {g.description}
                        </Text>
                      ) : null}
                      {g.metadata?.chapter_title && (
                        <Text style={[s.gapChapter, { color: colors.mutedForeground }]}>
                          Chapter: {g.metadata.chapter_title}
                        </Text>
                      )}

                      {/* Action buttons — only for high/medium */}
                      {isHighMed && (
                        <View style={s.gapActions}>
                          <ActionButton
                            icon="search"
                            label="Find sources"
                            onPress={() => router.push(`/work/${id}?tab=gaps` as any)}
                          />
                          {alreadyTracked ? (
                            <View style={[s.actionBtn, { backgroundColor: '#f0fdf4' }]}>
                              <Feather name="check-square" size={11} color="#166534" />
                              <Text style={[s.actionBtnLabel, { color: '#166534' }]}>Task created</Text>
                            </View>
                          ) : (
                            <ActionButton
                              icon={isTracking ? 'loader' : 'plus-square'}
                              label={isTracking ? 'Adding…' : 'Track as task'}
                              color="#d97706"
                              disabled={isTracking}
                              onPress={() => handleTrackGap(g.title)}
                            />
                          )}
                        </View>
                      )}
                    </View>
                  );
                })}
              </View>
            ))
        )}
        {gaps?.suggested_queries?.length > 0 && (
          <View style={{ marginTop: 8 }}>
            <Text style={[s.sevGroupLabel, { color: colors.mutedForeground, marginBottom: 6 }]}>
              SUGGESTED SEARCHES
            </Text>
            {gaps.suggested_queries.map((q: string, i: number) => (
              <Pressable
                key={i}
                onPress={() => router.push(`/work/${id}?tab=brainstorm&q=${encodeURIComponent(q)}` as any)}
                style={({ pressed }) => [
                  s.suggestedQuery,
                  { borderColor: colors.border, backgroundColor: colors.card, opacity: pressed ? 0.7 : 1 },
                ]}
              >
                <Feather name="search" size={12} color={colors.primary} />
                <Text style={[s.suggestedQueryText, { color: colors.foreground }]} numberOfLines={2}>
                  {q}
                </Text>
              </Pressable>
            ))}
          </View>
        )}
      </Section>

      {/* ── Chapter Structure ─────────────────────────────────────────────── */}
      {chapters && chapters.total_chapters > 0 && (
        <Section title={`Chapter Structure (${chapters.total_chapters})`}>

          {/* Search bar */}
          <View style={[s.chapterSearchRow, { borderColor: colors.border, backgroundColor: colors.muted + '60' }]}>
            <Feather name="search" size={13} color={colors.mutedForeground} />
            <TextInput
              style={[s.chapterSearchInput, { color: colors.foreground }]}
              placeholder="Search characters, events, settings…"
              placeholderTextColor={colors.mutedForeground}
              value={chapterQuery}
              onChangeText={setChapterQuery}
              returnKeyType="search"
              clearButtonMode="while-editing"
              autoCorrect={false}
            />
            {searchFetchingCount > 0 && (
              <ActivityIndicator size="small" color={colors.primary} style={{ marginRight: 2 }} />
            )}
            {chapterQuery.length > 0 && searchFetchingCount === 0 && (
              <Pressable onPress={() => setChapterQuery('')} hitSlop={8}>
                <Feather name="x" size={13} color={colors.mutedForeground} />
              </Pressable>
            )}
          </View>

          {/* Chapter list */}
          {(chapters.documents as any[]).map((doc: any) => {
            // Filter chapters for this document
            const visibleChapters = (doc.chapters as any[]).filter(chapterMatchesQuery);
            if (visibleChapters.length === 0) return null;
            return (
              <View key={doc.doc_id} style={{ gap: 6 }}>
                {/* Only show the doc label when there are multiple source documents */}
                {chapters.documents.length > 1 && (
                  <Text
                    style={[s.docGroupLabel, { color: colors.mutedForeground }]}
                    numberOfLines={1}
                  >
                    {doc.doc_title}
                  </Text>
                )}
                {visibleChapters.map((ch: any) => {
                  const isExpanded   = expandedChapters.has(ch.id);
                  const hasKnowledge = ch.knowledge_count > 0;
                  const knData       = chapterKnowledge[ch.id] ?? null;
                  const knLoading    = loadingChapterKnowledge[ch.id] ?? false;
                  // When a query is active, filter the displayed knowledge items too
                  const visibleKn    = knData ? filterKnowledge(knData) : knData;

                  return (
                    <View key={ch.id} ref={v => { chapterRowRefs.current[ch.id] = v; }}>
                      <Pressable
                        onPress={hasKnowledge ? () => toggleChapter(ch.id) : undefined}
                        style={({ pressed }) => [
                          s.chapterRow,
                          {
                            borderColor: isExpanded ? colors.primary + '40' : colors.border,
                            backgroundColor: isExpanded ? colors.primary + '08' : 'transparent',
                            opacity: pressed ? 0.7 : 1,
                          },
                        ]}
                      >
                        {/* Sequence number badge */}
                        <View style={[s.chapterSeqBadge, { backgroundColor: colors.muted }]}>
                          <Text style={[s.chapterSeq, { color: colors.mutedForeground }]}>
                            {ch.seq + 1}
                          </Text>
                        </View>

                        {/* Title */}
                        <Text
                          style={[s.chapterTitle, { color: colors.foreground }]}
                          numberOfLines={2}
                        >
                          {ch.title || `Chapter ${ch.seq + 1}`}
                        </Text>

                        {/* Knowledge count badge + chevron (or dash if empty) */}
                        {hasKnowledge ? (
                          <>
                            <View
                              style={[
                                s.knowledgeCountBadge,
                                {
                                  backgroundColor: colors.primary + '18',
                                  borderColor: colors.primary + '40',
                                },
                              ]}
                            >
                              <Text style={[s.knowledgeCountText, { color: colors.primary }]}>
                                {normalizedQuery && visibleKn != null
                                  ? `${visibleKn.length}/${ch.knowledge_count}`
                                  : ch.knowledge_count}
                              </Text>
                            </View>
                            <Feather
                              name={isExpanded ? 'chevron-up' : 'chevron-down'}
                              size={14}
                              color={colors.mutedForeground}
                            />
                          </>
                        ) : (
                          <Text style={[s.noKnowledge, { color: colors.mutedForeground }]}>—</Text>
                        )}
                      </Pressable>

                      {/* Expanded knowledge panel */}
                      {isExpanded && hasKnowledge && (
                        <View
                          style={[
                            s.chapterKnowledgeArea,
                            { borderColor: colors.primary + '40' },
                          ]}
                        >
                          <ChapterKnowledgePanel knowledge={visibleKn} loading={knLoading} />
                        </View>
                      )}
                    </View>
                  );
                })}
              </View>
            );
          })}

          {/* "N chapters hidden" note — only shown once all background fetches
              resolve so users understand why the list is shorter */}
          {normalizedQuery !== '' && searchFetchingCount === 0 && (() => {
            const allChapters = (chapters.documents as any[]).flatMap(
              (doc: any) => doc.chapters as any[],
            );
            const hiddenCount = allChapters.filter((ch: any) => !chapterMatchesQuery(ch)).length;
            if (hiddenCount === 0) return null;
            return (
              <View style={{ alignItems: 'center', paddingVertical: 6 }}>
                <Text style={{ fontSize: 12, color: colors.mutedForeground, fontFamily: 'Inter_400Regular' }}>
                  {hiddenCount} chapter{hiddenCount !== 1 ? 's' : ''} hidden
                </Text>
              </View>
            );
          })()}

          {/* No-results state — only shown once all fetches have resolved */}
          {normalizedQuery !== '' && searchFetchingCount === 0 &&
            (chapters.documents as any[]).every(
              (doc: any) => (doc.chapters as any[]).filter(chapterMatchesQuery).length === 0,
            ) && (
            <View style={{ alignItems: 'center', paddingVertical: 16, gap: 6 }}>
              <Feather name="search" size={20} color={colors.mutedForeground} />
              <Text style={[s.emptyText, { color: colors.mutedForeground, fontSize: 13, marginTop: 0 }]}>
                No chapters match "{chapterQuery}"
              </Text>
              <Pressable onPress={() => setChapterQuery('')} hitSlop={8}>
                <Text style={{ fontSize: 12, color: colors.primary, fontFamily: 'Inter_500Medium' }}>
                  Clear search
                </Text>
              </Pressable>
            </View>
          )}

        </Section>
      )}
    </ScrollView>
  );
}

// ─── styles ──────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  centered:     { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  emptyText:    { fontSize: 14, fontFamily: 'Inter_400Regular', marginTop: 4 },
  section: {
    marginBottom: 20,
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
    gap: 10,
  },
  sectionTitle: {
    fontSize: 10,
    fontFamily: 'Inter_600SemiBold',
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  summary:  { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 18, marginTop: 4 },
  evalAt:   { fontSize: 10, fontFamily: 'Inter_400Regular', textAlign: 'right' },
  metaLine: { fontSize: 12, fontFamily: 'Inter_400Regular' },

  // Stats strip
  statsStrip: {
    flexDirection: 'row',
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginBottom: 16,
    gap: 0,
    alignItems: 'center',
  },
  statCell: { flex: 1, alignItems: 'center', gap: 2 },
  statDivider: { width: StyleSheet.hairlineWidth, height: 32, marginHorizontal: 8 },
  statValue: { fontSize: 20, fontFamily: 'Inter_700Bold' },
  statLabel: { fontSize: 10, fontFamily: 'Inter_400Regular', textAlign: 'center' },
  rescoreBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  rescoreBtnLabel: { fontSize: 10, fontFamily: 'Inter_600SemiBold' },

  // Pipeline banner
  pipelineBanner: {
    borderWidth: 1, borderRadius: 10,
    padding: 12, marginBottom: 16, gap: 4,
  },
  pipelineRow:    { flexDirection: 'row', alignItems: 'center', gap: 6 },
  pipelineStage:  { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  pipelineSub:    { fontSize: 12, fontFamily: 'Inter_400Regular', marginLeft: 20 },
  pipelineFinding:{ fontSize: 11, fontFamily: 'Inter_400Regular', marginLeft: 20, marginTop: 2 },
  pipelineAdvanceRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4, marginLeft: 20,
  },
  advanceBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: '#16a34a', borderRadius: 6,
    paddingHorizontal: 10, paddingVertical: 5, minWidth: 80, justifyContent: 'center',
  },
  advanceBtnLabel: { fontSize: 12, fontFamily: 'Inter_600SemiBold', color: '#fff' },

  // Research banner
  researchBanner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    borderWidth: 1, borderRadius: 10, padding: 12, marginBottom: 16,
  },
  researchBannerText: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 18 },

  // Metric rows
  metricRow:    { gap: 4 },
  metricHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  metricLabel:  { fontSize: 12, fontFamily: 'Inter_400Regular' },
  metricValue:  { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  barTrack:     { height: 4, borderRadius: 2, overflow: 'hidden' },
  barFill:      { height: 4, borderRadius: 2 },

  // Gap cards
  sevGroupLabel: { fontSize: 9, fontFamily: 'Inter_600SemiBold', letterSpacing: 0.8 },
  gapCard: {
    borderWidth: 1, borderRadius: 10, padding: 10, gap: 6,
  },
  gapCardHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  gapTitle:  { fontSize: 13, fontFamily: 'Inter_500Medium' },
  gapDesc:   { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  gapChapter:{ fontSize: 10, fontFamily: 'Inter_400Regular', fontStyle: 'italic' },
  gapActions:{ flexDirection: 'row', gap: 6, marginTop: 4, flexWrap: 'wrap' },

  sevBadge: { borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2, alignSelf: 'flex-start' },
  sevText:  { fontSize: 10, fontFamily: 'Inter_600SemiBold', textTransform: 'capitalize' },

  // Action buttons inside gap cards
  actionBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 8, paddingVertical: 5, borderRadius: 6,
  },
  actionBtnLabel: { fontSize: 11, fontFamily: 'Inter_600SemiBold' },

  // Suggested queries
  suggestedQuery: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderWidth: 1, borderRadius: 8, padding: 10, marginBottom: 6,
  },
  suggestedQueryText: { fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1 },

  // Graph entry card
  graphBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    borderWidth: 1, borderRadius: 12, padding: 14, marginBottom: 20,
  },
  graphBtnIcon:  { width: 38, height: 38, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  graphBtnTitle: { fontSize: 14, fontFamily: 'Inter_600SemiBold', marginBottom: 2 },
  graphBtnSub:   { fontSize: 12, fontFamily: 'Inter_400Regular' },

  // Chapter search bar
  chapterSearchRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 10, paddingVertical: 7,
    marginBottom: 4,
  },
  chapterSearchInput: {
    flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular',
    paddingVertical: 0, // remove default Android padding
  },

  // Chapter structure
  docGroupLabel: {
    fontSize: 10, fontFamily: 'Inter_600SemiBold', letterSpacing: 0.4,
    marginBottom: 2, marginTop: 4,
  },
  chapterRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 9, paddingHorizontal: 10,
    borderRadius: 8, borderWidth: 1,
  },
  chapterSeqBadge: {
    width: 24, height: 24, borderRadius: 6,
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  chapterSeq:   { fontSize: 11, fontFamily: 'Inter_600SemiBold' },
  chapterTitle: { fontSize: 13, fontFamily: 'Inter_500Medium', lineHeight: 18, flex: 1 },
  knowledgeCountBadge: {
    borderWidth: 1, borderRadius: 10,
    paddingHorizontal: 6, paddingVertical: 2, flexShrink: 0,
  },
  knowledgeCountText: { fontSize: 11, fontFamily: 'Inter_600SemiBold' },
  noKnowledge: { fontSize: 12, fontFamily: 'Inter_400Regular', flexShrink: 0 },
  chapterKnowledgeArea: {
    borderWidth: 1, borderTopWidth: 0,
    borderBottomLeftRadius: 8, borderBottomRightRadius: 8,
    padding: 10, gap: 6,
  },

  // Knowledge items inside expanded chapter
  kindLabel: {
    fontSize: 9, fontFamily: 'Inter_600SemiBold', letterSpacing: 0.8,
    marginBottom: 2, marginTop: 2,
  },
  knowledgeItem: {
    borderWidth: 1, borderRadius: 6, padding: 8,
  },
  knowledgeText: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  confidenceBadge: {
    fontSize: 9, fontFamily: 'Inter_600SemiBold',
    borderWidth: 1, borderRadius: 4,
    paddingHorizontal: 4, paddingVertical: 2,
    flexShrink: 0, textAlign: 'center',
  },
});
