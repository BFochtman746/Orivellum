import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import {
  useListConversations,
  useCreateConversation,
  getListConversationsQueryKey,
} from '@workspace/api-client-react';
import { useQueryClient } from '@tanstack/react-query';
import { mobileFetch } from '@/lib/api';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import type { Conversation } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';
import { stripMarkdown } from '@/lib/stripMarkdown';

function ConversationItem({ item, onArchive }: { item: Conversation; onArchive?: (id: string) => void }) {
  const colors = useColors();
  const router = useRouter();

  const date = item.updated_at
    ? new Date(item.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    : '';

  const handleLongPress = () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const archived = !!(item as any).archived;
    Alert.alert(
      item.title ?? 'Conversation',
      '',
      [
        { text: archived ? 'Unarchive' : 'Archive', onPress: () => onArchive?.(item.id ?? '') },
        { text: 'Cancel', style: 'cancel' },
      ]
    );
  };

  return (
    <Pressable
      onPress={() => router.push(`/chat/${item.id}`)}
      onLongPress={handleLongPress}
      delayLongPress={400}
      style={({ pressed }) => [
        styles.item,
        {
          backgroundColor: colors.card,
          borderColor: colors.border,
          opacity: pressed ? 0.7 : 1,
        },
      ]}
    >
      <View style={[styles.avatar, { backgroundColor: colors.primary }]}>
        <Feather name="message-circle" size={16} color={colors.primaryForeground} />
      </View>
      <View style={styles.itemContent}>
        <View style={styles.itemHeader}>
          <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={1}>
            {item.title ?? 'New conversation'}
          </Text>
          <Text style={[styles.itemDate, { color: colors.mutedForeground }]}>{date}</Text>
        </View>
        {item.last_message ? (
          <Text style={[styles.itemPreview, { color: colors.mutedForeground }]} numberOfLines={2}>
            {stripMarkdown(item.last_message)}
          </Text>
        ) : (
          <Text style={[styles.itemPreview, { color: colors.muted }]}>No messages yet</Text>
        )}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 3 }}>
          {(item.message_count ?? 0) > 0 && (
            <Text style={[styles.itemCount, { color: colors.mutedForeground }]}>
              {item.message_count} msg{item.message_count === 1 ? '' : 's'}
            </Text>
          )}
          {(item as any).work_id && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
              <Feather name="book-open" size={10} color={colors.primary} />
              <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.primary }}>work</Text>
            </View>
          )}
        </View>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

