import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
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
  TouchableOpacity,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens, VELLUM_LIGHT } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { Feather } from '@expo/vector-icons';
import { useGetDashboardSummary, useGetDashboardActivity, useGetBriefing } from '@workspace/api-client-react';
import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { font } from '@/lib/typography';
import { useSheetAnimation } from '@/lib/useSheetAnimation';
import type { Work, ActivityItem } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';
import { WeatherCard } from '@/components/WeatherCard';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { mobileFetch } from '@/lib/api';
import { apiOrigin } from '@/lib/server';

function StatCard({ label, value, icon }: { label: string; value: number | undefined; icon: string }) {
  const colors = useColors();
  return (
    <View style={[styles.statCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <Feather name={icon as any} size={18} color={colors.primary} />
      <Text style={[styles.statValue, { color: colors.foreground }]}>
        {value ?? '—'}
      </Text>
      <Text style={[styles.statLabel, { color: colors.mutedForeground }]}>{label}</Text>
    </View>
  );
}

function WorkRow({ work }: { work: Work }) {
  const colors = useColors();
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.push(`/work/${work.id}`)}
      style={({ pressed }) => [
        styles.workRow,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
      ]}
    >
      <View style={styles.workRowLeft}>
        <Text style={[styles.workTitle, { color: colors.foreground }]} numberOfLines={1}>
          {work.title ?? 'Untitled'}
        </Text>
        <Text style={[styles.workMeta, { color: colors.mutedForeground }]}>
          {work.work_type ?? 'research'} · {work.doc_count ?? 0} docs · {work.knowledge_count ?? 0} nodes
        </Text>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

function ActivityRow({ item }: { item: ActivityItem }) {
  const colors = useColors();
  const router = useRouter();
  const iconMap: Record<string, string> = {
    work: 'book-open',
    document: 'file-text',
    conversation: 'message-circle',
    knowledge: 'cpu',
  };
  const icon = iconMap[item.kind ?? ''] ?? 'activity';
  const when = item.created_at ? new Date(item.created_at).toLocaleDateString() : '';

  const handlePress = () => {
    if (item.kind === 'work' && item.id) router.push(`/work/${item.id}`);
    else if (item.kind === 'conversation' && item.id) router.push(`/chat/${item.id}`);
  };

  const tappable = item.kind === 'work' || item.kind === 'conversation';

  return (
    <Pressable
      onPress={tappable ? handlePress : undefined}
      style={({ pressed }) => [
        styles.activityRow,
        { borderColor: colors.border, opacity: pressed && tappable ? 0.6 : 1 },
      ]}
    >
      <View style={[styles.activityIcon, { backgroundColor: colors.muted }]}>
        <Feather name={icon as any} size={13} color={colors.primary} />
      </View>
      <Text style={[styles.activityLabel, { color: colors.foreground }]} numberOfLines={1}>
        {item.label ?? item.kind}
      </Text>
      <Text style={[styles.activityDate, { color: colors.mutedForeground }]}>{when}</Text>
      {tappable && <Feather name="chevron-right" size={13} color={colors.mutedForeground} />}
    </Pressable>
  );
}

// ── Server hardware summary (compact) ─────────────────────────────────────────

interface MobileHwData {
  cpu_percent?: number;
  cpu_count?: number;
  ram?: { used_gb: number; total_gb: number; percent: number } | null;
  disk?: { used_gb: number; total_gb: number; percent: number } | null;
  gpus?: Array<{ name: string; vram_used_mb: number | null; vram_total_mb: number | null; utilization_percent: number | null }>;
  gpu_available?: boolean;
  error?: string | null;
}

function MiniGauge({
  pct,
  label,
  value,
  colors,
}: {
  pct: number;
  label: string;
  value: string;
  colors: ReturnType<typeof useColors>;
}) {
  const T = useVellumTokens();
  const fill = pct > 90 ? T.rust : pct > 70 ? T.gilt : T.green;
  return (
    <View style={{ flex: 1, minWidth: 80, gap: 4 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
        <Text style={{ fontSize: 10, ...font('medium'), color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          {label}
        </Text>
        <Text style={{ fontSize: 10, ...font('semibold'), color: colors.foreground }}>{value}</Text>
      </View>
      <View style={{ height: 4, borderRadius: 2, backgroundColor: colors.muted, overflow: 'hidden' }}>
        <View style={{ width: `${Math.min(pct, 100)}%`, height: '100%', borderRadius: 2, backgroundColor: fill }} />
      </View>
    </View>
  );
}

const _HW_API = () => `${apiOrigin()}/api/system/hardware`;

function ServerHealthCard() {
  const colors = useColors();
  const { data, isLoading } = useQuery<MobileHwData | null>({
    queryKey: ['system', 'hardware'],
    queryFn: async () => {
      const r = await mobileFetch(_HW_API());
      if (!r.ok) return null;
      return r.json();
    },
    // Poll at 5 s on mobile (fast enough to watch live generation without
    // the complexity of a separate jobs-running check).
    refetchInterval: 5_000,
    staleTime: 4_000,
  });

  if (isLoading) {
    return (
      <View style={[styles.hwCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
        <ActivityIndicator size="small" color={colors.primary} />
      </View>
    );
  }

  if (!data || data.error === 'psutil not installed') return null;

  const gpu = data.gpus?.[0];
  const vramPct =
    gpu?.vram_used_mb && gpu?.vram_total_mb
      ? (gpu.vram_used_mb / gpu.vram_total_mb) * 100
      : null;

  return (
    <View style={[styles.hwCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        <Feather name="cpu" size={13} color={colors.primary} />
        <Text style={{ fontSize: 11, ...font('semibold'), color: colors.foreground }}>
          Server
        </Text>
        {data.cpu_count != null && (
          <Text style={{ fontSize: 10, ...font('regular'), color: colors.mutedForeground }}>
            {data.cpu_count} cores
          </Text>
        )}
      </View>

      <View style={{ flexDirection: 'row', gap: 12, flexWrap: 'wrap' }}>
        <MiniGauge
          label="CPU"
          pct={data.cpu_percent ?? 0}
          value={`${(data.cpu_percent ?? 0).toFixed(0)}%`}
          colors={colors}
        />
        {data.ram && (
          <MiniGauge
            label="RAM"
            pct={data.ram.percent}
            value={`${data.ram.used_gb.toFixed(1)}/${data.ram.total_gb.toFixed(1)}G`}
            colors={colors}
          />
        )}
        {data.disk && (
          <MiniGauge
            label="Disk"
            pct={data.disk.percent}
            value={`${data.disk.used_gb.toFixed(0)}/${data.disk.total_gb.toFixed(0)}G`}
            colors={colors}
          />
        )}
        {data.gpu_available && vramPct != null && gpu && (
          <MiniGauge
            label="VRAM"
            pct={vramPct}
            value={`${((gpu.vram_used_mb ?? 0) / 1024).toFixed(1)}G`}
            colors={colors}
          />
        )}
      </View>
    </View>
  );
}

// ── Review Queue tile ─────────────────────────────────────────────────────────

const _REVIEW_TYPE_LABELS: Record<string, string> = {
  knowledge:  'AI knowledge',
  reclassify: 'Reclassify',
  suggestion: 'Suggestion',
  duplicate:  'Duplicate',
};

const _REVIEW_QUEUE_URL = () => `${apiOrigin()}/api/review/queue`;

function ReviewQueueTile() {
  const colors = useColors();
  const T = useVellumTokens();
  const router = useRouter();

  const { data } = useQuery<{
    count: number;
    counts_by_type: Record<string, number>;
  } | null>({
    queryKey: ['review', 'queue', 'dashboard-tile'],
    queryFn: async () => {
      const r = await mobileFetch(_REVIEW_QUEUE_URL());
      if (!r.ok) return null;
      return r.json();
    },
    refetchInterval: 60_000,
    staleTime: 50_000,
  });

  const count = data?.count ?? 0;
  if (count === 0) return null;

  // Pick the top two non-zero types as a compact subtitle
  const breakdown = Object.entries(data?.counts_by_type ?? {})
    .filter(([, n]) => n > 0)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 2)
    .map(([key, n]) => `${n} ${_REVIEW_TYPE_LABELS[key] ?? key}`)
    .join(' · ');

  return (
    <Pressable
      onPress={() => router.push('/review' as any)}
      style={({ pressed }) => [
        styles.reviewTile,
        {
          backgroundColor: colors.card,
          borderColor: T.giltLine,
          opacity: pressed ? 0.8 : 1,
          minHeight: 44,
        },
      ]}
      accessibilityRole="button"
      accessibilityLabel={`Review queue: ${count} item${count !== 1 ? 's' : ''} pending`}
    >
      {/* Icon */}
      <View style={[styles.reviewTileIcon, { backgroundColor: T.giltSoft }]}>
        <Feather name="shield" size={18} color={T.gilt} />
        {/* Count badge */}
        <View style={[styles.reviewTileBadge, { backgroundColor: T.rust }]}>
          <Text style={styles.reviewTileBadgeText}>
            {count > 99 ? '99+' : String(count)}
          </Text>
        </View>
      </View>

      {/* Text */}
      <View style={{ flex: 1 }}>
        <Text style={[styles.reviewTileTitle, { color: colors.foreground }]}>
          {count} item{count !== 1 ? 's' : ''} to review
        </Text>
        {!!breakdown && (
          <Text style={[styles.reviewTileSub, { color: colors.mutedForeground }]}>
            {breakdown}
          </Text>
        )}
      </View>

      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

// ── Studio card ───────────────────────────────────────────────────────────────

function StudioCard() {
  const colors = useColors();
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.push('/studio')}
      style={({ pressed }) => [
        styles.studioCard,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.75 : 1 },
      ]}
    >
      <View style={[styles.studioIcon, { backgroundColor: colors.primary + '22' }]}>
        <Feather name="mic" size={18} color={colors.primary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={[styles.studioTitle, { color: colors.foreground }]}>Studio</Text>
        <Text style={[styles.studioSub, { color: colors.mutedForeground }]}>
          Text-to-speech & image generation
        </Text>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

// ── System health — constants & types ─────────────────────────────────────────

const _SYS_DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const _SYS_API = () => `${_SYS_DOMAIN()}/api`;

interface SystemHealthData {
  status: string;
  services: {
    database: { status: string };
    ai: { status: string; endpoint: string };
  };
}
interface EmbeddingsStatusData { circuit_open: boolean; available_at: number | null }
interface NightshiftStatusData {
  running: boolean;
  started_at: string | null;
  last_run: { ran_at: string; docs_processed: number; items_added: number } | null;
}
type DiagStatus = 'ok' | 'warn' | 'error' | 'info';
interface DiagCheck { name: string; status: DiagStatus; value: string | number; detail: string }
interface DiagResult {
  generated_at: string;
  elapsed_ms: number;
  summary: { ok: number; warn: number; error: number; info: number; total: number };
  all_checks: DiagCheck[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function relTime(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const sec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (isNaN(sec) || sec < 0) return 'never';
  if (sec < 60) return 'just now';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

const DIAG_COLOR: Record<DiagStatus, string> = {
  ok: VELLUM_LIGHT.green, info: '#3b82f6', warn: VELLUM_LIGHT.gilt, error: VELLUM_LIGHT.rust,
};

// ── StatusRow ─────────────────────────────────────────────────────────────────

function StatusDot({ color }: { color: string }) {
  return <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color, marginTop: 2, flexShrink: 0 }} />;
}

function StatusRow({ label, color, detail }: { label: string; color: string; detail: string }) {
  const colors = useColors();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8, paddingVertical: 4 }}>
      <StatusDot color={color} />
      <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.foreground, width: 88 }}>
        {label}
      </Text>
      <Text
        style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, flex: 1 }}
        numberOfLines={1}
      >
        {detail}
      </Text>
    </View>
  );
}

// ── DiagnosticsSheet ──────────────────────────────────────────────────────────

const _DIAG_SHEET_H = 560;

function DiagnosticsSheet({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const [rendered, setRendered] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DiagResult | null>(null);
  const [fetchErr, setFetchErr] = useState('');

  const slideAnim = useRef(new Animated.Value(_DIAG_SHEET_H + 60)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      setRendered(true);
      Animated.parallel([
        Animated.spring(slideAnim, { toValue: 0, useNativeDriver: true, tension: 85, friction: 13 }),
        Animated.timing(fadeAnim,  { toValue: 1, duration: 180, useNativeDriver: true }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, { toValue: _DIAG_SHEET_H + 60, duration: 220, useNativeDriver: true }),
        Animated.timing(fadeAnim,  { toValue: 0, duration: 180, useNativeDriver: true }),
      ]).start(() => setRendered(false));
    }
  }, [visible]);

  const runDiag = useCallback(async () => {
    setLoading(true);
    setFetchErr('');
    setResult(null);
    try {
      const r = await mobileFetch(`${_SYS_API()}/system/diagnostics`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setResult(await r.json());
    } catch (e: any) {
      setFetchErr(e?.message ?? 'Diagnostics failed');
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-run when sheet first opens
  const didRun = useRef(false);
  useEffect(() => {
    if (visible && !didRun.current) {
      didRun.current = true;
      runDiag();
    }
    if (!visible) didRun.current = false;
  }, [visible, runDiag]);

  if (!rendered) return null;

  const checks = result?.all_checks ?? [];
  const summary = result?.summary;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose} statusBarTranslucent>
      {/* Backdrop */}
      <Animated.View
        style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.42)', opacity: fadeAnim }]}
        pointerEvents={visible ? 'auto' : 'none'}
      >
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      {/* Sheet */}
      <Animated.View
        style={[
          sysStyles.sheet,
          {
            backgroundColor: colors.card,
            borderColor: colors.border,
            paddingBottom: insets.bottom + 16,
            transform: [{ translateY: slideAnim }],
          },
        ]}
      >
        <View style={[sysStyles.handle, { backgroundColor: colors.border }]} />

        {/* Header */}
        <View style={sysStyles.sheetHeader}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
            <Feather name="activity" size={15} color={colors.primary} />
            <Text style={{ fontSize: 15, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
              System Diagnostic
            </Text>
            {summary && (
              <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                {summary.total} checks · {result?.elapsed_ms}ms
              </Text>
            )}
          </View>
          <Pressable onPress={onClose} hitSlop={10}>
            <Feather name="x" size={18} color={colors.mutedForeground} />
          </Pressable>
        </View>

        {/* Summary counts */}
        {summary && (
          <View style={[sysStyles.summaryBar, { backgroundColor: colors.muted + '60', borderColor: colors.border }]}>
            {(['ok', 'warn', 'error', 'info'] as const).map(s => (
              <View key={s} style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: DIAG_COLOR[s] }} />
                <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: DIAG_COLOR[s] }}>
                  {summary[s]}
                </Text>
                <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                  {s}
                </Text>
              </View>
            ))}
          </View>
        )}

        {/* Check list */}
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{ padding: 10, gap: 2 }}
          showsVerticalScrollIndicator={false}
        >
          {loading && (
            <View style={{ alignItems: 'center', paddingVertical: 44, gap: 10 }}>
              <ActivityIndicator color={colors.primary} />
              <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                Running checks…
              </Text>
            </View>
          )}
          {!loading && !!fetchErr && (
            <View style={{ alignItems: 'center', paddingVertical: 32, gap: 10 }}>
              <Feather name="wifi-off" size={32} color={colors.mutedForeground} style={{ opacity: 0.5 }} />
              <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center' }}>
                {fetchErr}
              </Text>
              <Pressable
                onPress={runDiag}
                style={({ pressed }) => [sysStyles.retryBtn, { borderColor: colors.border, backgroundColor: pressed ? colors.muted : 'transparent' }]}
              >
                <Feather name="refresh-cw" size={13} color={colors.foreground} />
                <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}>Retry</Text>
              </Pressable>
            </View>
          )}
          {!loading && !fetchErr && checks.map((c, i) => (
            <View key={i} style={[sysStyles.checkRow, { borderColor: colors.border }]}>
              <View
                style={{
                  width: 7, height: 7, borderRadius: 4,
                  backgroundColor: DIAG_COLOR[c.status] ?? colors.muted,
                  flexShrink: 0, marginTop: 3,
                }}
              />
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text
                  style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}
                  numberOfLines={1}
                >
                  {c.name}
                </Text>
                {!!c.detail && (
                  <Text
                    style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 15 }}
                    numberOfLines={2}
                  >
                    {c.detail}
                  </Text>
                )}
              </View>
              <Text
                style={{
                  fontSize: 10, fontFamily: 'Inter_400Regular',
                  color: DIAG_COLOR[c.status] ?? colors.mutedForeground,
                  flexShrink: 0, maxWidth: 80,
                }}
                numberOfLines={1}
              >
                {String(c.value).slice(0, 24)}
              </Text>
            </View>
          ))}
        </ScrollView>

        {/* Re-run button */}
        <View style={{ paddingHorizontal: 12, paddingTop: 8 }}>
          <Pressable
            onPress={runDiag}
            disabled={loading}
            style={({ pressed }) => [
              sysStyles.runBtn,
              { backgroundColor: pressed ? colors.primary + 'cc' : colors.primary, opacity: loading ? 0.5 : 1 },
            ]}
          >
            {loading
              ? <ActivityIndicator size="small" color="#fff" style={{ transform: [{ scale: 0.7 }] }} />
              : <Feather name="refresh-cw" size={14} color="#fff" />}
            <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>
              Re-run diagnostics
            </Text>
          </Pressable>
        </View>
      </Animated.View>
    </Modal>
  );
}

