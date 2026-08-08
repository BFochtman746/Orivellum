/**
 * A-01 Mail Steward — /mail/:id
 * Decision detail: sender, subject, time, assessment rationale, threat evidence, actions.
 */
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Feather } from '@expo/vector-icons';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens, alpha } from '@/lib/tokens';
import { font } from '@/lib/typography';
import { mobileFetchJson } from '@/lib/api';
import * as Haptics from 'expo-haptics';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface MailRecord {
  id: string;
  subject: string | null;
  sender_name: string | null;
  sender_domain: string | null;
  received_at: string | null;
  has_attachments: boolean;
  attention_level: string | null;
  needs_reply: boolean | null;
  is_high_risk: boolean | null;
  confidence: number | null;
  lifecycle_state: string;
  action_request_id: string | null;
}

interface MailAssessment {
  attention_level: string;
  rationale: string;
  suggested_reply: string | null;
  needs_reply: boolean;
  recommended_action: string;
  confidence: number;
  is_high_risk: boolean;
  injection_flagged: boolean;
  model_id: string;
  signals_json: string;
}

interface ActionOption {
  type: string;
  nonce: string;
  label: string;
}

interface DecisionDetail {
  record: MailRecord;
  assessment: MailAssessment | null;
  available_actions: ActionOption[];
  audit_trail: any[];
}

