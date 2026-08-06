import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { Image } from 'expo-image';
import {
  createAudioPlayer,
  setAudioModeAsync,
  type AudioPlayer,
} from 'expo-audio';
import { Feather } from '@expo/vector-icons';
import { Stack, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { mobileFetch } from '@/lib/api';
import { getApiToken } from '@/lib/token';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

// ── Voice catalog — mirrors _VOICE_CATALOG in studio.py ──────────────────────
// Full 28-voice catalog with perceptual dimensions and genre tags.
// Serves as the offline fallback; the server returns the same data at
// GET /api/studio/voices and may include additional custom profiles.
export interface VoiceEntry {
  id: string;
  name: string;
  accent?: string;   // 'american' | 'british' | 'custom'
  gender?: string;   // 'feminine' | 'masculine'
  description?: string;
  tags?: string[];
  builtin?: boolean;
}

const FALLBACK_VOICES: VoiceEntry[] = [
  // American Female
  { id: 'af_heart',   name: 'Heart',   accent: 'american', gender: 'feminine',  tags: ['literary fiction', 'memoir', 'spiritual'] },
  { id: 'af_bella',   name: 'Bella',   accent: 'american', gender: 'feminine',  tags: ['thriller', 'young adult', 'adventure'] },
  { id: 'af_nova',    name: 'Nova',    accent: 'american', gender: 'feminine',  tags: ['non-fiction', 'documentary', 'academic'] },
  { id: 'af_alloy',   name: 'Alloy',   accent: 'american', gender: 'feminine',  tags: ['academic', 'news', 'instructional'] },
  { id: 'af_sarah',   name: 'Sarah',   accent: 'american', gender: 'feminine',  tags: ['literary fiction', 'memoir', 'spiritual'] },
  { id: 'af_sky',     name: 'Sky',     accent: 'american', gender: 'feminine',  tags: ['children', 'young adult', 'fantasy'] },
  { id: 'af_jessica', name: 'Jessica', accent: 'american', gender: 'feminine',  tags: ['mystery', 'literary fiction', 'thriller'] },
  { id: 'af_kore',    name: 'Kore',    accent: 'american', gender: 'feminine',  tags: ['epic', 'literary fiction', 'mythology'] },
  { id: 'af_nicole',  name: 'Nicole',  accent: 'american', gender: 'feminine',  tags: ['memoir', 'self-help', 'romance'] },
  { id: 'af_aoede',   name: 'Aoede',   accent: 'american', gender: 'feminine',  tags: ['literary fiction', 'poetry', 'spiritual'] },
  { id: 'af_river',   name: 'River',   accent: 'american', gender: 'feminine',  tags: ['meditation', 'spiritual', 'nature'] },
  // American Male
  { id: 'am_adam',    name: 'Adam',    accent: 'american', gender: 'masculine', tags: ['epic', 'historical', 'thriller'], builtin: true },
  { id: 'am_echo',    name: 'Echo',    accent: 'american', gender: 'masculine', tags: ['non-fiction', 'documentary', 'news'] },
  { id: 'am_eric',    name: 'Eric',    accent: 'american', gender: 'masculine', tags: ['memoir', 'literary fiction', 'thriller'] },
  { id: 'am_fenrir',  name: 'Fenrir',  accent: 'american', gender: 'masculine', tags: ['epic', 'mythology', 'horror'] },
  { id: 'am_liam',    name: 'Liam',    accent: 'american', gender: 'masculine', tags: ['young adult', 'adventure', 'sci-fi'] },
  { id: 'am_michael', name: 'Michael', accent: 'american', gender: 'masculine', tags: ['non-fiction', 'historical', 'documentary'] },
  { id: 'am_onyx',    name: 'Onyx',    accent: 'american', gender: 'masculine', tags: ['epic', 'thriller', 'historical'] },
  { id: 'am_puck',    name: 'Puck',    accent: 'american', gender: 'masculine', tags: ['young adult', 'adventure', 'comedy', 'fantasy'] },
  { id: 'am_santa',   name: 'Santa',   accent: 'american', gender: 'masculine', tags: ['children', 'family', 'holiday', 'feel-good'] },
  // British Female
  { id: 'bf_emma',     name: 'Emma',     accent: 'british', gender: 'feminine',  tags: ['literary fiction', 'historical', 'mystery'], builtin: true },
  { id: 'bf_alice',    name: 'Alice',    accent: 'british', gender: 'feminine',  tags: ['academic', 'documentary', 'historical'] },
  { id: 'bf_isabella', name: 'Isabella', accent: 'british', gender: 'feminine',  tags: ['literary fiction', 'romance', 'historical'] },
  { id: 'bf_lily',     name: 'Lily',     accent: 'british', gender: 'feminine',  tags: ['children', 'young adult', 'romance'] },
  // British Male
  { id: 'bm_george', name: 'George', accent: 'british', gender: 'masculine', tags: ['historical', 'literary fiction', 'epic'], builtin: true },
  { id: 'bm_daniel', name: 'Daniel', accent: 'british', gender: 'masculine', tags: ['literary fiction', 'memoir', 'mystery'] },
  { id: 'bm_fable',  name: 'Fable',  accent: 'british', gender: 'masculine', tags: ['epic', 'mythology', 'fantasy'] },
  { id: 'bm_lewis',  name: 'Lewis',  accent: 'british', gender: 'masculine', tags: ['non-fiction', 'academic', 'historical'] },
];

const SIZES = [256, 512, 768, 1024];

/** Attach the bearer token so <Image> / audio can load protected media. */
function authSource(uri: string) {
  const token = getApiToken();
  return {
    uri,
    headers: token ? { authorization: `Bearer ${token}` } : undefined,
  };
}

function serveUrl(path: string) {
  return `${API}/studio/outputs/serve?path=${encodeURIComponent(path)}`;
}

/**
 * Save an image (data URI or authenticated http(s) URL) to the device photo
 * library. On web, falls back to a browser download.
 */
async function saveImageToPhotos(uri: string, name = `orivellum_${Date.now()}.png`) {
  if (Platform.OS === 'web') {
    // Browser: trigger a normal download.
    let href = uri;
    if (!uri.startsWith('data:')) {
      const resp = await mobileFetch(uri);
      if (!resp.ok) throw new Error(`Download failed (HTTP ${resp.status})`);
      href = URL.createObjectURL(await resp.blob());
    }
    const a = document.createElement('a');
    a.href = href;
    a.download = name;
    a.click();
    if (href !== uri) setTimeout(() => URL.revokeObjectURL(href), 10_000);
    return;
  }

  const MediaLibrary = await import('expo-media-library');
  const FileSystem = await import('expo-file-system/legacy');

  const perm = await MediaLibrary.requestPermissionsAsync(true);
  if (!perm.granted) {
    throw new Error('Photos permission denied — allow access in Settings to save images.');
  }

  const dest = `${FileSystem.cacheDirectory}${name}`;
  if (uri.startsWith('data:')) {
    const base64 = uri.split(',')[1] ?? '';
    if (!base64) throw new Error('Invalid image data');
    await FileSystem.writeAsStringAsync(dest, base64, {
      encoding: FileSystem.EncodingType.Base64,
    });
  } else {
    const token = getApiToken();
    const dl = await FileSystem.downloadAsync(uri, dest, {
      headers: token ? { authorization: `Bearer ${token}` } : undefined,
    });
    if (dl.status !== 200) throw new Error(`Download failed (HTTP ${dl.status})`);
  }

  await MediaLibrary.saveToLibraryAsync(dest);
  FileSystem.deleteAsync(dest, { idempotent: true }).catch(() => {});
}

/** Small "save to Photos" icon button with busy/done states. */
function SavePhotoButton({ uri, name, compact }: { uri: string; name?: string; compact?: boolean }) {
  const colors = useColors();
  const [state, setState] = useState<'idle' | 'saving' | 'done'>('idle');

  const handleSave = async () => {
    if (state === 'saving') return;
    setState('saving');
    try {
      await saveImageToPhotos(uri, name);
      setState('done');
      setTimeout(() => setState('idle'), 2500);
    } catch (e: any) {
      setState('idle');
      Alert.alert('Could not save image', e?.message ?? 'Saving to Photos failed');
    }
  };

  if (compact) {
    return (
      <Pressable onPress={handleSave} hitSlop={8} style={styles.iconBtn} disabled={state === 'saving'}>
        {state === 'saving' ? (
          <ActivityIndicator size="small" color={colors.primary} />
        ) : (
          <Feather name={state === 'done' ? 'check' : 'download'} size={16} color={state === 'done' ? '#22c55e' : colors.primary} />
        )}
      </Pressable>
    );
  }

  return (
    <Pressable
      onPress={handleSave}
      disabled={state === 'saving'}
      style={({ pressed }) => [
        styles.saveButton,
        { borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
      ]}
    >
      {state === 'saving' ? (
        <ActivityIndicator size="small" color={colors.primary} />
      ) : (
        <Feather name={state === 'done' ? 'check' : 'download'} size={15} color={state === 'done' ? '#22c55e' : colors.primary} />
      )}
      <Text style={[styles.saveButtonText, { color: state === 'done' ? '#22c55e' : colors.primary }]}>
        {state === 'saving' ? 'Saving…' : state === 'done' ? 'Saved to Photos' : Platform.OS === 'web' ? 'Download image' : 'Save to Photos'}
      </Text>
    </Pressable>
  );
}

// ── Audio share / export ──────────────────────────────────────────────────────

/**
 * Download an authenticated audio URL and open the native share sheet.
 * On web, falls back to a browser download (same as saveImageToPhotos).
 */
async function shareAudioFile(uri: string, name = `orivellum_${Date.now()}.mp3`) {
  if (Platform.OS === 'web') {
    const resp = await mobileFetch(uri);
    if (!resp.ok) throw new Error(`Download failed (HTTP ${resp.status})`);
    const href = URL.createObjectURL(await resp.blob());
    const a = document.createElement('a');
    a.href = href;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(href), 10_000);
    return;
  }

  const FileSystem = await import('expo-file-system/legacy');
  const Sharing    = await import('expo-sharing');

  const token = getApiToken();
  const dest  = `${FileSystem.cacheDirectory}${name}`;
  const dl = await FileSystem.downloadAsync(uri, dest, {
    headers: token ? { authorization: `Bearer ${token}` } : undefined,
  });
  if (dl.status !== 200) throw new Error(`Download failed (HTTP ${dl.status})`);

  const canShare = await Sharing.isAvailableAsync();
  if (canShare) {
    await Sharing.shareAsync(dl.uri, {
      mimeType: 'audio/mpeg',
      dialogTitle: name,
      UTI: 'public.mp3',
    });
  } else {
    Alert.alert('Share unavailable', 'Sharing is not supported on this platform.');
  }
  // Clean up cache after sharing dialog closes (best-effort)
  FileSystem.deleteAsync(dest, { idempotent: true }).catch(() => {});
}

/** Small share icon button for audio rows — busy/done feedback matches SavePhotoButton. */
function ShareAudioButton({ uri, name, compact }: { uri: string; name?: string; compact?: boolean }) {
  const colors = useColors();
  const [state, setState] = useState<'idle' | 'sharing' | 'done'>('idle');

  const handleShare = async () => {
    if (state === 'sharing') return;
    setState('sharing');
    try {
      await shareAudioFile(uri, name);
      setState('done');
      setTimeout(() => setState('idle'), 2500);
    } catch (e: any) {
      setState('idle');
      Alert.alert('Could not share audio', e?.message ?? 'Sharing failed');
    }
  };

  if (compact) {
    return (
      <Pressable onPress={handleShare} hitSlop={8} style={styles.iconBtn} disabled={state === 'sharing'}>
        {state === 'sharing' ? (
          <ActivityIndicator size="small" color={colors.primary} />
        ) : (
          <Feather
            name={state === 'done' ? 'check' : 'share-2'}
            size={16}
            color={state === 'done' ? '#22c55e' : colors.primary}
          />
        )}
      </Pressable>
    );
  }

  return (
    <Pressable
      onPress={handleShare}
      disabled={state === 'sharing'}
      style={({ pressed }) => [
        styles.saveButton,
        { borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
      ]}
    >
      {state === 'sharing' ? (
        <ActivityIndicator size="small" color={colors.primary} />
      ) : (
        <Feather
          name={state === 'done' ? 'check' : 'share-2'}
          size={15}
          color={state === 'done' ? '#22c55e' : colors.primary}
        />
      )}
      <Text style={[styles.saveButtonText, { color: state === 'done' ? '#22c55e' : colors.primary }]}>
        {state === 'sharing' ? 'Sharing…' : state === 'done' ? 'Shared!' : Platform.OS === 'web' ? 'Download' : 'Share'}
      </Text>
    </Pressable>
  );
}

// ── Shared UI bits ─────────────────────────────────────────────────────────────

function SectionCard({
  title,
  icon,
  children,
  right,
}: {
  title: string;
  icon: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  const colors = useColors();
  return (
    <View style={[styles.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={styles.cardHeader}>
        <View style={styles.cardHeaderLeft}>
          <Feather name={icon as any} size={16} color={colors.primary} />
          <Text style={[styles.cardTitle, { color: colors.foreground }]}>{title}</Text>
        </View>
        {right}
      </View>
      {children}
    </View>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  const colors = useColors();
  return <Text style={[styles.fieldLabel, { color: colors.mutedForeground }]}>{children}</Text>;
}

function PillPicker<T>({
  options,
  value,
  onChange,
  render,
}: {
  options: T[];
  value: T;
  onChange: (v: T) => void;
  render: (v: T) => string;
}) {
  const colors = useColors();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={{ flexDirection: 'row', gap: 6 }}
    >
      {options.map((opt, i) => {
        const active = opt === value;
        return (
          <Pressable
            key={i}
            onPress={() => onChange(opt)}
            style={{
              paddingHorizontal: 12,
              paddingVertical: 7,
              borderRadius: 8,
              borderWidth: 1,
              borderColor: active ? colors.primary : colors.border,
              backgroundColor: active ? colors.primary + '22' : 'transparent',
            }}
          >
            <Text
              style={{
                fontSize: 12,
                fontFamily: 'Inter_500Medium',
                color: active ? colors.primary : colors.mutedForeground,
              }}
            >
              {render(opt)}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

// ── Voice browser with sample preview ────────────────────────────────────────────

/**
 * Horizontally scrollable voice card row.
 * Tapping a card selects it; the ▶ button streams the cached sample
 * from GET /api/studio/voices/{id}/sample (generated on first request).
 */
function VoiceBrowserCard({
  voices,
  selectedId,
  onSelect,
  audio,
}: {
  voices: VoiceEntry[];
  selectedId: string;
  onSelect: (id: string) => void;
  audio: ReturnType<typeof useSharedAudio>;
}) {
  const colors = useColors();
  const selected = voices.find((v) => v.id === selectedId) ?? voices[0];

  const accentColor = (v: VoiceEntry) =>
    v.accent === 'british' ? '#3b82f6' : '#f59e0b';

  const genderSymbol = (v: VoiceEntry) =>
    v.gender === 'feminine' ? '♀' : v.gender === 'masculine' ? '♂' : '◆';

  return (
    <View style={{ gap: 10 }}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ flexDirection: 'row', gap: 8, paddingVertical: 2 }}
      >
        {voices.map((v) => {
          const isSelected = v.id === selectedId;
          const isPlaying  = audio.playingKey === `sample-${v.id}`;
          const isLoading  = audio.loadingKey === `sample-${v.id}`;
          const sampleUri  = `${API}/studio/voices/${encodeURIComponent(v.id)}/sample`;

          return (
            <Pressable
              key={v.id}
              onPress={() => onSelect(v.id)}
              style={[
                voiceCardStyles.card,
                {
                  borderColor:       isSelected ? colors.primary : colors.border,
                  backgroundColor:   isSelected ? colors.primary + '15' : colors.card,
                },
              ]}
            >
              {/* Name + gender */}
              <View style={voiceCardStyles.nameRow}>
                <Text
                  style={[
                    voiceCardStyles.name,
                    { color: isSelected ? colors.primary : colors.foreground },
                  ]}
                  numberOfLines={1}
                >
                  {v.name}
                </Text>
                <Text style={[voiceCardStyles.gender, { color: colors.mutedForeground }]}>
                  {genderSymbol(v)}
                </Text>
              </View>

              {/* Accent badge */}
              {v.accent && (
                <View
                  style={[
                    voiceCardStyles.accentBadge,
                    { borderColor: accentColor(v) + '55', backgroundColor: accentColor(v) + '18' },
                  ]}
                >
                  <Text style={[voiceCardStyles.accentText, { color: accentColor(v) }]}>
                    {v.accent === 'american' ? 'US' : v.accent === 'british' ? 'UK' : v.accent}
                  </Text>
                </View>
              )}

              {/* Sample preview button — shows spinner while the first-time
                  MP3 is being generated server-side (can take 5–15 s). */}
              <Pressable
                onPress={(e) => {
                  e.stopPropagation?.();
                  audio.toggle(`sample-${v.id}`, sampleUri);
                }}
                hitSlop={6}
                style={[
                  voiceCardStyles.playBtn,
                  {
                    backgroundColor: isPlaying ? colors.primary : colors.muted,
                  },
                ]}
              >
                {isLoading ? (
                  <ActivityIndicator size="small" color={colors.mutedForeground} style={{ transform: [{ scale: 0.6 }] }} />
                ) : (
                  <Feather
                    name={isPlaying ? 'pause' : 'play'}
                    size={11}
                    color={isPlaying ? colors.primaryForeground : colors.mutedForeground}
                  />
                )}
              </Pressable>
            </Pressable>
          );
        })}
      </ScrollView>

      {/* Selected voice info */}
      {selected && (
        <View
          style={[
            voiceCardStyles.selectedInfo,
            { borderColor: colors.border, backgroundColor: colors.muted + '60' },
          ]}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <Text style={[voiceCardStyles.selectedName, { color: colors.foreground }]}>
              {selected.name}
            </Text>
            {selected.accent && (
              <Text style={[voiceCardStyles.selectedMeta, { color: colors.mutedForeground }]}>
                {selected.accent === 'american' ? 'American' : selected.accent === 'british' ? 'British' : selected.accent}
                {selected.gender ? ` · ${selected.gender}` : ''}
              </Text>
            )}
          </View>
          {selected.tags && selected.tags.length > 0 && (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
              {selected.tags.slice(0, 3).map((tag) => (
                <View
                  key={tag}
                  style={[voiceCardStyles.tag, { borderColor: colors.border }]}
                >
                  <Text style={[voiceCardStyles.tagText, { color: colors.mutedForeground }]}>
                    {tag}
                  </Text>
                </View>
              ))}
            </View>
          )}
        </View>
      )}
    </View>
  );
}

const voiceCardStyles = StyleSheet.create({
  card: {
    width: 110,
    borderRadius: 10,
    borderWidth: 1.5,
    padding: 10,
    gap: 6,
  },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  name: { fontSize: 13, fontFamily: 'Inter_600SemiBold', flex: 1 },
  gender: { fontSize: 11 },
  accentBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
  },
  accentText: { fontSize: 9, fontFamily: 'Inter_600SemiBold' },
  playBtn: {
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'flex-start',
    marginTop: 2,
  },
  selectedInfo: {
    borderRadius: 8,
    borderWidth: 1,
    padding: 10,
  },
  selectedName: { fontSize: 13, fontFamily: 'Inter_600SemiBold' },
  selectedMeta: { fontSize: 11, fontFamily: 'Inter_400Regular' },
  tag: {
    borderRadius: 4,
    borderWidth: 1,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  tagText: { fontSize: 9, fontFamily: 'Inter_400Regular' },
});

// ── Playback (single shared player) ─────────────────────────────────────────────

function useSharedAudio() {
  const playerRef = useRef<AudioPlayer | null>(null);
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  // loadingKey: set as soon as the user taps play on a voice that hasn't
  // buffered yet (e.g. /voices/{id}/sample which generates the MP3 on first
  // request — can take 5-15 s).  Cleared once the player reports isLoaded/playing.
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  // Ref mirrors loadingKey so the setInterval closure always reads the current
  // value — React state is async, so the captured value in the closure would
  // otherwise be stale (always null when the interval is first created).
  const loadingKeyRef = useRef<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setAudioModeAsync({ playsInSilentMode: true }).catch(() => {});
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      try {
        playerRef.current?.remove();
      } catch {}
    };
  }, []);

  const stop = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    try {
      playerRef.current?.pause();
      playerRef.current?.remove();
    } catch {}
    playerRef.current = null;
    setPlayingKey(null);
    setLoadingKey(null);
    loadingKeyRef.current = null;
  };

  const toggle = (key: string, uri: string) => {
    if (playingKey === key) {
      stop();
      return;
    }
    // Guard: ignore a second tap while the same voice is still loading.
    // Read from the ref so this check is never stale across re-renders.
    if (loadingKeyRef.current === key) return;

    // Switch source — tear down any previous player first.
    if (pollRef.current) clearInterval(pollRef.current);
    try {
      playerRef.current?.remove();
    } catch {}
    playerRef.current = null;
    setPlayingKey(null);

    // Mark as loading *before* creating the player so the UI updates
    // immediately on the first render after the tap.
    setLoadingKey(key);
    loadingKeyRef.current = key; // sync update — read by the interval closure

    try {
      const player = createAudioPlayer(authSource(uri));
      playerRef.current = player;
      player.play();
      // Poll for load completion, end-of-track, and errors.
      // Uses loadingKeyRef (not the captured state) to avoid stale-closure bugs.
      pollRef.current = setInterval(() => {
        const st = player.currentStatus;
        // Transition loading → playing once the player has buffered and started.
        if (st?.isLoaded || st?.playing) {
          if (loadingKeyRef.current === key) {
            setPlayingKey(key);
            setLoadingKey(null);
            loadingKeyRef.current = null;
          }
        }
        if (st?.didJustFinish || (st?.isLoaded && !st.playing && st.currentTime > 0 && st.duration > 0 && st.currentTime >= st.duration - 0.25)) {
          stop();
        }
        if (st?.error) {
          Alert.alert(
            'Voice not ready',
            'Could not load the voice sample. It may still be generating — please try again in a moment.',
          );
          stop();
        }
      }, 600);
    } catch (e: any) {
      // Player creation failed — clear loading state before alerting.
      setLoadingKey(null);
      loadingKeyRef.current = null;
      Alert.alert('Playback failed', e?.message ?? 'Could not play audio');
    }
  };

  return { playingKey, loadingKey, toggle, stop };
}

// ── Voice Designer ───────────────────────────────────────────────────────────────

interface DesignMatch {
  voice_id: string;
  match_score: number;
  why: string;
  voice?: VoiceEntry;
}

interface DesignResult {
  description: string;
  interpretation: string;
  matches: DesignMatch[];
}

const _DESIGN_PLACEHOLDERS = [
  'Warm, British, like a BBC documentary narrator…',
  'Deep and commanding, ancient gravitas, male…',
  'Young, bright, energetic — perfect for adventure…',
  'Calm and contemplative, slow-paced, meditative…',
  'Intimate and feminine, like a close friend reading aloud…',
];

/**
 * Inline collapsible Voice Designer.
 * Tap "Design a voice with AI" to expand; enter a natural-language description;
 * receive top 3 matched voices with scores, rationale, preview, and a
 * "Use [Name]" button that calls onUseVoice and collapses the panel.
 */
function VoiceDesignerCard({
  voices,
  audio,
  onUseVoice,
}: {
  voices: VoiceEntry[];
  audio: ReturnType<typeof useSharedAudio>;
  onUseVoice: (id: string) => void;
}) {
  const colors = useColors();
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DesignResult | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  // Stable placeholder index per mount
  const [phIdx] = useState(() => Math.floor(Math.random() * _DESIGN_PLACEHOLDERS.length));

  const handleDesign = async () => {
    if (!description.trim()) return;
    setLoading(true);
    setResult(null);
    setErrorMsg('');
    try {
      const resp = await mobileFetch(`${API}/studio/voices/design`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: description.trim() }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const data: DesignResult = await resp.json();
      setResult(data);
    } catch (e: any) {
      setErrorMsg(e?.message ?? 'Voice design failed');
    } finally {
      setLoading(false);
    }
  };

  const handleUse = (id: string) => {
    onUseVoice(id);
    // Collapse + reset after selection
    setOpen(false);
    setResult(null);
    setDescription('');
    setErrorMsg('');
  };

  // ── Collapsed trigger button ──────────────────────────────────────────────────
  if (!open) {
    return (
      <Pressable
        onPress={() => setOpen(true)}
        style={({ pressed }) => ({
          flexDirection: 'row',
          alignItems: 'center',
          gap: 6,
          paddingHorizontal: 12,
          paddingVertical: 8,
          borderRadius: 8,
          borderWidth: 1,
          borderColor: colors.primary + '55',
          backgroundColor: colors.primary + '0a',
          alignSelf: 'flex-start',
          opacity: pressed ? 0.7 : 1,
        })}
        accessibilityRole="button"
        accessibilityLabel="Design a voice with AI"
      >
        <Feather name="zap" size={12} color={colors.primary} />
        <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.primary }}>
          Design a voice with AI
        </Text>
      </Pressable>
    );
  }

  // ── Expanded panel ────────────────────────────────────────────────────────────
  return (
    <View style={[vdStyles.container, { borderColor: colors.border, backgroundColor: colors.muted + '30' }]}>
      {/* Header */}
      <View style={vdStyles.header}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Feather name="zap" size={14} color={colors.primary} />
          <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
            Design a voice
          </Text>
        </View>
        <Pressable
          onPress={() => { setOpen(false); setResult(null); setErrorMsg(''); }}
          hitSlop={10}
        >
          <Feather name="x" size={16} color={colors.mutedForeground} />
        </Pressable>
      </View>

      <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 17 }}>
        Describe the narrator in plain language. The AI maps your description to the closest voice.
      </Text>

      {/* Description input */}
      <TextInput
        style={[styles.textArea, {
          color: colors.foreground,
          borderColor: colors.border,
          backgroundColor: colors.background,
          minHeight: 70,
        }]}
        placeholder={_DESIGN_PLACEHOLDERS[phIdx]}
        placeholderTextColor={colors.mutedForeground}
        value={description}
        onChangeText={setDescription}
        multiline
        maxLength={500}
        returnKeyType="done"
        blurOnSubmit
      />

      <Pressable
        onPress={handleDesign}
        disabled={!description.trim() || loading}
        style={({ pressed }) => [styles.primaryButton, {
          backgroundColor: colors.primary,
          opacity: !description.trim() || loading ? 0.45 : pressed ? 0.85 : 1,
        }]}
      >
        {loading
          ? <ActivityIndicator color={colors.primaryForeground} size="small" />
          : <Feather name="search" size={15} color={colors.primaryForeground} />
        }
        <Text style={[styles.primaryButtonText, { color: colors.primaryForeground }]}>
          {loading ? 'Matching…' : 'Find matching voices'}
        </Text>
      </Pressable>

      {/* Error state */}
      {!!errorMsg && (
        <View style={[vdStyles.infoBox, { borderColor: '#ef444444', backgroundColor: '#ef444410' }]}>
          <Feather name="alert-circle" size={12} color="#ef4444" />
          <Text style={{ color: '#ef4444', fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 }}>
            {errorMsg}
          </Text>
        </View>
      )}

      {/* Results */}
      {result && (
        <View style={{ gap: 10 }}>
          {/* AI interpretation of the description */}
          {!!result.interpretation && (
            <View style={[vdStyles.infoBox, { borderColor: colors.primary + '33', backgroundColor: colors.primary + '08' }]}>
              <Feather name="info" size={12} color={colors.primary} />
              <Text style={{ color: colors.primary, fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 }}>
                {result.interpretation}
              </Text>
            </View>
          )}

          {/* Matched voice cards */}
          {(result.matches ?? []).slice(0, 3).map((m, i) => {
            // Prefer the enriched voice from the server; fall back to local catalog
            const v: VoiceEntry = (m.voice as VoiceEntry | undefined) ??
              voices.find(vv => vv.id === m.voice_id) ??
              { id: m.voice_id, name: m.voice_id };
            const isPlaying = audio.playingKey === `sample-${v.id}`;
            const isLoading = audio.loadingKey === `sample-${v.id}`;
            const sampleUri = `${API}/studio/voices/${encodeURIComponent(v.id)}/sample`;
            const accentCol = v.accent === 'british' ? '#3b82f6' : '#f59e0b';
            const genderSym = v.gender === 'feminine' ? '♀' : v.gender === 'masculine' ? '♂' : '◆';
            const score = m.match_score ?? 0;
            const scoreColor = score >= 85 ? '#22c55e' : score >= 70 ? '#f59e0b' : colors.mutedForeground;

            return (
              <View
                key={v.id}
                style={[vdStyles.matchCard, {
                  borderColor: i === 0 ? colors.primary + '55' : colors.border,
                  backgroundColor: i === 0 ? colors.primary + '06' : colors.card,
                }]}
              >
                {/* Name row */}
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  {i === 0 && (
                    <View style={[vdStyles.bestBadge, {
                      backgroundColor: colors.primary + '20',
                      borderColor: colors.primary + '44',
                    }]}>
                      <Text style={{ fontSize: 9, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>
                        BEST MATCH
                      </Text>
                    </View>
                  )}
                  <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
                    {v.name}
                  </Text>
                  <Text style={{ fontSize: 12, color: colors.mutedForeground }}>{genderSym}</Text>
                  {v.accent && (
                    <View style={[vdStyles.accentBadge, {
                      borderColor: accentCol + '55',
                      backgroundColor: accentCol + '18',
                    }]}>
                      <Text style={{ fontSize: 9, fontFamily: 'Inter_600SemiBold', color: accentCol }}>
                        {v.accent === 'american' ? 'US' : v.accent === 'british' ? 'UK' : v.accent}
                      </Text>
                    </View>
                  )}
                  <Text style={{
                    marginLeft: 'auto' as any,
                    fontSize: 16, fontFamily: 'Inter_700Bold', color: scoreColor,
                  }}>
                    {score}%
                  </Text>
                </View>

                {/* AI rationale */}
                {!!m.why && (
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 17 }}>
                    {m.why}
                  </Text>
                )}

                {/* Genre tags */}
                {v.tags && v.tags.length > 0 && (
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4 }}>
                    {v.tags.slice(0, 3).map(tag => (
                      <View key={tag} style={[voiceCardStyles.tag, { borderColor: colors.border }]}>
                        <Text style={[voiceCardStyles.tagText, { color: colors.mutedForeground }]}>{tag}</Text>
                      </View>
                    ))}
                  </View>
                )}

                {/* Preview + Use actions */}
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <Pressable
                    onPress={() => audio.toggle(`sample-${v.id}`, sampleUri)}
                    style={[vdStyles.actionBtn, {
                      borderColor: (isPlaying || isLoading) ? colors.primary : colors.border,
                      backgroundColor: isPlaying ? colors.primary + '18' : 'transparent',
                      flex: 1,
                    }]}
                  >
                    {isLoading ? (
                      <ActivityIndicator size="small" color={colors.primary} style={{ transform: [{ scale: 0.75 }] }} />
                    ) : (
                      <Feather
                        name={isPlaying ? 'pause' : 'play'}
                        size={13}
                        color={isPlaying ? colors.primary : colors.mutedForeground}
                      />
                    )}
                    <Text style={{
                      fontSize: 12, fontFamily: 'Inter_500Medium',
                      color: (isPlaying || isLoading) ? colors.primary : colors.mutedForeground,
                    }}>
                      {isLoading ? 'Loading…' : isPlaying ? 'Playing…' : 'Preview'}
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => handleUse(v.id)}
                    style={({ pressed }) => [vdStyles.actionBtn, {
                      backgroundColor: colors.primary,
                      borderColor: colors.primary,
                      flex: 1,
                      opacity: pressed ? 0.85 : 1,
                    }]}
                  >
                    <Feather name="check" size={13} color={colors.primaryForeground} />
                    <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.primaryForeground }}>
                      Use {v.name}
                    </Text>
                  </Pressable>
                </View>
              </View>
            );
          })}
        </View>
      )}
    </View>
  );
}

