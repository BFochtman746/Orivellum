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
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useGetDocument, useListWorks } from '@workspace/api-client-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

// ── Related documents collapsible section ────────────────────────────────────

interface RelatedDoc {
  doc_id: string;
  title: string;
  kind: string | null;
  similarity: number | null;
  link_type: string;
  shared_topics: Array<{ id: string; name: string }>;
}

function RelatedSection({ docId, domain, colors, onNavigate, onTopicPress }: {
  docId: string;
  domain: string;
  colors: ReturnType<typeof import('@/hooks/useColors').useColors>;
  onNavigate: (id: string) => void;
  onTopicPress: (topicId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading } = useQuery<{ doc_id: string; related: RelatedDoc[] }>({
    queryKey: ['doc-related-mobile', docId],
    queryFn: async () => {
      const res = await mobileFetch(`https://${domain}/api/library/${docId}/related`);
      if (!res.ok) throw new Error('Failed to load related');
      return res.json();
    },
    enabled: !!docId,
    staleTime: 120_000,
  });
  const related = (data?.related ?? []).slice(0, 8);

  // Hidden entirely when we know there are no relations
  if (!isLoading && data && related.length === 0) return null;

  return (
    <View style={[{ borderWidth: 1, borderRadius: 8, overflow: 'hidden', marginBottom: 12 }, { borderColor: colors.border }]}>
      <Pressable
        onPress={() => setExpanded((v) => !v)}
        style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 12, backgroundColor: colors.muted + '33' }}
      >
        <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8 }}>
          RELATED DOCUMENTS{!isLoading && related.length > 0 ? ` (${related.length})` : ''}
        </Text>
        {isLoading
          ? <ActivityIndicator size="small" color={colors.mutedForeground} style={{ opacity: 0.5 }} />
          : <Feather name={expanded ? 'chevron-up' : 'chevron-down'} size={14} color={colors.mutedForeground} />}
      </Pressable>
      {expanded && (
        <View>
          {isLoading ? (
            <ActivityIndicator color={colors.primary} style={{ margin: 16 }} />
          ) : (
            related.map((item) => (
              <Pressable
                key={item.doc_id}
                onPress={() => onNavigate(item.doc_id)}
                style={({ pressed }) => [{
                  padding: 12, borderTopWidth: 1, borderTopColor: colors.border,
                  flexDirection: 'row', alignItems: 'center', gap: 10,
                  opacity: pressed ? 0.7 : 1,
                }]}
              >
                <Feather name="file-text" size={14} color={colors.mutedForeground} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }} numberOfLines={1}>
                    {item.title || '(untitled)'}
                  </Text>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 3 }}>
                    {item.kind ? (
                      <Text style={{ fontSize: 9, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.4 }}>
                        {item.kind}
                      </Text>
                    ) : null}
                    {item.shared_topics.length > 0 && (
                      <Pressable
                        onPress={(e) => { e.stopPropagation(); onTopicPress(item.shared_topics[0].id); }}
                        hitSlop={4}
                        style={{ backgroundColor: colors.primary + '18', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 }}
                      >
                        <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: colors.primary }} numberOfLines={1}>
                          {item.shared_topics[0].name}
                        </Text>
                      </Pressable>
                    )}
                  </View>
                </View>
                {/* Similarity badge */}
                {item.similarity != null && (
                  <View style={{ backgroundColor: colors.primary + '18', paddingHorizontal: 7, paddingVertical: 3, borderRadius: 5 }}>
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_700Bold', color: colors.primary }}>
                      {Math.round(item.similarity * 100)}%
                    </Text>
                  </View>
                )}
                <Feather name="chevron-right" size={13} color={colors.mutedForeground} />
              </Pressable>
            ))
          )}
        </View>
      )}
    </View>
  );
}


// ── Chunks collapsible section ──────────────────────────────────────────────

