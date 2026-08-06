/**
 * Memory screen — shows the user's captured facts from the memory system.
 *
 * Facts are automatically captured in the background after each chat reply
 * (via _post_reply_background → _infer_memory_facts on the API server).
 * This screen surfaces them from GET /api/memory so users can see what the
 * AI knows about them and how knowledge evolves over time.
 */
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { mobileFetch } from '@/lib/api';
import { useNavigation } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useEffect } from 'react';

interface MemoryFact {
  id: string;
  key: string;
  value: string;
  prev_value?: string | null;
  source?: string | null;
  confidence?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export default function MemoryScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation();
  const isWeb = Platform.OS === 'web';
  const topPad = isWeb ? 67 : insets.top + 8;

  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    navigation.setOptions({ title: 'Memory' });
  }, [navigation]);

  const fetchFacts = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(false);
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
      const res = await mobileFetch(`https://${domain}/api/memory`);
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      setFacts(data.facts ?? []);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchFacts(); }, [fetchFacts]);

  const formatDate = (iso: string | null | undefined) => {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
      return '';
    }
  };

  const renderFact = ({ item }: { item: MemoryFact }) => (
    <View
      style={[
        styles.factCard,
        { backgroundColor: colors.card, borderColor: colors.border },
      ]}
    >
      {/* Key */}
      <Text style={[styles.factKey, { color: colors.primary }]} numberOfLines={1}>
        {item.key}
      </Text>
      {/* Current value */}
      <Text style={[styles.factValue, { color: colors.foreground }]}>
        {item.value}
      </Text>
      {/* Previous value — struck through to show the superseded fact */}
      {!!item.prev_value && (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 4 }}>
          <Feather name="clock" size={10} color={colors.mutedForeground} />
          <Text
            style={[styles.factPrev, { color: colors.mutedForeground }]}
            numberOfLines={1}
          >
            Previously: {item.prev_value}
          </Text>
        </View>
      )}
      {/* Footer: source + date */}
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 }}>
        {item.source ? (
          <Text style={[styles.factMeta, { color: colors.mutedForeground }]} numberOfLines={1}>
            {item.source}
          </Text>
        ) : <View />}
        {(item.updated_at ?? item.created_at) && (
          <Text style={[styles.factMeta, { color: colors.mutedForeground }]}>
            {formatDate(item.updated_at ?? item.created_at)}
          </Text>
        )}
      </View>
    </View>
  );

  return (
    <View style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}>
      {/* Header */}
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ fontSize: 20 }}>✨</Text>
          <Text style={[styles.title, { color: colors.foreground }]}>Memory</Text>
        </View>
        <Pressable onPress={() => fetchFacts(true)} hitSlop={12} disabled={refreshing}>
          <Feather
            name="refresh-cw"
            size={16}
            color={colors.mutedForeground}
            style={{ opacity: refreshing ? 0.4 : 1 }}
          />
        </Pressable>
      </View>

      {/* Caption */}
      <Text style={[styles.caption, { color: colors.mutedForeground }]}>
        Facts captured automatically as you chat — ask "where are we on X" to recall them in conversation.
      </Text>

      {/* Body */}
      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : error ? (
        <View style={styles.centered}>
          <Feather name="alert-circle" size={36} color={colors.mutedForeground} />
          <Text style={[styles.emptyTitle, { color: colors.foreground }]}>Could not load memory</Text>
          <Pressable
            onPress={() => fetchFacts()}
            style={[styles.retryBtn, { backgroundColor: colors.primary }]}
          >
            <Text style={[styles.retryText, { color: colors.primaryForeground }]}>Retry</Text>
          </Pressable>
        </View>
      ) : facts.length === 0 ? (
        <View style={styles.centered}>
          <Text style={{ fontSize: 40, marginBottom: 12 }}>✨</Text>
          <Text style={[styles.emptyTitle, { color: colors.foreground }]}>No facts yet</Text>
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
            Facts are captured automatically as you chat.{'\n'}
            Try saying "I prefer X" or sharing context about your work.
          </Text>
        </View>
      ) : (
        <FlatList
          data={facts}
          keyExtractor={(item) => item.id}
          renderItem={renderFact}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => fetchFacts(true)}
              tintColor={colors.primary}
            />
          }
          contentContainerStyle={{
            paddingHorizontal: 16,
            paddingTop: 12,
            paddingBottom: insets.bottom + 32,
            gap: 10,
          }}
          showsVerticalScrollIndicator={false}
          ListHeaderComponent={
            <Text style={[styles.factCount, { color: colors.mutedForeground }]}>
              {facts.length} fact{facts.length !== 1 ? 's' : ''} stored
            </Text>
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold' },
  caption: {
    fontSize: 13,
    fontFamily: 'Inter_400Regular',
    lineHeight: 18,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    paddingHorizontal: 32,
  },
  emptyTitle: { fontSize: 17, fontFamily: 'Inter_600SemiBold', textAlign: 'center' },
  emptyText: {
    fontSize: 14,
    fontFamily: 'Inter_400Regular',
    textAlign: 'center',
    lineHeight: 20,
    opacity: 0.7,
  },
  retryBtn: {
    marginTop: 8,
    paddingHorizontal: 24,
    paddingVertical: 10,
    borderRadius: 20,
  },
  retryText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
  factCount: {
    fontSize: 11,
    fontFamily: 'Inter_400Regular',
    marginBottom: 6,
  },
  factCard: {
    borderRadius: 12,
    borderWidth: 1,
    padding: 14,
  },
  factKey: {
    fontSize: 11,
    fontFamily: 'Inter_600SemiBold',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  factValue: {
    fontSize: 15,
    fontFamily: 'Inter_400Regular',
    lineHeight: 21,
  },
  factPrev: {
    fontSize: 11,
    fontFamily: 'Inter_400Regular',
    fontStyle: 'italic',
    textDecorationLine: 'line-through',
    flex: 1,
  },
  factMeta: {
    fontSize: 10,
    fontFamily: 'Inter_400Regular',
    opacity: 0.7,
  },
});
