/**
 * Knowledge Graph Browser — full-screen view.
 *
 * Reachable via /graph or /graph?work_id=<id>&work_title=<title>.
 * All graph logic lives in @/components/KnowledgeGraphView.
 */
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { font } from '@/lib/typography';
import { Feather } from '@expo/vector-icons';
import { useQuery } from '@tanstack/react-query';
import { mobileFetch } from '@/lib/api';
import { KnowledgeGraphView } from '@/components/KnowledgeGraphView';
import { SkeletonItem } from '@/components/SkeletonItem';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API    = `https://${DOMAIN}/api`;

export default function GraphScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { work_id, work_title } = useLocalSearchParams<{ work_id?: string; work_title?: string }>();

  const [isGlobal, setIsGlobal] = useState(!work_id);
  const scopedWorkId = (!isGlobal && work_id) ? work_id : undefined;

  // Preflight query so we can show loading/error/empty at the page level.
  // KnowledgeGraphView shares the same React Query cache key, so this
  // result is served from cache — no duplicate network call.
  const endpoint = scopedWorkId
    ? `${API}/graph?work_id=${scopedWorkId}&limit=120`
    : `${API}/graph?limit=150`;

  const { isLoading, isError, data, refetch } = useQuery({
    queryKey: ['graph', scopedWorkId ?? 'global'],
    queryFn: () => mobileFetch(endpoint).then(r => r.json()),
    staleTime: 30_000,
    retry: 1,
  });

  const isEmpty = !isLoading && !isError && (data?.node_count === 0 || (data?.nodes ?? []).length === 0);

  return (
    <View style={[gStyles.container, { backgroundColor: colors.background }]}>
      {/* ── Header ── */}
      <View style={[gStyles.header, {
        paddingTop: insets.top + 8,
        backgroundColor: colors.card,
        borderBottomColor: colors.border,
      }]}>
        <Pressable
          onPress={() => router.back()}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          style={gStyles.backBtn}
          accessibilityRole="button"
          accessibilityLabel="Back"
        >
          <Feather name="arrow-left" size={20} color={colors.foreground} />
        </Pressable>

        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={[gStyles.headerTitle, { color: colors.foreground }]} numberOfLines={1}>
            {isGlobal ? 'Knowledge Graph' : (work_title ?? 'Work Graph')}
          </Text>
        </View>

        {/* Retry button — visible on error */}
        {isError && (
          <Pressable
            onPress={() => refetch()}
            hitSlop={10}
            style={[gStyles.toggleBtn, { backgroundColor: colors.muted }]}
            accessibilityRole="button"
            accessibilityLabel="Retry"
          >
            <Feather name="refresh-cw" size={13} color={colors.mutedForeground} />
            <Text style={[gStyles.toggleText, { color: colors.mutedForeground }]}>Retry</Text>
          </Pressable>
        )}

        {/* Work ↔ Global toggle — only shown when launched with a work_id */}
        {!!work_id && !isError && (
          <Pressable
            onPress={() => setIsGlobal(g => !g)}
            style={({ pressed }) => [
              gStyles.toggleBtn,
              { backgroundColor: isGlobal ? colors.primary : colors.muted, opacity: pressed ? 0.75 : 1 },
            ]}
          >
            <Feather name="globe" size={11} color={isGlobal ? colors.foreground : colors.mutedForeground} />
            <Text style={[gStyles.toggleText, { color: isGlobal ? colors.foreground : colors.mutedForeground }]}>
              {isGlobal ? 'Global' : 'This work'}
            </Text>
          </Pressable>
        )}
      </View>

      {/* ── Body ── */}
      {isLoading ? (
        <View style={{ padding: 16 }}>
          {[...Array(4)].map((_, i) => <SkeletonItem key={i} lines={1} />)}
        </View>
      ) : isError ? (
        <View style={gStyles.errorBox}>
          <Feather name="wifi-off" size={32} color={colors.mutedForeground} />
          <Text style={[gStyles.errorTitle, { color: colors.foreground }]}>Could not load graph</Text>
          <Text style={[gStyles.errorBody, { color: colors.mutedForeground }]}>
            Check your connection and tap Retry above.
          </Text>
          <Pressable
            onPress={() => refetch()}
            style={({ pressed }) => [
              gStyles.retryBtn,
              { borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
            ]}
          >
            <Feather name="refresh-cw" size={14} color={colors.primary} />
            <Text style={[gStyles.retryText, { color: colors.primary }]}>Retry</Text>
          </Pressable>
        </View>
      ) : isEmpty ? (
        <View style={gStyles.errorBox}>
          <Feather name="share-2" size={32} color={colors.mutedForeground} />
          <Text style={[gStyles.errorTitle, { color: colors.foreground }]}>No graph data yet</Text>
          <Text style={[gStyles.errorBody, { color: colors.mutedForeground }]}>
            Import documents and extract knowledge to build the graph.
          </Text>
        </View>
      ) : (
        <KnowledgeGraphView workId={scopedWorkId} />
      )}
    </View>
  );
}

const gStyles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingBottom: 10,
    gap: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  backBtn: {
    minHeight: 44,
    minWidth: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  headerTitle: {
    fontSize: 15,
    lineHeight: 22,
    ...font('semibold'),
  },
  toggleBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    minHeight: 44,
    borderRadius: 20,
  },
  toggleText: {
    fontSize: 11,
    lineHeight: 14,
    ...font('semibold'),
  },
  errorBox: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
    gap: 12,
  },
  errorTitle: {
    fontSize: 18,
    lineHeight: 24,
    ...font('semibold'),
    textAlign: 'center',
  },
  errorBody: {
    fontSize: 14,
    lineHeight: 22,
    ...font('regular'),
    textAlign: 'center',
    maxWidth: 260,
  },
  retryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 44,
    marginTop: 4,
  },
  retryText: {
    fontSize: 14,
    lineHeight: 20,
    ...font('medium'),
  },
});
