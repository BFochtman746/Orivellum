/**
 * Mobile Intake Screen — "Load anything"
 *
 * Lets users pick a file or photo, uploads it to the library,
 * then runs the intake pipeline and shows the Intake Profile card.
 */
import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { mobileFetch } from '@/lib/api';
import { getApiToken } from '@/lib/token';

const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API_BASE = `https://${DOMAIN}`;

// ── Types ──────────────────────────────────────────────────────────────────────

interface SuggestedAction {
  id: string;
  label: string;
  description: string;
  kind: string;
}

interface IntakeProfile {
  doc_id: string;
  what_it_is: string;
  kind: string;
  tier: string;
  filed_to: string | null;
  filed_to_id: string | null;
  confidence: number;
  summary: string;
  word_count: number;
  headings: string[];
  suggested_actions: SuggestedAction[];
  text_snippet: string | null;   // first ~500 chars of extracted text for chat grounding
  research_summary: string | null;
  research_sources: Array<{ title?: string; url?: string }>;
  error: string | null;
}

// ── Tier colors ────────────────────────────────────────────────────────────────

const TIER_COLORS: Record<string, string> = {
  canon:        '#7c3aed',
  source:       '#2563eb',
  artifact:     '#d97706',
  system:       '#64748b',
  conversation: '#059669',
};

function tierColor(tier: string): string {
  return TIER_COLORS[tier] ?? TIER_COLORS.source;
}

// ── Confidence bar ─────────────────────────────────────────────────────────────

function ConfidenceBar({ value, colors }: { value: number; colors: any }) {
  const pct = Math.round(value * 100);
  const barColor = pct >= 80 ? '#22c55e' : pct >= 50 ? '#f59e0b' : '#ef4444';
  return (
    <View style={styles.confRow}>
      <View style={[styles.confTrack, { backgroundColor: colors.muted }]}>
        <View style={[styles.confFill, { width: `${pct}%` as any, backgroundColor: barColor }]} />
      </View>
      <Text style={[styles.confPct, { color: colors.mutedForeground }]}>{pct}%</Text>
    </View>
  );
}

// ── Action button ──────────────────────────────────────────────────────────────

const ACTION_ICONS: Record<string, string> = {
  slot_book:       'bookmark',
  file_taxes:      'file-text',
  find_gaps:       'target',
  research:        'globe',
  extract_actions: 'zap',
  link_work:       'external-link',
  archive:         'archive',
  chat:            'message-circle',
};

function ActionChip({
  action,
  colors,
  onPress,
  busy,
  done,
}: {
  action: SuggestedAction;
  colors: any;
  onPress: () => void;
  busy?: boolean;
  done?: boolean;
}) {
  const icon = done ? 'check' : (ACTION_ICONS[action.kind] ?? 'activity');
  return (
    <Pressable
      onPress={busy || done ? undefined : onPress}
      style={({ pressed }) => [
        styles.chip,
        {
          backgroundColor: colors.card,
          borderColor: done ? '#22c55e' : colors.border,
          opacity: (pressed && !busy && !done) ? 0.7 : (busy ? 0.5 : 1),
        },
      ]}
      accessibilityLabel={action.label}
      accessibilityHint={action.description}
    >
      <Feather name={icon as any} size={13} color={done ? '#22c55e' : colors.primary} />
      <Text style={[styles.chipText, { color: colors.foreground }]}>
        {busy ? '…' : action.label}
      </Text>
    </Pressable>
  );
}

// ── Profile card ───────────────────────────────────────────────────────────────

