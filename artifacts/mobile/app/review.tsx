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
import { Feather } from '@expo/vector-icons';
import { Stack, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { mobileFetch } from '@/lib/api';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

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

// ── Type metadata ─────────────────────────────────────────────────────────────

const TYPE_META: Record<
  ReviewItem['item_type'],
  { label: string; icon: string; color: string }
> = {
  knowledge:  { label: 'AI knowledge', icon: 'star',    color: '#8b5cf6' },
  reclassify: { label: 'Reclassify',   icon: 'tag',     color: '#f59e0b' },
  suggestion: { label: 'Suggestion',   icon: 'zap',     color: '#0ea5e9' },
  duplicate:  { label: 'Duplicate',    icon: 'copy',    color: '#f43f5e' },
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
  if (value == null) return null;
  const pct = Math.min(100, Math.max(0, Math.round(value * 100)));
  const barColor =
    value < 0.5 ? '#ef4444' : value < 0.8 ? '#f59e0b' : '#22c55e';
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
          fontFamily: 'Inter_500Medium',
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
        fontFamily: 'Inter_400Regular',
        color: colors.mutedForeground,
        lineHeight: 16,
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
  const meta = TYPE_META[item.item_type];
  const isDupe = item.item_type === 'duplicate';

  const [pending, setPending] = useState<
    'approve' | 'reject' | 'defer' | null
  >(null);
  const [canonical, setCanonical] = useState<string | null>(
    isDupe ? String(item.evidence?.doc_a_id ?? '') || null : null,
  );

  const resolve = async (decision: 'approve' | 'reject' | 'defer') => {
    setPending(decision);
    try {
      // item.id is already namespaced: "knowledge:abc123", so the route is
      // POST /api/review/knowledge:abc123/resolve
      const r = await mobileFetch(`${API}/review/${item.id}/resolve`, {
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
      Alert.alert(
        'Could not resolve',
        e?.message ?? 'Something went wrong. Please try again.',
        [{ text: 'OK' }],
      );
    } finally {
      setPending(null);
    }
  };

  return (
    <View
      style={[
        rvStyles.card,
        {
          backgroundColor: colors.card,
          borderColor: colors.border,
          borderLeftColor: meta.color,
        },
      ]}
    >
      {/* Type badge + confidence */}
      <View style={rvStyles.cardHeader}>
        <View
          style={[
            rvStyles.typeBadge,
            {
              backgroundColor: meta.color + '20',
              borderColor: meta.color + '44',
            },
          ]}
        >
          <Feather name={meta.icon as any} size={11} color={meta.color} />
          <Text
            style={{
              fontSize: 11,
              fontFamily: 'Inter_500Medium',
              color: meta.color,
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
              fontFamily: 'Inter_400Regular',
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
                    fontFamily: selected
                      ? 'Inter_600SemiBold'
                      : 'Inter_400Regular',
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
              borderColor: '#22c55e44',
              backgroundColor: pressed ? '#22c55e18' : '#22c55e0a',
              opacity: pending != null ? 0.55 : 1,
            },
          ]}
        >
          {pending === 'approve' ? (
            <ActivityIndicator
              size="small"
              color="#22c55e"
              style={{ transform: [{ scale: 0.65 }] }}
            />
          ) : (
            <Feather name="thumbs-up" size={13} color="#22c55e" />
          )}
          <Text
            style={{
              fontSize: 12,
              fontFamily: 'Inter_600SemiBold',
              color: '#22c55e',
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
              borderColor: '#ef444444',
              backgroundColor: pressed ? '#ef444418' : '#ef44440a',
              opacity: pending != null ? 0.55 : 1,
            },
          ]}
        >
          {pending === 'reject' ? (
            <ActivityIndicator
              size="small"
              color="#ef4444"
              style={{ transform: [{ scale: 0.65 }] }}
            />
          ) : (
            <Feather name="thumbs-down" size={13} color="#ef4444" />
          )}
          <Text
            style={{
              fontSize: 12,
              fontFamily: 'Inter_600SemiBold',
              color: '#ef4444',
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
              fontFamily: 'Inter_500Medium',
              color: colors.mutedForeground,
            }}
          >
            Defer 7d
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

// ── Review screen ─────────────────────────────────────────────────────────────

export default function ReviewScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const isWeb = Platform.OS === 'web';

  const [items, setItems] = useState<ReviewItem[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState(false); // distinct from an empty queue
  const [filter, setFilter] = useState<ItemFilter>('all');

  const fetchQueue = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    try {
      const r = await mobileFetch(`${API}/review/queue`);
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

  const filtered =
    filter === 'all' ? items : items.filter(i => i.item_type === filter);

  const topPad = isWeb ? 67 : 0;

  return (
    <View style={[rvStyles.container, { backgroundColor: colors.background }]}>
      <Stack.Screen options={{ headerShown: false }} />

      {/* Page header — includes back/close button since stack header is hidden */}
      <View
        style={[
          rvStyles.header,
          {
            paddingTop: topPad + 12,
            borderBottomColor: colors.border,
            backgroundColor: colors.background,
          },
        ]}
      >
        {/* Back button — always shown so users can return from the nav sheet */}
        <Pressable
          onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))}
          hitSlop={10}
          accessibilityLabel="Go back"
          accessibilityRole="button"
          style={({ pressed }) => ({ opacity: pressed ? 0.5 : 1, marginRight: 4 })}
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
                  fontFamily: 'Inter_700Bold',
                }}
              >
                {totalCount > 99 ? '99+' : String(totalCount)}
              </Text>
            </View>
          )}
        </View>

        <Pressable
          onPress={() => fetchQueue(true)}
          hitSlop={10}
          disabled={refreshing}
          style={{ opacity: refreshing ? 0.45 : 1 }}
        >
          <Feather name="refresh-cw" size={16} color={colors.mutedForeground} />
        </Pressable>
      </View>

      {/* Filter pills */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{
          flexDirection: 'row',
          gap: 6,
          paddingHorizontal: 12,
          paddingVertical: 10,
        }}
        style={{
          borderBottomWidth: StyleSheet.hairlineWidth,
          borderBottomColor: colors.border,
          flexShrink: 0,
        }}
      >
        {FILTERS.map(f => {
          const n =
            f.key === 'all' ? totalCount : (counts[f.key] ?? 0);
          const active = f.key === filter;
          return (
            <Pressable
              key={f.key}
              onPress={() => setFilter(f.key)}
              style={{
                paddingHorizontal: 12,
                paddingVertical: 6,
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
                  fontFamily: 'Inter_500Medium',
                  color: active ? colors.primary : colors.mutedForeground,
                }}
              >
                {f.label}
              </Text>
              {n > 0 && (
                <Text
                  style={{
                    fontSize: 10,
                    fontFamily: 'Inter_500Medium',
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

      {/* Content */}
      {loading && items.length === 0 ? (
        <View style={rvStyles.centered}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : fetchError && items.length === 0 ? (
        /* Distinct error state — never confuse a failed fetch with an empty queue */
        <View style={rvStyles.centered}>
          <Feather name="wifi-off" size={44} color={colors.mutedForeground} style={{ opacity: 0.55 }} />
          <Text style={{ fontSize: 17, fontFamily: 'Inter_600SemiBold', color: colors.foreground, marginTop: 12 }}>
            Can't reach the server
          </Text>
          <Text style={{ fontSize: 14, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center', marginTop: 4, lineHeight: 20 }}>
            Your review queue could not be loaded. Check that Orivellum is running and try again.
          </Text>
          <Pressable
            onPress={() => fetchQueue(true)}
            style={({ pressed }) => ({
              marginTop: 16, paddingHorizontal: 20, paddingVertical: 10,
              borderRadius: 8, borderWidth: 1, borderColor: colors.border,
              backgroundColor: pressed ? colors.muted : 'transparent',
              flexDirection: 'row', alignItems: 'center', gap: 7,
            })}
          >
            <Feather name="refresh-cw" size={14} color={colors.foreground} />
            <Text style={{ fontSize: 14, fontFamily: 'Inter_500Medium', color: colors.foreground }}>Retry</Text>
          </Pressable>
        </View>
      ) : !fetchError && filtered.length === 0 ? (
        /* All-clear — only shown after a confirmed successful fetch with zero items */
        <View style={rvStyles.centered}>
          <Feather
            name="check-circle"
            size={48}
            color="#22c55e"
            style={{ opacity: 0.55 }}
          />
          <Text
            style={{
              fontSize: 17,
              fontFamily: 'Inter_600SemiBold',
              color: colors.foreground,
              marginTop: 12,
            }}
          >
            All clear
          </Text>
          <Text
            style={{
              fontSize: 14,
              fontFamily: 'Inter_400Regular',
              color: colors.mutedForeground,
              textAlign: 'center',
              marginTop: 4,
              lineHeight: 20,
            }}
          >
            {filter === 'all'
              ? 'Nothing needs your review right now.'
              : 'No items of this type need review.'}
          </Text>
        </View>
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
            padding: 12,
            gap: 10,
            paddingBottom: (isWeb ? 34 : insets.bottom) + 24,
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
    fontFamily: 'Inter_700Bold',
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
    fontFamily: 'Inter_600SemiBold',
    lineHeight: 19,
  },
  itemDesc: {
    fontSize: 13,
    fontFamily: 'Inter_400Regular',
    lineHeight: 18,
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
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minWidth: 80,
  },
});
