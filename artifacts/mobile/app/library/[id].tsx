import React, { useEffect, useRef, useState } from 'react';
import { mobileFetch } from '@/lib/api';
import { getApiToken } from '@/lib/token';
import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useGetDocument, useListWorks } from '@workspace/api-client-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

const READINESS_COLOR: Record<string, string> = {
  ready: '#4A8C65',
  error: '#dc2626',
  failed: '#dc2626',
  imported: '#d97706',
};

const READINESS_LABEL: Record<string, string> = {
  ready: 'Ready',
  error: 'Error',
  failed: 'Failed',
  imported: 'Processing…',
};

export default function LibraryDocDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const navigation = useNavigation();
  const qc = useQueryClient();
  const isWeb = Platform.OS === 'web';

  const [refreshing, setRefreshing] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [showWorkPicker, setShowWorkPicker] = useState(false);
  const [linkingWork, setLinkingWork] = useState(false);
  const [lifecycleUpdating, setLifecycleUpdating] = useState(false);

  // ── Read Aloud (TTS) ────────────────────────────────────────────────────────
  type TtsState = 'idle' | 'loading' | 'playing' | 'paused' | 'error';
  const [ttsState, setTtsState] = useState<TtsState>('idle');
  const ttsPlayerRef = useRef<AudioPlayer | null>(null);

  useEffect(() => {
    return () => { ttsPlayerRef.current?.remove(); };
  }, []);

  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

  const { data: docData, isLoading: docLoading, isError: docError, refetch: refetchDoc } =
    useGetDocument(id ?? '', { query: { enabled: !!id, staleTime: 15_000 } } as any);
  const { data: knData, isLoading: knLoading, isError: knError, refetch: refetchKn } = useQuery({
    queryKey: ['library-knowledge', id],
    queryFn: async () => {
      const res = await mobileFetch(`https://${domain}/api/library/${id}/knowledge`);
      if (!res.ok) throw new Error('Failed to load knowledge');
      return res.json();
    },
    enabled: !!id,
    staleTime: 30_000,
  });
  const { data: worksData } = useListWorks({} as any);

  const doc = (docData as any)?.document;
  const knowledge = (knData as any)?.knowledge ?? [];
  const works = (worksData as any)?.works ?? [];

  const handleRefresh = async () => {
    setRefreshing(true);
    try { await Promise.all([refetchDoc(), refetchKn()]); } finally { setRefreshing(false); }
  };

  const handleListen = async () => {
    if (ttsState === 'playing') {
      ttsPlayerRef.current?.pause();
      setTtsState('paused');
      return;
    }
    if (ttsState === 'paused' && ttsPlayerRef.current) {
      ttsPlayerRef.current.play();
      setTtsState('playing');
      return;
    }
    setTtsState('loading');
    try {
      await setAudioModeAsync({ playsInSilentMode: true });
      const res = await mobileFetch(`https://${domain}/api/studio/tts/document`, {
        method: 'POST',
        body: JSON.stringify({ doc_id: id, return_url: true }),
      });
      if (!res.ok) throw new Error(await res.text());
      const json = await res.json();
      const token = getApiToken();
      const serveUri = `https://${domain}/api/studio/outputs/serve?path=${encodeURIComponent(json.path)}`;
      const player = createAudioPlayer({
        uri: serveUri,
        headers: token ? { authorization: `Bearer ${token}` } : undefined,
      });
      ttsPlayerRef.current = player;
      player.play();
      setTtsState('playing');
      player.addListener('playbackStatusUpdate', (status) => {
        // Detect natural end-of-playback: not playing after content has started
        if (!status.playing && status.currentTime > 0 && status.duration > 0
            && status.currentTime >= status.duration - 0.5) {
          setTtsState('idle');
          ttsPlayerRef.current = null;
        }
      });
    } catch (e: any) {
      setTtsState('error');
      Alert.alert('Read Aloud failed', e?.message ?? 'Could not generate audio');
      setTimeout(() => setTtsState('idle'), 2000);
    }
  };

  const handleReview = async (itemId: string, status: 'approved' | 'rejected') => {
    setReviewing(itemId);
    try {
      const res = await mobileFetch(`https://${domain}/api/knowledge/${itemId}/review`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_status: status }),
      });
      if (!res.ok) throw new Error('Review failed');
      await refetchKn();
    } catch {
      Alert.alert('Error', 'Could not update review status');
    } finally {
      setReviewing(null);
    }
  };

  const LIFECYCLE_OPTIONS = [
    { value: 'draft',      label: 'Draft',      color: '#d97706' },
    { value: 'canonical',  label: 'Canonical',  color: '#059669' },
    { value: 'superseded', label: 'Superseded', color: '#6b7280' },
    { value: 'reference',  label: 'Reference',  color: '#2563eb' },
  ] as const;

  const handleSetLifecycle = (lc: string) => {
    Alert.alert(
      'Set Lifecycle',
      `Change lifecycle to "${lc}"?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Confirm',
          onPress: async () => {
            setLifecycleUpdating(true);
            try {
              const res = await mobileFetch(`https://${domain}/api/library/${id}/lifecycle`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lifecycle: lc }),
              });
              if (!res.ok) throw new Error('Update failed');
              await refetchDoc();
              qc.invalidateQueries({ queryKey: ['getGetDocument', id] });
            } catch {
              Alert.alert('Error', 'Could not update lifecycle');
            } finally {
              setLifecycleUpdating(false);
            }
          },
        },
      ]
    );
  };

  const showLifecyclePicker = () => {
    Alert.alert(
      'Document Lifecycle',
      'Choose the authority state for this document.',
      [
        ...LIFECYCLE_OPTIONS.map((o) => ({
          text: o.label,
          onPress: () => handleSetLifecycle(o.value),
        })),
        { text: 'Cancel', style: 'cancel' as const },
      ]
    );
  };

  const handleLinkWork = async (workId: string | null) => {
    setLinkingWork(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/library/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_id: workId }),
      });
      if (!res.ok) throw new Error('Link failed');
      await refetchDoc();
      qc.invalidateQueries({ queryKey: ['getGetDocument', id] });
    } catch {
      Alert.alert('Error', 'Could not update work assignment');
    } finally {
      setLinkingWork(false);
      setShowWorkPicker(false);
    }
  };

  useEffect(() => {
    if (doc?.title) navigation.setOptions({ title: doc.title });
  }, [doc?.title]);

  const topPad = isWeb ? 67 : insets.top;

  if (docLoading) {
    return (
      <View style={[styles.container, styles.centered, { backgroundColor: colors.background }]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (docError || !doc) {
    return (
      <View style={[styles.container, styles.centered, { backgroundColor: colors.background }]}>
        <Feather name="alert-circle" size={32} color={colors.mutedForeground} />
        <Text style={[styles.emptyText, { color: colors.mutedForeground, marginTop: 12 }]}>
          Could not load document
        </Text>
        <Pressable
          onPress={() => router.back()}
          style={[styles.backBtn, { borderColor: colors.border }]}
        >
          <Text style={[styles.backBtnText, { color: colors.foreground }]}>Go back</Text>
        </Pressable>
      </View>
    );
  }

  const readinessColor = READINESS_COLOR[doc.readiness ?? 'imported'] ?? colors.mutedForeground;
  const readinessLabel = READINESS_LABEL[doc.readiness ?? 'imported'] ?? doc.readiness;
  const docTitle = doc.title || doc.source?.split('/').pop() || 'Untitled';
  const linkedWork = works.find((w: any) => w.id === doc.work_id);

  const pendingKnowledge = knowledge.filter((k: any) => k.review_status === 'ai_auto');
  const approvedKnowledge = knowledge.filter((k: any) => k.review_status === 'approved');
  const otherKnowledge = knowledge.filter((k: any) => k.review_status !== 'ai_auto' && k.review_status !== 'approved' && k.review_status !== 'rejected');

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View
        style={[
          styles.header,
          { paddingTop: topPad + 8, borderBottomColor: colors.border, backgroundColor: colors.background },
        ]}
      >
        <Pressable onPress={() => router.back()} style={styles.backRow} hitSlop={8}>
          <Feather name="arrow-left" size={18} color={colors.primary} />
          <Text style={[styles.backLabel, { color: colors.primary }]}>Library</Text>
        </Pressable>
        <Text style={[styles.title, { color: colors.foreground }]} numberOfLines={2}>
          {docTitle}
        </Text>
        <View style={styles.metaRow}>
          <View style={[styles.badge, { backgroundColor: colors.muted }]}>
            <Text style={[styles.badgeText, { color: colors.foreground }]}>
              {(doc.kind ?? 'file').toUpperCase()}
            </Text>
          </View>
          <View style={[styles.badge, { backgroundColor: readinessColor + '22' }]}>
            <Text style={[styles.badgeText, { color: readinessColor }]}>{readinessLabel}</Text>
          </View>
          {doc.word_count ? (
            <Text style={[styles.metaText, { color: colors.mutedForeground }]}>
              {doc.word_count.toLocaleString()} words
            </Text>
          ) : null}
        </View>
        {/* Lifecycle picker row */}
        {(() => {
          const lc = (doc as any).lifecycle ?? 'draft';
          const opt = LIFECYCLE_OPTIONS.find((o) => o.value === lc);
          return (
            <Pressable
              onPress={showLifecyclePicker}
              disabled={lifecycleUpdating}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 8 }}
            >
              <View style={{
                paddingHorizontal: 8, paddingVertical: 3, borderRadius: 5,
                backgroundColor: (opt?.color ?? colors.mutedForeground) + '18',
                borderWidth: 1, borderColor: (opt?.color ?? colors.mutedForeground) + '44',
              }}>
                <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: opt?.color ?? colors.mutedForeground, letterSpacing: 0.4 }}>
                  {(opt?.label ?? lc).toUpperCase()}
                </Text>
              </View>
              {lifecycleUpdating
                ? <ActivityIndicator size="small" color={colors.mutedForeground} />
                : <Feather name="chevron-down" size={12} color={colors.mutedForeground} />}
            </Pressable>
          );
        })()}

        {doc.readiness === 'ready' && (
          <Pressable
            onPress={handleListen}
            disabled={ttsState === 'loading' || ttsState === 'error'}
            style={[styles.listenBtn, { borderColor: colors.primary + '55', backgroundColor: colors.primary + '0f' }]}
          >
            {ttsState === 'loading' ? (
              <ActivityIndicator size="small" color={colors.primary} />
            ) : (
              <Feather
                name={ttsState === 'playing' ? 'pause' : ttsState === 'paused' ? 'play' : 'headphones'}
                size={14}
                color={colors.primary}
              />
            )}
            <Text style={[styles.listenBtnText, { color: colors.primary }]}>
              {ttsState === 'loading' ? 'Generating…'
                : ttsState === 'playing' ? 'Pause'
                : ttsState === 'paused' ? 'Resume'
                : 'Listen'}
            </Text>
          </Pressable>
        )}
      </View>

      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: isWeb ? 50 : insets.bottom + 40 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
      >
        {/* Error message */}
        {doc.error_message && (
          <View style={[styles.errorBox, { backgroundColor: '#fee2e2', borderColor: '#fca5a5' }]}>
            <Feather name="alert-triangle" size={14} color="#dc2626" />
            <Text style={[styles.errorText, { color: '#dc2626' }]}>{doc.error_message}</Text>
          </View>
        )}

        {/* Overview */}
        <View style={[styles.section, { borderColor: colors.border }]}>
          <Text style={[styles.sectionTitle, { color: colors.mutedForeground }]}>OVERVIEW</Text>
          {doc.source && (
            <View style={styles.row}>
              <Text style={[styles.rowLabel, { color: colors.mutedForeground }]}>Source</Text>
              <Text style={[styles.rowValue, { color: colors.foreground }]} numberOfLines={2}>
                {doc.source.split('/').pop()}
              </Text>
            </View>
          )}
          {doc.created_at && (
            <View style={styles.row}>
              <Text style={[styles.rowLabel, { color: colors.mutedForeground }]}>Imported</Text>
              <Text style={[styles.rowValue, { color: colors.foreground }]}>
                {new Date(doc.created_at).toLocaleDateString()}
              </Text>
            </View>
          )}
          {doc.chunk_count != null && (
            <View style={styles.row}>
              <Text style={[styles.rowLabel, { color: colors.mutedForeground }]}>Chunks</Text>
              <Text style={[styles.rowValue, { color: colors.foreground }]}>{doc.chunk_count}</Text>
            </View>
          )}
          {/* Work assignment */}
          <View style={styles.row}>
            <Text style={[styles.rowLabel, { color: colors.mutedForeground }]}>Work</Text>
            <Pressable
              onPress={() => setShowWorkPicker(true)}
              style={[styles.workChip, { backgroundColor: colors.primary + '14', borderColor: colors.primary + '40' }]}
            >
              <Feather name="briefcase" size={11} color={colors.primary} />
              <Text style={[styles.workChipText, { color: colors.primary }]}>
                {linkedWork ? linkedWork.title : 'Link to Work'}
              </Text>
              <Feather name="chevron-down" size={11} color={colors.primary} />
            </Pressable>
          </View>
        </View>

        {/* Knowledge — AI review items first */}
        <View style={[styles.section, { borderColor: colors.border }]}>
          <Text style={[styles.sectionTitle, { color: colors.mutedForeground }]}>
            KNOWLEDGE {knowledge.length > 0 ? `(${knowledge.length})` : ''}
          </Text>
          {knLoading ? (
            <ActivityIndicator color={colors.primary} style={{ marginVertical: 12 }} />
          ) : knError ? (
            <Pressable
              onPress={() => refetchKn()}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 10 }}
            >
              <Feather name="alert-circle" size={14} color={colors.destructive ?? '#ef4444'} />
              <Text style={[styles.emptyText, { color: colors.destructive ?? '#ef4444' }]}>
                Could not load knowledge — tap to retry
              </Text>
            </Pressable>
          ) : knowledge.length === 0 ? (
            <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
              No knowledge extracted yet
            </Text>
          ) : (
            <>
              {pendingKnowledge.length > 0 && (
                <Text style={[styles.reviewHeader, { color: colors.primary }]}>
                  ✦ {pendingKnowledge.length} AI item{pendingKnowledge.length !== 1 ? 's' : ''} need review
                </Text>
              )}
              {[...pendingKnowledge, ...approvedKnowledge, ...otherKnowledge].map((item: any) => {
                const isPending = item.review_status === 'ai_auto';
                const isApproved = item.review_status === 'approved';
                const isRejected = item.review_status === 'rejected';
                return (
                  <View
                    key={item.id}
                    style={[
                      styles.knowledgeItem,
                      {
                        borderColor: isPending ? colors.primary + '44' : isApproved ? '#4A8C6544' : colors.border,
                        backgroundColor: isPending ? colors.primary + '08' : isApproved ? '#4A8C6508' : colors.muted + '55',
                        opacity: isRejected ? 0.45 : 1,
                      },
                    ]}
                  >
                    <Text style={[styles.knText, { color: colors.foreground }]}>{item.text}</Text>
                    <View style={styles.knFooter}>
                      <Text style={[styles.knMeta, { color: colors.mutedForeground }]}>
                        {item.kind} · {Math.round((item.confidence ?? 0) * 100)}%
                        {isPending ? ' · ✦ AI' : isApproved ? ' · ✓' : isRejected ? ' · ✗' : ''}
                      </Text>
                      {(isPending || isApproved) && (
                        <View style={styles.reviewButtons}>
                          {!isApproved && (
                            <Pressable
                              onPress={() => handleReview(item.id, 'approved')}
                              disabled={reviewing === item.id}
                              style={[styles.reviewBtn, { backgroundColor: '#4A8C6522' }]}
                              hitSlop={6}
                            >
                              {reviewing === item.id ? (
                                <ActivityIndicator size="small" color="#4A8C65" />
                              ) : (
                                <Feather name="thumbs-up" size={13} color="#4A8C65" />
                              )}
                            </Pressable>
                          )}
                          {!isRejected && (
                            <Pressable
                              onPress={() => handleReview(item.id, 'rejected')}
                              disabled={reviewing === item.id}
                              style={[styles.reviewBtn, { backgroundColor: '#dc262622' }]}
                              hitSlop={6}
                            >
                              <Feather name="thumbs-down" size={13} color="#dc2626" />
                            </Pressable>
                          )}
                        </View>
                      )}
                    </View>
                  </View>
                );
              })}
            </>
          )}
        </View>
      </ScrollView>

      {/* Work Picker Modal */}
      <Modal visible={showWorkPicker} transparent animationType="slide" onRequestClose={() => setShowWorkPicker(false)}>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { backgroundColor: colors.background, borderTopColor: colors.border }]}>
            <View style={styles.modalHeader}>
              <Text style={[styles.modalTitle, { color: colors.foreground }]}>Link to Work</Text>
              <Pressable onPress={() => setShowWorkPicker(false)} hitSlop={8}>
                <Feather name="x" size={20} color={colors.mutedForeground} />
              </Pressable>
            </View>
            <ScrollView>
              <Pressable
                onPress={() => handleLinkWork(null)}
                style={[styles.workOption, { borderColor: colors.border }]}
                disabled={linkingWork}
              >
                <Feather name="x-circle" size={16} color={colors.mutedForeground} />
                <Text style={[styles.workOptionText, { color: colors.mutedForeground }]}>No Work (unlink)</Text>
              </Pressable>
              {works.map((w: any) => (
                <Pressable
                  key={w.id}
                  onPress={() => handleLinkWork(w.id)}
                  style={[
                    styles.workOption,
                    { borderColor: colors.border },
                    w.id === doc.work_id && { backgroundColor: colors.primary + '10' },
                  ]}
                  disabled={linkingWork}
                >
                  <Feather name="briefcase" size={16} color={w.id === doc.work_id ? colors.primary : colors.foreground} />
                  <Text style={[styles.workOptionText, { color: w.id === doc.work_id ? colors.primary : colors.foreground }]}>
                    {w.title}
                  </Text>
                  {w.id === doc.work_id && <Feather name="check" size={16} color={colors.primary} />}
                </Pressable>
              ))}
            </ScrollView>
            {linkingWork && (
              <View style={styles.modalLoading}>
                <ActivityIndicator color={colors.primary} />
                <Text style={[styles.modalLoadingText, { color: colors.mutedForeground }]}>Saving…</Text>
              </View>
            )}
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: { paddingHorizontal: 16, paddingBottom: 14, borderBottomWidth: 1 },
  backRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 },
  backLabel: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', letterSpacing: -0.3, marginBottom: 8 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 5 },
  badgeText: { fontSize: 11, fontFamily: 'Inter_600SemiBold', letterSpacing: 0.3 },
  metaText: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  listenBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    alignSelf: 'flex-start', marginTop: 10,
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20, borderWidth: 1,
  },
  listenBtnText: { fontSize: 13, fontFamily: 'Inter_500Medium' },
  section: { borderWidth: 1, borderRadius: 10, padding: 14, marginBottom: 14 },
  sectionTitle: { fontSize: 10, fontFamily: 'Inter_700Bold', letterSpacing: 1, marginBottom: 10 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  rowLabel: { fontSize: 13, fontFamily: 'Inter_400Regular', flex: 1 },
  rowValue: { fontSize: 13, fontFamily: 'Inter_500Medium', flex: 2, textAlign: 'right' },
  workChip: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 7, borderWidth: 1,
  },
  workChipText: { fontSize: 12, fontFamily: 'Inter_500Medium', maxWidth: 140 },
  errorBox: {
    flexDirection: 'row', gap: 8, padding: 12,
    borderRadius: 8, borderWidth: 1, marginBottom: 14, alignItems: 'flex-start',
  },
  errorText: { fontSize: 13, fontFamily: 'Inter_400Regular', flex: 1 },
  reviewHeader: { fontSize: 12, fontFamily: 'Inter_600SemiBold', marginBottom: 8 },
  knowledgeItem: { padding: 10, borderRadius: 8, borderWidth: 1, marginBottom: 8 },
  knText: { fontSize: 13, fontFamily: 'Inter_400Regular', lineHeight: 18 },
  knFooter: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 },
  knMeta: { fontSize: 11, fontFamily: 'Inter_400Regular', flex: 1 },
  reviewButtons: { flexDirection: 'row', gap: 6 },
  reviewBtn: { padding: 6, borderRadius: 6, minWidth: 28, alignItems: 'center' },
  emptyText: { fontSize: 13, fontFamily: 'Inter_400Regular', textAlign: 'center', marginVertical: 12 },
  backBtn: { marginTop: 16, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 8, borderWidth: 1 },
  backBtnText: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  // Modal
  modalOverlay: { flex: 1, backgroundColor: '#00000060', justifyContent: 'flex-end' },
  modalSheet: { borderTopWidth: 1, borderTopLeftRadius: 20, borderTopRightRadius: 20, maxHeight: '70%' },
  modalHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 16 },
  modalTitle: { fontSize: 17, fontFamily: 'Inter_700Bold' },
  workOption: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingHorizontal: 16, paddingVertical: 14, borderBottomWidth: 1,
  },
  workOptionText: { fontSize: 15, fontFamily: 'Inter_500Medium', flex: 1 },
  modalLoading: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 16, justifyContent: 'center' },
  modalLoadingText: { fontSize: 13, fontFamily: 'Inter_400Regular' },
});