const vdStyles = StyleSheet.create({
  container: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    gap: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  infoBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    borderRadius: 8,
    borderWidth: 1,
    padding: 10,
  },
  matchCard: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 12,
    gap: 8,
  },
  bestBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
  },
  accentBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    paddingVertical: 9,
    borderRadius: 8,
    borderWidth: 1,
  },
});

// ── Voice Recommender (Work-aware AI narrator picks) ─────────────────────────────

interface RecommendRec {
  voice_id: string;
  score: number;
  headline: string;
  rationale: string;
  dimension_match: string;
  voice?: VoiceEntry;
}

interface RecommendResult {
  work_id: string;
  work_title: string;
  genre_analysis: string;
  narrator_profile: string;
  recommendations: RecommendRec[];
}

/**
 * Collapsible card that calls POST /api/studio/voices/recommend for a Work
 * and presents the top 3 narrator matches with score, rationale, sample
 * preview, and a one-tap "Use this voice" action.
 *
 * Visual design mirrors VoiceDesignerCard for consistency.
 */
function VoiceRecommenderCard({
  workId,
  workTitle,
  voices,
  audio,
  onUseVoice,
}: {
  workId: string | null;
  workTitle?: string;
  voices: VoiceEntry[];
  audio: ReturnType<typeof useSharedAudio>;
  onUseVoice: (id: string) => void;
}) {
  const colors = useColors();
  const [open, setOpen]           = useState(false);
  const [loading, setLoading]     = useState(false);
  const [result, setResult]       = useState<RecommendResult | null>(null);
  const [errorMsg, setErrorMsg]   = useState('');

  /**
   * cachedWorkId — workId whose result is currently shown (null = no cache).
   * requestGen   — monotonically increasing counter; each fetchRecs call
   *                captures its own generation token. Responses only apply
   *                state when their token still equals the current counter.
   *                This handles:
   *                  • rapid Retry (same Work, two in-flight requests)
   *                  • A→B→A selection (same wid, different generation)
   *                  • A→null (unmount / deselect path below)
   */
  const cachedWorkId = useRef<string | null>(null);
  const requestGen   = useRef(0);

  /** Invalidate any in-flight request without issuing a new one. */
  const invalidate = () => { ++requestGen.current; };

  /**
   * fetchRecs is the SINGLE place a recommendation POST is dispatched.
   * Ownership is tracked by a per-call generation token, not by workId,
   * so even two calls for the same Work can be correctly distinguished.
   */
  const fetchRecs = async (wid: string) => {
    const myGen = ++requestGen.current; // unique token for this call
    cachedWorkId.current = null;

    setLoading(true);
    setResult(null);
    setErrorMsg('');

    try {
      const resp = await mobileFetch(`${API}/studio/voices/recommend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_id: wid, top_n: 3 }),
      });

      if (requestGen.current !== myGen) return; // superseded — discard

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }

      const data: RecommendResult = await resp.json();

      if (requestGen.current !== myGen) return; // superseded after JSON parse

      setResult(data);
      cachedWorkId.current = wid;
    } catch (e: any) {
      if (requestGen.current !== myGen) return; // stale error — discard silently
      setErrorMsg(e?.message ?? 'Recommendation unavailable — the AI may be offline');
    } finally {
      if (requestGen.current === myGen) setLoading(false);
    }
  };

  /** Invalidate in-flight requests on unmount to prevent post-unmount state updates. */
  useEffect(() => () => { invalidate(); }, []);

  /**
   * When workId becomes null (user deselects the Work): close the panel,
   * clear all state, and invalidate any in-flight request immediately.
   * This runs before the fetch-dispatch effect so the panel never shows
   * stale picks after the work context is removed.
   */
  useEffect(() => {
    if (!workId) {
      invalidate();
      setOpen(false);
      setResult(null);
      setErrorMsg('');
      setLoading(false);
      cachedWorkId.current = null;
    }
  }, [workId]);

  /**
   * The effect is the ONLY dispatch point — handleOpen only sets open=true.
   * Runs when open flips true or workId changes while open.
   * The null-workId effect above already closed the panel before this fires
   * in the deselect case, so the `open && workId` guard is always correct.
   */
  useEffect(() => {
    if (open && workId && cachedWorkId.current !== workId) {
      fetchRecs(workId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workId, open]);

  /** Only open if a Work is actually selected; fetching is handled by the effect. */
  const handleOpen = () => { if (workId) setOpen(true); };

  const handleUse = (id: string) => {
    onUseVoice(id);
    setOpen(false);
  };

  // ── Collapsed trigger ────────────────────────────────────────────────────────
  if (!open) {
    const hasFreshResult = result !== null && cachedWorkId.current === workId;
    return (
      <Pressable
        onPress={workId ? handleOpen : undefined}
        disabled={!workId}
        style={({ pressed }) => ({
          flexDirection: 'row',
          alignItems: 'center',
          gap: 6,
          paddingHorizontal: 12,
          paddingVertical: 8,
          borderRadius: 8,
          borderWidth: 1,
          borderColor: workId ? colors.primary + '55' : colors.border,
          backgroundColor: workId ? colors.primary + '0a' : 'transparent',
          alignSelf: 'flex-start',
          opacity: !workId ? 0.4 : pressed ? 0.7 : 1,
        })}
        accessibilityRole="button"
        accessibilityLabel="AI narrator recommendations for this Work"
      >
        <Feather name="star" size={12} color={workId ? colors.primary : colors.mutedForeground} />
        <Text style={{
          fontSize: 12,
          fontFamily: 'Inter_500Medium',
          color: workId ? colors.primary : colors.mutedForeground,
        }}>
          {hasFreshResult ? 'AI picks · tap to review' : 'Best for this Work'}
        </Text>
        {hasFreshResult && (
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: colors.primary }} />
        )}
      </Pressable>
    );
  }

  // ── Expanded panel ────────────────────────────────────────────────────────────
  return (
    <View style={[vdStyles.container, {
      borderColor: colors.primary + '44',
      backgroundColor: colors.primary + '06',
    }]}>
      {/* Header */}
      <View style={vdStyles.header}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Feather name="star" size={14} color={colors.primary} />
          <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
            Best for {workTitle ? `"${workTitle}"` : 'this Work'}
          </Text>
        </View>
        <Pressable onPress={() => setOpen(false)} hitSlop={10}>
          <Feather name="x" size={16} color={colors.mutedForeground} />
        </Pressable>
      </View>

      <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 17 }}>
        AI casting director analysis — genre, tone, and style matched to the best available narrators.
      </Text>

      {/* Loading */}
      {loading && (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 6 }}>
          <ActivityIndicator size="small" color={colors.primary} />
          <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
            Analysing your Work…
          </Text>
        </View>
      )}

      {/* Error with retry */}
      {!!errorMsg && !loading && (
        <View style={{ gap: 8 }}>
          <View style={[vdStyles.infoBox, { borderColor: '#ef444444', backgroundColor: '#ef444410' }]}>
            <Feather name="alert-circle" size={12} color="#ef4444" />
            <Text style={{ color: '#ef4444', fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 }}>
              {errorMsg}
            </Text>
          </View>
          <Pressable
            onPress={() => workId && fetchRecs(workId)}
            style={({ pressed }) => [styles.primaryButton, {
              backgroundColor: colors.muted,
              opacity: pressed ? 0.7 : 1,
            }]}
          >
            <Feather name="refresh-cw" size={14} color={colors.foreground} />
            <Text style={[styles.primaryButtonText, { color: colors.foreground }]}>Retry</Text>
          </Pressable>
        </View>
      )}

      {/* Results */}
      {result && !loading && (
        <View style={{ gap: 10 }}>
          {/* Genre analysis */}
          {!!result.genre_analysis && (
            <View style={[vdStyles.infoBox, {
              borderColor: colors.primary + '33',
              backgroundColor: colors.primary + '08',
            }]}>
              <Feather name="book-open" size={12} color={colors.primary} />
              <Text style={{ color: colors.primary, fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 }}>
                {result.genre_analysis}
              </Text>
            </View>
          )}

          {/* Narrator profile */}
          {!!result.narrator_profile && (
            <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 17 }}>
              Ideal narrator: {result.narrator_profile}
            </Text>
          )}

          {/* Ranked voice cards */}
          {(result.recommendations ?? []).slice(0, 3).map((rec, i) => {
            const v: VoiceEntry = (rec.voice as VoiceEntry | undefined) ??
              voices.find(vv => vv.id === rec.voice_id) ??
              { id: rec.voice_id, name: rec.voice_id };
            const isPlaying  = audio.playingKey === `sample-${v.id}`;
            const isLoading  = audio.loadingKey === `sample-${v.id}`;
            const sampleUri  = `${API}/studio/voices/${encodeURIComponent(v.id)}/sample`;
            const accentCol  = v.accent === 'british' ? '#3b82f6' : '#f59e0b';
            const genderSym  = v.gender === 'feminine' ? '♀' : v.gender === 'masculine' ? '♂' : '◆';
            const score      = rec.score ?? 0;
            const scoreColor = score >= 85 ? '#22c55e' : score >= 70 ? '#f59e0b' : colors.mutedForeground;

            return (
              <View
                key={v.id}
                style={[vdStyles.matchCard, {
                  borderColor: i === 0 ? colors.primary + '55' : colors.border,
                  backgroundColor: i === 0 ? colors.primary + '06' : colors.card,
                }]}
              >
                {/* Name row */}
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  {i === 0 && (
                    <View style={[vdStyles.bestBadge, {
                      backgroundColor: colors.primary + '20',
                      borderColor: colors.primary + '44',
                    }]}>
                      <Text style={{ fontSize: 9, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>
                        TOP PICK
                      </Text>
                    </View>
                  )}
                  <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
                    {v.name}
                  </Text>
                  <Text style={{ fontSize: 12, color: colors.mutedForeground }}>{genderSym}</Text>
                  {v.accent && (
                    <View style={[vdStyles.accentBadge, {
                      borderColor: accentCol + '55',
                      backgroundColor: accentCol + '18',
                    }]}>
                      <Text style={{ fontSize: 9, fontFamily: 'Inter_600SemiBold', color: accentCol }}>
                        {v.accent === 'american' ? 'US' : v.accent === 'british' ? 'UK' : v.accent}
                      </Text>
                    </View>
                  )}
                  <Text style={{
                    marginLeft: 'auto' as any,
                    fontSize: 16, fontFamily: 'Inter_700Bold', color: scoreColor,
                  }}>
                    {score}%
                  </Text>
                </View>

                {/* Headline */}
                {!!rec.headline && (
                  <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground, lineHeight: 18 }}>
                    {rec.headline}
                  </Text>
                )}

                {/* Rationale */}
                {!!rec.rationale && (
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 17 }}>
                    {rec.rationale}
                  </Text>
                )}

                {/* Genre tags */}
                {v.tags && v.tags.length > 0 && (
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4 }}>
                    {v.tags.slice(0, 3).map(tag => (
                      <View key={tag} style={[voiceCardStyles.tag, { borderColor: colors.border }]}>
                        <Text style={[voiceCardStyles.tagText, { color: colors.mutedForeground }]}>{tag}</Text>
                      </View>
                    ))}
                  </View>
                )}

                {/* Preview + Use */}
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <Pressable
                    onPress={() => audio.toggle(`sample-${v.id}`, sampleUri)}
                    style={[vdStyles.actionBtn, {
                      borderColor: (isPlaying || isLoading) ? colors.primary : colors.border,
                      backgroundColor: isPlaying ? colors.primary + '18' : 'transparent',
                      flex: 1,
                    }]}
                  >
                    {isLoading ? (
                      <ActivityIndicator size="small" color={colors.primary} style={{ transform: [{ scale: 0.75 }] }} />
                    ) : (
                      <Feather
                        name={isPlaying ? 'pause' : 'play'}
                        size={13}
                        color={isPlaying ? colors.primary : colors.mutedForeground}
                      />
                    )}
                    <Text style={{
                      fontSize: 12, fontFamily: 'Inter_500Medium',
                      color: (isPlaying || isLoading) ? colors.primary : colors.mutedForeground,
                    }}>
                      {isLoading ? 'Loading…' : isPlaying ? 'Playing…' : 'Preview'}
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => handleUse(v.id)}
                    style={({ pressed }) => [vdStyles.actionBtn, {
                      backgroundColor: colors.primary,
                      borderColor: colors.primary,
                      flex: 1,
                      opacity: pressed ? 0.85 : 1,
                    }]}
                  >
                    <Feather name="check" size={13} color={colors.primaryForeground} />
                    <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.primaryForeground }}>
                      Use {v.name}
                    </Text>
                  </Pressable>
                </View>
              </View>
            );
          })}
        </View>
      )}
    </View>
  );
}

// ── TTS panel ───────────────────────────────────────────────────────────────────

// ── Streaming TTS hook ────────────────────────────────────────────────────────

type StreamPhase = 'idle' | 'loading' | 'playing' | 'done' | 'error';

/**
 * Manages per-segment synthesis + sequential playback.
 *
 * The server streams SSE events as each ~150-word segment is synthesised.
 * The hook starts playing the first segment as soon as it arrives (< 2 s),
 * then chains subsequent segments seamlessly.
 *
 * Falls back to the existing full-file flow when:
 *  - The server returns a non-SSE response (older deployment)
 *  - The platform does not support `response.body.getReader()`
 */
function useStreamingTTS() {
  const [phase,      setPhase]      = useState<StreamPhase>('idle');
  const [segCurrent, setSegCurrent] = useState(0);  // 1-based index currently playing
  const [segTotal,   setSegTotal]   = useState(0);  // 0 = unknown yet
  const [errorMsg,   setErrorMsg]   = useState('');

  // All mutable playback state lives in refs so closure captures stay fresh.
  const playerRef       = useRef<AudioPlayer | null>(null);
  const queueRef        = useRef<string[]>([]);   // segment URIs waiting to play
  const playedRef       = useRef(0);              // segments that started playing
  const streamDoneRef   = useRef(false);          // _playNext: finalize when queue drains
  const serverDoneRef   = useRef(false);          // server's 'done' SSE event received
  const segErrorRef     = useRef(0);              // count of segment_error events
  const segTotalRef     = useRef(0);              // mirrors segTotal for done-handler
  const pollRef         = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortRef        = useRef<AbortController | null>(null);
  const mountedRef      = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      _cleanupPlayer();
      abortRef.current?.abort();
    };
  }, []);

  function _cleanupPlayer() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    try { playerRef.current?.pause(); playerRef.current?.remove(); } catch {}
    playerRef.current = null;
  }

  function _playNext() {
    _cleanupPlayer();

    const uri = queueRef.current.shift();
    if (!uri) {
      // Queue empty — finalize only when the stream has signalled it is done
      if (streamDoneRef.current && mountedRef.current) setPhase('done');
      return;
    }

    const token = getApiToken();
    try {
      const player = createAudioPlayer({
        uri,
        headers: token ? { authorization: `Bearer ${token}` } : undefined,
      });
      playerRef.current = player;
      player.play();
      playedRef.current += 1;
      if (mountedRef.current) setSegCurrent(playedRef.current);

      pollRef.current = setInterval(() => {
        if (!mountedRef.current) return;
        const st = player.currentStatus;
        const ended =
          st?.didJustFinish ||
          (st?.isLoaded && !st.playing &&
           (st.currentTime ?? 0) > 0 && (st.duration ?? 0) > 0 &&
           (st.currentTime ?? 0) >= (st.duration ?? 0) - 0.3);
        if (ended) {
          clearInterval(pollRef.current!); pollRef.current = null;
          _playNext();
        } else if (st?.error) {
          clearInterval(pollRef.current!); pollRef.current = null;
          _playNext(); // skip errored segment
        }
      }, 400);
    } catch {
      _playNext(); // skip if player creation failed
    }
  }

  function _enqueueSegment(uri: string, total: number) {
    if (!mountedRef.current) return;
    setSegTotal(total);
    queueRef.current.push(uri);
    // Start playback immediately on the first segment — this is the < 2 s path
    if (!playerRef.current && pollRef.current === null) {
      setPhase('playing');
      _playNext();
    }
  }

  async function startStream(text: string, voice: string, speed: number) {
    abortRef.current?.abort();
    _cleanupPlayer();
    queueRef.current    = [];
    streamDoneRef.current = false;
    serverDoneRef.current = false;
    playedRef.current   = 0;
    segErrorRef.current = 0;
    segTotalRef.current = 0;

    if (!mountedRef.current) return;
    setPhase('loading');
    setSegCurrent(0);
    setSegTotal(0);
    setErrorMsg('');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const resp = await mobileFetch(`${API}/studio/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice, speed, stream: true }),
        signal: controller.signal,
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({})) as any;
        throw new Error(errData.detail ?? `HTTP ${resp.status}`);
      }

      // Streaming TTS requires SSE (text/event-stream).  If the server returns
      // anything else (e.g. an older deployment that ignores the stream field),
      // we surface an explicit error rather than playing an unrelated cached
      // audio file — callers should toggle stream:false or upgrade the server.
      const ct = resp.headers.get('content-type') ?? '';
      if (!ct.includes('text/event-stream') || !resp.body) {
        if (mountedRef.current) {
          setErrorMsg(
            'Streaming not available — the server returned an unexpected response type. ' +
            'Please try again; if the problem persists, use the standard synthesis path.'
          );
          setPhase('error');
        }
        return;
      }

      // ── Parse SSE stream ────────────────────────────────────────────────────
      const reader  = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer    = '';
      let readError = false;

      // eslint-disable-next-line no-constant-condition
      while (true) {
        let chunk: { done: boolean; value?: Uint8Array };
        try {
          chunk = await reader.read();
        } catch {
          readError = true;
          break;
        }
        if (chunk.done || controller.signal.aborted) break;

        buffer += decoder.decode(chunk.value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            const evt = JSON.parse(raw) as Record<string, any>;

            if (evt.type === 'segment' && evt.path) {
              // serveUrl() percent-encodes the raw path so createAudioPlayer
              // receives a well-formed URL with no bare slashes in the query string
              _enqueueSegment(serveUrl(evt.path), evt.total ?? 0);
              segTotalRef.current = evt.total ?? segTotalRef.current;

            } else if (evt.type === 'segment_error') {
              segErrorRef.current += 1;
              segTotalRef.current = evt.total ?? segTotalRef.current;
              if (mountedRef.current) setSegTotal(evt.total ?? 0);

            } else if (evt.type === 'done') {
              // ── Server confirmed completion ─────────────────────────────
              serverDoneRef.current = true;
              const total    = evt.total ?? segTotalRef.current;
              const okCount  = evt.ok_count  ?? (total - segErrorRef.current);
              const errCount = evt.error_count ?? segErrorRef.current;
              if (mountedRef.current) setSegTotal(total);

              if (total > 0 && okCount === 0) {
                // Every segment failed — report error immediately
                if (mountedRef.current) {
                  setErrorMsg(
                    `All ${errCount} segment${errCount !== 1 ? 's' : ''} failed to synthesize. ` +
                    'Check that at least one TTS backend (Kokoro / espeak-ng) is available.'
                  );
                  setPhase('error');
                }
                return;
              }

              // Allow the playback chain to drain; _playNext will call
              // setPhase('done') when the queue empties (streamDoneRef must be
              // set first so _playNext knows it's safe to finalize).
              streamDoneRef.current = true;
              if (!playerRef.current && queueRef.current.length === 0) {
                if (mountedRef.current) setPhase('done');
              }
            }
          } catch {} // malformed SSE line — skip
        }
      }

      // ── Post-loop: connection closed ──────────────────────────────────────
      if (controller.signal.aborted) return; // user pressed Stop

      if (!serverDoneRef.current) {
        // Stream ended WITHOUT the server's 'done' event — connection truncated.
        if (playedRef.current === 0 && queueRef.current.length === 0) {
          // Nothing arrived — full failure
          if (mountedRef.current) {
            setErrorMsg(
              readError
                ? 'Connection dropped before any audio was received. Please try again.'
                : 'No audio was received from the server. Please try again.'
            );
            setPhase('error');
          }
        } else {
          // Some segments already playing or queued — let playback finish.
          // Mark stream done so _playNext will finalize once the queue drains.
          streamDoneRef.current = true;
          if (!playerRef.current && queueRef.current.length === 0 && mountedRef.current) {
            setPhase('done');
          }
          // (The user heard the audio that arrived before the disconnect.)
        }
      }

    } catch (err: any) {
      if (err?.name === 'AbortError' || controller.signal.aborted) return;
      if (mountedRef.current) {
        setErrorMsg(err?.message ?? 'TTS request failed. Please try again.');
        setPhase('error');
      }
    }
  }

  function stop() {
    abortRef.current?.abort();
    abortRef.current = null;
    _cleanupPlayer();
    queueRef.current      = [];
    streamDoneRef.current  = false;
    serverDoneRef.current  = false;
    playedRef.current     = 0;
    segErrorRef.current   = 0;
    segTotalRef.current   = 0;
    if (mountedRef.current) {
      setPhase('idle');
      setSegCurrent(0);
      setSegTotal(0);
      setErrorMsg('');
    }
  }

  return {
    phase,
    segCurrent,
    segTotal,
    errorMsg,
    startStream,
    stop,
    isActive: phase === 'loading' || phase === 'playing',
  };
}

