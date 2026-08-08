/**
 * A-01 Mail Steward — /mail
 *
 * Attention queue: high → medium → low, newest first within each tier.
 * Swipe right → defer. Swipe left → reveal action tray (Reply / Move / Defer).
 * If Outlook is not connected, shows a connect prompt.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withSpring,
  withTiming,
  runOnJS,
} from 'react-native-reanimated';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import { Feather } from '@expo/vector-icons';
import { Stack, useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens, alpha } from '@/lib/tokens';
import { font } from '@/lib/typography';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { mobileFetchJson } from '@/lib/api';
import * as Haptics from 'expo-haptics';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

const SWIPE_THRESHOLD = 60;
const SWIPE_EXIT = 420;
const TRAY_WIDTH = 168; // 3 buttons × 56 px each

// ── Types ─────────────────────────────────────────────────────────────────────

interface MailRecord {
  id: string;
  subject: string | null;
  sender_name: string | null;
  sender_domain: string | null;
  received_at: string | null;
  is_read: boolean;
  attention_level: string | null;
  needs_reply: boolean | null;
  is_high_risk: boolean | null;
  confidence: number | null;
  lifecycle_state: string;
}

interface AttentionResponse {
  decisions: MailRecord[];
  total: number;
}

interface MailSummary {
  connected: boolean;
  send_enabled: boolean;
  high_attention: number;
  unread: number;
  total_synced: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86_400_000);
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'short' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function sorted(items: MailRecord[]): MailRecord[] {
  const ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };
  return [...items].sort((a, b) => {
    const la = ORDER[a.attention_level ?? 'low'] ?? 2;
    const lb = ORDER[b.attention_level ?? 'low'] ?? 2;
    if (la !== lb) return la - lb;
    return (b.received_at ?? '').localeCompare(a.received_at ?? '');
  });
}

// ── Tray action button ────────────────────────────────────────────────────────

function TrayButton({
  icon, label, color, onPress,
}: {
  icon: React.ComponentProps<typeof Feather>['name'];
  label: string;
  color: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      style={({ pressed }) => [ss.trayBtn, { opacity: pressed ? 0.6 : 1 }]}
      onPress={onPress}
    >
      <Feather name={icon} size={20} color={color} />
      <Text style={{ fontSize: 10, color, marginTop: 4, ...font('medium') }}>{label}</Text>
    </Pressable>
  );
}

// ── Swipeable card ────────────────────────────────────────────────────────────

function MailCard({ record, onOpen, onDefer, onReply, onMove }: {
  record: MailRecord;
  onOpen: () => void;
  onDefer: () => void;
  onReply: () => void;
  onMove: () => void;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  const translateX = useSharedValue(0);
  const opacity = useSharedValue(1);
  /** true when the action tray is snapped open */
  const isOpen = useSharedValue(false);

  const levelColor =
    record.attention_level === 'high' ? T.rust :
    record.attention_level === 'medium' ? T.gilt :
    colors.mutedForeground;

  /** Snap card back and close the tray — callable from JS thread */
  const closeTray = useCallback(() => {
    translateX.value = withSpring(0, { stiffness: 300, damping: 30 });
    isOpen.value = false;
  }, [translateX, isOpen]);

  const pan = Gesture.Pan()
    .onUpdate((e) => {
      if (isOpen.value) {
        // Tray open: clamp motion between fully-open and closed
        translateX.value = Math.max(-TRAY_WIDTH, Math.min(0, -TRAY_WIDTH + e.translationX));
      } else {
        // Tray closed: allow full right-exit OR left reveal (clamped at tray width)
        translateX.value = Math.max(-TRAY_WIDTH, Math.min(SWIPE_EXIT, e.translationX));
      }
    })
    .onEnd((e) => {
      if (isOpen.value) {
        // Swipe right past threshold → close tray
        if (e.translationX > 30 || e.velocityX > 300) {
          translateX.value = withSpring(0, { stiffness: 300, damping: 30 });
          isOpen.value = false;
        } else {
          translateX.value = withSpring(-TRAY_WIDTH, { stiffness: 300, damping: 30 });
        }
      } else if (e.translationX > SWIPE_THRESHOLD) {
        // Right swipe → defer (card exits)
        translateX.value = withSpring(SWIPE_EXIT, { stiffness: 200, damping: 20 });
        opacity.value = withTiming(0, { duration: 160 });
        runOnJS(Haptics.notificationAsync)(Haptics.NotificationFeedbackType.Warning);
        runOnJS(onDefer)();
      } else if (e.translationX < -(TRAY_WIDTH / 2)) {
        // Left swipe past half-tray width → reveal tray
        translateX.value = withSpring(-TRAY_WIDTH, { stiffness: 250, damping: 28 });
        isOpen.value = true;
        runOnJS(Haptics.impactAsync)(Haptics.ImpactFeedbackStyle.Light);
      } else {
        translateX.value = withSpring(0, { stiffness: 300, damping: 30 });
      }
    });

  const cardStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: translateX.value }],
    opacity: opacity.value,
  }));

  /** Tray fades in as the card slides left; stays invisible during right-swipe defer */
  const trayStyle = useAnimatedStyle(() => ({
    opacity: translateX.value < 0
      ? Math.min(1, Math.max(0, (-translateX.value / TRAY_WIDTH) * 1.5))
      : 0,
  }));

  const handleCardPress = useCallback(() => {
    if (isOpen.value) {
      closeTray();
    } else {
      onOpen();
    }
  }, [isOpen, closeTray, onOpen]);

  /**
   * Reply tray action — respects the same high-risk compose gate enforced by
   * the detail screen: high-risk cards navigate to detail without autoCompose
   * so compose remains blocked there too.
   */
  const handleReplyPress = useCallback(() => {
    closeTray();
    if (record.is_high_risk) {
      onOpen(); // open detail without compose intent
    } else {
      onReply(); // open detail with autoCompose=1
    }
  }, [closeTray, record.is_high_risk, onOpen, onReply]);

  const handleMovePress = useCallback(() => {
    closeTray();
    onMove();
  }, [closeTray, onMove]);

  return (
    <View style={ss.cardContainer}>
      {/* Action tray — sits behind the card, revealed as it slides left */}
      <Animated.View style={[ss.tray, { backgroundColor: colors.card, borderColor: colors.border }, trayStyle]}>
        {/* High-risk messages: show "View" (no compose intent) instead of "Reply" */}
        <TrayButton
          icon={record.is_high_risk ? 'eye' : 'corner-up-left'}
          label={record.is_high_risk ? 'View' : 'Reply'}
          color={record.is_high_risk ? colors.mutedForeground : colors.primary}
          onPress={handleReplyPress}
        />
        <View style={[ss.traySep, { backgroundColor: colors.border }]} />
        <TrayButton icon="folder" label="Move"  color={T.green} onPress={handleMovePress} />
        <View style={[ss.traySep, { backgroundColor: colors.border }]} />
        <TrayButton icon="clock"  label="Defer" color={T.gilt}  onPress={onDefer} />
      </Animated.View>

      {/* Swipeable card — sits on top of the tray */}
      <GestureDetector gesture={pan}>
        <Animated.View style={cardStyle}>
          <Pressable
            style={[ss.card, { backgroundColor: colors.card, borderColor: colors.border }]}
            onPress={handleCardPress}
            accessibilityRole="button"
            accessibilityLabel={record.subject ?? '(no subject)'}
          >
            {!record.is_read && (
              <View style={[ss.unreadDot, { backgroundColor: colors.primary }]} />
            )}

            <View style={ss.cardHeader}>
              <Text style={[ss.cardSubject, { color: colors.foreground }]} numberOfLines={1}>
                {record.subject ?? '(no subject)'}
              </Text>
              <Text style={{ fontSize: 11, color: colors.mutedForeground, ...font('regular') }}>
                {fmtDate(record.received_at)}
              </Text>
            </View>

            <Text
              style={{ fontSize: 12, marginBottom: 6, color: colors.mutedForeground, ...font('regular') }}
              numberOfLines={1}
            >
              {record.sender_name ?? `@${record.sender_domain ?? 'unknown'}`}
              {record.sender_domain ? ` · @${record.sender_domain}` : ''}
            </Text>

            <View style={ss.chips}>
              {record.attention_level && record.attention_level !== 'low' && (
                <View style={[ss.chip, { backgroundColor: alpha(levelColor, 0.12), borderColor: alpha(levelColor, 0.3) }]}>
                  <Text style={{ fontSize: 10, color: levelColor, textTransform: 'uppercase', letterSpacing: 0.4, ...font('semibold') }}>
                    {record.attention_level}
                  </Text>
                </View>
              )}
              {record.needs_reply && (
                <View style={[ss.chip, { backgroundColor: alpha(T.gilt, 0.10), borderColor: alpha(T.gilt, 0.3) }]}>
                  <Text style={{ fontSize: 10, color: T.gilt, textTransform: 'uppercase', letterSpacing: 0.4, ...font('semibold') }}>
                    Reply
                  </Text>
                </View>
              )}
              {record.is_high_risk && (
                <View style={[ss.chip, { backgroundColor: alpha(T.rust, 0.12), borderColor: alpha(T.rust, 0.3) }]}>
                  <Feather name="shield" size={10} color={T.rust} />
                  <Text style={{ fontSize: 10, color: T.rust, marginLeft: 2, ...font('semibold') }}>Risk</Text>
                </View>
              )}
            </View>

            <View style={ss.swipeHint}>
              <Feather name="chevron-right" size={12} color={colors.mutedForeground} style={{ opacity: 0.35 }} />
              <Text style={{ fontSize: 10, color: colors.mutedForeground, opacity: 0.35, flex: 1, textAlign: 'center', ...font('regular') }}>
                right to defer · left for actions
              </Text>
              <Feather name="chevron-left" size={12} color={colors.mutedForeground} style={{ opacity: 0.35 }} />
            </View>
          </Pressable>
        </Animated.View>
      </GestureDetector>
    </View>
  );
}

