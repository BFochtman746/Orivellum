/**
 * Governance — /governance
 *
 * Mobile equivalent of the web Governance page. Covers:
 *  - Stats strip (pending / approved / rejected / total)
 *  - Audit chain integrity check
 *  - Outbox backlog
 *  - Contradicting claims (Keep A / Keep B / Keep Both)
 *  - Open pipeline findings (Resolve)
 *
 * The review queue (AI-knowledge approve/reject) lives separately in /review.
 */
import React, { useState } from 'react';
import { mobileFetch } from '@/lib/api';
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
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const BASE = `https://${DOMAIN}/api`;

async function gFetch(path: string) {
  const r = await mobileFetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface GovStats {
  pending: number;
  approved: number;
  rejected: number;
  total: number;
}

interface AuditChainResult {
  ok: boolean;
  reason?: string;
  rows_checked?: number;
}

interface OutboxEvent {
  id: string;
  event_type: string;
  object_type: string | null;
  object_id: string | null;
  created_at: string;
}

interface Conflict {
  id: string;
  type: 'negation' | 'value_conflict';
  work_title: string | null;
  claim_a: { id: string; predicate: string; value: string; confidence: number };
  claim_b: { id: string; predicate: string; value: string; confidence: number };
}

interface Finding {
  id: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  message: string;
  state: 'open' | 'resolved';
  work_title?: string | null;
  created_at: string;
}

// ── Severity colors ───────────────────────────────────────────────────────────

const SEV_COLOR: Record<string, string> = {
  critical: '#dc2626',
  high: '#f97316',
  medium: '#f59e0b',
  low: '#6b7280',
};

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, icon, badge, children }: {
  title: string; icon: string; badge?: number; children: React.ReactNode;
}) {
  const colors = useColors();
  return (
    <View style={[s.section, { borderColor: colors.border, backgroundColor: colors.card }]}>
      <View style={s.sectionHead}>
        <Feather name={icon as any} size={14} color={colors.primary} />
        <Text style={[s.sectionTitle, { color: colors.mutedForeground }]}>{title.toUpperCase()}</Text>
        {badge != null && badge > 0 && (
          <View style={s.badge}>
            <Text style={s.badgeText}>{badge}</Text>
          </View>
        )}
      </View>
      {children}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function GovernanceScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const qc = useQueryClient();
  const isWeb = Platform.OS === 'web';

  const [refreshing, setRefreshing] = useState(false);
  const [checkingChain, setCheckingChain] = useState(false);
  const [chainResult, setChainResult] = useState<AuditChainResult | null>(null);
  const [resolvingConflict, setResolvingConflict] = useState<string | null>(null);
  const [resolvingFinding, setResolvingFinding] = useState<string | null>(null);

  // ── Queries ───────────────────────────────────────────────────────────────────

  const { data: stats, refetch: refetchStats } = useQuery<GovStats>({
    queryKey: ['gov-stats'],
    queryFn: () => gFetch('/governance/stats'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const { data: outboxData, refetch: refetchOutbox } = useQuery<{ items: OutboxEvent[]; total: number }>({
    queryKey: ['gov-outbox'],
    queryFn: () => gFetch('/governance/outbox?pending_only=true&limit=20'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const { data: conflictsData, refetch: refetchConflicts } = useQuery<{ conflicts: Conflict[] }>({
    queryKey: ['gov-conflicts'],
    queryFn: () => gFetch('/governance/conflicts'),
    staleTime: 60_000,
  });

  const { data: findingsData, refetch: refetchFindings } = useQuery<{ findings: Finding[]; count: number }>({
    queryKey: ['gov-findings'],
    queryFn: () => gFetch('/governance/findings?state=open&limit=50'),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  // ── Actions ───────────────────────────────────────────────────────────────────

  const handleCheckChain = async () => {
    setCheckingChain(true);
    setChainResult(null);
    try {
      const data = await gFetch('/governance/audit-chain');
      setChainResult(data);
    } catch {
      setChainResult({ ok: false, reason: 'Could not reach server' });
    } finally {
      setCheckingChain(false);
    }
  };

  const handleResolveConflict = async (conflictId: string, resolution: 'keep_a' | 'keep_b' | 'keep_both') => {
    setResolvingConflict(conflictId);
    try {
      const r = await mobileFetch(`${BASE}/governance/conflicts/${conflictId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resolution }),
      });
      if (!r.ok) throw new Error('Resolve failed');
      await refetchConflicts();
      await refetchStats();
    } catch {
      Alert.alert('Error', 'Could not resolve conflict');
    } finally {
      setResolvingConflict(null);
    }
  };

  const handleResolveFinding = async (findingId: string) => {
    setResolvingFinding(findingId);
    try {
      const r = await mobileFetch(`${BASE}/governance/findings/${findingId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resolution: 'acknowledged' }),
      });
      if (!r.ok) throw new Error('Resolve failed');
      await refetchFindings();
    } catch {
      Alert.alert('Error', 'Could not resolve finding');
    } finally {
      setResolvingFinding(null);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all([refetchStats(), refetchOutbox(), refetchConflicts(), refetchFindings()]);
    } finally {
      setRefreshing(false);
    }
  };

  const topPad = isWeb ? 67 : insets.top;
  const conflicts = conflictsData?.conflicts ?? [];
  const findings = findingsData?.findings ?? [];
  const outboxItems = outboxData?.items ?? [];

  return (
    <View style={[s.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View style={[s.header, { paddingTop: topPad + 8, borderBottomColor: colors.border, backgroundColor: colors.background }]}>
        <Pressable onPress={() => router.back()} style={s.backRow} hitSlop={8}>
          <Feather name="arrow-left" size={18} color={colors.primary} />
          <Text style={[s.backLabel, { color: colors.primary }]}>Back</Text>
        </Pressable>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Feather name="shield" size={20} color={colors.foreground} />
          <Text style={[s.title, { color: colors.foreground }]}>Governance</Text>
        </View>
        <Text style={[s.subtitle, { color: colors.mutedForeground }]}>
          Review AI-extracted knowledge before it becomes fact
        </Text>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 32 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={colors.primary} />}
        showsVerticalScrollIndicator={false}
      >
        {/* Stats strip */}
        {stats && (
          <View style={s.statsRow}>
            {[
              { label: 'Pending', value: stats.pending, color: '#f59e0b' },
              { label: 'Approved', value: stats.approved, color: '#22c55e' },
              { label: 'Rejected', value: stats.rejected, color: '#ef4444' },
              { label: 'Total', value: stats.total, color: colors.mutedForeground },
            ].map((item) => (
              <View key={item.label} style={[s.statCard, { backgroundColor: item.color + '14', borderColor: item.color + '33' }]}>
                <Text style={[s.statValue, { color: item.color }]}>{item.value}</Text>
                <Text style={[s.statLabel, { color: colors.mutedForeground }]}>{item.label}</Text>
              </View>
            ))}
          </View>
        )}

        {/* Audit chain */}
        <Section title="Audit Chain Integrity" icon="lock">
          {chainResult && (
            <View style={[s.chainResult, {
              backgroundColor: chainResult.ok ? '#22c55e14' : '#ef444414',
              borderColor: chainResult.ok ? '#22c55e44' : '#ef444444',
            }]}>
              <Feather name={chainResult.ok ? 'check-circle' : 'alert-triangle'} size={16}
                color={chainResult.ok ? '#22c55e' : '#ef4444'} />
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: chainResult.ok ? '#22c55e' : '#ef4444' }}>
                  {chainResult.ok ? 'Chain intact' : 'Chain broken'}
                </Text>
                {chainResult.rows_checked != null && (
                  <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                    {chainResult.rows_checked} rows verified · no tampering detected
                  </Text>
                )}
                {chainResult.reason && !chainResult.ok && (
                  <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: '#ef4444', marginTop: 2 }}>
                    {chainResult.reason}
                  </Text>
                )}
              </View>
            </View>
          )}
          <Pressable
            onPress={handleCheckChain}
            disabled={checkingChain}
            style={({ pressed }) => [s.actionBtn, { borderColor: colors.border, backgroundColor: colors.muted, opacity: pressed || checkingChain ? 0.6 : 1 }]}
          >
            {checkingChain ? <ActivityIndicator size="small" color={colors.primary} /> : <Feather name="refresh-cw" size={14} color={colors.primary} />}
            <Text style={[s.actionBtnText, { color: colors.primary }]}>
              {checkingChain ? 'Checking…' : 'Check Now'}
            </Text>
          </Pressable>
        </Section>

        {/* Contradicting claims */}
        {conflicts.length > 0 && (
          <Section title="Contradicting Claims" icon="alert-triangle" badge={conflicts.length}>
            {conflicts.map((conflict) => (
              <View key={conflict.id} style={[s.conflictCard, { borderColor: colors.border }]}>
                <View style={s.conflictHeader}>
                  <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: '#f59e0b', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    {conflict.type === 'negation' ? 'Negation' : 'Conflicting values'}
                  </Text>
                  {conflict.work_title && (
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                      {conflict.work_title}
                    </Text>
                  )}
                </View>
                {/* Side-by-side claims */}
                <View style={{ flexDirection: 'row', gap: 8, marginBottom: 10 }}>
                  {[conflict.claim_a, conflict.claim_b].map((claim, idx) => (
                    <View key={claim.id} style={[s.claimBox, { backgroundColor: colors.muted + '55', borderColor: colors.border, flex: 1 }]}>
                      <Text style={{ fontSize: 9, fontFamily: 'Inter_700Bold', color: colors.mutedForeground, marginBottom: 2 }}>
                        {idx === 0 ? 'A' : 'B'}
                      </Text>
                      <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }} numberOfLines={2}>
                        {claim.predicate}: {claim.value}
                      </Text>
                      <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 2 }}>
                        {Math.round(claim.confidence * 100)}% confidence
                      </Text>
                    </View>
                  ))}
                </View>
                {/* Resolution buttons */}
                <View style={{ flexDirection: 'row', gap: 6 }}>
                  {(['keep_a', 'keep_b', 'keep_both'] as const).map((res) => (
                    <Pressable
                      key={res}
                      onPress={() => handleResolveConflict(conflict.id, res)}
                      disabled={resolvingConflict === conflict.id}
                      style={({ pressed }) => [
                        s.resolveBtn,
                        { borderColor: colors.border, backgroundColor: colors.muted,
                          opacity: pressed || resolvingConflict === conflict.id ? 0.6 : 1, flex: 1 },
                      ]}
                    >
                      {resolvingConflict === conflict.id
                        ? <ActivityIndicator size="small" color={colors.primary} />
                        : <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: colors.foreground, textAlign: 'center' }}>
                            {res === 'keep_a' ? 'Keep A' : res === 'keep_b' ? 'Keep B' : 'Keep Both'}
                          </Text>
                      }
                    </Pressable>
                  ))}
                </View>
              </View>
            ))}
          </Section>
        )}

        {/* Open findings */}
        {findings.length > 0 && (
          <Section title="Open Findings" icon="flag" badge={findings.length}>
            {findings.map((finding) => (
              <View key={finding.id} style={[s.findingRow, { borderTopColor: colors.border }]}>
                <View style={[s.sevDot, { backgroundColor: SEV_COLOR[finding.severity] ?? colors.mutedForeground }]} />
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground }} numberOfLines={2}>
                    {finding.message}
                  </Text>
                  {finding.work_title && (
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 1 }}>
                      {finding.work_title}
                    </Text>
                  )}
                </View>
                <Pressable
                  onPress={() => handleResolveFinding(finding.id)}
                  disabled={resolvingFinding === finding.id}
                  hitSlop={8}
                  style={({ pressed }) => [s.resolveIcon, { opacity: pressed || resolvingFinding === finding.id ? 0.5 : 1 }]}
                >
                  {resolvingFinding === finding.id
                    ? <ActivityIndicator size="small" color={colors.primary} />
                    : <Feather name="x" size={16} color={colors.mutedForeground} />
                  }
                </Pressable>
              </View>
            ))}
          </Section>
        )}

        {/* Outbox backlog */}
        {outboxItems.length > 0 && (
          <Section title={`Outbox Backlog (${outboxData?.total ?? outboxItems.length})`} icon="send">
            <Text style={[s.metaText, { color: colors.mutedForeground }]}>
              Nightshift drains these automatically.
            </Text>
            {outboxItems.slice(0, 10).map((evt) => (
              <View key={evt.id} style={[s.outboxRow, { borderTopColor: colors.border }]}>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }} numberOfLines={1}>
                  {evt.event_type}
                </Text>
                <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                  {evt.object_type ?? ''}{evt.object_id ? ' · ' + evt.object_id.slice(0, 8) : ''}
                </Text>
              </View>
            ))}
            {(outboxData?.total ?? 0) > 10 && (
              <Text style={[s.metaText, { color: colors.mutedForeground, marginTop: 6 }]}>
                +{(outboxData?.total ?? 0) - 10} more
              </Text>
            )}
          </Section>
        )}

        {/* All clear */}
        {conflicts.length === 0 && findings.length === 0 && outboxItems.length === 0 && (
          <View style={[s.allClear, { backgroundColor: '#22c55e12', borderColor: '#22c55e33' }]}>
            <Feather name="check-circle" size={24} color="#22c55e" />
            <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: '#22c55e', marginTop: 8 }}>
              All clear
            </Text>
            <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center', marginTop: 4 }}>
              No conflicts, findings, or pending outbox events.
            </Text>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  container: { flex: 1 },
  header: { paddingHorizontal: 16, paddingBottom: 14, borderBottomWidth: 1 },
  backRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 },
  backLabel: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', letterSpacing: -0.3 },
  subtitle: { fontSize: 12, fontFamily: 'Inter_400Regular', marginTop: 2 },
  // Stats
  statsRow: { flexDirection: 'row', gap: 8, marginBottom: 14 },
  statCard: { flex: 1, borderRadius: 8, borderWidth: 1, padding: 10, alignItems: 'center' },
  statValue: { fontSize: 20, fontFamily: 'Inter_700Bold' },
  statLabel: { fontSize: 10, fontFamily: 'Inter_400Regular', marginTop: 2 },
  // Section
  section: { borderRadius: 10, borderWidth: 1, padding: 14, marginBottom: 14 },
  sectionHead: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 12 },
  sectionTitle: { fontSize: 10, fontFamily: 'Inter_700Bold', letterSpacing: 1 },
  badge: { backgroundColor: '#ef4444', borderRadius: 8, paddingHorizontal: 5, paddingVertical: 1 },
  badgeText: { fontSize: 9, fontFamily: 'Inter_700Bold', color: '#fff' },
  // Chain
  chainResult: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, borderRadius: 8, borderWidth: 1, padding: 10, marginBottom: 10 },
  // Action button
  actionBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start',
    borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 7, marginTop: 4,
  },
  actionBtnText: { fontSize: 13, fontFamily: 'Inter_500Medium' },
  // Conflicts
  conflictCard: { borderWidth: 1, borderRadius: 8, padding: 10, marginBottom: 10 },
  conflictHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  claimBox: { borderRadius: 6, borderWidth: 1, padding: 8 },
  resolveBtn: { borderWidth: 1, borderRadius: 6, paddingVertical: 6, alignItems: 'center' },
  // Findings
  findingRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 10, borderTopWidth: StyleSheet.hairlineWidth },
  sevDot: { width: 8, height: 8, borderRadius: 4, marginTop: 3, flexShrink: 0 },
  resolveIcon: { padding: 4 },
  // Outbox
  outboxRow: { paddingVertical: 8, borderTopWidth: StyleSheet.hairlineWidth },
  metaText: { fontSize: 12, fontFamily: 'Inter_400Regular', marginBottom: 6 },
  // All clear
  allClear: { borderRadius: 12, borderWidth: 1, padding: 24, alignItems: 'center', marginTop: 8 },
});
