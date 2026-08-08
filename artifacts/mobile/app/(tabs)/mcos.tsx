/**
 * MCOS — Model Calibration & Observation System.
 *
 * Shows benchmark suites, recent run results, and active regressions.
 * Lets users trigger individual or full benchmark runs.
 */
import React, { useState } from 'react';
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
import * as Haptics from 'expo-haptics';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { font, fontSerif } from '@/lib/typography';

// ── Types ──────────────────────────────────────────────────────────────────────

interface Benchmark {
  id: string;
  name: string;
  description?: string | null;
  prompt_count?: number;
  last_run_at?: string | null;
  last_pass_rate?: number | null;
  last_status?: string | null;
}

interface Regression {
  id: string;
  benchmark_id: string;
  benchmark_name?: string;
  pass_rate_before: number;
  pass_rate_after: number;
  run_at: string;
  acked_at?: string | null;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function passRateColor(rate: number | null | undefined, T: ReturnType<typeof useVellumTokens>): string {
  if (rate == null) return '#64748b';
  if (rate >= 0.8) return T.green;
  if (rate >= 0.5) return T.gilt;
  return T.rust;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function fmtPct(rate: number | null | undefined): string {
  if (rate == null) return '—';
  return `${Math.round(rate * 100)}%`;
}

// ── Regression banner ─────────────────────────────────────────────────────────

function RegressionBanner({
  regressions,
  onAck,
}: {
  regressions: Regression[];
  onAck: (id: string) => void;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  if (!regressions.length) return null;

  return (
    <View style={[styles.regBanner, { backgroundColor: T.rustSoft, borderColor: T.rust }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <Feather name="alert-triangle" size={13} color={T.rust} />
        <Text style={[styles.regTitle, { color: T.rust }]}>
          {regressions.length} regression{regressions.length !== 1 ? 's' : ''} detected
        </Text>
      </View>
      {regressions.map(r => (
        <View key={r.id} style={[styles.regRow, { borderTopColor: T.rust + '33' }]}>
          <View style={{ flex: 1 }}>
            <Text style={[styles.regName, { color: colors.foreground }]} numberOfLines={1}>
              {r.benchmark_name ?? r.benchmark_id}
            </Text>
            <Text style={[styles.regMeta, { color: colors.mutedForeground }]}>
              {fmtPct(r.pass_rate_before)} → {fmtPct(r.pass_rate_after)} · {fmtDate(r.run_at)}
            </Text>
          </View>
          <Pressable
            onPress={() => onAck(r.id)}
            style={({ pressed }) => [styles.ackBtn, { borderColor: T.rust, opacity: pressed ? 0.6 : 1 }]}
            accessibilityLabel="Acknowledge regression"
          >
            <Text style={[styles.ackText, { color: T.rust }]}>Ack</Text>
          </Pressable>
        </View>
      ))}
    </View>
  );
}

// ── Benchmark card ─────────────────────────────────────────────────────────────

function BenchmarkCard({
  benchmark,
  onRun,
  running,
}: {
  benchmark: Benchmark;
  onRun: (id: string) => void;
  running: boolean;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  const rate = benchmark.last_pass_rate;
  const color = passRateColor(rate, T);

  return (
    <View style={[styles.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={styles.cardHeader}>
        {/* Pass rate circle */}
        <View style={[styles.rateCircle, { borderColor: color }]}>
          <Text style={[styles.rateText, { color }]}>{fmtPct(rate)}</Text>
        </View>

        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={[styles.cardTitle, { color: colors.foreground }]} numberOfLines={1}>
            {benchmark.name}
          </Text>
          {!!benchmark.description && (
            <Text style={[styles.cardDesc, { color: colors.mutedForeground }]} numberOfLines={1}>
              {benchmark.description}
            </Text>
          )}
          <Text style={[styles.cardMeta, { color: colors.mutedForeground }]}>
            {benchmark.prompt_count != null ? `${benchmark.prompt_count} prompts` : ''}
            {benchmark.last_run_at ? ` · last run ${fmtDate(benchmark.last_run_at)}` : ' · never run'}
          </Text>
        </View>

        {/* Status indicator */}
        <View style={[
          styles.statusDot,
          { backgroundColor: benchmark.last_status === 'running' ? T.gilt : color },
        ]} />
      </View>

      {/* Run button */}
      <Pressable
        onPress={() => {
          if (Platform.OS !== 'web') Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
          onRun(benchmark.id);
        }}
        disabled={running}
        style={({ pressed }) => [
          styles.runBtn,
          { borderColor: colors.border, opacity: running || pressed ? 0.6 : 1 },
        ]}
      >
        {running
          ? <ActivityIndicator size="small" color={colors.primary} />
          : <Feather name="play" size={13} color={colors.primary} />}
        <Text style={[styles.runBtnText, { color: colors.primary }]}>
          {running ? 'Running…' : 'Run benchmark'}
        </Text>
      </Pressable>
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function McosScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const queryClient = useQueryClient();
  const [runningId, setRunningId] = useState<string | null>(null);
  const [runningAll, setRunningAll] = useState(false);

  const { data: bData, isLoading: bLoading, isError: bError, refetch: bRefetch } = useQuery<{ benchmarks: Benchmark[] }>({
    queryKey: ['mobile', 'mcos', 'benchmarks'],
    queryFn: () => mobileFetch('/api/mcos/benchmarks').then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const { data: rData, refetch: rRefetch } = useQuery<{ regressions: Regression[] }>({
    queryKey: ['mobile', 'mcos', 'regressions'],
    queryFn: () => mobileFetch('/api/mcos/regressions').then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const benchmarks = bData?.benchmarks ?? [];
  const regressions = (rData?.regressions ?? []).filter(r => !r.acked_at);

  const handleRefresh = () => {
    bRefetch();
    rRefetch();
    queryClient.invalidateQueries({ queryKey: ['mobile', 'mcos'] });
  };

  const handleRun = async (id: string) => {
    setRunningId(id);
    try {
      const res = await mobileFetch(`/api/mcos/benchmarks/${id}/run`, { method: 'POST' });
      if (!res.ok) throw new Error(`status ${res.status}`);
      // Poll for result — refetch after 3 s
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['mobile', 'mcos', 'benchmarks'] });
        setRunningId(null);
      }, 3000);
    } catch (e: any) {
      Alert.alert('Error', e.message ?? 'Could not start run');
      setRunningId(null);
    }
  };

  const handleRunAll = async () => {
    setRunningAll(true);
    try {
      const res = await mobileFetch('/api/mcos/run-all', { method: 'POST' });
      if (!res.ok) throw new Error(`status ${res.status}`);
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['mobile', 'mcos'] });
        setRunningAll(false);
      }, 3000);
    } catch (e: any) {
      Alert.alert('Error', e.message ?? 'Could not start runs');
      setRunningAll(false);
    }
  };

  const handleAck = async (id: string) => {
    try {
      await mobileFetch(`/api/mcos/regressions/${id}/ack`, { method: 'POST' });
      queryClient.invalidateQueries({ queryKey: ['mobile', 'mcos', 'regressions'] });
    } catch {
      Alert.alert('Error', 'Could not acknowledge regression');
    }
  };

  const overallPct = benchmarks.length > 0 && benchmarks.every(b => b.last_pass_rate != null)
    ? Math.round(benchmarks.reduce((a, b) => a + (b.last_pass_rate ?? 0), 0) / benchmarks.length * 100)
    : null;

  return (
    <ScrollView
      style={[styles.root, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 24 }]}
      refreshControl={
        <RefreshControl
          refreshing={bLoading}
          onRefresh={handleRefresh}
          tintColor={colors.primary}
        />
      }
    >
      {/* Header */}
      <View style={styles.headerRow}>
        <Feather name="bar-chart-2" size={20} color={colors.primary} />
        <Text style={[styles.pageTitle, { color: colors.foreground }]}>MCOS</Text>
      </View>
      <Text style={[styles.pageSubtitle, { color: colors.mutedForeground }]}>
        Model calibration &amp; prompt health monitoring
      </Text>

      {/* Summary stats */}
      {!bLoading && benchmarks.length > 0 && (
        <View style={[styles.statsCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          {[
            { label: 'Benchmarks', value: String(benchmarks.length) },
            { label: 'Avg pass rate', value: overallPct != null ? `${overallPct}%` : '—' },
            { label: 'Regressions', value: String(regressions.length) },
          ].map((s, i) => (
            <View key={s.label} style={[styles.statCell, i < 2 && { borderRightWidth: 1, borderRightColor: colors.border }]}>
              <Text style={[styles.statValue, {
                color: s.label === 'Regressions' && regressions.length > 0 ? T.rust : colors.foreground,
              }]}>
                {s.value}
              </Text>
              <Text style={[styles.statLabel, { color: colors.mutedForeground }]}>{s.label}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Regression banner */}
      <RegressionBanner regressions={regressions} onAck={handleAck} />

      {/* Run all button */}
      {!bLoading && benchmarks.length > 0 && (
        <Pressable
          onPress={handleRunAll}
          disabled={runningAll}
          style={({ pressed }) => [
            styles.runAllBtn,
            { borderColor: colors.primary, opacity: runningAll || pressed ? 0.6 : 1 },
          ]}
        >
          {runningAll
            ? <ActivityIndicator size="small" color={colors.primary} />
            : <Feather name="play-circle" size={16} color={colors.primary} />}
          <Text style={[styles.runAllText, { color: colors.primary }]}>
            {runningAll ? 'Running all…' : 'Run all benchmarks'}
          </Text>
        </Pressable>
      )}

      {bLoading ? (
        [...Array(3)].map((_, i) => <SkeletonItem key={i} lines={2} />)
      ) : bError ? (
        <View style={styles.emptyBox}>
          <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load benchmarks</Text>
          <Pressable onPress={handleRefresh} style={[styles.retryBtn, { borderColor: colors.border }]}>
            <Text style={[styles.retryText, { color: colors.primary }]}>Retry</Text>
          </Pressable>
        </View>
      ) : benchmarks.length === 0 ? (
        <EmptyState
          icon="bar-chart-2"
          title="No benchmarks yet"
          body="Benchmarks are seeded from the MCOS configuration. Run the seed endpoint to populate them."
        />
      ) : (
        <>
          <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>BENCHMARKS</Text>
          {benchmarks.map(b => (
            <BenchmarkCard
              key={b.id}
              benchmark={b}
              onRun={handleRun}
              running={runningId === b.id}
            />
          ))}
        </>
      )}
    </ScrollView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 16 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  pageTitle: { fontSize: 26, lineHeight: 32, ...fontSerif('bold') },
  pageSubtitle: { fontSize: 15, lineHeight: 22, marginBottom: 16, ...font('regular') },
  statsCard: { flexDirection: 'row', borderRadius: 12, borderWidth: 1, marginBottom: 16, overflow: 'hidden' },
  statCell: { flex: 1, padding: 14, alignItems: 'center', gap: 2 },
  statValue: { fontSize: 22, lineHeight: 28, ...fontSerif('bold') },
  statLabel: { fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', ...font('regular') },
  sectionLabel: {
    fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase',
    marginBottom: 8, marginTop: 4, ...font('semibold'),
  },
  // Regression banner
  regBanner: { borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 16 },
  regTitle: { fontSize: 12, lineHeight: 18, ...font('semibold') },
  regRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth },
  regName: { fontSize: 13, lineHeight: 20, ...font('medium') },
  regMeta: { fontSize: 11, lineHeight: 16, ...font('regular') },
  ackBtn: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6, borderWidth: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  ackText: { fontSize: 12, lineHeight: 16, ...font('semibold') },
  // Run all
  runAllBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8, paddingVertical: 12, borderRadius: 10, borderWidth: 1,
    marginBottom: 16, minHeight: 44,
  },
  runAllText: { fontSize: 14, lineHeight: 20, ...font('medium') },
  // Benchmark card
  card: { borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 10 },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 10 },
  rateCircle: {
    width: 44, height: 44, borderRadius: 22, borderWidth: 2.5,
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  rateText: { fontSize: 11, ...font('bold') },
  cardTitle: { fontSize: 14, lineHeight: 20, ...font('semibold') },
  cardDesc: { fontSize: 12, lineHeight: 18, ...font('regular') },
  cardMeta: { fontSize: 11, lineHeight: 16, marginTop: 1, ...font('regular') },
  statusDot: { width: 8, height: 8, borderRadius: 4, flexShrink: 0 },
  runBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 8, borderRadius: 8, borderWidth: 1, minHeight: 36,
  },
  runBtnText: { fontSize: 12, lineHeight: 18, ...font('medium') },
  // Empty / error
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, lineHeight: 22, ...font('medium') },
  retryBtn: { marginTop: 4, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 8, borderWidth: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  retryText: { fontSize: 14, lineHeight: 20, ...font('medium') },
});
