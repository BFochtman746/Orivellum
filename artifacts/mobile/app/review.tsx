/**
 * Governance Review Queue — /review
 *
 * Mobile inbox for every item that needs a human decision before the system
 * treats it as fact: AI-extracted knowledge, reclassification flags,
 * system suggestions, and near-duplicate document pairs.
 *
 * Items are sorted most-uncertain first (confidence ascending).  Each card
 * offers Approve / Reject / Defer (7-day snooze) in one tap.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
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
import { Stack, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens, alpha } from '@/lib/tokens';
import { font } from '@/lib/typography';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { mobileFetch } from '@/lib/api';
import * as Haptics from 'expo-haptics';
import { apiOrigin } from '@/lib/server';

const SWIPE_THRESHOLD = 60;  // px to commit a swipe decision
const SWIPE_EXIT     = 450;  // px card travels before it leaves screen

const DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const API = () => `${DOMAIN()}/api`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReviewItem {
  id: string;
  item_type: 'knowledge' | 'reclassify' | 'suggestion' | 'duplicate';
  title: string;
  description: string;
  confidence: number | null;
  work_id: string | null;
  work_title: string | null;
  evidence: Record<string, unknown>;
  created_at: string;
}

interface QueueResponse {
  items: ReviewItem[];
  count: number;
  counts_by_type: Record<string, number>;
}

type ItemFilter = 'all' | ReviewItem['item_type'];

// ── Type metadata — colors resolved at render time via useVellumTokens() ─────
// Static fallback strings here are replaced inline in components.
const TYPE_META_STATIC: Record<
  ReviewItem['item_type'],
  { label: string; icon: string }
> = {
  knowledge:  { label: 'AI knowledge', icon: 'star'  },
  reclassify: { label: 'Reclassify',   icon: 'tag'   },
  suggestion: { label: 'Suggestion',   icon: 'zap'   },
  duplicate:  { label: 'Duplicate',    icon: 'copy'  },
};

const FILTERS: { key: ItemFilter; label: string }[] = [
  { key: 'all',        label: 'All' },
  { key: 'knowledge',  label: 'Knowledge' },
  { key: 'suggestion', label: 'Suggestions' },
  { key: 'duplicate',  label: 'Duplicates' },
  { key: 'reclassify', label: 'Reclassify' },
];

// ── Confidence bar ────────────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number | null }) {
  const colors = useColors();
  const T = useVellumTokens();
  if (value == null) return null;
  const pct = Math.min(100, Math.max(0, Math.round(value * 100)));
  const barColor =
    value < 0.5 ? T.rust : value < 0.8 ? T.gilt : T.green;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
      <View
        style={[
          rvStyles.confTrack,
          { backgroundColor: colors.muted },
        ]}
      >
        <View
          style={{
            height: '100%',
            width: `${pct}%`,
            backgroundColor: barColor,
            borderRadius: 3,
          }}
        />
      </View>
      <Text
        style={{
          fontSize: 10,
          lineHeight: 14,
          ...font('medium'),
          color: colors.mutedForeground,
        }}
      >
        {pct}%
      </Text>
    </View>
  );
}

// ── Evidence line ─────────────────────────────────────────────────────────────

function EvidenceLine({ item }: { item: ReviewItem }) {
  const colors = useColors();
  const ev = item.evidence ?? {};
  const parts: string[] = [];

  if (item.item_type === 'knowledge') {
    if (ev.subject && ev.predicate) {
      parts.push(
        `${ev.subject} → ${ev.predicate}${ev.object ? ` → ${ev.object}` : ''}`,
      );
    }
    if (ev.source_doc) parts.push(`Source: ${ev.source_doc}`);
  } else if (item.item_type === 'reclassify') {
    if (ev.doc_title) parts.push(String(ev.doc_title));
    if (ev.current_kind) parts.push(`Currently: ${ev.current_kind}`);
  } else if (item.item_type === 'duplicate') {
    const a = ev.doc_a_title ?? ev.doc_a_id;
    const b = ev.doc_b_title ?? ev.doc_b_id;
    if (a) parts.push(String(a));
    if (b) parts.push(String(b));
  } else if (item.item_type === 'suggestion') {
    if (Array.isArray(ev.doc_ids))
      parts.push(`${(ev.doc_ids as unknown[]).length} documents`);
  }

  if (item.work_title) parts.push(`in ${item.work_title}`);
  if (parts.length === 0) return null;

  return (
    <Text
      style={{
        fontSize: 11,
        lineHeight: 16,
        ...font('regular'),
        color: colors.mutedForeground,
      }}
      numberOfLines={2}
    >
      {parts.join(' · ')}
    </Text>
  );
}

// ── Review item card ──────────────────────────────────────────────────────────

function ReviewCard({
  item,
  onResolved,
}: {
  item: ReviewItem;
  onResolved: (id: string) => void;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  const meta = TYPE_META_STATIC[item.item_type];
  const isDupe = item.item_type === 'duplicate';

  // Resolve token-based colors per type
  const typeColor: string =
    item.item_type === 'knowledge'  ? T.gilt :
    item.item_type === 'reclassify' ? T.gilt :
    item.item_type === 'suggestion' ? T.gilt :
    /* duplicate */                   T.rust;

  const typeColorSoft: string =
    item.item_type === 'duplicate' ? T.rustSoft : T.giltSoft;

  const typeColorLine: string =
    item.item_type === 'duplicate' ? alpha(T.rust, 0.32) : T.giltLine;

  const [pending, setPending] = useState<
    'approve' | 'reject' | 'defer' | null
  >(null);
  const [canonical, setCanonical] = useState<string | null>(
    isDupe ? String(item.evidence?.doc_a_id ?? '') || null : null,
  );

  // ── Swipe animation state ─────────────────────────────────────────────────
  const translateX = useSharedValue(0);

  const resolve = async (decision: 'approve' | 'reject' | 'defer') => {
    // Haptic fires on the JS thread — works whether triggered by button or by
    // swipe (which already called runOnJS(resolve)).
    if (Platform.OS !== 'web') {
      if (decision === 'approve' || decision === 'reject') {
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
      } else {
        Haptics.selectionAsync().catch(() => {});
      }
    }
    setPending(decision);
    try {
      const r = await mobileFetch(`${API()}/review/${item.id}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          reason: '',
          ...(isDupe && decision === 'approve' && canonical
            ? { canonical_doc_id: canonical }
            : {}),
        }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${r.status}`);
      }
      onResolved(item.id);
    } catch (e: any) {
      // Snap card back on failure (handles both button-tap and swipe paths)
      translateX.value = withSpring(0);
      Alert.alert(
        'Could not resolve',
        e?.message ?? 'Something went wrong. Please try again.',
        [{ text: 'OK' }],
      );
    } finally {
      setPending(null);
    }
  };

  // Pan gesture — horizontal-only, fails on vertical scroll so the FlatList
  // can still scroll normally.
  const pan = Gesture.Pan()
    .activeOffsetX([-10, 10])
    .failOffsetY([-8, 8])
    .enabled(pending === null)
    .onUpdate(e => {
      translateX.value = e.translationX;
    })
    .onEnd(e => {
      if (e.translationX >= SWIPE_THRESHOLD) {
        // Commit approve: animate card off to the right, then fire API
        translateX.value = withTiming(SWIPE_EXIT, { duration: 220 });
        runOnJS(resolve)('approve');
      } else if (e.translationX <= -SWIPE_THRESHOLD) {
        // Commit reject: animate card off to the left, then fire API
        translateX.value = withTiming(-SWIPE_EXIT, { duration: 220 });
        runOnJS(resolve)('reject');
      } else {
        // Below threshold — bounce back
        translateX.value = withSpring(0, { damping: 18, stiffness: 200 });
      }
    });

  // Card slides with the finger
  const cardAnimStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: translateX.value }],
  }));

  // Green hint fades in as card moves right
  const approveHintStyle = useAnimatedStyle(() => {
    const opacity = Math.min(1, Math.max(0, translateX.value / SWIPE_THRESHOLD));
    return { opacity };
  });

  // Red hint fades in as card moves left
  const rejectHintStyle = useAnimatedStyle(() => {
    const opacity = Math.min(1, Math.max(0, -translateX.value / SWIPE_THRESHOLD));
    return { opacity };
  });

  return (
    <View style={rvStyles.swipeContainer}>
      {/* ── Approve hint (revealed behind card when swiping right) ── */}
      <Animated.View style={[rvStyles.swipeHintLeft, { backgroundColor: T.greenSoft }, approveHintStyle]}>
        <Feather name="thumbs-up" size={22} color={T.green} />
        <Text style={{ fontSize: 12, lineHeight: 18, ...font('bold'), color: T.green, marginTop: 3 }}>
          Approve
        </Text>
      </Animated.View>

      {/* ── Reject hint (revealed behind card when swiping left) ── */}
      <Animated.View style={[rvStyles.swipeHintRight, { backgroundColor: T.rustSoft }, rejectHintStyle]}>
        <Feather name="thumbs-down" size={22} color={T.rust} />
        <Text style={{ fontSize: 12, lineHeight: 18, ...font('bold'), color: T.rust, marginTop: 3 }}>
          Reject
        </Text>
      </Animated.View>

      {/* ── The card itself ── */}
      <GestureDetector gesture={pan}>
        <Animated.View
          style={[
            rvStyles.card,
            {
              backgroundColor: colors.card,
              borderColor: colors.border,
              borderLeftColor: typeColor,
            },
            cardAnimStyle,
          ]}
        >
          {/* Type badge + confidence */}
          <View style={rvStyles.cardHeader}>
            <View
              style={[
                rvStyles.typeBadge,
                {
                  backgroundColor: typeColorSoft,
                  borderColor: typeColorLine,
                },
              ]}
            >
              <Feather name={meta.icon as any} size={11} color={typeColor} />
              <Text
                style={{
                  fontSize: 11,
                  lineHeight: 14,
                  ...font('medium'),
                  color: typeColor,
                }}
              >
                {meta.label}
              </Text>
            </View>
            <ConfidenceBar value={item.confidence} />
          </View>

          {/* Title */}
          <Text
            style={[rvStyles.itemTitle, { color: colors.foreground }]}
            numberOfLines={2}
          >
            {item.title}
          </Text>

          {/* Description */}
          <Text
            style={[rvStyles.itemDesc, { color: colors.foreground + 'cc' }]}
            numberOfLines={3}
          >
            {item.description}
          </Text>

          {/* Evidence */}
          <EvidenceLine item={item} />

          {/* Duplicate: canonical doc picker */}
          {isDupe && (
            <View
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 6,
                flexWrap: 'wrap',
              }}
            >
              <Text
                style={{
                  fontSize: 11,
                  lineHeight: 14,
                  ...font('regular'),
                  color: colors.mutedForeground,
                }}
              >
                Keep on approve:
              </Text>
              {(['a', 'b'] as const).map(side => {
                const id = String(item.evidence?.[`doc_${side}_id`] ?? '');
                const label = String(
                  item.evidence?.[`doc_${side}_title`] ??
                    `Document ${side.toUpperCase()}`,
                );
                if (!id) return null;
                const selected = canonical === id;
                return (
                  <Pressable
                    key={side}
                    onPress={() => setCanonical(id)}
                    hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
                    style={[
                      rvStyles.canonicalBtn,
                      {
                        borderColor: selected ? colors.primary : colors.border,
                        backgroundColor: selected
                          ? colors.primary + '18'
                          : 'transparent',
                      },
                    ]}
                  >
                    <Text
                      style={{
                        fontSize: 11,
                        lineHeight: 14,
                        ...font(selected ? 'semibold' : 'regular'),
                        color: selected ? colors.primary : colors.mutedForeground,
                      }}
                      numberOfLines={1}
                    >
                      {label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          )}

          {/* Action buttons */}
          <View style={rvStyles.actions}>
            {/* Approve */}
            <Pressable
              onPress={() => resolve('approve')}
              disabled={pending != null}
              style={({ pressed }) => [
                rvStyles.actionBtn,
                {
                  borderColor: alpha(T.green, 0.32),
                  backgroundColor: pressed ? T.greenSoft : alpha(T.green, 0.06),
                  opacity: pending != null ? 0.55 : 1,
                },
              ]}
            >
              {pending === 'approve' ? (
                <ActivityIndicator
                  size="small"
                  color={T.green}
                  style={{ transform: [{ scale: 0.65 }] }}
                />
              ) : (
                <Feather name="thumbs-up" size={13} color={T.green} />
              )}
              <Text
                style={{
                  fontSize: 12,
                  lineHeight: 18,
                  ...font('semibold'),
                  color: T.green,
                }}
              >
                Approve
              </Text>
            </Pressable>

            {/* Reject */}
            <Pressable
              onPress={() => resolve('reject')}
              disabled={pending != null}
              style={({ pressed }) => [
                rvStyles.actionBtn,
                {
                  borderColor: alpha(T.rust, 0.32),
                  backgroundColor: pressed ? T.rustSoft : alpha(T.rust, 0.06),
                  opacity: pending != null ? 0.55 : 1,
                },
              ]}
            >
              {pending === 'reject' ? (
                <ActivityIndicator
                  size="small"
                  color={T.rust}
                  style={{ transform: [{ scale: 0.65 }] }}
                />
              ) : (
                <Feather name="thumbs-down" size={13} color={T.rust} />
              )}
              <Text
                style={{
                  fontSize: 12,
                  lineHeight: 18,
                  ...font('semibold'),
                  color: T.rust,
                }}
              >
                Reject
              </Text>
            </Pressable>

            {/* Defer */}
            <Pressable
              onPress={() => resolve('defer')}
              disabled={pending != null}
              style={({ pressed }) => [
                rvStyles.actionBtn,
                {
                  borderColor: colors.border,
                  backgroundColor: pressed ? colors.muted : 'transparent',
                  opacity: pending != null ? 0.55 : 1,
                },
              ]}
            >
              {pending === 'defer' ? (
                <ActivityIndicator
                  size="small"
                  color={colors.mutedForeground}
                  style={{ transform: [{ scale: 0.65 }] }}
                />
              ) : (
                <Feather name="clock" size={13} color={colors.mutedForeground} />
              )}
              <Text
                style={{
                  fontSize: 12,
                  lineHeight: 18,
                  ...font('medium'),
                  color: colors.mutedForeground,
                }}
              >
                Defer 7d
              </Text>
            </Pressable>
          </View>
        </Animated.View>
      </GestureDetector>
    </View>
  );
}

// ── Review screen ─────────────────────────────────────────────────────────────

export default function ReviewScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [items, setItems] = useState<ReviewItem[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState(false); // distinct from an empty queue
  const [filter, setFilter] = useState<ItemFilter>('all');
  const [workFilter, setWorkFilter] = useState<string | null>(null);

  const fetchQueue = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    try {
      const r = await mobileFetch(`${API()}/review/queue`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: QueueResponse = await r.json();
      setItems(data.items ?? []);
      setCounts(data.counts_by_type ?? {});
      setTotalCount(data.count ?? 0);
      setFetchError(false);
    } catch {
      // Non-2xx or network error: show error state, don't overwrite last good data
      setFetchError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchQueue();
    const t = setInterval(() => fetchQueue(), 60_000);
    return () => clearInterval(t);
  }, [fetchQueue]);

  /** Remove a resolved item from local state immediately. */
  const handleResolved = useCallback((id: string) => {
    setItems(prev => prev.filter(i => i.id !== id));
    setTotalCount(prev => Math.max(0, prev - 1));
  }, []);

  // Unique Works that appear in the queue — used to render the Work filter chips.
  const worksInQueue = Array.from(
    new Map(
      items
        .filter(i => i.work_id && i.work_title)
        .map(i => [i.work_id!, { id: i.work_id!, title: i.work_title! }])
    ).values()
  );

  // Auto-clear the work filter when the selected Work no longer has any items
  // (last item resolved, or background refresh removed it). Without this the
  // queue is stuck on an empty state with no visible way to escape.
  useEffect(() => {
    if (workFilter === null) return;
    const presentIds = new Set(items.map(i => i.work_id).filter(Boolean));
    if (!presentIds.has(workFilter)) setWorkFilter(null);
  }, [items, workFilter]);

  // Apply work filter first so the type filter operates on the narrowed set.
  const workFiltered = workFilter === null
    ? items
    : items.filter(i => i.work_id === workFilter);

  const filtered =
    filter === 'all' ? workFiltered : workFiltered.filter(i => i.item_type === filter);

  return (
    <View style={[rvStyles.container, { backgroundColor: colors.background }]}>
      <Stack.Screen options={{ headerShown: false }} />

      {/* Page header — includes back/close button since stack header is hidden */}
      <View
        style={[
          rvStyles.header,
          {
            paddingTop: insets.top + 12,
            borderBottomColor: colors.border,
            backgroundColor: colors.background,
          },
        ]}
      >
        {/* Back button — always shown so users can return from the nav sheet */}
        <Pressable
          onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          accessibilityLabel="Go back"
          accessibilityRole="button"
          style={({ pressed }) => ({
            opacity: pressed ? 0.5 : 1,
            marginRight: 4,
            minHeight: 44,
            alignItems: 'center',
            justifyContent: 'center',
          })}
        >
          <Feather name="arrow-left" size={22} color={colors.foreground} />
        </Pressable>

        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 }}>
          <Feather name="inbox" size={18} color={colors.primary} />
          <Text style={[rvStyles.pageTitle, { color: colors.foreground }]}>
            Review Queue
          </Text>
          {totalCount > 0 && (
            <View
              style={[
                rvStyles.countBadge,
                { backgroundColor: colors.primary },
              ]}
            >
              <Text
                style={{
                  color: colors.primaryForeground,
                  fontSize: 11,
                  lineHeight: 14,
                  ...font('bold'),
                }}
              >
                {totalCount > 99 ? '99+' : String(totalCount)}
              </Text>
            </View>
          )}
        </View>

        <Pressable
          onPress={() => fetchQueue(true)}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          disabled={refreshing}
          style={{
            opacity: refreshing ? 0.45 : 1,
            minHeight: 44,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Feather name="refresh-cw" size={16} color={colors.mutedForeground} />
        </Pressable>
      </View>

      {/* Type filter pills */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{
          flexDirection: 'row',
          gap: 6,
          paddingHorizontal: 16,
          paddingVertical: 10,
          alignItems: 'center',
        }}
        style={{
          borderBottomWidth: worksInQueue.length > 0 ? 0 : StyleSheet.hairlineWidth,
          borderBottomColor: colors.border,
          flexShrink: 0,
        }}
      >
        {FILTERS.map(f => {
          const n =
            f.key === 'all' ? workFiltered.length : (workFiltered.filter(i => i.item_type === f.key).length);
          const active = f.key === filter;
          return (
            <Pressable
              key={f.key}
              onPress={() => setFilter(f.key)}
              hitSlop={{ top: 4, bottom: 4, left: 4, right: 4 }}
              style={{
                paddingHorizontal: 12,
                paddingVertical: 8,
                minHeight: 36,
                borderRadius: 8,
                borderWidth: 1,
                borderColor: active ? colors.primary : colors.border,
                backgroundColor: active
                  ? colors.primary + '18'
                  : 'transparent',
                flexDirection: 'row',
                alignItems: 'center',
                gap: 5,
              }}
            >
              <Text
                style={{
                  fontSize: 13,
                  lineHeight: 18,
                  ...font('medium'),
                  color: active ? colors.primary : colors.mutedForeground,
                }}
              >
                {f.label}
              </Text>
              {n > 0 && (
                <Text
                  style={{
                    fontSize: 10,
                    lineHeight: 14,
                    ...font('medium'),
                    color: active ? colors.primary : colors.mutedForeground,
                    opacity: 0.7,
                  }}
                >
                  {n}
                </Text>
              )}
            </Pressable>
          );
        })}
      </ScrollView>

      {/* Work filter chips — only shown when the queue has items from at least one Work */}
      {worksInQueue.length > 0 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{
            flexDirection: 'row',
            gap: 6,
            paddingHorizontal: 16,
            paddingVertical: 8,
            alignItems: 'center',
          }}
          style={{
            borderBottomWidth: StyleSheet.hairlineWidth,
            borderBottomColor: colors.border,
            flexShrink: 0,
          }}
        >
          <Text style={{ fontSize: 10, lineHeight: 14, ...font('medium'), color: colors.mutedForeground, marginRight: 2 }}>
            WORK
          </Text>
          {/* "All Works" chip */}
          <Pressable
            onPress={() => setWorkFilter(null)}
            hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
            style={{
              paddingHorizontal: 10,
              paddingVertical: 6,
              minHeight: 32,
              borderRadius: 8,
              borderWidth: 1,
              borderColor: workFilter === null ? colors.primary : colors.border,
              backgroundColor: workFilter === null ? colors.primary + '18' : 'transparent',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{
              fontSize: 12,
              lineHeight: 16,
              ...font(workFilter === null ? 'semibold' : 'regular'),
              color: workFilter === null ? colors.primary : colors.mutedForeground,
            }}>
              All
            </Text>
          </Pressable>

          {worksInQueue.map(w => {
            const active = workFilter === w.id;
            return (
              <Pressable
                key={w.id}
                onPress={() => setWorkFilter(w.id)}
                hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
                style={{
                  paddingHorizontal: 10,
                  paddingVertical: 6,
                  minHeight: 32,
                  borderRadius: 8,
                  borderWidth: 1,
                  borderColor: active ? colors.primary : colors.border,
                  backgroundColor: active ? colors.primary + '18' : 'transparent',
                  maxWidth: 160,
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Text
                  numberOfLines={1}
                  style={{
                    fontSize: 12,
                    lineHeight: 16,
                    ...font(active ? 'semibold' : 'regular'),
                    color: active ? colors.primary : colors.mutedForeground,
                  }}
                >
                  {w.title}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      {/* Content */}
      {loading && items.length === 0 ? (
        <View style={{ flex: 1, paddingTop: 8 }}>
          {[...Array(4)].map((_, i) => (
            <SkeletonItem key={i} lines={3} />
          ))}
        </View>
      ) : fetchError && items.length === 0 ? (
        /* Distinct error state — never confuse a failed fetch with an empty queue */
        <View style={rvStyles.centered}>
          <Feather name="wifi-off" size={44} color={colors.mutedForeground} style={{ opacity: 0.55 }} />
          <Text style={{ fontSize: 17, lineHeight: 22, ...font('semibold'), color: colors.foreground, marginTop: 12 }}>
            Can't reach the server
          </Text>
          <Text style={{ fontSize: 14, lineHeight: 20, ...font('regular'), color: colors.mutedForeground, textAlign: 'center', marginTop: 4 }}>
            Your review queue could not be loaded. Check that Orivellum is running and try again.
          </Text>
          <Pressable
            onPress={() => fetchQueue(true)}
            style={({ pressed }) => ({
              marginTop: 16, paddingHorizontal: 20, paddingVertical: 10,
              minHeight: 44,
              borderRadius: 8, borderWidth: 1, borderColor: colors.border,
              backgroundColor: pressed ? colors.muted : 'transparent',
              flexDirection: 'row', alignItems: 'center', gap: 7,
            })}
          >
            <Feather name="refresh-cw" size={14} color={colors.foreground} />
            <Text style={{ fontSize: 14, lineHeight: 20, ...font('medium'), color: colors.foreground }}>Retry</Text>
          </Pressable>
        </View>
      ) : !fetchError && filtered.length === 0 ? (
        /* All-clear — only shown after a confirmed successful fetch with zero items */
        <EmptyState
          icon="check-circle"
          title="All caught up!"
          body={
            workFilter !== null
              ? 'No items for this Work need review.'
              : filter === 'all'
              ? 'No items need your review right now.'
              : 'No items of this type need review.'
          }
        />
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={item => item.id}
          renderItem={({ item }) => (
            <ReviewCard item={item} onResolved={handleResolved} />
          )}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => fetchQueue(true)}
              tintColor={colors.primary}
            />
          }
          contentContainerStyle={{
            paddingHorizontal: 16,
            paddingTop: 12,
            gap: 10,
            paddingBottom: insets.bottom + 24,
          }}
          showsVerticalScrollIndicator={false}
          ItemSeparatorComponent={() => <View style={{ height: 2 }} />}
        />
      )}
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const rvStyles = StyleSheet.create({
  container: { flex: 1 },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 14,
    borderBottomWidth: 1,
  },
  pageTitle: {
    fontSize: 24,
    lineHeight: 30,
    ...font('bold'),
    letterSpacing: -0.3,
  },
  countBadge: {
    borderRadius: 10,
    paddingHorizontal: 7,
    paddingVertical: 2,
    minWidth: 22,
    alignItems: 'center',
  },

  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingHorizontal: 32,
  },

  // Card
  card: {
    borderRadius: 10,
    borderWidth: 1,
    borderLeftWidth: 4,
    padding: 14,
    gap: 8,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  typeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
    borderWidth: 1,
  },
  itemTitle: {
    fontSize: 14,
    lineHeight: 20,
    ...font('semibold'),
  },
  itemDesc: {
    fontSize: 13,
    lineHeight: 18,
    ...font('regular'),
  },

  // Confidence
  confTrack: {
    height: 5,
    borderRadius: 3,
    width: 52,
    overflow: 'hidden',
  },

  // Duplicate canonical picker
  canonicalBtn: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 6,
    borderWidth: 1,
    maxWidth: 160,
  },

  // Action buttons
  actions: {
    flexDirection: 'row',
    gap: 7,
    flexWrap: 'wrap',
    marginTop: 2,
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minWidth: 80,
    minHeight: 44,
  },

  // Swipe gesture container + hint layers
  swipeContainer: {
    position: 'relative',
    overflow: 'hidden',
    borderRadius: 10,
  },
  swipeHintLeft: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    right: 0,
    alignItems: 'flex-start',
    justifyContent: 'center',
    paddingLeft: 20,
  },
  swipeHintRight: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    right: 0,
    alignItems: 'flex-end',
    justifyContent: 'center',
    paddingRight: 20,
  },
});
