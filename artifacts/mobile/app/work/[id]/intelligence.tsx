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
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { mobileFetch } from '@/lib/api';
import { ErrorScreen } from '@/components/OfflineBanner';

// ─── severity colours ─────────────────────────────────────────────────────────

const SEV: Record<string, { bg: string; text: string; border: string }> = {
  critical: { bg: '#fee2e2', text: '#b91c1c', border: '#fca5a5' },
  high:     { bg: '#fef3c7', text: '#92400e', border: '#fcd34d' },
  medium:   { bg: '#e0f2fe', text: '#0369a1', border: '#7dd3fc' },
  low:      { bg: '#f0fdf4', text: '#166534', border: '#86efac' },
};

const WORKER_STAGES = new Set(['B0', 'B1', 'B2', 'B3', 'B4', 'B5']);

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
  const { id }   = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const domain   = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const base     = `https://${domain}/api`;

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

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [cRes, gRes, stRes, plRes] = await Promise.all([
        mobileFetch(`${base}/works/${id}/completeness`),
        mobileFetch(`${base}/works/${id}/gaps`),
        mobileFetch(`${base}/works/${id}/stats`),
        mobileFetch(`${base}/works/${id}/pipeline`),
      ]);
      const [cData, gData, stData, plData] = await Promise.all([
        cRes.ok  ? cRes.json()  : null,
        gRes.ok  ? gRes.json()  : null,
        stRes.ok ? stRes.json() : null,
        plRes.ok ? plRes.json() : null,
      ]);
      setCompleteness(cData);
      setGaps(gData);
      setStats(stData);
      setPipeline(plData?.pipeline ?? null);
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
});
