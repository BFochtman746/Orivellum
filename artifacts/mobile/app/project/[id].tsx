/**
 * Project detail — mastery progress and concept list.
 *
 * Reachable via /project/[id] from the Projects tab.
 * Shows overall mastery, per-concept status, and links to the Work's
 * learn tab for starting a session.
 */
import React, { useState } from 'react';
import {
  ActivityIndicator,
  Animated,
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
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { font, fontSerif } from '@/lib/typography';
import Svg, { Circle } from 'react-native-svg';

// ── Types ──────────────────────────────────────────────────────────────────────

interface Concept {
  id: string;
  title: string;
  description?: string | null;
  mastery_level?: number | null;
  mastery_label?: string | null;
  due_at?: string | null;
  review_count?: number;
}

interface ProjectDetail {
  id: string;
  title: string;
  description?: string | null;
  work_id?: string | null;
  work_title?: string | null;
  concept_count: number;
  mastered_count: number;
  mastery_pct: number;
  concepts: Concept[];
}

// ── Mastery ring ──────────────────────────────────────────────────────────────

function MasteryRingLarge({ pct }: { pct: number }) {
  const T = useVellumTokens();
  const size = 88;
  const r = (size - 8) / 2;
  const circ = 2 * Math.PI * r;
  const stroke = pct >= 80 ? T.green : pct >= 50 ? T.gilt : T.gilt;
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
      style={{ transform: [{ rotate: '-90deg' }] }}>
      <Circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={7} stroke="#e5e7eb" />
      <Circle
        cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={7}
        stroke={stroke}
        strokeDasharray={`${circ}`}
        strokeDashoffset={`${circ * (1 - pct / 100)}`}
        strokeLinecap="round"
      />
    </Svg>
  );
}

// ── Mastery bar ───────────────────────────────────────────────────────────────

function MasteryBar({ level, color }: { level: number; color: string }) {
  const anim = React.useRef(new Animated.Value(0)).current;
  const colors = useColors();
  React.useEffect(() => {
    Animated.timing(anim, { toValue: level / 5, duration: 500, useNativeDriver: false }).start();
  }, [level]);
  return (
    <View style={{ height: 4, backgroundColor: colors.muted, borderRadius: 2, overflow: 'hidden', flex: 1 }}>
      <Animated.View
        style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, borderRadius: 2,
          backgroundColor: color,
          width: anim.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }),
        }}
      />
    </View>
  );
}

// ── Concept card ──────────────────────────────────────────────────────────────

const MASTERY_COLORS: Record<number, string> = {
  0: '#94a3b8',
  1: '#f59e0b',
  2: '#f97316',
  3: '#3b82f6',
  4: '#8b5cf6',
  5: '#22c55e',
};

const MASTERY_LABELS: Record<number, string> = {
  0: 'New',
  1: 'Learning',
  2: 'Familiar',
  3: 'Confident',
  4: 'Advanced',
  5: 'Mastered',
};