// ── Connect prompt ────────────────────────────────────────────────────────────

function ConnectPrompt() {
  const colors = useColors();
  const router = useRouter();
  const T = useVellumTokens();
  return (
    <View style={[ss.connectWrap, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <Feather name="mail" size={36} color={T.gilt} style={{ marginBottom: 12 }} />
      <Text style={[ss.connectTitle, { color: colors.foreground }]}>Connect Outlook</Text>
      <Text style={[ss.connectBody, { color: colors.mutedForeground }]}>
        Link your Microsoft account to get AI-assessed email on your phone.
        Message body is never stored — only metadata and analysis.
      </Text>
      <Pressable
        style={[ss.connectBtn, { backgroundColor: colors.primary }]}
        onPress={() => router.push('/mail/connect' as any)}
      >
        <Feather name="link" size={15} color="#fff" />
        <Text style={{ fontSize: 14, color: '#fff', marginLeft: 6, ...font('semibold') }}>Connect account</Text>
      </Pressable>
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function MailScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [summary, setSummary] = useState<MailSummary | null>(null);
  const [records, setRecords] = useState<MailRecord[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [deferred, setDeferred] = useState<Set<string>>(new Set());

  const load = useCallback(async (refresh = false) => {
    try {
      const [sum, att] = await Promise.all([
        mobileFetchJson<MailSummary>(`${API}/mail/summary`),
        mobileFetchJson<AttentionResponse>(`${API}/mail/attention?limit=100`),
      ]);
      setSummary(sum);
      setRecords(sorted(att.decisions));
    } catch (e: any) {
      if (!refresh) Alert.alert('Mail', e.message ?? 'Failed to load mail');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Re-fetch immediately every time this screen comes into focus —
  // this also drives the _layout badge to re-poll right on enter.
  useFocusEffect(useCallback(() => { load(true); }, [load]));

  useEffect(() => {
    const t = setInterval(() => load(true), 30_000);
    return () => clearInterval(t);
  }, [load]);

  const handleRefresh = useCallback(() => { setRefreshing(true); load(true); }, [load]);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    try {
      await mobileFetchJson(`${API}/mail/sync`, { method: 'POST' });
      setTimeout(() => load(true), 3000);
    } catch (e: any) {
      Alert.alert('Sync failed', e.message ?? 'Could not trigger sync');
    } finally {
      setSyncing(false);
    }
  }, [load]);

  const handleDefer = useCallback((id: string) => {
    setDeferred(prev => new Set([...prev, id]));
  }, []);

  /** Fetch the decision nonce, call move API, then remove from queue */
  const handleMove = useCallback(async (id: string) => {
    Alert.alert(
      'Move to Review',
      'Move this message out of your attention queue into the Review folder?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Move',
          onPress: async () => {
            try {
              const detail = await mobileFetchJson<{
                available_actions: Array<{ type: string; nonce: string; label: string }>;
              }>(`${API}/mail/decisions/${id}`);
              const moveAction = detail.available_actions.find(a => a.type === 'MOVE');
              if (!moveAction) {
                // No move action available — treat as defer
                setDeferred(prev => new Set([...prev, id]));
                return;
              }
              await mobileFetchJson(`${API}/mail/decisions/${id}/move`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ destination: 'review', nonce: moveAction.nonce }),
              });
              Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
              setDeferred(prev => new Set([...prev, id]));
            } catch (e: any) {
              Alert.alert('Move failed', e.message ?? 'Could not move message');
            }
          },
        },
      ],
    );
  }, []);

  /** Navigate to detail with compose pre-selected */
  const handleReply = useCallback((id: string) => {
    router.push(`/mail/${id}?autoCompose=1` as any);
  }, [router]);

  const visible = records.filter(r => !deferred.has(r.id));

  const renderItem = useCallback(({ item }: { item: MailRecord }) => (
    <MailCard
      record={item}
      onOpen={() => router.push(`/mail/${item.id}` as any)}
      onDefer={() => handleDefer(item.id)}
      onReply={() => handleReply(item.id)}
      onMove={() => handleMove(item.id)}
    />
  ), [router, handleDefer, handleReply, handleMove]);

  return (
    <View style={[ss.root, { backgroundColor: colors.background }]}>
      <Stack.Screen
        options={{
          title: 'Mail',
          headerShown: true,
          headerStyle: { backgroundColor: colors.background },
          headerTintColor: colors.foreground,
          headerRight: () => (
            <View style={{ flexDirection: 'row', gap: 4 }}>
              {summary?.connected && (
                <Pressable onPress={handleSync} style={ss.headerBtn} accessibilityLabel="Sync inbox">
                  {syncing
                    ? <ActivityIndicator size="small" color={colors.primary} />
                    : <Feather name="refresh-cw" size={18} color={colors.foreground} />
                  }
                </Pressable>
              )}
              <Pressable onPress={() => router.push('/mail/settings' as any)} style={ss.headerBtn} accessibilityLabel="Mail settings">
                <Feather name="settings" size={18} color={colors.foreground} />
              </Pressable>
            </View>
          ),
        }}
      />

      {loading ? (
        <View style={{ padding: 12, gap: 8 }}>
          <SkeletonItem lines={2} />
          <SkeletonItem lines={2} />
          <SkeletonItem lines={2} />
          <SkeletonItem lines={2} />
        </View>
      ) : summary && !summary.connected ? (
        <View style={ss.center}>
          <ConnectPrompt />
        </View>
      ) : visible.length === 0 ? (
        <EmptyState
          icon="inbox"
          title="No decisions pending"
          body={`${summary?.total_synced ?? 0} messages synced`}
        />
      ) : (
        <>
          {(summary?.high_attention ?? 0) > 0 && (
            <View style={[ss.statsBar, { backgroundColor: alpha(T.rust, 0.08), borderBottomColor: alpha(T.rust, 0.18) }]}>
              <Feather name="alert-circle" size={12} color={T.rust} />
              <Text style={{ fontSize: 12, color: T.rust, marginLeft: 4, ...font('medium') }}>
                {summary!.high_attention} high-attention {summary!.high_attention === 1 ? 'message' : 'messages'}
              </Text>
            </View>
          )}
          <FlatList
            data={visible}
            keyExtractor={r => r.id}
            renderItem={renderItem}
            contentContainerStyle={{ paddingHorizontal: 12, paddingTop: 8, paddingBottom: insets.bottom + 16 }}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={colors.primary} />}
            ItemSeparatorComponent={() => <View style={{ height: 6 }} />}
          />
        </>
      )}
    </View>
  );
}