export default function ConversationsScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const router = useRouter();
  const [search, setSearch] = useState('');

  const { data, isLoading, isError, refetch } = useListConversations(
    { archived: false, limit: 200 },
    { query: { refetchInterval: 15_000, staleTime: 10_000 } } as any
  );
  const allConversations = data?.conversations ?? [];
  const conversations = search
    ? allConversations.filter((c) =>
        (c.title ?? '').toLowerCase().includes(search.toLowerCase()) ||
        stripMarkdown(c.last_message ?? '').toLowerCase().includes(search.toLowerCase())
      )
    : allConversations;
  const hasData = allConversations.length > 0;

  const { mutateAsync: createConversation, isPending: creating } = useCreateConversation();
  const queryClient = useQueryClient();

  const handleArchive = async (convId: string) => {
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const r = await mobileFetch(`https://${domain}/api/conversations/${convId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archived: true }),
      });
      if (r.ok) {
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey({ archived: false, limit: 200 }) });
        refetch();
      }
    } catch {
      Alert.alert('Could not archive', 'Check your connection and try again.', [{ text: 'OK' }]);
    }
  };

  const handleNew = async () => {
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    try {
      const result = await createConversation({ data: { title: null, work_id: null } });
      const convoId = result?.conversation?.id;
      if (convoId) {
        refetch();
        router.push(`/chat/${convoId}`);
      }
    } catch {
      Alert.alert(
        'Could not create conversation',
        'Make sure the Orivellum server is running, then try again.',
        [{ text: 'OK' }]
      );
    }
  };

  const topPad = isWeb ? 67 : insets.top;

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View
        style={[
          styles.header,
          {
            paddingTop: topPad + 12,
            borderBottomColor: colors.border,
            backgroundColor: colors.background,
          },
        ]}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <Text style={[styles.title, { color: colors.foreground }]}>Conversations</Text>
          <Pressable
            onPress={handleNew}
            style={({ pressed }) => [
              styles.newBtn,
              { backgroundColor: colors.primary, opacity: pressed || creating ? 0.7 : 1 },
            ]}
            disabled={creating}
          >
            {creating ? (
              <ActivityIndicator size="small" color={colors.primaryForeground} />
            ) : (
              <Feather name="plus" size={20} color={colors.primaryForeground} />
            )}
          </Pressable>
        </View>
        {/* Search bar */}
        <View style={[styles.searchRow, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Feather name="search" size={14} color={colors.mutedForeground} />
          <TextInput
            style={[styles.searchInput, { color: colors.foreground }]}
            placeholder="Search conversations…"
            placeholderTextColor={colors.mutedForeground}
            value={search}
            onChangeText={setSearch}
            autoCorrect={false}
          />
          {search.length > 0 && (
            <Pressable onPress={() => setSearch('')} hitSlop={8}>
              <Feather name="x" size={14} color={colors.mutedForeground} />
            </Pressable>
          )}
        </View>
      </View>

      {/* Offline banner — shown only when we have cached data to display */}
      {isError && hasData && (
        <OfflineBanner
          message="Showing cached conversations — server unreachable"
          onRetry={refetch}
        />
      )}

      {/* Body */}
      {isLoading && !hasData ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : isError && !hasData ? (
        // Hard error — no cached data at all
        <ErrorScreen
          message="Can't reach the server"
          detail="Make sure Orivellum is running on your local machine and your device is on the same network."
          onRetry={refetch}
        />
      ) : conversations.length === 0 ? (
        <View style={styles.centered}>
          <Feather name="message-square" size={44} color={colors.mutedForeground} />
          <Text style={[styles.emptyTitle, { color: colors.foreground }]}>
            {search ? 'No results' : 'No conversations yet'}
          </Text>
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
            {search ? `Nothing matched "${search}"` : 'Tap + to start a new one'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={conversations}
          keyExtractor={(item) => item.id ?? ''}
          renderItem={({ item }) => <ConversationItem item={item} onArchive={handleArchive} />}
          refreshControl={
            <RefreshControl refreshing={isLoading} onRefresh={refetch} tintColor={colors.primary} />
          }
          contentContainerStyle={{
            paddingHorizontal: 16,
            paddingTop: 12,
            paddingBottom: isWeb ? 34 + 50 : insets.bottom + 100,
          }}
          showsVerticalScrollIndicator={false}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'column',
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: 1,
  },
  searchRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderWidth: 1, borderRadius: 9, paddingHorizontal: 10, height: 36,
  },
  searchInput: {
    flex: 1, fontSize: 14, fontFamily: 'Inter_400Regular',
  },
  title: { fontSize: 26, fontFamily: 'Inter_700Bold' },
  newBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: 'center',
    justifyContent: 'center',
  },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  emptyTitle: { fontSize: 17, fontFamily: 'Inter_600SemiBold' },
  emptyText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  item: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 8,
    borderWidth: 1,
    padding: 14,
    gap: 12,
  },
  avatar: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: 'center',
    justifyContent: 'center',
  },
  itemContent: { flex: 1 },
  itemHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 },
  itemTitle: { flex: 1, fontSize: 15, fontFamily: 'Inter_600SemiBold', marginRight: 8 },
  itemDate: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  itemPreview: { fontSize: 13, fontFamily: 'Inter_400Regular', lineHeight: 18 },
  itemCount: { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 4 },
});
