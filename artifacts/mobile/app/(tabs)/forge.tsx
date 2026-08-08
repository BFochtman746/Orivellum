/**
 * Forge — Website Factory hub.
 *
 * Lists all Forge projects with status badges, lets users create new ones,
 * and navigates to /forge/[id] for the full pipeline view.
 */
import React, { useState } from 'react';
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
  TextInput,
  View,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { mobileFetch } from '@/lib/api';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { font, fontSerif } from '@/lib/typography';

// ── Types ──────────────────────────────────────────────────────────────────────

interface ForgeProject {
  id: string;
  name: string;
  brief?: string | null;
  status: string;
  work_title?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

// ── Status helpers ─────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  idle:              '#64748b',
  planning:          '#8b5cf6',
  awaiting_approval: '#f59e0b',
  designing:         '#3b82f6',
  building:          '#f97316',
  verifying:         '#06b6d4',
  complete:          '#22c55e',
  failed:            '#ef4444',
};

const STATUS_LABELS: Record<string, string> = {
  idle:              'Idle',
  planning:          'Planning…',
  awaiting_approval: 'Needs review',
  designing:         'Designing…',
  building:          'Building…',
  verifying:         'Verifying…',
  complete:          'Complete',
  failed:            'Failed',
};

function statusColor(s: string) { return STATUS_COLORS[s] ?? '#64748b'; }
function statusLabel(s: string) { return STATUS_LABELS[s] ?? s; }
function isActive(s: string) { return ['planning','designing','building','verifying'].includes(s); }

// ── Project card ───────────────────────────────────────────────────────────────

function ProjectCard({ project }: { project: ForgeProject }) {
  const colors = useColors();
  const router = useRouter();
  const sc = statusColor(project.status);
  const sl = statusLabel(project.status);
  const active = isActive(project.status);

  return (
    <Pressable
      onPress={() => {
        if (Platform.OS !== 'web') Haptics.selectionAsync().catch(() => {});
        router.push(`/forge/${project.id}` as any);
      }}
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.85 : 1 },
      ]}
    >
      {/* Status accent */}
      <View style={[styles.cardAccent, { backgroundColor: sc }]} />

      <View style={styles.cardBody}>
        <View style={styles.cardTop}>
          <Text style={[styles.cardTitle, { color: colors.foreground }]} numberOfLines={1}>
            {project.name}
          </Text>
          <View style={[styles.statusBadge, { backgroundColor: sc + '22', borderColor: sc + '55' }]}>
            {active && (
              <View style={[styles.activeDot, { backgroundColor: sc }]} />
            )}
            <Text style={[styles.statusText, { color: sc }]}>{sl}</Text>
          </View>
        </View>

        {!!project.brief && (
          <Text style={[styles.cardBrief, { color: colors.mutedForeground }]} numberOfLines={2}>
            {project.brief}
          </Text>
        )}

        {!!project.work_title && (
          <View style={styles.workRow}>
            <Feather name="book-open" size={10} color={colors.mutedForeground} />
            <Text style={[styles.workLabel, { color: colors.mutedForeground }]} numberOfLines={1}>
              {project.work_title}
            </Text>
          </View>
        )}
      </View>

      <Feather name="chevron-right" size={16} color={colors.mutedForeground} style={styles.chevron} />
    </Pressable>
  );
}

// ── New project modal ──────────────────────────────────────────────────────────