const ss = StyleSheet.create({
  root: { flex: 1 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  headerBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  statsBar: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 16, paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  card: { borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, padding: 12, paddingLeft: 16 },
  unreadDot: { position: 'absolute', left: 6, top: 18, width: 6, height: 6, borderRadius: 3 },
  cardHeader: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, marginBottom: 3 },
  cardSubject: { flex: 1, fontSize: 14, lineHeight: 20, fontFamily: 'Inter_600SemiBold' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 4, marginBottom: 6 },
  chip: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: 4, paddingHorizontal: 5, paddingVertical: 2 },
  swipeHint: { flexDirection: 'row', alignItems: 'center', marginTop: 4 },
  cardContainer: { position: 'relative' },
  tray: {
    position: 'absolute',
    right: 0,
    top: 0,
    bottom: 0,
    width: TRAY_WIDTH,
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
  },
  trayBtn: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    height: '100%',
  },
  traySep: {
    width: StyleSheet.hairlineWidth,
    height: '55%',
  },
  connectWrap: { margin: 24, padding: 28, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center' },
  connectTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold', marginBottom: 10, textAlign: 'center' },
  connectBody: { fontSize: 13, lineHeight: 20, textAlign: 'center', marginBottom: 20 },
  connectBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 20, paddingVertical: 11, borderRadius: 8 },
});