// ── TTS Panel ─────────────────────────────────────────────────────────────────

// ── Premium engine badge ────────────────────────────────────────────────────

function PremiumEngineBadge() {
  const colors = useColors();
  const [label, setLabel] = React.useState<string | null>(null);
  const [premium, setPremium] = React.useState(false);

  React.useEffect(() => {
    mobileFetch(`${API}/studio/status`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d?.tts) return;
        setPremium(d.tts.premium_tts_active === true);
        setLabel(d.tts.best_strategy ?? null);
      })
      .catch(() => {});
  }, []);

  if (!label) return null;

  const text = premium
    ? '✦ Premium TTS active'
    : label === 'Kokoro ONNX' ? 'Kokoro neural TTS'
    : label === 'AI Server'   ? 'AI server TTS'
    : label === 'espeak-ng'   ? 'espeak-ng (basic)'
    : label;

  const badgeColor = premium ? '#7c3aed' : colors.mutedForeground;
  const badgeBg    = premium ? '#7c3aed15' : colors.muted + '60';

  return (
    <View style={{
      flexDirection: 'row', alignItems: 'center', gap: 5,
      paddingHorizontal: 10, paddingVertical: 6,
      borderRadius: 8, borderWidth: 1,
      borderColor: premium ? '#7c3aed40' : colors.border,
      backgroundColor: badgeBg,
    }}>
      <Feather name={premium ? 'star' : 'volume-2'} size={11} color={badgeColor} />
      <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: badgeColor }}>
        {text}
      </Text>
    </View>
  );
}