// ── Automation Activity ───────────────────────────────────────────────────────

interface ActionRun {
  id: string;
  action_name: string;
  inputs: string;
  status: 'running' | 'done' | 'error';
  output_path: string | null;
  output_label: string | null;
  output_doc_id: string | null;
  work_id: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

interface LogLine {
  ts: string | null;
  level: 'info' | 'error' | 'warn';
  msg: string;
}

interface ActionDef {
  name: string;
  description: string;
  category: string;
  input_schema: {
    required?: string[];
    properties?: Record<string, { description?: string; type?: string }>;
  };
}

interface WorkEntry { id: string; title: string | null }

// Duration between two ISO strings in a human-readable form
function _duration(start: string | null, end: string | null): string {
  if (!start || !end) return '';
  const s = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000);
  if (isNaN(s) || s < 0) return '';
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

// ── RunLogSheet ───────────────────────────────────────────────────────────────

const _LOG_SHEET_H = 500;
const _LOG_LEVEL_COLOR: Record<string, string> = {
  info: VELLUM_LIGHT.green, warn: VELLUM_LIGHT.gilt, error: VELLUM_LIGHT.rust,
};

function RunLogSheet({
  run,
  visible,
  onClose,
  onRetrySuccess,
}: {
  run: ActionRun | null;
  visible: boolean;
  onClose: () => void;
  onRetrySuccess: (newRun: ActionRun) => void;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const [rendered, setRendered] = useState(false);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [logLoading, setLogLoading] = useState(false);
  const [logErr, setLogErr] = useState('');
  const [retrying, setRetrying] = useState(false);

  const slideAnim = useRef(new Animated.Value(_LOG_SHEET_H + 60)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      setRendered(true);
      Animated.parallel([
        Animated.spring(slideAnim, { toValue: 0, useNativeDriver: true, tension: 85, friction: 13 }),
        Animated.timing(fadeAnim,  { toValue: 1, duration: 180, useNativeDriver: true }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, { toValue: _LOG_SHEET_H + 60, duration: 220, useNativeDriver: true }),
        Animated.timing(fadeAnim,  { toValue: 0, duration: 180, useNativeDriver: true }),
      ]).start(() => setRendered(false));
    }
  }, [visible]);

