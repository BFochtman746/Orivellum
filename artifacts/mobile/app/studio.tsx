import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
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

// Fallback voices — mirror the built-in voices exposed by studio.py.
const FALLBACK_VOICES = [
  { id: 'af_heart', name: 'Heart (AF)' },
  { id: 'af_bella', name: 'Bella (AF)' },
  { id: 'am_adam', name: 'Adam (AM)' },
  { id: 'bf_emma', name: 'Emma (BF)' },
  { id: 'bm_george', name: 'George (BM)' },
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
  voices: { id: string; name: string }[];
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
        <PillPicker
          options={voices}
          value={voices.find((v) => v.id === voice) ?? voices[0]}
          onChange={(v) => setVoice(v.id)}
          render={(v) => v.name}
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

// ── Screen ──────────────────────────────────────────────────────────────────────

export default function StudioScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const audio = useSharedAudio();

  const [voices, setVoices] = useState<{ id: string; name: string }[]>(FALLBACK_VOICES);
  const [outputs, setOutputs] = useState<any[]>([]);
  const [loadingOutputs, setLoadingOutputs] = useState(true);

  const loadVoices = async () => {
    try {
      const r = await mobileFetch(`${API}/studio/voices`);
      if (r.ok) {
        const data = await r.json();
        const list = (data.voices ?? [])
          .filter((v: any) => v.id && v.name)
          .map((v: any) => ({ id: v.id, name: v.name }));
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
        <View>
          <Text style={[styles.title, { color: colors.foreground }]}>Studio</Text>
          <Text style={[styles.subtitle, { color: colors.mutedForeground }]}>
            Text-to-speech &amp; image generation
          </Text>
        </View>

        <TTSPanel voices={voices} onGenerated={loadOutputs} audio={audio} />
        <ImagePanel onGenerated={loadOutputs} />
        <OutputsPanel outputs={outputs} loading={loadingOutputs} onRefresh={loadOutputs} audio={audio} />
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