function ProfileCard({
  profile,
  colors,
  router,
  onProfileUpdate,
}: {
  profile: IntakeProfile;
  colors: any;
  router: any;
  onProfileUpdate?: (p: IntakeProfile) => void;
}) {
  const tc = tierColor(profile.tier);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [archived, setArchived] = useState(false);

  const handleAction = async (action: SuggestedAction) => {
    switch (action.kind) {

      case 'retry': {
        // Re-run intake to check if extraction has finished
        setActionBusy(action.id);
        try {
          const resp = await mobileFetch(`${API_BASE}/api/intake`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ doc_id: profile.doc_id }),
          });
          if (!resp.ok) throw new Error('Intake refetch failed');
          const updated: IntakeProfile = await resp.json();
          onProfileUpdate?.(updated);
        } catch (e: any) {
          Alert.alert('Error', e.message ?? 'Could not check intake status');
        } finally {
          setActionBusy(null);
        }
        break;
      }

      case 'chat': {
        setActionBusy(action.id);
        try {
          const convResp = await mobileFetch(`${API_BASE}/api/conversations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              title: 'Document discussion',
              work_id: profile.filed_to_id ?? undefined,
            }),
          });
          if (!convResp.ok) throw new Error('Could not create conversation');
          const convData = await convResp.json();
          const id: string | undefined = convData.conversation?.id;
          if (!id) throw new Error('No conversation ID returned');
          // Ground the first message with extracted text when not linked to a Work
          const basePrompt = 'Give me an overview of this document\'s key points and how it might be useful.';
          const groundedPrompt = (!profile.filed_to_id && profile.text_snippet)
            ? `Document excerpt:\n\n${profile.text_snippet}\n\n---\n\n${basePrompt}`
            : basePrompt;
          await mobileFetch(`${API_BASE}/api/conversations/${id}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: groundedPrompt, stream: false }),
          });
          router.push(`/chat/${id}`);
        } catch (e: any) {
          Alert.alert('Error', e.message ?? 'Could not open chat');
        } finally {
          setActionBusy(null);
        }
        break;
      }

      case 'slot_book':
        router.push(profile.filed_to_id ? `/works/${profile.filed_to_id}` : '/books');
        break;

      case 'link_work':
        router.push('/works');
        break;

      case 'find_gaps':
        router.push(profile.filed_to_id ? `/works/${profile.filed_to_id}` : '/works');
        break;

      case 'file_taxes':
        // Navigate to library doc where the user can link it to an Expenses Work
        router.push(`/library/${profile.doc_id}`);
        break;

      case 'extract_actions': {
        // Create a conversation then POST the extraction prompt as the first message
        setActionBusy(action.id);
        try {
          const convResp = await mobileFetch(`${API_BASE}/api/conversations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              title: 'Action items',
              work_id: profile.filed_to_id ?? undefined,
            }),
          });
          if (!convResp.ok) throw new Error('Could not create conversation');
          const convData = await convResp.json();
          const id: string | undefined = convData.conversation?.id;
          if (!id) throw new Error('No conversation ID returned');
          const basePrompt = 'List all action items, tasks, to-dos, and deadlines from this document. Group them by owner or deadline if possible.';
          const groundedPrompt = (!profile.filed_to_id && profile.text_snippet)
            ? `Document excerpt:\n\n${profile.text_snippet}\n\n---\n\n${basePrompt}`
            : basePrompt;
          await mobileFetch(`${API_BASE}/api/conversations/${id}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: groundedPrompt, stream: false }),
          });
          router.push(`/chat/${id}`);
        } catch (e: any) {
          Alert.alert('Error', e.message ?? 'Could not open chat');
        } finally {
          setActionBusy(null);
        }
        break;
      }

      case 'archive': {
        Alert.alert(
          'Archive document',
          'This will mark the document as archived. It will be hidden from research results.',
          [
            { text: 'Cancel', style: 'cancel' },
            {
              text: 'Archive',
              style: 'destructive',
              onPress: async () => {
                setActionBusy(action.id);
                try {
                  const resp = await mobileFetch(
                    `${API_BASE}/api/library/${profile.doc_id}/lifecycle`,
                    {
                      method: 'PATCH',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ lifecycle: 'archived' }),
                    }
                  );
                  if (!resp.ok) throw new Error('Archive failed');
                  setArchived(true);
                } catch (e: any) {
                  Alert.alert('Error', e.message ?? 'Archive failed');
                } finally {
                  setActionBusy(null);
                }
              },
            },
          ]
        );
        break;
      }

      case 'research': {
        Alert.alert(
          'Web Research',
          'This will send the document title to an external search service (Tavily) to retrieve '
          + 'live web results. No document content is sent externally. Results are saved as a '
          + 'knowledge note.\n\nDo you want to proceed?',
          [
            { text: 'Cancel', style: 'cancel' },
            {
              text: 'Research It',
              onPress: async () => {
                setActionBusy(action.id);
                try {
                  // Fire-and-forget POST — returns immediately with {job_id, status}
                  const resp = await mobileFetch(`${API_BASE}/api/intake/research`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ doc_id: profile.doc_id, confirmed: true }),
                  });
                  if (!resp.ok) {
                    const e = await resp.json().catch(() => ({}));
                    throw new Error((e as any).detail ?? 'Research failed');
                  }
                  // Poll at 2s intervals; max 35 attempts (~70s)
                  const docId = profile.doc_id;
                  const MAX_POLLS = 35;
                  let done = false;
                  for (let i = 0; i < MAX_POLLS; i++) {
                    await new Promise<void>(r => setTimeout(r, 2000));
                    const statusResp = await mobileFetch(
                      `${API_BASE}/api/intake/${docId}/research-status`
                    );
                    if (!statusResp.ok) throw new Error('Research status unavailable');
                    const job = await statusResp.json() as {
                      status: string;
                      research_summary?: string | null;
                      research_sources?: Array<{ title?: string; url?: string }>;
                      error?: string | null;
                    };
                    if (job.status === 'done') {
                      // Merge research results into the existing profile
                      onProfileUpdate?.({
                        ...profile,
                        research_summary: job.research_summary ?? null,
                        research_sources: job.research_sources ?? [],
                      });
                      done = true;
                      break;
                    }
                    if (job.status === 'error') {
                      throw new Error(job.error ?? 'Research failed on the server');
                    }
                    // pending / running — keep polling
                  }
                  if (!done) throw new Error('Research timed out. Tavily may be slow — try again later.');
                } catch (e: any) {
                  Alert.alert('Research failed', e.message ?? 'Unknown error');
                } finally {
                  setActionBusy(null);
                }
              },
            },
          ]
        );
        break;
      }

      default:
        if (profile.filed_to_id) router.push(`/works/${profile.filed_to_id}`);
    }
  };

  return (
    <View style={[styles.profileCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
      {/* Tier badge */}
      <View style={styles.profileHeader}>
        <View style={[styles.tierBadge, { backgroundColor: tc + '18', borderColor: tc + '44' }]}>
          <Text style={[styles.tierBadgeText, { color: tc }]}>{profile.tier.toUpperCase()}</Text>
        </View>
        <Text style={[styles.kindText, { color: colors.mutedForeground }]}>{profile.kind.toUpperCase()}</Text>
        {profile.word_count > 0 && (
          <Text style={[styles.kindText, { color: colors.mutedForeground }]}>
            {profile.word_count.toLocaleString()}w
          </Text>
        )}
      </View>

      {/* What it is */}
      <Text style={[styles.whatText, { color: colors.foreground }]}>{profile.what_it_is}</Text>

      {profile.filed_to && (
        <Text style={[styles.filedTo, { color: colors.primary }]}>
          📁 Filed under: {profile.filed_to}
        </Text>
      )}

      {/* Confidence */}
      <View style={styles.confSection}>
        <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
          CLASSIFICATION CONFIDENCE
        </Text>
        <ConfidenceBar value={profile.confidence} colors={colors} />
      </View>

      {/* Summary */}
      {profile.summary && (
        <View style={styles.summarySection}>
          <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>SUMMARY</Text>
          <Text style={[styles.summaryText, { color: colors.mutedForeground }]} numberOfLines={6}>
            {profile.summary}
          </Text>
        </View>
      )}

      {/* Actions */}
      {!archived && profile.suggested_actions.length > 0 && (
        <View style={styles.actionsSection}>
          <Text style={[styles.sectionLabel, { color: colors.mutedForeground }]}>
            SUGGESTED ACTIONS
          </Text>
          <View style={styles.chipsRow}>
            {profile.suggested_actions.map(a => (
              <ActionChip
                key={a.id}
                action={a}
                colors={colors}
                onPress={() => handleAction(a)}
                busy={actionBusy === a.id}
                done={false}
              />
            ))}
          </View>
        </View>
      )}
      {archived && (
        <View style={[styles.archivedNote, { borderColor: '#22c55e44' }]}>
          <Feather name="check" size={14} color="#22c55e" />
          <Text style={{ color: '#22c55e', fontSize: 12, fontFamily: 'Inter_400Regular' }}>
            Document archived
          </Text>
        </View>
      )}
    </View>
  );
}

// ── Main screen ────────────────────────────────────────────────────────────────

import { useRouter } from 'expo-router';

export default function IntakeScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [phase, setPhase] = useState<'pick' | 'uploading' | 'profiling' | 'done' | 'error'>('pick');
  const [fileName, setFileName] = useState('');
  const [profile, setProfile] = useState<IntakeProfile | null>(null);
  const [errorMsg, setErrorMsg] = useState('');

  const reset = () => {
    setPhase('pick');
    setFileName('');
    setProfile(null);
    setErrorMsg('');
  };

  const runIntake = async (docId: string) => {
    setPhase('profiling');
    try {
      const resp = await mobileFetch(`${API_BASE}/api/intake`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_id: docId }),
      });
      if (!resp.ok) {
        const e = await resp.json().catch(() => ({}));
        throw new Error((e as any).detail ?? `HTTP ${resp.status}`);
      }
      const data: IntakeProfile = await resp.json();
      setProfile(data);
      setPhase('done');
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Intake profile failed');
      setPhase('error');
    }
  };

  const uploadFile = async (uri: string, name: string, mimeType: string) => {
    setPhase('uploading');
    setFileName(name);
    try {
      const form = new FormData();
      form.append('file', { uri, name, type: mimeType } as any);
      // Use mobileFetch so the bearer token is attached automatically
      const resp = await mobileFetch(`${API_BASE}/api/library/upload`, {
        method: 'POST',
        body: form as any,
      });
      if (!resp.ok) {
        const e = await resp.json().catch(() => ({}));
        throw new Error((e as any).detail ?? `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const docId = data.document?.id;
      if (!docId) throw new Error('Upload did not return a document ID');
      await runIntake(docId);
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Upload failed');
      setPhase('error');
    }
  };

  const pickDocument = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: '*/*',
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.[0]) return;
      const asset = result.assets[0];
      await uploadFile(asset.uri, asset.name, asset.mimeType ?? 'application/octet-stream');
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not open document picker');
      setPhase('error');
    }
  };

  const pickPhoto = async () => {
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) {
        setErrorMsg('Photo library permission denied');
        setPhase('error');
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 0.85,
      });
      if (result.canceled || !result.assets?.[0]) return;
      const asset = result.assets[0];
      const name = asset.fileName ?? `photo_${Date.now()}.jpg`;
      await uploadFile(asset.uri, name, asset.mimeType ?? 'image/jpeg');
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not open photo library');
      setPhase('error');
    }
  };

  const takePhoto = async () => {
    try {
      const perm = await ImagePicker.requestCameraPermissionsAsync();
      if (!perm.granted) {
        setErrorMsg('Camera permission denied');
        setPhase('error');
        return;
      }
      const result = await ImagePicker.launchCameraAsync({ quality: 0.85 });
      if (result.canceled || !result.assets?.[0]) return;
      const asset = result.assets[0];
      const name = asset.fileName ?? `photo_${Date.now()}.jpg`;
      await uploadFile(asset.uri, name, asset.mimeType ?? 'image/jpeg');
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not open camera');
      setPhase('error');
    }
  };

  return (
    <ScrollView
      style={[styles.root, { backgroundColor: colors.background }]}
      contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 32 }]}
    >
      {/* Header */}
      <View style={styles.headerRow}>
        <View style={[styles.headerIcon, { backgroundColor: colors.primary + '18' }]}>
          <Feather name="inbox" size={24} color={colors.primary} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={[styles.pageTitle, { color: colors.foreground }]}>Load Anything</Text>
          <Text style={[styles.pageSubtitle, { color: colors.mutedForeground }]}>
            Import a file for instant identification and next-step suggestions
          </Text>
        </View>
      </View>

      {/* Pick phase */}
      {phase === 'pick' && (
        <View style={styles.pickSection}>
          <Text style={[styles.pickHint, { color: colors.mutedForeground }]}>
            Pick a document, image, or take a photo:
          </Text>
          <Pressable
            onPress={pickDocument}
            style={({ pressed }) => [
              styles.pickBtn,
              { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.75 : 1 },
            ]}
          >
            <View style={[styles.pickBtnIcon, { backgroundColor: colors.primary + '18' }]}>
              <Feather name="file" size={22} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.pickBtnTitle, { color: colors.foreground }]}>Pick a Document</Text>
              <Text style={[styles.pickBtnSub, { color: colors.mutedForeground }]}>
                PDF, DOCX, XLSX, TXT, CSV, Markdown…
              </Text>
            </View>
            <Feather name="chevron-right" size={18} color={colors.mutedForeground} />
          </Pressable>

          <Pressable
            onPress={pickPhoto}
            style={({ pressed }) => [
              styles.pickBtn,
              { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.75 : 1 },
            ]}
          >
            <View style={[styles.pickBtnIcon, { backgroundColor: '#7c3aed18' }]}>
              <Feather name="image" size={22} color="#7c3aed" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.pickBtnTitle, { color: colors.foreground }]}>Photo Library</Text>
              <Text style={[styles.pickBtnSub, { color: colors.mutedForeground }]}>
                Receipt, whiteboard, screenshot, diagram
              </Text>
            </View>
            <Feather name="chevron-right" size={18} color={colors.mutedForeground} />
          </Pressable>

          <Pressable
            onPress={takePhoto}
            style={({ pressed }) => [
              styles.pickBtn,
              { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.75 : 1 },
            ]}
          >
            <View style={[styles.pickBtnIcon, { backgroundColor: '#0596191A' }]}>
              <Feather name="camera" size={22} color="#059619" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.pickBtnTitle, { color: colors.foreground }]}>Take a Photo</Text>
              <Text style={[styles.pickBtnSub, { color: colors.mutedForeground }]}>
                Capture a receipt, whiteboard, or document
              </Text>
            </View>
            <Feather name="chevron-right" size={18} color={colors.mutedForeground} />
          </Pressable>
        </View>
      )}

      {/* Uploading / profiling */}
      {(phase === 'uploading' || phase === 'profiling') && (
        <View style={styles.loadingBox}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={[styles.loadingTitle, { color: colors.foreground }]}>
            {phase === 'uploading' ? 'Uploading…' : 'Identifying…'}
          </Text>
          {fileName ? (
            <Text style={[styles.loadingFile, { color: colors.mutedForeground }]} numberOfLines={1}>
              {fileName}
            </Text>
          ) : null}
          <Text style={[styles.loadingHint, { color: colors.mutedForeground }]}>
            {phase === 'uploading'
              ? 'Sending your file to the workspace'
              : 'Running the intake pipeline — classifying, extracting, embedding'}
          </Text>
        </View>
      )}

      {/* Error */}
      {phase === 'error' && (
        <View style={[styles.errorBox, { backgroundColor: '#fef2f2', borderColor: '#fca5a5' }]}>
          <Feather name="alert-circle" size={24} color="#ef4444" />
          <Text style={styles.errorTitle}>Something went wrong</Text>
          <Text style={styles.errorMsg}>{errorMsg}</Text>
          <Pressable
            onPress={reset}
            style={[styles.retryBtn, { backgroundColor: colors.primary }]}
          >
            <Text style={styles.retryBtnText}>Try Again</Text>
          </Pressable>
        </View>
      )}

      {/* Done — profile */}
      {phase === 'done' && profile && (
        <>
          <ProfileCard
            profile={profile}
            colors={colors}
            router={router}
            onProfileUpdate={setProfile}
          />
          <Pressable
            onPress={reset}
            style={({ pressed }) => [
              styles.loadAnotherBtn,
              { borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
            ]}
          >
            <Feather name="plus" size={16} color={colors.primary} />
            <Text style={[styles.loadAnotherText, { color: colors.primary }]}>Load Another</Text>
          </Pressable>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: 16 },
  headerRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 14, marginBottom: 24 },
  headerIcon: { width: 52, height: 52, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  pageTitle: { fontSize: 22, fontFamily: 'Merriweather_700Bold' },
  pageSubtitle: { fontSize: 13, fontFamily: 'Inter_400Regular', lineHeight: 18, marginTop: 2 },
  pickSection: { gap: 10 },
  pickHint: { fontSize: 12, fontFamily: 'Inter_400Regular', marginBottom: 4 },
  pickBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 14, padding: 14,
    borderRadius: 12, borderWidth: 1,
  },
  pickBtnIcon: { width: 44, height: 44, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  pickBtnTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold', marginBottom: 2 },
  pickBtnSub: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  loadingBox: { alignItems: 'center', paddingVertical: 60, gap: 14 },
  loadingTitle: { fontSize: 18, fontFamily: 'Inter_600SemiBold' },
  loadingFile: { fontSize: 12, fontFamily: 'Inter_400Regular', maxWidth: 260 },
  loadingHint: { fontSize: 12, fontFamily: 'Inter_400Regular', textAlign: 'center', maxWidth: 280 },
  errorBox: {
    alignItems: 'center', borderRadius: 12, borderWidth: 1,
    padding: 24, gap: 10, marginTop: 16,
  },
  errorTitle: { fontSize: 16, fontFamily: 'Inter_600SemiBold', color: '#dc2626' },
  errorMsg: { fontSize: 13, fontFamily: 'Inter_400Regular', color: '#ef4444', textAlign: 'center' },
  retryBtn: { marginTop: 8, paddingHorizontal: 24, paddingVertical: 10, borderRadius: 8 },
  retryBtnText: { color: '#fff', fontFamily: 'Inter_600SemiBold', fontSize: 14 },
  profileCard: { borderRadius: 14, borderWidth: 1, padding: 16, gap: 14 },
  profileHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  tierBadge: { borderRadius: 6, borderWidth: 1, paddingHorizontal: 8, paddingVertical: 3 },
  tierBadgeText: { fontSize: 9, fontFamily: 'Inter_600SemiBold', letterSpacing: 0.8 },
  kindText: { fontSize: 9, fontFamily: 'Inter_400Regular', letterSpacing: 0.5, textTransform: 'uppercase' },
  whatText: { fontSize: 15, fontFamily: 'Inter_600SemiBold', lineHeight: 20 },
  filedTo: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  confSection: { gap: 6 },
  sectionLabel: { fontSize: 9, fontFamily: 'Inter_600SemiBold', letterSpacing: 1, textTransform: 'uppercase' },
  confRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  confTrack: { flex: 1, height: 5, borderRadius: 3, overflow: 'hidden' },
  confFill: { height: '100%', borderRadius: 3 },
  confPct: { fontSize: 10, fontFamily: 'Inter_400Regular', minWidth: 28, textAlign: 'right' },
  summarySection: { gap: 6 },
  summaryText: { fontSize: 12, fontFamily: 'Inter_400Regular', lineHeight: 18 },
  actionsSection: { gap: 8 },
  chipsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 10, paddingVertical: 7,
    borderRadius: 8, borderWidth: 1, minHeight: 34,
  },
  chipText: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  loadAnotherBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8, marginTop: 16, paddingVertical: 12,
    borderRadius: 10, borderWidth: 1,
  },
  loadAnotherText: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  archivedNote: {
    flexDirection: 'row' as const, alignItems: 'center' as const, gap: 6,
    padding: 10, borderRadius: 8, borderWidth: 1,
  },
});
