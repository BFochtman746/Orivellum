/**
 * Memory screen — shows the user's captured facts from the memory system.
 *
 * Facts are automatically captured in the background after each chat reply
 * (via _post_reply_background → _infer_memory_facts on the API server).
 * This screen surfaces them from GET /api/memory so users can see what the
 * AI knows about them, and lets them:
 *   • Tap a card to edit the value inline (PATCH /api/system/user-memory/{id})
 *   • Long-press or tap the trash icon to delete
 *   • Swipe left to reveal a delete action
 *   • Clear all at once
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
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
import * as Haptics from 'expo-haptics';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { mobileFetch } from '@/lib/api';
import { useNavigation, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { font, fontSerif } from '@/lib/typography';
import { apiOrigin } from '@/lib/server';

const DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const API = () => `${DOMAIN()}/api`;

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

// ── FactCard component ─────────────────────────────────────────────────────────
// Wraps each fact row in a Swipeable with its own ref and drag-hint animation.

interface FactCardProps {
  item: MemoryFact;
  colors: ReturnType<typeof useColors>;
  T: ReturnType<typeof useVellumTokens>;
  isDeleting: boolean;
  onEdit: (fact: MemoryFact) => void;
  onDelete: (fact: MemoryFact) => void;
  formatDate: (iso: string | null | undefined) => string;
}

function FactCard({ item, colors, T, isDeleting, onEdit, onDelete, formatDate }: FactCardProps) {
  const swipeRef = useRef<Swipeable>(null);
  const dragHint = useRef(new Animated.Value(0)).current;

  // Brief drag-hint animation on first mount: slide 8px left then back
  useEffect(() => {
    const timer = setTimeout(() => {
      Animated.sequence([
        Animated.timing(dragHint, {
          toValue: -8,
          duration: 180,
          useNativeDriver: true,
        }),
        Animated.spring(dragHint, {
          toValue: 0,
          friction: 6,
          useNativeDriver: true,
        }),
      ]).start();
    }, 600);
    return () => clearTimeout(timer);
  }, [dragHint]);

  const renderRightActions = (
    progress: Animated.AnimatedInterpolation<number>,
    dragX: Animated.AnimatedInterpolation<number>,
  ) => {
    const scale = dragX.interpolate({
      inputRange: [-80, -40],
      outputRange: [1, 0.8],
      extrapolate: 'clamp',
    });
    return (
      <Pressable
        onPress={() => {
          swipeRef.current?.close();
          onDelete(item);
        }}
        style={{
          width: 72,
          backgroundColor: T.rust,
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 12,
          marginLeft: 6,
        }}
      >
        <Animated.View style={{ transform: [{ scale }] }}>
          <Feather name="trash-2" size={20} color="#fff" />
        </Animated.View>
        <Text style={{ color: '#fff', fontSize: 11, marginTop: 2, fontFamily: 'Inter_500Medium' }}>
          Delete
        </Text>
      </Pressable>
    );
  };

  return (
    <Swipeable
      ref={swipeRef}
      renderRightActions={renderRightActions}
      rightThreshold={40}
      overshootRight={false}
      friction={2}
      onSwipeableWillOpen={() => {
        if (Platform.OS !== 'web') {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
        }
      }}
    >
      <Animated.View style={{ transform: [{ translateX: dragHint }] }}>
        <Pressable
          onPress={() => onEdit(item)}
          onLongPress={() => onDelete(item)}
          delayLongPress={400}
          style={({ pressed }) => [
            styles.factCard,
            {
              backgroundColor: colors.card,
              borderColor: colors.border,
              opacity: isDeleting || pressed ? 0.5 : 1,
            },
          ]}
          accessibilityLabel={`${item.key}: ${item.value}. Tap to edit. Long press or swipe left to delete.`}
          accessibilityHint="Tap to edit this memory"
        >
          <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
            <View style={{ flex: 1 }}>
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

            {/* Action buttons */}
            <View style={{ gap: 4 }}>
              {/* Edit button */}
              <Pressable
                onPress={() => onEdit(item)}
                hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                disabled={isDeleting}
                style={({ pressed }) => ({
                  padding: 6,
                  marginLeft: 8,
                  borderRadius: 6,
                  minHeight: 44,
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: pressed ? colors.primary + '20' : 'transparent',
                  opacity: isDeleting ? 0.4 : 1,
                })}
                accessibilityLabel="Edit this memory"
                accessibilityRole="button"
              >
                <Feather name="edit-2" size={14} color={colors.primary} />
              </Pressable>

              {/* Delete button */}
              <Pressable
                onPress={() => onDelete(item)}
                hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                disabled={isDeleting}
                style={({ pressed }) => ({
                  padding: 6,
                  marginLeft: 8,
                  borderRadius: 6,
                  minHeight: 44,
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: pressed ? T.rustSoft : 'transparent',
                  opacity: isDeleting ? 0.4 : 1,
                })}
                accessibilityLabel="Delete this memory"
                accessibilityRole="button"
              >
                {isDeleting
                  ? <ActivityIndicator size="small" color={colors.mutedForeground} />
                  : <Feather name="trash-2" size={14} color={T.rust} />}
              </Pressable>
            </View>
          </View>
        </Pressable>
      </Animated.View>
    </Swipeable>
  );
}

