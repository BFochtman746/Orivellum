/**
 * System Settings & Health — /system
 *
 * Mobile equivalent of the web System page. Covers:
 *  - Server health + DB stats
 *  - AI extraction / re-ranking toggles
 *  - Embeddings probe
 *  - Nightshift status + manual run
 *  - App version
 */
import React, { useState, useEffect, useRef } from 'react';
import { mobileFetch } from '@/lib/api';
import {
  Animated,
  ActivityIndicator,
  Alert,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useVellumTokens, alpha } from '@/lib/tokens';
import { font, fontSerif } from '@/lib/typography';
import { SkeletonItem } from '@/components/SkeletonItem';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

// ── API helpers ───────────────────────────────────────────────────────────────

async function fetchJson(path: string) {
  const r = await mobileFetch(`${API}${path}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function postJson(path: string, body?: object) {
  const r = await mobileFetch(`${API}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Stat pill ─────────────────────────────────────────────────────────────────

function StatPill({ label, value, color }: { label: string; value: string | number; color: string }) {
  const colors = useColors();
  return (
    <View style={[s.statPill, { backgroundColor: alpha(color, 0.10), borderColor: alpha(color, 0.27) }]}>
      <Text style={[s.statValue, { color }]}>{value}</Text>
      <Text style={[s.statLabel, { color: colors.mutedForeground }]}>{label}</Text>
    </View>
  );
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) {
  const colors = useColors();
  return (
    <View style={[s.section, { borderColor: colors.border, backgroundColor: colors.card }]}>
      <View style={s.sectionHead}>
        <Feather name={icon as any} size={14} color={colors.primary} />
        <Text style={[s.sectionTitle, { color: colors.mutedForeground }]}>{title.toUpperCase()}</Text>
      </View>
      {children}
    </View>
  );
}

// ── Pulse dot ─────────────────────────────────────────────────────────────────

function PulseDot({ color, size = 10 }: { color: string; size?: number }) {
  const scale = useRef(new Animated.Value(1)).current;
  const opacity = useRef(new Animated.Value(0.7)).current;

  useEffect(() => {
    const pulse = Animated.loop(
      Animated.sequence([
        Animated.parallel([
          Animated.timing(scale,   { toValue: 1.6, duration: 900, useNativeDriver: true }),
          Animated.timing(opacity, { toValue: 0,   duration: 900, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.timing(scale,   { toValue: 1,   duration: 0,   useNativeDriver: true }),
          Animated.timing(opacity, { toValue: 0.7, duration: 0,   useNativeDriver: true }),
        ]),
      ])
    );
    pulse.start();
    return () => pulse.stop();
  }, []);

  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      {/* Pulse ring */}
      <Animated.View
        style={{
          position: 'absolute',
          width: size, height: size, borderRadius: size / 2,
          backgroundColor: color,
          transform: [{ scale }],
          opacity,
        }}
      />
      {/* Solid dot */}
      <View style={{ width: size * 0.65, height: size * 0.65, borderRadius: size, backgroundColor: color }} />
    </View>
  );
}

// ── Profile section ───────────────────────────────────────────────────────────

const COMM_STYLE_OPTS = [
  { value: '',          label: 'Default' },
  { value: 'casual',    label: 'Casual' },
  { value: 'direct',    label: 'Direct' },
  { value: 'socratic',  label: 'Socratic' },
  { value: 'formal',    label: 'Formal' },
  { value: 'technical', label: 'Technical' },
];

function ProfileSection() {
  const colors = useColors();
  const [name,    setName]    = useState('');
  const [bio,     setBio]     = useState('');
  const [style,   setStyle]   = useState('');
  const [saving,  setSaving]  = useState(false);
  const [loaded,  setLoaded]  = useState(false);

  useEffect(() => {
    mobileFetch(`${API}/system/profile`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        setName(d.user_name ?? '');
        setBio(d.user_bio ?? '');
        setStyle(d.communication_style ?? '');
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await mobileFetch(`${API}/system/profile`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_name: name, user_bio: bio, communication_style: style }),
      });
      Alert.alert('Saved', 'Your profile has been updated.');
    } catch {
      Alert.alert('Error', 'Could not save profile.');
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) return null;

  return (
    <Section title="Your Profile" icon="user">
      <Text style={[s.metaText, { color: colors.mutedForeground }]}>
        Your name and bio are injected into every AI prompt so responses feel personal.
      </Text>

      <Text style={[profileS.label, { color: colors.mutedForeground }]}>Name</Text>
      <TextInput
        value={name}
        onChangeText={setName}
        placeholder="e.g. Brian"
        placeholderTextColor={colors.mutedForeground}
        maxLength={120}
        style={[profileS.input, { borderColor: colors.border, color: colors.foreground, backgroundColor: colors.background }]}
      />

      <Text style={[profileS.label, { color: colors.mutedForeground }]}>About you</Text>
      <TextInput
        value={bio}
        onChangeText={setBio}
        placeholder="e.g. Author working on a sci-fi trilogy"
        placeholderTextColor={colors.mutedForeground}
        maxLength={240}
        multiline
        numberOfLines={2}
        style={[profileS.input, profileS.textarea, { borderColor: colors.border, color: colors.foreground, backgroundColor: colors.background }]}
      />
      <Text style={[profileS.counter, { color: colors.mutedForeground }]}>{bio.length}/240</Text>

      <Text style={[profileS.label, { color: colors.mutedForeground }]}>Communication style</Text>
      <View style={profileS.pillRow}>
        {COMM_STYLE_OPTS.map(opt => (
          <Pressable
            key={opt.value}
            onPress={() => setStyle(opt.value)}
            style={[
              profileS.pill,
              { borderColor: style === opt.value ? colors.primary : colors.border },
              style === opt.value && { backgroundColor: alpha(colors.primary, 0.10) },
            ]}
          >
            <Text style={[profileS.pillText, { color: style === opt.value ? colors.primary : colors.mutedForeground }]}>
              {opt.label}
            </Text>
          </Pressable>
        ))}
      </View>

      <Pressable
        onPress={save}
        disabled={saving}
        style={({ pressed }) => [s.actionBtn, { borderColor: colors.primary, backgroundColor: colors.primary, opacity: saving || pressed ? 0.7 : 1 }]}
      >
        {saving
          ? <ActivityIndicator size="small" color="#fff" />
          : <Feather name="save" size={14} color="#fff" />}
        <Text style={[s.actionBtnText, { color: '#fff' }]}>Save profile</Text>
      </Pressable>
    </Section>
  );
}

const profileS = StyleSheet.create({
  label:    { fontSize: 11, ...font('semibold'), letterSpacing: 0.5, marginBottom: 4, marginTop: 10, textTransform: 'uppercase' },
  input:    { borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, fontSize: 13, ...font('regular') },
  textarea: { minHeight: 52, textAlignVertical: 'top' },
  counter:  { fontSize: 10, ...font('regular'), marginTop: 2, textAlign: 'right' },
  pillRow:  { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 4 },
  pill:     { borderWidth: 1, borderRadius: 20, paddingHorizontal: 12, paddingVertical: 8, minHeight: 36, justifyContent: 'center' },
  pillText: { fontSize: 12, ...font('regular') },
});

// ── Toggle row ────────────────────────────────────────────────────────────────

function ToggleRow({
  label,
  description,
  value,
  onToggle,
  loading,
  disabled,
}: {
  label: string;
  description: string;
  value: boolean;
  onToggle: (v: boolean) => void;
  loading?: boolean;
  disabled?: boolean;
}) {
  const colors = useColors();
  return (
    <View style={[s.toggleRow, { borderTopColor: colors.border, opacity: disabled ? 0.5 : 1 }]}>
      <View style={{ flex: 1, marginRight: 12 }}>
        <Text style={[s.toggleLabel, { color: colors.foreground }]}>{label}</Text>
        <Text style={[s.toggleDesc, { color: colors.mutedForeground }]} numberOfLines={3}>
          {description}
        </Text>
      </View>
      {loading ? (
        <ActivityIndicator size="small" color={colors.primary} />
      ) : (
        <Switch
          value={value}
          onValueChange={disabled ? undefined : onToggle}
          disabled={disabled}
          trackColor={{ false: colors.border, true: colors.primary + '88' }}
          thumbColor={value ? colors.primary : colors.mutedForeground}
        />
      )}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function SystemScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const qc = useQueryClient();
  const isWeb = Platform.OS === 'web';

  const [refreshing, setRefreshing] = useState(false);
  const [probeResult, setProbeResult] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [nightshiftRunning, setNightshiftRunning] = useState(false);

  // ── Queries ──────────────────────────────────────────────────────────────────

  const { data: health, isLoading: healthLoading, refetch: refetchHealth } = useQuery({
    queryKey: ['sys-health'],
    queryFn: () => fetchJson('/system/health'),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  const { data: aiExtraction, isLoading: aiExLoading, refetch: refetchAiEx } = useQuery<{ enabled: boolean }>({
    queryKey: ['sys-ai-extraction'],
    queryFn: () => fetchJson('/system/settings/ai-extraction'),
    staleTime: 30_000,
  });

  const { data: aiReranking, isLoading: aiRrLoading, refetch: refetchAiRr } = useQuery<{ enabled: boolean }>({
    queryKey: ['sys-ai-reranking'],
    queryFn: () => fetchJson('/system/settings/ai-reranking'),
    staleTime: 30_000,
  });

  const { data: audioEnhance, isLoading: audioEnhLoading, refetch: refetchAudioEnh } = useQuery<{
    enabled: boolean;
    installed: boolean;
    model: string;
    install_hint: string | null;
  }>({
    queryKey: ['sys-audio-enhance'],
    queryFn: () => fetchJson('/system/settings/audio-enhance'),
    staleTime: 30_000,
  });

  const { data: embStatus } = useQuery({
    queryKey: ['sys-emb-status'],
    queryFn: () => fetchJson('/system/embeddings/status'),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const { data: nightshiftStatus, refetch: refetchNightshift } = useQuery({
    queryKey: ['sys-nightshift'],
    queryFn: () => fetchJson('/system/nightshift/status'),
    staleTime: 5_000,
    refetchInterval: (q) => (q.state.data?.running ? 3_000 : 30_000),
  });

  const { data: version } = useQuery({
    queryKey: ['sys-version'],
    queryFn: () => fetchJson('/version'),
    staleTime: Infinity,
  });

  const { data: dedupStats, refetch: refetchDedup } = useQuery({
    queryKey: ['sys-dedup'],
    queryFn: () => fetchJson('/library/duplicates?limit=1'),
    staleTime: 60_000,
  });

  // ── Mutations ─────────────────────────────────────────────────────────────────

  const [aiExToggling, setAiExToggling] = useState(false);
  const toggleAiExtraction = async (val: boolean) => {
    setAiExToggling(true);
    try {
      await postJson('/system/settings/ai-extraction', { enabled: val });
      await refetchAiEx();
    } catch {
      Alert.alert('Error', 'Could not update AI extraction setting');
    } finally {
      setAiExToggling(false);
    }
  };

  const [aiRrToggling, setAiRrToggling] = useState(false);
  const toggleAiReranking = async (val: boolean) => {
    setAiRrToggling(true);
    try {
      await postJson('/system/settings/ai-reranking', { enabled: val });
      await refetchAiRr();
    } catch {
      Alert.alert('Error', 'Could not update AI re-ranking setting');
    } finally {
      setAiRrToggling(false);
    }
  };

  const [audioEnhToggling, setAudioEnhToggling] = useState(false);
  const toggleAudioEnhancement = async (val: boolean) => {
    setAudioEnhToggling(true);
    try {
      const r = await mobileFetch(`${API}/system/settings/audio-enhance`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: val }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetchAudioEnh();
    } catch {
      Alert.alert('Error', 'Could not update audio enhancement setting');
    } finally {
      setAudioEnhToggling(false);
    }
  };

  const handleProbeEmbeddings = async () => {
    setProbing(true);
    setProbeResult(null);
    try {
      const res = await mobileFetch(`${API}/system/embeddings/probe`, { method: 'POST' });
      const data = await res.json();
      setProbeResult(res.ok ? `✓ ${data.message ?? 'Embeddings working'}` : `✗ ${data.detail ?? 'Probe failed'}`);
    } catch (e: any) {
      setProbeResult(`✗ ${e?.message ?? 'Network error'}`);
    } finally {
      setProbing(false);
    }
  };

  const handleRunNightshift = async () => {
    setNightshiftRunning(true);
    try {
      await postJson('/system/nightshift/run-now');
      setTimeout(() => {
        refetchNightshift();
        setNightshiftRunning(false);
      }, 1500);
    } catch {
      Alert.alert('Error', 'Could not start nightshift');
      setNightshiftRunning(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all([
        refetchHealth(), refetchAiEx(), refetchAiRr(), refetchAudioEnh(),
        refetchNightshift(), refetchDedup(),
      ]);
    } finally {
      setRefreshing(false);
    }
  };

  // ── Derived state ──────────────────────────────────────────────────────────────

  const topPad = isWeb ? 67 : insets.top;
  const overallOk = (health as any)?.status === 'ok';
  const dbStats = (health as any)?.database ?? {};

  const embState: string = (embStatus as any)?.state ?? 'unknown';
  const embOk = embState === 'closed'; // circuit-breaker "closed" = healthy
  // Embeddings: ok → green, loading/half-open → gilt, open/error → rust
  const embColor = embOk ? T.green : embState === 'open' ? T.rust : T.gilt;

  return (
    <View style={[s.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View style={[s.header, { paddingTop: topPad + 8, borderBottomColor: colors.border, backgroundColor: colors.background }]}>
        <Pressable
          onPress={() => router.back()}
          style={s.backRow}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
        >
          <Feather name="arrow-left" size={18} color={colors.primary} />
          <Text style={[s.backLabel, { color: colors.primary }]}>Back</Text>
        </Pressable>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Feather name="settings" size={20} color={colors.foreground} />
          <Text style={[s.title, { color: colors.foreground }]}>System</Text>
        </View>
        <Text style={[s.subtitle, { color: colors.mutedForeground }]}>
          Infrastructure health and AI settings
        </Text>
      </View>

      <ScrollView
        contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 16, paddingBottom: insets.bottom + 24 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={colors.primary} />}
        showsVerticalScrollIndicator={false}
      >
        {/* Health card */}
        <Section title="Server Health" icon="activity">
          {healthLoading ? (
            <>
              <SkeletonItem lines={1} icon={false} />
              <SkeletonItem lines={1} icon={false} />
            </>
          ) : (
            <>
              <View style={[s.healthBadge, {
                backgroundColor: alpha(overallOk ? T.green : T.rust, 0.10),
                borderColor: alpha(overallOk ? T.green : T.rust, 0.28),
              }]}>
                <PulseDot color={overallOk ? T.green : T.rust} size={10} />
                <Text style={[s.healthBadgeText, { color: overallOk ? T.green : T.rust }]}>
                  {overallOk ? 'All systems operational' : 'Degraded — check server logs'}
                </Text>
              </View>
              <View style={s.pillRow}>
                <StatPill label="Works" value={dbStats.works ?? '—'} color={colors.primary} />
                <StatPill label="Docs" value={dbStats.documents ?? '—'} color={colors.mutedForeground} />
                <StatPill label="Knowledge" value={dbStats.knowledge ?? '—'} color={colors.mutedForeground} />
                {dbStats.size_mb != null && (
                  <StatPill label="DB" value={`${dbStats.size_mb} MB`} color={colors.mutedForeground} />
                )}
              </View>
              {version && (
                <Text style={[s.versionText, { color: colors.mutedForeground }]}>
                  v{(version as any).version ?? '—'} · {(version as any).build_date ?? ''}
                </Text>
              )}
            </>
          )}
        </Section>

        {/* AI settings */}
        <Section title="AI Settings" icon="zap">
          <ToggleRow
            label="AI Knowledge Extraction"
            description="After import, use local AI to extract entities, claims, and relationships."
            value={!!(aiExtraction as any)?.enabled}
            onToggle={toggleAiExtraction}
            loading={aiExLoading || aiExToggling}
          />
          <ToggleRow
            label="AI Search Re-ranking"
            description="Re-rank top search results using local AI for higher relevance."
            value={!!(aiReranking as any)?.enabled}
            onToggle={toggleAiReranking}
            loading={aiRrLoading || aiRrToggling}
          />
          <ToggleRow
            label="Audio Enhancement (DeepFilterNet3)"
            description="Denoise audio before Whisper transcription — removes background noise, reverb, and crosstalk. Runs on CPU at ~0.2× real-time, no GPU needed."
            value={!!audioEnhance?.enabled}
            onToggle={toggleAudioEnhancement}
            loading={audioEnhLoading || audioEnhToggling}
            disabled={audioEnhance !== undefined && !audioEnhance.installed && !audioEnhance.enabled}
          />
          {/* Installed badge + status / install hint */}
          {audioEnhance !== undefined && (
            <View style={{ marginTop: 2, marginBottom: 4, gap: 4 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <View style={[
                  audioEnhBadge.badge,
                  audioEnhance.installed
                    ? { backgroundColor: alpha(T.green, 0.10), borderColor: alpha(T.green, 0.33) }
                    : { backgroundColor: colors.muted, borderColor: colors.border },
                ]}>
                  <Text style={[
                    audioEnhBadge.badgeText,
                    { color: audioEnhance.installed ? T.green : colors.mutedForeground },
                  ]}>
                    {audioEnhance.installed ? 'installed' : 'not installed'}
                  </Text>
                </View>
                {audioEnhance.installed && audioEnhance.enabled && (
                  <Text style={[s.audioStatusText, { color: T.green }]}>
                    Active — audio will be denoised before transcription
                  </Text>
                )}
              </View>
              {!audioEnhance.installed && audioEnhance.install_hint && (
                <Text style={[s.audioStatusText, { color: T.gilt, lineHeight: 16 }]}>
                  To install: <Text style={{ ...font('semibold') }}>{audioEnhance.install_hint}</Text>
                </Text>
              )}
            </View>
          )}
        </Section>

        {/* Embeddings */}
        <Section title="Semantic Search" icon="search">
          <View style={s.row}>
            <View style={[s.dot, { backgroundColor: embColor, marginRight: 6 }]} />
            <Text style={[s.rowText, { color: colors.foreground }]}>
              {embOk
                ? 'Semantic search active'
                : embState === 'open'
                  ? 'Circuit breaker open — keyword-only mode'
                  : `State: ${embState}`}
            </Text>
          </View>
          {probeResult && (
            <View style={[s.probeResult, {
              backgroundColor: alpha(probeResult.startsWith('✓') ? T.green : T.rust, 0.10),
              borderColor: alpha(probeResult.startsWith('✓') ? T.green : T.rust, 0.28),
            }]}>
              <Text style={[s.probeResultText, { color: probeResult.startsWith('✓') ? T.green : T.rust }]}>
                {probeResult}
              </Text>
            </View>
          )}
          <Pressable
            onPress={handleProbeEmbeddings}
            disabled={probing}
            style={({ pressed }) => [s.actionBtn, { borderColor: colors.border, backgroundColor: colors.muted, opacity: pressed || probing ? 0.6 : 1 }]}
          >
            {probing ? <ActivityIndicator size="small" color={colors.primary} /> : <Feather name="cpu" size={14} color={colors.primary} />}
            <Text style={[s.actionBtnText, { color: colors.primary }]}>
              {probing ? 'Testing…' : 'Test Embeddings'}
            </Text>
          </Pressable>
        </Section>

        {/* Nightshift */}
        <Section title="Nightshift Maintenance" icon="moon">
          {nightshiftStatus && (
            <View style={s.row}>
              <View style={[s.dot, {
                backgroundColor: (nightshiftStatus as any).running ? T.gilt : T.green,
                marginRight: 6,
              }]} />
              <Text style={[s.rowText, { color: colors.foreground }]}>
                {(nightshiftStatus as any).running ? 'Running now…' : 'Idle'}
              </Text>
            </View>
          )}
          {(nightshiftStatus as any)?.last_run && (
            <Text style={[s.metaText, { color: colors.mutedForeground }]}>
              Last run: {new Date((nightshiftStatus as any).last_run).toLocaleString()}
            </Text>
          )}
          <Pressable
            onPress={handleRunNightshift}
            disabled={nightshiftRunning || (nightshiftStatus as any)?.running}
            style={({ pressed }) => [
              s.actionBtn,
              { borderColor: colors.border, backgroundColor: colors.muted,
                opacity: pressed || nightshiftRunning || (nightshiftStatus as any)?.running ? 0.6 : 1 },
            ]}
          >
            {nightshiftRunning ? <ActivityIndicator size="small" color={colors.primary} /> : <Feather name="play" size={14} color={colors.primary} />}
            <Text style={[s.actionBtnText, { color: colors.primary }]}>
              {nightshiftRunning ? 'Starting…' : 'Run Now'}
            </Text>
          </Pressable>
        </Section>

        {/* Deduplication stats */}
        {dedupStats && (
          <Section title="Deduplication" icon="copy">
            <Text style={[s.metaText, { color: colors.mutedForeground }]}>
              Near-duplicate detection runs automatically during nightly maintenance.
            </Text>
            <Pressable
              onPress={() => router.push('/library' as any)}
              style={({ pressed }) => [s.actionBtn, { borderColor: colors.border, backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1 }]}
            >
              <Feather name="external-link" size={14} color={colors.primary} />
              <Text style={[s.actionBtnText, { color: colors.primary }]}>View in Library</Text>
            </Pressable>
          </Section>
        )}

        {/* Memory */}
        <Section title="Memory" icon="cpu">
          <Text style={[s.metaText, { color: colors.mutedForeground }]}>
            Facts captured automatically as you chat are injected into every AI reply. View or delete them here.
          </Text>
          <Pressable
            onPress={() => router.push('/memory' as any)}
            style={({ pressed }) => [s.actionBtn, { borderColor: colors.border, backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1 }]}
          >
            <Feather name="cpu" size={14} color={colors.primary} />
            <Text style={[s.actionBtnText, { color: colors.primary }]}>View & Manage Memory</Text>
          </Pressable>
        </Section>

        <ProfileSection />
      </ScrollView>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  container: { flex: 1 },
  header: { paddingHorizontal: 16, paddingBottom: 14, borderBottomWidth: 1 },
  backRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10, minHeight: 44 },
  backLabel: { fontSize: 14, ...font('medium') },
  title: { fontSize: 22, ...fontSerif('bold'), letterSpacing: -0.3 },
  subtitle: { fontSize: 12, lineHeight: 18, ...font('regular'), marginTop: 2 },
  // Section
  section: { borderRadius: 10, borderWidth: 1, padding: 14, marginBottom: 16 },
  sectionHead: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 12 },
  sectionTitle: { fontSize: 10, ...font('bold'), letterSpacing: 1 },
  // Stats
  pillRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginVertical: 8 },
  statPill: { borderRadius: 8, borderWidth: 1, paddingHorizontal: 10, paddingVertical: 6, alignItems: 'center', minWidth: 70 },
  statValue: { fontSize: 16, ...font('bold') },
  statLabel: { fontSize: 10, ...font('regular'), marginTop: 1 },
  versionText: { fontSize: 11, lineHeight: 16, ...font('regular'), marginTop: 6 },
  // Health
  healthBadge: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 10, borderRadius: 8, borderWidth: 1, marginBottom: 10 },
  healthBadgeText: { fontSize: 13, lineHeight: 18, ...font('semibold') },
  dot: { width: 8, height: 8, borderRadius: 4 },
  // Toggle row
  toggleRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 12, minHeight: 48, borderTopWidth: StyleSheet.hairlineWidth },
  toggleLabel: { fontSize: 13, lineHeight: 18, ...font('semibold'), marginBottom: 2 },
  toggleDesc: { fontSize: 11, lineHeight: 15, ...font('regular') },
  // Generic row
  row: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  rowText: { fontSize: 13, lineHeight: 18, ...font('regular'), flex: 1 },
  metaText: { fontSize: 12, lineHeight: 18, ...font('regular'), marginBottom: 10 },
  // Audio status inline text
  audioStatusText: { fontSize: 11, lineHeight: 16, ...font('regular') },
  // Probe result
  probeResult: { borderRadius: 6, borderWidth: 1, padding: 8, marginVertical: 6 },
  probeResultText: { fontSize: 12, lineHeight: 18, ...font('regular') },
  // Action button
  actionBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start',
    borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, marginTop: 4,
    minHeight: 44,
  },
  actionBtnText: { fontSize: 13, lineHeight: 18, ...font('medium') },
});

const audioEnhBadge = StyleSheet.create({
  badge:     { borderWidth: 1, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  badgeText: { fontSize: 10, ...font('semibold') },
});
