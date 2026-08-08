/**
 * Forge project detail — pipeline view.
 *
 * Shows the current pipeline stage (Plan → Design → Build → Verify),
 * approve/reject buttons when a job is awaiting approval, a live event log,
 * and job history with build preview links.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Linking,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { font, fontSerif } from '@/lib/typography';
import * as WebBrowser from 'expo-web-browser';

// ── Types ──────────────────────────────────────────────────────────────────────

interface ForgeJob {
  id: string;
  type: string;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  build_dir?: string | null;
  meta_data?: Record<string, any>;
}

interface ForgeEvent {
  id: string;
  kind: string;
  message: string;
  created_at: string;
}

interface ForgeProjectDetail {
  id: string;
  name: string;
  brief?: string | null;
  status: string;
  work_title?: string | null;
  jobs: ForgeJob[];
  latest_job?: ForgeJob | null;
}

// ── Pipeline step ──────────────────────────────────────────────────────────────

const PIPELINE_STEPS = [
  { type: 'PLAN',   icon: 'map', label: 'Plan' },
  { type: 'DESIGN', icon: 'pen-tool', label: 'Design' },
  { type: 'BUILD',  icon: 'code', label: 'Build' },
  { type: 'VERIFY', icon: 'check-circle', label: 'Verify' },
];

function PipelineStepper({ jobs, currentStatus }: { jobs: ForgeJob[]; currentStatus: string }) {
  const colors = useColors();
  const T = useVellumTokens();
  const completedTypes = new Set(jobs.filter(j => j.status === 'complete').map(j => j.type));
  const runningJob = jobs.find(j => ['running', 'awaiting_approval'].includes(j.status));

  return (
    <View style={styles.stepper}>
      {PIPELINE_STEPS.map((step, i) => {
        const isDone = completedTypes.has(step.type);
        const isRunning = runningJob?.type === step.type;
        const isWaiting = runningJob?.type === step.type && runningJob?.status === 'awaiting_approval';
        const color = isDone ? T.green : isWaiting ? T.gilt : isRunning ? colors.primary : colors.border;

        return (
          <React.Fragment key={step.type}>
            <View style={styles.stepItem}>
              <View style={[
                styles.stepCircle,
                {
                  backgroundColor: isDone ? T.green : isWaiting ? T.giltSoft : isRunning ? colors.primary + '22' : colors.muted,
                  borderColor: color,
                },
              ]}>
                {isRunning && !isWaiting
                  ? <ActivityIndicator size="small" color={colors.primary} />
                  : <Feather
                      name={isDone ? 'check' : (step.icon as any)}
                      size={14}
                      color={isDone ? '#fff' : isWaiting ? T.gilt : isRunning ? colors.primary : colors.mutedForeground}
                    />
                }
              </View>
              <Text style={[styles.stepLabel, { color: isDone ? T.green : isRunning ? colors.primary : colors.mutedForeground }]}>
                {step.label}
              </Text>
              {isWaiting && (
                <Text style={[styles.stepReview, { color: T.gilt }]}>Review</Text>
              )}
            </View>
            {i < PIPELINE_STEPS.length - 1 && (
              <View style={[styles.stepConnector, { backgroundColor: isDone ? T.green : colors.border }]} />
            )}
          </React.Fragment>
        );
      })}
    </View>
  );
}

// ── Event log ─────────────────────────────────────────────────────────────────

function EventLog({ jobId }: { jobId: string }) {
  const colors = useColors();
  const domain = process.env.EXPO_PUBLIC_DOMAIN;
  const apiBase = domain ? `https://${domain}/api` : 'http://localhost:8000/api';

  const { data } = useQuery<{ events: ForgeEvent[] }>({
    queryKey: ['mobile', 'forge', 'events', jobId],
    queryFn: () => mobileFetch(`/api/forge/jobs/${jobId}/events`).then(r => r.json()),
    staleTime: 3_000,
    refetchInterval: 5_000,
  });

  const events = data?.events ?? [];
  if (!events.length) return null;

  return (
    <View style={[styles.eventLog, { backgroundColor: '#0f172a', borderColor: colors.border }]}>
      <Text style={styles.eventLogTitle}>Event log</Text>
      {events.slice(-20).map(e => (
        <Text key={e.id} style={styles.eventLine} numberOfLines={3}>
          <Text style={{ color: '#64748b' }}>{new Date(e.created_at).toLocaleTimeString()} </Text>
          <Text style={{ color: e.kind === 'error' ? '#f87171' : e.kind === 'warning' ? '#fbbf24' : '#94a3b8' }}>
            {e.message}
          </Text>
        </Text>
      ))}
    </View>
  );
}

// ── Job row ───────────────────────────────────────────────────────────────────

const JOB_STATUS_COLORS: Record<string, string> = {
  pending:           '#64748b',
  running:           '#3b82f6',
  awaiting_approval: '#f59e0b',
  passed:            '#22c55e',
  complete:          '#22c55e',
  failed:            '#ef4444',
  rejected:          '#ef4444',
};

function JobRow({ job }: { job: ForgeJob }) {
  const colors = useColors();
  const sc = JOB_STATUS_COLORS[job.status] ?? '#64748b';
  const duration = job.started_at && job.completed_at
    ? Math.round((new Date(job.completed_at).getTime() - new Date(job.started_at).getTime()) / 1000)
    : null;

  return (
    <View style={[styles.jobRow, { borderBottomColor: colors.border }]}>
      <View style={[styles.jobDot, { backgroundColor: sc }]} />
      <View style={{ flex: 1 }}>
        <Text style={[styles.jobType, { color: colors.foreground }]}>{job.type}</Text>
        <Text style={[styles.jobMeta, { color: colors.mutedForeground }]}>
          {job.status}{duration != null ? ` · ${duration}s` : ''}
        </Text>
      </View>
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function ForgeDetailScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [starting, setStarting] = useState(false);
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

  const { data, isLoading, isError, refetch } = useQuery<ForgeProjectDetail>({
    queryKey: ['mobile', 'forge', 'project', id],
    queryFn: () => mobileFetch(`/api/forge/projects/${id}`).then(r => r.json()),
    staleTime: 5_000,
    refetchInterval: (query) => {
      const status = (query.state.data as ForgeProjectDetail | undefined)?.status;
      const active = ['planning','designing','building','verifying','running'].includes(status ?? '');
      return active ? 6_000 : false;
    },
    enabled: !!id,
  });

  const awaitingJob = data?.jobs?.find(j => j.status === 'awaiting_approval');
  const latestJob = data?.latest_job ?? data?.jobs?.[0] ?? null;

  // Find the most-recent completed BUILD job that has a build directory.
  // This is the source for the preview URL.
  // BUILD jobs use status "passed" on success (not "complete").
  const buildJob = data?.jobs?.find(j => j.type === 'BUILD' && j.status === 'passed' && j.build_dir) ?? null;
  const previewUrl = buildJob
    ? `https://${domain}/api/forge/projects/${id}/jobs/${buildJob.id}/preview/index.html`
    : null;

  /** Open the built site in the system in-app browser (no auth required — the
   *  preview endpoint serves static files directly from the build directory). */
  const openPreview = async () => {
    if (!previewUrl) return;
    try {
      await WebBrowser.openBrowserAsync(previewUrl, {
        presentationStyle: WebBrowser.WebBrowserPresentationStyle.FULL_SCREEN,
      });
    } catch {
      // Fall back to the system browser if WebBrowser is unavailable.
      Linking.openURL(previewUrl);
    }
  };

  const handleApprove = async () => {
    if (!awaitingJob) return;
    setApproving(true);
    try {
      const res = await mobileFetch(`/api/forge/projects/${id}/jobs/${awaitingJob.id}/approve`, { method: 'POST' });
      if (!res.ok) throw new Error(`status ${res.status}`);
      queryClient.invalidateQueries({ queryKey: ['mobile', 'forge', 'project', id] });
    } catch (e: any) {
      Alert.alert('Error', e.message ?? 'Could not approve');
    } finally {
      setApproving(false);
    }
  };

  const handleReject = async () => {
    if (!awaitingJob) return;
    Alert.alert('Reject job', 'Are you sure you want to reject this plan/design?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Reject',
        style: 'destructive',
        onPress: async () => {
          setRejecting(true);
          try {
            const res = await mobileFetch(`/api/forge/projects/${id}/jobs/${awaitingJob.id}/reject`, { method: 'POST' });
            if (!res.ok) throw new Error(`status ${res.status}`);
            queryClient.invalidateQueries({ queryKey: ['mobile', 'forge', 'project', id] });
          } catch (e: any) {
            Alert.alert('Error', e.message ?? 'Could not reject');
          } finally {
            setRejecting(false);
          }
        },
      },
    ]);
  };

  const handleStartPlan = async () => {
    setStarting(true);
    try {
      const res = await mobileFetch(`/api/forge/projects/${id}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'PLAN' }),
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
      queryClient.invalidateQueries({ queryKey: ['mobile', 'forge', 'project', id] });
    } catch (e: any) {
      Alert.alert('Error', e.message ?? 'Could not start pipeline');
    } finally {
      setStarting(false);
    }
  };

  if (isLoading) {
    return (
      <View style={[styles.root, { backgroundColor: colors.background }]}>
        <View style={[styles.header, { paddingTop: insets.top + 8, borderBottomColor: colors.border }]}>
          <Pressable onPress={() => router.back()} hitSlop={10} style={styles.backBtn}>
            <Feather name="arrow-left" size={20} color={colors.foreground} />
          </Pressable>
          <Text style={[styles.headerTitle, { color: colors.foreground }]}>Forge</Text>
        </View>
        <ScrollView contentContainerStyle={{ padding: 16 }}>
          {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
        </ScrollView>
      </View>
    );
  }

  if (isError || !data) {
    return (
      <View style={[styles.root, { backgroundColor: colors.background }]}>
        <View style={[styles.header, { paddingTop: insets.top + 8, borderBottomColor: colors.border }]}>
          <Pressable onPress={() => router.back()} hitSlop={10} style={styles.backBtn}>
            <Feather name="arrow-left" size={20} color={colors.foreground} />
          </Pressable>
          <Text style={[styles.headerTitle, { color: colors.foreground }]}>Forge</Text>
        </View>
        <View style={styles.emptyBox}>
          <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load project</Text>
          <Pressable onPress={() => refetch()} style={[styles.retryBtn, { borderColor: colors.border }]}>
            <Text style={[styles.retryText, { color: colors.primary }]}>Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const jobs = data.jobs ?? [];
  const isIdle = data.status === 'idle' || !jobs.length;

  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View style={[styles.header, {
        paddingTop: insets.top + 8,
        borderBottomColor: colors.border,
        backgroundColor: colors.card,
      }]}>
        <Pressable onPress={() => router.back()} hitSlop={10} style={styles.backBtn}
          accessibilityRole="button" accessibilityLabel="Back to Forge">
          <Feather name="arrow-left" size={20} color={colors.foreground} />
        </Pressable>
        <Text style={[styles.headerTitle, { color: colors.foreground }]} numberOfLines={1}>
          {data.name}
        </Text>
        {data.status === 'complete' && (
          previewUrl ? (
            <Pressable
              onPress={openPreview}
              hitSlop={8}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 4,
                       paddingHorizontal: 10, paddingVertical: 5, borderRadius: 16,
                       backgroundColor: T.greenSoft, marginLeft: 4 }}
              accessibilityRole="button"
              accessibilityLabel="Preview built site"
            >
              <Feather name="external-link" size={12} color={T.green} />
              <Text style={{ fontSize: 12, color: T.green, ...font('semibold') }}>Preview</Text>
            </Pressable>
          ) : (
            <Feather name="check-circle" size={16} color={T.green} style={{ marginLeft: 4 }} />
          )
        )}
      </View>

      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 24 }]}
        refreshControl={
          <RefreshControl
            refreshing={false}
            onRefresh={() => {
              refetch();
              queryClient.invalidateQueries({ queryKey: ['mobile', 'forge', 'project', id] });
            }}
            tintColor={colors.primary}
          />
        }
      >
        {/* Brief */}
        {!!data.brief && (
          <Text style={[styles.brief, { color: colors.mutedForeground }]}>{data.brief}</Text>
        )}
        {!!data.work_title && (
          <View style={styles.workRow}>
            <Feather name="book-open" size={11} color={colors.mutedForeground} />
            <Text style={[styles.workLabel, { color: colors.mutedForeground }]}>{data.work_title}</Text>
          </View>
        )}

        {/* Pipeline stepper */}
        <View style={[styles.section, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <Text style={[styles.sectionTitle, { color: colors.mutedForeground }]}>PIPELINE</Text>
          <PipelineStepper jobs={jobs} currentStatus={data.status} />
        </View>

        {/* Approval gate */}
        {awaitingJob && (
          <View style={[styles.approvalCard, { backgroundColor: T.giltSoft, borderColor: T.giltLine }]}>
            <View style={styles.approvalHeader}>
              <Feather name="alert-circle" size={16} color={T.gilt} />
              <Text style={[styles.approvalTitle, { color: T.gilt }]}>
                {awaitingJob.type === 'PLAN' ? 'Plan ready for review' : 'Design ready for review'}
              </Text>
            </View>
            <Text style={[styles.approvalBody, { color: colors.foreground }]}>
              {awaitingJob.type === 'PLAN'
                ? 'Review the generated site plan, then approve to proceed to design.'
                : 'Review the visual concept, then approve to start building.'}
            </Text>
            <View style={styles.approvalBtns}>
              <Pressable
                onPress={handleReject}
                disabled={rejecting || approving}
                style={({ pressed }) => [
                  styles.rejectBtn,
                  { borderColor: T.rust, opacity: rejecting || pressed ? 0.6 : 1 },
                ]}
              >
                {rejecting
                  ? <ActivityIndicator size="small" color={T.rust} />
                  : <><Feather name="x" size={14} color={T.rust} /><Text style={[styles.rejectText, { color: T.rust }]}>Reject</Text></>}
              </Pressable>
              <Pressable
                onPress={handleApprove}
                disabled={approving || rejecting}
                style={({ pressed }) => [
                  styles.approveBtn,
                  { backgroundColor: T.green, opacity: approving || pressed ? 0.7 : 1 },
                ]}
              >
                {approving
                  ? <ActivityIndicator size="small" color="#fff" />
                  : <><Feather name="check" size={14} color="#fff" /><Text style={styles.approveText}>Approve</Text></>}
              </Pressable>
            </View>
          </View>
        )}

        {/* Start pipeline (idle state) */}
        {isIdle && (
          <Pressable
            onPress={handleStartPlan}
            disabled={starting}
            style={({ pressed }) => [
              styles.startBtn,
              { backgroundColor: colors.primary, opacity: starting || pressed ? 0.7 : 1 },
            ]}
          >
            {starting
              ? <ActivityIndicator color="#fff" size="small" />
              : <><Feather name="play" size={16} color="#fff" /><Text style={styles.startBtnText}>Start pipeline</Text></>}
          </Pressable>
        )}

        {/* Event log */}
        {latestJob && (
          <EventLog jobId={latestJob.id} />
        )}

        {/* Preview card — shown when a BUILD job has completed */}
        {previewUrl && (
          <Pressable
            onPress={openPreview}
            style={({ pressed }) => [
              styles.previewCard,
              { backgroundColor: T.greenSoft, borderColor: T.green + '55', opacity: pressed ? 0.8 : 1 },
            ]}
            accessibilityRole="button"
            accessibilityLabel="Open preview of built website"
          >
            <View style={styles.previewInner}>
              <View style={[styles.previewIcon, { backgroundColor: T.green + '22' }]}>
                <Feather name="monitor" size={20} color={T.green} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={[styles.previewTitle, { color: T.green }]}>Site ready — tap to preview</Text>
                <Text style={[styles.previewSub, { color: colors.mutedForeground }]} numberOfLines={1}>
                  Opens in browser · {previewUrl.replace(/^https?:\/\//, '').split('/').slice(0, 3).join('/')}…
                </Text>
              </View>
              <Feather name="external-link" size={16} color={T.green} />
            </View>
          </Pressable>
        )}

        {/* Job history */}
        {jobs.length > 0 && (
          <View style={[styles.section, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Text style={[styles.sectionTitle, { color: colors.mutedForeground }]}>JOB HISTORY</Text>
            {jobs.map(j => <JobRow key={j.id} job={j} />)}
          </View>
        )}
      </ScrollView>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16,
    paddingBottom: 12, gap: 10, borderBottomWidth: StyleSheet.hairlineWidth,
  },
  backBtn: { minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { flex: 1, fontSize: 15, lineHeight: 22, ...font('semibold') },
  content: { paddingHorizontal: 16, paddingTop: 14 },
  brief: { fontSize: 14, lineHeight: 22, marginBottom: 6, ...font('regular') },
  workRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 14 },
  workLabel: { fontSize: 12, lineHeight: 18, ...font('regular') },
  section: { borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 14 },
  sectionTitle: { fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', marginBottom: 12, ...font('semibold') },
  // Pipeline stepper
  stepper: { flexDirection: 'row', alignItems: 'center' },
  stepItem: { alignItems: 'center', gap: 4 },
  stepCircle: { width: 36, height: 36, borderRadius: 18, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  stepLabel: { fontSize: 10, letterSpacing: 0.3, ...font('medium') },
  stepReview: { fontSize: 9, letterSpacing: 0.3, ...font('semibold') },
  stepConnector: { flex: 1, height: 2, marginBottom: 18 },
  // Approval
  approvalCard: { borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 14, gap: 8 },
  approvalHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  approvalTitle: { fontSize: 14, lineHeight: 20, ...font('semibold') },
  approvalBody: { fontSize: 13, lineHeight: 20, ...font('regular') },
  approvalBtns: { flexDirection: 'row', gap: 10, marginTop: 4 },
  rejectBtn: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 10, borderRadius: 8, borderWidth: 1, minHeight: 44 },
  rejectText: { fontSize: 14, ...font('medium') },
  approveBtn: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 10, borderRadius: 8, minHeight: 44 },
  approveText: { fontSize: 14, color: '#fff', ...font('medium') },
  // Start button
  startBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14, borderRadius: 12, minHeight: 52, marginBottom: 14 },
  startBtnText: { fontSize: 15, color: '#fff', ...font('semibold') },
  // Event log
  eventLog: { borderRadius: 10, borderWidth: 1, padding: 12, marginBottom: 14 },
  eventLogTitle: { fontSize: 10, letterSpacing: 0.6, color: '#475569', marginBottom: 8, ...font('semibold') },
  eventLine: { fontSize: 11, lineHeight: 18, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', marginBottom: 2 },
  // Job history
  jobRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  jobDot: { width: 8, height: 8, borderRadius: 4, flexShrink: 0, marginTop: 2 },
  jobType: { fontSize: 13, lineHeight: 20, ...font('medium') },
  jobMeta: { fontSize: 11, lineHeight: 16, ...font('regular') },
  // Empty / error
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, lineHeight: 22, ...font('medium') },
  retryBtn: { paddingHorizontal: 20, paddingVertical: 10, borderRadius: 8, borderWidth: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  retryText: { fontSize: 14, lineHeight: 20, ...font('medium') },
  // Preview card
  previewCard: { borderRadius: 12, borderWidth: 1, padding: 14, marginBottom: 14, minHeight: 64 },
  previewInner: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  previewIcon: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center' },
  previewTitle: { fontSize: 14, lineHeight: 20, ...font('semibold') },
  previewSub: { fontSize: 11, lineHeight: 16, marginTop: 2, ...font('regular') },
});