  // Load log when run changes and sheet is open
  useEffect(() => {
    if (!visible || !run) return;
    setLines([]);
    setLogErr('');
    setLogLoading(true);
    mobileFetch(`${_SYS_API()}/actions/runs/${run.id}/log`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => setLines(d.lines ?? []))
      .catch(e => setLogErr(e?.message ?? 'Could not load log'))
      .finally(() => setLogLoading(false));
  }, [visible, run?.id]);

  const handleRetry = useCallback(async () => {
    if (!run) return;
    setRetrying(true);
    try {
      const resp = await mobileFetch(`${_SYS_API()}/actions/runs/${run.id}/retry`, { method: 'POST' });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const result = await resp.json();
      // Build a new ActionRun representing the retry result so the old
      // failed row is left intact in the list and the new run appears at top.
      const newRun: ActionRun = {
        id: result.run_id ?? `retry-${run.id}`,
        action_name: run.action_name,
        inputs: run.inputs,
        status: 'done',
        output_path: result.output_path ?? null,
        output_label: result.output_label ?? null,
        output_doc_id: result.output_doc_id ?? null,
        work_id: run.work_id,
        error: null,
        created_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
      };
      onRetrySuccess(newRun);
      onClose();
    } catch (e: any) {
      setLogErr(e?.message ?? 'Retry failed');
    } finally {
      setRetrying(false);
    }
  }, [run, onRetrySuccess, onClose]);

  if (!rendered || !run) return null;