function NewProjectModal({
  visible,
  onClose,
  onCreated,
}: {
  visible: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const [name, setName] = useState('');
  const [brief, setBrief] = useState('');
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    const n = name.trim();
    if (!n) return;
    setSaving(true);
    try {
      const res = await mobileFetch('/api/forge/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: n, brief: brief.trim() || undefined }),
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      setName('');
      setBrief('');
      onCreated(data.id ?? data.project?.id);
    } catch (e: any) {
      Alert.alert('Error', e.message ?? 'Could not create project');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <View style={[styles.modalRoot, { backgroundColor: colors.background, paddingBottom: insets.bottom + 16 }]}>
        {/* Handle */}
        <View style={[styles.modalHandle, { backgroundColor: colors.border }]} />

        <View style={styles.modalHeader}>
          <Text style={[styles.modalTitle, { color: colors.foreground }]}>New Forge Project</Text>
          <Pressable onPress={onClose} hitSlop={12}>
            <Feather name="x" size={20} color={colors.mutedForeground} />
          </Pressable>
        </View>

        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={styles.modalContent}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={[styles.fieldLabel, { color: colors.mutedForeground }]}>PROJECT NAME *</Text>
          <TextInput
            style={[styles.input, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.card }]}
            placeholder="My awesome site"
            placeholderTextColor={colors.mutedForeground}
            value={name}
            onChangeText={setName}
            autoFocus
            returnKeyType="next"
          />

          <Text style={[styles.fieldLabel, { color: colors.mutedForeground, marginTop: 16 }]}>BRIEF (optional)</Text>
          <TextInput
            style={[styles.input, styles.inputMulti, { color: colors.foreground, borderColor: colors.border, backgroundColor: colors.card }]}
            placeholder="Describe the site you want to build — purpose, audience, key pages…"
            placeholderTextColor={colors.mutedForeground}
            value={brief}
            onChangeText={setBrief}
            multiline
            numberOfLines={4}
            textAlignVertical="top"
            returnKeyType="done"
          />

          <Pressable
            onPress={handleCreate}
            disabled={!name.trim() || saving}
            style={({ pressed }) => [
              styles.createBtn,
              { backgroundColor: colors.primary, opacity: (!name.trim() || saving || pressed) ? 0.6 : 1 },
            ]}
          >
            {saving
              ? <ActivityIndicator color="#fff" size="small" />
              : <>
                  <Feather name="globe" size={16} color="#fff" />
                  <Text style={styles.createBtnText}>Create Project</Text>
                </>
            }
          </Pressable>
        </ScrollView>
      </View>
    </Modal>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function ForgeScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [showNew, setShowNew] = useState(false);

  const { data, isLoading, isError, refetch } = useQuery<{ projects: ForgeProject[] }>({
    queryKey: ['mobile', 'forge', 'projects'],
    queryFn: () => mobileFetch('/api/forge/projects').then(r => r.json()),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const projects = data?.projects ?? [];
  const needsReview = projects.filter(p => p.status === 'awaiting_approval');
  const active = projects.filter(p => isActive(p.status));
  const rest = projects.filter(p => !isActive(p.status) && p.status !== 'awaiting_approval');

  const handleCreated = (id: string) => {
    setShowNew(false);
    queryClient.invalidateQueries({ queryKey: ['mobile', 'forge', 'projects'] });
    router.push(`/forge/${id}` as any);
  };

  return (
    <>
      <ScrollView
        style={[styles.root, { backgroundColor: colors.background }]}
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 80 }]}
        refreshControl={
          <RefreshControl
            refreshing={isLoading}
            onRefresh={() => {
              refetch();
              queryClient.invalidateQueries({ queryKey: ['mobile', 'forge', 'projects'] });
            }}
            tintColor={colors.primary}
          />
        }
      >
        {/* Header */}
        <View style={styles.headerRow}>
          <Feather name="globe" size={20} color={colors.primary} />
          <Text style={[styles.pageTitle, { color: colors.foreground }]}>Forge</Text>
        </View>
        <Text style={[styles.pageSubtitle, { color: colors.mutedForeground }]}>
          AI-powered website builder from your knowledge base
        </Text>

        {isLoading ? (
          [...Array(3)].map((_, i) => <SkeletonItem key={i} lines={2} />)
        ) : isError ? (
          <View style={styles.emptyBox}>
            <Feather name="wifi-off" size={28} color={colors.mutedForeground} />
            <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>Could not load projects</Text>
            <Pressable onPress={() => refetch()} style={[styles.retryBtn, { borderColor: colors.border }]}>
              <Text style={[styles.retryText, { color: colors.primary }]}>Retry</Text>
            </Pressable>
          </View>
        ) : projects.length === 0 ? (
          <EmptyState
            icon="globe"
            title="No Forge projects yet"
            body="Create a project to start building an AI-generated website from a plain-language brief."
          />
        ) : (
          <>
            {needsReview.length > 0 && (
              <>
                <Text style={[styles.sectionLabel, { color: T.gilt }]}>NEEDS REVIEW ({needsReview.length})</Text>
                {needsReview.map(p => <ProjectCard key={p.id} project={p} />)}
              </>
            )}
            {active.length > 0 && (
              <>
                <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>ACTIVE</Text>
                {active.map(p => <ProjectCard key={p.id} project={p} />)}
              </>
            )}
            {rest.length > 0 && (
              <>
                {(needsReview.length > 0 || active.length > 0) && (
                  <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>ALL PROJECTS</Text>
                )}
                {rest.map(p => <ProjectCard key={p.id} project={p} />)}
              </>
            )}
          </>
        )}
      </ScrollView>

      {/* FAB */}
      <Pressable
        onPress={() => setShowNew(true)}
        style={({ pressed }) => [
          styles.fab,
          { backgroundColor: colors.primary, bottom: insets.bottom + 20, opacity: pressed ? 0.8 : 1 },
        ]}
        accessibilityLabel="Create new Forge project"
        accessibilityRole="button"
      >
        <Feather name="plus" size={22} color="#fff" />
      </Pressable>

      <NewProjectModal
        visible={showNew}
        onClose={() => setShowNew(false)}
        onCreated={handleCreated}
      />
    </>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 16 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  pageTitle: { fontSize: 26, lineHeight: 32, ...fontSerif('bold') },
  pageSubtitle: { fontSize: 15, lineHeight: 22, marginBottom: 16, ...font('regular') },
  sectionLabel: {
    fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase',
    marginBottom: 8, marginTop: 4, ...font('semibold'),
  },
  card: {
    flexDirection: 'row', alignItems: 'center',
    borderRadius: 12, borderWidth: 1, marginBottom: 10,
    overflow: 'hidden', minHeight: 44,
  },
  cardAccent: { width: 3, alignSelf: 'stretch' },
  cardBody: { flex: 1, padding: 12, gap: 4 },
  cardTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  cardTitle: { fontSize: 15, lineHeight: 22, flex: 1, ...font('semibold') },
  statusBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 7, paddingVertical: 3, borderRadius: 6, borderWidth: 1,
    flexShrink: 0,
  },
  activeDot: { width: 5, height: 5, borderRadius: 3 },
  statusText: { fontSize: 10, letterSpacing: 0.4, ...font('semibold') },
  cardBrief: { fontSize: 12, lineHeight: 18, ...font('regular') },
  workRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  workLabel: { fontSize: 11, lineHeight: 16, ...font('regular') },
  chevron: { marginRight: 12 },
  emptyBox: { alignItems: 'center', paddingTop: 64, gap: 12 },
  emptyText: { fontSize: 15, lineHeight: 22, ...font('medium') },
  retryBtn: { marginTop: 4, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 8, borderWidth: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  retryText: { fontSize: 14, lineHeight: 20, ...font('medium') },
  fab: {
    position: 'absolute', right: 20, width: 52, height: 52,
    borderRadius: 26, alignItems: 'center', justifyContent: 'center',
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2, shadowRadius: 6, elevation: 6,
  },
  // Modal
  modalRoot: { flex: 1, paddingTop: 12 },
  modalHandle: { width: 36, height: 4, borderRadius: 2, alignSelf: 'center', marginBottom: 16 },
  modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, marginBottom: 8 },
  modalTitle: { fontSize: 18, lineHeight: 24, ...fontSerif('bold') },
  modalContent: { paddingHorizontal: 20, paddingTop: 8, gap: 4 },
  fieldLabel: { fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', marginBottom: 6, ...font('semibold') },
  input: {
    borderWidth: 1, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 15, lineHeight: 22, ...font('regular'),
  },
  inputMulti: { height: 100, paddingTop: 12 },
  createBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8, marginTop: 24, paddingVertical: 14, borderRadius: 12, minHeight: 52,
  },
  createBtnText: { fontSize: 15, lineHeight: 22, color: '#fff', ...font('semibold') },
});
