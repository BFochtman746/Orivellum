/**
 * Mobile Intelligence page — mirrors the web /works/:id/intelligence route.
 * Fetches chapters, completeness, and gaps and displays them in a scrollable
 * summary optimised for small screens.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useLocalSearchParams, useNavigation } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { mobileFetch } from '@/lib/api';
import { ErrorScreen } from '@/components/OfflineBanner';

// ─── severity colours ─────────────────────────────────────────────────────────

const SEV: Record<string, { bg: string; text: string }> = {
  critical: { bg: '#fee2e2', text: '#b91c1c' },
  high:     { bg: '#fef3c7', text: '#92400e' },
  medium:   { bg: '#e0f2fe', text: '#0369a1' },
  low:      { bg: '#f0fdf4', text: '#166534' },
};

// ─── helpers ─────────────────────────────────────────────────────────────────

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
  const pct = Math.min(100, Math.max(0, value * (unit === '%' ? 1 : 100)));
  return (
    <View style={s.metricRow}>
      <View style={s.metricHeader}>
        <Text style={[s.metricLabel, { color: colors.mutedForeground }]}>{label}</Text>
        <Text style={[s.metricValue, { color: colors.foreground }]}>
          {unit === '%' ? `${Math.round(value)}%` : Math.round(value * 100) + '%'}
        </Text>
      </View>
      <View style={[s.barTrack, { backgroundColor: colors.muted }]}>
        <View
          style={[
            s.barFill,
            {
              width: `${pct}%` as any,
              backgroundColor: pct >= 80 ? '#16a34a' : pct >= 50 ? '#d97706' : '#dc2626',
            },
          ]}
        />
      </View>
    </View>
  );
}

// ─── main screen ─────────────────────────────────────────────────────────────

export default function WorkIntelligenceScreen() {
  const colors   = useColors();
  const insets   = useSafeAreaInsets();
  const isWeb    = Platform.OS === 'web';
  const { id }   = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const domain   = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

  const [completeness, setCompleteness] = useState<any>(null);
  const [gaps,         setGaps]         = useState<any>(null);
  const [chapters,     setChapters]     = useState<any>(null);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [cRes, gRes, chRes] = await Promise.all([
        mobileFetch(`https://${domain}/api/works/${id}/completeness`),
        mobileFetch(`https://${domain}/api/works/${id}/gaps`),
        mobileFetch(`https://${domain}/api/works/${id}/chapters`),
      ]);
      const [cData, gData, chData] = await Promise.all([
        cRes.ok ? cRes.json() : null,
        gRes.ok ? gRes.json() : null,
        chRes.ok ? chRes.json() : null,
      ]);
      setCompleteness(cData);
      setGaps(gData);
      setChapters(chData);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [id, domain]);

  useEffect(() => {
    navigation.setOptions({ title: 'Intelligence' });
    fetchAll();
  }, [fetchAll, navigation]);

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

  const dims: any[]    = completeness?.dimensions ?? [];
  const overallScore   = completeness?.overall   ?? 0;
  const allGaps: any[] = gaps?.gaps ?? [];
  const allChapters: any[] = chapters?.chapters ?? [];
  const coveragePct    = gaps?.coverage_pct ?? null;

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.background }}
      contentContainerStyle={{ paddingTop: topPad + 16, paddingBottom: 40, paddingHorizontal: 16 }}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl refreshing={loading} onRefresh={fetchAll} tintColor={colors.primary} />
      }
    >
      {/* ── Overall completeness ── */}
      <Section title="Completeness">
        <MetricRow
          label="Overall"
          value={overallScore}
          unit="%"
        />
        {dims.map((d: any) => (
          <MetricRow key={d.name} label={d.label ?? d.name} value={d.score ?? 0} unit="%" />
        ))}
        {completeness?.summary ? (
          <Text style={[s.summary, { color: colors.mutedForeground }]}>
            {completeness.summary}
          </Text>
        ) : null}
      </Section>

      {/* ── Gaps ── */}
      <Section title={`Research Gaps${allGaps.length ? ` (${allGaps.length})` : ''}`}>
        {coveragePct != null && (
          <Text style={[s.metaLine, { color: colors.mutedForeground }]}>
            Coverage: {coveragePct}%
          </Text>
        )}
        {allGaps.length === 0 ? (
          <Text style={[s.emptyText, { color: colors.mutedForeground }]}>No gaps detected</Text>
        ) : (
          allGaps.map((g: any, i: number) => {
            const sev = g.severity ?? 'medium';
            const gc  = SEV[sev] ?? SEV.medium;
            return (
              <View key={i} style={[s.gapCard, { borderColor: colors.border }]}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
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
              </View>
            );
          })
        )}
      </Section>

      {/* ── Chapter structure ── */}
      {allChapters.length > 0 && (
        <Section title={`Chapters (${allChapters.length})`}>
          {allChapters.map((ch: any, i: number) => (
            <View key={i} style={[s.chapterRow, { borderBottomColor: colors.border }]}>
              <View style={[s.chapterNum, { backgroundColor: colors.muted }]}>
                <Text style={[s.chapterNumText, { color: colors.mutedForeground }]}>
                  {ch.chapter_number ?? i + 1}
                </Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={[s.chapterTitle, { color: colors.foreground }]} numberOfLines={2}>
                  {ch.title}
                </Text>
                {ch.summary ? (
                  <Text style={[s.chapterSummary, { color: colors.mutedForeground }]} numberOfLines={2}>
                    {ch.summary}
                  </Text>
                ) : null}
              </View>
            </View>
          ))}
        </Section>
      )}
    </ScrollView>
  );
}

// ─── styles ──────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  emptyText: { fontSize: 14, fontFamily: 'Inter_400Regular', marginTop: 4 },
  section: {
    marginBottom: 24,
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
  summary: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 18, marginTop: 4 },
  metaLine: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  metricRow: { gap: 4 },
  metricHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  metricLabel: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  metricValue: { fontSize: 12, fontFamily: 'Inter_600SemiBold', tabularNums: true } as any,
  barTrack: { height: 4, borderRadius: 2, overflow: 'hidden' },
  barFill:  { height: 4, borderRadius: 2 },
  gapCard: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    gap: 2,
  },
  gapTitle: { fontSize: 13, fontFamily: 'Inter_500Medium' },
  gapDesc:  { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 },
  sevBadge: { borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  sevText:  { fontSize: 10, fontFamily: 'Inter_600SemiBold', textTransform: 'capitalize' },
  chapterRow: {
    flexDirection: 'row',
    gap: 10,
    paddingBottom: 10,
    marginBottom: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    alignItems: 'flex-start',
  },
  chapterNum: {
    width: 28,
    height: 28,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
  },
  chapterNumText:    { fontSize: 11, fontFamily: 'Inter_600SemiBold' },
  chapterTitle:      { fontSize: 13, fontFamily: 'Inter_500Medium' },
  chapterSummary:    { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 2 },
});
