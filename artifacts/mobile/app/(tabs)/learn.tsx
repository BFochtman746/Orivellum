import React from 'react';
import {
  ActivityIndicator,
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
  const r = (size - 6) / 2;
  const circ = 2 * Math.PI * r;
  const stroke = pct >= 80 ? '#22c55e' : pct >= 50 ? '#f59e0b' : '#6366f1';

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

function WorkCard({ work }: { work: LearnWork }) {
  const colors = useColors();
  const router = useRouter();
  const hasC = work.concept_count > 0;
  const label = work.mastery_pct >= 100 ? 'Mastered'
    : work.mastery_pct >= 80 ? 'Near complete'
    : work.mastery_pct >= 50 ? 'In progress'
    : work.mastery_pct > 0 ? 'Getting started'
    : hasC ? 'Not started' : 'No concepts';
  const labelColor = work.mastery_pct >= 80 ? '#16a34a'
    : work.mastery_pct >= 50 ? '#b45309'
    : '#6366f1';

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

      {isLoading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 48 }} />
      ) : isError ? (
        <View style={styles.emptyBox}>
          <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load data</Text>
        </View>
      ) : works.length === 0 ? (
        <View style={styles.emptyBox}>
          <Feather name="award" size={36} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>No Works yet</Text>
          <Text style={[styles.emptyHint, { color: colors.mutedForeground }]}>
            Create a Work and import documents to start learning
          </Text>
        </View>
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
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>READY TO SEED</Text>
              {withKnowledge.map(w => <WorkCard key={w.id} work={w} />)}
            </>
          )}
          {empty.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>IMPORT DOCS FIRST</Text>
              {empty.map(w => <WorkCard key={w.id} work={w} />)}
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
  pageTitle: { fontSize: 26, fontFamily: 'Merriweather_700Bold' },
  pageSubtitle: { fontSize: 13, fontFamily: 'Inter_400Regular', marginBottom: 16 },
  statsCard: {
    flexDirection: 'row', borderRadius: 12, borderWidth: 1,
    marginBottom: 20, overflow: 'hidden',
  },
  statCell: { flex: 1, padding: 14, alignItems: 'center', gap: 2 },
  statValue: { fontSize: 22, fontFamily: 'Merriweather_700Bold' },
  statLabel: { fontSize: 10, fontFamily: 'Inter_400Regular', textTransform: 'uppercase', letterSpacing: 0.5 },
  sectionLabel: {
    fontSize: 10, fontFamily: 'Inter_600SemiBold', letterSpacing: 1.2,
    textTransform: 'uppercase', marginBottom: 8, marginTop: 4,
  },
  card: { borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 10 },
  cardInner: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  ringWrap: { position: 'relative', width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  ringPct: { position: 'absolute', fontSize: 10, fontFamily: 'Inter_600SemiBold' },
  ringPlaceholder: {
    width: 44, height: 44, borderRadius: 22, borderWidth: 2,
    borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center',
  },
  cardContent: { flex: 1, gap: 2 },
  cardTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
  cardLabel: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  cardStats: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, fontFamily: 'Inter_500Medium' },
  emptyHint: { fontSize: 12, fontFamily: 'Inter_400Regular', textAlign: 'center', maxWidth: 240 },
});