interface MailSummary {
  connected: boolean;
  send_enabled: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function ConfidenceBar({ value }: { value: number | null }) {
  const colors = useColors();
  const T = useVellumTokens();
  if (value == null) return null;
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const barColor = value < 0.5 ? T.rust : value < 0.8 ? T.gilt : T.green;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6 }}>
      <View style={[ss.confTrack, { backgroundColor: colors.muted }]}>
        <View style={{ height: '100%', width: `${pct}%`, backgroundColor: barColor, borderRadius: 3 }} />
      </View>
      <Text style={{ fontSize: 11, minWidth: 32, color: barColor, ...font('medium') }}>{pct}%</Text>
    </View>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  const colors = useColors();
  return (
    <View style={ss.infoRow}>
      <Text style={{ fontSize: 11, width: 88, color: colors.mutedForeground, ...font('medium') }}>{label}</Text>
      <Text style={{ fontSize: 13, flex: 1, color: colors.foreground, ...font('regular') }}>{value}</Text>
    </View>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const colors = useColors();
  return (
    <View style={[ss.section, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <Text style={[ss.sectionTitle, { color: colors.mutedForeground }]}>{title}</Text>
      {children}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function MailDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const qc = useQueryClient();
  const [acting, setActing] = useState(false);

  const { data: detail, isLoading, error } = useQuery<DecisionDetail>({
    queryKey: ['mail-decision', id],
    queryFn: () => mobileFetchJson(`${API}/mail/decisions/${id}`),
    enabled: !!id,
    staleTime: 15_000,
  });

  const { data: summary } = useQuery<MailSummary>({
    queryKey: ['mail-summary'],
    queryFn: () => mobileFetchJson(`${API}/mail/summary`),
    staleTime: 30_000,
  });

  const invalidate = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['mail-attention'] });
    qc.invalidateQueries({ queryKey: ['mail-summary'] });
    qc.invalidateQueries({ queryKey: ['mail-decision', id] });
  }, [qc, id]);

  const handleCompose = useCallback(async (draftAction: ActionOption) => {
    if (!id) return;
    setActing(true);
    try {
      const data = await mobileFetchJson<{ action_request_id: string }>(
        `${API}/mail/decisions/${id}/draft`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nonce: draftAction.nonce }) },
      );
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
      // sendNonce is NOT in the URL — fetched fresh at send time
      router.push(`/mail/compose/${data.action_request_id}?recordId=${id}` as any);
    } catch (e: any) {
      Alert.alert('Draft failed', e.message ?? 'Could not create draft');
    } finally {
      setActing(false);
    }
  }, [id, router]);

  const handleMove = useCallback(async (moveAction: ActionOption) => {
    if (!id) return;
    setActing(true);
    try {
      await mobileFetchJson(`${API}/mail/decisions/${id}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destination: 'review', nonce: moveAction.nonce }),
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      invalidate();
      router.back();
    } catch (e: any) {
      Alert.alert('Move failed', e.message ?? 'Could not move message');
    } finally {
      setActing(false);
    }
  }, [id, router, invalidate]);

  if (isLoading) {
    return (
      <View style={[ss.root, { backgroundColor: colors.background }]}>
        <Stack.Screen options={{ title: 'Mail', headerShown: true, headerStyle: { backgroundColor: colors.background }, headerTintColor: colors.foreground }} />
        <View style={ss.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      </View>
    );
  }

  if (!detail) {
    return (
      <View style={[ss.root, { backgroundColor: colors.background }]}>
        <Stack.Screen options={{ title: 'Not found', headerShown: true, headerStyle: { backgroundColor: colors.background }, headerTintColor: colors.foreground }} />
        <View style={ss.center}>
          <Feather name="alert-circle" size={36} color={colors.mutedForeground} />
          <Text style={{ fontSize: 14, marginTop: 10, color: colors.mutedForeground, ...font('regular') }}>
            {(error as Error)?.message ?? 'Decision not found'}
          </Text>
        </View>
      </View>
    );
  }

  const { record, assessment, available_actions } = detail;
  const draftAction = available_actions.find(a => a.type === 'CREATE_DRAFT');
  const moveAction = available_actions.find(a => a.type === 'MOVE');
  const signals: string[] = (() => { try { return JSON.parse(assessment?.signals_json ?? '[]'); } catch { return []; } })();

  const levelColor =
    record.attention_level === 'high' ? T.rust :
    record.attention_level === 'medium' ? T.gilt :
    colors.mutedForeground;

  return (
    <View style={[ss.root, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          title: record.subject ?? 'Mail',
          headerShown: true,
          headerStyle: { backgroundColor: colors.background },
          headerTintColor: colors.foreground,
        }}
      />

      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 24, gap: 10 }}
        showsVerticalScrollIndicator={false}
      >
        {/* Message meta */}
        <Section title="Message">
          <Text style={[ss.subject, { color: colors.foreground }]}>
            {record.subject ?? '(no subject)'}
          </Text>
          <InfoRow
            label="From"
            value={record.sender_name
              ? `${record.sender_name} (@${record.sender_domain ?? '?'})`
              : `@${record.sender_domain ?? 'unknown'}`}
          />
          <InfoRow label="Received" value={fmtDate(record.received_at)} />
          {record.has_attachments && <InfoRow label="Attachments" value="Yes" />}
          <InfoRow label="State" value={record.lifecycle_state} />
        </Section>

        {/* Assessment */}
        {assessment && (
          <Section title="AI Assessment">
            <View style={ss.badgeRow}>
              {record.attention_level && (
                <View style={[ss.badge, { backgroundColor: alpha(levelColor, 0.12), borderColor: alpha(levelColor, 0.3) }]}>
                  <Text style={{ fontSize: 11, color: levelColor, textTransform: 'uppercase', letterSpacing: 0.4, ...font('semibold') }}>
                    {record.attention_level}
                  </Text>
                </View>
              )}
              {assessment.is_high_risk && (
                <View style={[ss.badge, { backgroundColor: alpha(T.rust, 0.12), borderColor: alpha(T.rust, 0.3) }]}>
                  <Feather name="shield" size={11} color={T.rust} />
                  <Text style={{ fontSize: 11, color: T.rust, marginLeft: 3, ...font('semibold') }}>High Risk</Text>
                </View>
              )}
              {assessment.injection_flagged && (
                <View style={[ss.badge, { backgroundColor: alpha(T.gilt, 0.12), borderColor: alpha(T.gilt, 0.3) }]}>
                  <Text style={{ fontSize: 11, color: T.gilt, ...font('semibold') }}>Injection flag</Text>
                </View>
              )}
            </View>
            <Text style={{ fontSize: 13, lineHeight: 20, marginTop: 8, color: colors.foreground, ...font('regular') }}>
              {assessment.rationale}
            </Text>
            <ConfidenceBar value={assessment.confidence} />
            <Text style={{ fontSize: 10, marginTop: 8, color: colors.mutedForeground, ...font('regular') }}>
              Model: {assessment.model_id}
            </Text>
          </Section>
        )}

        {/* Threat signals */}
        {signals.length > 0 && (
          <View style={[ss.section, { backgroundColor: alpha(T.rust, 0.06), borderColor: alpha(T.rust, 0.22) }]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              <Feather name="alert-triangle" size={13} color={T.rust} />
              <Text style={{ fontSize: 11, color: T.rust, textTransform: 'uppercase', letterSpacing: 0.4, ...font('semibold') }}>
                Threat signals
              </Text>
            </View>
            {signals.map((s, i) => (
              <Text key={i} style={{ fontSize: 12, lineHeight: 18, color: T.rust, ...font('regular') }}>• {s}</Text>
            ))}
          </View>
        )}

        {/* Suggested reply preview */}
        {assessment?.suggested_reply && (
          <Section title="Suggested reply">
            <Text style={{ fontSize: 12, lineHeight: 18, fontStyle: 'italic', color: colors.mutedForeground, ...font('regular') }}>
              {assessment.suggested_reply.slice(0, 240)}
              {assessment.suggested_reply.length > 240 ? '…' : ''}
            </Text>
          </Section>
        )}

        {/* Action buttons */}
        <View style={ss.actions}>
          {draftAction && !record.is_high_risk && (
            <Pressable
              style={[ss.primaryBtn, { backgroundColor: colors.primary }, acting && { opacity: 0.6 }]}
              onPress={() => handleCompose(draftAction)}
              disabled={acting}
            >
              {acting
                ? <ActivityIndicator size="small" color="#fff" />
                : <Feather name="mail" size={16} color="#fff" />
              }
              <Text style={{ fontSize: 15, color: '#fff', marginLeft: 8, ...font('semibold') }}>
                {summary?.send_enabled ? 'Compose & send reply' : 'Compose reply'}
              </Text>
            </Pressable>
          )}

          {moveAction && (
            <Pressable
              style={[ss.outlineBtn, { borderColor: colors.border }, acting && { opacity: 0.6 }]}
              onPress={() => handleMove(moveAction)}
              disabled={acting}
            >
              <Feather name="arrow-right" size={15} color={colors.foreground} />
              <Text style={{ fontSize: 14, color: colors.foreground, marginLeft: 6, ...font('medium') }}>Move to Review</Text>
            </Pressable>
          )}

          <Pressable style={ss.ghostBtn} onPress={() => router.back()}>
            <Text style={{ fontSize: 14, color: colors.mutedForeground, ...font('regular') }}>Defer — back to queue</Text>
          </Pressable>
        </View>

        {record.is_high_risk && draftAction && (
          <View style={[ss.riskWarn, { backgroundColor: alpha(T.rust, 0.08), borderColor: alpha(T.rust, 0.22) }]}>
            <Feather name="shield" size={13} color={T.rust} />
            <Text style={{ fontSize: 12, color: T.rust, flex: 1, marginLeft: 6, lineHeight: 18, ...font('regular') }}>
              Compose is disabled for high-risk messages. Review the threat signals above before acting.
            </Text>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const ss = StyleSheet.create({
  root: { flex: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  section: { borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, padding: 14 },
  sectionTitle: { fontSize: 10, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 },
  subject: { fontSize: 16, fontFamily: 'Inter_600SemiBold', lineHeight: 22, marginBottom: 10 },
  infoRow: { flexDirection: 'row', paddingVertical: 3 },
  badgeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 5 },
  badge: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 3 },
  confTrack: { flex: 1, height: 5, borderRadius: 3 },
  actions: { gap: 8, marginTop: 4 },
  primaryBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', paddingVertical: 13, borderRadius: 10 },
  outlineBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', paddingVertical: 12, borderRadius: 10, borderWidth: 1 },
  ghostBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', paddingVertical: 10 },
  riskWarn: { flexDirection: 'row', alignItems: 'flex-start', borderWidth: 1, borderRadius: 8, padding: 10 },
});