  const statusColor = run.status === 'done' ? T.green : run.status === 'error' ? T.rust : T.gilt;
  const dur = _duration(run.created_at, run.completed_at);

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose} statusBarTranslucent>
      <Animated.View
        style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.42)', opacity: fadeAnim }]}
        pointerEvents={visible ? 'auto' : 'none'}
      >
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      <Animated.View
        style={[
          sysStyles.sheet,
          {
            backgroundColor: colors.card,
            borderColor: colors.border,
            paddingBottom: insets.bottom + 16,
            transform: [{ translateY: slideAnim }],
          },
        ]}
      >
        <View style={[sysStyles.handle, { backgroundColor: colors.border }]} />

        {/* Header */}
        <View style={sysStyles.sheetHeader}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: statusColor, flexShrink: 0 }} />
            <Text
              style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground, flex: 1 }}
              numberOfLines={1}
            >
              {run.action_name.replace(/_/g, ' ')}
            </Text>
            {!!dur && (
              <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                {dur}
              </Text>
            )}
          </View>
          <Pressable onPress={onClose} hitSlop={10}>
            <Feather name="x" size={18} color={colors.mutedForeground} />
          </Pressable>
        </View>

        {/* Timestamp strip */}
        <View style={{ paddingHorizontal: 16, paddingBottom: 8 }}>
          <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
            {relTime(run.created_at)} · {run.status}
            {run.work_id ? ` · work ${run.work_id.slice(0, 8)}` : ''}
          </Text>
        </View>

        {/* Log lines */}
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{ paddingHorizontal: 16, paddingVertical: 4, gap: 2 }}
          showsVerticalScrollIndicator={false}
        >
          {logLoading && (
            <View style={{ alignItems: 'center', paddingVertical: 32 }}>
              <ActivityIndicator color={colors.primary} />
            </View>
          )}
          {!logLoading && !!logErr && (
            <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: T.rust, lineHeight: 18 }}>
              {logErr}
            </Text>
          )}
          {!logLoading && !logErr && lines.map((line, i) => {
            const levelColor = _LOG_LEVEL_COLOR[line.level ?? 'info'] ?? colors.mutedForeground;
            const ts = line.ts ? new Date(line.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
            return (
              <View
                key={i}
                style={{
                  flexDirection: 'row',
                  gap: 8,
                  paddingVertical: 3,
                  borderBottomWidth: StyleSheet.hairlineWidth,
                  borderBottomColor: colors.border,
                }}
              >
                {/* Level indicator */}
                <View style={{ width: 4, borderRadius: 2, backgroundColor: levelColor, alignSelf: 'stretch', flexShrink: 0 }} />
                <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, width: 56, paddingTop: 1, flexShrink: 0 }}>
                  {ts}
                </Text>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.foreground, flex: 1, lineHeight: 18 }}>
                  {line.msg}
                </Text>
              </View>
            );
          })}
        </ScrollView>

        {/* Retry button — only for failed runs */}
        {run.status === 'error' && (
          <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
            <Pressable
              onPress={handleRetry}
              disabled={retrying}
              style={({ pressed }) => [
                actStyles.retryBtn,
                { backgroundColor: pressed ? T.rustSoft : T.rust, opacity: retrying ? 0.55 : 1 },
              ]}
            >
              {retrying
                ? <ActivityIndicator size="small" color="#fff" />
                : <Feather name="refresh-cw" size={14} color="#fff" />}
              <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>
                {retrying ? 'Retrying…' : 'Retry this action'}
              </Text>
            </Pressable>
          </View>
        )}
      </Animated.View>
    </Modal>
  );
}

// ── ActionLauncherSheet ───────────────────────────────────────────────────────

const _LAUNCHER_SHEET_H = 640;

