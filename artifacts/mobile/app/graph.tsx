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
import { Feather } from '@expo/vector-icons';
import { KnowledgeGraphView } from '@/components/KnowledgeGraphView';

export default function GraphScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { work_id, work_title } = useLocalSearchParams<{ work_id?: string; work_title?: string }>();

  const [isGlobal, setIsGlobal] = useState(!work_id);

  const scopedWorkId = (!isGlobal && work_id) ? work_id : undefined;

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
          hitSlop={10}
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

        {/* Work ↔ Global toggle — only shown when launched with a work_id */}
        {!!work_id && (
          <Pressable
            onPress={() => setIsGlobal(g => !g)}
            style={({ pressed }) => [
              gStyles.toggleBtn,
              { backgroundColor: isGlobal ? colors.primary : colors.muted, opacity: pressed ? 0.75 : 1 },
            ]}
          >
            <Feather name="globe" size={11} color={isGlobal ? '#fff' : colors.mutedForeground} />
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: isGlobal ? '#fff' : colors.mutedForeground }}>
              {isGlobal ? 'Global' : 'This work'}
            </Text>
          </Pressable>
        )}
      </View>

      {/* ── Graph view ── */}
      <KnowledgeGraphView workId={scopedWorkId} />
    </View>
  );
}

const gStyles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingBottom: 10,
    gap: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  backBtn:     { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
  toggleBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 20,
  },
});
