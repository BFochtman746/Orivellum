/**
 * A-01 Mail Steward — /mail/connect
 * Microsoft device-code OAuth flow.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Clipboard,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Feather } from '@expo/vector-icons';
import { Stack, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens } from '@/lib/tokens';
import { font } from '@/lib/typography';
import { mobileFetchJson } from '@/lib/api';
import * as Haptics from 'expo-haptics';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

type Step = 'idle' | 'pending' | 'polling' | 'done' | 'error';

interface DeviceCodeResponse {
  user_code: string;
  verification_uri: string;
  handle: string;
}

interface PollResponse {
  status: 'pending' | 'connected';
  display_name?: string;
  mail?: string;
}

export default function MailConnectScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [step, setStep] = useState<Step>('idle');
  const [userCode, setUserCode] = useState('');
  const [verifyUrl, setVerifyUrl] = useState('');
  const [handle, setHandle] = useState('');
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  useEffect(() => () => stopPoll(), [stopPoll]);

  const doPoll = useCallback(async (h: string) => {
    try {
      const data = await mobileFetchJson<PollResponse>(`${API}/mail/connect/poll`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ handle: h }),
      });
      if (data.status === 'connected') {
        stopPoll();
        setStep('done');
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
        setTimeout(() => router.replace('/mail' as any), 1400);
      }
    } catch (e: any) {
      const msg: string = e.message ?? '';
      if (msg.includes('not found') || msg.includes('expired')) {
        stopPoll();
        setError('Session expired — please try again.');
        setStep('error');
      }
    }
  }, [stopPoll, router]);

  const handleStart = useCallback(async () => {
    setStep('pending');
    setError('');
    try {
      const data = await mobileFetchJson<DeviceCodeResponse>(`${API}/mail/connect/start`, { method: 'POST' });
      setUserCode(data.user_code);
      setVerifyUrl(data.verification_uri);
      setHandle(data.handle);
      setStep('polling');
      pollRef.current = setInterval(() => doPoll(data.handle), 5000);
    } catch (e: any) {
      setError(e.message ?? 'Failed to start connection');
      setStep('error');
    }
  }, [doPoll]);

  const handleCopy = useCallback(() => {
    Clipboard.setString(userCode);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
  }, [userCode]);

  return (
    <View style={[ss.root, { backgroundColor: colors.background, paddingBottom: insets.bottom + 20 }]}>
      <Stack.Screen
        options={{
          title: 'Connect Outlook',
          headerShown: true,
          headerStyle: { backgroundColor: colors.background },
          headerTintColor: colors.foreground,
        }}
      />

      <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: 'center', padding: 24 }}>
        <View style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={[ss.iconWrap, { backgroundColor: `${T.gilt}18` }]}>
            <Feather name="mail" size={28} color={T.gilt} />
          </View>
          <Text style={[ss.title, { color: colors.foreground }]}>Connect your Outlook</Text>

          {step === 'idle' && (
            <>
              <Text style={{ fontSize: 13, lineHeight: 20, width: '100%', marginBottom: 18, color: colors.mutedForeground, ...font('regular') }}>
                Orivellum uses a device-code flow — no password is stored.{'\n\n'}
                {'• '}Only subject, sender domain, and AI analysis are retained{'\n'}
                {'• '}Message body is never persisted{'\n'}
                {'• '}Disconnect at any time from Mail settings
              </Text>
              <Pressable style={[ss.primaryBtn, { backgroundColor: colors.primary }]} onPress={handleStart}>
                <Feather name="link" size={16} color="#fff" />
                <Text style={{ fontSize: 15, color: '#fff', marginLeft: 8, ...font('semibold') }}>Start connection</Text>
              </Pressable>
            </>
          )}

          {step === 'pending' && (
            <View style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 20 }}>
              <ActivityIndicator color={colors.primary} />
              <Text style={{ fontSize: 14, marginLeft: 10, color: colors.mutedForeground, ...font('regular') }}>
                Requesting device code…
              </Text>
            </View>
          )}

          {step === 'polling' && (
            <>
              <Text style={{ fontSize: 13, lineHeight: 20, width: '100%', marginBottom: 14, color: colors.mutedForeground, ...font('regular') }}>
                1. Open <Text style={{ color: colors.foreground, ...font('semibold') }}>microsoft.com/devicelogin</Text>{'\n'}
                2. Enter this code:
              </Text>

              <Pressable
                style={[ss.codeBox, { backgroundColor: colors.muted, borderColor: colors.border }]}
                onPress={handleCopy}
                accessibilityLabel="Copy code"
              >
                <Text style={[ss.code, { color: colors.foreground }]}>{userCode}</Text>
                <Feather name="copy" size={16} color={colors.mutedForeground} style={{ marginLeft: 8 }} />
              </Pressable>

              <Pressable
                style={[ss.outlineBtn, { borderColor: colors.border, marginBottom: 14 }]}
                onPress={() => verifyUrl && Linking.openURL(verifyUrl)}
              >
                <Feather name="external-link" size={14} color={colors.foreground} />
                <Text style={{ fontSize: 14, color: colors.foreground, marginLeft: 6, ...font('medium') }}>
                  Open Microsoft sign-in
                </Text>
              </Pressable>

              <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 14 }}>
                <ActivityIndicator size="small" color={colors.primary} />
                <Text style={{ fontSize: 12, marginLeft: 8, color: colors.mutedForeground, ...font('regular') }}>
                  Waiting for sign-in…
                </Text>
              </View>

              <Pressable
                style={{ alignSelf: 'center', padding: 8 }}
                onPress={() => { stopPoll(); setStep('idle'); }}
              >
                <Text style={{ fontSize: 13, color: colors.mutedForeground, ...font('regular') }}>Cancel</Text>
              </Pressable>
            </>
          )}

          {step === 'done' && (
            <View style={{ alignItems: 'center', paddingVertical: 16, gap: 8 }}>
              <Feather name="check-circle" size={40} color={T.green} />
              <Text style={{ fontSize: 16, color: colors.foreground, ...font('semibold') }}>Connected!</Text>
              <Text style={{ fontSize: 13, color: colors.mutedForeground, ...font('regular') }}>Redirecting to Mail…</Text>
            </View>
          )}

          {step === 'error' && (
            <>
              <Text style={{ fontSize: 13, marginBottom: 16, color: T.rust, ...font('regular') }}>{error}</Text>
              <Pressable style={[ss.primaryBtn, { backgroundColor: colors.primary }]} onPress={handleStart}>
                <Text style={{ fontSize: 15, color: '#fff', ...font('semibold') }}>Try again</Text>
              </Pressable>
            </>
          )}
        </View>

        {step !== 'polling' && step !== 'done' && (
          <Pressable style={{ alignSelf: 'center', marginTop: 16, padding: 8 }} onPress={() => router.back()}>
            <Text style={{ fontSize: 13, color: colors.mutedForeground, ...font('regular') }}>← Back to Mail</Text>
          </Pressable>
        )}
      </ScrollView>
    </View>
  );
}

const ss = StyleSheet.create({
  root: { flex: 1 },
  card: { borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, padding: 24, alignItems: 'center' },
  iconWrap: { width: 56, height: 56, borderRadius: 28, alignItems: 'center', justifyContent: 'center', marginBottom: 14 },
  title: { fontSize: 18, fontFamily: 'Inter_600SemiBold', marginBottom: 12, textAlign: 'center' },
  primaryBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', width: '100%', paddingVertical: 12, borderRadius: 8 },
  outlineBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', width: '100%', paddingVertical: 11, borderRadius: 8, borderWidth: 1 },
  codeBox: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', borderRadius: 10, borderWidth: 1, padding: 14, width: '100%', marginBottom: 14 },
  code: { fontSize: 22, fontFamily: 'Inter_700Bold', letterSpacing: 4, flex: 1, textAlign: 'center' },
});