function ChunksSection({ docId, domain, colors }: {
  docId: string;
  domain: string;
  colors: ReturnType<typeof import('@/hooks/useColors').useColors>;
}) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading } = useQuery<{ chunks: Array<{ id: string; page: number; text: string }>; count: number }>({
    queryKey: ['doc-chunks-mobile', docId],
    queryFn: async () => {
      const res = await mobileFetch(`https://${domain}/api/library/${docId}/chunks`);
      if (!res.ok) throw new Error('Failed to load chunks');
      return res.json();
    },
    enabled: !!docId && expanded,
    staleTime: 60_000,
  });
  const count = data?.count ?? 0;
  return (
    <View style={[{ borderWidth: 1, borderRadius: 8, overflow: 'hidden', marginBottom: 12 }, { borderColor: colors.border }]}>
      <Pressable
        onPress={() => setExpanded((v) => !v)}
        style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 12, backgroundColor: colors.muted + '33' }}
      >
        <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8 }}>
          CHUNKS {count > 0 ? `(${count})` : ''}
        </Text>
        <Feather name={expanded ? 'chevron-up' : 'chevron-down'} size={14} color={colors.mutedForeground} />
      </Pressable>
      {expanded && (
        <ScrollView style={{ maxHeight: 400 }} nestedScrollEnabled>
          {isLoading ? (
            <ActivityIndicator color={colors.primary} style={{ margin: 16 }} />
          ) : (data?.chunks ?? []).length === 0 ? (
            <Text style={{ padding: 12, fontSize: 12, color: colors.mutedForeground }}>No chunks extracted yet.</Text>
          ) : (
            (data?.chunks ?? []).map((chunk) => (
              <View key={chunk.id} style={{ padding: 12, borderTopWidth: 1, borderTopColor: colors.border }}>
                {chunk.page > 0 && (
                  <View style={{ alignSelf: 'flex-start', backgroundColor: colors.primary + '22', borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2, marginBottom: 4 }}>
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>p.{chunk.page}</Text>
                  </View>
                )}
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 18 }} numberOfLines={4}>
                  {chunk.text}
                </Text>
              </View>
            ))
          )}
        </ScrollView>
      )}
    </View>
  );
}

// ── Extracted text collapsible section ───────────────────────────────────────

function ExtractedTextSection({ text, colors }: { text: string; colors: ReturnType<typeof import('@/hooks/useColors').useColors> }) {
  const [expanded, setExpanded] = useState(false);
  const preview = text.slice(0, 400);
  const hasMore = text.length > 400;
  return (
    <View style={[{ borderWidth: 1, borderRadius: 8, overflow: 'hidden', marginBottom: 12 }, { borderColor: colors.border }]}>
      <Pressable
        onPress={() => setExpanded((v) => !v)}
        style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 12, backgroundColor: colors.muted + '33' }}
      >
        <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8 }}>EXTRACTED TEXT</Text>
        <Feather name={expanded ? 'chevron-up' : 'chevron-down'} size={14} color={colors.mutedForeground} />
      </Pressable>
      {expanded ? (
        <ScrollView style={{ maxHeight: 320, padding: 12 }} nestedScrollEnabled>
          <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 18 }}>{text}</Text>
        </ScrollView>
      ) : (
        <View style={{ padding: 12 }}>
          <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.foreground + 'bb', lineHeight: 18 }}>{preview}</Text>
          {hasMore && <Text style={{ fontSize: 11, color: colors.primary, marginTop: 4 }}>Tap to expand…</Text>}
        </View>
      )}
    </View>
  );
}

const READINESS_COLOR: Record<string, string> = {
  ready: '#4A8C65',
  error: '#dc2626',
  failed: '#dc2626',
  imported: '#d97706',
  transcribing: '#7c3aed',
};