function TTSPanel({
  voices,
  onGenerated,
  audio,
}: {
  voices: VoiceEntry[];
  onGenerated: () => void;
  audio: ReturnType<typeof useSharedAudio>;
}) {
  const colors = useColors();
  const [text, setText] = useState('');
  const [voice, setVoice] = useState(voices[0]?.id ?? 'af_heart');
  const [speed, setSpeed] = useState(1.0);
  const overLimit = text.length > 10_000;

  const tts = useStreamingTTS();

  // Optional Work context for AI narrator recommendations
  const [works, setWorks] = useState<{ id: string; title: string }[]>([]);
  const [recWorkId, setRecWorkId] = useState<string | null>(null);

  useEffect(() => {
    mobileFetch(`${API}/works`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.works) {
          setWorks(d.works.map((w: any) => ({ id: w.id, title: w.title ?? 'Untitled' })));
        }
      })
      .catch(() => {});
  }, []);

  // Notify parent when synthesis finishes (triggers output list refresh)
  useEffect(() => {
    if (tts.phase === 'done') onGenerated();
  }, [tts.phase]);  // eslint-disable-line react-hooks/exhaustive-deps

  const progressPct = tts.segTotal > 0
    ? Math.round((tts.segCurrent / tts.segTotal) * 100)
    : 0;

  return (
    <SectionCard title="Text to Speech" icon="volume-2">
      <View style={styles.field}>
        <View style={styles.rowBetween}>
          <FieldLabel>Text</FieldLabel>
          <Text style={{ fontSize: 11, color: overLimit ? '#ef4444' : colors.mutedForeground, fontFamily: 'Inter_400Regular' }}>
            {text.length.toLocaleString()} / 10,000
          </Text>
        </View>
        <TextInput
          style={[styles.textArea, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.background }]}
          placeholder="Enter text to synthesize…"
          placeholderTextColor={colors.mutedForeground}
          value={text}
          onChangeText={setText}
          multiline
          editable={!tts.isActive}
        />
      </View>

      <View style={styles.field}>
        <View style={styles.rowBetween}>
          <FieldLabel>Voice</FieldLabel>
        </View>
        <VoiceDesignerCard
          voices={voices}
          audio={audio}
          onUseVoice={setVoice}
        />
        {/* AI narrator recommendations — optional Work context */}
        {works.length > 0 && (
          <View style={{ gap: 8 }}>
            <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
              Select a Work for AI narrator picks:
            </Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ flexDirection: 'row', gap: 6 }}
            >
              {works.map(w => {
                const active = w.id === recWorkId;
                return (
                  <Pressable
                    key={w.id}
                    onPress={() => setRecWorkId(active ? null : w.id)}
                    style={{
                      paddingHorizontal: 12,
                      paddingVertical: 7,
                      borderRadius: 8,
                      borderWidth: 1,
                      borderColor: active ? colors.primary : colors.border,
                      backgroundColor: active ? colors.primary + '22' : 'transparent',
                      maxWidth: 160,
                    }}
                  >
                    <Text
                      numberOfLines={1}
                      style={{
                        fontSize: 12,
                        fontFamily: 'Inter_500Medium',
                        color: active ? colors.primary : colors.mutedForeground,
                      }}
                    >
                      {w.title}
                    </Text>
                  </Pressable>
                );
              })}
            </ScrollView>
            <VoiceRecommenderCard
              workId={recWorkId}
              workTitle={works.find(w => w.id === recWorkId)?.title}
              voices={voices}
              audio={audio}
              onUseVoice={setVoice}
            />
          </View>
        )}
        <VoiceBrowserCard
          voices={voices}
          selectedId={voice}
          onSelect={setVoice}
          audio={audio}
        />
      </View>

      <View style={styles.field}>
        <FieldLabel>Speed — {speed.toFixed(1)}×</FieldLabel>
        <PillPicker
          options={[0.5, 0.75, 1.0, 1.25, 1.5, 2.0]}
          value={speed}
          onChange={setSpeed}
          render={(s) => `${s.toFixed(2).replace(/0$/, '').replace(/\.$/, '')}×`}
        />
      </View>

      {/* TTS engine badge */}
      <PremiumEngineBadge />

      {/* Synthesize / Stop button */}
      <Pressable
        onPress={tts.isActive ? tts.stop : () => { if (text.trim() && !overLimit) tts.startStream(text.trim(), voice, speed); }}
        disabled={(!text.trim() || overLimit) && !tts.isActive}
        style={({ pressed }) => [
          styles.primaryButton,
          {
            backgroundColor: tts.isActive ? '#ef4444' : colors.primary,
            opacity: (!text.trim() || overLimit) && !tts.isActive ? 0.5 : pressed ? 0.85 : 1,
          },
        ]}
      >
        {tts.phase === 'loading' ? (
          <ActivityIndicator color="#fff" size="small" />
        ) : tts.isActive ? (
          <Feather name="square" size={14} color="#fff" />
        ) : (
          <Feather name="mic" size={15} color={colors.primaryForeground} />
        )}
        <Text style={[styles.primaryButtonText, { color: tts.isActive ? '#fff' : colors.primaryForeground }]}>
          {tts.phase === 'loading' ? 'Preparing…' : tts.isActive ? 'Stop' : 'Synthesize'}
        </Text>
      </Pressable>

      {/* Streaming progress */}
      {(tts.phase === 'loading' || tts.phase === 'playing' || tts.phase === 'done') && (
        <View style={ttsStreamStyles.container}>
          {/* Progress track */}
          <View style={[ttsStreamStyles.track, { backgroundColor: colors.muted }]}>
            <View
              style={[
                ttsStreamStyles.fill,
                {
                  backgroundColor: tts.phase === 'done' ? '#22c55e' : colors.primary,
                  width: tts.phase === 'loading' ? '3%'
                       : tts.phase === 'done'    ? '100%'
                       : `${Math.max(3, progressPct)}%`,
                },
              ]}
            />
          </View>

          {/* Status label */}
          <View style={ttsStreamStyles.statusRow}>
            {tts.phase === 'loading' && (
              <>
                <ActivityIndicator size="small" color={colors.primary}
                  style={{ transform: [{ scale: 0.75 }] }} />
                <Text style={[ttsStreamStyles.statusText, { color: colors.mutedForeground }]}>
                  Preparing audio…
                </Text>
              </>
            )}
            {tts.phase === 'playing' && (
              <>
                <Feather name="volume-2" size={13} color={colors.primary} />
                <Text style={[ttsStreamStyles.statusText, { color: colors.foreground }]}>
                  {tts.segTotal > 0
                    ? `Playing segment ${tts.segCurrent} of ${tts.segTotal}`
                    : `Playing segment ${tts.segCurrent}`}
                </Text>
              </>
            )}
            {tts.phase === 'done' && (
              <>
                <Feather name="check-circle" size={13} color="#22c55e" />
                <Text style={[ttsStreamStyles.statusText, { color: '#22c55e' }]}>
                  Done{tts.segTotal > 1 ? ` — ${tts.segTotal} segments` : ''}
                </Text>
                <Pressable
                  onPress={tts.stop}
                  hitSlop={10}
                  style={{ marginLeft: 'auto' }}
                >
                  <Feather name="x" size={13} color={colors.mutedForeground} />
                </Pressable>
              </>
            )}
          </View>
        </View>
      )}

      {/* Error state */}
      {tts.phase === 'error' && !!tts.errorMsg && (
        <View style={[ttsStreamStyles.errorBox, { borderColor: '#ef444433', backgroundColor: '#ef444410' }]}>
          <Feather name="alert-circle" size={13} color="#ef4444" />
          <Text style={[ttsStreamStyles.errorText, { color: '#ef4444' }]} numberOfLines={3}>
            {tts.errorMsg}
          </Text>
        </View>
      )}
    </SectionCard>
  );
}

