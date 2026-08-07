import React, { useState } from 'react';
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import Svg, { Circle } from 'react-native-svg';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { font } from '@/lib/typography';

interface LearnWork {
  id: string;
  title: string;
  work_type?: string;
  concept_count: number;
  graduated_count: number;
  mastery_pct: number;
  knowledge_count?: number;
}

function MasteryRing({ pct, size = 44 }: { pct: number; size?: number }) {
  const T = useVellumTokens();
  const r = (size - 6) / 2;
  const circ = 2 * Math.PI * r;
  const stroke = pct >= 80 ? T.green : pct >= 50 ? T.gilt : T.gilt;

  return (
    <Svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
      style={{ transform: [{ rotate: '-90deg' }] }}>
      <Circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={5} stroke="#e5e7eb" />
      <Circle
        cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={5}
        stroke={stroke}
        strokeDasharray={`${circ}`}
        strokeDashoffset={`${circ * (1 - pct / 100)}`}
        strokeLinecap="round"
      />
    </Svg>
  );
}

interface LearnHealth {
  total_due: number;
  stuck_count: number;
  graduating_this_week: number;
}

function LearningHealthCard() {
  const colors = useColors();
  const T = useVellumTokens();
  const domain = process.env.EXPO_PUBLIC_DOMAIN;
  const apiBase = domain ? `https://${domain}/api` : 'http://localhost:8000/api';
  const { data } = useQuery<LearnHealth>({
    queryKey: ['mobile', 'learn', 'health'],
    queryFn: () => mobileFetch(`${apiBase}/learn/health`).then(r => r.json()),
    staleTime: 60_000,
    refetchInterval: 120_000,
    enabled: !!domain,
  });

  // Only render when there's something worth surfacing
  if (!data || (data.total_due === 0 && data.stuck_count === 0 && data.graduating_this_week === 0)) {
    return null;
  }

  const metrics = [
    {
      icon: 'clock' as const,
      value: data.total_due,
      label: 'due for review',
      color: data.total_due > 0 ? T.gilt : colors.mutedForeground,
    },
    {
      icon: 'alert-triangle' as const,
      value: data.stuck_count,
      label: 'stuck',
      color: data.stuck_count > 0 ? T.rust : colors.mutedForeground,
    },
    {
      icon: 'award' as const,
      value: data.graduating_this_week,
      label: 'graduated this week',
      color: data.graduating_this_week > 0 ? T.green : colors.mutedForeground,
    },
  ];

  return (
    <View
      style={{
        borderRadius: 12, borderWidth: 1, borderColor: colors.border,
        backgroundColor: colors.card, padding: 14, marginTop: 4, marginBottom: 16,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        <Feather name="activity" size={13} color={colors.primary} />
        <Text style={{ fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', color: colors.mutedForeground, ...font('semibold') }}>
          LEARNING HEALTH
        </Text>
      </View>
      <View style={{ flexDirection: 'row', gap: 0 }}>
        {metrics.map((m, i) => (
          <View
            key={m.label}
            style={[
              { flex: 1, alignItems: 'center', gap: 2 },
              i < metrics.length - 1 && { borderRightWidth: 1, borderRightColor: colors.border },
            ]}
          >
            <Feather name={m.icon} size={13} color={m.color} />
            <Text style={{ fontSize: 18, color: m.color, ...font('bold') }}>
              {m.value}
            </Text>
            <Text style={{ fontSize: 12, lineHeight: 18, color: colors.mutedForeground, textAlign: 'center', ...font('regular') }}>
              {m.label}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function WorkCard({ work }: { work: LearnWork }) {
  const colors = useColors();
  const T = useVellumTokens();
  const router = useRouter();
  const hasC = work.concept_count > 0;
  const label = work.mastery_pct >= 100 ? 'Mastered'
    : work.mastery_pct >= 80 ? 'Near complete'
    : work.mastery_pct >= 50 ? 'In progress'
    : work.mastery_pct > 0 ? 'Getting started'
    : hasC ? 'Not started' : 'No concepts';
  const labelColor = work.mastery_pct >= 80 ? T.green
    : work.mastery_pct >= 50 ? T.gilt
    : T.rust;

  return (
    <Pressable
      onPress={() => router.push(`/works/${work.id}` as any)}
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.85 : 1 },
      ]}
    >
      <View style={styles.cardInner}>
        {/* Ring */}
        <View style={styles.ringWrap}>
          {hasC ? (
            <>
              <MasteryRing pct={work.mastery_pct} />
              <Text style={[styles.ringPct, { color: colors.foreground }]}>
                {work.mastery_pct}%
              </Text>
            </>
          ) : (
            <View style={[styles.ringPlaceholder, { borderColor: colors.border }]}>
              <Feather name="zap" size={16} color={colors.mutedForeground} />
            </View>
          )}
        </View>

        {/* Content */}
        <View style={styles.cardContent}>
          <Text style={[styles.cardTitle, { color: colors.foreground }]} numberOfLines={1}>
            {work.title}
          </Text>
          <Text style={[styles.cardLabel, { color: hasC ? labelColor : colors.mutedForeground }]}>
            {label}
          </Text>
          {hasC && (
            <Text style={[styles.cardStats, { color: colors.mutedForeground }]}>
              {work.graduated_count}/{work.concept_count} concepts graduated
            </Text>
          )}
        </View>

        <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
      </View>
    </Pressable>
  );
}

export default function LearnScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery<{ works: LearnWork[] }>({
    queryKey: ['mobile', 'learn'],
    queryFn: () => mobileFetch('/api/learn').then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const works = data?.works ?? [];
  const withConcepts = works.filter(w => w.concept_count > 0);
  const withKnowledge = works.filter(w => w.concept_count === 0 && (w.knowledge_count ?? 0) > 0);
  const empty = works.filter(w => w.concept_count === 0 && !(w.knowledge_count ?? 0));

  const totalC = works.reduce((a, w) => a + w.concept_count, 0);
  const totalG = works.reduce((a, w) => a + w.graduated_count, 0);
  const overallPct = totalC > 0 ? Math.round((totalG / totalC) * 100) : 0;

  const [showReadyToSeed, setShowReadyToSeed] = useState(false);
  const [showImportFirst, setShowImportFirst] = useState(false);

  return (
    <ScrollView
      style={[styles.root, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 24 }]}
      refreshControl={
        <RefreshControl
          refreshing={isLoading}
          onRefresh={() => { refetch(); queryClient.invalidateQueries({ queryKey: ['mobile', 'learn'] }); }}
          tintColor={colors.primary}
        />
      }
    >
      {/* Header */}
      <View style={styles.headerRow}>
        <Feather name="award" size={20} color={colors.primary} />
        <Text style={[styles.pageTitle, { color: colors.foreground }]}>Learn</Text>
      </View>
      <Text style={[styles.pageSubtitle, { color: colors.mutedForeground }]}>
        Socratic study sessions across your Works
      </Text>

      {/* Overall stats */}
      {!isLoading && totalC > 0 && (
        <View style={[styles.statsCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          {[
            { label: 'Concepts', value: totalC },
            { label: 'Graduated', value: totalG },
            { label: 'Mastery', value: `${overallPct}%` },
          ].map((s, i) => (
            <View key={s.label} style={[styles.statCell, i < 2 && { borderRightWidth: 1, borderRightColor: colors.border }]}>
              <Text style={[styles.statValue, { color: colors.foreground }]}>{s.value}</Text>
              <Text style={[styles.statLabel, { color: colors.mutedForeground }]}>{s.label}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Learning health card — shown when there's actionable data */}
      {!isLoading && <LearningHealthCard />}

      {isLoading ? (
        <>
          {[...Array(4)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
        </>
      ) : isError ? (
        <View style={styles.emptyBox}>
          <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load data</Text>
        </View>
      ) : works.length === 0 ? (
        <EmptyState
          icon="award"
          title="Nothing to learn yet"
          body="Add Works and documents to build your learning curriculum."
        />
      ) : (
        <>
          {withConcepts.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>ACTIVE STUDY</Text>
              {withConcepts.map(w => <WorkCard key={w.id} work={w} />)}
            </>
          )}
          {withKnowledge.length > 0 && (
            <>
              <Pressable
                onPress={() => setShowReadyToSeed((v: boolean) => !v)}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4, marginBottom: showReadyToSeed ? 8 : 4, minHeight: 44 }}
                hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              >
                <Text style={[styles.sectionLabel, { color: colors.mutedForeground, marginBottom: 0, marginTop: 0 }]}>
                  READY TO SEED ({withKnowledge.length})
                </Text>
                <Feather name={showReadyToSeed ? 'chevron-up' : 'chevron-down'} size={12} color={colors.mutedForeground} />
              </Pressable>
              {showReadyToSeed && withKnowledge.map(w => <WorkCard key={w.id} work={w} />)}
            </>
          )}
          {empty.length > 0 && (
            <>
              <Pressable
                onPress={() => setShowImportFirst((v: boolean) => !v)}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4, marginBottom: showImportFirst ? 8 : 4, minHeight: 44 }}
                hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              >
                <Text style={[styles.sectionLabel, { color: colors.mutedForeground, marginBottom: 0, marginTop: 0 }]}>
                  IMPORT DOCS FIRST ({empty.length})
                </Text>
                <Feather name={showImportFirst ? 'chevron-up' : 'chevron-down'} size={12} color={colors.mutedForeground} />
              </Pressable>
              {showImportFirst && empty.map(w => <WorkCard key={w.id} work={w} />)}
            </>
          )}
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 16 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  pageTitle: { fontSize: 26, lineHeight: 32, ...font('bold') },
  pageSubtitle: { fontSize: 15, lineHeight: 22, marginBottom: 16, ...font('regular') },
  statsCard: {
    flexDirection: 'row', borderRadius: 12, borderWidth: 1,
    marginBottom: 20, overflow: 'hidden',
  },
  statCell: { flex: 1, padding: 14, alignItems: 'center', gap: 2 },
  statValue: { fontSize: 22, lineHeight: 28, ...font('bold') },
  statLabel: { fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', ...font('regular') },
  sectionLabel: {
    fontSize: 11, letterSpacing: 0.6,
    textTransform: 'uppercase', marginBottom: 8, marginTop: 4,
    ...font('semibold'),
  },
  card: { borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 10, minHeight: 44 },
  cardInner: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  ringWrap: { position: 'relative', width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  ringPct: { position: 'absolute', fontSize: 10, ...font('semibold') },
  ringPlaceholder: {
    width: 44, height: 44, borderRadius: 22, borderWidth: 2,
    borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center',
  },
  cardContent: { flex: 1, gap: 2 },
  cardTitle: { fontSize: 15, lineHeight: 22, ...font('semibold') },
  cardLabel: { fontSize: 12, lineHeight: 18, ...font('regular') },
  cardStats: { fontSize: 12, lineHeight: 18, ...font('regular') },
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, lineHeight: 22, ...font('medium') },
  emptyHint: { fontSize: 12, lineHeight: 18, textAlign: 'center', maxWidth: 240, ...font('regular') },
});
