/**
 * A-01 Mail Steward — /mail/settings (mobile)
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Feather } from '@expo/vector-icons';
import { Stack, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens, alpha } from '@/lib/tokens';
import { font } from '@/lib/typography';
import { mobileFetchJson } from '@/lib/api';
import * as Haptics from 'expo-haptics';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

interface MailSettings {
  send_enabled: boolean;
  lemonade_url: string;
  lemonade_model: string;
  sync_folders: string[];
  account_display: string;
  threat_feeds_enabled: boolean;
  context_days: number;
}

interface MailSummary { connected: boolean }

function Field({ label, value, onChangeText, placeholder, mono = false }: {
  label: string; value: string; onChangeText: (v: string) => void;
  placeholder?: string; mono?: boolean;
}) {
  const colors = useColors();
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={{ fontSize: 11, marginBottom: 4, color: colors.mutedForeground, ...font('medium') }}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.mutedForeground}
        style={[ss.textInput, { backgroundColor: colors.muted, borderColor: colors.border, color: colors.foreground, fontSize: mono ? 12 : 14, fontFamily: 'Inter_400Regular' }]}
        autoCapitalize="none"
        autoCorrect={false}
      />
    </View>
  );
}

export default function MailSettingsScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const qc = useQueryClient();

  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [lemonadeUrl, setLemonadeUrl] = useState('');
  const [lemonadeModel, setLemonadeModel] = useState('');
  const [syncFolders, setSyncFolders] = useState('inbox');
  const [sendEnabled, setSendEnabled] = useState(false);
  const [feedsEnabled, setFeedsEnabled] = useState(true);
  const [contextDays, setContextDays] = useState('30');

  const { data: settings, isLoading } = useQuery<MailSettings>({
    queryKey: ['mail-settings'],
    queryFn: () => mobileFetchJson(`${API}/mail/settings`),
    staleTime: 60_000,
  });

  const { data: summary } = useQuery<MailSummary>({
    queryKey: ['mail-summary'],
    queryFn: () => mobileFetchJson(`${API}/mail/summary`),
    staleTime: 30_000,
  });

  useEffect(() => {
    if (!settings) return;
    setLemonadeUrl(settings.lemonade_url ?? '');
    setLemonadeModel(settings.lemonade_model ?? '');
    setSyncFolders((settings.sync_folders ?? ['inbox']).join(', '));
    setSendEnabled(settings.send_enabled);
    setFeedsEnabled(settings.threat_feeds_enabled);
    setContextDays(String(settings.context_days ?? 30));
  }, [settings]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const folders = syncFolders.split(',').map(s => s.trim()).filter(Boolean);
      await mobileFetchJson(`${API}/mail/settings`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lemonade_url: lemonadeUrl,
          lemonade_model: lemonadeModel,
          sync_folders: folders.length ? folders : ['inbox'],
          send_enabled: sendEnabled,
          threat_feeds_enabled: feedsEnabled,
          context_days: Math.max(0, parseInt(contextDays, 10) || 0),
        }),
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      qc.invalidateQueries({ queryKey: ['mail-settings'] });
      qc.invalidateQueries({ queryKey: ['mail-summary'] });
      Alert.alert('Saved', 'Mail settings updated.');
    } catch (e: any) {
      Alert.alert('Save failed', e.message ?? 'Could not save settings');
    } finally {
      setSaving(false);
    }
  }, [lemonadeUrl, lemonadeModel, syncFolders, sendEnabled, feedsEnabled, contextDays, qc]);

  const handleDisconnect = useCallback(() => {
    Alert.alert(
      'Disconnect Outlook',
      'All synced records remain but no new mail will be fetched.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Disconnect', style: 'destructive',
          onPress: async () => {
            setDisconnecting(true);
            try {
              await mobileFetchJson(`${API}/mail/disconnect`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ confirm: 'disconnect' }),
              });
              Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
              qc.invalidateQueries({ queryKey: ['mail-summary'] });
              router.replace('/mail' as any);
            } catch (e: any) {
              Alert.alert('Disconnect failed', e.message ?? 'Could not disconnect');
            } finally {
              setDisconnecting(false);
            }
          },
        },
      ],
    );
  }, [qc, router]);

  return (
    <View style={[ss.root, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          title: 'Mail settings',
          headerShown: true,
          headerStyle: { backgroundColor: colors.background },
          headerTintColor: colors.foreground,
        }}
      />
      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 24, gap: 12 }}>

        {/* Account */}
        <View style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Text style={[ss.cardTitle, { color: colors.foreground }]}>Account</Text>
          {isLoading ? (
            <ActivityIndicator color={colors.primary} />
          ) : settings?.account_display ? (
            <>
              <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 10 }}>
                <Feather name="check-circle" size={14} color={T.green} />
                <Text style={{ fontSize: 13, marginLeft: 6, flex: 1, color: colors.foreground, ...font('medium') }}>
                  {settings.account_display}
                </Text>
              </View>
              {summary?.connected && (
                <Pressable
                  style={[ss.dangerBtn, { borderColor: alpha(T.rust, 0.4) }]}
                  onPress={handleDisconnect}
                  disabled={disconnecting}
                >
                  {disconnecting
                    ? <ActivityIndicator size="small" color={T.rust} />
                    : <Feather name="log-out" size={14} color={T.rust} />
                  }
                  <Text style={{ fontSize: 13, color: T.rust, marginLeft: 6, ...font('medium') }}>Disconnect Outlook</Text>
                </Pressable>
              )}
            </>
          ) : (
            <Pressable
              style={[ss.primaryBtn, { backgroundColor: colors.primary }]}
              onPress={() => router.push('/mail/connect' as any)}
            >
              <Feather name="link" size={14} color="#fff" />
              <Text style={{ fontSize: 14, color: '#fff', marginLeft: 6, ...font('semibold') }}>Connect Outlook</Text>
            </Pressable>
          )}
        </View>

        {/* Send gate */}
        <View style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Text style={[ss.cardTitle, { color: colors.foreground }]}>Send gate</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 13, color: colors.foreground, ...font('medium') }}>Enable send</Text>
              <Text style={{ fontSize: 11, marginTop: 2, lineHeight: 16, color: colors.mutedForeground, ...font('regular') }}>
                Requires Mail.Send delegated permission in your Entra app
              </Text>
            </View>
            <Switch
              value={sendEnabled}
              onValueChange={setSendEnabled}
              trackColor={{ true: colors.primary, false: colors.muted }}
            />
          </View>
        </View>

        {/* AI model */}
        <View style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Text style={[ss.cardTitle, { color: colors.foreground }]}>Local AI (Lemonade)</Text>
          <Field label="URL" value={lemonadeUrl} onChangeText={setLemonadeUrl} placeholder="http://127.0.0.1:13305/api/v1" mono />
          <Field label="Model (blank = server default)" value={lemonadeModel} onChangeText={setLemonadeModel} placeholder="" mono />
        </View>

        {/* Sync folders */}
        <View style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Text style={[ss.cardTitle, { color: colors.foreground }]}>Sync folders</Text>
          <Field label="Comma-separated folder names" value={syncFolders} onChangeText={setSyncFolders} placeholder="inbox" mono />
        </View>

        {/* Chat context window */}
        <View style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Text style={[ss.cardTitle, { color: colors.foreground }]}>Chat context window</Text>
          <Text style={{ fontSize: 11, marginBottom: 6, color: colors.mutedForeground, ...font('medium') }}>Days</Text>
          <TextInput
            value={contextDays}
            onChangeText={setContextDays}
            keyboardType="numeric"
            placeholder="30"
            placeholderTextColor={colors.mutedForeground}
            style={[ss.textInput, { backgroundColor: colors.muted, borderColor: colors.border, color: colors.foreground, fontFamily: 'Inter_400Regular', width: 80 }]}
          />
          <Text style={{ fontSize: 11, marginTop: 8, lineHeight: 16, color: colors.mutedForeground, ...font('regular') }}>
            Only emails received within this many days are injected into chat. Set to 0 to include all time.
          </Text>
        </View>

        {/* Threat feeds */}
        <View style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Text style={[ss.cardTitle, { color: colors.foreground }]}>Threat feeds</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <Text style={{ fontSize: 13, color: colors.foreground, flex: 1, ...font('medium') }}>
              Enable OpenPhish + URLhaus
            </Text>
            <Switch
              value={feedsEnabled}
              onValueChange={setFeedsEnabled}
              trackColor={{ true: colors.primary, false: colors.muted }}
            />
          </View>
        </View>

        <Pressable
          style={[ss.primaryBtn, { backgroundColor: colors.primary }, saving && { opacity: 0.6 }]}
          onPress={handleSave}
          disabled={saving}
        >
          {saving ? <ActivityIndicator size="small" color="#fff" /> : <Feather name="save" size={15} color="#fff" />}
          <Text style={{ fontSize: 15, color: '#fff', marginLeft: 8, ...font('semibold') }}>Save settings</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

const ss = StyleSheet.create({
  root: { flex: 1 },
  card: { borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, padding: 14 },
  cardTitle: { fontSize: 13, fontFamily: 'Inter_600SemiBold', marginBottom: 12 },
  textInput: { borderRadius: 8, borderWidth: StyleSheet.hairlineWidth, padding: 9 },
  primaryBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', paddingVertical: 13, borderRadius: 10 },
  dangerBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderRadius: 8, paddingVertical: 9 },
});
