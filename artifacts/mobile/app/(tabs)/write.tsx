/**
 * Write tab — document list for the AI Write Desk.
 * Lists all write_documents, lets users create, open, and delete documents.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
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
import { Swipeable } from 'react-native-gesture-handler';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { mobileFetch } from '@/lib/api';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { font } from '@/lib/typography';
import * as Haptics from 'expo-haptics';

const _semibold = font('semibold');

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

interface WriteDoc {
  id: string;
  title: string;
  word_count: number;
  work_id: string | null;
  is_pinned: number;
  created_at: string;
  updated_at: string;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function DocRow({
  doc,
  onPress,
  onDelete,
}: {
  doc: WriteDoc;
  onPress: () => void;
  onDelete: () => void;
}) {
  const colors = useColors();
  const swipeRef = useRef<Swipeable>(null);
  const isWeb = Platform.OS === 'web';

  const renderRightActions = () => (
    <View style={{ justifyContent: 'center', paddingHorizontal: 16 }}>
      <Pressable
        onPress={() => {
          swipeRef.current?.close();
          onDelete();
        }}
        style={[styles.deleteAction, { backgroundColor: '#ef4444' }]}
      >
        <Feather name="trash-2" size={18} color="#fff" />
        <Text style={styles.deleteActionText}>Delete</Text>
      </Pressable>
    </View>
  );

  const content = (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.docRow,
        {
          backgroundColor: colors.card,
          borderColor: colors.border,
          opacity: pressed ? 0.75 : 1,
        },
      ]}
    >
      <View style={[styles.docIconWrap, { backgroundColor: `${colors.primary}18` }]}>
        <Feather name="edit-3" size={16} color={colors.primary} />
      </View>
      <View style={styles.docMeta}>
        <Text
          style={[styles.docTitle, { color: colors.foreground }]}
          numberOfLines={1}
        >
          {doc.title || 'Untitled'}
        </Text>
        <View style={styles.docSubRow}>
          {doc.is_pinned ? (
            <Feather name="bookmark" size={11} color={colors.primary} style={{ marginRight: 4 }} />
          ) : null}
          <Text style={[styles.docMeta2, { color: colors.mutedForeground }]}>
            {doc.word_count ?? 0} words
          </Text>
          <Text style={[styles.docMeta2, { color: colors.mutedForeground }]}>
            {' · '}
            {relativeTime(doc.updated_at)}
          </Text>
        </View>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );

  if (isWeb) {
    return (
      <View>
        {content}
        <Pressable
          onPress={onDelete}
          style={{ position: 'absolute', right: 12, top: 0, bottom: 0, justifyContent: 'center' }}
        >
          <Feather name="trash-2" size={16} color="#ef4444" />
        </Pressable>
      </View>
    );
  }

  return (
    <Swipeable
      ref={swipeRef}
      renderRightActions={renderRightActions}
      rightThreshold={60}
      onSwipeableOpen={() => {
        if (Platform.OS !== 'web') {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
        }
      }}
    >
      {content}
    </Swipeable>
  );
}

export default function WriteScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [docs, setDocs] = useState<WriteDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState('');
  const [creating, setCreating] = useState(false);

  const fetchDocs = useCallback(async () => {
    try {
      const r = await mobileFetch(`${API}/write/documents`);
      if (r.ok) {
        const data = await r.json();
        setDocs((data.documents as WriteDoc[]) ?? []);
      }
    } catch {
      // network error — keep stale data
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    fetchDocs();
  }, [fetchDocs]);

  const handleCreate = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    try {
      const r = await mobileFetch(`${API}/write/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Untitled', content_text: '' }),
      });
      if (!r.ok) throw new Error('Create failed');
      const doc = await r.json();
      router.push(`/write/${doc.id}` as any);
      fetchDocs();
    } catch (e: any) {
      Alert.alert('Could not create document', e?.message ?? 'Please try again.');
    } finally {
      setCreating(false);
    }
  }, [creating, fetchDocs, router]);

  const handleDelete = useCallback(
    (doc: WriteDoc) => {
      Alert.alert(
        'Delete document?',
        `"${doc.title || 'Untitled'}" will be permanently deleted.`,
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Delete',
            style: 'destructive',
            onPress: async () => {
              try {
                await mobileFetch(`${API}/write/documents/${doc.id}`, {
                  method: 'DELETE',
                });
                setDocs((prev) => prev.filter((d) => d.id !== doc.id));
              } catch {
                Alert.alert('Delete failed', 'Please try again.');
              }
            },
          },
        ]
      );
    },
    []
  );

  const filtered = docs.filter((d) =>
    search.trim() === '' ||
    (d.title || 'Untitled').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Toolbar */}
      <View
        style={[
          styles.toolbar,
          { borderBottomColor: colors.border, paddingTop: insets.top > 0 ? 0 : 8 },
        ]}
      >
        {/* Search */}
        <View style={[styles.searchWrap, { backgroundColor: colors.muted, borderColor: colors.border }]}>
          <Feather name="search" size={14} color={colors.mutedForeground} />
          <TextInput
            style={[styles.searchInput, { color: colors.foreground }]}
            placeholder="Search documents…"
            placeholderTextColor={colors.mutedForeground}
            value={search}
            onChangeText={setSearch}
            returnKeyType="search"
          />
          {search.length > 0 && (
            <Pressable onPress={() => setSearch('')} hitSlop={8}>
              <Feather name="x" size={13} color={colors.mutedForeground} />
            </Pressable>
          )}
        </View>

        {/* Create button */}
        <Pressable
          onPress={handleCreate}
          disabled={creating}
          style={({ pressed }) => [
            styles.createBtn,
            { backgroundColor: colors.primary, opacity: pressed || creating ? 0.75 : 1 },
          ]}
        >
          {creating ? (
            <ActivityIndicator size="small" color="#fff" />
          ) : (
            <Feather name="plus" size={18} color="#fff" />
          )}
        </Pressable>
      </View>

      {/* List */}
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(d) => d.id}
          contentContainerStyle={[
            styles.list,
            { paddingBottom: insets.bottom + 24 },
          ]}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={handleRefresh}
              tintColor={colors.primary}
            />
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <Feather name="edit-3" size={40} color={colors.mutedForeground} />
              <Text style={[styles.emptyTitle, { color: colors.foreground }]}>
                No documents yet
              </Text>
              <Text style={[styles.emptySubtitle, { color: colors.mutedForeground }]}>
                Tap + to start writing
              </Text>
            </View>
          }
          renderItem={({ item }) => (
            <DocRow
              doc={item}
              onPress={() => router.push(`/write/${item.id}` as any)}
              onDelete={() => handleDelete(item)}
            />
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  toolbar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  searchWrap: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 8,
    gap: 6,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    padding: 0,
  },
  createBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  list: { paddingTop: 8 },
  docRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: 14,
    marginVertical: 5,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 12,
    gap: 12,
  },
  docIconWrap: {
    width: 38,
    height: 38,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  docMeta: { flex: 1 },
  docTitle: {
    fontSize: 15,
    ..._semibold,
    marginBottom: 3,
  },
  docSubRow: { flexDirection: 'row', alignItems: 'center' },
  docMeta2: { fontSize: 12 },
  deleteAction: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 10,
  },
  deleteActionText: { color: '#fff', fontSize: 13, fontWeight: '600' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { alignItems: 'center', paddingTop: 80, gap: 12 },
  emptyTitle: { fontSize: 18, fontWeight: '600', marginTop: 8 },
  emptySubtitle: { fontSize: 14 },
});