function ActionLauncherSheet({
  visible,
  onClose,
  onLaunched,
}: {
  visible: boolean;
  onClose: () => void;
  onLaunched: (run: ActionRun) => void;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const [rendered, setRendered] = useState(false);

  // Per-action field values: { [actionName]: { [fieldName]: string } }
  const [inputs, setInputs] = useState<Record<string, Record<string, string>>>({});
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [errorMap, setErrorMap] = useState<Record<string, string>>({});

  const slideAnim = useRef(new Animated.Value(_LAUNCHER_SHEET_H + 60)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      setRendered(true);
      Animated.parallel([
        Animated.spring(slideAnim, { toValue: 0, useNativeDriver: true, tension: 85, friction: 13 }),
        Animated.timing(fadeAnim,  { toValue: 1, duration: 180, useNativeDriver: true }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, { toValue: _LAUNCHER_SHEET_H + 60, duration: 220, useNativeDriver: true }),
        Animated.timing(fadeAnim,  { toValue: 0, duration: 180, useNativeDriver: true }),
      ]).start(() => setRendered(false));
    }
  }, [visible]);

  // Fetch available actions (cached 60s)
  const { data: actionsData, isLoading: actionsLoading } = useQuery<{ actions: ActionDef[] }>({
    queryKey: ['actions', 'list'],
    queryFn: async () => {
      const r = await mobileFetch(`${_SYS_API()}/actions`);
      if (!r.ok) return { actions: [] };
      return r.json();
    },
    enabled: visible,
    staleTime: 60_000,
  });

  // Fetch works for the work_id pill picker (cached 60s)
  const { data: worksData } = useQuery<{ works: WorkEntry[] }>({
    queryKey: ['works', 'list-short'],
    queryFn: async () => {
      const r = await mobileFetch(`${_SYS_API()}/works`);
      if (!r.ok) return { works: [] };
      return r.json();
    },
    enabled: visible,
    staleTime: 60_000,
  });

  const actions = actionsData?.actions ?? [];
  const works   = worksData?.works ?? [];

  const setField = (actionName: string, field: string, value: string) => {
    setInputs(prev => ({
      ...prev,
      [actionName]: { ...(prev[actionName] ?? {}), [field]: value },
    }));
  };

  const handleRun = async (action: ActionDef) => {
    const actionInputs = inputs[action.name] ?? {};
    const required = action.input_schema?.required ?? [];
    const missing  = required.filter(f => !actionInputs[f]);
    if (missing.length > 0) {
      setErrorMap(prev => ({ ...prev, [action.name]: `Fill in: ${missing.join(', ')}` }));
      return;
    }
    setErrorMap(prev => ({ ...prev, [action.name]: '' }));
    setSubmitting(action.name);
    try {
      const resp = await mobileFetch(`${_SYS_API()}/actions/${action.name}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(actionInputs),
      });
      const result = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((result as any).detail ?? `HTTP ${resp.status}`);

      // Build an optimistic run row (actions are synchronous; result is available now)
      const newRun: ActionRun = {
        id: (result as any).run_id ?? `opt-${Date.now()}`,
        action_name: action.name,
        inputs: JSON.stringify(actionInputs),
        status: 'done',
        output_path: (result as any).output_path ?? null,
        output_label: (result as any).output_label ?? null,
        output_doc_id: (result as any).output_doc_id ?? null,
        work_id: actionInputs['work_id'] ?? null,
        error: null,
        created_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
      };
      onLaunched(newRun);
      onClose();
    } catch (e: any) {
      setErrorMap(prev => ({ ...prev, [action.name]: e?.message ?? 'Action failed' }));
    } finally {
      setSubmitting(null);
    }
  };

  if (!rendered) return null;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose} statusBarTranslucent>
      {/* Backdrop */}
      <Animated.View
        style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.42)', opacity: fadeAnim }]}
        pointerEvents={visible ? 'auto' : 'none'}
      >
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      {/* Sheet */}
      <Animated.View
        style={[
          sysStyles.sheet,
          {
            backgroundColor: colors.card,
            borderColor: colors.border,
            height: _LAUNCHER_SHEET_H,
            paddingBottom: insets.bottom + 16,
            transform: [{ translateY: slideAnim }],
          },
        ]}
      >
        <View style={[sysStyles.handle, { backgroundColor: colors.border }]} />

        {/* Header */}
        <View style={sysStyles.sheetHeader}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1 }}>
            <Feather name="zap" size={15} color={colors.primary} />
            <Text style={{ fontSize: 15, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
              Run an Action
            </Text>
          </View>
          <Pressable onPress={onClose} hitSlop={10}>
            <Feather name="x" size={18} color={colors.mutedForeground} />
          </Pressable>
        </View>

        {/* Content */}
        {actionsLoading ? (
          <View style={{ flex: 1 }}>
            {[...Array(4)].map((_, i) => <SkeletonItem key={i} />)}
          </View>
        ) : actions.length === 0 ? (
          <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24 }}>
            <Feather name="zap" size={28} color={colors.mutedForeground} style={{ opacity: 0.35, marginBottom: 10 }} />
            <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center' }}>
              No actions are registered on the server yet.
            </Text>
          </View>
        ) : (
          <ScrollView
            style={{ flex: 1 }}
            contentContainerStyle={{ paddingHorizontal: 14, paddingVertical: 8, gap: 12 }}
            showsVerticalScrollIndicator={false}
            keyboardShouldPersistTaps="handled"
          >
            {actions.map(action => {
              const required     = action.input_schema?.required ?? [];
              const schemaProps  = action.input_schema?.properties ?? {};
              const needsWork    = required.includes('work_id');
              const textFields   = required.filter(f => f !== 'work_id');
              const actionInputs = inputs[action.name] ?? {};
              const canRun       = required.every(f => !!actionInputs[f]);
              const errMsg       = errorMap[action.name] ?? '';
              const isRunning    = submitting === action.name;

              return (
                <View
                  key={action.name}
                  style={[launchStyles.actionCard, { borderColor: colors.border, backgroundColor: colors.background }]}
                >
                  {/* Title row */}
                  <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8 }}>
                    <View style={[launchStyles.iconBox, { backgroundColor: colors.primary + '18' }]}>
                      <Feather name="zap" size={14} color={colors.primary} />
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
                          {action.name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                        </Text>
                        <View style={[launchStyles.catBadge, { borderColor: colors.border, backgroundColor: colors.muted }]}>
                          <Text style={{ fontSize: 9, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>
                            {action.category}
                          </Text>
                        </View>
                      </View>
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 15, marginTop: 2 }}>
                        {action.description}
                      </Text>
                    </View>
                  </View>

                  {/* Work picker — horizontal pill scroller */}
                  {needsWork && (
                    <View style={{ marginTop: 10 }}>
                      <Text style={launchStyles.fieldLabel}>Work</Text>
                      {works.length === 0 ? (
                        <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                          No Works found
                        </Text>
                      ) : (
                        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -2 }}>
                          <View style={{ flexDirection: 'row', gap: 6, paddingHorizontal: 2, paddingVertical: 2 }}>
                            {works.map(w => {
                              const sel = actionInputs['work_id'] === w.id;
                              return (
                                <Pressable
                                  key={w.id}
                                  onPress={() => setField(action.name, 'work_id', w.id)}
                                  style={[
                                    launchStyles.workPill,
                                    {
                                      borderColor: sel ? colors.primary : colors.border,
                                      backgroundColor: sel ? colors.primary + '18' : colors.muted,
                                    },
                                  ]}
                                >
                                  <Text style={{
                                    fontSize: 11,
                                    fontFamily: sel ? 'Inter_600SemiBold' : 'Inter_400Regular',
                                    color: sel ? colors.primary : colors.foreground,
                                  }} numberOfLines={1}>
                                    {w.title ?? w.id}
                                  </Text>
                                </Pressable>
                              );
                            })}
                          </View>
                        </ScrollView>
                      )}
                    </View>
                  )}

                  {/* Text inputs for other required fields */}
                  {textFields.map(field => {
                    const fieldSchema = schemaProps[field] ?? {};
                    return (
                      <View key={field} style={{ marginTop: 10 }}>
                        <Text style={launchStyles.fieldLabel}>{field}</Text>
                        <TextInput
                          style={[
                            launchStyles.textInput,
                            { borderColor: colors.border, backgroundColor: colors.background, color: colors.foreground },
                          ]}
                          placeholder={fieldSchema.description ?? field}
                          placeholderTextColor={colors.mutedForeground}
                          value={actionInputs[field] ?? ''}
                          onChangeText={v => setField(action.name, field, v)}
                          returnKeyType="done"
                          blurOnSubmit
                        />
                      </View>
                    );
                  })}

                  {/* Validation hint */}
                  {!!errMsg && (
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: T.rust, marginTop: 6 }}>
                      {errMsg}
                    </Text>
                  )}

                  {/* Run button */}
                  <Pressable
                    onPress={() => handleRun(action)}
                    disabled={isRunning || (required.length > 0 && !canRun)}
                    style={({ pressed }) => [
                      launchStyles.runBtn,
                      {
                        backgroundColor: colors.primary,
                        marginTop: 10,
                        opacity: isRunning || (required.length > 0 && !canRun) ? 0.38 : pressed ? 0.8 : 1,
                      },
                    ]}
                  >
                    {isRunning
                      ? <ActivityIndicator size="small" color="#fff" style={{ transform: [{ scale: 0.75 }] }} />
                      : <Feather name="zap" size={13} color="#fff" />}
                    <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>
                      {isRunning ? 'Running…' : 'Run'}
                    </Text>
                  </Pressable>
                </View>
              );
            })}
          </ScrollView>
        )}
      </Animated.View>
    </Modal>
  );
}

const launchStyles = StyleSheet.create({
  actionCard: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 12,
  },
  iconBox: {
    width: 30,
    height: 30,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  catBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    borderWidth: 1,
  },
  fieldLabel: {
    fontSize: 10,
    fontFamily: 'Inter_500Medium',
    color: '#888',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  workPill: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 16,
    borderWidth: 1,
    maxWidth: 160,
  },
  textInput: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontSize: 13,
    fontFamily: 'Inter_400Regular',
  },
  runBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 8,
    borderRadius: 8,
  },
});

// ── AutomationActivityCard ────────────────────────────────────────────────────

const _STATUS_ICON: Record<string, string> = {
  done: 'check-circle', error: 'x-circle', running: 'loader',
};
const _STATUS_COLOR: Record<string, string> = {
  done: VELLUM_LIGHT.green, error: VELLUM_LIGHT.rust, running: VELLUM_LIGHT.gilt,
};

function AutomationActivityCard() {
  const colors = useColors();
  const T = useVellumTokens();
  const [collapsed, setCollapsed] = useState(false);
  const [selectedRun, setSelectedRun] = useState<ActionRun | null>(null);
  const [sheetVisible, setSheetVisible] = useState(false);
  const [launcherVisible, setLauncherVisible] = useState(false);

  // Optimistically-prepended new run rows (from retry or new launch)
  const [pendingRuns, setPendingRuns] = useState<ActionRun[]>([]);

  const { data, isLoading, refetch } = useQuery<{ runs: ActionRun[]; count: number }>({
    queryKey: ['actions', 'runs'],
    queryFn: async () => {
      const r = await mobileFetch(`${_SYS_API()}/actions/runs?limit=10`);
      if (!r.ok) return { runs: [], count: 0 };
      return r.json();
    },
    refetchInterval: 15_000,
    staleTime: 12_000,
  });

  // Merge: prepend pending (new) rows, then server rows, deduplicate by id
  const serverRuns = data?.runs ?? [];
  const runIdSet = new Set(serverRuns.map(r => r.id));
  const runs = [
    ...pendingRuns.filter(r => !runIdSet.has(r.id)),
    ...serverRuns,
  ];

  const handleRowPress = (run: ActionRun) => {
    setSelectedRun(run);
    setSheetVisible(true);
  };

  // Called when retry succeeds: prepend the new run row optimistically,
  // then refetch twice so the list stays accurate.
  const handleRetrySuccess = useCallback((newRun: ActionRun) => {
    setPendingRuns(prev => [newRun, ...prev.filter(r => r.id !== newRun.id)]);
    refetch();
    setTimeout(() => refetch().then(() => setPendingRuns([])), 3000);
  }, [refetch]);

  // Called when the launcher fires a new action: optimistically show as "running",
  // then poll until the server row appears (actions are synchronous so the first
  // refetch after 1s typically picks it up).
  const handleLaunched = useCallback((newRun: ActionRun) => {
    // Show optimistic row immediately
    const optimistic: ActionRun = { ...newRun, status: 'running', completed_at: null };
    setPendingRuns(prev => [optimistic, ...prev.filter(r => r.id !== optimistic.id)]);
    // First refetch after 1s (action is usually done by then)
    setTimeout(() => refetch(), 1_000);
    // Second refetch clears the pending list once server has the real row
    setTimeout(() => refetch().then(() => setPendingRuns([])), 4_000);
  }, [refetch]);

  const failedCount  = runs.filter(r => r.status === 'error').length;
  const runningCount = runs.filter(r => r.status === 'running').length;
  const summaryColor = failedCount > 0 ? T.rust : runningCount > 0 ? T.gilt : T.green;

  return (
    <>
      <View style={[actStyles.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
        {/* Header row */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {/* Collapse toggle */}
          <Pressable
            onPress={() => setCollapsed(c => !c)}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 }}
            hitSlop={6}
          >
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: summaryColor, flexShrink: 0 }} />
            <Text
              style={{
                flex: 1, fontSize: 11, fontFamily: 'Inter_600SemiBold',
                color: colors.foreground, textTransform: 'uppercase', letterSpacing: 0.8,
              }}
            >
              Automation
            </Text>
            {failedCount > 0 && (
              <View style={[actStyles.badge, { backgroundColor: T.rustSoft, borderColor: T.rust }]}>
                <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: T.rust }}>
                  {failedCount} failed
                </Text>
              </View>
            )}
            {runningCount > 0 && (
              <View style={[actStyles.badge, { backgroundColor: T.giltSoft, borderColor: T.giltLine }]}>
                <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: T.gilt }}>
                  {runningCount} running
                </Text>
              </View>
            )}
            <Feather
              name={collapsed ? 'chevron-down' : 'chevron-up'}
              size={14}
              color={colors.mutedForeground}
            />
          </Pressable>

          {/* "Run action" button — always visible in the header */}
          <Pressable
            onPress={() => setLauncherVisible(true)}
            style={({ pressed }) => [
              actStyles.runActionBtn,
              {
                borderColor: colors.primary + '55',
                backgroundColor: pressed ? colors.primary + '18' : colors.primary + '10',
              },
            ]}
            hitSlop={6}
          >
            <Feather name="zap" size={11} color={colors.primary} />
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>
              Run
            </Text>
          </Pressable>
        </View>

        {!collapsed && (
          <View style={{ marginTop: 10, gap: 0 }}>
            {isLoading && runs.length === 0 && (
              <>{[...Array(3)].map((_, i) => <SkeletonItem key={i} lines={1} />)}</>
            )}
            {!isLoading && runs.length === 0 && (
              <View style={{ paddingVertical: 12, alignItems: 'center', gap: 4 }}>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                  No runs yet. Tap Run to launch an action.
                </Text>
              </View>
            )}
            {runs.map((run, idx) => {
              const statusColor = _STATUS_COLOR[run.status] ?? colors.mutedForeground;
              const icon = _STATUS_ICON[run.status] ?? 'activity';
              const label = run.action_name.replace(/_/g, ' ');
              const dur = _duration(run.created_at, run.completed_at);
              const when = relTime(run.created_at);
              const isLast = idx === runs.length - 1;

              return (
                <Pressable
                  key={run.id}
                  onPress={() => handleRowPress(run)}
                  style={({ pressed }) => [
                    actStyles.runRow,
                    {
                      borderBottomWidth: isLast ? 0 : StyleSheet.hairlineWidth,
                      borderBottomColor: colors.border,
                      opacity: pressed ? 0.65 : 1,
                    },
                  ]}
                >
                  <Feather
                    name={icon as any}
                    size={13}
                    color={statusColor}
                    style={run.status === 'running' ? { opacity: 0.8 } : undefined}
                  />
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text
                      style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }}
                      numberOfLines={1}
                    >
                      {label}
                    </Text>
                    {run.status === 'error' && !!run.error && (
                      <Text
                        style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: T.rust, lineHeight: 15 }}
                        numberOfLines={1}
                      >
                        {run.error.slice(0, 80)}
                      </Text>
                    )}
                    {run.status === 'done' && !!run.output_label && (
                      <Text
                        style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 15 }}
                        numberOfLines={1}
                      >
                        {run.output_label}
                      </Text>
                    )}
                  </View>
                  <View style={{ alignItems: 'flex-end', gap: 2, flexShrink: 0 }}>
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                      {when}
                    </Text>
                    {!!dur && (
                      <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                        {dur}
                      </Text>
                    )}
                  </View>
                  <Feather name="chevron-right" size={13} color={colors.mutedForeground} />
                </Pressable>
              );
            })}
          </View>
        )}
      </View>

      <RunLogSheet
        run={selectedRun}
        visible={sheetVisible}
        onClose={() => setSheetVisible(false)}
        onRetrySuccess={handleRetrySuccess}
      />

      <ActionLauncherSheet
        visible={launcherVisible}
        onClose={() => setLauncherVisible(false)}
        onLaunched={handleLaunched}
      />
    </>
  );
}

const actStyles = StyleSheet.create({
  card: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    marginBottom: 0,
  },
  badge: {
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 5,
    borderWidth: 1,
  },
  runRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    paddingVertical: 10,
    minHeight: 44,
  },
  retryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    paddingVertical: 11,
    borderRadius: 10,
  },
  runActionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 9,
    paddingVertical: 4,
    borderRadius: 6,
    borderWidth: 1,
    flexShrink: 0,
  },
});

// ── SystemHealthCard ──────────────────────────────────────────────────────────

function SystemHealthCard() {
  const colors = useColors();
  const T = useVellumTokens();
  const [collapsed, setCollapsed] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  const { data: health } = useQuery<SystemHealthData | null>({
    queryKey: ['system', 'svc-health'],
    queryFn: async () => {
      const r = await mobileFetch(`${_SYS_API()}/system/health`);
      return r.ok ? r.json() : null;
    },
    refetchInterval: 60_000,
    staleTime: 50_000,
  });

  const { data: embeddings } = useQuery<EmbeddingsStatusData | null>({
    queryKey: ['system', 'embeddings-status'],
    queryFn: async () => {
      const r = await mobileFetch(`${_SYS_API()}/system/embeddings/status`);
      return r.ok ? r.json() : null;
    },
    refetchInterval: 60_000,
    staleTime: 50_000,
  });

  const { data: nightshift } = useQuery<NightshiftStatusData | null>({
    queryKey: ['system', 'nightshift-status-dash'],
    queryFn: async () => {
      const r = await mobileFetch(`${_SYS_API()}/system/nightshift/status`);
      return r.ok ? r.json() : null;
    },
    refetchInterval: 60_000,
    staleTime: 50_000,
  });

  // ── Derived display values ──────────────────────────────────────────────────

  const aiStatus = health?.services.ai.status ?? 'unknown';
  const serverColor =
    aiStatus === 'ok' ? T.green : aiStatus === 'degraded' ? T.gilt : aiStatus === 'unknown' ? '#9ca3af' : T.rust;
  const serverDetail =
    aiStatus === 'ok' ? 'AI endpoint reachable' :
    aiStatus === 'degraded' ? 'AI endpoint degraded' :
    aiStatus === 'unavailable' ? 'AI endpoint unreachable' : 'Checking…';

  const embCircuit = embeddings?.circuit_open;
  const embColor = embCircuit == null ? '#9ca3af' : embCircuit ? T.gilt : T.green;
  const embDetail =
    embCircuit == null ? 'Checking…' :
    embCircuit ? 'Circuit open — keyword search only' : 'Online — semantic search active';

  const ns = nightshift;
  const lastRun = ns?.last_run;
  const nsRunning = ns?.running;
  const nsColor = lastRun ? T.green : nsRunning ? T.gilt : '#9ca3af';
  const nsDetail = nsRunning
    ? 'Running now…'
    : lastRun
      ? `${relTime(lastRun.ran_at)} · ${lastRun.docs_processed} docs, ${lastRun.items_added} items`
      : 'Never run';

  const dbStatus = health?.services.database.status ?? 'unknown';
  const dbColor =
    dbStatus === 'ok' ? T.green : dbStatus === 'degraded' ? T.gilt : dbStatus === 'unknown' ? '#9ca3af' : T.rust;
  const dbDetail = dbStatus === 'ok' ? 'Healthy' : dbStatus === 'unknown' ? 'Checking…' : dbStatus;

  const overallOk = aiStatus === 'ok' && !embCircuit && dbStatus === 'ok';
  const overallColor = health == null ? '#9ca3af' : overallOk ? T.green : T.gilt;

  return (
    <>
      <View style={[sysStyles.card, { backgroundColor: colors.card, borderColor: colors.border }]}>
        {/* Header row — toggles collapse */}
        <Pressable
          onPress={() => setCollapsed(c => !c)}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}
          hitSlop={6}
        >
          <StatusDot color={overallColor} />
          <Text
            style={{
              flex: 1,
              fontSize: 11,
              fontFamily: 'Inter_600SemiBold',
              color: colors.foreground,
              textTransform: 'uppercase',
              letterSpacing: 0.8,
            }}
          >
            System Health
          </Text>
          <Feather
            name={collapsed ? 'chevron-down' : 'chevron-up'}
            size={14}
            color={colors.mutedForeground}
          />
        </Pressable>

        {!collapsed && (
          <>
            <View style={{ marginTop: 10, gap: 0 }}>
              <StatusRow label="Server"     color={serverColor} detail={serverDetail} />
              <StatusRow label="Embeddings" color={embColor}    detail={embDetail}    />
              <StatusRow label="Nightshift" color={nsColor}     detail={nsDetail}     />
              <StatusRow label="Database"   color={dbColor}     detail={dbDetail}     />
            </View>

            <Pressable
              onPress={() => setSheetOpen(true)}
              style={({ pressed }) => [
                sysStyles.diagBtn,
                { borderColor: colors.border, backgroundColor: pressed ? colors.muted : 'transparent' },
              ]}
            >
              <Feather name="activity" size={13} color={colors.primary} />
              <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.primary }}>
                Run diagnostics
              </Text>
            </Pressable>
          </>
        )}
      </View>

      <DiagnosticsSheet visible={sheetOpen} onClose={() => setSheetOpen(false)} />
    </>
  );
}

// ── Styles for system health components ───────────────────────────────────────

const sysStyles = StyleSheet.create({
  card: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    marginBottom: 24,
  },
  diagBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    paddingVertical: 9,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 12,
  },
  // Sheet
  sheet: {
    position: 'absolute',
    bottom: 0, left: 0, right: 0,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    paddingTop: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.12,
    shadowRadius: 14,
    elevation: 24,
    maxHeight: '85%',
  },
  handle: {
    width: 36, height: 4, borderRadius: 2,
    alignSelf: 'center', marginBottom: 12,
  },
  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    marginBottom: 8,
  },
  summaryBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderBottomWidth: StyleSheet.hairlineWidth,
    marginBottom: 2,
  },
  checkRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    paddingVertical: 6,
    paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  retryBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 16, paddingVertical: 8,
    borderRadius: 8, borderWidth: 1,
  },
  runBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 7, paddingVertical: 11, borderRadius: 10,
  },
});

export default function DashboardScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';

  const {
    data: summary,
    isLoading: summaryLoading,
    isError: summaryError,
    refetch: refetchSummary,
  } = useGetDashboardSummary({ query: { refetchInterval: 30_000, staleTime: 20_000 } } as any);

  const {
    data: activityData,
    isLoading: activityLoading,
    isError: activityError,
    refetch: refetchActivity,
  } = useGetDashboardActivity({ limit: 10 }, { query: { refetchInterval: 30_000, staleTime: 20_000 } } as any);

  const { data: briefing } = useGetBriefing({ query: { staleTime: 300_000 } } as any);

  const isLoading = summaryLoading || activityLoading;
  const isError = summaryError || activityError;
  const recentWorks = summary?.recent_works ?? [];
  const activity = activityData?.activity ?? [];
  const hasData = recentWorks.length > 0 || activity.length > 0;

  const [showWorkspaceHealth, setShowWorkspaceHealth] = useState(false);
  const [aiSettingsOpen, setAiSettingsOpen] = useState(false);
  const { rendered: aiSettingsRendered, slideAnim: aiSettingsSlideAnim, fadeAnim: aiSettingsFadeAnim, panHandlers: aiSettingsPanHandlers } = useSheetAnimation(aiSettingsOpen, 300, () => setAiSettingsOpen(false));
  const [aiExtractionEnabled, setAiExtractionEnabled] = useState<boolean | null>(null);
  const [aiRerankEnabled, setAiRerankEnabled] = useState<boolean | null>(null);
  const [aiSettingsLoading, setAiSettingsLoading] = useState(false);

  const openAiSettings = async () => {
    setAiSettingsOpen(true);
    try {
      const domain = apiOrigin();
      const r = await mobileFetch(`${domain}/api/system/settings`);
      if (r.ok) {
        const d = await r.json();
        setAiExtractionEnabled(d.ai_extraction_enabled === 'true' || d.ai_extraction_enabled === true);
        setAiRerankEnabled(d.reranking_enabled === 'true' || d.reranking_enabled === true);
      }
    } catch { /* non-fatal */ }
  };

  const patchSetting = async (key: string, value: boolean) => {
    const domain = apiOrigin();
    try {
      await mobileFetch(`${domain}/api/system/settings`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      });
    } catch { /* non-fatal */ }
  };

  const topPad = isWeb ? 67 : 0;
  const botPad = isWeb ? 34 : 0;

  const handleRefresh = () => {
    refetchSummary();
    refetchActivity();
  };

  // Full-screen error when there's no cached data to show
  if (!isLoading && isError && !hasData) {
    return (
      <View style={[styles.container, { backgroundColor: colors.background, paddingTop: topPad + 16 }]}>
        <View style={styles.header}>
          <Text style={[styles.brand, { color: colors.foreground }]}>Orivellum</Text>
        </View>
        <ErrorScreen
          message="Can't reach your workspace"
          detail="Check your connection and make sure the Orivellum server is running."
          onRetry={handleRefresh}
        />
      </View>
    );
  }

  return (
    <>
    <FlatList
      style={[styles.container, { backgroundColor: colors.background }]}
      contentContainerStyle={{
        paddingTop: topPad + 16,
        paddingBottom: insets.bottom + 24,
        paddingHorizontal: 16,
      }}
      scrollEnabled
      keyboardShouldPersistTaps="handled"
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl
          refreshing={isLoading}
          onRefresh={handleRefresh}
          tintColor={colors.primary}
        />
      }
      data={[]}
      renderItem={null}
      ListHeaderComponent={
        <>
          {/* Header */}
          <View style={[styles.header, { flexDirection: 'row', alignItems: 'flex-start' }]}>
            <View style={{ flex: 1 }}>
              <Text style={[styles.brand, { color: colors.foreground }]}>Orivellum</Text>
              <Text style={[styles.subtitle, { color: colors.mutedForeground }]}>
                {briefing?.greeting ?? 'Your research workspace'}
              </Text>
            </View>
            <Pressable
              onPress={openAiSettings}
              hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              style={({ pressed }) => ({
                width: 34, height: 34, borderRadius: 17,
                alignItems: 'center', justifyContent: 'center',
                opacity: pressed ? 0.6 : 1, marginTop: 2,
              })}
            >
              <Feather name="settings" size={17} color={colors.mutedForeground} />
            </Pressable>
          </View>

          {/* Weather briefing card — location-aware ambient context */}
          <WeatherCard />

          {/* Server hardware summary */}
          <ServerHealthCard />

          {/* Studio quick action */}
          <StudioCard />

          {/* Review queue nudge — disappears when queue is empty */}
          <ReviewQueueTile />

          {/* Offline banner — shown when we have cached data but server is unreachable */}
          {isError && hasData && (
            <OfflineBanner
              message="Can't reach your workspace — showing cached data"
              onRetry={handleRefresh}
            />
          )}

          {/* Stats */}
          {summaryLoading ? (
            <>{[...Array(4)].map((_, i) => <SkeletonItem key={i} />)}</>
          ) : (
            <View style={styles.statsGrid}>
              <StatCard label="Works" value={summary?.work_count} icon="book-open" />
              <StatCard label="Docs" value={summary?.document_count} icon="file-text" />
              <StatCard label="Nodes" value={summary?.knowledge_count} icon="cpu" />
              <StatCard label="Chats" value={summary?.conversation_count} icon="message-circle" />
            </View>
          )}

          {/* Recent Works */}
          {recentWorks.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
                RECENT WORKS
              </Text>
              {recentWorks.map((w) => (
                <WorkRow key={w.id} work={w} />
              ))}
            </>
          )}

          {/* Activity */}
          {activity.length > 0 && (
            <>
              <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
                RECENT ACTIVITY
              </Text>
              {activity.map((item, i) => (
                <ActivityRow key={item.id ?? i} item={item} />
              ))}
            </>
          )}

          {!isLoading && !isError && recentWorks.length === 0 && activity.length === 0 && (
            <EmptyState
              icon="inbox"
              title="No activity yet"
              body="Create a work to get started."
            />
          )}

          {/* Workspace health — collapsed by default to keep the dashboard clean */}
          <Pressable
            onPress={() => setShowWorkspaceHealth(v => !v)}
            hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 24, marginBottom: 4 }}
          >
            <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
              WORKSPACE HEALTH
            </Text>
            <Feather
              name={showWorkspaceHealth ? 'chevron-up' : 'chevron-down'}
              size={12}
              color={colors.mutedForeground}
            />
          </Pressable>
          {showWorkspaceHealth && (
            <>
              <AutomationActivityCard />
              <SystemHealthCard />
            </>
          )}
        </>
      }
    />

    {/* AI Settings bottom sheet */}
    <Modal
      transparent
      visible={aiSettingsRendered}
      animationType="none"
      onRequestClose={() => setAiSettingsOpen(false)}
    >
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.35)', opacity: aiSettingsFadeAnim }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={() => setAiSettingsOpen(false)} />
      </Animated.View>
      <Animated.View {...aiSettingsPanHandlers} style={{ position: 'absolute', bottom: 0, left: 0, right: 0, backgroundColor: colors.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, borderTopWidth: 1, borderColor: colors.border, paddingHorizontal: 20, paddingTop: 16, paddingBottom: botPad + 24, transform: [{ translateY: aiSettingsSlideAnim }] }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 18 }}>
            <Feather name="settings" size={16} color={colors.primary} style={{ marginRight: 8 }} />
            <Text style={{ fontSize: 16, fontFamily: 'Inter_700Bold', color: colors.foreground, flex: 1 }}>AI Settings</Text>
            <Pressable onPress={() => setAiSettingsOpen(false)} hitSlop={8}>
              <Feather name="x" size={18} color={colors.mutedForeground} />
            </Pressable>
          </View>
          {[
            { label: 'AI Extraction', sub: 'Automatically harvest knowledge from imported documents', key: 'ai_extraction_enabled', value: aiExtractionEnabled, set: setAiExtractionEnabled },
            { label: 'Re-ranking', sub: 'Boost semantic search accuracy using a re-ranker model', key: 'reranking_enabled', value: aiRerankEnabled, set: setAiRerankEnabled },
          ].map((row, i) => (
            <View key={row.key} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 14, borderTopWidth: i === 0 ? 0 : StyleSheet.hairlineWidth, borderTopColor: colors.border }}>
              <View style={{ flex: 1, marginRight: 16 }}>
                <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>{row.label}</Text>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 2 }}>{row.sub}</Text>
              </View>
              <TouchableOpacity
                onPress={async () => {
                  const next = !row.value;
                  row.set(next);
                  await patchSetting(row.key, next);
                }}
                style={{
                  width: 46, height: 26, borderRadius: 13,
                  backgroundColor: row.value ? colors.primary : colors.muted,
                  borderWidth: 1, borderColor: row.value ? colors.primary : colors.border,
                  justifyContent: 'center', paddingHorizontal: 2,
                }}
                activeOpacity={0.8}
              >
                <View style={{
                  width: 20, height: 20, borderRadius: 10, backgroundColor: '#fff',
                  alignSelf: row.value ? 'flex-end' : 'flex-start',
                  shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 2, shadowOffset: { width: 0, height: 1 },
                }} />
              </TouchableOpacity>
            </View>
          ))}
      </Animated.View>
    </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: { marginBottom: 24 },
  brand: { fontSize: 28, ...font('bold'), letterSpacing: -0.5 },
  subtitle: { fontSize: 15, ...font('regular'), lineHeight: 20, marginTop: 2 },
  loader: { marginVertical: 24 },
  // Review queue tile
  reviewTile: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    gap: 12,
    marginBottom: 14,
    minHeight: 44,
  },
  reviewTileIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    position: 'relative',
  },
  reviewTileBadge: {
    position: 'absolute',
    top: -4,
    right: -4,
    minWidth: 16,
    height: 16,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 3,
  },
  reviewTileBadgeText: {
    fontSize: 9,
    fontFamily: 'Inter_700Bold',
    color: '#fff',
    lineHeight: 11,
  },
  reviewTileTitle: {
    fontSize: 14,
    fontFamily: 'Inter_600SemiBold',
  },
  reviewTileSub: {
    fontSize: 12,
    fontFamily: 'Inter_400Regular',
    marginTop: 2,
  },

  studioCard: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    gap: 12,
    marginBottom: 24,
    minHeight: 44,
  },
  studioIcon: {
    width: 40,
    height: 40,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  studioTitle: { fontSize: 15, ...font('semibold'), lineHeight: 20, marginBottom: 2 },
  studioSub: { fontSize: 13, ...font('regular'), lineHeight: 18 },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 28,
  },
  statCard: {
    flex: 1,
    minWidth: '44%',
    borderRadius: 6,
    borderWidth: 1,
    padding: 14,
    alignItems: 'flex-start',
    gap: 6,
  },
  statValue: { fontSize: 24, ...font('bold') },
  statLabel: { fontSize: 11, ...font('medium'), lineHeight: 14, textTransform: 'uppercase', letterSpacing: 0.5 },
  sectionLabel: {
    fontSize: 11,
    ...font('semibold'),
    letterSpacing: 1,
    lineHeight: 14,
    textTransform: 'uppercase',
    marginBottom: 10,
    marginTop: 24,
  },
  workRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 6,
    borderWidth: 1,
    padding: 14,
    marginBottom: 8,
    minHeight: 44,
  },
  workRowLeft: { flex: 1, marginRight: 8 },
  workTitle: { fontSize: 15, ...font('semibold'), lineHeight: 20, marginBottom: 3 },
  workMeta: { fontSize: 13, ...font('regular'), lineHeight: 18 },
  activityRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderBottomWidth: 1,
    paddingVertical: 11,
    gap: 10,
    minHeight: 44,
  },
  activityIcon: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  activityLabel: { flex: 1, fontSize: 13, ...font('regular'), lineHeight: 18 },
  activityDate: { fontSize: 12, ...font('regular'), lineHeight: 16 },
  emptyState: {
    alignItems: 'center',
    paddingVertical: 60,
    gap: 12,
  },
  emptyText: {
    fontSize: 15,
    ...font('regular'),
    textAlign: 'center',
    lineHeight: 22,
  },
  hwCard: {
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    marginBottom: 16,
  },
});
