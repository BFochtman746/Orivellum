import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
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
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { mobileFetch } from '@/lib/api';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { font } from '@/lib/typography';
import { getApiToken } from '@/lib/token';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API = `https://${DOMAIN}/api`;

// ── Types ──────────────────────────────────────────────────────────────────────

interface ActionDef {
  name: string;
  description: string;
  category: string;
  input_schema: {
    properties?: Record<string, { description?: string; type?: string }>;
    required?: string[];
  };
}

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

interface Work {
  id: string;
  title?: string | null;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function relTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const sec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (isNaN(sec) || sec < 0) return '—';
  if (sec < 60) return 'just now';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

function actionTitle(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

const CATEGORY_ICON: Record<string, string> = {
  finance:  'dollar-sign',
  export:   'download',
  generate: 'file-text',
  learn:    'award',
  general:  'zap',
};

function categoryIcon(cat: string): string {
  return CATEGORY_ICON[cat] ?? 'zap';
}

// ── Run status ─────────────────────────────────────────────────────────────────

function RunStatusIcon({ status, colors }: { status: ActionRun['status']; colors: ReturnType<typeof useColors> }) {
  if (status === 'running') return <ActivityIndicator size="small" color={colors.primary} />;
  if (status === 'done')    return <Feather name="check-circle" size={15} color="#22c55e" />;
  return <Feather name="x-circle" size={15} color="#ef4444" />;
}

// ── Share output file ──────────────────────────────────────────────────────────

async function shareOutput(outputPath: string, label: string | null) {
  const url = `${API}/studio/outputs/serve?path=${encodeURIComponent(outputPath)}`;
  if (Platform.OS === 'web') {
    const resp = await mobileFetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = label ?? `orivellum_output_${Date.now()}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(href), 10_000);
    return;
  }
  const FileSystem = await import('expo-file-system/legacy');
  const Sharing = await import('expo-sharing');
  const token = getApiToken();
  const ext = outputPath.split('.').pop() ?? 'bin';
  const safeName = (label ?? `orivellum_${Date.now()}`).replace(/[^a-zA-Z0-9._-]/g, '_') + '.' + ext;
  const dest = `${FileSystem.cacheDirectory}${safeName}`;
  await FileSystem.deleteAsync(dest, { idempotent: true });
  const dl = await FileSystem.downloadAsync(url, dest, {
    headers: token ? { authorization: `Bearer ${token}` } : undefined,
  });
  if (dl.status !== 200) throw new Error(`Download failed (HTTP ${dl.status})`);
  const canShare = await Sharing.isAvailableAsync();
  if (canShare) {
    await Sharing.shareAsync(dl.uri, { dialogTitle: label ?? 'Action output' });
  } else {
    Alert.alert('Share unavailable', 'Sharing is not supported on this platform.');
  }
  FileSystem.deleteAsync(dest, { idempotent: true }).catch(() => {});
}

// ── Share button ───────────────────────────────────────────────────────────────

function ShareOutputButton({ run, colors }: { run: ActionRun; colors: ReturnType<typeof useColors> }) {
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle');
  const handleShare = async () => {
    if (state === 'busy' || !run.output_path) return;
    setState('busy');
    try {
      await shareOutput(run.output_path, run.output_label);
      setState('done');
      setTimeout(() => setState('idle'), 2500);
    } catch (e: any) {
      setState('idle');
      Alert.alert('Download failed', e?.message ?? 'Could not download output');
    }
  };
  return (
    <Pressable onPress={handleShare} hitSlop={8} style={[st.runBtn, { borderColor: colors.border }]} disabled={state === 'busy'}>
      {state === 'busy'
        ? <ActivityIndicator size="small" color={colors.primary} />
        : <Feather name={state === 'done' ? 'check' : 'download'} size={13} color={state === 'done' ? '#22c55e' : colors.primary} />}
      <Text style={[st.runBtnLabel, { color: state === 'done' ? '#22c55e' : colors.primary }]}>
        {state === 'done' ? 'Done' : Platform.OS === 'web' ? 'Download' : 'Share'}
      </Text>
    </Pressable>
  );
}

// ── Run row ────────────────────────────────────────────────────────────────────

function RunRow({
  run,
  colors,
  onRetry,
}: {
  run: ActionRun;
  colors: ReturnType<typeof useColors>;
  onRetry: (runId: string) => void;
}) {
  return (
    <View style={[st.runRow, { borderBottomColor: colors.border }]}>
      <View style={{ width: 20, alignItems: 'center', paddingTop: 1 }}>
        <RunStatusIcon status={run.status} colors={colors} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={[st.runName, { color: colors.foreground }]} numberOfLines={1}>
          {actionTitle(run.action_name)}
        </Text>
        {run.status === 'error' && run.error && (
          <Text style={[st.runDetail, { color: '#ef4444' }]} numberOfLines={2}>{run.error}</Text>
        )}
        {run.status !== 'error' && run.output_label && (
          <Text style={[st.runDetail, { color: colors.mutedForeground }]} numberOfLines={1}>{run.output_label}</Text>
        )}
        {run.status === 'running' && (
          <Text style={[st.runDetail, { color: colors.mutedForeground }]}>Running…</Text>
        )}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        {run.status === 'done' && run.output_path && (
          <ShareOutputButton run={run} colors={colors} />
        )}
        {run.status === 'error' && (
          <Pressable onPress={() => onRetry(run.id)} hitSlop={8} style={[st.runBtn, { borderColor: colors.border }]}>
            <Feather name="refresh-cw" size={13} color={colors.primary} />
            <Text style={[st.runBtnLabel, { color: colors.primary }]}>Retry</Text>
          </Pressable>
        )}
        <Text style={[st.runTime, { color: colors.mutedForeground }]}>
          {relTime(run.completed_at ?? run.created_at)}
        </Text>
      </View>
    </View>
  );
}

// ── Work picker bottom sheet ───────────────────────────────────────────────────

function WorkPickerSheet({
  visible,
  works,
  selectedId,
  onSelect,
  onClose,
}: {
  visible: boolean;
  works: Work[];
  selectedId: string;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const [rendered, setRendered] = useState(false);
  const slideAnim = useRef(new Animated.Value(500)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      setRendered(true);
      Animated.parallel([
        Animated.spring(slideAnim, { toValue: 0, useNativeDriver: true, tension: 85, friction: 13 }),
        Animated.timing(fadeAnim, { toValue: 1, duration: 180, useNativeDriver: true }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, { toValue: 500, duration: 220, useNativeDriver: true }),
        Animated.timing(fadeAnim, { toValue: 0, duration: 180, useNativeDriver: true }),
      ]).start(() => setRendered(false));
    }
  }, [visible]);

  if (!rendered) return null;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose} statusBarTranslucent>
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.42)', opacity: fadeAnim }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>
      <Animated.View style={[st.sheet, { backgroundColor: colors.card, borderColor: colors.border, paddingBottom: insets.bottom + 16, transform: [{ translateY: slideAnim }] }]}>
        <View style={[st.sheetHandle, { backgroundColor: colors.border }]} />
        <Text style={[st.sheetTitle, { color: colors.foreground }]}>Select a Work</Text>
        <ScrollView>
          {works.map((w) => {
            const active = w.id === selectedId;
            return (
              <Pressable
                key={w.id}
                onPress={() => { onSelect(w.id); onClose(); }}
                style={({ pressed }) => [
                  st.workPickerRow,
                  {
                    backgroundColor: active ? colors.primary + '18' : pressed ? colors.muted : 'transparent',
                    borderColor: active ? colors.primary + '44' : colors.border,
                  },
                ]}
              >
                <Feather name="book-open" size={15} color={active ? colors.primary : colors.mutedForeground} />
                <Text style={[st.workPickerLabel, { color: active ? colors.primary : colors.foreground, ...font(active ? 'semibold' : 'regular') }]} numberOfLines={1}>
                  {w.title ?? w.id}
                </Text>
                {active && <Feather name="check" size={14} color={colors.primary} />}
              </Pressable>
            );
          })}
          {works.length === 0 && (
            <Text style={[st.emptyText, { color: colors.mutedForeground }]}>No Works found.</Text>
          )}
        </ScrollView>
      </Animated.View>
    </Modal>
  );
}

// ── Action input / preview / execute sheet ─────────────────────────────────────

function ActionSheet({
  action,
  works,
  visible,
  onClose,
  onExecuted,
}: {
  action: ActionDef | null;
  works: Work[];
  visible: boolean;
  onClose: () => void;
  onExecuted: () => void;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const [rendered, setRendered] = useState(false);
  const slideAnim = useRef(new Animated.Value(600)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // Input state
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [previewMsg, setPreviewMsg] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [workPickerOpen, setWorkPickerOpen] = useState(false);

  // Reset when action changes
  useEffect(() => {
    if (action) {
      setInputs({});
      setPreviewMsg(null);
    }
  }, [action?.name]);

  useEffect(() => {
    if (visible) {
      setRendered(true);
      Animated.parallel([
        Animated.spring(slideAnim, { toValue: 0, useNativeDriver: true, tension: 85, friction: 13 }),
        Animated.timing(fadeAnim, { toValue: 1, duration: 180, useNativeDriver: true }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, { toValue: 600, duration: 220, useNativeDriver: true }),
        Animated.timing(fadeAnim, { toValue: 0, duration: 180, useNativeDriver: true }),
      ]).start(() => setRendered(false));
    }
  }, [visible]);

  if (!rendered || !action) return null;

  const props = action.input_schema?.properties ?? {};
  const required: string[] = action.input_schema?.required ?? [];
  const needsWork = required.includes('work_id');
  const textFields = required.filter((k) => k !== 'work_id');

  const selectedWork = works.find((w) => w.id === inputs['work_id']);
  const missingWork = needsWork && !inputs['work_id'];
  const missingText = textFields.filter((k) => !inputs[k]);
  const canRun = !missingWork && missingText.length === 0;

  const handlePreview = async () => {
    setPreviewing(true);
    setPreviewMsg(null);
    try {
      const r = await mobileFetch(`${API}/actions/${action.name}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(inputs),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setPreviewMsg(d.confirm_message ?? 'Ready to run.');
    } catch {
      setPreviewMsg('Could not load preview — tap Run to proceed anyway.');
    } finally {
      setPreviewing(false);
    }
  };

  const handleExecute = async () => {
    setExecuting(true);
    try {
      const r = await mobileFetch(`${API}/actions/${action.name}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(inputs),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Action failed' }));
        throw new Error(err.detail ?? 'Action failed');
      }
      const result = await r.json();
      onClose();
      onExecuted();
      Alert.alert(
        'Action complete',
        result.output_label ?? result.summary ?? 'Done! Check Recent Runs for output.',
      );
    } catch (e: any) {
      Alert.alert('Action failed', e?.message ?? 'Unknown error');
    } finally {
      setExecuting(false);
    }
  };

  return (
    <>
      <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose} statusBarTranslucent>
        <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.42)', opacity: fadeAnim }]}>
          <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        </Animated.View>

        <Animated.View
          style={[
            st.actionSheet,
            {
              backgroundColor: colors.card,
              borderColor: colors.border,
              paddingBottom: insets.bottom + 20,
              transform: [{ translateY: slideAnim }],
            },
          ]}
        >
          <View style={[st.sheetHandle, { backgroundColor: colors.border }]} />

          {/* Header */}
          <View style={st.actionSheetHeader}>
            <View style={[st.actionIconWrap, { backgroundColor: colors.primary + '18' }]}>
              <Feather name={categoryIcon(action.category) as any} size={20} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[st.actionSheetTitle, { color: colors.foreground }]}>
                {actionTitle(action.name)}
              </Text>
              <Text style={[st.actionSheetCat, { color: colors.mutedForeground }]}>
                {action.category}
              </Text>
            </View>
            <Pressable onPress={onClose} hitSlop={10}>
              <Feather name="x" size={20} color={colors.mutedForeground} />
            </Pressable>
          </View>

          <Text style={[st.actionSheetDesc, { color: colors.mutedForeground }]}>
            {action.description}
          </Text>

          <ScrollView style={{ flex: 1 }} keyboardShouldPersistTaps="handled">
            {/* Work picker */}
            {needsWork && (
              <View style={st.fieldRow}>
                <Text style={[st.fieldLabel, { color: colors.mutedForeground }]}>Work *</Text>
                <Pressable
                  onPress={() => setWorkPickerOpen(true)}
                  style={[
                    st.workPickerBtn,
                    {
                      borderColor: missingWork ? '#f59e0b' : colors.border,
                      backgroundColor: colors.background,
                    },
                  ]}
                >
                  <Feather name="book-open" size={14} color={selectedWork ? colors.primary : colors.mutedForeground} />
                  <Text
                    style={[
                      st.workPickerBtnLabel,
                      { color: selectedWork ? colors.foreground : colors.mutedForeground },
                    ]}
                    numberOfLines={1}
                  >
                    {selectedWork?.title ?? selectedWork?.id ?? 'Select a Work…'}
                  </Text>
                  <Feather name="chevron-down" size={14} color={colors.mutedForeground} />
                </Pressable>
              </View>
            )}

            {/* Text inputs for other required fields */}
            {textFields.map((key) => {
              const fieldSchema = props[key] ?? {};
              return (
                <View key={key} style={st.fieldRow}>
                  <Text style={[st.fieldLabel, { color: colors.mutedForeground }]}>
                    {key} *
                  </Text>
                  <TextInput
                    style={[st.textInput, { borderColor: inputs[key] ? colors.border : '#f59e0b', color: colors.foreground, backgroundColor: colors.background }]}
                    placeholder={fieldSchema.description ?? key}
                    placeholderTextColor={colors.mutedForeground}
                    value={inputs[key] ?? ''}
                    onChangeText={(v) => setInputs((prev) => ({ ...prev, [key]: v }))}
                    returnKeyType="done"
                  />
                </View>
              );
            })}

            {/* Validation hint */}
            {!canRun && (
              <View style={[st.validationHint, { backgroundColor: '#f59e0b18', borderColor: '#f59e0b44' }]}>
                <Feather name="alert-circle" size={13} color="#f59e0b" />
                <Text style={{ fontSize: 12, ...font('regular'), color: '#f59e0b', flex: 1 }}>
                  {missingWork ? 'Select a Work to continue.' : `Fill in: ${missingText.join(', ')}`}
                </Text>
              </View>
            )}

            {/* Preview message */}
            {previewMsg && (
              <View style={[st.previewBox, { backgroundColor: colors.muted, borderColor: colors.border }]}>
                <Feather name="info" size={13} color={colors.mutedForeground} />
                <Text style={[st.previewText, { color: colors.foreground }]}>{previewMsg}</Text>
              </View>
            )}

            {/* Action buttons */}
            <View style={st.actionBtns}>
              {!previewMsg && (
                <Pressable
                  onPress={handlePreview}
                  disabled={!canRun || previewing}
                  style={({ pressed }) => [
                    st.previewBtn,
                    {
                      borderColor: colors.border,
                      opacity: (!canRun || previewing) ? 0.45 : pressed ? 0.7 : 1,
                    },
                  ]}
                >
                  {previewing
                    ? <ActivityIndicator size="small" color={colors.primary} />
                    : <Feather name="eye" size={15} color={colors.primary} />}
                  <Text style={[st.previewBtnLabel, { color: colors.primary }]}>
                    {previewing ? 'Loading…' : 'Preview'}
                  </Text>
                </Pressable>
              )}
              <Pressable
                onPress={handleExecute}
                disabled={!canRun || executing}
                style={({ pressed }) => [
                  st.runActionBtn,
                  {
                    backgroundColor: canRun && !executing ? colors.primary : colors.muted,
                    opacity: executing ? 0.7 : pressed ? 0.85 : 1,
                    flex: previewMsg ? 1 : undefined,
                  },
                ]}
              >
                {executing
                  ? <ActivityIndicator size="small" color="#fff" />
                  : <Feather name="zap" size={15} color={canRun ? '#fff' : colors.mutedForeground} />}
                <Text style={[st.runActionBtnLabel, { color: canRun && !executing ? '#fff' : colors.mutedForeground }]}>
                  {executing ? 'Running…' : 'Run'}
                </Text>
              </Pressable>
            </View>
          </ScrollView>
        </Animated.View>
      </Modal>

      <WorkPickerSheet
        visible={workPickerOpen}
        works={works}
        selectedId={inputs['work_id'] ?? ''}
        onSelect={(id) => setInputs((prev) => ({ ...prev, work_id: id }))}
        onClose={() => setWorkPickerOpen(false)}
      />
    </>
  );
}

// ── Action card ────────────────────────────────────────────────────────────────

function ActionCard({
  action,
  colors,
  onRun,
}: {
  action: ActionDef;
  colors: ReturnType<typeof useColors>;
  onRun: () => void;
}) {
  const required: string[] = action.input_schema?.required ?? [];
  const hasInputs = required.length > 0;

  return (
    <View style={[st.actionCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
      <View style={st.actionCardHeader}>
        <View style={[st.actionCardIconWrap, { backgroundColor: colors.primary + '18' }]}>
          <Feather name={categoryIcon(action.category) as any} size={17} color={colors.primary} />
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={[st.actionCardTitle, { color: colors.foreground }]} numberOfLines={1}>
            {actionTitle(action.name)}
          </Text>
          <View style={[st.catBadge, { backgroundColor: colors.muted, borderColor: colors.border }]}>
            <Text style={[st.catBadgeText, { color: colors.mutedForeground }]}>{action.category}</Text>
          </View>
        </View>
        <Pressable
          onPress={onRun}
          style={({ pressed }) => [
            st.runChip,
            { backgroundColor: colors.primary, opacity: pressed ? 0.75 : 1 },
          ]}
          accessibilityRole="button"
          accessibilityLabel={`Run ${actionTitle(action.name)}`}
        >
          <Feather name="zap" size={12} color="#fff" />
          <Text style={st.runChipLabel}>{hasInputs ? 'Set up' : 'Run'}</Text>
        </Pressable>
      </View>
      <Text style={[st.actionCardDesc, { color: colors.mutedForeground }]} numberOfLines={2}>
        {action.description}
      </Text>
    </View>
  );
}

// ── Main screen ────────────────────────────────────────────────────────────────

export default function ActionsScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();

  // Catalog
  const [actions, setActions] = useState<ActionDef[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState('');

  // Runs
  const [runs, setRuns] = useState<ActionRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runCount, setRunCount] = useState(0);

  // Works (for action input forms)
  const [works, setWorks] = useState<Work[]>([]);

  // Action sheet
  const [selectedAction, setSelectedAction] = useState<ActionDef | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  // Polling
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Fetch catalog ────────────────────────────────────────────────────────────
  const fetchCatalog = useCallback(async () => {
    try {
      const r = await mobileFetch(`${API}/actions`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setActions(d.actions ?? []);
      setCatalogError('');
    } catch (e: any) {
      setCatalogError(e?.message ?? 'Could not load actions');
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  // ── Fetch runs ───────────────────────────────────────────────────────────────
  const fetchRuns = useCallback(async () => {
    try {
      const r = await mobileFetch(`${API}/actions/runs?limit=20`);
      if (!r.ok) return;
      const d = await r.json();
      setRuns(d.runs ?? []);
      setRunCount(d.count ?? 0);
    } catch {
      // silent
    } finally {
      setRunsLoading(false);
    }
  }, []);

  // ── Fetch works ──────────────────────────────────────────────────────────────
  const fetchWorks = useCallback(async () => {
    try {
      const r = await mobileFetch(`${API}/works`);
      if (!r.ok) return;
      const d = await r.json();
      setWorks(d.works ?? []);
    } catch {
      // silent
    }
  }, []);

  // ── Initial load ─────────────────────────────────────────────────────────────
  useEffect(() => {
    fetchCatalog();
    fetchRuns();
    fetchWorks();
  }, [fetchCatalog, fetchRuns, fetchWorks]);

  // ── Poll runs every 3 s while any run is "running" ────────────────────────────
  useEffect(() => {
    const hasRunning = runs.some((r) => r.status === 'running');
    if (hasRunning) {
      if (!pollRef.current) {
        pollRef.current = setInterval(fetchRuns, 3_000);
      }
    } else {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [runs, fetchRuns]);

  // ── Retry ─────────────────────────────────────────────────────────────────────
  const handleRetry = useCallback(async (runId: string) => {
    try {
      const r = await mobileFetch(`${API}/actions/runs/${runId}/retry`, { method: 'POST' });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Retry failed' }));
        throw new Error(err.detail ?? 'Retry failed');
      }
      await fetchRuns();
      Alert.alert('Retried', 'Action re-executed. Check the runs list for the result.');
    } catch (e: any) {
      Alert.alert('Retry failed', e?.message ?? 'Unknown error');
    }
  }, [fetchRuns]);

  // ── Open action sheet ─────────────────────────────────────────────────────────
  const handleRunAction = (action: ActionDef) => {
    setSelectedAction(action);
    setSheetOpen(true);
  };

  // ── Refresh ───────────────────────────────────────────────────────────────────
  const [refreshing, setRefreshing] = useState(false);
  const handleRefresh = async () => {
    setRefreshing(true);
    await Promise.all([fetchCatalog(), fetchRuns(), fetchWorks()]);
    setRefreshing(false);
  };

  // ── Group actions by category ─────────────────────────────────────────────────
  const categories = Array.from(new Set(actions.map((a) => a.category)));

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={[
          st.container,
          { paddingBottom: insets.bottom + 24 },
        ]}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={colors.primary} />
        }
      >
        {/* ── Catalog ─────────────────────────────────────────────────────── */}
        <View style={st.sectionHeader}>
          <Feather name="zap" size={16} color={colors.primary} />
          <Text style={[st.sectionTitle, { color: colors.foreground }]}>Actions</Text>
        </View>
        <Text style={[st.sectionSub, { color: colors.mutedForeground }]}>
          Typed, grounded automations — each shows a preview before running and saves its output to your Library.
        </Text>

        {catalogLoading ? (
          <View style={st.loadingWrap}>
            <ActivityIndicator size="large" color={colors.primary} />
          </View>
        ) : catalogError ? (
          <View style={[st.emptyCard, { borderColor: colors.border }]}>
            <Feather name="alert-circle" size={20} color="#ef4444" />
            <Text style={[st.emptyText, { color: '#ef4444' }]}>{catalogError}</Text>
            <Pressable onPress={fetchCatalog} style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
              <Text style={[st.retryLink, { color: colors.primary }]}>Retry</Text>
            </Pressable>
          </View>
        ) : actions.length === 0 ? (
          <View style={[st.emptyCard, { borderColor: colors.border }]}>
            <Feather name="zap" size={20} color={colors.mutedForeground} />
            <Text style={[st.emptyText, { color: colors.mutedForeground }]}>No actions registered.</Text>
          </View>
        ) : (
          categories.map((cat) => (
            <View key={cat} style={st.categorySection}>
              <Text style={[st.categoryLabel, { color: colors.mutedForeground }]}>
                {cat.toUpperCase()}
              </Text>
              {actions.filter((a) => a.category === cat).map((action) => (
                <ActionCard
                  key={action.name}
                  action={action}
                  colors={colors}
                  onRun={() => handleRunAction(action)}
                />
              ))}
            </View>
          ))
        )}

        {/* ── Recent runs ──────────────────────────────────────────────────── */}
        <View style={[st.sectionHeader, { marginTop: 28 }]}>
          <Feather name="clock" size={16} color={colors.primary} />
          <Text style={[st.sectionTitle, { color: colors.foreground }]}>Recent Runs</Text>
          <View style={{ flex: 1 }} />
          <Pressable onPress={fetchRuns} hitSlop={10}>
            <Text style={[st.refreshLink, { color: colors.mutedForeground }]}>
              {runCount} total · refresh
            </Text>
          </Pressable>
        </View>

        {runsLoading ? (
          <View style={st.loadingWrap}>
            <ActivityIndicator color={colors.primary} />
          </View>
        ) : runs.length === 0 ? (
          <View style={[st.emptyCard, { borderColor: colors.border }]}>
            <Feather name="clock" size={20} color={colors.mutedForeground} />
            <Text style={[st.emptyText, { color: colors.mutedForeground }]}>
              No runs yet — tap an action above to get started.
            </Text>
          </View>
        ) : (
          <View style={[st.runsCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            {runs.map((run, i) => (
              <RunRow
                key={run.id}
                run={run}
                colors={colors}
                onRetry={handleRetry}
              />
            ))}
          </View>
        )}
      </ScrollView>

      {/* ── Action input / execute sheet ──────────────────────────────────── */}
      <ActionSheet
        action={selectedAction}
        works={works}
        visible={sheetOpen}
        onClose={() => setSheetOpen(false)}
        onExecuted={() => {
          setSheetOpen(false);
          setTimeout(fetchRuns, 800);
        }}
      />
    </View>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const st = StyleSheet.create({
  container: {
    padding: 16,
    gap: 12,
  },

  // Section headers
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 4,
  },
  sectionTitle: {
    fontSize: 18,
    ...font('semibold'),
  },
  sectionSub: {
    fontSize: 13,
    ...font('regular'),
    lineHeight: 19,
    marginBottom: 12,
  },
  categorySection: {
    gap: 8,
    marginBottom: 4,
  },
  categoryLabel: {
    fontSize: 10,
    ...font('semibold'),
    letterSpacing: 1,
    paddingLeft: 2,
  },

  // Action cards
  actionCard: {
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 14,
    gap: 8,
  },
  actionCardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  actionCardIconWrap: {
    width: 36,
    height: 36,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionCardTitle: {
    fontSize: 14,
    ...font('semibold'),
    marginBottom: 3,
  },
  catBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 5,
    borderWidth: StyleSheet.hairlineWidth,
  },
  catBadgeText: {
    fontSize: 10,
    ...font('medium'),
  },
  runChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 11,
    paddingVertical: 7,
    borderRadius: 8,
  },
  runChipLabel: {
    fontSize: 12,
    ...font('semibold'),
    color: '#fff',
  },
  actionCardDesc: {
    fontSize: 12,
    ...font('regular'),
    lineHeight: 18,
  },

  // Run rows
  runsCard: {
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: 'hidden',
  },
  runRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  runName: {
    fontSize: 13,
    ...font('medium'),
  },
  runDetail: {
    fontSize: 11,
    ...font('regular'),
    lineHeight: 16,
    marginTop: 2,
  },
  runTime: {
    fontSize: 10,
    ...font('regular'),
  },
  runBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 7,
    borderWidth: StyleSheet.hairlineWidth,
  },
  runBtnLabel: {
    fontSize: 11,
    ...font('medium'),
  },

  // Empty / loading states
  loadingWrap: {
    paddingVertical: 32,
    alignItems: 'center',
  },
  emptyCard: {
    borderWidth: StyleSheet.hairlineWidth,
    borderStyle: 'dashed',
    borderRadius: 12,
    paddingVertical: 32,
    alignItems: 'center',
    gap: 10,
  },
  emptyText: {
    fontSize: 13,
    ...font('regular'),
    textAlign: 'center',
    paddingHorizontal: 24,
  },
  retryLink: {
    fontSize: 13,
    ...font('semibold'),
  },
  refreshLink: {
    fontSize: 12,
    ...font('regular'),
  },

  // Bottom sheets (shared)
  sheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 16,
    paddingTop: 8,
    maxHeight: '80%',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.12,
    shadowRadius: 14,
    elevation: 24,
  },
  sheetHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    alignSelf: 'center',
    marginBottom: 16,
    marginTop: 6,
  },
  sheetTitle: {
    fontSize: 16,
    ...font('semibold'),
    marginBottom: 12,
  },

  // Work picker sheet
  workPickerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 13,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    marginBottom: 6,
  },
  workPickerLabel: {
    fontSize: 14,
    flex: 1,
  },

  // Action sheet
  actionSheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 16,
    paddingTop: 8,
    maxHeight: '90%',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.14,
    shadowRadius: 16,
    elevation: 28,
  },
  actionSheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginBottom: 6,
  },
  actionIconWrap: {
    width: 44,
    height: 44,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionSheetTitle: {
    fontSize: 16,
    ...font('semibold'),
  },
  actionSheetCat: {
    fontSize: 11,
    ...font('medium'),
    marginTop: 1,
  },
  actionSheetDesc: {
    fontSize: 13,
    ...font('regular'),
    lineHeight: 19,
    marginBottom: 16,
  },

  // Input fields
  fieldRow: {
    gap: 6,
    marginBottom: 14,
  },
  fieldLabel: {
    fontSize: 11,
    ...font('semibold'),
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  },
  textInput: {
    height: 44,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 14,
    fontSize: 14,
    ...font('regular'),
  },
  workPickerBtn: {
    height: 44,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  workPickerBtnLabel: {
    flex: 1,
    fontSize: 14,
    ...font('regular'),
  },
  validationHint: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    padding: 12,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    marginBottom: 14,
  },
  previewBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    padding: 12,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    marginBottom: 14,
  },
  previewText: {
    fontSize: 13,
    ...font('regular'),
    lineHeight: 18,
    flex: 1,
  },

  // Action buttons in sheet
  actionBtns: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 8,
  },
  previewBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 13,
  },
  previewBtnLabel: {
    fontSize: 14,
    ...font('semibold'),
  },
  runActionBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 13,
  },
  runActionBtnLabel: {
    fontSize: 14,
    ...font('semibold'),
  },
});
