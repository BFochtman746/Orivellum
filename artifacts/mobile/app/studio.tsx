import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
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

              {/* Sample preview button */}
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
                <Feather
                  name={isPlaying ? 'pause' : 'play'}
                  size={11}
                  color={isPlaying ? colors.primaryForeground : colors.mutedForeground}
                />
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
  };

  const toggle = (key: string, uri: string) => {
    if (playingKey === key) {
      stop();
      return;
    }
    // Switch source
    if (pollRef.current) clearInterval(pollRef.current);
    try {
      playerRef.current?.remove();
    } catch {}
    try {
      const player = createAudioPlayer(authSource(uri));
      playerRef.current = player;
      player.play();
      setPlayingKey(key);
      // Poll for end-of-track since there is no simple onEnded callback here.
      pollRef.current = setInterval(() => {
        const st = player.currentStatus;
        if (st?.didJustFinish || (st?.isLoaded && !st.playing && st.currentTime > 0 && st.duration > 0 && st.currentTime >= st.duration - 0.25)) {
          stop();
        }
        if (st?.error) {
          Alert.alert('Playback error', st.error);
          stop();
        }
      }, 600);
    } catch (e: any) {
      Alert.alert('Playback failed', e?.message ?? 'Could not play audio');
      stop();
    }
  };

  return { playingKey, toggle, stop };
}

// ── TTS panel ───────────────────────────────────────────────────────────────────

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
  const [loading, setLoading] = useState(false);
  const [resultUri, setResultUri] = useState<string | null>(null);
  const overLimit = text.length > 10_000;

  const handleSynthesize = async () => {
    if (!text.trim() || overLimit) return;
    setLoading(true);
    audio.stop();
    setResultUri(null);
    try {
      const resp = await mobileFetch(`${API}/studio/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.trim(), voice, speed }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({} as any));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      // The endpoint returns a saved output; refresh the list and play the newest.
      await new Promise((r) => setTimeout(r, 300));
      onGenerated();
      // Fetch newest audio output path for direct playback.
      const listResp = await mobileFetch(`${API}/studio/outputs`);
      const list = await listResp.json().catch(() => ({ outputs: [] }));
      const newestAudio = (list.outputs ?? []).find((o: any) => o.kind === 'audio');
      if (newestAudio) setResultUri(serveUrl(newestAudio.path));
    } catch (e: any) {
      Alert.alert('TTS failed', e?.message ?? 'Synthesis failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SectionCard title="Text to Speech" icon="volume-2">
      <View style={styles.field}>
        <View style={styles.rowBetween}>
          <FieldLabel>Text</FieldLabel>
          <Text style={{ fontSize: 11, color: overLimit ? colors.destructive : colors.mutedForeground, fontFamily: 'Inter_400Regular' }}>
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
        />
      </View>

      <View style={styles.field}>
        <FieldLabel>Voice</FieldLabel>
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

      <Pressable
        onPress={handleSynthesize}
        disabled={!text.trim() || loading || overLimit}
        style={({ pressed }) => [
          styles.primaryButton,
          { backgroundColor: colors.primary, opacity: !text.trim() || loading || overLimit ? 0.5 : pressed ? 0.85 : 1 },
        ]}
      >
        {loading ? (
          <ActivityIndicator color={colors.primaryForeground} size="small" />
        ) : (
          <Feather name="mic" size={15} color={colors.primaryForeground} />
        )}
        <Text style={[styles.primaryButtonText, { color: colors.primaryForeground }]}>
          {loading ? 'Synthesizing…' : 'Synthesize'}
        </Text>
      </Pressable>

      {resultUri && (
        <Pressable
          onPress={() => audio.toggle('tts-result', resultUri)}
          style={[styles.playRow, { borderColor: colors.border, backgroundColor: colors.muted }]}
        >
          <Feather name={audio.playingKey === 'tts-result' ? 'pause' : 'play'} size={16} color={colors.primary} />
          <Text style={{ color: colors.foreground, fontSize: 13, fontFamily: 'Inter_500Medium' }}>
            {audio.playingKey === 'tts-result' ? 'Playing…' : 'Play result'}
          </Text>
        </Pressable>
      )}
    </SectionCard>
  );
}

// ── Image generation panel ───────────────────────────────────────────────────────

function ImagePanel({ onGenerated }: { onGenerated: () => void }) {
  const colors = useColors();
  const [prompt, setPrompt] = useState('');
  const [negPrompt, setNegPrompt] = useState('');
  const [size, setSize] = useState(512);
  const [loading, setLoading] = useState(false);
  const [resultUri, setResultUri] = useState<string | null>(null);
  const [status, setStatus] = useState<{ any_online: boolean; backends: any[] } | null>(null);

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
    <SectionCard
      title="Image Generation"
      icon="image"
      right={
        <View style={[styles.statusPill, { borderColor: anyOnline ? '#22c55e55' : colors.border, backgroundColor: anyOnline ? '#22c55e18' : 'transparent' }]}>
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: anyOnline ? '#22c55e' : colors.mutedForeground }} />
          <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: anyOnline ? '#22c55e' : colors.mutedForeground }}>
            {anyOnline ? 'Backend online' : 'No backend'}
          </Text>
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
  );
}

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

type StudioTab = 'voice' | 'image' | 'workshop';

const STUDIO_TABS: { id: StudioTab; label: string; icon: string }[] = [
  { id: 'voice',    label: 'Voice',    icon: 'volume-2' },
  { id: 'image',    label: 'Image',    icon: 'image' },
  { id: 'workshop', label: 'Workshop', icon: 'edit-3' },
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
            Voice · Image · Workshop
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