function ConceptCard({ concept }: { concept: Concept }) {
  const colors = useColors();
  const level = concept.mastery_level ?? 0;
  const color = MASTERY_COLORS[level] ?? '#94a3b8';
  const label = concept.mastery_label ?? MASTERY_LABELS[level] ?? 'New';

  const isDue = concept.due_at && new Date(concept.due_at) <= new Date();

  return (
    <View style={[styles.conceptCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={styles.conceptLeft}>
        {/* Level dot */}
        <View style={[styles.levelDot, { backgroundColor: color }]} />
      </View>
      <View style={styles.conceptContent}>
        <Text style={[styles.conceptTitle, { color: colors.foreground }]} numberOfLines={2}>
          {concept.title}
        </Text>
        <View style={styles.conceptMeta}>
          <MasteryBar level={level} color={color} />
          <Text style={[styles.conceptLabel, { color }]}>{label}</Text>
        </View>
        {isDue && (
          <View style={styles.dueRow}>
            <Feather name="clock" size={10} color={colors.mutedForeground} />
            <Text style={[styles.dueText, { color: colors.mutedForeground }]}>Due for review</Text>
          </View>
        )}
      </View>
      {concept.review_count != null && concept.review_count > 0 && (
        <Text style={[styles.reviewCount, { color: colors.mutedForeground }]}>
          ×{concept.review_count}
        </Text>
      )}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function ProjectDetailScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [filter, setFilter] = useState<'all' | 'due' | 'mastered'>('all');

  const { data, isLoading, isError, refetch } = useQuery<ProjectDetail>({
    queryKey: ['mobile', 'project', id],
    queryFn: () => mobileFetch(`/api/projects/${id}`).then(r => r.json()),
    staleTime: 30_000,
    enabled: !!id,
  });

  if (isLoading) {
    return (
      <View style={[styles.root, { backgroundColor: colors.background }]}>
        <View style={[styles.header, { paddingTop: insets.top + 8, borderBottomColor: colors.border }]}>
          <Pressable onPress={() => router.back()} hitSlop={10} style={styles.backBtn}>
            <Feather name="arrow-left" size={20} color={colors.foreground} />
          </Pressable>
          <Text style={[styles.headerTitle, { color: colors.foreground }]} numberOfLines={1}>Project</Text>
        </View>
        <ScrollView contentContainerStyle={{ padding: 16 }}>
          {[...Array(6)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
        </ScrollView>
      </View>
    );
  }

  if (isError || !data) {
    return (
      <View style={[styles.root, { backgroundColor: colors.background }]}>
        <View style={[styles.header, { paddingTop: insets.top + 8, borderBottomColor: colors.border }]}>
          <Pressable onPress={() => router.back()} hitSlop={10} style={styles.backBtn}>
            <Feather name="arrow-left" size={20} color={colors.foreground} />
          </Pressable>
          <Text style={[styles.headerTitle, { color: colors.foreground }]}>Project</Text>
        </View>
        <View style={styles.emptyBox}>
          <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load project</Text>
          <Pressable onPress={() => refetch()} style={[styles.retryBtn, { borderColor: colors.border }]}>
            <Text style={[styles.retryText, { color: colors.primary }]}>Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const concepts = data.concepts ?? [];
  const filtered = filter === 'due'
    ? concepts.filter(c => c.due_at && new Date(c.due_at) <= new Date())
    : filter === 'mastered'
    ? concepts.filter(c => (c.mastery_level ?? 0) >= 5)
    : concepts;

  const dueCount = concepts.filter(c => c.due_at && new Date(c.due_at) <= new Date()).length;

  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      {/* Custom header */}
      <View style={[styles.header, { paddingTop: insets.top + 8, borderBottomColor: colors.border, backgroundColor: colors.card }]}>
        <Pressable onPress={() => router.back()} hitSlop={10} style={styles.backBtn}
          accessibilityRole="button" accessibilityLabel="Back to Projects">
          <Feather name="arrow-left" size={20} color={colors.foreground} />
        </Pressable>
        <Text style={[styles.headerTitle, { color: colors.foreground }]} numberOfLines={1}>
          {data.title}
        </Text>
        {!!data.work_id && (
          <Pressable
            onPress={() => router.push(`/works/${data.work_id}` as any)}
            hitSlop={10}
            style={styles.workBtn}
            accessibilityLabel={`Open Work: ${data.work_title}`}
          >
            <Feather name="book-open" size={16} color={colors.primary} />
          </Pressable>
        )}
      </View>

      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 24 }]}
        refreshControl={
          <RefreshControl
            refreshing={false}
            onRefresh={() => {
              refetch();
              queryClient.invalidateQueries({ queryKey: ['mobile', 'project', id] });
            }}
            tintColor={colors.primary}
          />
        }
      >
        {/* Mastery hero */}
        <View style={[styles.heroCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={styles.heroRing}>
            <MasteryRingLarge pct={data.mastery_pct} />
            <Text style={[styles.heroPct, { color: colors.foreground }]}>{data.mastery_pct}%</Text>
          </View>
          <View style={styles.heroStats}>
            <Text style={[styles.heroTitle, { color: colors.foreground }]} numberOfLines={2}>
              {data.title}
            </Text>
            {!!data.description && (
              <Text style={[styles.heroDesc, { color: colors.mutedForeground }]} numberOfLines={2}>
                {data.description}
              </Text>
            )}
            <Text style={[styles.heroMeta, { color: colors.mutedForeground }]}>
              {data.mastered_count}/{data.concept_count} concepts mastered
            </Text>
            {dueCount > 0 && (
              <Text style={[styles.heroDue, { color: T.gilt }]}>
                {dueCount} due for review
              </Text>
            )}
          </View>
        </View>

        {/* Start session button (links to work learn tab) */}
        {!!data.work_id && (
          <Pressable
            onPress={() => {
              if (Platform.OS !== 'web') Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
              router.push(`/works/${data.work_id}` as any);
            }}
            style={({ pressed }) => [
              styles.sessionBtn,
              { backgroundColor: colors.primary, opacity: pressed ? 0.85 : 1 },
            ]}
          >
            <Feather name="award" size={16} color="#fff" />
            <Text style={styles.sessionBtnText}>Start study session</Text>
          </Pressable>
        )}

        {/* Filter chips */}
        {concepts.length > 0 && (
          <View style={styles.filterRow}>
            {(['all', 'due', 'mastered'] as const).map(f => (
              <Pressable
                key={f}
                onPress={() => setFilter(f)}
                style={[
                  styles.filterChip,
                  {
                    backgroundColor: filter === f ? colors.primary : colors.card,
                    borderColor: filter === f ? colors.primary : colors.border,
                  },
                ]}
              >
                <Text style={[styles.filterText, { color: filter === f ? '#fff' : colors.mutedForeground }]}>
                  {f === 'all' ? `All (${concepts.length})` : f === 'due' ? `Due (${dueCount})` : `Mastered (${data.mastered_count})`}
                </Text>
              </Pressable>
            ))}
          </View>
        )}

        {/* Concepts list */}
        {concepts.length === 0 ? (
          <View style={styles.emptyBox}>
            <Feather name="zap" size={28} color={colors.mutedForeground} />
            <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No concepts yet</Text>
            <Text style={[styles.emptyHint, { color: colors.mutedForeground }]}>
              Concepts are seeded automatically from the linked Work's knowledge base.
            </Text>
          </View>
        ) : filtered.length === 0 ? (
          <View style={styles.emptyBox}>
            <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>None in this filter</Text>
          </View>
        ) : (
          <>
            <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
              CONCEPTS — {filtered.length}
            </Text>
            {filtered.map(c => <ConceptCard key={c.id} concept={c} />)}
          </>
        )}
      </ScrollView>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16,
    paddingBottom: 12, gap: 10, borderBottomWidth: StyleSheet.hairlineWidth,
  },
  backBtn: { minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { flex: 1, fontSize: 15, lineHeight: 22, ...font('semibold') },
  workBtn: { minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' },
  content: { paddingHorizontal: 16, paddingTop: 16 },
  heroCard: {
    flexDirection: 'row', alignItems: 'center', gap: 16,
    borderRadius: 14, borderWidth: 1, padding: 16, marginBottom: 14,
  },
  heroRing: { position: 'relative', width: 88, height: 88, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  heroPct: { position: 'absolute', fontSize: 16, ...font('bold') },
  heroStats: { flex: 1, gap: 3 },
  heroTitle: { fontSize: 17, lineHeight: 24, ...fontSerif('bold') },
  heroDesc: { fontSize: 12, lineHeight: 18, ...font('regular') },
  heroMeta: { fontSize: 12, lineHeight: 18, ...font('regular') },
  heroDue: { fontSize: 12, lineHeight: 18, ...font('semibold') },
  sessionBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8, paddingVertical: 13, borderRadius: 12, minHeight: 48, marginBottom: 16,
  },
  sessionBtnText: { fontSize: 15, lineHeight: 22, color: '#fff', ...font('semibold') },
  filterRow: { flexDirection: 'row', gap: 8, marginBottom: 16, flexWrap: 'wrap' },
  filterChip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 8, borderWidth: 1, minHeight: 36,
  },
  filterText: { fontSize: 12, lineHeight: 18, ...font('medium') },
  sectionLabel: {
    fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase',
    marginBottom: 8, ...font('semibold'),
  },
  conceptCard: {
    flexDirection: 'row', alignItems: 'center',
    borderRadius: 10, borderWidth: 1, padding: 12, marginBottom: 8, gap: 10,
  },
  conceptLeft: { alignItems: 'center', justifyContent: 'center', width: 12 },
  levelDot: { width: 10, height: 10, borderRadius: 5 },
  conceptContent: { flex: 1, gap: 5 },
  conceptTitle: { fontSize: 13, lineHeight: 20, ...font('medium') },
  conceptMeta: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  conceptLabel: { fontSize: 10, letterSpacing: 0.4, ...font('semibold'), flexShrink: 0 },
  dueRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  dueText: { fontSize: 10, lineHeight: 14, ...font('regular') },
  reviewCount: { fontSize: 11, ...font('regular'), flexShrink: 0 },
  emptyBox: { alignItems: 'center', paddingTop: 48, gap: 10, paddingHorizontal: 24 },
  emptyText: { fontSize: 15, lineHeight: 22, ...font('medium') },
  emptyHint: { fontSize: 12, lineHeight: 18, textAlign: 'center', ...font('regular') },
  retryBtn: { paddingHorizontal: 20, paddingVertical: 10, borderRadius: 8, borderWidth: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  retryText: { fontSize: 14, lineHeight: 20, ...font('medium') },
});