const ttsStreamStyles = StyleSheet.create({
  container: { gap: 8 },
  track:     { height: 4, borderRadius: 2, overflow: 'hidden' },
  fill:      { height: 4, borderRadius: 2 },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  statusText: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  errorBox: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    padding: 10, borderRadius: 8, borderWidth: 1,
  },
  errorText: { fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 },
});

// ── Image generation panel ───────────────────────────────────────────────────────

function ImagePanel({ onGenerated }: { onGenerated: () => void }) {
  const colors = useColors();
  const [prompt, setPrompt] = useState('');
  const [negPrompt, setNegPrompt] = useState('');
  const [size, setSize] = useState(512);
  const [loading, setLoading] = useState(false);
  const [resultUri, setResultUri] = useState<string | null>(null);
  const [status, setStatus] = useState<{ any_online: boolean; backends: any[] } | null>(null);

  // ── Backend settings modal ───────────────────────────────────────────────────
  const [settingsVisible, setSettingsVisible] = useState(false);
  const [urlInput, setUrlInput] = useState('');
  const [loadingUrl, setLoadingUrl] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadStatus = async () => {
    try {
      const r = await mobileFetch(`${API}/studio/image-status`);
      if (r.ok) setStatus(await r.json());
    } catch {}
  };

  useEffect(() => {
    loadStatus();
    const t = setInterval(loadStatus, 30_000);
    return () => clearInterval(t);
  }, []);

  const openSettings = async () => {
    setSettingsVisible(true);
    setLoadingUrl(true);
    try {
      const r = await mobileFetch(`${API}/system/settings/image-gen`);
      if (r.ok) {
        const d = await r.json();
        setUrlInput(d.url ?? '');
      }
    } catch {}
    setLoadingUrl(false);
  };

  const saveSettings = async () => {
    setSaving(true);
    try {
      const r = await mobileFetch(`${API}/system/settings/image-gen`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: urlInput.trim() }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setSettingsVisible(false);
      // Re-probe immediately so the pill updates
      loadStatus();
    } catch (e: any) {
      Alert.alert('Could not save', e?.message ?? 'Settings save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleGenerate = async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    setResultUri(null);
    try {
      const resp = await mobileFetch(`${API}/studio/image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: prompt.trim(),
          negative_prompt: negPrompt.trim(),
          width: size,
          height: size,
          steps: 20,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({} as any));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const item = data?.data?.[0];
      const url = item?.url ?? (item?.b64_json ? `data:image/png;base64,${item.b64_json}` : null);
      if (!url) throw new Error('No image in response');
      setResultUri(url);
      onGenerated();
    } catch (e: any) {
      Alert.alert('Image generation failed', e?.message ?? 'Generation failed');
    } finally {
      setLoading(false);
    }
  };

  const anyOnline = status?.any_online ?? false;

  return (
    <>
      {/* ── Backend settings modal ─────────────────────────────────────────────── */}
      <Modal
        visible={settingsVisible}
        animationType="slide"
        transparent
        onRequestClose={() => setSettingsVisible(false)}
      >
        <Pressable
          style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)', justifyContent: 'flex-end' }}
          onPress={() => setSettingsVisible(false)}
        >
          <Pressable
            onPress={() => {/* swallow taps inside the sheet */}}
            style={[imgSettingsStyles.sheet, { backgroundColor: colors.card, borderColor: colors.border }]}
          >
            <View style={imgSettingsStyles.handle} />

            <Text style={[imgSettingsStyles.title, { color: colors.foreground }]}>
              Image Backend Settings
            </Text>
            <Text style={[imgSettingsStyles.subtitle, { color: colors.mutedForeground }]}>
              Enter a custom URL (Automatic1111, ComfyUI, or compatible). Leave blank to auto-detect.
            </Text>

            {/* Custom URL input */}
            <View style={{ marginTop: 16 }}>
              <Text style={[imgSettingsStyles.label, { color: colors.mutedForeground }]}>Custom backend URL</Text>
              {loadingUrl ? (
                <ActivityIndicator color={colors.primary} style={{ marginVertical: 12 }} />
              ) : (
                <TextInput
                  style={[imgSettingsStyles.input, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.background }]}
                  placeholder="http://192.168.1.x:7860"
                  placeholderTextColor={colors.mutedForeground}
                  value={urlInput}
                  onChangeText={setUrlInput}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="url"
                />
              )}
            </View>

            {/* Backend status list */}
            {(status?.backends ?? []).length > 0 && (
              <View style={{ marginTop: 16, gap: 6 }}>
                <Text style={[imgSettingsStyles.label, { color: colors.mutedForeground }]}>Detected backends</Text>
                {(status?.backends ?? []).map((b: any, i: number) => (
                  <View key={i} style={[imgSettingsStyles.backendRow, { borderColor: colors.border }]}>
                    <View style={{
                      width: 7, height: 7, borderRadius: 4,
                      backgroundColor: b.online ? '#22c55e' : colors.mutedForeground,
                    }} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }}>
                        {b.name}
                      </Text>
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }} numberOfLines={1}>
                        {b.url}
                      </Text>
                    </View>
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: b.online ? '#22c55e' : colors.mutedForeground }}>
                      {b.online ? 'Online' : 'Offline'}
                    </Text>
                  </View>
                ))}
              </View>
            )}

            {/* Actions */}
            <View style={{ flexDirection: 'row', gap: 10, marginTop: 20 }}>
              <Pressable
                onPress={() => { setUrlInput(''); }}
                style={[imgSettingsStyles.btnSecondary, { borderColor: colors.border }]}
              >
                <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>Clear</Text>
              </Pressable>
              <Pressable
                onPress={saveSettings}
                disabled={saving}
                style={({ pressed }) => [
                  imgSettingsStyles.btnPrimary,
                  { backgroundColor: colors.primary, opacity: saving ? 0.6 : pressed ? 0.85 : 1, flex: 1 },
                ]}
              >
                {saving
                  ? <ActivityIndicator size="small" color="#fff" />
                  : <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.primaryForeground }}>Save</Text>
                }
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      <SectionCard
        title="Image Generation"
        icon="image"
        right={
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <View style={[styles.statusPill, { borderColor: anyOnline ? '#22c55e55' : colors.border, backgroundColor: anyOnline ? '#22c55e18' : 'transparent' }]}>
              <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: anyOnline ? '#22c55e' : colors.mutedForeground }} />
              <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: anyOnline ? '#22c55e' : colors.mutedForeground }}>
                {anyOnline ? 'Backend online' : 'No backend'}
              </Text>
            </View>
            <Pressable onPress={openSettings} hitSlop={8} style={styles.iconBtn}>
              <Feather name="settings" size={14} color={colors.mutedForeground} />
            </Pressable>
          </View>
        }
      >
        <View style={styles.field}>
          <FieldLabel>Prompt</FieldLabel>
          <TextInput
            style={[styles.textArea, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.background, minHeight: 70 }]}
            placeholder="Describe the image to generate…"
            placeholderTextColor={colors.mutedForeground}
            value={prompt}
            onChangeText={setPrompt}
            multiline
          />
        </View>

        <View style={styles.field}>
          <FieldLabel>Negative prompt (optional)</FieldLabel>
          <TextInput
            style={[styles.textArea, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.background, minHeight: 44 }]}
            placeholder="What to avoid…"
            placeholderTextColor={colors.mutedForeground}
            value={negPrompt}
            onChangeText={setNegPrompt}
            multiline
          />
        </View>

        <View style={styles.field}>
          <FieldLabel>Size</FieldLabel>
          <PillPicker options={SIZES} value={size} onChange={setSize} render={(s) => `${s}px`} />
        </View>

        <Pressable
          onPress={handleGenerate}
          disabled={!prompt.trim() || loading}
          style={({ pressed }) => [
            styles.primaryButton,
            { backgroundColor: colors.primary, opacity: !prompt.trim() || loading ? 0.5 : pressed ? 0.85 : 1 },
          ]}
        >
          {loading ? (
            <ActivityIndicator color={colors.primaryForeground} size="small" />
          ) : (
            <Feather name="image" size={15} color={colors.primaryForeground} />
          )}
          <Text style={[styles.primaryButtonText, { color: colors.primaryForeground }]}>
            {loading ? 'Generating…' : 'Generate Image'}
          </Text>
        </Pressable>

        {resultUri && (
          <View style={[styles.imageResult, { borderColor: colors.border }]}>
            <Image source={authSource(resultUri)} style={styles.resultImage} contentFit="contain" />
            <SavePhotoButton uri={resultUri} />
          </View>
        )}
      </SectionCard>
    </>
  );
}

const imgSettingsStyles = StyleSheet.create({
  sheet: {
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    paddingHorizontal: 20,
    paddingBottom: 36,
    paddingTop: 12,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#88888844',
    alignSelf: 'center',
    marginBottom: 16,
  },
  title: {
    fontSize: 17,
    fontFamily: 'Inter_600SemiBold',
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 12,
    fontFamily: 'Inter_400Regular',
    lineHeight: 18,
  },
  label: {
    fontSize: 11,
    fontFamily: 'Inter_500Medium',
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: 6,
  },
  input: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    fontFamily: 'Inter_400Regular',
  },
  backendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderRadius: 8,
  },
  btnSecondary: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 16,
    paddingVertical: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnPrimary: {
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
});

// ── Recent outputs ────────────────────────────────────────────────────────────────

function OutputsPanel({
  outputs,
  loading,
  onRefresh,
  audio,
}: {
  outputs: any[];
  loading: boolean;
  onRefresh: () => void;
  audio: ReturnType<typeof useSharedAudio>;
}) {
  const colors = useColors();

  const handleDelete = (out: any) => {
    Alert.alert('Delete output', `Remove "${out.name}"?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          if (audio.playingKey === out.path) audio.stop();
          try {
            const r = await mobileFetch(`${API}/studio/outputs/archive?path=${encodeURIComponent(out.path)}`, {
              method: 'DELETE',
            });
            if (!r.ok) throw new Error();
            onRefresh();
          } catch {
            Alert.alert('Error', 'Could not remove output');
          }
        },
      },
    ]);
  };

  return (
    <SectionCard title="Recent Outputs" icon="clock">
      {loading && outputs.length === 0 ? (
        <ActivityIndicator color={colors.primary} style={{ marginVertical: 20 }} />
      ) : outputs.length === 0 ? (
        <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
          No outputs yet — synthesize speech or generate an image above.
        </Text>
      ) : (
        <View style={{ gap: 8 }}>
          {outputs.map((out) => {
            const isImage = out.kind === 'image';
            const isAudio = out.kind === 'audio';
            const isPlaying = audio.playingKey === out.path;
            return (
              <View key={out.path} style={[styles.outputRow, { borderColor: colors.border, backgroundColor: colors.background }]}>
                {isImage ? (
                  <Image source={authSource(serveUrl(out.path))} style={styles.thumb} contentFit="cover" />
                ) : (
                  <View style={[styles.thumb, styles.thumbIcon, { backgroundColor: colors.muted }]}>
                    <Feather name={isAudio ? 'music' : 'file'} size={16} color={colors.primary} />
                  </View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={{ color: colors.foreground, fontSize: 13, fontFamily: 'Inter_500Medium' }} numberOfLines={1}>
                    {out.name}
                  </Text>
                  <Text style={{ color: colors.mutedForeground, fontSize: 11, fontFamily: 'Inter_400Regular' }}>
                    {out.kind} · {out.size_bytes >= 1_048_576 ? `${(out.size_bytes / 1_048_576).toFixed(1)} MB` : `${Math.round(out.size_bytes / 1024)} KB`}
                  </Text>
                </View>
                {isAudio && (
                  <Pressable onPress={() => audio.toggle(out.path, serveUrl(out.path))} hitSlop={8} style={styles.iconBtn}>
                    <Feather name={isPlaying ? 'pause' : 'play'} size={16} color={colors.primary} />
                  </Pressable>
                )}
                {isAudio && <ShareAudioButton uri={serveUrl(out.path)} name={out.name} compact />}
                {isImage && <SavePhotoButton uri={serveUrl(out.path)} name={out.name} compact />}
                <Pressable onPress={() => handleDelete(out)} hitSlop={8} style={styles.iconBtn}>
                  <Feather name="trash-2" size={16} color={colors.destructive} />
                </Pressable>
              </View>
            );
          })}
        </View>
      )}
    </SectionCard>
  );
}

// ── Document Workshop ─────────────────────────────────────────────────────────────

/**
 * Normalize the workshop critique to a readable string.
 * The API returns a structured dict:
 *   { verdict: str, scores: {}, suggestions: [], gaps: [], strengths: [] }
 * or null on failure, or a raw string in some fallback paths.
 */
function normalizeCritique(c: unknown): string {
  if (!c) return '';
  if (typeof c === 'string') return c.trim();
  if (typeof c !== 'object' || Array.isArray(c)) return String(c);
  const o = c as Record<string, unknown>;
  const parts: string[] = [];
  if (o.verdict)                                         parts.push(String(o.verdict));
  if (Array.isArray(o.strengths)   && o.strengths.length)
    parts.push('Strengths:\n'  + (o.strengths   as string[]).map(s => `• ${s}`).join('\n'));
  if (Array.isArray(o.suggestions) && o.suggestions.length)
    parts.push('Suggestions:\n'+ (o.suggestions as string[]).map(s => `• ${s}`).join('\n'));
  if (Array.isArray(o.gaps)        && o.gaps.length)
    parts.push('Gaps:\n'       + (o.gaps        as string[]).map(s => `• ${s}`).join('\n'));
  if (o.scores && typeof o.scores === 'object' && !Array.isArray(o.scores)) {
    const sc = Object.entries(o.scores as Record<string, unknown>).map(([k, v]) => `${k}: ${v}`).join(' · ');
    if (sc) parts.push(`Scores: ${sc}`);
  }
  return parts.join('\n\n').trim();
}

type WorkshopPhase = 'idle' | 'planning' | 'clarify' | 'generating' | 'done' | 'error';

const PROGRESS_PHASES: { label: string; icon: string }[] = [
  { label: 'Planning structure…',   icon: 'layers' },
  { label: 'Drafting content…',     icon: 'edit-3' },
  { label: 'Reviewing quality…',    icon: 'check-circle' },
  { label: 'Finalising document…',  icon: 'package' },
];

function WorkshopPanel() {
  const colors = useColors();
  const [goal, setGoal] = useState('');
  const [workId, setWorkId] = useState<string | null>(null);
  const [works, setWorks] = useState<{ id: string; title: string }[]>([]);
  const [phase, setPhase] = useState<WorkshopPhase>('idle');
  const [questions, setQuestions] = useState<{ id: string; question: string }[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [detectedIntent, setDetectedIntent] = useState('');
  const [progressIdx, setProgressIdx] = useState(0);
  const [critique, setCritique] = useState('');      // normalized human-readable string
  const [resultDocId, setResultDocId] = useState<string | null>(null);
  const [resultFilename, setResultFilename] = useState('');
  const [downloadUrl, setDownloadUrl] = useState(''); // relative path for API download
  const [errorMsg, setErrorMsg] = useState('');
  const progressTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load works list for the context picker
  useEffect(() => {
    mobileFetch(`${API}/works`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.works) {
          setWorks(d.works.map((w: any) => ({ id: w.id, title: w.title ?? 'Untitled' })));
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => () => {
    if (progressTimer.current) clearInterval(progressTimer.current);
  }, []);

  const startProgress = () => {
    setProgressIdx(0);
    progressTimer.current = setInterval(() => {
      setProgressIdx(i => Math.min(i + 1, PROGRESS_PHASES.length - 1));
    }, 5_000);
  };

  const stopProgress = () => {
    if (progressTimer.current) { clearInterval(progressTimer.current); progressTimer.current = null; }
  };

  const doExecute = async (sid: string | null, ans: Record<string, string>) => {
    setPhase('generating');
    startProgress();
    try {
      const r = await mobileFetch(`${API}/generate/workshop/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sid,
          request: goal.trim(),
          format: 'docx',
          work_id: workId,
          answers: ans,
        }),
      });
      stopProgress();
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data?.detail ?? `HTTP ${r.status}`);
      // critique is a structured dict from the server — normalize to readable string
      setCritique(normalizeCritique(data.critique ?? data.summary ?? null));
      setResultDocId(data.doc_id ?? null);
      setResultFilename(data.filename ?? 'document.docx');
      // Relative download path, e.g. "/api/generate/download?path=outputs/..."
      setDownloadUrl(data.download_url ?? '');
      setPhase('done');
    } catch (e: any) {
      stopProgress();
      setErrorMsg(e?.message ?? 'Generation failed');
      setPhase('error');
    }
  };

  const handlePlan = async () => {
    if (!goal.trim()) return;
    setPhase('planning');
    setErrorMsg('');
    try {
      const r = await mobileFetch(`${API}/generate/workshop/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request: goal.trim(), work_id: workId }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data?.detail ?? `HTTP ${r.status}`);
      // Plan response shape: { id: string, questions: [...], detected_format, detected_intent }
      const sid = data.id ?? data.session_id ?? null;
      setSessionId(sid);
      setDetectedIntent(data.detected_intent ?? '');
      const qs: { id: string; question: string }[] = (data.questions ?? []).map(
        (q: any, i: number) => ({
          id: q.id ?? String(i),
          question: typeof q === 'string' ? q : (q.question ?? q.text ?? String(q)),
        }),
      );
      if (qs.length > 0) {
        setQuestions(qs);
        setAnswers({});
        setPhase('clarify');
      } else {
        // No clarifying questions — execute immediately
        await doExecute(sid, {});
      }
    } catch (e: any) {
      setErrorMsg(e?.message ?? 'Planning failed');
      setPhase('error');
    }
  };

  const handleGenerate = () => doExecute(sessionId, answers);

  const handleReset = () => {
    setPhase('idle');
    setGoal('');
    setWorkId(null);
    setQuestions([]);
    setAnswers({});
    setSessionId(null);
    setCritique('');
    setResultDocId(null);
    setResultFilename('');
    setDownloadUrl('');
    setErrorMsg('');
    setProgressIdx(0);
  };

  const handleCopy = async () => {
    // Copy the critique text — the human-readable AI review of the generated document
    const text = critique || `Document generated: ${resultFilename}`;
    try {
      await Clipboard.setStringAsync(text);
      Alert.alert('Copied', 'AI review copied to clipboard.');
    } catch {
      Alert.alert('Copy failed', 'Could not access clipboard.');
    }
  };

  const handleShare = async () => {
    // Build a share payload that includes the critique + download link so the
    // recipient can open the actual document file from a browser.
    // downloadUrl is already API-relative: "/api/generate/download?path=..."
    const fileLink = downloadUrl ? `https://${DOMAIN}${downloadUrl}` : null;
    const body = [
      critique || 'Document generated successfully.',
      fileLink ? `\nDocument file: ${fileLink}` : '',
    ].join('').trim();
    try {
      await Share.share({
        message: body,
        title: resultFilename || 'Generated Document',
        url: fileLink ?? undefined,   // iOS share sheet uses url for AirDrop / Files
      });
    } catch {}
  };

  // ── Done ──────────────────────────────────────────────────────────────────────
  if (phase === 'done') {
    return (
      <SectionCard title="Document Ready" icon="file-text">
        <View style={{ gap: 12 }}>

          {/* Success banner — filename + Library reference */}
          <View style={[wsStyles.banner, { borderColor: '#22c55e44', backgroundColor: '#22c55e0a' }]}>
            <Feather name="check-circle" size={15} color="#22c55e" />
            <View style={{ flex: 1, gap: 3 }}>
              <Text style={{ color: '#22c55e', fontSize: 14, fontFamily: 'Inter_600SemiBold' }}>
                {resultFilename || 'document.docx'}
              </Text>
              {resultDocId ? (
                <Text style={{ color: '#22c55e99', fontSize: 11, fontFamily: 'Inter_400Regular' }}>
                  Saved to Library · ID {resultDocId}
                </Text>
              ) : (
                <Text style={{ color: '#22c55e99', fontSize: 11, fontFamily: 'Inter_400Regular' }}>
                  Generated successfully
                </Text>
              )}
            </View>
          </View>

          {/* AI critique — always show the box, show placeholder when empty */}
          <View style={{ gap: 6 }}>
            <FieldLabel>AI Review</FieldLabel>
            <ScrollView
              style={[wsStyles.critiqueBox, { borderColor: colors.border, backgroundColor: colors.muted + '40' }]}
              nestedScrollEnabled
              showsVerticalScrollIndicator={false}
            >
              <Text style={{
                color: critique ? colors.foreground : colors.mutedForeground,
                fontSize: 13,
                fontFamily: 'Inter_400Regular',
                lineHeight: 19,
                padding: 12,
              }}>
                {critique || 'No AI review available for this document.'}
              </Text>
            </ScrollView>
          </View>

          {/* Action buttons */}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              onPress={handleCopy}
              style={({ pressed }) => [wsStyles.actionBtn, { borderColor: colors.border, flex: 1, opacity: pressed ? 0.7 : 1 }]}
            >
              <Feather name="copy" size={14} color={colors.primary} />
              <Text style={{ color: colors.primary, fontSize: 13, fontFamily: 'Inter_500Medium' }}>Copy review</Text>
            </Pressable>
            <Pressable
              onPress={handleShare}
              style={({ pressed }) => [wsStyles.actionBtn, { borderColor: colors.border, flex: 1, opacity: pressed ? 0.7 : 1 }]}
            >
              <Feather name="share-2" size={14} color={colors.primary} />
              <Text style={{ color: colors.primary, fontSize: 13, fontFamily: 'Inter_500Medium' }}>
                {downloadUrl ? 'Share & link' : 'Share'}
              </Text>
            </Pressable>
          </View>

          {/* Download URL hint — so users know the file is accessible */}
          {!!downloadUrl && (
            <Text style={{ color: colors.mutedForeground, fontSize: 11, fontFamily: 'Inter_400Regular', lineHeight: 16 }}>
              File accessible from the web interface, or tap Share to send the download link.
            </Text>
          )}

          <Pressable
            onPress={handleReset}
            style={({ pressed }) => [styles.primaryButton, { backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1 }]}
          >
            <Feather name="refresh-cw" size={15} color={colors.foreground} />
            <Text style={[styles.primaryButtonText, { color: colors.foreground }]}>New document</Text>
          </Pressable>
        </View>
      </SectionCard>
    );
  }

  // ── Generating ────────────────────────────────────────────────────────────────
  if (phase === 'generating') {
    return (
      <SectionCard title="Generating Document" icon="zap">
        <View style={{ gap: 14, paddingVertical: 6 }}>
          {PROGRESS_PHASES.map((p, i) => {
            const done   = i < progressIdx;
            const active = i === progressIdx;
            return (
              <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <View style={[wsStyles.phaseIcon, {
                  backgroundColor: done ? '#22c55e22' : active ? colors.primary + '22' : colors.muted,
                  borderColor:     done ? '#22c55e55' : active ? colors.primary + '55' : colors.border,
                }]}>
                  {done ? (
                    <Feather name="check" size={11} color="#22c55e" />
                  ) : active ? (
                    <ActivityIndicator size="small" color={colors.primary} style={{ transform: [{ scale: 0.6 }] }} />
                  ) : (
                    <Feather name={p.icon as any} size={11} color={colors.mutedForeground} />
                  )}
                </View>
                <Text style={{
                  fontSize: 13, lineHeight: 18,
                  fontFamily: active ? 'Inter_500Medium' : 'Inter_400Regular',
                  color: done ? '#22c55e' : active ? colors.foreground : colors.mutedForeground,
                }}>
                  {p.label}
                </Text>
              </View>
            );
          })}
          <Text style={{ color: colors.mutedForeground, fontSize: 11, fontFamily: 'Inter_400Regular', textAlign: 'center', marginTop: 6 }}>
            Usually takes 30–90 seconds…
          </Text>
        </View>
      </SectionCard>
    );
  }

  // ── Clarify ───────────────────────────────────────────────────────────────────
  if (phase === 'clarify') {
    return (
      <SectionCard title="A few quick questions" icon="help-circle">
        <View style={{ gap: 14 }}>
          {!!detectedIntent && (
            <View style={[wsStyles.intentBadge, { borderColor: colors.primary + '44', backgroundColor: colors.primary + '0a' }]}>
              <Feather name="star" size={12} color={colors.primary} />
              <Text style={{ color: colors.primary, fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 }}>
                {detectedIntent}
              </Text>
            </View>
          )}

          {questions.map((q, i) => (
            <View key={q.id} style={{ gap: 6 }}>
              <Text style={{ color: colors.foreground, fontSize: 13, fontFamily: 'Inter_500Medium', lineHeight: 18 }}>
                {i + 1}. {q.question}
              </Text>
              <TextInput
                style={[wsStyles.answerInput, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.background }]}
                placeholder="Your answer (or leave blank to skip)…"
                placeholderTextColor={colors.mutedForeground}
                value={answers[q.id] ?? ''}
                onChangeText={(t) => setAnswers(prev => ({ ...prev, [q.id]: t }))}
                multiline
              />
            </View>
          ))}

          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable onPress={handleGenerate}
              style={({ pressed }) => [styles.primaryButton, { backgroundColor: colors.primary, flex: 1, opacity: pressed ? 0.85 : 1 }]}>
              <Feather name="zap" size={15} color={colors.primaryForeground} />
              <Text style={[styles.primaryButtonText, { color: colors.primaryForeground }]}>Generate</Text>
            </Pressable>
            <Pressable onPress={() => setPhase('idle')}
              style={[styles.primaryButton, { backgroundColor: colors.muted, paddingHorizontal: 20 }]}>
              <Text style={[styles.primaryButtonText, { color: colors.foreground }]}>Back</Text>
            </Pressable>
          </View>
        </View>
      </SectionCard>
    );
  }

  // ── Planning spinner ──────────────────────────────────────────────────────────
  if (phase === 'planning') {
    return (
      <SectionCard title="Document Workshop" icon="edit-3">
        <View style={{ alignItems: 'center', paddingVertical: 28, gap: 14 }}>
          <ActivityIndicator color={colors.primary} />
          <Text style={{ color: colors.mutedForeground, fontSize: 13, fontFamily: 'Inter_400Regular' }}>
            Planning your document…
          </Text>
        </View>
      </SectionCard>
    );
  }

  // ── Error ─────────────────────────────────────────────────────────────────────
  if (phase === 'error') {
    return (
      <SectionCard title="Document Workshop" icon="edit-3">
        <View style={{ gap: 12 }}>
          <View style={[wsStyles.intentBadge, { borderColor: '#ef444444', backgroundColor: '#ef444410' }]}>
            <Feather name="alert-circle" size={12} color="#ef4444" />
            <Text style={{ color: '#ef4444', fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 }}>
              {errorMsg}
            </Text>
          </View>
          <Pressable onPress={handleReset}
            style={({ pressed }) => [styles.primaryButton, { backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1 }]}>
            <Text style={[styles.primaryButtonText, { color: colors.foreground }]}>Try again</Text>
          </Pressable>
        </View>
      </SectionCard>
    );
  }

  // ── Idle — goal input ─────────────────────────────────────────────────────────
  return (
    <SectionCard title="Document Workshop" icon="edit-3">
      <Text style={{ color: colors.mutedForeground, fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 }}>
        Describe what you want to write. The AI will plan, draft, and self-review it.
      </Text>

      <View style={styles.field}>
        <FieldLabel>What do you want to write?</FieldLabel>
        <TextInput
          style={[styles.textArea, {
            color: colors.foreground,
            borderColor: colors.border,
            backgroundColor: colors.background,
            minHeight: 100,
          }]}
          placeholder={'e.g. A chapter outline for a thriller novel set in 1920s Berlin…'}
          placeholderTextColor={colors.mutedForeground}
          value={goal}
          onChangeText={setGoal}
          multiline
        />
      </View>

      {works.length > 0 && (
        <View style={styles.field}>
          <FieldLabel>Work context (optional)</FieldLabel>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ flexDirection: 'row', gap: 6 }}
          >
            {[{ id: null as null, title: 'No context' }, ...works].map(w => {
              const active = w.id === workId;
              return (
                <Pressable
                  key={w.id ?? '__none'}
                  onPress={() => setWorkId(w.id)}
                  style={{
                    paddingHorizontal: 12, paddingVertical: 7,
                    borderRadius: 8, borderWidth: 1,
                    borderColor: active ? colors.primary : colors.border,
                    backgroundColor: active ? colors.primary + '22' : 'transparent',
                  }}
                >
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: active ? colors.primary : colors.mutedForeground }}
                    numberOfLines={1}>
                    {w.title}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      )}

      <Pressable
        onPress={handlePlan}
        disabled={!goal.trim()}
        style={({ pressed }) => [styles.primaryButton, {
          backgroundColor: colors.primary,
          opacity: !goal.trim() ? 0.45 : pressed ? 0.85 : 1,
        }]}
      >
        <Feather name="edit-3" size={15} color={colors.primaryForeground} />
        <Text style={[styles.primaryButtonText, { color: colors.primaryForeground }]}>Plan &amp; Generate</Text>
      </Pressable>
    </SectionCard>
  );
}

// ── Audiobook Builder ─────────────────────────────────────────────────────────

type AudiobookPhase = 'idle' | 'generating' | 'done' | 'error';

function _safeTitle(title: string) {
  return title.replace(/[^\w\-]/g, '_').substring(0, 50);
}

function AudiobookPanel({
  voices,
  onGenerated,
  audio,
  outputs,
}: {
  voices: VoiceEntry[];
  onGenerated: () => void;
  audio: ReturnType<typeof useSharedAudio>;
  outputs: any[];
}) {
  const colors = useColors();
  const [works, setWorks] = useState<{ id: string; title: string }[]>([]);
  const [workId, setWorkId] = useState<string | null>(null);
  const [voice, setVoice] = useState('bm_george');
  const [speed, setSpeed] = useState(1.0);
  const [phase, setPhase] = useState<AudiobookPhase>('idle');
  const [result, setResult] = useState<{ path: string; filename: string; work_title: string } | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [sharing, setSharing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load works list
  useEffect(() => {
    mobileFetch(`${API}/works`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.works) {
          const list: { id: string; title: string }[] = d.works.map((w: any) => ({
            id: w.id,
            title: w.title ?? 'Untitled',
          }));
          setWorks(list);
          if (list.length && !workId) setWorkId(list[0].id);
        }
      })
      .catch(() => {});
  }, []);

  // Cleanup on unmount
  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current);
    abortRef.current?.abort();
  }, []);

  const selectedWork = works.find(w => w.id === workId) ?? null;

  // Previous audiobooks for the selected work — audio outputs whose filename
  // begins with the server-generated safe title prefix.
  const previousOutputs = selectedWork
    ? outputs.filter(o => o.kind === 'audio' && o.name.startsWith(_safeTitle(selectedWork.title)))
    : [];

  const startTimer = () => {
    setElapsed(0);
    timerRef.current = setInterval(() => setElapsed(s => s + 1), 1000);
  };

  const stopTimer = () => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  };

  const formatElapsed = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}m ${sec}s` : `${s}s`;
  };

  const handleGenerate = async () => {
    if (!workId) return;
    audio.stop();
    setPhase('generating');
    setResult(null);
    setErrorMsg('');
    startTimer();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const resp = await mobileFetch(`${API}/studio/tts/work`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          work_id: workId,
          voice,
          speed,
          include_credits: true,
          acx_mastering: true,
          return_url: true,
        }),
        signal: ctrl.signal,
      });
      stopTimer();
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setResult({ path: data.path, filename: data.filename, work_title: data.work_title });
      setPhase('done');
      onGenerated();
    } catch (e: any) {
      stopTimer();
      if (e?.name === 'AbortError') {
        setPhase('idle');
        return;
      }
      setErrorMsg(e?.message ?? 'Audiobook generation failed');
      setPhase('error');
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    stopTimer();
    setPhase('idle');
  };

  const handleShare = async () => {
    if (!result || sharing) return;
    setSharing(true);
    try {
      const FileSystem = await import('expo-file-system/legacy');
      const Sharing = await import('expo-sharing');
      const token = getApiToken();
      const uri = serveUrl(result.path);
      const dest = `${FileSystem.cacheDirectory}${result.filename}`;
      const dl = await FileSystem.downloadAsync(uri, dest, {
        headers: token ? { authorization: `Bearer ${token}` } : undefined,
      });
      if (dl.status !== 200) throw new Error(`Download failed (HTTP ${dl.status})`);
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(dl.uri, {
          mimeType: 'audio/mpeg',
          dialogTitle: result.filename,
          UTI: 'public.mp3',
        });
      } else {
        Alert.alert('Share unavailable', 'Sharing is not supported on this platform.');
      }
    } catch (e: any) {
      Alert.alert('Share failed', e?.message ?? 'Could not share file');
    } finally {
      setSharing(false);
    }
  };

  const handleReset = () => {
    setPhase('idle');
    setResult(null);
    setErrorMsg('');
    setElapsed(0);
    audio.stop();
  };

  // ── Generating ───────────────────────────────────────────────────────────────
  if (phase === 'generating') {
    const STEPS = [
      'Fetching document text from all chapters',
      'Synthesizing narration segments',
      'Concatenating + ACX loudness mastering',
    ];
    // Each step takes roughly 20 s of elapsed time as a heuristic marker
    return (
      <SectionCard title="Generating Audiobook" icon="headphones">
        <View style={{ gap: 16, paddingVertical: 6 }}>
          {/* Waveform visual */}
          <View style={{ flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'center', gap: 4, height: 44 }}>
            {[14, 30, 38, 26, 18, 36, 22, 32, 16, 28].map((h, i) => (
              <View
                key={i}
                style={{
                  width: 5,
                  height: h,
                  borderRadius: 3,
                  backgroundColor: i % 2 === 0 ? colors.primary : colors.primary + 'aa',
                }}
              />
            ))}
          </View>

          <View style={{ gap: 4, alignItems: 'center' }}>
            <Text style={{ color: colors.foreground, fontSize: 15, fontFamily: 'Inter_600SemiBold' }}>
              Synthesizing narration…
            </Text>
            <Text style={{ color: colors.mutedForeground, fontSize: 12, fontFamily: 'Inter_400Regular' }}>
              Elapsed: {formatElapsed(elapsed)} · Large works take several minutes
            </Text>
          </View>

          {STEPS.map((step, i) => {
            const done   = elapsed >= (i + 1) * 20;
            const active = !done && elapsed >= i * 20;
            return (
              <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <View style={[wsStyles.phaseIcon, {
                  backgroundColor: done   ? '#22c55e22' : active ? colors.primary + '22' : colors.muted,
                  borderColor:     done   ? '#22c55e55' : active ? colors.primary + '55' : colors.border,
                }]}>
                  {done
                    ? <Feather name="check" size={11} color="#22c55e" />
                    : active
                    ? <ActivityIndicator size="small" color={colors.primary} style={{ transform: [{ scale: 0.6 }] }} />
                    : <Feather name="clock" size={11} color={colors.mutedForeground} />
                  }
                </View>
                <Text style={{
                  fontSize: 13, lineHeight: 18,
                  fontFamily: active ? 'Inter_500Medium' : 'Inter_400Regular',
                  color: done ? '#22c55e' : active ? colors.foreground : colors.mutedForeground,
                }}>
                  {step}
                </Text>
              </View>
            );
          })}

          <Pressable
            onPress={handleCancel}
            style={({ pressed }) => [styles.primaryButton, {
              backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1, marginTop: 4,
            }]}
          >
            <Feather name="x" size={15} color={colors.foreground} />
            <Text style={[styles.primaryButtonText, { color: colors.foreground }]}>Cancel</Text>
          </Pressable>
        </View>
      </SectionCard>
    );
  }

  // ── Done ─────────────────────────────────────────────────────────────────────
  if (phase === 'done' && result) {
    const playKey  = `audiobook-${result.path}`;
    const isPlaying = audio.playingKey === playKey;
    const playUri  = serveUrl(result.path);
    return (
      <SectionCard title="Audiobook Ready" icon="headphones">
        <View style={{ gap: 12 }}>
          {/* Success banner */}
          <View style={[wsStyles.banner, { borderColor: '#22c55e44', backgroundColor: '#22c55e0a' }]}>
            <Feather name="check-circle" size={15} color="#22c55e" />
            <View style={{ flex: 1, gap: 3 }}>
              <Text style={{ color: '#22c55e', fontSize: 14, fontFamily: 'Inter_600SemiBold' }}>
                {result.work_title}
              </Text>
              <Text style={{ color: '#22c55e99', fontSize: 11, fontFamily: 'Inter_400Regular' }}>
                {result.filename} · ACX-mastered MP3
              </Text>
            </View>
          </View>

          {/* Playback row */}
          <Pressable
            onPress={() => audio.toggle(playKey, playUri)}
            style={[styles.playRow, {
              borderColor: isPlaying ? colors.primary : colors.border,
              backgroundColor: isPlaying ? colors.primary + '10' : colors.muted,
            }]}
          >
            <View style={{
              width: 36, height: 36, borderRadius: 18,
              backgroundColor: isPlaying ? colors.primary : colors.muted,
              alignItems: 'center', justifyContent: 'center',
            }}>
              <Feather name={isPlaying ? 'pause' : 'play'} size={16}
                color={isPlaying ? colors.primaryForeground : colors.mutedForeground} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }}>
                {isPlaying ? 'Playing…' : 'Play audiobook'}
              </Text>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                Full narrated MP3
              </Text>
            </View>
            {isPlaying && (
              <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 2 }}>
                {[12, 18, 14, 20, 10].map((h, i) => (
                  <View key={i} style={{
                    width: 3, height: h, borderRadius: 2, backgroundColor: colors.primary,
                  }} />
                ))}
              </View>
            )}
          </Pressable>

          {/* Actions */}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              onPress={handleShare}
              disabled={sharing}
              style={({ pressed }) => [wsStyles.actionBtn, {
                borderColor: colors.border, flex: 1, opacity: sharing || pressed ? 0.7 : 1,
              }]}
            >
              {sharing
                ? <ActivityIndicator size="small" color={colors.primary} />
                : <Feather name="share-2" size={14} color={colors.primary} />
              }
              <Text style={{ color: colors.primary, fontSize: 13, fontFamily: 'Inter_500Medium' }}>
                {sharing ? 'Preparing…' : 'Save / Share'}
              </Text>
            </Pressable>
            <Pressable
              onPress={handleReset}
              style={({ pressed }) => [wsStyles.actionBtn, {
                borderColor: colors.border, flex: 1, opacity: pressed ? 0.7 : 1,
              }]}
            >
              <Feather name="refresh-cw" size={14} color={colors.mutedForeground} />
              <Text style={{ color: colors.mutedForeground, fontSize: 13, fontFamily: 'Inter_500Medium' }}>
                New build
              </Text>
            </Pressable>
          </View>
        </View>
      </SectionCard>
    );
  }

  // ── Error ─────────────────────────────────────────────────────────────────────
  if (phase === 'error') {
    return (
      <SectionCard title="Audiobook Builder" icon="headphones">
        <View style={{ gap: 12 }}>
          <View style={[wsStyles.intentBadge, { borderColor: '#ef444444', backgroundColor: '#ef444410' }]}>
            <Feather name="alert-circle" size={12} color="#ef4444" />
            <Text style={{ color: '#ef4444', fontSize: 12, fontFamily: 'Inter_400Regular', flex: 1, lineHeight: 17 }}>
              {errorMsg}
            </Text>
          </View>
          <Pressable
            onPress={handleReset}
            style={({ pressed }) => [styles.primaryButton, {
              backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1,
            }]}
          >
            <Text style={[styles.primaryButtonText, { color: colors.foreground }]}>Try again</Text>
          </Pressable>
        </View>
      </SectionCard>
    );
  }

  // ── Idle ─────────────────────────────────────────────────────────────────────
  return (
    <SectionCard title="Audiobook Builder" icon="headphones">
      <Text style={{ color: colors.mutedForeground, fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 17 }}>
        Generate a full ACX-mastered audiobook MP3 from all ready documents in a Work.
      </Text>

      {/* Work selector */}
      <View style={styles.field}>
        <FieldLabel>Work</FieldLabel>
        {works.length === 0 ? (
          <Text style={{ color: colors.mutedForeground, fontSize: 12, fontFamily: 'Inter_400Regular' }}>
            No Works found — create one and add documents first.
          </Text>
        ) : (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ flexDirection: 'row', gap: 6 }}
          >
            {works.map(w => {
              const active = w.id === workId;
              return (
                <Pressable
                  key={w.id}
                  onPress={() => setWorkId(w.id)}
                  style={{
                    paddingHorizontal: 12, paddingVertical: 7,
                    borderRadius: 8, borderWidth: 1,
                    borderColor: active ? colors.primary : colors.border,
                    backgroundColor: active ? colors.primary + '22' : 'transparent',
                    maxWidth: 160,
                  }}
                >
                  <Text
                    numberOfLines={1}
                    style={{
                      fontSize: 12,
                      fontFamily: 'Inter_500Medium',
                      color: active ? colors.primary : colors.mutedForeground,
                    }}
                  >
                    {w.title}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        )}
      </View>

      {/* Voice selector */}
      <View style={styles.field}>
        <FieldLabel>Narrator voice</FieldLabel>
        {/* AI narrator recommendations — auto-populates from the selected Work */}
        <VoiceRecommenderCard
          workId={workId}
          workTitle={selectedWork?.title}
          voices={voices}
          audio={audio}
          onUseVoice={setVoice}
        />
        <VoiceBrowserCard
          voices={voices}
          selectedId={voice}
          onSelect={setVoice}
          audio={audio}
        />
      </View>

      {/* Speed */}
      <View style={styles.field}>
        <FieldLabel>Narration speed — {speed.toFixed(2).replace(/0$/, '').replace(/\.$/, '')}×</FieldLabel>
        <PillPicker
          options={[0.75, 1.0, 1.1, 1.25]}
          value={speed}
          onChange={setSpeed}
          render={(s) => `${s.toFixed(2).replace(/0$/, '').replace(/\.$/, '')}×`}
        />
      </View>

      {/* Previous builds for this work */}
      {previousOutputs.length > 0 && (
        <View style={[abStyles.prevBox, { borderColor: colors.border, backgroundColor: colors.muted + '40' }]}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <Feather name="clock" size={12} color={colors.mutedForeground} />
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Previous builds
            </Text>
          </View>
          {previousOutputs.slice(0, 3).map(out => {
            const prevKey  = `ab-prev-${out.path}`;
            const isPlaying = audio.playingKey === prevKey;
            return (
              <View key={out.path} style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <Pressable
                  onPress={() => audio.toggle(prevKey, serveUrl(out.path))}
                  hitSlop={6}
                  style={[abStyles.prevPlayBtn, {
                    backgroundColor: isPlaying ? colors.primary : colors.muted,
                  }]}
                >
                  <Feather
                    name={isPlaying ? 'pause' : 'play'}
                    size={11}
                    color={isPlaying ? colors.primaryForeground : colors.mutedForeground}
                  />
                </Pressable>
                <Text
                  numberOfLines={1}
                  style={{ flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}
                >
                  {out.name}
                </Text>
                <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                  {out.size_bytes >= 1_048_576
                    ? `${(out.size_bytes / 1_048_576).toFixed(1)} MB`
                    : `${Math.round(out.size_bytes / 1024)} KB`}
                </Text>
              </View>
            );
          })}
        </View>
      )}

      {/* Generate button */}
      <Pressable
        onPress={handleGenerate}
        disabled={!workId || works.length === 0}
        style={({ pressed }) => [styles.primaryButton, {
          backgroundColor: colors.primary,
          opacity: !workId || works.length === 0 ? 0.45 : pressed ? 0.85 : 1,
        }]}
      >
        <Feather name="headphones" size={15} color={colors.primaryForeground} />
        <Text style={[styles.primaryButtonText, { color: colors.primaryForeground }]}>
          Generate Audiobook
        </Text>
      </Pressable>

      <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center', lineHeight: 16 }}>
        Includes opening &amp; closing credits · ACX-mastered · Keep app open during generation
      </Text>
    </SectionCard>
  );
}

const abStyles = StyleSheet.create({
  prevBox: { borderRadius: 8, borderWidth: 1, padding: 10, gap: 8 },
  prevPlayBtn: {
    width: 24, height: 24, borderRadius: 12,
    alignItems: 'center', justifyContent: 'center',
  },
});

const wsStyles = StyleSheet.create({
  banner: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    borderRadius: 8, borderWidth: 1, padding: 12,
  },
  critiqueBox: { borderRadius: 8, borderWidth: 1, maxHeight: 200 },
  actionBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 11, borderRadius: 8, borderWidth: 1,
  },
  phaseIcon: {
    width: 26, height: 26, borderRadius: 13, borderWidth: 1,
    alignItems: 'center', justifyContent: 'center',
  },
  intentBadge: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 8,
    borderRadius: 8, borderWidth: 1, padding: 10,
  },
  answerInput: {
    borderWidth: 1, borderRadius: 8, padding: 10,
    fontSize: 13, fontFamily: 'Inter_400Regular',
    minHeight: 52, textAlignVertical: 'top',
  },
  tabBar: {
    flexDirection: 'row', gap: 6, marginTop: 12,
  },
  tabPill: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 5, paddingVertical: 9, borderRadius: 8, borderWidth: 1,
  },
});

// ── Screen ──────────────────────────────────────────────────────────────────────

type StudioTab = 'voice' | 'image' | 'workshop' | 'audiobook';

const STUDIO_TABS: { id: StudioTab; label: string; icon: string }[] = [
  { id: 'voice',     label: 'Voice',     icon: 'volume-2'   },
  { id: 'audiobook', label: 'Audiobook', icon: 'headphones' },
  { id: 'image',     label: 'Image',     icon: 'image'      },
  { id: 'workshop',  label: 'Workshop',  icon: 'edit-3'     },
];

export default function StudioScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const audio = useSharedAudio();

  const [tab, setTab] = useState<StudioTab>('voice');
  const [voices, setVoices] = useState<VoiceEntry[]>(FALLBACK_VOICES);
  const [outputs, setOutputs] = useState<any[]>([]);
  const [loadingOutputs, setLoadingOutputs] = useState(true);

  const loadVoices = async () => {
    try {
      const r = await mobileFetch(`${API}/studio/voices`);
      if (r.ok) {
        const data = await r.json();
        const list: VoiceEntry[] = (data.voices ?? [])
          .filter((v: any) => v.id && v.name)
          .map((v: any) => ({
            id:          v.id,
            name:        v.name,
            accent:      v.accent,
            gender:      v.gender,
            description: v.description,
            tags:        Array.isArray(v.tags) ? v.tags : [],
            builtin:     v.builtin ?? false,
          }));
        if (list.length) setVoices(list);
      }
    } catch {
      // Keep fallback voices.
    }
  };

  const loadOutputs = async () => {
    try {
      const r = await mobileFetch(`${API}/studio/outputs`);
      if (r.ok) {
        const data = await r.json();
        setOutputs(data.outputs ?? []);
      }
    } catch {
      // Leave existing.
    } finally {
      setLoadingOutputs(false);
    }
  };

  useEffect(() => {
    loadVoices();
    loadOutputs();
    const t = setInterval(loadOutputs, 15_000);
    return () => clearInterval(t);
  }, []);

  const topPad = isWeb ? 67 : insets.top;

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Stack.Screen options={{ title: 'Studio', headerShown: true }} />
      <ScrollView
        contentContainerStyle={{
          paddingTop: topPad + 8,
          paddingBottom: (isWeb ? 34 : insets.bottom) + 40,
          paddingHorizontal: 16,
          gap: 16,
        }}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
        {/* Header + tab bar */}
        <View>
          <Text style={[styles.title, { color: colors.foreground }]}>Studio</Text>
          <Text style={[styles.subtitle, { color: colors.mutedForeground }]}>
            Voice · Audiobook · Image · Workshop
          </Text>
          <View style={wsStyles.tabBar}>
            {STUDIO_TABS.map(t => {
              const active = t.id === tab;
              return (
                <Pressable
                  key={t.id}
                  onPress={() => setTab(t.id)}
                  style={[wsStyles.tabPill, {
                    backgroundColor: active ? colors.primary : 'transparent',
                    borderColor: active ? colors.primary : colors.border,
                  }]}
                >
                  <Feather name={t.icon as any} size={13}
                    color={active ? colors.primaryForeground : colors.mutedForeground} />
                  <Text style={{
                    fontSize: 13, fontFamily: 'Inter_500Medium',
                    color: active ? colors.primaryForeground : colors.mutedForeground,
                  }}>
                    {t.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </View>

        {/* Tab content */}
        {tab === 'voice' && (
          <>
            <TTSPanel voices={voices} onGenerated={loadOutputs} audio={audio} />
            <OutputsPanel outputs={outputs.filter(o => o.kind === 'audio')}
              loading={loadingOutputs} onRefresh={loadOutputs} audio={audio} />
          </>
        )}
        {tab === 'image' && (
          <>
            <ImagePanel onGenerated={loadOutputs} />
            <OutputsPanel outputs={outputs.filter(o => o.kind === 'image')}
              loading={loadingOutputs} onRefresh={loadOutputs} audio={audio} />
          </>
        )}
        {tab === 'audiobook' && (
          <>
            <AudiobookPanel
              voices={voices}
              onGenerated={loadOutputs}
              audio={audio}
              outputs={outputs}
            />
            <OutputsPanel
              outputs={outputs.filter(o => o.kind === 'audio')}
              loading={loadingOutputs}
              onRefresh={loadOutputs}
              audio={audio}
            />
          </>
        )}
        {tab === 'workshop' && (
          <WorkshopPanel />
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 28, fontFamily: 'Inter_700Bold', letterSpacing: -0.5 },
  subtitle: { fontSize: 13, fontFamily: 'Inter_400Regular', marginTop: 2 },
  card: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 16,
    gap: 14,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cardHeaderLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  cardTitle: { fontSize: 16, fontFamily: 'Inter_600SemiBold' },
  field: { gap: 8 },
  fieldLabel: {
    fontSize: 11,
    fontFamily: 'Inter_600SemiBold',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  textArea: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    fontSize: 14,
    fontFamily: 'Inter_400Regular',
    minHeight: 90,
    textAlignVertical: 'top',
  },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 13,
    borderRadius: 8,
  },
  primaryButtonText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
  playRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 12,
    borderRadius: 8,
    borderWidth: 1,
  },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    borderWidth: 1,
  },
  imageResult: { borderRadius: 8, borderWidth: 1, overflow: 'hidden' },
  resultImage: { width: '100%', height: 280, backgroundColor: '#00000010' },
  saveButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    margin: 8,
    paddingVertical: 9,
    borderRadius: 8,
    borderWidth: 1,
  },
  saveButtonText: { fontSize: 13, fontFamily: 'Inter_500Medium' },
  emptyText: { fontSize: 13, fontFamily: 'Inter_400Regular', textAlign: 'center', paddingVertical: 20, lineHeight: 19 },
  outputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
  },
  thumb: { width: 44, height: 44, borderRadius: 6 },
  thumbIcon: { alignItems: 'center', justifyContent: 'center' },
  iconBtn: { padding: 6 },
});
