import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  FlatList,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

// ── Persona definitions (must match backend _PERSONAS in conversations.py) ────
const PERSONAS = [
  {
    id: 'default',
    label: 'Default',
    emoji: '🤖',
    description: 'Balanced, helpful assistant',
  },
  {
    id: 'story_partner',
    label: 'Story Partner',
    emoji: '✨',
    description: 'Creative collaborator for narrative and fiction',
  },
  {
    id: 'technical_editor',
    label: 'Technical Editor',
    emoji: '🔬',
    description: 'Precise, structured feedback on technical writing',
  },
  {
    id: 'research_assistant',
    label: 'Research Assistant',
    emoji: '📚',
    description: 'Deep synthesis, citations, analytical depth',
  },
  {
    id: 'devils_advocate',
    label: "Devil's Advocate",
    emoji: '⚡',
    description: 'Challenges assumptions, surfaces counterarguments',
  },
] as const;

type PersonaId = (typeof PERSONAS)[number]['id'];
import { useColors } from '@/hooks/useColors';
import { useVellumTokens } from '@/lib/tokens';
import { Feather } from '@expo/vector-icons';
import {
  useListConversations,
  useCreateConversation,
  getListConversationsQueryKey,
} from '@workspace/api-client-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { mobileFetch } from '@/lib/api';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { font } from '@/lib/typography';
import * as Haptics from 'expo-haptics';
import type { Conversation } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';
import { stripMarkdown } from '@/lib/stripMarkdown';
import { readCache, writeCache } from '@/lib/cache';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { useSheetAnimation } from '@/lib/useSheetAnimation';

function ConversationItem({ item, onArchive, onDelete, onRename }: { item: Conversation; onArchive?: (id: string) => void; onDelete?: (id: string) => void; onRename?: (id: string, title: string) => void }) {
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
        { text: 'Rename', onPress: () => onRename?.(item.id ?? '', item.title ?? '') },
        { text: archived ? 'Unarchive' : 'Archive', onPress: () => onArchive?.(item.id ?? '') },
        { text: 'Delete', style: 'destructive', onPress: () => onDelete?.(item.id ?? '') },
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
              <Feather name="book-open" size={11} color={colors.primary} />
              <Text style={{ fontSize: 11, ...font('regular'), color: colors.primary }} numberOfLines={1}>
                {(item as any).work_title ?? 'work'}
              </Text>
            </View>
          )}
          {/* Persona badge — only shown for non-default personas */}
          {(item as any).persona_id && (item as any).persona_id !== 'default' && (() => {
            const p = PERSONAS.find(p => p.id === (item as any).persona_id);
            return p ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 2 }}>
                <Text style={{ fontSize: 10 }}>{p.emoji}</Text>
                <Text style={{ fontSize: 10, ...font('regular'), color: colors.mutedForeground }}>{p.label}</Text>
              </View>
            ) : null;
          })()}
          {(item as any).model && (
            <Text style={{ fontSize: 11, ...font('regular'), color: colors.mutedForeground, opacity: 0.7 }}>
              {String((item as any).model).split('/').pop()?.split('-').slice(0, 3).join('-')}
            </Text>
          )}
        </View>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

