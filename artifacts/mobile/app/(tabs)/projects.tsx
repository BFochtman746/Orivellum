/**
 * Projects — learning project list.
 *
 * Lists all learning projects with mastery rings and concept counts.
 * Navigates to /project/[id] for detail.
 */
import React from 'react';
import {
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
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { font, fontSerif } from '@/lib/typography';
import Svg, { Circle } from 'react-native-svg';

// ── Types ──────────────────────────────────────────────────────────────────────

interface Project {
  id: string;
  title: string;
  description?: string | null;
  status?: string | null;
  concept_count?: number;
  mastered_count?: number;
  mastery_pct?: number;
  created_at?: string | null;
}

// ── Mastery ring ──────────────────────────────────────────────────────────────

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

// ── Project card ──────────────────────────────────────────────────────────────

function ProjectCard({ project }: { project: Project }) {
  const colors = useColors();
  const T = useVellumTokens();
  const router = useRouter();
  const pct = project.mastery_pct ?? 0;
  const concepts = project.concept_count ?? 0;
  const mastered = project.mastered_count ?? 0;

  const label = pct >= 100 ? 'Complete'
    : pct >= 80 ? 'Near complete'
    : pct >= 50 ? 'In progress'
    : pct > 0 ? 'Getting started'
    : concepts > 0 ? 'Not started' : 'No concepts yet';

  const labelColor = pct >= 80 ? T.green : pct >= 50 ? T.gilt : colors.mutedForeground;

  return (
    <Pressable
      onPress={() => {
        if (Platform.OS !== 'web') Haptics.selectionAsync().catch(() => {});
        router.push(`/project/${project.id}` as any);
      }}
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.85 : 1 },
      ]}
    >
      <View style={styles.cardInner}>
        {/* Mastery ring */}
        <View style={styles.ringWrap}>
          {concepts > 0 ? (
            <>
              <MasteryRing pct={pct} />
              <Text style={[styles.ringPct, { color: colors.foreground }]}>{pct}%</Text>
            </>
          ) : (
            <View style={[styles.ringPlaceholder, { borderColor: colors.border }]}>
              <Feather name="compass" size={16} color={colors.mutedForeground} />
            </View>
          )}
        </View>

        {/* Text */}
        <View style={styles.cardContent}>
          <Text style={[styles.cardTitle, { color: colors.foreground }]} numberOfLines={1}>
            {project.title}
          </Text>
          {!!project.description && (
            <Text style={[styles.cardDesc, { color: colors.mutedForeground }]} numberOfLines={1}>
              {project.description}
            </Text>
          )}
          <Text style={[styles.cardLabel, { color: labelColor }]}>{label}</Text>
          {concepts > 0 && (
            <Text style={[styles.cardStats, { color: colors.mutedForeground }]}>
              {mastered}/{concepts} concepts mastered
            </Text>
          )}
        </View>

        <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
      </View>
    </Pressable>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function ProjectsScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery<{ projects: Project[] }>({
    queryKey: ['mobile', 'projects'],
    queryFn: () => mobileFetch('/api/projects').then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const projects = data?.projects ?? [];
  const active = projects.filter(p => (p.mastery_pct ?? 0) > 0 && (p.mastery_pct ?? 0) < 100);
  const notStarted = projects.filter(p => !((p.mastery_pct ?? 0) > 0));
  const completed = projects.filter(p => (p.mastery_pct ?? 0) >= 100);

  return (
    <ScrollView
      style={[styles.root, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 24 }]}
      refreshControl={
        <RefreshControl
          refreshing={isLoading}
          onRefresh={() => {
            refetch();
            queryClient.invalidateQueries({ queryKey: ['mobile', 'projects'] });
          }}
          tintColor={colors.primary}
        />
      }
    >
      {/* Header */}
      <View style={styles.headerRow}>
        <Feather name="compass" size={20} color={colors.primary} />
        <Text style={[styles.pageTitle, { color: colors.foreground }]}>Projects</Text>
      </View>
      <Text style={[styles.pageSubtitle, { color: colors.mutedForeground }]}>
        Structured learning journeys across your knowledge base
      </Text>

      {/* Summary stats */}
      {!isLoading && projects.length > 0 && (
        <View style={[styles.statsCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          {[
            { label: 'Total',     value: projects.length },
            { label: 'Active',    value: active.length },
            { label: 'Complete',  value: completed.length },
          ].map((s, i) => (
            <View
              key={s.label}
              style={[styles.statCell, i < 2 && { borderRightWidth: 1, borderRightColor: colors.border }]}
            >
              <Text style={[styles.statValue, { color: colors.foreground }]}>{s.value}</Text>
              <Text style={[styles.statLabel, { color: colors.mutedForeground }]}>{s.label}</Text>
            </View>
          ))}
        </View>
      )}

      {isLoading ? (
        [...Array(4)].map((_, i) => <SkeletonItem key={i} lines={2} />)
      ) : isError ? (
        <View style={styles.emptyBox}>
          <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load projects</Text>
          <Pressable onPress={() => refetch()} style={[styles.retryBtn, { borderColor: colors.border }]}>
            <Text style={[styles.retryText, { color: colors.primary }]}>Retry</Text>
          </Pressable>
        </View>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="compass"
          title="No projects yet"
          body="Learning projects are created from your Works when concepts are seeded."
        />
      ) : (
        <>
          {active.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>IN PROGRESS</Text>
              {active.map(p => <ProjectCard key={p.id} project={p} />)}
            </>
          )}
          {notStarted.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>NOT STARTED</Text>
              {notStarted.map(p => <ProjectCard key={p.id} project={p} />)}
            </>
          )}
          {completed.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>COMPLETE</Text>
              {completed.map(p => <ProjectCard key={p.id} project={p} />)}
            </>
          )}
        </>
      )}
    </ScrollView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 16 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  pageTitle: { fontSize: 26, lineHeight: 32, ...fontSerif('bold') },
  pageSubtitle: { fontSize: 15, lineHeight: 22, marginBottom: 16, ...font('regular') },
  statsCard: {
    flexDirection: 'row', borderRadius: 12, borderWidth: 1,
    marginBottom: 20, overflow: 'hidden',
  },
  statCell: { flex: 1, padding: 14, alignItems: 'center', gap: 2 },
  statValue: { fontSize: 22, lineHeight: 28, ...fontSerif('bold') },
  statLabel: { fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', ...font('regular') },
  sectionLabel: {
    fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase',
    marginBottom: 8, marginTop: 4, ...font('semibold'),
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
  cardDesc: { fontSize: 12, lineHeight: 18, ...font('regular') },
  cardLabel: { fontSize: 12, lineHeight: 18, ...font('regular') },
  cardStats: { fontSize: 12, lineHeight: 18, ...font('regular') },
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, lineHeight: 22, ...font('medium') },
  retryBtn: { marginTop: 4, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 8, borderWidth: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  retryText: { fontSize: 14, lineHeight: 20, ...font('medium') },
});
