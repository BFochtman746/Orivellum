/**
 * A-01 Mail Steward — /mail/compose/:actionRequestId
 * Edit and optionally send a reply draft.
 *
 * sendNonce is fetched fresh at send time (never stored in URL or state) to
 * prevent single-use authorization tokens from leaking into logs.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
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
import { executeSendFlow } from '@/lib/mail-send-flow';
import type { SendFlowFetch, SendFlowResult } from '@/lib/mail-send-flow';
import * as Haptics from 'expo-haptics';
import { apiOrigin } from '@/lib/server';

const DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const API = () => `${DOMAIN()}/api`;

interface DecisionDetail {
  record: { id: string; subject: string | null; sender_domain: string | null };
  assessment: { suggested_reply: string | null; rationale: string; is_high_risk: boolean; model_id: string } | null;
}

interface MailSummary { send_enabled: boolean }

export default function ComposeScreen() {
  const { actionRequestId } = useLocalSearchParams<{ actionRequestId: string }>();
  const { recordId } = useLocalSearchParams<{ recordId?: string }>();

  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const qc = useQueryClient();

  const [bodyText, setBodyText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);

  const { data: detail } = useQuery<DecisionDetail>({
    queryKey: ['mail-decision', recordId],
    queryFn: () => mobileFetchJson(`${API()}/mail/decisions/${recordId}`),
    enabled: !!recordId,
    staleTime: 60_000,
  });

  const { data: summary } = useQuery<MailSummary>({
    queryKey: ['mail-summary'],
    queryFn: () => mobileFetchJson(`${API()}/mail/summary`),
    staleTime: 30_000,
  });

  useEffect(() => {
    if (bodyText === null && detail?.assessment?.suggested_reply) {
      setBodyText(detail.assessment.suggested_reply);
    }
  }, [detail, bodyText]);

  /** Save draft; returns true on success. */
  const saveDraft = useCallback(async (): Promise<boolean> => {
    if (!actionRequestId) return false;
    setSaving(true);
    try {
      await mobileFetchJson(`${API()}/mail/drafts/${actionRequestId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body_text: bodyText }),
      });
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
      return true;
    } catch (e: any) {
      Alert.alert('Save failed', e.message ?? 'Could not save draft');
      return false;
    } finally {
      setSaving(false);
    }
  }, [actionRequestId, bodyText]);

  const handleSend = useCallback(async () => {
    if (!actionRequestId || !recordId) return;
    setSending(true);
    try {
      // Delegates to the exported executeSendFlow: PATCH → nonce → send.
      // Aborts at the first failure so we never deliver a stale draft.
      const result = await executeSendFlow(
        actionRequestId, recordId, bodyText, mobileFetchJson, API(),
      );
      if (!result.success) {
        Alert.alert('Send failed', result.error ?? 'Could not send reply');
        return;
      }
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      qc.invalidateQueries({ queryKey: ['mail-attention'] });
      qc.invalidateQueries({ queryKey: ['mail-summary'] });
      Alert.alert('Sent', 'Your reply was sent via Outlook.', [
        { text: 'OK', onPress: () => router.replace('/mail' as any) },
      ]);
    } finally {
      setSending(false);
    }
  }, [actionRequestId, recordId, bodyText, qc, router]);

  const sendEnabled = !!summary?.send_enabled;
  const busy = saving || sending;

  return (
    <KeyboardAvoidingView
      style={[ss.root, { backgroundColor: colors.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      keyboardVerticalOffset={90}
    >
      <Stack.Screen
        options={{
          title: detail?.record.subject ? `Re: ${detail.record.subject}` : 'Compose reply',
          headerShown: true,
          headerStyle: { backgroundColor: colors.background },
          headerTintColor: colors.foreground,
          headerRight: () => (
            <View style={{ flexDirection: 'row', gap: 6 }}>
              <Pressable
                onPress={() => saveDraft()}
                disabled={busy}
                style={ss.headerBtn}
                accessibilityLabel="Save draft"
              >
                {saving
                  ? <ActivityIndicator size="small" color={colors.primary} />
                  : <Feather name="save" size={18} color={colors.foreground} />
                }
              </Pressable>
              {sendEnabled && (
                <Pressable
                  onPress={handleSend}
                  disabled={busy}
                  style={[ss.sendBtn, { backgroundColor: colors.primary }]}
                  accessibilityLabel="Send reply"
                >
                  {sending
                    ? <ActivityIndicator size="small" color="#fff" />
                    : <Feather name="send" size={15} color="#fff" />
                  }
                </Pressable>
              )}
            </View>
          ),
        }}
      />

      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 20 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* Sender context */}
        {detail && (
          <View style={[ss.contextStrip, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Feather name="mail" size={12} color={colors.mutedForeground} />
            <Text style={{ fontSize: 12, marginLeft: 6, color: colors.mutedForeground, flex: 1, ...font('regular') }} numberOfLines={1}>
              to @{detail.record.sender_domain ?? 'unknown'}
            </Text>
          </View>
        )}

        {/* Send-disabled notice */}
        {!sendEnabled && (
          <View style={[ss.notice, { backgroundColor: alpha(T.gilt, 0.08), borderColor: alpha(T.gilt, 0.25) }]}>
            <Feather name="alert-triangle" size={12} color={T.gilt} />
            <Text style={{ fontSize: 12, color: T.gilt, flex: 1, marginLeft: 6, lineHeight: 18, ...font('regular') }}>
              Send is disabled — draft saved to Outlook. Enable send in Mail settings.
            </Text>
          </View>
        )}

        {/* Editor */}
        <TextInput
          style={[
            ss.editor,
            {
              backgroundColor: colors.card,
              borderColor: colors.border,
              color: colors.foreground,
              fontFamily: 'Inter_400Regular',
            },
          ]}
          multiline
          placeholder="Write your reply…"
          placeholderTextColor={colors.mutedForeground}
          value={bodyText ?? ''}
          onChangeText={setBodyText}
          textAlignVertical="top"
        />

        {/* Assessment card */}
        {detail?.assessment && (
          <View style={[ss.assessCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4, color: colors.mutedForeground, ...font('semibold') }}>
              AI Assessment
            </Text>
            <Text style={{ fontSize: 12, lineHeight: 18, color: colors.mutedForeground, ...font('regular') }} numberOfLines={4}>
              {detail.assessment.rationale}
            </Text>
            <Text style={{ fontSize: 10, marginTop: 6, color: colors.mutedForeground, ...font('regular') }}>
              {detail.assessment.model_id}
            </Text>
          </View>
        )}

        {/* Bottom send button */}
        {sendEnabled && (
          <Pressable
            style={[ss.sendBtnFull, { backgroundColor: colors.primary }, busy && { opacity: 0.6 }]}
            onPress={handleSend}
            disabled={busy}
          >
            {sending
              ? <ActivityIndicator size="small" color="#fff" />
              : <Feather name="send" size={16} color="#fff" />
            }
            <Text style={{ fontSize: 15, color: '#fff', marginLeft: 8, ...font('semibold') }}>Send reply</Text>
          </Pressable>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const ss = StyleSheet.create({
  root: { flex: 1 },
  headerBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  sendBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center', borderRadius: 8 },
  contextStrip: { flexDirection: 'row', alignItems: 'center', borderRadius: 8, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 10, paddingVertical: 7, marginBottom: 10 },
  notice: { flexDirection: 'row', alignItems: 'flex-start', borderRadius: 8, borderWidth: 1, paddingHorizontal: 10, paddingVertical: 8, marginBottom: 10 },
  editor: { minHeight: 240, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, padding: 12, fontSize: 14, lineHeight: 22, marginBottom: 12 },
  assessCard: { borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, padding: 12, marginBottom: 12 },
  sendBtnFull: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', paddingVertical: 13, borderRadius: 10 },
});