export default function ConversationsScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const router = useRouter();
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [renameModal, setRenameModal] = useState<{ id: string; title: string } | null>(null);
  const [renameText, setRenameText] = useState('');
  const renameRef = useRef<TextInput>(null);

  // Persona picker — shown before creating a new conversation
  const [personaSheetOpen, setPersonaSheetOpen] = useState(false);
  const [selectedPersona, setSelectedPersona] = useState<PersonaId>('default');

  // Memory bottom sheet
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memoryFacts, setMemoryFacts] = useState<any[]>([]);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const { rendered: personaRendered, slideAnim: personaSlideAnim, fadeAnim: personaFadeAnim } = useSheetAnimation(personaSheetOpen, 440);
  const { rendered: memoryRendered, slideAnim: memorySlideAnim, fadeAnim: memoryFadeAnim } = useSheetAnimation(memoryOpen, 400);

  const openMemorySheet = async () => {
    setMemoryOpen(true);
    setMemoryLoading(true);
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
      const r = await mobileFetch(`https://${domain}/api/memory`);
      if (r.ok) setMemoryFacts((await r.json()).facts ?? []);
    } catch { /* non-fatal */ }
    finally { setMemoryLoading(false); }
  };

  // Debounce the search term (~300ms) so filtering/API calls don't fire on every keystroke
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  const { data, isLoading, isError, refetch } = useListConversations(
    { archived: showArchived, limit: 200 } as any,
    { query: { refetchInterval: 15_000, staleTime: 10_000 } } as any
  );

  // ── Offline cache ─────────────────────────────────────────────────────────
  const [cachedConvs, setCachedConvs] = useState<any[]>([]);
  const [usingConvCache, setUsingConvCache] = useState(false);

  useEffect(() => {
    if (data?.conversations?.length) {
      writeCache('conversations:list', data.conversations);
      setCachedConvs(data.conversations);
      setUsingConvCache(false);
    }
  }, [data?.conversations]);

  useEffect(() => {
    if (isError) {
      readCache<any[]>('conversations:list').then(entry => {
        if (entry?.data?.length) {
          setCachedConvs(entry.data);
          setUsingConvCache(true);
        }
      });
    }
  }, [isError]);

  const allConversations = (isError && usingConvCache) ? cachedConvs : (data?.conversations ?? []);

  // API-backed FTS search (when query >= 2 chars)
  const isSearchMode = debouncedSearch.trim().length >= 2;
  const { data: msgSearchData, isFetching: msgSearchFetching } = useQuery<{
    results: Array<{
      id: string;
      conversation_id: string;
      conv_title: string | null;
      snippet: string;
      work_title: string | null;
      created_at: string;
      role: string;
    }>;
  } | null>({
    queryKey: ['msg-search', debouncedSearch.trim()],
    queryFn: async () => {
      const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
      const r = await mobileFetch(
        `https://${domain}/api/conversations/search?q=${encodeURIComponent(debouncedSearch.trim())}&limit=30`
      );
      if (!r.ok) return null;
      return r.json();
    },
    enabled: isSearchMode,
    staleTime: 10_000,
  });

  // Local title/snippet filtering for short queries (< 2 chars)
  const conversations = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    if (!q) return allConversations;
    return allConversations.filter((c) =>
      (c.title ?? '').toLowerCase().includes(q) ||
      stripMarkdown(c.last_message ?? '').toLowerCase().includes(q)
    );
  }, [allConversations, debouncedSearch]);
  const hasData = allConversations.length > 0;

  const { mutateAsync: createConversation, isPending: creating } = useCreateConversation();
  const queryClient = useQueryClient();

  const handleRename = async (convId: string, newTitle: string) => {
    const trimmed = newTitle.trim();
    if (!trimmed) return;
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      await mobileFetch(`https://${domain}/api/conversations/${convId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed }),
      });
      queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey({ archived: false, limit: 200 }) });
      refetch();
    } catch {
      Alert.alert('Could not rename', 'Check your connection and try again.', [{ text: 'OK' }]);
    }
    setRenameModal(null);
  };

  const openRenameModal = (convId: string, currentTitle: string) => {
    setRenameText(currentTitle);
    setRenameModal({ id: convId, title: currentTitle });
    setTimeout(() => renameRef.current?.focus(), 100);
  };

  const handleDelete = async (convId: string) => {
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const r = await mobileFetch(`https://${domain}/api/conversations/${convId}`, { method: 'DELETE' });
      if (r.ok) {
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey({ archived: false, limit: 200 }) });
        refetch();
      }
    } catch {
      Alert.alert('Could not delete', 'Check your connection and try again.', [{ text: 'OK' }]);
    }
  };

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
    // Open persona picker before creating the conversation
    setSelectedPersona('default');
    setPersonaSheetOpen(true);
  };

  const handleCreateWithPersona = async (personaId: PersonaId) => {
    setPersonaSheetOpen(false);
    try {
      const result = await createConversation({
        data: {
          title: null,
          work_id: null,
          ...(personaId !== 'default' ? { persona_id: personaId } : {}),
        },
      });
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
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={[styles.title, { color: colors.foreground }]}>Conversations</Text>
            <Pressable
              onPress={() => setShowArchived((v) => !v)}
              hitSlop={{ top: 12, bottom: 12, left: 8, right: 8 }}
              style={{
                paddingHorizontal: 10, paddingVertical: 5, borderRadius: 12,
                backgroundColor: showArchived ? colors.primary + '22' : colors.muted,
                borderWidth: 1,
                borderColor: showArchived ? colors.primary + '55' : colors.border,
              }}
            >
              <Text style={{ fontSize: 12, ...font('medium'), color: showArchived ? colors.primary : colors.mutedForeground }}>
                {showArchived ? 'Archived' : 'Active'}
              </Text>
            </Pressable>
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Pressable
              onPress={openMemorySheet}
              hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              style={({ pressed }) => ({
                width: 44, height: 44, borderRadius: 22,
                alignItems: 'center', justifyContent: 'center',
                backgroundColor: colors.muted,
                borderWidth: 1, borderColor: colors.border,
                opacity: pressed ? 0.7 : 1,
              })}
            >
              <Text style={{ fontSize: 16 }}>✨</Text>
            </Pressable>
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
        <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
          {[...Array(4)].map((_, i) => <SkeletonItem key={i} />)}
        </View>
      ) : isError && !hasData ? (
        // Hard error — no cached data at all
        <ErrorScreen
          message="Can't reach the server"
          detail="Make sure Orivellum is running on your local machine and your device is on the same network."
          onRetry={refetch}
        />
      ) : isSearchMode ? (
        /* ── API full-text search results (query >= 2 chars) ─────────── */
        msgSearchFetching ? (
          <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
            {[...Array(4)].map((_, i) => <SkeletonItem key={i} />)}
          </View>
        ) : !msgSearchData?.results?.length ? (
          <EmptyState
            icon="search"
            title="No messages found"
            body={`Nothing matched "${debouncedSearch}"`}
          />
        ) : (
          <FlatList
            data={msgSearchData.results}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => (
              <Pressable
                onPress={() =>
                  router.push({
                    pathname: `/chat/${item.conversation_id}`,
                    params: { msgId: item.id },
                  } as any)
                }
                style={({ pressed }) => [
                  styles.msgResultRow,
                  { backgroundColor: pressed ? colors.muted : colors.card, borderColor: colors.border },
                ]}
              >
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Text style={[styles.msgResultTitle, { color: colors.foreground }]} numberOfLines={1}>
                    {item.conv_title || 'Untitled'}
                  </Text>
                  {item.work_title && (
                    <Text style={[styles.msgResultWork, { color: colors.primary }]} numberOfLines={1}>
                      · {item.work_title}
                    </Text>
                  )}
                </View>
                <Text style={[styles.msgResultSnippet, { color: colors.mutedForeground }]} numberOfLines={3}>
                  {item.snippet}
                </Text>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 }}>
                  <Text style={[styles.msgResultMeta, { color: colors.mutedForeground }]}>
                    {item.role === 'user' ? 'You' : 'AI'}
                  </Text>
                  <Text style={[styles.msgResultMeta, { color: colors.mutedForeground }]}>
                    {item.created_at ? new Date(item.created_at).toLocaleDateString() : ''}
                  </Text>
                </View>
              </Pressable>
            )}
            contentContainerStyle={{
              paddingHorizontal: 16,
              paddingTop: 12,
              paddingBottom: isWeb ? 34 + 50 : insets.bottom + 24,
            }}
            showsVerticalScrollIndicator={false}
            ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
          />
        )
      ) : conversations.length === 0 ? (
        debouncedSearch ? (
          <EmptyState
            icon="search"
            title="No matching conversations"
            body={`Nothing matched "${debouncedSearch}"`}
          />
        ) : (
          <EmptyState
            icon="message-circle"
            title="No conversations yet"
            body="Start a chat from any Work or the Dashboard."
          />
        )
      ) : (
        <FlatList
          data={conversations}
          keyExtractor={(item) => item.id ?? ''}
          renderItem={({ item }) => <ConversationItem item={item} onArchive={handleArchive} onDelete={handleDelete} onRename={openRenameModal} />}
          refreshControl={
            <RefreshControl refreshing={isLoading} onRefresh={refetch} tintColor={colors.primary} />
          }
          contentContainerStyle={{
            paddingHorizontal: 16,
            paddingTop: 12,
            paddingBottom: isWeb ? 34 + 50 : insets.bottom + 24,
          }}
          showsVerticalScrollIndicator={false}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
        />
      )}

      {/* Rename Modal */}
      <Modal
        visible={!!renameModal}
        transparent
        animationType="fade"
        onRequestClose={() => setRenameModal(null)}
      >
        <Pressable
          style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', padding: 24 }}
          onPress={() => setRenameModal(null)}
        >
          <Pressable
            onPress={(e) => e.stopPropagation()}
            style={{ backgroundColor: colors.card, borderRadius: 12, padding: 20, gap: 14 }}
          >
            <Text style={{ fontSize: 16, ...font('semibold'), color: colors.foreground }}>
              Rename conversation
            </Text>
            <TextInput
              ref={renameRef}
              value={renameText}
              onChangeText={setRenameText}
              style={{
                height: 44, borderWidth: 1, borderColor: colors.primary,
                borderRadius: 8, paddingHorizontal: 10, fontSize: 14,
                ...font('regular'), color: colors.foreground,
              }}
              autoFocus
              returnKeyType="done"
              onSubmitEditing={() => renameModal && handleRename(renameModal.id, renameText)}
            />
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 10 }}>
              <Pressable
                onPress={() => setRenameModal(null)}
                style={{ paddingHorizontal: 16, paddingVertical: 10, minHeight: 44, justifyContent: 'center', borderRadius: 8, borderWidth: 1, borderColor: colors.border }}
              >
                <Text style={{ fontSize: 14, ...font('regular'), color: colors.mutedForeground }}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={() => renameModal && handleRename(renameModal.id, renameText)}
                style={{ paddingHorizontal: 16, paddingVertical: 10, minHeight: 44, justifyContent: 'center', borderRadius: 8, backgroundColor: colors.primary }}
              >
                <Text style={{ fontSize: 14, ...font('semibold'), color: colors.primaryForeground }}>Save</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Persona picker bottom sheet */}
      <Modal
        transparent
        visible={personaRendered}
        animationType="none"
        onRequestClose={() => setPersonaSheetOpen(false)}
      >
        <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.4)', opacity: personaFadeAnim }]}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setPersonaSheetOpen(false)} />
        </Animated.View>
        <Animated.View style={{
          position: 'absolute', bottom: 0, left: 0, right: 0,
          backgroundColor: colors.card,
          borderTopLeftRadius: 20,
          borderTopRightRadius: 20,
          borderTopWidth: 1,
          borderColor: colors.border,
          paddingTop: 20,
          paddingHorizontal: 16,
          paddingBottom: insets.bottom + 24,
          transform: [{ translateY: personaSlideAnim }],
        }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6 }}>
              <Text style={{ fontSize: 16, ...font('bold'), color: colors.foreground, flex: 1 }}>
                Choose a persona
              </Text>
              <Pressable onPress={() => setPersonaSheetOpen(false)} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
                <Feather name="x" size={18} color={colors.mutedForeground} />
              </Pressable>
            </View>
            <Text style={{ fontSize: 12, ...font('regular'), color: colors.mutedForeground, marginBottom: 16 }}>
              Sets the AI's role and communication style for this conversation.
            </Text>
            {PERSONAS.map((p) => (
              <Pressable
                key={p.id}
                onPress={() => setSelectedPersona(p.id)}
                style={({ pressed }) => ({
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 12,
                  paddingVertical: 12,
                  paddingHorizontal: 14,
                  marginBottom: 8,
                  minHeight: 44,
                  borderRadius: 12,
                  borderWidth: 1.5,
                  borderColor: selectedPersona === p.id ? colors.primary : colors.border,
                  backgroundColor: pressed
                    ? colors.muted
                    : selectedPersona === p.id
                    ? colors.primary + '0d'
                    : 'transparent',
                })}
              >
                <Text style={{ fontSize: 22 }}>{p.emoji}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 14, ...font('semibold'), color: colors.foreground }}>
                    {p.label}
                  </Text>
                  <Text style={{ fontSize: 12, ...font('regular'), color: colors.mutedForeground }}>
                    {p.description}
                  </Text>
                </View>
                {selectedPersona === p.id && (
                  <Feather name="check" size={16} color={colors.primary} />
                )}
              </Pressable>
            ))}
            <Pressable
              onPress={() => handleCreateWithPersona(selectedPersona)}
              disabled={creating}
              style={({ pressed }) => ({
                marginTop: 8,
                height: 48,
                borderRadius: 12,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: colors.primary,
                opacity: pressed || creating ? 0.7 : 1,
              })}
            >
              {creating ? (
                <ActivityIndicator color={colors.primaryForeground} />
              ) : (
                <Text style={{ fontSize: 15, ...font('semibold'), color: colors.primaryForeground }}>
                  Start conversation
                </Text>
              )}
            </Pressable>
        </Animated.View>
      </Modal>

      {/* Memory bottom sheet */}
      <Modal
        transparent
        visible={memoryRendered}
        animationType="none"
        onRequestClose={() => setMemoryOpen(false)}
      >
        <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.35)', opacity: memoryFadeAnim }]}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setMemoryOpen(false)} />
        </Animated.View>
        <Animated.View style={[styles.memorySheet, { position: 'absolute', bottom: 0, left: 0, right: 0, backgroundColor: colors.card, borderColor: colors.border, paddingBottom: insets.bottom + 16, transform: [{ translateY: memorySlideAnim }] }]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 12 }}>
              <Text style={{ fontSize: 18, marginRight: 8 }}>✨</Text>
              <Text style={{ fontSize: 16, ...font('bold'), color: colors.foreground, flex: 1 }}>Memory</Text>
              <Pressable onPress={() => setMemoryOpen(false)} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
                <Feather name="x" size={18} color={colors.mutedForeground} />
              </Pressable>
            </View>
            <Text style={{ fontSize: 12, ...font('regular'), color: colors.mutedForeground, marginBottom: 14 }}>
              Facts captured automatically during your conversations.
            </Text>
            {memoryLoading ? (
              <View style={{ paddingVertical: 12 }}>
                {[...Array(3)].map((_, i) => <SkeletonItem key={i} lines={1} />)}
              </View>
            ) : memoryFacts.length === 0 ? (
              <View style={{ alignItems: 'center', paddingVertical: 24 }}>
                <Text style={{ fontSize: 13, ...font('regular'), color: colors.mutedForeground, textAlign: 'center' }}>
                  No facts yet — share preferences in chat and they'll appear here.
                </Text>
              </View>
            ) : (
              <FlatList
                data={memoryFacts}
                keyExtractor={f => f.id ?? f.key}
                style={{ maxHeight: 340 }}
                ItemSeparatorComponent={() => <View style={{ height: 1, backgroundColor: colors.border }} />}
                renderItem={({ item }) => (
                  <View style={{ paddingVertical: 10 }}>
                    <Text style={{ fontSize: 11, ...font('bold'), color: colors.mutedForeground, letterSpacing: 0.5, marginBottom: 2 }}>
                      {(item.key ?? '').toUpperCase()}
                    </Text>
                    <Text style={{ fontSize: 13, ...font('regular'), color: colors.foreground }}>{item.value}</Text>
                    {item.source ? <Text style={{ fontSize: 11, ...font('regular'), color: colors.mutedForeground, marginTop: 2 }}>From: {item.source}</Text> : null}
                  </View>
                )}
              />
            )}
          </Animated.View>
      </Modal>
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
    borderWidth: 1, borderRadius: 9, paddingHorizontal: 10, height: 44,
  },
  searchInput: {
    flex: 1, fontSize: 15, ...font('regular'),
  },
  title: { fontSize: 26, ...font('bold'), letterSpacing: -0.3 },
  newBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  emptyTitle: { fontSize: 17, ...font('semibold'), lineHeight: 22 },
  emptyText: { fontSize: 15, ...font('regular'), lineHeight: 22 },
  item: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 8,
    borderWidth: 1,
    padding: 14,
    gap: 12,
    minHeight: 44,
  },
  avatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  itemContent: { flex: 1 },
  itemHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 },
  itemTitle: { flex: 1, fontSize: 15, ...font('semibold'), lineHeight: 20, marginRight: 8 },
  itemDate: { fontSize: 12, ...font('regular'), lineHeight: 16 },
  itemPreview: { fontSize: 13, ...font('regular'), lineHeight: 18 },
  itemCount: { fontSize: 12, ...font('regular'), lineHeight: 16, marginTop: 2 },
  // ── Message search result row ─────────────────────────────────────────────
  memorySheet: {
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderTopWidth: 1,
    paddingHorizontal: 16,
    paddingTop: 18,
  },
  msgResultRow: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    minHeight: 44,
  },
  msgResultTitle: { fontSize: 14, ...font('semibold'), lineHeight: 18, flex: 1 },
  msgResultWork: { fontSize: 12, ...font('regular'), lineHeight: 18 },
  msgResultSnippet: { fontSize: 13, ...font('regular'), lineHeight: 19 },
  msgResultMeta: { fontSize: 11, ...font('regular'), lineHeight: 15 },
});