// ── Main screen ────────────────────────────────────────────────────────────────

export default function MemoryScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation();
  const router = useRouter();
  const isWeb = Platform.OS === 'web';
  const topPad = isWeb ? 67 : insets.top + 8;

  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [clearingAll, setClearingAll] = useState(false);

  // ── Inline edit state ──────────────────────────────────────────────────────
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [savingId, setSavingId] = useState<string | null>(null);

  useEffect(() => {
    navigation.setOptions({ title: 'Memory' });
  }, [navigation]);

  const fetchFacts = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(false);
    try {
      const res = await mobileFetch(`${API()}/memory`);
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

  // ── Edit handlers ──────────────────────────────────────────────────────────

  const startEdit = useCallback((fact: MemoryFact) => {
    // Don't open edit on a card that's being deleted
    if (deletingId === fact.id) return;
    setEditingId(fact.id);
    setEditValue(fact.value);
  }, [deletingId]);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setEditValue('');
  }, []);

  const saveEdit = useCallback(async (fact: MemoryFact) => {
    const trimmed = editValue.trim();
    if (!trimmed) {
      cancelEdit();
      return;
    }
    // No-op if unchanged
    if (trimmed === fact.value) {
      cancelEdit();
      return;
    }
    setSavingId(fact.id);
    try {
      const res = await mobileFetch(`${API()}/system/user-memory/${fact.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: trimmed }),
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
      // Update local state optimistically — push old value into prev_value
      setFacts(prev =>
        prev.map(f =>
          f.id === fact.id ? { ...f, value: trimmed, prev_value: f.value } : f
        )
      );
      setEditingId(null);
      setEditValue('');
    } catch {
      Alert.alert('Error', 'Could not save the updated fact. Please try again.');
    } finally {
      setSavingId(null);
    }
  }, [editValue, cancelEdit]);

  // ── Delete handlers ────────────────────────────────────────────────────────

  const handleDeleteFact = useCallback((fact: MemoryFact) => {
    // Cancel any active edit for this card before deleting
    if (editingId === fact.id) cancelEdit();
    // Medium impact when opening the destructive confirmation dialog
    if (Platform.OS !== 'web') {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    }
    Alert.alert(
      'Delete memory?',
      `"${fact.key}" will be permanently removed. The AI won't remember this fact in future chats.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            setDeletingId(fact.id);
            try {
              const res = await mobileFetch(
                `${API()}/system/user-memory/${fact.id}`,
                { method: 'DELETE' },
              );
              if (!res.ok) throw new Error(`status ${res.status}`);
              setFacts(prev => prev.filter(f => f.id !== fact.id));
              // Success notification confirms the fact is permanently gone
              if (Platform.OS !== 'web') {
                Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
              }
            } catch {
              Alert.alert('Error', 'Could not delete fact. Please try again.');
            } finally {
              setDeletingId(null);
            }
          },
        },
      ],
    );
  }, [editingId, cancelEdit]);

  const handleClearAll = useCallback(() => {
    if (facts.length === 0) return;
    Alert.alert(
      'Clear all memories?',
      `All ${facts.length} stored fact${facts.length !== 1 ? 's' : ''} will be permanently deleted. The AI will start fresh with no remembered context.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Clear All',
          style: 'destructive',
          onPress: async () => {
            setClearingAll(true);
            try {
              const res = await mobileFetch(
                `${API()}/system/user-memory`,
                { method: 'DELETE' },
              );
              if (!res.ok) throw new Error(`status ${res.status}`);
              setFacts([]);
              cancelEdit();
            } catch {
              Alert.alert('Error', 'Could not clear memories. Please try again.');
            } finally {
              setClearingAll(false);
            }
          },
        },
      ],
    );
  }, [facts.length, cancelEdit]);

  const formatDate = (iso: string | null | undefined) => {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
      });
    } catch {
      return '';
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  const renderFact = ({ item }: { item: MemoryFact }) => {
    const isDeleting = deletingId === item.id;
    const isEditing  = editingId  === item.id;
    const isSaving   = savingId   === item.id;

    if (isEditing) {
      return (
        <View
          style={[
            styles.factCard,
            {
              backgroundColor: colors.card,
              borderColor: colors.primary + 'AA',
              borderWidth: 1.5,
            },
          ]}
        >
          {/* Key label */}
          <Text style={[styles.factKey, { color: colors.primary }]} numberOfLines={1}>
            {item.key}
          </Text>

          {/* Editable value field */}
          <TextInput
            value={editValue}
            onChangeText={setEditValue}
            autoFocus
            multiline
            style={[
              styles.editInput,
              {
                color: colors.foreground,
                borderColor: colors.border,
                backgroundColor: colors.background,
              },
            ]}
            placeholderTextColor={colors.mutedForeground}
            placeholder="Enter the corrected value…"
            returnKeyType="default"
            blurOnSubmit={false}
          />

          {/* Save / Cancel row */}
          <View style={styles.editActions}>
            <Pressable
              onPress={cancelEdit}
              style={({ pressed }) => [
                styles.editBtn,
                {
                  borderWidth: 1,
                  borderColor: colors.border,
                  backgroundColor: pressed ? colors.muted : 'transparent',
                },
              ]}
            >
              <Text style={[styles.editBtnText, { color: colors.mutedForeground }]}>
                Cancel
              </Text>
            </Pressable>

            <Pressable
              onPress={() => saveEdit(item)}
              disabled={!editValue.trim() || isSaving}
              style={({ pressed }) => [
                styles.editBtn,
                {
                  backgroundColor: colors.primary,
                  opacity: (!editValue.trim() || isSaving || pressed) ? 0.6 : 1,
                },
              ]}
            >
              {isSaving
                ? <ActivityIndicator size="small" color={colors.background} />
                : <Text style={[styles.editBtnText, { color: colors.background }]}>Save</Text>}
            </Pressable>
          </View>
        </View>
      );
    }

    return (
      <FactCard
        item={item}
        colors={colors}
        T={T}
        isDeleting={isDeleting}
        onEdit={startEdit}
        onDelete={handleDeleteFact}
        formatDate={formatDate}
      />
    );
  };

  return (
    <View style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}>
      {/* Header */}
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
          {!isWeb && (
            <Pressable
              onPress={() => router.canGoBack() ? router.back() : router.replace('/' as any)}
              hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              style={{ minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center', marginRight: 2 }}
              accessibilityRole="button"
              accessibilityLabel="Back"
            >
              <Feather name="arrow-left" size={20} color={colors.foreground} />
            </Pressable>
          )}
          <Text style={{ fontSize: 20 }}>✨</Text>
          <Text style={[styles.title, { color: colors.foreground }]}>Memory</Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          {/* Clear all */}
          {facts.length > 0 && (
            <Pressable
              onPress={handleClearAll}
              hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              disabled={clearingAll}
              style={({ pressed }) => ({
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
                paddingHorizontal: 10,
                paddingVertical: 5,
                minHeight: 44,
                borderRadius: 8,
                borderWidth: 1,
                borderColor: T.giltLine,
                backgroundColor: pressed ? T.rustSoft : 'transparent',
                opacity: clearingAll ? 0.5 : 1,
              })}
              accessibilityLabel="Clear all memories"
              accessibilityRole="button"
            >
              {clearingAll
                ? <ActivityIndicator size="small" color={T.rust} />
                : <Feather name="trash-2" size={13} color={T.rust} />}
              <Text style={[styles.clearAllText, { color: T.rust }]}>
                Clear all
              </Text>
            </Pressable>
          )}
          {/* Refresh */}
          <Pressable
            onPress={() => fetchFacts(true)}
            hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
            disabled={refreshing}
            style={{ minHeight: 44, alignItems: 'center', justifyContent: 'center' }}
          >
            <Feather
              name="refresh-cw"
              size={16}
              color={colors.mutedForeground}
              style={{ opacity: refreshing ? 0.4 : 1 }}
            />
          </Pressable>
        </View>
      </View>

      {/* Caption */}
      <Text style={[styles.caption, { color: colors.mutedForeground }]}>
        Facts captured automatically as you chat. Tap to edit,{' '}
        swipe left or long-press <Feather name="trash-2" size={11} color={colors.mutedForeground} /> to delete.
      </Text>

      {/* Body */}
      {loading ? (
        <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
          {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
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
        <EmptyState
          icon="database"
          title="No memory facts yet"
          body="As you chat with Orivellum it learns and stores facts here."
        />
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
            paddingBottom: insets.bottom + 24,
            gap: 10,
          }}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
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
  title: { fontSize: 22, ...fontSerif('bold') },
  caption: {
    fontSize: 12,
    lineHeight: 18,
    ...font('regular'),
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  clearAllText: {
    fontSize: 12,
    lineHeight: 18,
    ...font('medium'),
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    paddingHorizontal: 32,
  },
  emptyTitle: { fontSize: 17, ...font('semibold'), textAlign: 'center' },
  retryBtn: {
    marginTop: 8,
    paddingHorizontal: 24,
    paddingVertical: 10,
    borderRadius: 20,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  retryText: { fontSize: 14, ...font('semibold') },
  factCount: {
    fontSize: 12,
    lineHeight: 18,
    ...font('regular'),
    marginBottom: 6,
  },
  factCard: {
    borderRadius: 12,
    borderWidth: 1,
    padding: 14,
  },
  factKey: {
    fontSize: 11,
    ...font('semibold'),
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  factValue: {
    fontSize: 15,
    lineHeight: 22,
    ...font('regular'),
  },
  factPrev: {
    fontSize: 11,
    ...font('regular'),
    fontStyle: 'italic',
    textDecorationLine: 'line-through',
    flex: 1,
  },
  factMeta: {
    fontSize: 10,
    ...font('regular'),
    opacity: 0.7,
  },

  // ── Inline edit ────────────────────────────────────────────────────────────
  editInput: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    fontSize: 15,
    lineHeight: 22,
    ...font('regular'),
    marginTop: 8,
    marginBottom: 10,
    minHeight: 64,
    textAlignVertical: 'top',
  },
  editActions: {
    flexDirection: 'row',
    gap: 8,
  },
  editBtn: {
    flex: 1,
    paddingVertical: 9,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
  },
  editBtnText: {
    fontSize: 14,
    ...font('semibold'),
  },
});