const READINESS_LABEL: Record<string, string> = {
  ready: 'Ready',
  error: 'Error',
  failed: 'Failed',
  imported: 'Processing…',
  transcribing: 'Transcribing…',
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
  const [bulkReviewing, setBulkReviewing] = useState<'approve' | 'dismiss' | null>(null);
  const [showWorkPicker, setShowWorkPicker] = useState(false);
  const [linkingWork, setLinkingWork] = useState(false);
  const [lifecycleUpdating, setLifecycleUpdating] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [processingProgress, setProcessingProgress] = useState<{
    stage: string; pct: number; items_found: number; chunk_count: number;
  } | null>(null);

  const handleReprocess = async () => {
    setReprocessing(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/library/${id}/reprocess`, { method: 'POST' });
      if (!res.ok) throw new Error('Reprocess failed');
      // Poll until readiness changes from imported/error/no_text
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        await refetchDoc();
        if (attempts >= 15) { clearInterval(poll); setReprocessing(false); }
      }, 2000);
    } catch {
      Alert.alert('Error', 'Could not queue reprocess');
      setReprocessing(false);
    }
  };

  // ── Native audio player (for uploaded audio documents) ─────────────────────
  type AudioState = 'idle' | 'loading' | 'playing' | 'paused' | 'error';
  const [audioState, setAudioState] = useState<AudioState>('idle');
  const audioPlayerRef = useRef<AudioPlayer | null>(null);

  useEffect(() => {
    return () => { audioPlayerRef.current?.remove(); };
  }, []);

  const handlePlayOriginal = async () => {
    if (audioState === 'playing') {
      audioPlayerRef.current?.pause();
      setAudioState('paused');
      return;
    }
    if (audioState === 'paused' && audioPlayerRef.current) {
      audioPlayerRef.current.play();
      setAudioState('playing');
      return;
    }
    setAudioState('loading');
    try {
      await setAudioModeAsync({ playsInSilentMode: true });
      const token = getApiToken();
      const uri = `https://${domain}/api/library/${id}/download`;
      const player = createAudioPlayer({
        uri,
        headers: token ? { authorization: `Bearer ${token}` } : undefined,
      });
      audioPlayerRef.current = player;
      player.play();
      setAudioState('playing');
      player.addListener('playbackStatusUpdate', (status) => {
        if (!status.playing && status.currentTime > 0 && status.duration > 0
            && status.currentTime >= status.duration - 0.5) {
          setAudioState('idle');
          audioPlayerRef.current = null;
        }
      });
    } catch (e: any) {
      setAudioState('error');
      Alert.alert('Playback failed', e?.message ?? 'Could not play audio file');
      setTimeout(() => setAudioState('idle'), 2000);
    }
  };

  // ── Read Aloud (TTS) — chunked multi-part playback ────────────────────────
  // Long documents are split into ~4 500-char parts, synthesized on demand
  // (current part + one prefetch ahead), and played back with auto-advance.
  // A monotonic session id prevents stale synthesis results from a previous
  // invocation from clobbering state owned by the current one.
  type TtsState = 'idle' | 'loading' | 'playing' | 'paused' | 'error';
  const [ttsState, setTtsState] = useState<TtsState>('idle');
  const [ttsChunks, setTtsChunks] = useState<string[]>([]);
  const [ttsIndex, setTtsIndex] = useState(0);
  const ttsPlayerRef = useRef<AudioPlayer | null>(null);
  const ttsSessionRef = useRef(0);
  const ttsPathCacheRef = useRef<Map<number, string>>(new Map()); // part → serve path
  const ttsPromisesRef = useRef<Map<number, Promise<string>>>(new Map()); // in-flight

  useEffect(() => {
    return () => {
      ttsSessionRef.current++;
      ttsPlayerRef.current?.remove();
      ttsPathCacheRef.current.clear();
      ttsPromisesRef.current.clear();
    };
  }, []);

  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

  // ── TTS helpers ─────────────────────────────────────────────────────────────

  const TTS_PART_CHARS = 4500;
  const TTS_STALE = 'tts-stale';

  /** Split extracted text into ≤4 500-char parts at paragraph / sentence
   *  boundaries.  Uses only basic string ops so it works in Hermes/JSC. */
  const splitTextForTts = (text: string): string[] => {
    const paras = text.replace(/\n{3,}/g, '\n\n').split(/\n\n+/);
    const parts: string[] = [];
    let cur = '';
    const flush = () => { if (cur.trim()) parts.push(cur.trim()); cur = ''; };
    for (const p of paras) {
      if (p.length > TTS_PART_CHARS) {
        // Split at sentence boundaries (safe regex — no lookbehind)
        const sentences = p.replace(/([.!?])\s+/g, '$1\n').split('\n').filter(Boolean);
        for (const s of sentences) {
          if (cur && cur.length + s.length + 1 > TTS_PART_CHARS) flush();
          cur += (cur ? ' ' : '') + s;
          while (cur.length > TTS_PART_CHARS) {
            parts.push(cur.slice(0, TTS_PART_CHARS));
            cur = cur.slice(TTS_PART_CHARS);
          }
        }
      } else {
        if (cur && cur.length + p.length + 2 > TTS_PART_CHARS) flush();
        cur += (cur ? '\n\n' : '') + p;
      }
    }
    flush();
    return parts;
  };

  /** Synthesize one part, cache its serve-path, and return it.
   *  Single-flight per part via promise map; stale-session results discarded. */
  const synthesizePart = (parts: string[], i: number): Promise<string> => {
    const session = ttsSessionRef.current;
    const cached = ttsPathCacheRef.current.get(i);
    if (cached) return Promise.resolve(cached);
    const inflight = ttsPromisesRef.current.get(i);
    if (inflight) return inflight;
    const p = (async () => {
      const res = await mobileFetch(`https://${domain}/api/studio/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: parts[i], voice: 'af_heart', speed: 1.0, return_url: true }),
      });
      if (ttsSessionRef.current !== session) throw new Error(TTS_STALE);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${res.status}`);
      }
      const json = await res.json();
      if (ttsSessionRef.current !== session) throw new Error(TTS_STALE);
      const path = json.path as string;
      ttsPathCacheRef.current.set(i, path);
      return path;
    })();
    p.finally(() => {
      if (ttsPromisesRef.current.get(i) === p) ttsPromisesRef.current.delete(i);
    }).catch(() => {});
    ttsPromisesRef.current.set(i, p);
    return p;
  };

  /** Create a player for part `i`, start it, and wire auto-advance. */
  const playPartAt = async (parts: string[], i: number) => {
    const session = ttsSessionRef.current;
    setTtsState('loading');
    try {
      const servePath = await synthesizePart(parts, i);
      if (ttsSessionRef.current !== session) return;
      const token = getApiToken();
      const uri = `https://${domain}/api/studio/outputs/serve?path=${encodeURIComponent(servePath)}`;
      ttsPlayerRef.current?.remove();
      ttsPlayerRef.current = null;
      await setAudioModeAsync({ playsInSilentMode: true });
      const player = createAudioPlayer({
        uri,
        headers: token ? { authorization: `Bearer ${token}` } : undefined,
      });
      ttsPlayerRef.current = player;
      player.play();
      setTtsIndex(i);
      setTtsState('playing');
      // Prefetch next part in the background
      if (i + 1 < parts.length) synthesizePart(parts, i + 1).catch(() => {});
      // Auto-advance on natural end-of-part
      player.addListener('playbackStatusUpdate', (status) => {
        if (!status.playing && status.currentTime > 0 && status.duration > 0
            && status.currentTime >= status.duration - 0.5) {
          if (ttsSessionRef.current !== session) return;
          const next = i + 1;
          if (next < parts.length) {
            playPartAt(parts, next);
          } else {
            // All parts finished
            setTtsState('idle');
            setTtsChunks([]);
            setTtsIndex(0);
            ttsPlayerRef.current?.remove();
            ttsPlayerRef.current = null;
          }
        }
      });
    } catch (e: any) {
      if (e?.message !== TTS_STALE && ttsSessionRef.current === session) {
        setTtsState('error');
        Alert.alert('Read Aloud failed', e?.message ?? 'Could not synthesize audio');
        setTimeout(() => setTtsState('idle'), 2000);
      }
    }
  };

  const stopTts = () => {
    ttsSessionRef.current++;
    ttsPlayerRef.current?.remove();
    ttsPlayerRef.current = null;
    ttsPathCacheRef.current.clear();
    ttsPromisesRef.current.clear();
    setTtsState('idle');
    setTtsChunks([]);
    setTtsIndex(0);
  };

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

  // ── SSE live-progress stream ────────────────────────────────────────────────
  // Opens a streaming fetch to the /progress SSE endpoint while the document
  // is in a processing state.  Falls back to 4 s polling when streaming body
  // is unavailable (older React Native versions) or the connection drops.
  useEffect(() => {
    const _PROCESSING = new Set(['imported', 'transcribing']);
    const _TERMINAL   = new Set(['ready', 'error', 'no_text']);
    const readiness = doc?.readiness as string | undefined;

    if (!id || !readiness || !_PROCESSING.has(readiness)) {
      setProcessingProgress(null);
      return;
    }

    let aborted = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    const controller = new AbortController();

    const startPollingFallback = () => {
      if (pollTimer) return;
      pollTimer = setInterval(() => {
        if (!aborted) refetchDoc().catch(() => {});
      }, 4_000);
    };

    (async () => {
      try {
        const token = getApiToken();
        const headers: Record<string, string> = {};
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const response = await fetch(
          `https://${domain}/api/library/${id}/progress`,
          { signal: controller.signal, headers },
        );

        if (!response.ok || !response.body) {
          startPollingFallback();
          return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (!aborted) {
          const { done, value } = await reader.read();
          if (done || aborted) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              setProcessingProgress({
                stage:       evt.stage,
                pct:         evt.pct,
                items_found: evt.items_found,
                chunk_count: evt.chunk_count,
              });
              if (_TERMINAL.has(evt.readiness)) {
                reader.cancel();
                if (!aborted) refetchDoc().catch(() => {});
                return;
              }
            } catch {
              // malformed event — ignore
            }
          }
        }
      } catch {
        if (!aborted) startPollingFallback();
      }
    })();

    return () => {
      aborted = true;
      controller.abort();
      if (pollTimer) clearInterval(pollTimer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, doc?.readiness]);

  // Inline title editing
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');

  const saveTitleEdit = async () => {
    setEditingTitle(false);
    const trimmed = titleDraft.trim();
    if (!trimmed || trimmed === doc?.title) return;
    try {
      await mobileFetch(`https://${domain}/api/library/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed }),
      });
      refetchDoc();
    } catch { /* non-fatal */ }
  };

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
    // Start a fresh session
    ttsSessionRef.current++;
    ttsPlayerRef.current?.remove();
    ttsPlayerRef.current = null;
    ttsPathCacheRef.current.clear();
    ttsPromisesRef.current.clear();
    setTtsChunks([]);
    setTtsIndex(0);
    setTtsState('loading');
    const session = ttsSessionRef.current;
    try {
      // Prefer already-extracted text; fall back to joining chunks
      let text: string = (doc?.extracted_text as string) || '';
      if (!text.trim()) {
        const res = await mobileFetch(`https://${domain}/api/library/${id}/chunks`);
        if (ttsSessionRef.current !== session) return;
        if (res.ok) {
          const data = await res.json();
          if (ttsSessionRef.current !== session) return;
          text = (data.chunks ?? []).map((c: any) => c.text ?? '').filter(Boolean).join('\n\n');
        }
      }
      text = text.trim();
      if (!text) {
        setTtsState('idle');
        Alert.alert('No text', 'This document has no readable text to play.');
        return;
      }
      const parts = splitTextForTts(text);
      setTtsChunks(parts);
      await playPartAt(parts, 0);
    } catch (e: any) {
      if (ttsSessionRef.current === session) {
        setTtsState('error');
        Alert.alert('Read Aloud failed', e?.message ?? 'Could not start playback');
        setTimeout(() => setTtsState('idle'), 2000);
      }
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

  const handleBulkReview = async (action: 'approve' | 'dismiss') => {
    const pending = (knData as any)?.knowledge?.filter((k: any) => k.review_status === 'ai_auto') ?? [];
    if (pending.length === 0) return;
    setBulkReviewing(action);
    const status = action === 'approve' ? 'approved' : 'rejected';
    try {
      const results = await Promise.allSettled(
        pending.map(async (item: any) => {
          const res = await mobileFetch(`https://${domain}/api/knowledge/${item.id}/review`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ review_status: status }),
          });
          if (!res.ok) throw new Error(`${item.id}: server returned ${res.status}`);
        })
      );
      // Refetch regardless so any successful updates are reflected immediately
      await refetchKn();
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) {
        const succeeded = results.length - failed;
        Alert.alert(
          'Partial update',
          `${succeeded} item${succeeded !== 1 ? 's' : ''} updated, ${failed} failed. Please retry the remaining items.`
        );
      }
    } catch {
      Alert.alert('Error', `Could not ${action} all items`);
    } finally {
      setBulkReviewing(null);
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
        {editingTitle ? (
          <TextInput
            autoFocus
            style={[styles.title, { color: colors.foreground, borderBottomWidth: 2, borderBottomColor: colors.primary }]}
            value={titleDraft}
            onChangeText={setTitleDraft}
            onBlur={saveTitleEdit}
            onSubmitEditing={saveTitleEdit}
            returnKeyType="done"
            multiline={false}
          />
        ) : (
          <Pressable onLongPress={() => { setTitleDraft(docTitle); setEditingTitle(true); }} delayLongPress={500}>
            <Text style={[styles.title, { color: colors.foreground }]} numberOfLines={2}>
              {docTitle}
            </Text>
          </Pressable>
        )}
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
        {/* Share button */}
        <Pressable
          onPress={() => Share.share({ title: docTitle, message: docTitle })}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4, alignSelf: 'flex-start' }}
        >
          <Feather name="share-2" size={13} color={colors.primary} />
          <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.primary }}>Share</Text>
        </Pressable>
        {/* Reprocess button — shown for stuck documents */}
        {(doc.readiness === 'error' || doc.readiness === 'no_text' || doc.readiness === 'imported') && (
          <Pressable
            onPress={handleReprocess}
            disabled={reprocessing}
            style={[styles.listenBtn, { borderColor: '#f59e0b55', backgroundColor: '#f59e0b0f' }]}
          >
            {reprocessing
              ? <ActivityIndicator size="small" color="#d97706" />
              : <Feather name="refresh-cw" size={14} color="#d97706" />}
            <Text style={[styles.listenBtnText, { color: '#d97706' }]}>
              {reprocessing ? 'Processing…' : 'Re-extract'}
            </Text>
          </Pressable>
        )}

        {/* Live processing progress bar — shown while SSE events are arriving */}
        {(doc.readiness === 'imported' || doc.readiness === 'transcribing') && processingProgress && (
          <View style={{ marginTop: 10, marginBottom: 2 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: colors.mutedForeground, textTransform: 'capitalize' }}>
                {processingProgress.stage.replace(/_/g, ' ')}…
              </Text>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>
                {processingProgress.pct}%
              </Text>
            </View>
            <View style={{ height: 4, backgroundColor: colors.muted, borderRadius: 2, overflow: 'hidden' }}>
              <View style={{
                height: '100%',
                width: `${processingProgress.pct}%`,
                backgroundColor: colors.primary,
                borderRadius: 2,
              }} />
            </View>
            {processingProgress.items_found > 0 && (
              <Text style={{ fontSize: 10, color: colors.mutedForeground, marginTop: 3 }}>
                {processingProgress.items_found} knowledge item{processingProgress.items_found !== 1 ? 's' : ''} found
              </Text>
            )}
          </View>
        )}

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

        {/* Play Original button — only for uploaded audio documents */}
        {doc.readiness === 'ready' && doc.kind === 'audio' && (
          <Pressable
            onPress={handlePlayOriginal}
            disabled={audioState === 'loading' || audioState === 'error'}
            style={[styles.listenBtn, { borderColor: '#7c3aed55', backgroundColor: '#7c3aed0f', marginRight: 6 }]}
          >
            {audioState === 'loading' ? (
              <ActivityIndicator size="small" color="#7c3aed" />
            ) : (
              <Feather
                name={audioState === 'playing' ? 'pause' : audioState === 'paused' ? 'play' : 'music'}
                size={14}
                color="#7c3aed"
              />
            )}
            <Text style={[styles.listenBtnText, { color: '#7c3aed' }]}>
              {audioState === 'loading' ? 'Loading…'
                : audioState === 'playing' ? 'Pause'
                : audioState === 'paused' ? 'Resume'
                : 'Play'}
            </Text>
          </Pressable>
        )}

        {doc.readiness === 'ready' && (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 10 }}>
            <Pressable
              onPress={handleListen}
              disabled={ttsState === 'loading' || ttsState === 'error'}
              style={[styles.listenBtn, { borderColor: colors.primary + '55', backgroundColor: colors.primary + '0f', marginTop: 0 }]}
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
              {ttsChunks.length > 1 && (ttsState === 'playing' || ttsState === 'paused' || ttsState === 'loading') && (
                <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.primary + 'bb' }}>
                  {ttsIndex + 1}/{ttsChunks.length}
                </Text>
              )}
            </Pressable>
            {(ttsState === 'playing' || ttsState === 'paused') && (
              <Pressable
                onPress={stopTts}
                style={[styles.listenBtn, { borderColor: '#dc262655', backgroundColor: '#dc26260f', marginTop: 0 }]}
                hitSlop={6}
              >
                <Feather name="square" size={13} color="#dc2626" />
                <Text style={[styles.listenBtnText, { color: '#dc2626' }]}>Stop</Text>
              </Pressable>
            )}
          </View>
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

        {/* Extracted text — collapsible section */}
        {doc.extracted_text ? (
          <ExtractedTextSection text={doc.extracted_text as string} colors={colors} />
        ) : null}

        {/* Chunks — collapsible section */}
        <ChunksSection docId={id ?? ''} domain={domain} colors={colors} />

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
                <>
                  <Text style={[styles.reviewHeader, { color: colors.primary }]}>
                    ✦ {pendingKnowledge.length} AI item{pendingKnowledge.length !== 1 ? 's' : ''} need review
                  </Text>
                  {pendingKnowledge.length > 1 && (
                    <View style={{ flexDirection: 'row', gap: 8, marginBottom: 10 }}>
                      <Pressable
                        onPress={() => handleBulkReview('approve')}
                        disabled={!!bulkReviewing}
                        style={{
                          flex: 1, flexDirection: 'row', alignItems: 'center',
                          justifyContent: 'center', gap: 5, paddingVertical: 8,
                          borderRadius: 8, borderWidth: 1,
                          backgroundColor: '#4A8C6518', borderColor: '#4A8C6544',
                          opacity: bulkReviewing ? 0.6 : 1,
                        }}
                      >
                        {bulkReviewing === 'approve'
                          ? <ActivityIndicator size="small" color="#4A8C65" />
                          : <Feather name="thumbs-up" size={13} color="#4A8C65" />}
                        <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: '#4A8C65' }}>
                          Approve all
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={() => handleBulkReview('dismiss')}
                        disabled={!!bulkReviewing}
                        style={{
                          flex: 1, flexDirection: 'row', alignItems: 'center',
                          justifyContent: 'center', gap: 5, paddingVertical: 8,
                          borderRadius: 8, borderWidth: 1,
                          backgroundColor: '#dc262618', borderColor: '#dc262644',
                          opacity: bulkReviewing ? 0.6 : 1,
                        }}
                      >
                        {bulkReviewing === 'dismiss'
                          ? <ActivityIndicator size="small" color="#dc2626" />
                          : <Feather name="thumbs-down" size={13} color="#dc2626" />}
                        <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: '#dc2626' }}>
                          Dismiss all
                        </Text>
                      </Pressable>
                    </View>
                  )}
                </>
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

        {/* Related documents — outside Knowledge section, hidden when no results */}
        {doc.readiness === 'ready' && (
          <RelatedSection
            docId={id ?? ''}
            domain={domain}
            colors={colors}
            onNavigate={(relatedId) => router.push(`/library/${relatedId}` as any)}
            onTopicPress={(topicId) => router.push(`/topics?topicId=${topicId}` as any)}
          />
        )}
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
