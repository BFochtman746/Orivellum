import React, { useState, useEffect, useCallback, useRef } from 'react';
import { mobileFetch } from '@/lib/api';
import { getApiToken } from '@/lib/token';
import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
import * as Clipboard from 'expo-clipboard';
import {
  ActivityIndicator,
  Alert,
  Animated,
  FlatList,
  LayoutAnimation,
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
import { Swipeable } from 'react-native-gesture-handler';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens, VELLUM_LIGHT, alpha } from '@/lib/tokens';
import { Feather } from '@expo/vector-icons';
import {
  useGetWork,
  useGetWorkDocuments,
  useGetWorkKnowledge,
  useGetWorkTasks,
  useCreateWorkTask,
  useCreateConversation,
  useListConversations,
  useUpdateWork,
  useUpdateConversation,
  getListConversationsQueryKey,
  getGetWorkTasksQueryKey,
  getGetWorkStatsQueryKey,
  getGetWorkQueryKey,
} from '@workspace/api-client-react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import type { Document, KnowledgeItem, Task } from '@workspace/api-client-react';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';
import { readCache, writeCache } from '@/lib/cache';
import { KnowledgeGraphView } from '@/components/KnowledgeGraphView';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';
import { font, fontSerif } from '@/lib/typography';

type Tab = 'overview' | 'docs' | 'knowledge' | 'tasks' | 'conversations' | 'learn' | 'gaps' | 'completeness' | 'book' | 'brainstorm' | 'intelligence' | 'trailer' | 'genesis' | 'graph';

// Primary tabs always visible in the bar; secondary tabs disclosed via "More"
const PRIMARY_TABS: { key: Tab; label: string }[] = [
  { key: 'overview',       label: 'Overview'  },
  { key: 'docs',           label: 'Docs'      },
  { key: 'knowledge',      label: 'Knowledge' },
  { key: 'tasks',          label: 'Tasks'     },
  { key: 'conversations',  label: 'Chats'     },
];
const SECONDARY_TABS: { key: Tab; label: string }[] = [
  { key: 'intelligence', label: 'Intelligence' },
  { key: 'gaps',         label: 'Gaps'     },
  { key: 'completeness', label: 'Coverage' },
  { key: 'learn',        label: 'Learn'    },
  { key: 'book',         label: 'Book'     },
  { key: 'brainstorm',   label: 'Ideas'    },
  { key: 'trailer',      label: 'Trailer'  },
  { key: 'genesis',      label: 'Genesis'  },
  { key: 'graph',        label: 'Graph'    },
];

// All tab keys in display order — used by TabDotIndicator
const TAB_ORDER: Tab[] = [
  ...PRIMARY_TABS.map(t => t.key),
  ...SECONDARY_TABS.map(t => t.key),
];

function TabDotIndicator({ tabs, activeTab }: { tabs: Tab[]; activeTab: Tab }) {
  const colors = useColors();
  const T = useVellumTokens();
  return (
    <View style={{
      flexDirection: 'row', justifyContent: 'center', alignItems: 'center',
      gap: 6, paddingVertical: 5, backgroundColor: colors.background,
    }}>
      {tabs.map(tab => {
        const isActive = tab === activeTab;
        return (
          <View
            key={tab}
            style={{
              width: isActive ? 18 : 5,
              height: 4,
              borderRadius: 2,
              backgroundColor: isActive ? T.gilt : colors.border,
            }}
          />
        );
      })}
    </View>
  );
}

function TabBar({ active, onSelect, colors, badges = {}, onNavigateGraph }: {
  active: Tab;
  onSelect: (t: Tab) => void;
  colors: any;
  badges?: Partial<Record<Tab, number>>;
  onNavigateGraph?: () => void;
}) {
  const T = useVellumTokens();
  const isSecondaryActive = SECONDARY_TABS.some(t => t.key === active);
  const activeSecondaryLabel = SECONDARY_TABS.find(t => t.key === active)?.label;

  const openMore = () => {
    Alert.alert(
      'More',
      undefined,
      [
        ...SECONDARY_TABS.map(t => ({
          text: t.label + (badges[t.key] && badges[t.key]! > 0 ? ` (${badges[t.key]})` : ''),
          onPress: () => onSelect(t.key),
        })),
        { text: 'Knowledge Graph ↗', onPress: () => onNavigateGraph?.() },
        { text: 'Cancel', style: 'cancel' as const },
      ],
    );
  };

  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={{ borderBottomWidth: 1, borderBottomColor: colors.border, backgroundColor: colors.background }}
      contentContainerStyle={{ flexDirection: 'row' }}
    >
      {PRIMARY_TABS.map((t) => {
        const badge = badges[t.key];
        return (
          <Pressable
            key={t.key}
            onPress={() => onSelect(t.key)}
            style={[
              styles.tab,
              active === t.key && { borderBottomColor: colors.primary, borderBottomWidth: 2 },
            ]}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
              <Text
                style={[
                  styles.tabLabel,
                  {
                    color: active === t.key ? colors.primary : colors.mutedForeground,
                    fontFamily: active === t.key ? 'Inter_600SemiBold' : 'Inter_400Regular',
                  },
                ]}
              >
                {t.label}
              </Text>
              {badge != null && badge > 0 && (
                <View style={{ backgroundColor: colors.primary, borderRadius: 8, minWidth: 16, paddingHorizontal: 3, alignItems: 'center' }}>
                  <Text style={{ color: colors.primaryForeground, fontSize: 9, fontFamily: 'Inter_700Bold', lineHeight: 14 }}>{badge}</Text>
                </View>
              )}
            </View>
          </Pressable>
        );
      })}
      {/* "More" discloses secondary tabs */}
      {(() => {
        const secondaryBadgeTotal = SECONDARY_TABS.reduce(
          (sum, t) => sum + (badges[t.key] ?? 0), 0,
        );
        return (
          <Pressable
            onPress={openMore}
            style={[
              styles.tab,
              isSecondaryActive && { borderBottomColor: colors.primary, borderBottomWidth: 2 },
            ]}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
              <Text style={[
                styles.tabLabel,
                { color: isSecondaryActive ? colors.primary : colors.mutedForeground,
                  fontFamily: isSecondaryActive ? 'Inter_600SemiBold' : 'Inter_400Regular' },
              ]}>
                {isSecondaryActive ? activeSecondaryLabel : '•••'}
              </Text>
              {!isSecondaryActive && secondaryBadgeTotal > 0 && (
                <View style={{ backgroundColor: T.rust, borderRadius: 8, minWidth: 16, paddingHorizontal: 3, alignItems: 'center' }}>
                  <Text style={{ color: '#fff', fontSize: 9, fontFamily: 'Inter_700Bold', lineHeight: 14 }}>
                    {secondaryBadgeTotal}
                  </Text>
                </View>
              )}
            </View>
          </Pressable>
        );
      })()}
    </ScrollView>
  );
}

function DocItem({ doc, onReprocess }: { doc: Document; onReprocess?: (docId: string) => void }) {
  const colors = useColors();
  const T = useVellumTokens();
  const router = useRouter();
  const isStuck = doc.readiness === 'error' || doc.readiness === 'no_text' || doc.readiness === 'imported';
  return (
    <Pressable
      onPress={() => router.push(`/library/${doc.id}` as any)}
      style={({ pressed }) => [styles.listItem, { borderColor: colors.border, opacity: pressed ? 0.7 : 1, flexDirection: 'column', gap: 0, minHeight: 44 }]}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 }}>
        <View style={[styles.itemIcon, { backgroundColor: colors.muted }]}>
          <Feather name="file-text" size={14} color={colors.primary} />
        </View>
        <View style={styles.itemBody}>
          <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={1}>
            {doc.title ?? doc.source ?? 'Document'}
          </Text>
          <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
            {doc.kind ?? 'file'} · {doc.readiness ?? 'pending'}
          </Text>
        </View>
        <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
      </View>
      {isStuck && onReprocess && (
        <Pressable
          onPress={(e) => { e.stopPropagation?.(); onReprocess(doc.id ?? ''); }}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-end', marginTop: 6, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, backgroundColor: T.giltSoft, borderWidth: 1, borderColor: T.giltLine, minHeight: 44 }}
        >
          <Feather name="refresh-cw" size={11} color={T.gilt} />
          <Text style={{ fontSize: 11, ...font('semibold'), color: T.gilt }}>Re-extract</Text>
        </Pressable>
      )}
    </Pressable>
  );
}

function KnowledgeRow({ item, onReviewed, onDelete }: { item: KnowledgeItem; onReviewed?: () => void; onDelete?: () => void }) {
  const colors = useColors();
  const T = useVellumTokens();
  const confRaw = (item.confidence ?? 0) * 100;  // unrounded — used for tier classification
  const conf = Math.round(confRaw);               // rounded — used for display only
  const confTier = confRaw >= 80 ? 'High' : confRaw >= 50 ? 'Med' : 'Low';
  const [localStatus, setLocalStatus] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);

  const status = localStatus ?? (item as any).review_status ?? 'auto';
  const isAiAuto = (item as any).review_status === 'ai_auto' || (item as any).source === 'llm';
  const isRejected = status === 'rejected';

  const review = async (action: 'approve' | 'reject') => {
    // Selection haptic on each knowledge review decision
    if (Platform.OS !== 'web') {
      Haptics.selectionAsync().catch(() => {});
    }
    setReviewing(true);
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const res = await mobileFetch(`https://${domain}/api/knowledge/${item.id}/review`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_status: action === 'approve' ? 'approved' : 'rejected' }),
      });
      if (res.ok) {
        setLocalStatus(action === 'approve' ? 'approved' : 'rejected');
        onReviewed?.();
      }
    } catch (_) {
      // silent — network error
    } finally {
      setReviewing(false);
    }
  };

  const handleLongPress = () => {
    if (!onDelete) return;
    if (Platform.OS !== 'web') {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    }
    Alert.alert('Delete Knowledge Item', 'Remove this item?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: onDelete },
    ]);
  };

  return (
    <Pressable
      onLongPress={handleLongPress}
      delayLongPress={400}
      style={[styles.listItem, { borderColor: colors.border, opacity: isRejected ? 0.45 : 1 }]}
    >
      <View style={[styles.itemIcon, { backgroundColor: colors.muted }]}>
        <Feather name="cpu" size={14} color={isAiAuto ? T.gilt : colors.primary} />
      </View>
      <View style={styles.itemBody}>
        <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={3}>
          {item.text}
        </Text>
        <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
          {item.kind} · {conf}% {confTier} · {isAiAuto ? '✦ AI' : 'rule'}
          {status === 'approved' ? ' · ✓ approved' : status === 'rejected' ? ' · ✗ rejected' : ''}
        </Text>
        {isAiAuto && status !== 'approved' && status !== 'rejected' && (
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
            <Pressable
              onPress={() => review('approve')}
              disabled={reviewing}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
                paddingHorizontal: 10,
                paddingVertical: 6,
                borderRadius: 6,
                backgroundColor: T.greenSoft,
                opacity: reviewing ? 0.38 : 1,
                minHeight: 44,
              }}
            >
              <Feather name="thumbs-up" size={12} color={T.green} />
              <Text style={{ fontSize: 11, color: T.green, ...font('semibold') }}>Approve</Text>
            </Pressable>
            <Pressable
              onPress={() => review('reject')}
              disabled={reviewing}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
                paddingHorizontal: 10,
                paddingVertical: 6,
                borderRadius: 6,
                backgroundColor: T.rustSoft,
                opacity: reviewing ? 0.38 : 1,
                minHeight: 44,
              }}
            >
              <Feather name="thumbs-down" size={12} color={T.rust} />
              <Text style={{ fontSize: 11, color: T.rust, ...font('semibold') }}>Reject</Text>
            </Pressable>
          </View>
        )}
      </View>
    </Pressable>
  );
}

function TaskRow({ task, onDelete, onToggle }: { task: Task; onDelete?: () => void; onToggle?: () => void }) {
  const colors = useColors();
  const done = task.status === 'done' || task.status === 'complete' || task.status === 'completed';
  const handleLongPress = () => {
    if (!onDelete) return;
    if (Platform.OS !== 'web') {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    }
    Alert.alert('Delete Task', `Remove "${task.text}"?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: onDelete },
    ]);
  };
  return (
    <Pressable
      onLongPress={handleLongPress}
      style={[styles.listItem, { borderColor: colors.border, minHeight: 44 }]}
      delayLongPress={400}
    >
      <Pressable onPress={onToggle} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
        <Feather
          name={done ? 'check-circle' : 'circle'}
          size={18}
          color={done ? colors.primary : colors.mutedForeground}
        />
      </Pressable>
      <View style={styles.itemBody}>
        <Text
          style={[
            styles.itemTitle,
            {
              color: done ? colors.mutedForeground : colors.foreground,
              textDecorationLine: done ? 'line-through' : 'none',
            },
          ]}
        >
          {task.text}
        </Text>
        <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
          {task.status} · {
            task.priority === 1 ? 'P1' :
            task.priority === 2 ? 'P2' :
            task.priority === 3 ? 'P3' : 'No priority'
          }
        </Text>
      </View>
    </Pressable>
  );
}

// ─── Swipeable conversation row ──────────────────────────────────────────────

function ConvSwipeRow({
  conv,
  colors,
  onPress,
  onArchive,
}: {
  conv: any;
  colors: any;
  onPress: () => void;
  onArchive: (id: string, title: string) => void;
}) {
  const T = useVellumTokens();
  const swipeRef = useRef<Swipeable>(null);

  const renderRightActions = (_progress: any, dragX: Animated.AnimatedInterpolation<number>) => {
    const scale = dragX.interpolate({ inputRange: [-80, 0], outputRange: [1, 0.8], extrapolate: 'clamp' });
    return (
      <Animated.View style={{ transform: [{ scale }], justifyContent: 'center', paddingHorizontal: 12, paddingVertical: 6 }}>
        <Pressable
          onPress={() => {
            swipeRef.current?.close();
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
            onArchive(conv.id, conv.title ?? 'Untitled');
          }}
          style={{
            backgroundColor: T.gilt,
            borderRadius: 10,
            paddingHorizontal: 18,
            paddingVertical: 10,
            alignItems: 'center',
            justifyContent: 'center',
            gap: 4,
            minHeight: 52,
          }}
        >
          <Feather name="archive" size={16} color="#fff" />
          <Text style={{ color: '#fff', fontSize: 12, fontFamily: 'Inter_700Bold' }}>Archive</Text>
        </Pressable>
      </Animated.View>
    );
  };

  const rowContent = (
    <Pressable
      onPress={onPress}
      style={({ pressed }: { pressed: boolean }) => [
        styles.listItem,
        { borderColor: colors.border, opacity: pressed ? 0.7 : 1, minHeight: 44 },
      ]}
    >
      <View style={[styles.itemIcon, { backgroundColor: colors.muted }]}>
        <Feather name="message-circle" size={14} color={colors.primary} />
      </View>
      <View style={styles.itemBody}>
        <Text style={[styles.itemTitle, { color: colors.foreground }]} numberOfLines={1}>
          {conv.title || 'Untitled'}
        </Text>
        {conv.last_message ? (
          <Text style={[styles.itemMeta, { color: colors.mutedForeground }]} numberOfLines={1}>
            {conv.last_message}
          </Text>
        ) : null}
        <Text style={[styles.itemMeta, { color: colors.mutedForeground, opacity: 0.7 }]}>
          {conv.message_count ?? 0} msg{(conv.message_count ?? 0) === 1 ? '' : 's'}
          {conv.updated_at ? ` · ${new Date(conv.updated_at).toLocaleDateString()}` : ''}
        </Text>
      </View>
      <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
    </Pressable>
  );

  // Swipe gestures are not meaningful on web
  if (Platform.OS === 'web') return rowContent;

  return (
    <Swipeable
      ref={swipeRef}
      renderRightActions={renderRightActions}
      overshootRight={false}
      rightThreshold={40}
    >
      {rowContent}
    </Swipeable>
  );
}

// ─── Gaps tab — research gap summary from the intelligence pipeline ───────────

// Severity color coding tuned for the dark theme: red (high/critical),
// amber (medium), muted (low). `dot` drives the leading indicator + accent.
const GAP_SEVERITY: Record<string, { bg: string; text: string; dot: string }> = {
  critical: { bg: VELLUM_LIGHT.rustSoft,  text: VELLUM_LIGHT.rust,  dot: VELLUM_LIGHT.rust  },
  high:     { bg: VELLUM_LIGHT.rustSoft,  text: VELLUM_LIGHT.rust,  dot: VELLUM_LIGHT.rust  },
  medium:   { bg: VELLUM_LIGHT.giltSoft,  text: VELLUM_LIGHT.gilt,  dot: VELLUM_LIGHT.gilt  },
  low:      { bg: 'rgba(148,163,184,0.15)', text: '#94a3b8', dot: '#94a3b8' },
};

const SEVERITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

// ── Brainstorm tab ────────────────────────────────────────────────────────────

interface BrainstormIdea {
  id: string;
  domain: string;
  text: string;
  originality: number;
  usefulness: number;
  on_pareto_front: boolean;
  knowledge_item_id: string | null;
}

interface BrainstormSession {
  id: string;
  work_id: string;
  seed_prompt: string;
  context_type: string;
  status: 'running' | 'done' | 'failed';
  ideas: BrainstormIdea[];
  domain_count: number;
  created_at: string;
  completed_at: string | null;
}

const BRAINSTORM_CONTEXTS = [
  { value: 'general',               label: 'General'   },
  { value: 'narrative_structure',   label: 'Narrative' },
  { value: 'chapter_architecture',  label: 'Chapters'  },
  { value: 'research_planning',     label: 'Research'  },
] as const;

const BRAINSTORM_DOMAINS = [3, 5, 7] as const;

function IdeaCard({
  idea,
  sessionId,
  workId,
  colors,
  onApprove,
  approving,
}: {
  idea: BrainstormIdea;
  sessionId: string;
  workId: string;
  colors: any;
  onApprove: (sessionId: string, ideaId: string) => void;
  approving: string | null;
}) {
  const isApproved   = !!idea.knowledge_item_id;
  const isApproving  = approving === idea.id;
  const pct          = Math.round(idea.originality * 100);
  const T = useVellumTokens();
  const origColor    = pct >= 70 ? T.gilt : pct >= 45 ? T.gilt : '#94a3b8';

  return (
    <View
      style={{
        borderWidth: 1,
        borderRadius: 10,
        padding: 12,
        marginBottom: 8,
        borderColor: idea.on_pareto_front ? colors.primary + '55' : colors.border,
        backgroundColor: idea.on_pareto_front ? colors.primary + '08' : 'transparent',
        opacity: isApproved ? 0.65 : 1,
      }}
    >
      {/* Domain + scores row */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <View style={{ backgroundColor: colors.muted, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 }}>
          <Text style={{ fontSize: 10, fontWeight: '500', color: colors.mutedForeground }}>
            {idea.domain.split(' ').slice(0, 2).join(' ')}
          </Text>
        </View>
        {/* Usefulness dots */}
        <View style={{ flexDirection: 'row', gap: 3 }}>
          {[1, 2, 3, 4, 5].map((n) => (
            <View
              key={n}
              style={{
                width: 6, height: 6, borderRadius: 3,
                backgroundColor: n <= idea.usefulness ? T.gilt : colors.muted,
              }}
            />
          ))}
        </View>
        {/* Originality bar */}
        <View style={{ flex: 1, height: 3, backgroundColor: colors.muted, borderRadius: 1.5, overflow: 'hidden', minWidth: 40 }}>
          <View style={{ height: '100%', width: `${pct}%` as any, backgroundColor: origColor }} />
        </View>
        <Text style={{ fontSize: 10, fontWeight: '500', color: colors.mutedForeground }}>{pct}%</Text>
      </View>

      {/* Idea text */}
      <Text style={{ fontSize: 13, lineHeight: 19, color: colors.foreground, marginBottom: 10 }}>
        {idea.text}
      </Text>

      {/* Footer */}
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ fontSize: 10, color: colors.mutedForeground }}>
          orig {pct}% · useful {idea.usefulness}/5
        </Text>
        {isApproved ? (
          <View style={{
            paddingHorizontal: 8, paddingVertical: 3, borderRadius: 5,
            borderWidth: 1, borderColor: T.green + '44', backgroundColor: T.greenSoft,
          }}>
            <Text style={{ fontSize: 11, fontWeight: '500', color: T.green }}>✓ In knowledge</Text>
          </View>
        ) : (
          <Pressable
            onPress={() => onApprove(sessionId, idea.id)}
            disabled={isApproving}
            hitSlop={8}
            style={({ pressed }) => ({
              flexDirection: 'row', alignItems: 'center', gap: 4,
              paddingHorizontal: 10, paddingVertical: 8,
              borderRadius: 6, borderWidth: 1,
              borderColor: colors.primary + '55',
              backgroundColor: pressed ? colors.primary + '14' : 'transparent',
              opacity: isApproving ? 0.38 : 1,
              minHeight: 44,
            })}
          >
            {isApproving
              ? <ActivityIndicator size="small" color={colors.primary} />
              : <Feather name="thumbs-up" size={12} color={colors.primary} />}
            <Text style={{ fontSize: 12, fontWeight: '500', color: colors.primary }}>Use idea</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

function BrainstormTab({ workId, colors, initialSeed, initialContext }: { workId: string; colors: any; initialSeed?: string; initialContext?: string }) {
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? '';

  const [seed,         setSeed]         = React.useState(initialSeed ?? '');
  const [contextType,  setContextType]  = React.useState<string>(initialContext ?? 'general');
  const [nDomains,     setNDomains]     = React.useState<number>(5);
  const [running,      setRunning]      = React.useState(false);
  const [activeSession, setActiveSession] = React.useState<BrainstormSession | null>(null);
  const [history,      setHistory]      = React.useState<BrainstormSession[]>([]);
  const [histLoading,  setHistLoading]  = React.useState(true);
  const [showOthers,   setShowOthers]   = React.useState(false);
  const [approving,    setApproving]    = React.useState<string | null>(null);

  const loadHistory = React.useCallback(async () => {
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${workId}/brainstorm`);
      if (res.ok) setHistory(await res.json());
    } catch {
      // silently ignore — history is optional
    } finally {
      setHistLoading(false);
    }
  }, [workId, domain]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const handleRun = async () => {
    if (!seed.trim() || running) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setRunning(true);
    setActiveSession(null);
    setShowOthers(false);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${workId}/brainstorm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          seed_prompt:  seed.trim(),
          context_type: contextType,
          n_domains:    nDomains,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        Alert.alert('Brainstorm failed', (body as any).detail ?? 'Something went wrong');
        return;
      }
      const session: BrainstormSession = await res.json();
      setActiveSession(session);
      await loadHistory();
    } catch (e: any) {
      Alert.alert('Connection error', e.message ?? 'Could not reach the server');
    } finally {
      setRunning(false);
    }
  };

  const handleApprove = async (sessionId: string, ideaId: string) => {
    setApproving(ideaId);
    try {
      const res = await mobileFetch(
        `https://${domain}/api/works/${workId}/brainstorm/${sessionId}/ideas/${ideaId}/approve`,
        { method: 'POST' }
      );
      if (!res.ok) throw new Error('Approval failed');
      const data: any = await res.json();
      // Patch the active session locally so the button updates immediately
      setActiveSession((prev) =>
        prev
          ? {
              ...prev,
              ideas: prev.ideas.map((i) =>
                i.id === ideaId ? { ...i, knowledge_item_id: data.knowledge_item_id } : i
              ),
            }
          : prev
      );
    } catch (e: any) {
      Alert.alert('Error', e.message ?? 'Approval failed');
    } finally {
      setApproving(null);
    }
  };

  const pareto = (activeSession?.ideas ?? []).filter((i) => i.on_pareto_front);
  const others = (activeSession?.ideas ?? []).filter((i) => !i.on_pareto_front);

  return (
    <ScrollView
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 16, paddingBottom: insets.bottom + 24 }}
      showsVerticalScrollIndicator={false}
      keyboardShouldPersistTaps="handled"
    >
      {/* ── Seed prompt ─────────────────────────────────────────────────────── */}
      <TextInput
        style={{
          borderWidth: 1,
          borderColor: colors.border,
          borderRadius: 8,
          paddingHorizontal: 12,
          paddingVertical: 10,
          fontSize: 14,
          color: colors.foreground,
          backgroundColor: colors.card,
          marginBottom: 10,
          minHeight: 72,
          textAlignVertical: 'top',
        }}
        value={seed}
        onChangeText={setSeed}
        placeholder="What question or topic should we explore? (e.g. How should I structure chapter 3?)"
        placeholderTextColor={colors.mutedForeground}
        multiline
        editable={!running}
        returnKeyType="default"
      />

      {/* ── Context type chips ──────────────────────────────────────────────── */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 6, paddingBottom: 10 }}
      >
        {BRAINSTORM_CONTEXTS.map((opt) => {
          const active = contextType === opt.value;
          return (
            <Pressable
              key={opt.value}
              onPress={() => setContextType(opt.value)}
              hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }}
              style={{
                paddingHorizontal: 12, paddingVertical: 5, borderRadius: 12, borderWidth: 1,
                borderColor: active ? colors.primary : colors.border,
                backgroundColor: active ? colors.primary + '18' : 'transparent',
              }}
            >
              <Text style={{ fontSize: 12, fontWeight: '500', color: active ? colors.primary : colors.mutedForeground }}>
                {opt.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {/* ── Domain count chips ──────────────────────────────────────────────── */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <Text style={{ fontSize: 12, fontWeight: '500', color: colors.mutedForeground }}>Domains:</Text>
        {BRAINSTORM_DOMAINS.map((n) => {
          const active = nDomains === n;
          return (
            <Pressable
              key={n}
              onPress={() => setNDomains(n)}
              hitSlop={{ top: 8, bottom: 8, left: 6, right: 6 }}
              style={{
                paddingHorizontal: 12, paddingVertical: 5, borderRadius: 12, borderWidth: 1,
                borderColor: active ? colors.primary : colors.border,
                backgroundColor: active ? colors.primary + '18' : 'transparent',
              }}
            >
              <Text style={{ fontSize: 12, fontWeight: '500', color: active ? colors.primary : colors.mutedForeground }}>
                {n}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {/* ── Run button ──────────────────────────────────────────────────────── */}
      <Pressable
        onPress={handleRun}
        disabled={!seed.trim() || running}
        style={({ pressed }) => ({
          flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
          gap: 8, paddingVertical: 13, borderRadius: 10, minHeight: 44,
          backgroundColor: (!seed.trim() || running)
            ? colors.muted
            : pressed ? colors.primary + 'cc' : colors.primary,
          opacity: (!seed.trim() || running) ? 0.38 : 1,
        })}
      >
        {running
          ? <ActivityIndicator color="#fff" size="small" />
          : <Feather name="zap" size={16} color={(!seed.trim()) ? colors.mutedForeground : '#fff'} />}
        <Text style={{
          fontSize: 14, ...font('semibold'),
          color: (!seed.trim() || running) ? colors.mutedForeground : '#fff',
        }}>
          {running ? 'Generating ideas…' : 'Generate ideas'}
        </Text>
      </Pressable>

      {/* ── Running indicator ──────────────────────────────────────────────── */}
      {running && (
        <View style={{ alignItems: 'center', paddingVertical: 24, gap: 8 }}>
          <Text style={{ fontSize: 13, color: colors.mutedForeground }}>
            Thinking across {nDomains} domains…
          </Text>
          <Text style={{ fontSize: 11, color: colors.mutedForeground, textAlign: 'center', maxWidth: 240 }}>
            This usually takes 15–30 seconds. The AI is generating divergent ideas across different fields.
          </Text>
        </View>
      )}

      {/* ── Session results ────────────────────────────────────────────────── */}
      {activeSession && !running && (
        <View style={{ marginTop: 20 }}>
          {/* Meta */}
          <Text style={{ fontSize: 12, color: colors.mutedForeground, marginBottom: 2 }} numberOfLines={2}>
            "{activeSession.seed_prompt.slice(0, 100)}{activeSession.seed_prompt.length > 100 ? '…' : ''}"
          </Text>
          <Text style={{ fontSize: 11, color: colors.mutedForeground, marginBottom: 14 }}>
            {activeSession.domain_count} domains · {activeSession.ideas.length} ideas
            {activeSession.completed_at
              ? ` · ${new Date(activeSession.completed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
              : ''}
          </Text>

          {/* Pareto front */}
          {pareto.length > 0 && (
            <View style={{ marginBottom: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <Feather name="zap" size={14} color={colors.primary} />
                <Text style={{ fontSize: 13, fontWeight: '600', color: colors.foreground }}>
                  Best ideas
                </Text>
                <Text style={{ fontSize: 11, color: colors.mutedForeground }}>(originality × usefulness)</Text>
              </View>
              {pareto.map((idea) => (
                <IdeaCard
                  key={idea.id}
                  idea={idea}
                  sessionId={activeSession.id}
                  workId={workId}
                  colors={colors}
                  onApprove={handleApprove}
                  approving={approving}
                />
              ))}
            </View>
          )}

          {/* Alternate ideas */}
          {others.length > 0 && (
            <View>
              <Pressable
                onPress={() => setShowOthers((v) => !v)}
                hitSlop={8}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 4, paddingVertical: 8, minHeight: 36 }}
              >
                <Feather
                  name={showOthers ? 'chevron-up' : 'chevron-down'}
                  size={14}
                  color={colors.mutedForeground}
                />
                <Text style={{ fontSize: 13, color: colors.mutedForeground }}>
                  {others.length} alternate idea{others.length !== 1 ? 's' : ''}
                </Text>
              </Pressable>
              {showOthers && others.map((idea) => (
                <IdeaCard
                  key={idea.id}
                  idea={idea}
                  sessionId={activeSession.id}
                  workId={workId}
                  colors={colors}
                  onApprove={handleApprove}
                  approving={approving}
                />
              ))}
            </View>
          )}
        </View>
      )}

      {/* ── Past sessions ──────────────────────────────────────────────────── */}
      {!histLoading && history.length > 0 && (
        <View style={{
          marginTop: 24,
          borderTopWidth: StyleSheet.hairlineWidth,
          borderTopColor: colors.border,
          paddingTop: 16,
        }}>
          <Text style={{
            fontSize: 11, fontWeight: '600', color: colors.mutedForeground,
            letterSpacing: 0.8, textTransform: 'uppercase', marginBottom: 8,
          }}>
            Past sessions
          </Text>
          {history.slice(0, 6).map((s) => (
            <Pressable
              key={s.id}
              onPress={() => { setActiveSession(s); setShowOthers(false); }}
              style={({ pressed }) => ({
                flexDirection: 'row', alignItems: 'center', gap: 8,
                paddingVertical: 10,
                borderBottomWidth: StyleSheet.hairlineWidth,
                borderBottomColor: colors.border,
                minHeight: 44,
                opacity: pressed ? 0.7 : 1,
              })}
            >
              <Feather name="clock" size={13} color={colors.mutedForeground} />
              <Text style={{ flex: 1, fontSize: 13, color: colors.foreground }} numberOfLines={1}>
                {s.seed_prompt}
              </Text>
              <Text style={{ fontSize: 11, color: colors.mutedForeground, marginRight: 4 }}>
                {s.ideas.length}
              </Text>
              <View style={{
                paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, borderWidth: 1,
                borderColor: s.status === 'done' ? T.green + '44' : s.status === 'failed' ? T.rust + '44' : T.gilt + '44',
                backgroundColor: s.status === 'done' ? T.greenSoft : s.status === 'failed' ? T.rustSoft : T.giltSoft,
              }}>
                <Text style={{ fontSize: 10, fontWeight: '500', color: s.status === 'done' ? T.green : s.status === 'failed' ? T.rust : T.gilt }}>
                  {s.status}
                </Text>
              </View>
            </Pressable>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

function GapsTab({
  workId,
  colors,
  onResearch,
  onCreateTask,
  onBrainstorm,
  pipelineActive,
}: {
  workId: string;
  colors: any;
  onResearch: (gapTitle: string) => void;
  onCreateTask: (taskText: string) => void;
  onBrainstorm: (seed: string) => void;
  /** When true (pipeline has stages remaining), poll every 15 s so new gaps
   *  surface automatically. Polling stops once the pipeline reaches B17 or
   *  when no pipeline exists (same terminal criterion as the web Gaps tab). */
  pipelineActive?: boolean;
}) {
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchGaps = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${workId}/gaps`);
      if (!res.ok) throw new Error('gaps error');
      setData(await res.json());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [workId, domain]);

  // Initial fetch
  useEffect(() => { fetchGaps(); }, [fetchGaps]);

  // Live polling — mirrors the web Gaps tab: 15 s while the pipeline is active,
  // stops automatically when the pipeline finishes (pipelineActive goes false).
  useEffect(() => {
    if (!pipelineActive) return;
    const iv = setInterval(fetchGaps, 15_000);
    return () => clearInterval(iv);
  }, [pipelineActive, fetchGaps]);

  if (loading) {
    return (
      <View style={{ flex: 1, paddingTop: 8 }}>
        {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
      </View>
    );
  }
  if (error) {
    return (
      <EmptyState
        icon="alert-circle"
        title="Could not load gaps"
        body="Check your connection and try again."
        cta="Retry"
        onCta={fetchGaps}
      />
    );
  }

  const rawGaps: any[] = data?.gaps ?? [];
  // Rank high → medium → low (critical sorts above high).
  const gaps = [...rawGaps].sort(
    (a, b) =>
      (SEVERITY_RANK[a.severity ?? 'medium'] ?? 2) - (SEVERITY_RANK[b.severity ?? 'medium'] ?? 2),
  );
  const coverage: number | null = data?.coverage_pct != null ? Number(data.coverage_pct) : null;
  const isComplete = gaps.length === 0 || coverage === 100;

  return (
    <ScrollView
      contentContainerStyle={[styles.listPad, { paddingBottom: insets.bottom + 24 }]}
      showsVerticalScrollIndicator={false}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={fetchGaps} tintColor={colors.primary} />}
    >
      {/* Coverage indicator */}
      <View style={{ marginBottom: 16 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 }}>
          <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
            Coverage · {gaps.length} gap{gaps.length !== 1 ? 's' : ''}
          </Text>
          <Text style={[styles.itemMeta, { color: colors.foreground, fontFamily: 'Inter_600SemiBold' }]}>
            {coverage != null ? `${coverage}%` : '—'}
          </Text>
        </View>
        <View style={{ height: 6, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
          <View
            style={{
              height: '100%',
              width: `${Math.max(0, Math.min(100, coverage ?? 0))}%` as any,
              backgroundColor: isComplete ? T.green : colors.primary,
              borderRadius: 3,
            }}
          />
        </View>
      </View>

      {isComplete ? (
        <View style={styles.centered}>
          <Feather name="check-circle" size={32} color={T.green} />
          <Text style={[styles.emptyText, { color: colors.foreground, fontFamily: 'Inter_500Medium' }]}>
            No gaps — coverage looks complete
          </Text>
        </View>
      ) : (
        gaps.map((g: any, i: number) => {
          const sev = (g.severity ?? 'medium') as string;
          const gCol = GAP_SEVERITY[sev] ?? GAP_SEVERITY.medium;
          const isHigh = sev === 'high' || sev === 'critical';
          const gapTitle = g.title ?? g.kind ?? 'Research gap';
          return (
            <View
              key={i}
              style={[
                styles.listItem,
                { borderColor: colors.border, flexDirection: 'column', gap: 6, alignItems: 'flex-start' },
              ]}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: gCol.dot }} />
                <View style={[styles.statusBadge, { backgroundColor: gCol.bg, paddingHorizontal: 8, paddingVertical: 2 }]}>
                  <Text style={[styles.statusText, { color: gCol.text }]}>{sev}</Text>
                </View>
                <Text style={[styles.itemTitle, { color: colors.foreground, flex: 1 }]} numberOfLines={2}>
                  {gapTitle}
                </Text>
              </View>
              {g.description ? (
                <Text style={[styles.itemMeta, { color: colors.mutedForeground, lineHeight: 16 }]} numberOfLines={4}>
                  {g.description}
                </Text>
              ) : null}
              {(
                <View style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
                  <Pressable
                    onPress={() => onCreateTask(`Research gap: ${gapTitle}`)}
                    style={({ pressed }) => ({
                      flexDirection: 'row', alignItems: 'center', gap: 4,
                      paddingHorizontal: 10, paddingVertical: 8, borderRadius: 6,
                      backgroundColor: colors.muted, opacity: pressed ? 0.7 : 1,
                      minHeight: 44,
                    })}
                  >
                    <Feather name="plus" size={12} color={colors.primary} />
                    <Text style={{ fontSize: 12, color: colors.primary, ...font('semibold') }}>
                      Add Task
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => onResearch(gapTitle)}
                    style={({ pressed }) => ({
                      flexDirection: 'row', alignItems: 'center', gap: 4,
                      paddingHorizontal: 10, paddingVertical: 8, borderRadius: 6,
                      backgroundColor: colors.primary, opacity: pressed ? 0.7 : 1,
                      minHeight: 44,
                    })}
                  >
                    <Feather name="message-circle" size={12} color={colors.primaryForeground} />
                    <Text style={{ fontSize: 12, color: colors.primaryForeground, ...font('semibold') }}>
                      Discuss →
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => onBrainstorm(gapTitle)}
                    style={({ pressed }) => ({
                      flexDirection: 'row', alignItems: 'center', gap: 4,
                      paddingHorizontal: 10, paddingVertical: 8, borderRadius: 6,
                      borderWidth: 1, borderColor: T.giltLine,
                      backgroundColor: pressed ? T.giltSoft : 'transparent',
                      opacity: pressed ? 0.7 : 1,
                      minHeight: 44,
                    })}
                  >
                    <Feather name="zap" size={12} color={T.gilt} />
                    <Text style={{ fontSize: 12, color: T.gilt, ...font('semibold') }}>
                      Brainstorm
                    </Text>
                  </Pressable>
                </View>
              )}
            </View>
          );
        })
      )}

      {/* ── Suggested research queries ────────────────────────────────────────
          Each card shows the query text and two action buttons:
          • Brainstorm → opens the Ideas tab with research_planning context
          • Discuss →   creates a work-linked chat conversation with a draft
                        message so users can research the query via AI chat
      ── */}
      {Array.isArray(data?.suggested_queries) && (data.suggested_queries as string[]).length > 0 && (
        <View style={{ marginTop: 24 }}>
          <Text
            style={{
              fontSize: 11,
              fontFamily: 'Inter_600SemiBold',
              color: colors.mutedForeground,
              letterSpacing: 0.6,
              textTransform: 'uppercase',
              marginBottom: 4,
            }}
          >
            Suggested Research Queries
          </Text>
          <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginBottom: 10, opacity: 0.7 }}>
            Brainstorm ideas or discuss each query in chat
          </Text>
          {(data.suggested_queries as string[]).map((q: string, i: number) => (
            <View
              key={i}
              style={{
                borderRadius: 8,
                borderWidth: 1,
                borderColor: colors.border,
                backgroundColor: colors.card,
                marginBottom: 8,
                overflow: 'hidden',
              }}
            >
              {/* Query text */}
              <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingHorizontal: 12, paddingVertical: 10 }}>
                <Feather name="zap" size={13} color={colors.primary} style={{ marginTop: 2 }} />
                <Text
                  style={{
                    flex: 1,
                    fontSize: 13,
                    fontFamily: 'Inter_400Regular',
                    color: colors.foreground,
                    lineHeight: 18,
                  }}
                  numberOfLines={3}
                >
                  {q}
                </Text>
              </View>

              {/* Action row: Brainstorm | Discuss */}
              <View style={{ flexDirection: 'row', borderTopWidth: 1, borderTopColor: colors.border }}>
                <Pressable
                  onPress={() => onBrainstorm(q)}
                  style={({ pressed }) => ({
                    flex: 1,
                    alignItems: 'center',
                    paddingVertical: 9,
                    backgroundColor: pressed ? colors.muted : 'transparent',
                  })}
                  accessibilityRole="button"
                  accessibilityLabel={`Brainstorm: ${q}`}
                >
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>
                    Brainstorm →
                  </Text>
                </Pressable>

                <View style={{ width: 1, backgroundColor: colors.border }} />

                <Pressable
                  onPress={() => onResearch(q)}
                  style={({ pressed }) => ({
                    flex: 1,
                    alignItems: 'center',
                    paddingVertical: 9,
                    backgroundColor: pressed ? colors.muted : 'transparent',
                  })}
                  accessibilityRole="button"
                  accessibilityLabel={`Discuss: ${q}`}
                >
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground }}>
                    Discuss →
                  </Text>
                </Pressable>
              </View>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

// ─── Completeness Tab ─────────────────────────────────────────────────────────
//
// Mobile equivalent of the web CompletenessTab. Fetches
// GET /api/works/:id/completeness and polls every 10 s while the pipeline is
// active — mirrors the same pipelineActive flag and interval used by GapsTab.

interface ComplDimension {
  name: string;
  label?: string;
  score: number;         // 0–100 overall quality score for the dimension
  current?: number;      // raw current value (words / chapters)
  target?: number;       // target value for progress bars
}

interface ComplReport {
  overall: number;
  readiness: string;
  summary?: string;
  dimensions: ComplDimension[];
}

/** Color ramp matching the web: green ≥ 70, amber ≥ 30, rust below. */
function barColor(pct: number, T: ReturnType<typeof useVellumTokens>): string {
  if (pct >= 70) return T.green;
  if (pct >= 30) return T.gilt;
  return T.rust;
}

function CompletenessTab({
  workId,
  pipelineActive,
}: {
  workId: string;
  pipelineActive?: boolean;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

  const [data, setData] = useState<ComplReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchCompleteness = useCallback(async () => {
    setError(false);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${workId}/completeness`);
      if (!res.ok) throw new Error('completeness error');
      setData(await res.json());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [workId, domain]);

  // Initial fetch on mount
  useEffect(() => { fetchCompleteness(); }, [fetchCompleteness]);

  // Live polling — 10 s while the pipeline is active (same pattern as GapsTab).
  // Automatically stops once pipelineActive goes false (B17 terminal stage).
  useEffect(() => {
    if (!pipelineActive) return;
    const iv = setInterval(fetchCompleteness, 10_000);
    return () => clearInterval(iv);
  }, [pipelineActive, fetchCompleteness]);

  if (loading) {
    return (
      <View style={{ flex: 1, paddingTop: 8 }}>
        {[...Array(4)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
      </View>
    );
  }

  if (error || !data) {
    return (
      <ErrorScreen
        message="Could not load coverage"
        detail="Re-extract documents, then try again."
        onRetry={fetchCompleteness}
      />
    );
  }

  // Derive a readiness accent colour
  const overall = data.overall ?? 0;
  const readinessColor = overall >= 80 ? T.green : overall >= 50 ? T.gilt : T.rust;
  const readinessSoft  = overall >= 80 ? T.greenSoft : overall >= 50 ? T.giltSoft : T.rustSoft;
  const readinessLine  = overall >= 80 ? T.green + '55' : overall >= 50 ? T.giltLine : T.rust + '55';

  // Separate content (word-count) and structure (chapter) dims for explicit
  // current/target progress bars — matching the web implementation.
  const contentDim  = data.dimensions.find(d => d.name === 'content');
  const structDim   = data.dimensions.find(d => d.name === 'structure');
  const otherDims   = data.dimensions.filter(d => d.name !== 'content' && d.name !== 'structure');

  return (
    <ScrollView
      contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 32, paddingTop: 12 }}
      refreshControl={
        <RefreshControl refreshing={false} onRefresh={fetchCompleteness} tintColor={colors.primary} />
      }
      showsVerticalScrollIndicator={false}
    >
      {/* ── Overall readiness banner ────────────────────────────────────────── */}
      <View style={[{
        borderRadius: 12, borderWidth: 1, padding: 16, marginBottom: 14,
        flexDirection: 'row', alignItems: 'center', gap: 14,
        backgroundColor: readinessSoft, borderColor: readinessLine,
      }]}>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 10, fontFamily: 'Inter_700Bold', color: readinessColor,
                         textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 2 }}>
            Readiness
          </Text>
          <Text style={{ fontSize: 18, fontFamily: 'Fraunces_700Bold', color: readinessColor }}>
            {data.readiness}
          </Text>
          {data.summary ? (
            <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 4 }}
                  numberOfLines={3}>
              {data.summary}
            </Text>
          ) : null}
        </View>
        <Text style={{ fontSize: 36, fontFamily: 'Inter_700Bold', color: readinessColor }}>
          {overall}%
        </Text>
      </View>

      {/* ── Overall progress bar ────────────────────────────────────────────── */}
      <View style={{ marginBottom: 20 }}>
        <View style={{ height: 8, backgroundColor: colors.muted, borderRadius: 4, overflow: 'hidden' }}>
          <View style={{
            height: 8,
            width: `${Math.min(100, overall)}%` as any,
            backgroundColor: readinessColor,
            borderRadius: 4,
          }} />
        </View>
      </View>

      {/* ── Content (words) and structure (chapters) progress bars ─────────── */}
      {(contentDim || structDim) && (
        <View style={{ backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border,
                       borderRadius: 10, padding: 14, marginBottom: 14, gap: 12 }}>
          <Text style={{ fontSize: 10, fontFamily: 'Inter_700Bold', color: colors.mutedForeground,
                         textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 2 }}>
            Progress vs targets
          </Text>

          {contentDim && (contentDim.target ?? 0) > 0 && (() => {
            const pct = Math.min(100, Math.round(((contentDim.current ?? 0) / contentDim.target!) * 100));
            return (
              <View>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}>Words</Text>
                  <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                    {Number(contentDim.current ?? 0).toLocaleString()} / {Number(contentDim.target).toLocaleString()}
                    {'  '}<Text style={{ opacity: 0.65 }}>({pct}%)</Text>
                  </Text>
                </View>
                <View style={{ height: 6, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
                  <View style={{ height: 6, width: `${pct}%` as any, backgroundColor: barColor(pct, T), borderRadius: 3 }} />
                </View>
              </View>
            );
          })()}

          {structDim && (structDim.target ?? 0) > 0 && (() => {
            const pct = Math.min(100, Math.round(((structDim.current ?? 0) / structDim.target!) * 100));
            return (
              <View>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}>Chapters</Text>
                  <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                    {structDim.current ?? 0} / {structDim.target}
                    {'  '}<Text style={{ opacity: 0.65 }}>({pct}%)</Text>
                  </Text>
                </View>
                <View style={{ height: 6, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
                  <View style={{ height: 6, width: `${pct}%` as any, backgroundColor: barColor(pct, T), borderRadius: 3 }} />
                </View>
              </View>
            );
          })()}
        </View>
      )}

      {/* ── Dimension breakdown ─────────────────────────────────────────────── */}
      {otherDims.length > 0 && (
        <View style={{ backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border,
                       borderRadius: 10, padding: 14, marginBottom: 14, gap: 10 }}>
          <Text style={{ fontSize: 10, fontFamily: 'Inter_700Bold', color: colors.mutedForeground,
                         textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 2 }}>
            Dimensions
          </Text>
          {otherDims.map(dim => (
            <View key={dim.name}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}>
                  {dim.label ?? dim.name}
                </Text>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: barColor(dim.score, T) }}>
                  {dim.score}%
                </Text>
              </View>
              <View style={{ height: 4, backgroundColor: colors.muted, borderRadius: 2, overflow: 'hidden' }}>
                <View style={{ height: 4, width: `${Math.min(100, dim.score)}%` as any,
                               backgroundColor: barColor(dim.score, T) + 'cc', borderRadius: 2 }} />
              </View>
            </View>
          ))}
        </View>
      )}

      {/* ── Live polling badge — reassures the user the data is refreshing ─── */}
      {pipelineActive && (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, justifyContent: 'center', marginTop: 4 }}>
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: T.gilt }} />
          <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
            Updating every 10 s while pipeline is active
          </Text>
        </View>
      )}
    </ScrollView>
  );
}

// ─── Trailer Architect (mobile) ───────────────────────────────────────────────

const PHASE_LABELS_MOBILE: Record<string, string> = {
  loading:  'Loading book content…',
  analyze:  'Analysing book…',
  concept:  'Generating concepts…',
  method:   'Selecting production method…',
  plan:     'Building shotlist + narration…',
  validate: 'Validating package…',
  package:  'Assembling package…',
  done:     'Complete',
  error:    'Failed',
};

interface TrailerListItemMobile {
  id: string;
  status: string;
  phase: string;
  created_at: string;
  error?: string;
}

interface TrailerFindingMobile {
  severity: string;
  code: string;
  msg: string;
}

interface TrailerPkgMobile {
  status: string;
  status_badge: string;
  generated: string;
  brief: Record<string, unknown>;
  concept: Record<string, unknown>;
  plan: Record<string, unknown>;
  docs: Record<string, string>;
  shot_prompts?: Record<string, string>;
  validation: { findings: TrailerFindingMobile[] };
}

function CopyButtonMobile({ text, label, colors }: { text: string; label: string; colors: any }) {
  const T = useVellumTokens();
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await Clipboard.setStringAsync(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* non-fatal */ }
  };
  return (
    <Pressable
      onPress={handleCopy}
      style={({ pressed }) => ({
        flexDirection: 'row', alignItems: 'center', gap: 4,
        paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6,
        borderWidth: 1,
        borderColor: copied ? T.green + '55' : colors.border,
        backgroundColor: copied ? T.greenSoft : (pressed ? colors.muted + '88' : colors.muted + '33'),
      })}
    >
      <Feather name={copied ? 'check' : 'copy'} size={11} color={copied ? T.green : colors.mutedForeground} />
      <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: copied ? T.green : colors.mutedForeground }}>
        {copied ? 'Copied!' : label}
      </Text>
    </Pressable>
  );
}

interface TrailerPackageMobile {
  id: string;
  status: string;
  phase: string;
  error?: string;
  package?: TrailerPkgMobile;
}

function TrailerStatusBadgeMobile({ status, phase, colors }: { status: string; phase: string; colors: any }) {
  const T = useVellumTokens();
  if (status === 'running') {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <ActivityIndicator size="small" color={colors.primary} style={{ transform: [{ scale: 0.65 }] }} />
        <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: colors.primary }} numberOfLines={1}>
          {PHASE_LABELS_MOBILE[phase] ?? phase}
        </Text>
      </View>
    );
  }
  if (status === 'ready') {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <Feather name="check-circle" size={11} color={T.green} />
        <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: T.green }}>READY</Text>
      </View>
    );
  }
  if (status === 'blocked') {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
        <Feather name="alert-circle" size={11} color={T.gilt} />
        <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: T.gilt }}>BLOCKED</Text>
      </View>
    );
  }
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
      <Feather name="x-circle" size={11} color={T.rust} />
      <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: T.rust }}>FAILED</Text>
    </View>
  );
}

function TrailerPackageViewMobile({ pkg, colors }: { pkg: TrailerPkgMobile; colors: any }) {
  const T = useVellumTokens();
  const [activeDoc, setActiveDoc] = useState<string | null>(null);

  // ── Narration playback ──────────────────────────────────────────────────────
  type NarrState = 'idle' | 'loading' | 'playing';
  const [narrState, setNarrState] = useState<NarrState>('idle');
  const narrPlayerRef = useRef<AudioPlayer | null>(null);
  const narrDomain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

  useEffect(() => {
    return () => { narrPlayerRef.current?.remove(); };
  }, []);

  const handlePlayNarration = async () => {
    const text = pkg.docs?.narration_script;
    if (!text) return;

    // Stop — tap while playing
    if (narrState === 'playing') {
      narrPlayerRef.current?.remove();
      narrPlayerRef.current = null;
      setNarrState('idle');
      return;
    }

    setNarrState('loading');
    try {
      await setAudioModeAsync({ playsInSilentMode: true });
      const token = getApiToken();

      // Synthesise via the same endpoint TtsContext uses (return_url → JSON path)
      const ttsRes = await mobileFetch(`https://${narrDomain}/api/studio/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice: 'af_heart', speed: 1.0, return_url: true }),
      });
      if (!ttsRes.ok) {
        const err = await ttsRes.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `TTS error (${ttsRes.status})`);
      }
      const { path } = await ttsRes.json() as { path: string };

      const serveUri =
        `https://${narrDomain}/api/studio/outputs/serve?path=${encodeURIComponent(path)}`;

      const player = createAudioPlayer({
        uri: serveUri,
        headers: token ? { authorization: `Bearer ${token}` } : undefined,
      });
      narrPlayerRef.current = player;
      player.play();
      setNarrState('playing');

      player.addListener('playbackStatusUpdate', (status) => {
        // Guard against stale callbacks from a replaced player
        if (narrPlayerRef.current !== player) return;
        if (!status.playing && status.currentTime > 0 && status.duration > 0
            && status.currentTime >= status.duration - 0.5) {
          player.remove();
          narrPlayerRef.current = null;
          setNarrState('idle');
        }
      });
    } catch (e: any) {
      narrPlayerRef.current?.remove();
      narrPlayerRef.current = null;
      setNarrState('idle');
      Alert.alert('Playback failed', e?.message ?? 'Could not play narration');
    }
  };

  const statusReady      = pkg.status === 'READY';
  const criticalFindings = (pkg.validation.findings ?? []).filter(f => f.severity === 'critical');
  const docKeys          = Object.keys(pkg.docs ?? {});
  const activeDocText    = activeDoc ? (pkg.docs[activeDoc] ?? '') : '';

  const logline      = typeof pkg.brief.logline   === 'string' ? pkg.brief.logline   : '';
  const genre        = typeof pkg.brief.genre     === 'string' ? pkg.brief.genre     : '';
  const tone         = Array.isArray(pkg.brief.tone) ? (pkg.brief.tone as string[]).slice(0, 3) : [];
  const conceptName  = typeof pkg.concept.name    === 'string' ? pkg.concept.name    : '';
  const conceptAngle = typeof pkg.concept.angle   === 'string' ? pkg.concept.angle   : '';
  const conceptBeats = Array.isArray(pkg.concept.beats) ? (pkg.concept.beats as string[]) : [];
  const planRaw      = pkg.plan as Record<string, unknown>;
  const shotCount    = Array.isArray(planRaw?.shots) ? (planRaw.shots as unknown[]).length : 0;
  const duration     = typeof planRaw?.duration === 'number' ? String(planRaw.duration) : '?';

  // Copy targets: first shot image prompt + music brief prompt
  const shots        = Array.isArray(planRaw?.shots) ? (planRaw.shots as Record<string, unknown>[]) : [];
  const firstShot    = shots[0] ?? {};
  const firstImgPrompt = (pkg.shot_prompts?.['shot_00'] ??
    (typeof firstShot.image_prompt === 'string' ? firstShot.image_prompt : ''));
  const musicRaw     = planRaw?.music as Record<string, unknown> | undefined;
  const musicPrompt  = typeof musicRaw?.prompt === 'string' ? musicRaw.prompt : '';

  return (
    <View style={{ gap: 12, paddingTop: 8 }}>
      {/* Status row */}
      <View style={{
        flexDirection: 'row', alignItems: 'center', gap: 8,
        paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1,
        borderColor: statusReady ? T.green + '66' : T.giltLine,
        backgroundColor: statusReady ? T.greenSoft : T.giltSoft,
      }}>
        <Feather name={statusReady ? 'check-circle' : 'alert-circle'} size={13} color={statusReady ? T.green : T.gilt} />
        <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: statusReady ? T.green : T.gilt, flex: 1 }}>
          {pkg.status_badge}
        </Text>
        <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: statusReady ? T.green : T.gilt, opacity: 0.7 }}>
          {pkg.generated}
        </Text>
      </View>

      {/* Critical findings */}
      {criticalFindings.map((f, i) => (
        <View key={i} style={{
          flexDirection: 'row', alignItems: 'flex-start', gap: 6,
          paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8,
          borderWidth: 1, borderColor: T.rust + '20', backgroundColor: T.rustSoft,
        }}>
          <Feather name="x-circle" size={12} color={T.rust} style={{ marginTop: 1 }} />
          <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: T.rust, flex: 1 }}>
            <Text style={{ fontFamily: 'Inter_600SemiBold' }}>{f.code}</Text> — {f.msg}
          </Text>
        </View>
      ))}

      {/* Book brief */}
      <View style={{
        padding: 10, borderRadius: 8, borderWidth: 1, borderColor: colors.border,
        backgroundColor: colors.muted + '22', gap: 6,
      }}>
        <Text style={{ fontSize: 9, fontFamily: 'Inter_700Bold', color: colors.mutedForeground, letterSpacing: 1, textTransform: 'uppercase' }}>
          Book Brief
        </Text>
        {logline ? (
          <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 17, fontStyle: 'italic' }}>
            "{logline}"
          </Text>
        ) : null}
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4 }}>
          {genre ? (
            <View style={{ paddingHorizontal: 7, paddingVertical: 2, borderRadius: 10, backgroundColor: colors.muted, borderWidth: 1, borderColor: colors.border }}>
              <Text style={{ fontSize: 9, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>{genre}</Text>
            </View>
          ) : null}
          {tone.map((t, i) => (
            <View key={i} style={{ paddingHorizontal: 7, paddingVertical: 2, borderRadius: 10, borderWidth: 1, borderColor: colors.border }}>
              <Text style={{ fontSize: 9, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>{t}</Text>
            </View>
          ))}
        </View>
      </View>

      {/* Chosen concept */}
      {conceptName ? (
        <View style={{
          padding: 10, borderRadius: 8, borderWidth: 1,
          borderColor: colors.primary + '33', backgroundColor: colors.primary + '08', gap: 4,
        }}>
          <Text style={{ fontSize: 9, fontFamily: 'Inter_700Bold', color: colors.primary + 'aa', letterSpacing: 1, textTransform: 'uppercase' }}>
            Chosen Concept
          </Text>
          <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>{conceptName}</Text>
          {conceptAngle ? (
            <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>{conceptAngle}</Text>
          ) : null}
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4, marginTop: 2 }}>
            {conceptBeats.map((b, i) => (
              <View key={i} style={{ paddingHorizontal: 7, paddingVertical: 2, borderRadius: 10, backgroundColor: colors.muted + '44', borderWidth: 1, borderColor: colors.border }}>
                <Text style={{ fontSize: 9, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>{b}</Text>
              </View>
            ))}
          </View>
        </View>
      ) : null}

      {/* Shot count */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <Feather name="film" size={12} color={colors.mutedForeground} />
        <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
          {shotCount} shot{shotCount !== 1 ? 's' : ''} planned · {duration}s runtime
        </Text>
      </View>

      {/* Copy prompts — first shot image + music brief */}
      {(firstImgPrompt || musicPrompt) ? (
        <View style={{
          borderRadius: 8, borderWidth: 1, borderColor: colors.border,
          backgroundColor: colors.card, padding: 10, gap: 8,
        }}>
          <Text style={{ fontSize: 9, fontFamily: 'Inter_700Bold', color: colors.mutedForeground, letterSpacing: 1, textTransform: 'uppercase' }}>
            Prompt Copy
          </Text>
          {firstImgPrompt ? (
            <View style={{ gap: 4 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>
                  Shot 00 — Image Prompt
                </Text>
                <CopyButtonMobile text={firstImgPrompt.trim()} label="Copy" colors={colors} />
              </View>
              <View style={{ backgroundColor: colors.muted + '44', borderRadius: 6, padding: 8 }}>
                <Text
                  numberOfLines={3}
                  style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 15 }}
                >
                  {firstImgPrompt.trim()}
                </Text>
              </View>
            </View>
          ) : null}
          {musicPrompt ? (
            <View style={{ gap: 4 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                  <Feather name="music" size={10} color={colors.mutedForeground} />
                  <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>
                    Music Brief Prompt
                  </Text>
                </View>
                <CopyButtonMobile text={musicPrompt.trim()} label="Copy" colors={colors} />
              </View>
              <View style={{ backgroundColor: colors.muted + '44', borderRadius: 6, padding: 8 }}>
                <Text
                  numberOfLines={2}
                  style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 15 }}
                >
                  {musicPrompt.trim()}
                </Text>
              </View>
            </View>
          ) : null}
        </View>
      ) : null}

      {/* ── Play Narration — shown when narration_script is in the package ── */}
      {pkg.docs?.narration_script ? (
        <View style={{
          flexDirection: 'row', alignItems: 'center', gap: 10,
          paddingHorizontal: 12, paddingVertical: 10, borderRadius: 8, borderWidth: 1,
          borderColor: colors.primary + '44', backgroundColor: colors.primary + '0a',
        }}>
          <Pressable
            onPress={handlePlayNarration}
            disabled={narrState === 'loading'}
            style={({ pressed }) => ({
              flexDirection: 'row', alignItems: 'center', gap: 6,
              paddingHorizontal: 12, paddingVertical: 7, borderRadius: 7,
              backgroundColor: pressed ? colors.primary + 'cc' : colors.primary,
              opacity: narrState === 'loading' ? 0.6 : 1,
            })}
            accessibilityRole="button"
            accessibilityLabel={narrState === 'playing' ? 'Stop narration' : 'Play narration'}
          >
            {narrState === 'loading'
              ? <ActivityIndicator size="small" color="#fff" />
              : <Feather name={narrState === 'playing' ? 'square' : 'play'} size={13} color="#fff" />
            }
            <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>
              {narrState === 'loading' ? 'Synthesising…' : narrState === 'playing' ? 'Stop' : 'Play Narration'}
            </Text>
          </Pressable>
          <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, flex: 1 }}>
            {narrState === 'playing' ? 'Playing narration script…' : 'Listen to the trailer narration'}
          </Text>
        </View>
      ) : null}

      {/* Production doc tabs */}
      {docKeys.length > 0 ? (
        <View style={{ gap: 6 }}>
          <Text style={{ fontSize: 9, fontFamily: 'Inter_700Bold', color: colors.mutedForeground, letterSpacing: 1, textTransform: 'uppercase' }}>
            Production Documents
          </Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, flexDirection: 'row' }}>
            {docKeys.map(key => (
              <Pressable
                key={key}
                onPress={() => setActiveDoc(activeDoc === key ? null : key)}
                style={({ pressed }) => ({
                  paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8, borderWidth: 1,
                  borderColor: activeDoc === key ? colors.primary : colors.border,
                  backgroundColor: activeDoc === key
                    ? colors.primary
                    : pressed ? colors.muted + '88' : colors.muted + '33',
                })}
              >
                <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: activeDoc === key ? '#fff' : colors.mutedForeground }}>
                  {key.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
          {activeDoc && activeDocText ? (
            <View style={{
              borderRadius: 8, borderWidth: 1, borderColor: colors.border,
              backgroundColor: colors.muted + '22', padding: 10, maxHeight: 260,
            }}>
              <ScrollView nestedScrollEnabled>
                <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 17 }}>
                  {activeDocText}
                </Text>
              </ScrollView>
            </View>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

function TrailerItemMobile({ trailer, workId, colors }: { trailer: TrailerListItemMobile; workId: string; colors: any }) {
  const T = useVellumTokens();
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const [expanded, setExpanded]           = useState(false);
  const [fullTrailer, setFullTrailer]     = useState<TrailerPackageMobile | null>(null);
  const [loadingFull, setLoadingFull]     = useState(false);
  const liveStatus = fullTrailer?.status ?? trailer.status;
  const livePhase  = fullTrailer?.phase  ?? trailer.phase;

  const fetchFull = useCallback(async () => {
    if (loadingFull) return;
    setLoadingFull(true);
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/trailers/${trailer.id}`);
      if (r.ok) setFullTrailer(await r.json());
    } catch { /* non-fatal */ }
    finally { setLoadingFull(false); }
  }, [domain, workId, trailer.id, loadingFull]);

  // Poll while running
  useEffect(() => {
    if (liveStatus !== 'running') return;
    const iv = setInterval(fetchFull, 3000);
    return () => clearInterval(iv);
  }, [liveStatus, fetchFull]);

  // Load detail on expand
  useEffect(() => {
    if (expanded && !fullTrailer && !loadingFull) fetchFull();
  }, [expanded, fullTrailer, loadingFull, fetchFull]);

  return (
    <View style={{
      borderRadius: 10, borderWidth: 1, borderColor: colors.border,
      backgroundColor: colors.card, overflow: 'hidden', marginBottom: 8,
    }}>
      <Pressable
        onPress={() => setExpanded(e => !e)}
        style={({ pressed }) => ({
          flexDirection: 'row', alignItems: 'center', gap: 10,
          paddingHorizontal: 12, paddingVertical: 11,
          backgroundColor: pressed ? colors.muted + '55' : 'transparent',
        })}
      >
        <Feather name="film" size={15} color={colors.mutedForeground} />
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
            {new Date(trailer.created_at).toLocaleString()}
          </Text>
        </View>
        <TrailerStatusBadgeMobile status={liveStatus} phase={livePhase} colors={colors} />
        <Feather name={expanded ? 'chevron-down' : 'chevron-right'} size={14} color={colors.mutedForeground} />
      </Pressable>

      {expanded && (
        <View style={{ borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border, padding: 12 }}>
          {(liveStatus === 'running' || (loadingFull && !fullTrailer)) ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12 }}>
              <ActivityIndicator size="small" color={colors.primary} />
              <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                {PHASE_LABELS_MOBILE[livePhase] ?? livePhase}
              </Text>
            </View>
          ) : liveStatus === 'failed' ? (
            <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: T.rust, paddingVertical: 8 }}>
              {fullTrailer?.error ?? 'Pipeline failed — check server logs.'}
            </Text>
          ) : (liveStatus === 'ready' || liveStatus === 'blocked') && fullTrailer?.package ? (
            <TrailerPackageViewMobile pkg={fullTrailer.package} colors={colors} />
          ) : (
            <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, fontStyle: 'italic', paddingVertical: 8 }}>
              Package not yet available.
            </Text>
          )}
        </View>
      )}
    </View>
  );
}

function TrailerTab({ workId, colors }: { workId: string; colors: any }) {
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const insets = useSafeAreaInsets();
  const [trailers, setTrailers]     = useState<TrailerListItemMobile[]>([]);
  const [loading, setLoading]       = useState(true);
  const [generating, setGenerating] = useState(false);
  const [fetchError, setFetchError] = useState(false);

  const fetchTrailers = useCallback(async () => {
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/trailers`);
      if (r.ok) {
        const data = await r.json();
        setTrailers(data.trailers ?? []);
        setFetchError(false);
      } else {
        setFetchError(true);
      }
    } catch {
      setFetchError(true);
    } finally {
      setLoading(false);
    }
  }, [domain, workId]);

  useEffect(() => { fetchTrailers(); }, [fetchTrailers]);

  // Poll while any trailer is running
  useEffect(() => {
    const anyRunning = trailers.some(t => t.status === 'running');
    if (!anyRunning) return;
    const iv = setInterval(fetchTrailers, 5000);
    return () => clearInterval(iv);
  }, [trailers, fetchTrailers]);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/trailer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error((body as any).detail ?? `HTTP ${r.status}`);
      }
      await fetchTrailers();
    } catch (err: any) {
      Alert.alert('Could not start trailer', err?.message ?? 'Unknown error. Try again.');
    } finally {
      setGenerating(false);
    }
  };

  const hasRunning = trailers.some(t => t.status === 'running');

  return (
    <ScrollView
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 16, paddingBottom: insets.bottom + 24 }}
      refreshControl={
        <RefreshControl refreshing={loading} onRefresh={fetchTrailers} tintColor={colors.primary} />
      }
    >
      {/* Header row */}
      <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 16 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1 }}>
          <Feather name="film" size={15} color={colors.mutedForeground} />
          <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.8 }}>
            Trailer Architect
          </Text>
          {trailers.length > 0 && (
            <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 10, backgroundColor: colors.muted }}>
              <Text style={{ fontSize: 9, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground }}>{trailers.length}</Text>
            </View>
          )}
        </View>

        <Pressable
          onPress={handleGenerate}
          disabled={generating || hasRunning}
          style={({ pressed }) => ({
            flexDirection: 'row', alignItems: 'center', gap: 6,
            paddingHorizontal: 12, paddingVertical: 10, borderRadius: 8,
            borderWidth: 1, borderColor: colors.primary + '55',
            backgroundColor: pressed || generating || hasRunning
              ? colors.primary + '14' : colors.primary + '0a',
            opacity: generating || hasRunning ? 0.38 : 1,
            minHeight: 44,
          })}
        >
          {generating || hasRunning
            ? <ActivityIndicator size="small" color={colors.primary} style={{ transform: [{ scale: 0.7 }] }} />
            : <Feather name="zap" size={13} color={colors.primary} />}
          <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>
            {hasRunning ? 'Generating…' : 'Generate Trailer'}
          </Text>
        </Pressable>
      </View>

      {/* Content */}
      {fetchError ? (
        <EmptyState
          icon="wifi-off"
          title="Could not load trailers"
          body="Check your connection and try again."
          cta="Retry"
          onCta={fetchTrailers}
        />
      ) : trailers.length === 0 ? (
        <EmptyState
          icon="film"
          title="No trailers yet"
          body="Add at least one processed document, then tap Generate Trailer."
        />
      ) : (
        trailers.map(t => (
          <TrailerItemMobile key={t.id} trailer={t} workId={workId} colors={colors} />
        ))
      )}
    </ScrollView>
  );
}

// ─── Overview tab with "Start Discussion" CTA ────────────────────────────────

// ── Generate section ──────────────────────────────────────────────────────────

type GenerateFormat = 'excel' | 'pdf' | 'docx' | 'slides';

const GENERATE_FORMATS: { key: GenerateFormat; label: string; icon: string; endpoint: string; body: object; mime: string; uti: string }[] = [
  { key: 'excel',  label: 'Excel',       icon: 'grid',        endpoint: '/generate/excel',  body: {},           mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',          uti: 'com.microsoft.excel.xlsx' },
  { key: 'pdf',    label: 'PDF Report',  icon: 'file-text',   endpoint: '/generate/report', body: { format: 'pdf' },  mime: 'application/pdf',           uti: 'com.adobe.pdf' },
  { key: 'docx',   label: 'DOCX Report', icon: 'align-left',  endpoint: '/generate/report', body: { format: 'docx' }, mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', uti: 'org.openxmlformats.wordprocessingml.document' },
  { key: 'slides', label: 'Slides',      icon: 'monitor',     endpoint: '/generate/slides', body: {},           mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', uti: 'org.openxmlformats.presentationml.presentation' },
];

function GenerateSection({ workId, colors }: { workId: string; colors: any }) {
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const [busy, setBusy] = useState<Record<GenerateFormat, boolean>>({ excel: false, pdf: false, docx: false, slides: false });

  const handleGenerate = async (fmt: typeof GENERATE_FORMATS[number]) => {
    setBusy(prev => ({ ...prev, [fmt.key]: true }));
    try {
      const res = await mobileFetch(`https://${domain}/api${fmt.endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_id: workId, ...fmt.body }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `Generation failed (HTTP ${res.status})`);
      }
      const data = await res.json() as { ok: boolean; filename: string; download_url: string };

      if (Platform.OS === 'web') {
        // Web: open the download URL in a new tab
        const { Linking } = await import('react-native');
        await Linking.openURL(`https://${domain}${data.download_url}`);
        return;
      }

      // Native: download with auth header, then share/open via system sheet
      const FileSystem = await import('expo-file-system/legacy');
      const Sharing    = await import('expo-sharing');
      const token = getApiToken();
      const dest  = `${FileSystem.cacheDirectory ?? ''}${data.filename}`;
      const dl = await FileSystem.downloadAsync(
        `https://${domain}${data.download_url}`,
        dest,
        { headers: token ? { authorization: `Bearer ${token}` } : undefined },
      );
      if (dl.status !== 200) throw new Error(`Download failed (HTTP ${dl.status})`);

      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(dl.uri, {
          mimeType: fmt.mime,
          dialogTitle: data.filename,
          UTI: fmt.uti,
        });
      } else {
        Alert.alert('Saved', `${data.filename} is saved in your Files app.`);
      }
      // best-effort cleanup after share sheet closes
      FileSystem.deleteAsync(dest, { idempotent: true }).catch(() => {});
    } catch (err: any) {
      Alert.alert('Generation failed', err?.message ?? 'Unknown error. Try again.');
    } finally {
      setBusy(prev => ({ ...prev, [fmt.key]: false }));
    }
  };

  return (
    <View style={{ marginTop: 16 }}>
      {/* Section header */}
      <View style={{
        flexDirection: 'row', alignItems: 'center', gap: 6,
        marginBottom: 8,
      }}>
        <Feather name="download" size={13} color={colors.mutedForeground} />
        <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Generate &amp; Export
        </Text>
      </View>

      {/* 2×2 button grid */}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {GENERATE_FORMATS.map(fmt => {
          const isBusy = busy[fmt.key];
          return (
            <Pressable
              key={fmt.key}
              onPress={() => handleGenerate(fmt)}
              disabled={isBusy}
              style={({ pressed }) => ({
                flexDirection: 'row', alignItems: 'center', gap: 6,
                flex: 1, minWidth: '44%',
                paddingHorizontal: 12, paddingVertical: 10, borderRadius: 8,
                borderWidth: 1,
                borderColor: colors.border,
                backgroundColor: pressed ? colors.muted : colors.card,
                opacity: isBusy ? 0.6 : 1,
              })}
            >
              {isBusy
                ? <ActivityIndicator size="small" color={colors.primary} />
                : <Feather name={fmt.icon as any} size={14} color={colors.primary} />
              }
              <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground, flexShrink: 1 }}>
                {isBusy ? 'Generating…' : fmt.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

function OverviewTab({ workId, onStartDiscussion, starting, onNavigateToTab, bookIntel, onOpenBook, onTargetsSaved, pipeline, pipelineLoading, onStartPipeline, onAdvancePipeline, advancingPipeline }: {
  workId: string;
  onStartDiscussion: () => void;
  starting: boolean;
  onNavigateToTab?: (tab: Tab) => void;
  bookIntel?: any;
  onOpenBook?: () => void;
  onTargetsSaved?: () => void;
  pipeline?: any;
  pipelineLoading?: boolean;
  onStartPipeline?: () => void;
  onAdvancePipeline?: () => void;
  advancingPipeline?: boolean;
}) {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const { data: workData, isLoading, isError, refetch } = useGetWork(workId);
  const work = workData?.work;
  const queryClient = useQueryClient();
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState('');
  const { mutate: updateWork } = useUpdateWork();

  // ── Targets editing ──────────────────────────────────────────────────────
  const [editingTargets, setEditingTargets] = useState(false);
  const [wordInput, setWordInput]           = useState('');
  const [chapterInput, setChapterInput]     = useState('');
  const [savingTargets, setSavingTargets]   = useState(false);

  const currentMeta = (work as any)?.meta ?? {};
  const savedTargets = (currentMeta?.completeness_targets ?? {}) as {
    word_target?: number;
    chapter_target?: number;
  };

  // ── Progress bar data — derived from bookIntel ────────────────────────────
  // Prefer the canonical manuscript word count (most accurate single source),
  // then fall back to summing chapter word counts from the outline.
  const wordCount: number = (() => {
    const cw = (bookIntel?.canonical as any)?.word_count;
    if (typeof cw === 'number' && cw > 0) return cw;
    if (Array.isArray(bookIntel?.outline)) {
      return (bookIntel.outline as any[]).reduce(
        (s: number, ch: any) => s + (ch.word_count ?? 0), 0,
      );
    }
    return 0;
  })();
  const chapterCount: number = Array.isArray(bookIntel?.outline)
    ? (bookIntel.outline as any[]).length : 0;
  const _wordTarget  = savedTargets.word_target    ?? 50_000;
  const _chapTarget  = savedTargets.chapter_target ?? 10;
  const wordPct      = wordCount    > 0 ? Math.min(Math.round(100 * wordCount    / _wordTarget), 100) : 0;
  const chapPct      = chapterCount > 0 ? Math.min(Math.round(100 * chapterCount / _chapTarget),  100) : 0;
  const _barColor    = (pct: number): string => pct >= 70 ? T.green : pct >= 30 ? T.gilt : T.rust;
  const wordBarColor = _barColor(wordPct);
  const chapBarColor = _barColor(chapPct);

  const openTargetEditor = () => {
    setWordInput(String(savedTargets.word_target ?? 50000));
    setChapterInput(String(savedTargets.chapter_target ?? 10));
    setEditingTargets(true);
  };

  const saveTargets = () => {
    const wt = parseInt(wordInput, 10);
    const ct = parseInt(chapterInput, 10);
    if (!wt || !ct || wt < 1 || ct < 1) {
      Alert.alert('Invalid targets', 'Word count and chapter count must be positive numbers.');
      return;
    }
    setSavingTargets(true);
    const mergedMeta = { ...currentMeta, completeness_targets: { word_target: wt, chapter_target: ct } };
    updateWork(
      { workId, data: { meta: mergedMeta } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId) });
          setEditingTargets(false);
          setSavingTargets(false);
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          onTargetsSaved?.();
        },
        onError: () => {
          setSavingTargets(false);
          Alert.alert('Save failed', 'Could not save targets — check your connection.');
        },
      },
    );
  };

  const cancelTargets = () => {
    setEditingTargets(false);
    setWordInput('');
    setChapterInput('');
  };

  // ── Work type picker ──────────────────────────────────────────────────────
  const WORK_TYPES_MOBILE = [
    { id: 'research',  label: 'Research' },
    { id: 'writing',   label: 'Writing' },
    { id: 'learning',  label: 'Learning' },
    { id: 'project',   label: 'Project' },
    { id: 'reference', label: 'Reference' },
  ] as const;

  const handleTypeChange = () => {
    const currentType = work?.work_type ?? 'research';
    Alert.alert(
      'Work Type',
      'Select a type for this Work',
      [
        ...WORK_TYPES_MOBILE.map(t => ({
          text: t.id === currentType ? `${t.label} ✓` : t.label,
          onPress: () => {
            if (t.id === currentType) return;
            updateWork(
              { workId, data: { work_type: t.id } },
              {
                onSuccess: () =>
                  queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId) }),
                onError: () =>
                  Alert.alert('Save failed', 'Could not update work type — check your connection.'),
              },
            );
          },
        })),
        { text: 'Cancel', style: 'cancel' as const },
      ],
    );
  };

  const startDescEdit = () => {
    setDescDraft(work?.description ?? '');
    setEditingDesc(true);
  };

  const saveDesc = () => {
    setEditingDesc(false);
    const trimmed = descDraft.trim();
    if (trimmed === (work?.description ?? '')) return;
    updateWork({ workId, data: { title: work?.title ?? '', description: trimmed || null } }, {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: [workId] }),
    });
  };

  if (isLoading && !work) {
    return (
      <View style={{ flex: 1, paddingTop: 8 }}>
        {[...Array(6)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
      </View>
    );
  }

  if (isError && !work) {
    return (
      <ErrorScreen
        message="Can't load work details"
        detail="Check your connection and try again."
        onRetry={refetch}
      />
    );
  }

  return (
    <ScrollView
      contentContainerStyle={[styles.overviewPad, { paddingBottom: insets.bottom + 24 }]}
      showsVerticalScrollIndicator={false}
      refreshControl={<RefreshControl refreshing={isLoading} onRefresh={refetch} tintColor={colors.primary} />}
    >
      {editingDesc ? (
        <View style={{ marginBottom: 16 }}>
          <TextInput
            style={[styles.description, { color: colors.foreground, borderWidth: 1, borderColor: colors.primary, borderRadius: 6, padding: 8 }]}
            value={descDraft}
            onChangeText={setDescDraft}
            multiline
            autoFocus
            onBlur={saveDesc}
            returnKeyType="done"
            placeholder="Work description…"
            placeholderTextColor={colors.mutedForeground}
          />
        </View>
      ) : (
        <Pressable onPress={startDescEdit} style={{ marginBottom: 0 }}>
          {work?.description ? (
            <Text style={[styles.description, { color: colors.foreground }]}>{work.description}</Text>
          ) : (
            <Text style={[styles.description, { color: colors.mutedForeground, fontStyle: 'italic' }]}>Tap to add a description…</Text>
          )}
        </Pressable>
      )}

      <View style={[styles.infoGrid, { borderColor: colors.border }]}>
        {((): Array<{ label: string; value: string; tab?: Tab; onPress?: () => void }> => {
          const rows: Array<{ label: string; value: string; tab?: Tab; onPress?: () => void }> = [
            { label: 'Type', value: work?.work_type ?? '—', onPress: handleTypeChange },
            { label: 'Status', value: work?.status ?? '—' },
            { label: 'Documents', value: String((work as any)?.doc_count ?? 0), tab: 'docs' as Tab },
          ];
          const ready = (work as any)?.ready_doc_count ?? 0;
          const errs  = (work as any)?.error_doc_count ?? 0;
          const proc  = (work as any)?.processing_doc_count ?? 0;
          const total = (work as any)?.doc_count ?? 0;
          if (total > 0) {
            const parts: string[] = [];
            if (ready > 0) parts.push(`${ready} ready`);
            if (proc > 0)  parts.push(`${proc} processing`);
            if (errs > 0)  parts.push(`${errs} error${errs !== 1 ? 's' : ''}`);
            if (parts.length) rows.push({ label: 'Readiness', value: parts.join(' · '), tab: 'docs' as Tab });
          }
          rows.push(
            { label: 'Knowledge',      value: String((work as any)?.knowledge_count ?? 0), tab: 'knowledge' as Tab },
            { label: 'Pending Tasks',  value: String((work as any)?.pending_tasks ?? 0),   tab: 'tasks' as Tab },
            { label: 'Conversations',  value: String((work as any)?.conv_count ?? 0),       tab: 'conversations' as Tab },
            { label: 'Updated',        value: work?.updated_at ? new Date(work.updated_at).toLocaleDateString() : '—' },
          );
          return rows;
        })().map((row) => (
          <Pressable
            key={row.label}
            onPress={row.onPress ?? (row.tab ? () => onNavigateToTab?.(row.tab!) : undefined)}
            style={({ pressed }) => [
              styles.infoRow,
              { borderBottomColor: colors.border, opacity: ((row.tab || row.onPress) && pressed) ? 0.7 : 1 },
            ]}
          >
            <Text style={[styles.infoLabel, { color: colors.mutedForeground }]}>{row.label}</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
              <Text style={[styles.infoValue, { color: (row.tab || row.onPress) ? colors.primary : colors.foreground }]}>
                {row.value}
              </Text>
              {(row.tab || row.onPress) && <Feather name="chevron-right" size={12} color={colors.primary} />}
            </View>
          </Pressable>
        ))}
      </View>

      {/* Book health card — compact overview; full view is in the Book tab */}
      {bookIntel && (() => {
        const c = bookIntel.completeness ?? {};
        const avgPct = Math.round(
          ((c.structural_pct ?? 0) + (c.content_pct ?? 0) +
           (c.research_pct ?? 0) + (c.editorial_pct ?? 0)) / 4,
        );
        const avgColor = avgPct >= 70 ? T.green : avgPct >= 40 ? T.gilt : T.rust;
        return (
          <View style={{
            marginTop: 16, borderWidth: 1, borderRadius: 10,
            borderColor: colors.border, overflow: 'hidden',
          }}>
            {/* Header */}
            <View style={{
              flexDirection: 'row', alignItems: 'center', gap: 8,
              paddingHorizontal: 14, paddingVertical: 10,
              borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
              backgroundColor: colors.muted + '44',
            }}>
              <Feather name="book" size={14} color={colors.primary} />
              <Text style={{ fontSize: 12, fontWeight: '600', color: colors.foreground, flex: 1 }}>
                Book Health
              </Text>
              <View style={{
                paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10,
                backgroundColor: avgColor + '18', borderWidth: 1, borderColor: avgColor + '44',
              }}>
                <Text style={{ fontSize: 11, fontWeight: '700', color: avgColor }}>{avgPct}%</Text>
              </View>
            </View>

            <View style={{ paddingHorizontal: 14, paddingVertical: 10, gap: 8 }}>
              {/* Knowledge reviewed bar */}
              {bookIntel.knowledge_total > 0 && (
                <View>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text style={{ fontSize: 12, color: colors.mutedForeground }}>Knowledge reviewed</Text>
                    <Text style={{ fontSize: 12, fontWeight: '600', color: colors.foreground }}>
                      {bookIntel.knowledge_reviewed}/{bookIntel.knowledge_total}
                    </Text>
                  </View>
                  <View style={{ height: 4, backgroundColor: colors.muted, borderRadius: 2, overflow: 'hidden' }}>
                    <View style={{
                      height: '100%', borderRadius: 2, backgroundColor: colors.primary,
                      width: `${Math.round(100 * bookIntel.knowledge_reviewed / bookIntel.knowledge_total)}%` as any,
                    }} />
                  </View>
                </View>
              )}

              {/* Gaps */}
              {Array.isArray(bookIntel.gaps) && bookIntel.gaps.length > 0 && (
                <Text style={{ fontSize: 12, color: T.gilt }}>
                  ⚠ {bookIntel.gaps.length} gap{bookIntel.gaps.length !== 1 ? 's' : ''} detected
                </Text>
              )}

              {/* Next action */}
              {bookIntel.next_action && (
                <View style={{
                  padding: 10, borderRadius: 8,
                  backgroundColor: colors.primary + '0e',
                  borderWidth: 1, borderColor: colors.primary + '30',
                }}>
                  <Text style={{ fontSize: 11, fontWeight: '600', color: colors.primary, marginBottom: 2 }}>
                    Next step
                  </Text>
                  <Text style={{ fontSize: 12, color: colors.foreground, lineHeight: 17 }}>
                    {bookIntel.next_action}
                  </Text>
                </View>
              )}

              {/* Tap-through to Book tab */}
              {onOpenBook && (
                <Pressable
                  onPress={onOpenBook}
                  style={({ pressed }) => ({
                    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                    gap: 6, paddingVertical: 8, borderRadius: 8,
                    backgroundColor: pressed ? colors.muted : 'transparent',
                  })}
                >
                  <Text style={{ fontSize: 12, color: colors.primary, fontFamily: 'Inter_500Medium' }}>
                    View full book analysis
                  </Text>
                  <Feather name="chevron-right" size={13} color={colors.primary} />
                </Pressable>
              )}
            </View>
          </View>
        );
      })()}

      {/* ── Book Pipeline card — two states: active stage or "start" CTA ── */}
      {pipelineLoading ? (
        <View style={{
          marginTop: 16, borderWidth: 1, borderRadius: 10,
          borderColor: T.giltLine, backgroundColor: T.giltSoft,
          padding: 14, alignItems: 'center',
        }}>
          <ActivityIndicator size="small" color={T.gilt} />
        </View>
      ) : pipeline ? (
        /* ── Active pipeline: compact stage + Advance button ── */
        <View style={{
          marginTop: 16, borderWidth: 1, borderRadius: 10,
          borderColor: T.giltLine, overflow: 'hidden',
          backgroundColor: T.giltSoft,
        }}>
          {/* Header row */}
          <View style={{
            flexDirection: 'row', alignItems: 'center', gap: 8,
            paddingHorizontal: 14, paddingVertical: 10,
            backgroundColor: T.gilt + '18',
            borderBottomWidth: StyleSheet.hairlineWidth,
            borderBottomColor: T.gilt + '44',
          }}>
            <Feather name="book-open" size={14} color={T.gilt} />
            <Text style={{ fontSize: 12, color: T.gilt, flex: 1, fontFamily: 'Inter_600SemiBold' }}>
              Book Pipeline
            </Text>
            {/* Tapping the header navigates to the full Book tab */}
            <Pressable
              onPress={() => onNavigateToTab?.('book')}
              hitSlop={12}
              accessibilityLabel="Open Book tab"
            >
              <Feather name="external-link" size={13} color={T.gilt} />
            </Pressable>
          </View>

          {/* Stage row + Advance button */}
          <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, paddingVertical: 12, gap: 10 }}>
            {/* Badge: current stage code */}
            <View style={{ backgroundColor: T.gilt + '30', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4 }}>
              <Text style={{ fontSize: 13, fontFamily: 'Inter_700Bold', color: T.gilt }}>
                {pipeline.status ?? 'B0'}
              </Text>
            </View>
            {/* Stage label */}
            <Text style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', color: T.gilt }} numberOfLines={2}>
              {pipeline.stage_label ?? pipeline.status ?? 'In progress'}
            </Text>
            {/* Advance button — only shown when there is a next stage */}
            {pipeline.next_status && onAdvancePipeline && (
              <Pressable
                onPress={onAdvancePipeline}
                disabled={advancingPipeline}
                style={({ pressed }) => ({
                  flexDirection: 'row', alignItems: 'center', gap: 5,
                  paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8,
                  backgroundColor: pressed || advancingPipeline ? T.gilt + 'cc' : T.gilt,
                  opacity: advancingPipeline ? 0.7 : 1,
                })}
                accessibilityRole="button"
                accessibilityLabel={`Advance to ${pipeline.next_status}`}
              >
                {advancingPipeline
                  ? <ActivityIndicator size="small" color="#fff" />
                  : <>
                      <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>
                        {`→ ${pipeline.next_status}`}
                      </Text>
                    </>
                }
              </Pressable>
            )}
          </View>
        </View>
      ) : onStartPipeline ? (
        /* ── No pipeline yet: "Start" CTA ── */
        <View style={{
          marginTop: 16, borderWidth: 1, borderRadius: 10,
          borderColor: T.giltLine, overflow: 'hidden',
          backgroundColor: T.giltSoft,
        }}>
          <View style={{
            flexDirection: 'row', alignItems: 'center', gap: 8,
            paddingHorizontal: 14, paddingVertical: 10,
            backgroundColor: T.gilt + '18',
            borderBottomWidth: StyleSheet.hairlineWidth,
            borderBottomColor: T.gilt + '44',
          }}>
            <Feather name="book-open" size={14} color={T.gilt} />
            <Text style={{ fontSize: 12, fontWeight: '600', color: T.gilt, flex: 1, fontFamily: 'Inter_600SemiBold' }}>
              Book Pipeline
            </Text>
          </View>
          <View style={{ paddingHorizontal: 14, paddingVertical: 12, gap: 10 }}>
            <Text style={{ fontSize: 13, color: T.gilt, lineHeight: 18, fontFamily: 'Inter_400Regular' }}>
              Promote this Work to a book and track it through the full B0–B17 publication pipeline.
            </Text>
            <Pressable
              onPress={onStartPipeline}
              style={({ pressed }) => ({
                flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                gap: 6, paddingVertical: 10, borderRadius: 8,
                backgroundColor: pressed ? T.gilt + 'cc' : T.gilt,
              })}
              accessibilityRole="button"
              accessibilityLabel="Start Book Pipeline"
            >
              <Feather name="book" size={14} color="#fff" />
              <Text style={{ fontSize: 13, fontWeight: '600', color: '#fff', fontFamily: 'Inter_600SemiBold' }}>
                Start Book Pipeline
              </Text>
            </Pressable>
          </View>
        </View>
      ) : null}

      {/* ── Completeness targets card ─────────────────────────────────── */}
      <View style={{
        marginTop: 16, borderWidth: 1, borderRadius: 10,
        borderColor: colors.border, overflow: 'hidden',
      }}>
        {/* Header */}
        <View style={{
          flexDirection: 'row', alignItems: 'center', gap: 8,
          paddingHorizontal: 14, paddingVertical: 10,
          borderBottomWidth: editingTargets ? StyleSheet.hairlineWidth : 0,
          borderBottomColor: colors.border,
          backgroundColor: colors.muted + '44',
        }}>
          <Feather name="target" size={14} color={colors.primary} />
          <Text style={{ fontSize: 12, fontWeight: '600', color: colors.foreground, flex: 1 }}>
            Completeness Targets
          </Text>
          {!editingTargets && (
            <Pressable
              onPress={openTargetEditor}
              hitSlop={8}
              style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1, padding: 4 })}
            >
              <Feather name="edit-2" size={13} color={colors.primary} />
            </Pressable>
          )}
        </View>

        {/* Summary row (view mode) */}
        {!editingTargets && (
          <Pressable
            onPress={openTargetEditor}
            style={({ pressed }) => ({
              paddingHorizontal: 14, paddingVertical: 12,
              flexDirection: 'row', alignItems: 'center', gap: 8,
              opacity: pressed ? 0.7 : 1,
            })}
          >
            <View style={{ flex: 1, gap: 8 }}>

              {/* ── Word count row + progress bar ── */}
              <View style={{ gap: 4 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Feather name="file-text" size={12} color={colors.mutedForeground} />
                  <Text style={{ fontSize: 13, color: colors.foreground, flex: 1 }}>
                    {savedTargets.word_target
                      ? `${Number(savedTargets.word_target).toLocaleString()} words`
                      : '50,000 words'}
                    {!savedTargets.word_target && (
                      <Text style={{ color: colors.mutedForeground, fontSize: 11 }}> (default)</Text>
                    )}
                  </Text>
                  {wordCount > 0 && (
                    <Text style={{ fontSize: 11, fontWeight: '600', color: wordBarColor }}>
                      {wordCount >= 1000
                        ? `${(wordCount / 1000).toFixed(1)}k`
                        : wordCount.toLocaleString()}
                      {' '}/ {_wordTarget >= 1000
                        ? `${(_wordTarget / 1000).toFixed(0)}k`
                        : _wordTarget.toLocaleString()}
                    </Text>
                  )}
                </View>
                {wordCount > 0 && (
                  <View style={{ height: 3, backgroundColor: colors.muted, borderRadius: 2, overflow: 'hidden' }}>
                    <View style={{
                      height: '100%', borderRadius: 2, backgroundColor: wordBarColor,
                      width: `${wordPct}%` as any,
                    }} />
                  </View>
                )}
              </View>

              {/* ── Chapter count row + progress bar ── */}
              <View style={{ gap: 4 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Feather name="layers" size={12} color={colors.mutedForeground} />
                  <Text style={{ fontSize: 13, color: colors.foreground, flex: 1 }}>
                    {savedTargets.chapter_target
                      ? `${savedTargets.chapter_target} chapters`
                      : '10 chapters'}
                    {!savedTargets.chapter_target && (
                      <Text style={{ color: colors.mutedForeground, fontSize: 11 }}> (default)</Text>
                    )}
                  </Text>
                  {chapterCount > 0 && (
                    <Text style={{ fontSize: 11, fontWeight: '600', color: chapBarColor }}>
                      {chapterCount} / {_chapTarget}
                    </Text>
                  )}
                </View>
                {chapterCount > 0 && (
                  <View style={{ height: 3, backgroundColor: colors.muted, borderRadius: 2, overflow: 'hidden' }}>
                    <View style={{
                      height: '100%', borderRadius: 2, backgroundColor: chapBarColor,
                      width: `${chapPct}%` as any,
                    }} />
                  </View>
                )}
              </View>

            </View>
            <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
          </Pressable>
        )}

        {/* Inline edit form */}
        {editingTargets && (
          <View style={{ paddingHorizontal: 14, paddingVertical: 12, gap: 12 }}>
            {/* Word target row */}
            <View style={{ gap: 4 }}>
              <Text style={{ fontSize: 11, fontWeight: '600', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.6 }}>
                Word target
              </Text>
              <View style={{
                flexDirection: 'row', alignItems: 'center', gap: 8,
                borderWidth: 1, borderColor: colors.primary + '88', borderRadius: 8,
                paddingHorizontal: 10, paddingVertical: 8,
                backgroundColor: colors.card,
              }}>
                <Feather name="file-text" size={14} color={colors.primary} />
                <TextInput
                  style={{ flex: 1, fontSize: 15, color: colors.foreground, fontFamily: 'Inter_400Regular' }}
                  value={wordInput}
                  onChangeText={setWordInput}
                  keyboardType="number-pad"
                  placeholder="e.g. 80000"
                  placeholderTextColor={colors.mutedForeground}
                  editable={!savingTargets}
                  returnKeyType="next"
                  selectTextOnFocus
                />
                <Text style={{ fontSize: 11, color: colors.mutedForeground }}>words</Text>
              </View>
            </View>

            {/* Chapter target row */}
            <View style={{ gap: 4 }}>
              <Text style={{ fontSize: 11, fontWeight: '600', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.6 }}>
                Chapter target
              </Text>
              <View style={{
                flexDirection: 'row', alignItems: 'center', gap: 8,
                borderWidth: 1, borderColor: colors.primary + '88', borderRadius: 8,
                paddingHorizontal: 10, paddingVertical: 8,
                backgroundColor: colors.card,
              }}>
                <Feather name="layers" size={14} color={colors.primary} />
                <TextInput
                  style={{ flex: 1, fontSize: 15, color: colors.foreground, fontFamily: 'Inter_400Regular' }}
                  value={chapterInput}
                  onChangeText={setChapterInput}
                  keyboardType="number-pad"
                  placeholder="e.g. 20"
                  placeholderTextColor={colors.mutedForeground}
                  editable={!savingTargets}
                  returnKeyType="done"
                  onSubmitEditing={saveTargets}
                  selectTextOnFocus
                />
                <Text style={{ fontSize: 11, color: colors.mutedForeground }}>chapters</Text>
              </View>
            </View>

            {/* Action buttons */}
            <View style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
              <Pressable
                onPress={cancelTargets}
                disabled={savingTargets}
                style={({ pressed }) => ({
                  flex: 1, paddingVertical: 10, borderRadius: 8, alignItems: 'center',
                  borderWidth: 1, borderColor: colors.border,
                  backgroundColor: pressed ? colors.muted : 'transparent',
                  opacity: savingTargets ? 0.5 : 1,
                })}
              >
                <Text style={{ fontSize: 13, fontWeight: '600', color: colors.mutedForeground }}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={saveTargets}
                disabled={savingTargets}
                style={({ pressed }) => ({
                  flex: 2, paddingVertical: 10, borderRadius: 8,
                  alignItems: 'center', justifyContent: 'center',
                  flexDirection: 'row', gap: 6,
                  backgroundColor: savingTargets
                    ? colors.muted
                    : pressed ? colors.primary + 'cc' : colors.primary,
                })}
              >
                {savingTargets
                  ? <ActivityIndicator size="small" color="#fff" />
                  : <Feather name="check" size={14} color="#fff" />}
                <Text style={{ fontSize: 13, fontWeight: '600', color: savingTargets ? colors.mutedForeground : '#fff' }}>
                  {savingTargets ? 'Saving…' : 'Save targets'}
                </Text>
              </Pressable>
            </View>
          </View>
        )}
      </View>

      {/* Generate & Export */}
      <GenerateSection workId={workId} colors={colors} />

      {/* Start Discussion CTA */}
      <Pressable
        onPress={onStartDiscussion}
        disabled={starting}
        style={({ pressed }) => [
          styles.discussBtn,
          { backgroundColor: colors.primary, opacity: pressed || starting ? 0.7 : 1 },
        ]}
      >
        {starting ? (
          <ActivityIndicator size="small" color={colors.primaryForeground} />
        ) : (
          <Feather name="message-circle" size={16} color={colors.primaryForeground} />
        )}
        <Text style={[styles.discussBtnText, { color: colors.primaryForeground }]}>
          {starting ? 'Starting…' : 'Start a Discussion'}
        </Text>
      </Pressable>
    </ScrollView>
  );
}

// ─── Mobile Learn tab helper sub-components ───────────────────────────────────

/** Expandable worked-example card for procedural_gap errors.
 *  Must be a standalone component so React hooks rules are satisfied. */
function MobileProceduralGapCard({
  feedback,
  remediationHint,
  colors,
}: {
  feedback: string;
  remediationHint: string | null;
  colors: any;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <View style={{ gap: 8 }}>
      <View style={{
        borderWidth: 1, borderColor: '#93c5fd', borderRadius: 10,
        backgroundColor: '#eff6ff', padding: 12,
        flexDirection: 'row', alignItems: 'flex-start', gap: 10,
      }}>
        <View style={{
          width: 28, height: 28, borderRadius: 14,
          backgroundColor: '#dbeafe', alignItems: 'center', justifyContent: 'center',
        }}>
          <Feather name="tool" size={13} color="#2563eb" />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#1e40af', marginBottom: 2 }}>
            Procedural gap
          </Text>
          <Text style={{ fontSize: 13, color: '#1d4ed8', lineHeight: 19 }}>{feedback}</Text>
        </View>
      </View>
      {remediationHint && (
        <Pressable
          onPress={() => setExpanded(e => !e)}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}
        >
          <Feather name={expanded ? 'chevron-down' : 'chevron-right'} size={13} color="#2563eb" />
          <Text style={{ fontSize: 12, color: '#2563eb', fontFamily: 'Inter_500Medium' }}>
            {expanded ? 'Hide' : 'Show'} worked example
          </Text>
        </Pressable>
      )}
      {expanded && remediationHint && (
        <View style={{
          borderWidth: 1, borderColor: colors.border, borderRadius: 8,
          backgroundColor: colors.muted, padding: 12,
        }}>
          <Text style={{ fontSize: 11, color: colors.mutedForeground, fontFamily: 'Inter_600SemiBold', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Step-by-step hint
          </Text>
          <Text style={{ fontSize: 13, color: colors.foreground, lineHeight: 20 }}>{remediationHint}</Text>
        </View>
      )}
    </View>
  );
}

// ─── Mobile Learn tab ─────────────────────────────────────────────────────────

type MobileLearnPhase = 'loading' | 'seeding' | 'question' | 'assessing' | 'feedback' | 'all_done' | 'error' | 'session_done' | 'interleaved_summary';

interface MobileSession {
  concept_id: string;
  subject: string;
  description: string;
  question: string;
  context_snippet: string;
  question_type: "recall" | "transfer";
  session_mode: "blocked" | "interleaved";  // mode that produced this question
}

type MobileErrorType = "careless_slip" | "procedural_gap" | "conceptual_misconception" | "knowledge_gap" | null;

interface MobileAssessResult {
  score: number;
  feedback: string;
  route: 'STEP_FORWARD' | 'STEP_BACKWARD' | 'STAY_HERE';
  graduated: boolean;
  next_concept_id: string | null;
  summary: { total: number; graduated: number; mastery_pct: number };
  // Error classification (v95)
  error_type: MobileErrorType;
  remediation_hint: string | null;
  deep_review_needed: boolean;
  socratic_followup: string | null;
  suggested_prereq_id: string | null;
  suggested_prereq_subject: string | null;
  question_type: "recall" | "transfer";
}

function MobileLearnTab({ workId, colors }: { workId: string; colors: any }) {
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [phase, setPhase]       = useState<MobileLearnPhase>('loading');
  const [session, setSession]   = useState<MobileSession | null>(null);
  const [answer, setAnswer]     = useState('');
  const [result, setResult]     = useState<MobileAssessResult | null>(null);
  const [summary, setSummary]   = useState<{ total: number; graduated: number; mastery_pct: number; due_count?: number } | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [sessionCorrect, setSessionCorrect] = useState(0);
  const [learnView, setLearnView] = useState<'study' | 'concepts' | 'graph'>('study');
  const [concepts, setConcepts] = useState<any[]>([]);
  const [conceptsLoading, setConceptsLoading] = useState(false);
  const [resettingConcept, setResettingConcept] = useState<string | null>(null);
  const [interleavedMode, setInterleavedMode] = useState(false);
  const [interleavedHistory, setInterleavedHistory] = useState<{concept_id: string; subject: string; score: number}[]>([]);
  const [graphNodes, setGraphNodes] = useState<any[]>([]);
  const [graphLoading, setGraphLoading] = useState(false);

  const SESSION_LIMIT = 5; // correct answers before "session complete" screen
  const INTERLEAVED_SESSION_LENGTH = 10; // questions per interleaved session

  const domain = process.env.EXPO_PUBLIC_DOMAIN;
  const apiBase = `https://${domain}/api`;

  const fetchSummary = async () => {
    const r = await mobileFetch(`${apiBase}/works/${workId}/learning/summary`);
    if (!r.ok) throw new Error('Could not load summary');
    return r.json();
  };

  const loadQuestion = async (conceptId?: string | null, forceInterleaved?: boolean) => {
    setAnswer('');
    setResult(null);
    setPhase('question');
    const useInterleaved = forceInterleaved ?? interleavedMode;
    const params = new URLSearchParams({ type: 'auto' });
    params.set('mode', useInterleaved ? 'interleaved' : 'blocked');
    if (!useInterleaved && conceptId) params.set('concept_id', conceptId);
    const url = `${apiBase}/works/${workId}/learning/question?${params}`;
    const r = await mobileFetch(url);
    if (r.status === 422) { setPhase('all_done'); return; }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    setSession({
      concept_id:      d.concept_id,
      subject:         d.subject ?? 'Concept',
      description:     d.description ?? '',
      question:        d.question,
      context_snippet: d.context_snippet ?? '',
      question_type:   d.question_type ?? 'recall',
      session_mode:    d.session_mode === 'interleaved' ? 'interleaved' : 'blocked',
    });
  };

  const init = async () => {
    setPhase('loading');
    setErrorMsg('');
    try {
      const data = await fetchSummary();
      setSummary(data);
      if (data.total === 0) {
        setPhase('seeding');
        const sr = await mobileFetch(`${apiBase}/works/${workId}/learning/seed`, { method: 'POST' });
        if (!sr.ok) throw new Error('Could not seed concepts');
        const sd = await sr.json();
        if (!(sd.concepts ?? []).length) throw new Error('No knowledge found — import documents first.');
        const s2 = await fetchSummary();
        setSummary(s2);
      }
      if (data.mastery_pct === 100 && data.total > 0) { setPhase('all_done'); return; }
      await loadQuestion(null);
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not start session');
      setPhase('error');
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { init(); }, [workId]);

  const loadConcepts = useCallback(async () => {
    setConceptsLoading(true);
    try {
      const r = await mobileFetch(`${apiBase}/works/${workId}/learning/concepts`);
      if (r.ok) { const d = await r.json(); setConcepts(d.concepts ?? []); }
    } catch { /* non-fatal */ } finally { setConceptsLoading(false); }
  }, [apiBase, workId]);

  const resetConcept = useCallback(async (conceptId: string, subject: string) => {
    Alert.alert(
      'Reset streak',
      `Reset the mastery streak for "${subject}"? The concept will re-enter the study queue.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: async () => {
            setResettingConcept(conceptId);
            try {
              await mobileFetch(`${apiBase}/works/${workId}/learning/concepts/${conceptId}/reset`, { method: 'POST' });
              // Refresh summary + list
              const [sr, cr] = await Promise.all([
                mobileFetch(`${apiBase}/works/${workId}/learning/summary`),
                mobileFetch(`${apiBase}/works/${workId}/learning/concepts`),
              ]);
              if (sr.ok) { const d = await sr.json(); setSummary(d); }
              if (cr.ok) { const d = await cr.json(); setConcepts(d.concepts ?? []); }
            } catch { /* non-fatal */ } finally {
              setResettingConcept(null);
            }
          },
        },
      ]
    );
  }, [apiBase, workId]);

  /** Tap on a concept row → load a focused question for that concept and switch to study view. */
  const focusOnConcept = useCallback(async (conceptId: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setLearnView('study');
    try {
      await loadQuestion(conceptId);
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Error loading question');
      setPhase('error');
    }
  }, [loadQuestion]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (learnView === 'concepts') loadConcepts();
  }, [learnView, loadConcepts]);

  // Clear graph cache whenever the Work changes so we never show a previous Work's dep map
  useEffect(() => {
    setGraphNodes([]);
    setGraphLoading(false);
  }, [workId]);

  // Load prerequisite graph when the Dep Map view is selected;
  // a cancelled flag discards any in-flight response from a previous Work.
  useEffect(() => {
    if (learnView !== 'graph') return;
    let cancelled = false;
    setGraphNodes([]);   // clear stale data immediately on every re-trigger
    setGraphLoading(true);
    mobileFetch(`${apiBase}/works/${workId}/learning/graph`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!cancelled) setGraphNodes(d.nodes ?? []); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setGraphLoading(false); });
    return () => { cancelled = true; };
  }, [learnView, workId]); // eslint-disable-line react-hooks/exhaustive-deps

  const submitAnswer = async () => {
    if (!session || !answer.trim()) return;
    // Heavy impact on quiz submit — the most committed action in the study session
    if (Platform.OS !== 'web') {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    }
    setPhase('assessing');
    try {
      const r = await mobileFetch(`${apiBase}/works/${workId}/learning/assess`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          concept_id:    session.concept_id,
          question:      session.question,
          answer:        answer.trim(),
          question_type: session.question_type ?? 'recall',
          session_mode:  session.session_mode ?? 'blocked',  // use the mode that produced this question
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: MobileAssessResult = await r.json();
      setResult(d);
      setSummary(d.summary);
      // Interleaved-specific tracking: use session's recorded mode, not the toggle state.
      // Prevents a blocked question (loaded before Mix was toggled) being treated as interleaved.
      if (session.session_mode === 'interleaved') {
        const newHistory = [...interleavedHistory, { concept_id: session.concept_id, subject: session.subject, score: d.score }];
        setInterleavedHistory(newHistory);
        if (newHistory.length >= INTERLEAVED_SESSION_LENGTH) {
          setPhase('interleaved_summary');
          return;
        }
        setPhase('feedback');
        return;
      }
      // Blocked mode: track correct answers and enforce session limit
      if (d.score >= 0.75) {
        const newCorrect = sessionCorrect + 1;
        setSessionCorrect(newCorrect);
        if (newCorrect >= SESSION_LIMIT) { setPhase('session_done'); return; }
      }
      setPhase('feedback');
    } catch (e: any) {
      setErrorMsg(e.message ?? 'Could not assess answer');
      setPhase('error');
    }
  };

  const next = async () => {
    if (!result) { await loadQuestion(null); return; }
    if (result.summary.mastery_pct === 100) { setPhase('all_done'); return; }
    try {
      if (session?.session_mode === 'interleaved') {
        // next_concept_id is from blocked routing — ignore it; let weighted selection pick next
        await loadQuestion(null, true);
      } else {
        await loadQuestion(result.next_concept_id);
      }
    }
    catch (e: any) { setErrorMsg(e.message ?? 'Error loading next question'); setPhase('error'); }
  };

  const scoreBg    = (s: number) => s >= 0.75 ? T.greenSoft : s >= 0.5 ? T.giltSoft : T.rustSoft;
  const scoreColor = (s: number) => s >= 0.75 ? T.green : s >= 0.5 ? T.gilt : T.rust;

  // ── Loading / seeding ────────────────────────────────────────────────────
  if (phase === 'loading' || phase === 'seeding') {
    return (
      <View style={{ flex: 1, paddingTop: 8 }}>
        {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
      </View>
    );
  }

  // ── All done ────────────────────────────────────────────────────────────
  if (phase === 'all_done') {
    const handleReset = async () => {
      try {
        await mobileFetch(`${apiBase}/works/${workId}/learning/reset`, { method: 'POST' });
        init();
      } catch { init(); }
    };
    return (
      <View style={[styles.centered, { flex: 1, padding: 32 }]}>
        <Feather name="award" size={48} color={colors.primary} />
        <Text style={[styles.workTitle, { color: colors.foreground, textAlign: 'center', marginTop: 16, fontSize: 20 }]}>
          All concepts mastered!
        </Text>
        <Text style={[styles.description, { color: colors.mutedForeground, textAlign: 'center', marginTop: 8 }]}>
          Add more documents to unlock new material, or reset your streaks to study again.
        </Text>
        {summary && (
          <Text style={[styles.itemMeta, { color: colors.mutedForeground, marginTop: 12 }]}>
            {summary.graduated}/{summary.total} concepts · {summary.mastery_pct}%
          </Text>
        )}
        <Pressable
          onPress={handleReset}
          style={({ pressed }) => [
            styles.discussBtn,
            { backgroundColor: colors.muted, marginTop: 24, paddingHorizontal: 24, opacity: pressed ? 0.7 : 1 },
          ]}
        >
          <Feather name="refresh-cw" size={14} color={colors.foreground} />
          <Text style={[styles.discussBtnText, { color: colors.foreground }]}>Reset &amp; study again</Text>
        </Pressable>
      </View>
    );
  }

  // ── Interleaved session summary (after 10 questions) ────────────────────
  if (phase === 'interleaved_summary') {
    const byConceptId: Record<string, { subject: string; scores: number[] }> = {};
    for (const h of interleavedHistory) {
      if (!byConceptId[h.concept_id]) byConceptId[h.concept_id] = { subject: h.subject, scores: [] };
      byConceptId[h.concept_id].scores.push(h.score);
    }
    const conceptStats = Object.values(byConceptId)
      .map(c => ({ subject: c.subject, questions: c.scores.length, avg: c.scores.reduce((a, b) => a + b, 0) / c.scores.length }))
      .sort((a, b) => b.avg - a.avg);
    const overallAvg = interleavedHistory.reduce((a, h) => a + h.score, 0) / Math.max(interleavedHistory.length, 1);

    const exitInterleaved = () => {
      setInterleavedMode(false);
      setInterleavedHistory([]);
      // Use forceInterleaved=false explicitly — setInterleavedMode hasn't committed yet
      loadQuestion(null, false);
    };
    const anotherSession = () => {
      setInterleavedHistory([]);
      loadQuestion(null, true); // explicit interleaved — interleavedMode is still true here
    };

    return (
      <View style={[styles.listPad, { paddingTop: 24, paddingBottom: 80 }]}>
        {/* Header */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 20 }}>
          <View style={{ width: 44, height: 44, borderRadius: 12, backgroundColor: '#8b5cf620', alignItems: 'center', justifyContent: 'center' }}>
            <Feather name="shuffle" size={20} color="#7c3aed" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 17, fontFamily: 'Merriweather_700Bold', color: colors.foreground }}>
              Session complete
            </Text>
            <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
              {interleavedHistory.length} questions · {conceptStats.length} concepts · avg {Math.round(overallAvg * 100)}%
            </Text>
          </View>
        </View>

        {/* Per-concept breakdown */}
        <View style={{ borderRadius: 12, borderWidth: 1, borderColor: colors.border, overflow: 'hidden', marginBottom: 20 }}>
          {conceptStats.map((c, i) => (
            <View key={i} style={{
              flexDirection: 'row', alignItems: 'center', gap: 10,
              padding: 12, borderBottomWidth: i < conceptStats.length - 1 ? 1 : 0,
              borderBottomColor: colors.border, backgroundColor: colors.background,
            }}>
              <Text style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }} numberOfLines={1}>
                {c.subject}
              </Text>
              <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                {c.questions}q
              </Text>
              <Text style={{
                fontSize: 13, fontFamily: 'Inter_700Bold', width: 44, textAlign: 'right',
                color: c.avg >= 0.75 ? T.green : c.avg >= 0.5 ? T.gilt : T.rust,
              }}>
                {Math.round(c.avg * 100)}%
              </Text>
            </View>
          ))}
        </View>

        {/* Actions */}
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <Pressable
            onPress={exitInterleaved}
            style={({ pressed }) => ({
              flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: 13,
              borderRadius: 10, borderWidth: 1, borderColor: colors.border,
              opacity: pressed ? 0.7 : 1, minHeight: 44,
            })}
          >
            <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>Exit</Text>
          </Pressable>
          <Pressable
            onPress={anotherSession}
            style={({ pressed }) => ({
              flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
              gap: 6, paddingVertical: 13, borderRadius: 10,
              backgroundColor: '#7c3aed', opacity: pressed ? 0.7 : 1, minHeight: 44,
            })}
          >
            <Feather name="shuffle" size={14} color="#fff" />
            <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>New session</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // ── Session done (5 correct in one sitting) ──────────────────────────────
  if (phase === 'session_done') {
    const masteryPct = summary?.mastery_pct ?? 0;
    return (
      <View style={[styles.centered, { flex: 1, padding: 32 }]}>
        <Feather name="check-circle" size={52} color={T.green} />
        <Text style={[styles.workTitle, { color: colors.foreground, textAlign: 'center', marginTop: 16, fontSize: 20 }]}>
          Great session!
        </Text>
        <Text style={[styles.description, { color: colors.mutedForeground, textAlign: 'center', marginTop: 6 }]}>
          {SESSION_LIMIT} correct answers · {masteryPct}% mastery
        </Text>

        {/* Mastery progress bar */}
        <View style={{ width: '100%', marginTop: 20, gap: 6 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <Text style={{ fontSize: 12, color: colors.mutedForeground, fontFamily: 'Inter_500Medium' }}>
              Overall mastery
            </Text>
            <Text style={{ fontSize: 12, color: colors.foreground, fontFamily: 'Inter_600SemiBold' }}>
              {masteryPct}%
            </Text>
          </View>
          <View style={{ height: 8, backgroundColor: colors.muted, borderRadius: 4, overflow: 'hidden' }}>
            <View
              style={{
                height: '100%',
                width: `${masteryPct}%` as any,
                backgroundColor: masteryPct === 100 ? T.green : colors.primary,
                borderRadius: 4,
              }}
            />
          </View>
          {summary && (
            <Text style={{ fontSize: 11, color: colors.mutedForeground, textAlign: 'center' }}>
              {summary.graduated}/{summary.total} concepts mastered
            </Text>
          )}
        </View>

        <View style={{ flexDirection: 'row', gap: 12, marginTop: 28, width: '100%' }}>
          <Pressable
            onPress={() => router.back()}
            style={({ pressed }: { pressed: boolean }) => ({
              flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
              gap: 6, paddingVertical: 13, borderRadius: 10, borderWidth: 1,
              borderColor: colors.border, opacity: pressed ? 0.7 : 1, minHeight: 44,
            })}
          >
            <Feather name="check" size={14} color={colors.foreground} />
            <Text style={{ fontWeight: '600', fontSize: 14, color: colors.foreground }}>Done for now</Text>
          </Pressable>
          <Pressable
            onPress={() => { setSessionCorrect(0); init(); }}
            style={({ pressed }: { pressed: boolean }) => ({
              flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
              gap: 6, paddingVertical: 13, borderRadius: 10,
              backgroundColor: colors.primary, opacity: pressed ? 0.7 : 1, minHeight: 44,
            })}
          >
            <Feather name="chevron-right" size={14} color="#fff" />
            <Text style={{ fontWeight: '600', fontSize: 14, color: '#fff' }}>Keep going</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // ── Error ───────────────────────────────────────────────────────────────
  if (phase === 'error') {
    return (
      <View style={[styles.centered, { flex: 1, padding: 32 }]}>
        <Feather name="alert-circle" size={40} color={T.rust} />
        <Text style={[styles.itemTitle, { color: T.rust, textAlign: 'center', marginTop: 12 }]}>{errorMsg}</Text>
        <Pressable
          onPress={init}
          style={({ pressed }) => [
            styles.discussBtn,
            { backgroundColor: colors.primary, marginTop: 20, paddingHorizontal: 28, opacity: pressed ? 0.7 : 1 },
          ]}
        >
          <Feather name="refresh-cw" size={14} color={colors.primaryForeground} />
          <Text style={[styles.discussBtnText, { color: colors.primaryForeground }]}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  // ── Active session ──────────────────────────────────────────────────────
  return (
    <ScrollView
      contentContainerStyle={[styles.listPad, { paddingTop: 16, paddingBottom: insets.bottom + 24 }]}
      keyboardShouldPersistTaps="handled"
      showsVerticalScrollIndicator={false}
    >
      {/* Study / Concepts view toggle + Interleaved toggle */}
      <View style={{ flexDirection: 'row', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        {(['study', 'concepts', 'graph'] as const).map((v) => (
          <Pressable
            key={v}
            onPress={() => setLearnView(v)}
            hitSlop={8}
            style={{
              paddingHorizontal: 14, paddingVertical: 6, borderRadius: 12, borderWidth: 1,
              borderColor: learnView === v ? colors.primary : colors.border,
              backgroundColor: learnView === v ? colors.primary + '18' : 'transparent',
            }}
          >
            <Text style={{ fontSize: 13, fontWeight: '600', color: learnView === v ? colors.primary : colors.mutedForeground }}>
              {v === 'study' ? 'Study' : v === 'concepts' ? 'Concepts' : 'Dep Map'}
            </Text>
          </Pressable>
        ))}
        {/* Interleaved toggle — shown when ≥3 in-progress concepts or already active */}
        {(() => {
          const inProgressCount = concepts.filter((c: any) => c.consecutive_passes > 0 && !c.graduated).length;
          if (inProgressCount < 3 && !interleavedMode) return null;
          return (
            <Pressable
              onPress={() => {
                const next = !interleavedMode;
                setInterleavedMode(next);
                setInterleavedHistory([]);
                setLearnView('study');
                loadQuestion(null, next);
              }}
              hitSlop={8}
              style={{
                flexDirection: 'row', alignItems: 'center', gap: 5,
                paddingHorizontal: 12, paddingVertical: 6, borderRadius: 12, borderWidth: 1,
                borderColor: interleavedMode ? '#8b5cf6' : colors.border,
                backgroundColor: interleavedMode ? '#8b5cf618' : 'transparent',
              }}
            >
              <Feather name="shuffle" size={12} color={interleavedMode ? '#7c3aed' : colors.mutedForeground} />
              <Text style={{ fontSize: 13, fontWeight: '600', color: interleavedMode ? '#7c3aed' : colors.mutedForeground }}>
                {interleavedMode ? 'Interleaved' : 'Mix'}
              </Text>
            </Pressable>
          );
        })()}
      </View>

      {/* Concepts list — shown when learnView === 'concepts' */}
      {learnView === 'concepts' && (
        conceptsLoading ? (
          <View>
            {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
          </View>
        ) : concepts.length === 0 ? (
          <EmptyState
            icon="book-open"
            title="No concepts yet"
            body="Start a study session to generate concepts."
          />
        ) : (
          <>
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.6, textTransform: 'uppercase', marginBottom: 10 }}>
              {concepts.length} concept{concepts.length !== 1 ? 's' : ''} · tap to study
            </Text>
            {concepts.map((c: any) => {
              // Mastery tier
              const passes   = c.consecutive_passes ?? 0;
              const isDue    = c.is_due && c.graduated; // overdue spaced-repetition review
              const isLocked = !c.prereqs_met && !c.graduated; // prerequisites not yet started
              const tier: 'graduated' | 'in_progress' | 'not_started' =
                c.graduated ? 'graduated' : passes > 0 ? 'in_progress' : 'not_started';
              const tierLabel = isDue ? 'Due' : isLocked ? 'Locked' : tier === 'graduated' ? 'Graduated' : tier === 'in_progress' ? 'In progress' : 'Not started';
              const tierCol   = isDue ? T.gilt : isLocked ? colors.mutedForeground : tier === 'graduated' ? T.green : tier === 'in_progress' ? colors.primary : colors.mutedForeground;
              const tierBg    = isDue ? T.giltSoft : isLocked ? colors.muted : tier === 'graduated' ? T.greenSoft : tier === 'in_progress' ? colors.primary + '18' : colors.muted;
              const borderCol = isDue ? T.giltLine : isLocked ? colors.border : tier === 'graduated' ? T.green + '44' : tier === 'in_progress' ? colors.primary + '33' : colors.border;
              const barPct    = tier === 'graduated' ? 100 : Math.min(99, Math.round((passes / 3) * 100));

              // Last-practised label
              let lastPractisedLabel = 'Never practised';
              if (c.last_practised) {
                const ms = Date.now() - new Date(c.last_practised).getTime();
                const days = Math.floor(ms / 86_400_000);
                if (days === 0)       lastPractisedLabel = 'Practised today';
                else if (days === 1)  lastPractisedLabel = 'Practised yesterday';
                else if (days < 7)   lastPractisedLabel = `Practised ${days}d ago`;
                else if (days < 30)  lastPractisedLabel = `Practised ${Math.floor(days / 7)}w ago`;
                else                 lastPractisedLabel = `Practised ${Math.floor(days / 30)}mo ago`;
              }

              return (
                <Pressable
                  key={c.id}
                  onPress={() => {
                    if (isLocked) {
                      // Show which prerequisites need to be completed first
                      const labels: string[] = c.prereq_labels ?? [];
                      Alert.alert(
                        '🔒 Prerequisites needed',
                        labels.length > 0
                          ? `Complete these first to unlock "${c.subject}":\n\n${labels.map((l: string) => `• ${l}`).join('\n')}`
                          : `Complete your prerequisites before studying "${c.subject}".`,
                        [
                          { text: 'OK', style: 'default' },
                          {
                            text: 'Study anyway',
                            style: 'destructive',
                            onPress: () => focusOnConcept(c.id),
                          },
                        ],
                      );
                      return;
                    }
                    focusOnConcept(c.id);
                  }}
                  onLongPress={() => {
                    const hasProgress = (c.consecutive_passes ?? 0) > 0 || c.graduated;
                    if (!hasProgress) return;
                    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                    resetConcept(c.id, c.subject);
                  }}
                  delayLongPress={450}
                  accessibilityRole="button"
                  accessibilityLabel={`Study ${c.subject}`}
                  style={({ pressed }: { pressed: boolean }) => ({
                    borderWidth: 1, borderRadius: 10, padding: 12, marginBottom: 8,
                    borderColor: resettingConcept === c.id ? T.giltLine : borderCol,
                    backgroundColor: tier === 'graduated' ? T.greenSoft : 'transparent',
                    opacity: pressed ? 0.75 : 1,
                  })}
                >
                  {/* Header row: icon + subject + tier badge + chevron */}
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <Feather
                      name={isDue ? 'clock' : isLocked ? 'lock' : tier === 'graduated' ? 'award' : tier === 'in_progress' ? 'trending-up' : 'circle'}
                      size={14}
                      color={tierCol}
                    />
                    <Text style={{ flex: 1, fontSize: 14, fontFamily: 'Inter_600SemiBold', color: isLocked ? colors.mutedForeground : colors.foreground }} numberOfLines={1}>
                      {c.subject}
                    </Text>
                    <View style={{ paddingHorizontal: 7, paddingVertical: 2, borderRadius: 8, backgroundColor: tierBg }}>
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_700Bold', color: tierCol }}>
                        {tier === 'graduated' ? '✓ ' : ''}{tierLabel}
                      </Text>
                    </View>
                    <Feather name="chevron-right" size={13} color={colors.mutedForeground} />
                  </View>

                  {/* Description or locked-prereq notice */}
                  {isLocked && (c.prereq_labels ?? []).length > 0 ? (
                    <Text style={{ fontSize: 11, color: colors.mutedForeground, marginLeft: 22, marginTop: 4, fontStyle: 'italic' }} numberOfLines={2}>
                      Requires: {(c.prereq_labels as string[]).join(', ')}
                    </Text>
                  ) : c.description ? (
                    <Text style={{ fontSize: 12, color: colors.mutedForeground, marginLeft: 22, marginTop: 4 }} numberOfLines={2}>
                      {c.description}
                    </Text>
                  ) : null}

                  {/* Progress bar — full for graduated, proportional for in-progress */}
                  {tier !== 'not_started' && (
                    <View style={{ marginTop: 8, marginLeft: 22, height: 3, backgroundColor: colors.muted, borderRadius: 1.5, overflow: 'hidden' }}>
                      <View style={{ height: '100%', width: `${barPct}%` as any, backgroundColor: tierCol, borderRadius: 1.5 }} />
                    </View>
                  )}

                  {/* Footer: pass count + last-practised */}
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 5, marginLeft: 22 }}>
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                      {passes} pass{passes !== 1 ? 'es' : ''} in a row
                    </Text>
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                      {lastPractisedLabel}
                    </Text>
                  </View>
                </Pressable>
              );
            })}
          </>
        )
      )}

      {/* Dependency map — layered depth list grouped by prerequisite order */}
      {learnView === 'graph' && (
        graphLoading ? (
          <View>
            {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
          </View>
        ) : graphNodes.length === 0 ? (
          <EmptyState
            icon="git-branch"
            title="No dependency map yet"
            body="No prerequisite relationships defined yet."
          />
        ) : (() => {
          // Compute BFS depth for each node
          const depth: Record<string, number> = {};
          const visited = new Set<string>();
          const computeDepth = (id: string, visiting = new Set<string>()): number => {
            if (depth[id] !== undefined) return depth[id];
            if (visiting.has(id)) { depth[id] = 0; return 0; }
            visiting.add(id);
            const prereqs: string[] = graphNodes.find((n: any) => n.id === id)?.prereq_ids ?? [];
            depth[id] = prereqs.length === 0
              ? 0
              : Math.max(...prereqs.map(p => computeDepth(p, new Set(visiting)))) + 1;
            return depth[id];
          };
          graphNodes.forEach((n: any) => computeDepth(n.id));

          const maxDepth = Math.max(0, ...Object.values(depth));
          const layers: any[][] = Array.from({ length: maxDepth + 1 }, () => []);
          graphNodes.forEach((n: any) => layers[depth[n.id] ?? 0].push(n));
          void visited;

          const nodeColor = (n: any): string => {
            if (n.graduated)                      return T.green;
            if ((n.consecutive_passes ?? 0) > 0)  return T.gilt;
            if (n.prereqs_met !== false)           return '#3b82f6';
            return colors.mutedForeground;
          };
          const nodeLabel = (n: any): string => {
            if (n.graduated)                      return '✓ Graduated';
            if ((n.consecutive_passes ?? 0) > 0)  return 'In progress';
            if (n.prereqs_met !== false)           return 'Eligible';
            return '🔒 Locked';
          };

          return (
            <View>
              {/* Legend */}
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
                {[
                  { color: T.green, label: 'Graduated' },
                  { color: T.gilt, label: 'In progress' },
                  { color: '#3b82f6', label: 'Eligible' },
                  { color: colors.mutedForeground, label: 'Locked' },
                ].map(({ color, label }) => (
                  <View key={label} style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                    <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color }} />
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                      {label}
                    </Text>
                  </View>
                ))}
              </View>

              {layers.map((layer, li) => (
                <View key={li} style={{ marginBottom: 16 }}>
                  {/* Layer header */}
                  <Text style={{
                    fontSize: 10, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground,
                    letterSpacing: 0.7, textTransform: 'uppercase', marginBottom: 8,
                  }}>
                    {li === 0 ? 'Foundation' : `Builds on layer ${li}`} · {layer.length} concept{layer.length !== 1 ? 's' : ''}
                  </Text>

                  {/* Left border to show depth visually */}
                  <View style={{ marginLeft: li * 10, borderLeftWidth: li > 0 ? 2 : 0, borderLeftColor: colors.border, paddingLeft: li > 0 ? 10 : 0 }}>
                    {layer.map((n: any) => {
                      const col = nodeColor(n);
                      const lbl = nodeLabel(n);
                      const isLocked = n.prereqs_met === false && !n.graduated;
                      return (
                        <Pressable
                          key={n.id}
                          onPress={() => {
                            if (isLocked) {
                              const labels: string[] = n.prereq_labels ?? [];
                              Alert.alert(
                                '🔒 Prerequisites needed',
                                labels.length > 0
                                  ? `Complete these first:\n\n${labels.map((l: string) => `• ${l}`).join('\n')}`
                                  : 'Complete your prerequisites first.',
                                [
                                  { text: 'OK', style: 'cancel' },
                                  { text: 'Study anyway', onPress: () => focusOnConcept(n.id) },
                                ],
                              );
                            } else {
                              focusOnConcept(n.id);
                            }
                          }}
                          style={({ pressed }: { pressed: boolean }) => ({
                            flexDirection: 'row', alignItems: 'center', gap: 10,
                            padding: 10, marginBottom: 6, borderRadius: 8,
                            borderWidth: 1, borderColor: col + '44',
                            backgroundColor: col + '10',
                            opacity: pressed ? 0.7 : isLocked ? 0.55 : 1,
                          })}
                        >
                          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: col, flexShrink: 0 }} />
                          <Text style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_500Medium', color: isLocked ? colors.mutedForeground : colors.foreground }} numberOfLines={1}>
                            {n.subject}
                          </Text>
                          <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: col }}>
                            {lbl}
                          </Text>
                          {!isLocked && (
                            <Feather name="chevron-right" size={12} color={colors.mutedForeground} />
                          )}
                        </Pressable>
                      );
                    })}
                  </View>
                </View>
              ))}

              <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center', marginTop: 4, marginBottom: 8 }}>
                Tap a concept to study it
              </Text>
            </View>
          );
        })()
      )}

      {/* Mastery bar — only shown in study view */}
      {learnView === 'study' && summary && (
        <View style={{ marginBottom: 12 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 5 }}>
            <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
              {summary.graduated}/{summary.total} graduated
            </Text>
            <Text style={[styles.itemMeta, { color: colors.foreground, fontFamily: 'Inter_600SemiBold' }]}>
              {summary.mastery_pct}%
            </Text>
          </View>
          <View style={{ height: 6, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
            <View
              style={{
                height: '100%',
                width: `${summary.mastery_pct}%` as any,
                backgroundColor: colors.primary,
                borderRadius: 3,
              }}
            />
          </View>
        </View>
      )}

      {/* Due for review banner — study view only, shown when concepts are overdue */}
      {learnView === 'study' && summary && (summary.due_count ?? 0) > 0 && (
        <Pressable
          onPress={async () => {
            // Load a question for the most-overdue concept
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
            try {
              const r = await mobileFetch(`${apiBase}/works/${workId}/learning/due`);
              if (r.ok) {
                const d = await r.json();
                const first = d.due?.[0];
                if (first?.id) { await loadQuestion(first.id); }
                else { await loadQuestion(null); }
              } else {
                await loadQuestion(null);
              }
            } catch {
              await loadQuestion(null);
            }
          }}
          style={({ pressed }: { pressed: boolean }) => ({
            flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
            backgroundColor: T.giltSoft, borderWidth: 1, borderColor: T.giltLine,
            borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10, marginBottom: 14,
            opacity: pressed ? 0.8 : 1,
          })}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Feather name="clock" size={15} color={T.gilt} />
            <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: T.gilt }}>
              {summary.due_count} concept{summary.due_count !== 1 ? 's' : ''} due for review
            </Text>
          </View>
          <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: T.gilt }}>
            Review →
          </Text>
        </Pressable>
      )}

      {/* Concept chip — study view only; masked when interleaved + question phase */}
      {learnView === 'study' && session && (
        session.session_mode === 'interleaved' && phase === 'question' ? (
          <View style={{
            borderWidth: 1, borderColor: '#8b5cf644', borderRadius: 10,
            padding: 14, marginBottom: 16, backgroundColor: '#8b5cf608',
          }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <Feather name="shuffle" size={11} color="#7c3aed" />
              <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: '#7c3aed', textTransform: 'uppercase', letterSpacing: 0.8 }}>
                Interleaved · {interleavedHistory.length + 1}/{INTERLEAVED_SESSION_LENGTH}
              </Text>
            </View>
            <Text style={[styles.itemTitle, { color: colors.foreground, fontSize: 16 }]}>
              Which concept does this test?
            </Text>
            <Text style={[styles.itemMeta, { color: colors.mutedForeground, marginTop: 4 }]}>
              Identify the concept and answer — revealed after you submit.
            </Text>
          </View>
        ) : (
          <View style={{
            borderWidth: 1, borderColor: colors.border, borderRadius: 10,
            padding: 14, marginBottom: 16, backgroundColor: colors.background,
          }}>
            <Text style={[styles.itemMeta, {
              color: colors.mutedForeground, textTransform: 'uppercase',
              letterSpacing: 0.8, marginBottom: 4,
            }]}>
              {session.session_mode === 'interleaved' ? 'Revealed concept' : 'Studying'}
            </Text>
            <Text style={[styles.itemTitle, { color: colors.foreground, fontSize: 16 }]}>
              {session.subject}
            </Text>
            {session.session_mode !== 'interleaved' && session.description ? (
              <Text style={[styles.itemMeta, { color: colors.mutedForeground, marginTop: 4 }]}>
                {session.description}
              </Text>
            ) : null}
          </View>
        )
      )}

      {/* Question card — study view only */}
      {learnView === 'study' && session && (
        <View style={{
          borderWidth: 1, borderColor: colors.border, borderRadius: 12,
          padding: 16, marginBottom: 16, backgroundColor: colors.background,
        }}>
          {/* Transfer question badge */}
          {session.question_type === 'transfer' && (
            <View style={{
              flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10,
            }}>
              <View style={{
                flexDirection: 'row', alignItems: 'center', gap: 4,
                paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20,
                backgroundColor: T.giltSoft, borderWidth: 1, borderColor: T.giltLine,
              }}>
                <Feather name="zap" size={11} color={T.gilt} />
                <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: T.gilt }}>
                  Application question
                </Text>
              </View>
              <Text style={{ fontSize: 10, color: colors.mutedForeground, fontFamily: 'Inter_400Regular' }}>
                Novel scenario
              </Text>
            </View>
          )}

          {session.context_snippet ? (
            <Text style={[styles.itemMeta, {
              color: colors.mutedForeground, fontStyle: 'italic',
              marginBottom: 12, borderLeftWidth: 2, borderLeftColor: colors.border, paddingLeft: 10,
            }]}>
              {session.context_snippet}
            </Text>
          ) : null}

          <Text style={[styles.itemTitle, { color: colors.foreground, fontSize: 15, lineHeight: 23, marginBottom: 16 }]}>
            {session.question}
          </Text>

          {phase !== 'feedback' ? (
            <>
              <TextInput
                value={answer}
                onChangeText={setAnswer}
                multiline
                numberOfLines={5}
                placeholder="Write your answer here…"
                placeholderTextColor={colors.mutedForeground}
                editable={phase === 'question'}
                style={{
                  borderWidth: 1, borderColor: colors.border, borderRadius: 8,
                  padding: 12, color: colors.foreground, fontSize: 14,
                  fontFamily: 'Inter_400Regular', minHeight: 110,
                  textAlignVertical: 'top', backgroundColor: colors.background,
                  marginBottom: 12, opacity: phase === 'assessing' ? 0.6 : 1,
                }}
              />
              <Pressable
                onPress={submitAnswer}
                disabled={!answer.trim() || phase === 'assessing'}
                style={({ pressed }) => ({
                  flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                  gap: 8, paddingVertical: 12, borderRadius: 10,
                  backgroundColor: colors.primary,
                  opacity: (!answer.trim() || phase === 'assessing' || pressed) ? 0.6 : 1,
                })}
              >
                {phase === 'assessing'
                  ? <ActivityIndicator size="small" color={colors.primaryForeground} />
                  : <Feather name="send" size={14} color={colors.primaryForeground} />}
                <Text style={{ color: colors.primaryForeground, fontFamily: 'Inter_600SemiBold', fontSize: 14 }}>
                  {phase === 'assessing' ? 'Assessing…' : 'Submit Answer'}
                </Text>
              </Pressable>
            </>
          ) : result ? (
            /* Differentiated feedback by error type */
            <View style={{ gap: 12 }}>
              {/* User's answer (dimmed) */}
              <Text style={[styles.itemMeta, {
                color: colors.mutedForeground, fontStyle: 'italic',
                padding: 10, borderRadius: 6, backgroundColor: colors.muted,
              }]}>
                {answer}
              </Text>

              {/* Score badge */}
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <Text style={{ fontSize: 24, fontFamily: 'Inter_700Bold', color: scoreColor(result.score) }}>
                  {Math.round(result.score * 100)}%
                </Text>
                {result.graduated && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 20, backgroundColor: T.greenSoft }}>
                    <Feather name="award" size={12} color={T.green} />
                    <Text style={{ fontSize: 11, color: T.green, fontFamily: 'Inter_600SemiBold' }}>Graduated!</Text>
                  </View>
                )}
              </View>

              {/* Interleaved concept reveal — bound to session's recorded mode, not toggle state */}
              {session?.session_mode === 'interleaved' && session && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: '#8b5cf610', borderWidth: 1, borderColor: '#8b5cf633' }}>
                  <Feather name="shuffle" size={12} color="#7c3aed" />
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>This tested: </Text>
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.foreground, flex: 1 }} numberOfLines={1}>
                    {session.subject}
                  </Text>
                </View>
              )}

              {/* ── careless_slip ─── amber retry card */}
              {result.error_type === 'careless_slip' && (
                <View style={{
                  borderWidth: 1, borderColor: T.giltLine, borderRadius: 10,
                  backgroundColor: '#fffbeb', padding: 12, gap: 8,
                }}>
                  <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10 }}>
                    <View style={{
                      width: 28, height: 28, borderRadius: 14,
                      backgroundColor: T.giltSoft, alignItems: 'center', justifyContent: 'center',
                    }}>
                      <Feather name="alert-circle" size={14} color={T.gilt} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: T.gilt, marginBottom: 2 }}>
                        Almost — small slip
                      </Text>
                      <Text style={{ fontSize: 13, color: T.gilt, lineHeight: 19 }}>{result.feedback}</Text>
                    </View>
                  </View>
                  <Pressable
                    onPress={() => {
                      // In interleaved mode, pick any concept (not retry the same one)
                      if (session?.session_mode === 'interleaved') {
                        loadQuestion(null, true);
                      } else {
                        loadQuestion(session?.concept_id);
                      }
                    }}
                    style={({ pressed }) => ({
                      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                      gap: 6, paddingVertical: 10, borderRadius: 8,
                      borderWidth: 1, borderColor: T.giltLine, backgroundColor: T.giltSoft,
                      opacity: pressed ? 0.7 : 1,
                    })}
                  >
                    <Feather name="refresh-cw" size={13} color={T.gilt} />
                    <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: T.gilt }}>Try once more</Text>
                  </Pressable>
                </View>
              )}

              {/* ── procedural_gap ─── blue expandable worked-example card */}
              {result.error_type === 'procedural_gap' && (
                <MobileProceduralGapCard
                  feedback={result.feedback}
                  remediationHint={result.remediation_hint}
                  colors={colors}
                />
              )}

              {/* ── conceptual_misconception ─── violet + Socratic follow-up */}
              {result.error_type === 'conceptual_misconception' && (
                <View style={{ gap: 8 }}>
                  {result.deep_review_needed && (
                    <View style={{
                      flexDirection: 'row', alignItems: 'center', gap: 6,
                      paddingHorizontal: 10, paddingVertical: 7, borderRadius: 8,
                      backgroundColor: T.rustSoft, borderWidth: 1, borderColor: T.rust + '66',
                    }}>
                      <Feather name="alert-triangle" size={12} color={T.rust} />
                      <Text style={{ fontSize: 12, color: '#b91c1c', fontFamily: 'Inter_500Medium', flex: 1 }}>
                        Deep review needed — this misconception has appeared multiple times
                      </Text>
                    </View>
                  )}
                  <View style={{
                    borderWidth: 1, borderColor: '#c4b5fd', borderRadius: 10,
                    backgroundColor: '#f5f3ff', padding: 12,
                    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
                  }}>
                    <View style={{
                      width: 28, height: 28, borderRadius: 14,
                      backgroundColor: '#ede9fe', alignItems: 'center', justifyContent: 'center',
                    }}>
                      <Feather name="help-circle" size={13} color={T.gilt} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#5b21b6', marginBottom: 2 }}>
                        Conceptual misconception
                      </Text>
                      <Text style={{ fontSize: 13, color: '#6d28d9', lineHeight: 19 }}>{result.feedback}</Text>
                    </View>
                  </View>
                  {result.socratic_followup && (
                    <View style={{
                      borderWidth: 1, borderColor: '#c4b5fd', borderRadius: 10,
                      backgroundColor: '#faf5ff', padding: 12, gap: 6,
                    }}>
                      <Text style={{ fontSize: 10, color: T.gilt, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.6 }}>
                        Socratic follow-up
                      </Text>
                      <Text style={{ fontSize: 14, color: '#4c1d95', lineHeight: 21, fontFamily: 'Inter_500Medium' }}>
                        {result.socratic_followup}
                      </Text>
                    </View>
                  )}
                </View>
              )}

              {/* ── knowledge_gap ─── red card + prereq link */}
              {result.error_type === 'knowledge_gap' && (
                <View style={{ gap: 8 }}>
                  <View style={{
                    borderWidth: 1, borderColor: '#fca5a5', borderRadius: 10,
                    backgroundColor: '#fef2f2', padding: 12,
                    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
                  }}>
                    <View style={{
                      width: 28, height: 28, borderRadius: 14,
                      backgroundColor: T.rustSoft, alignItems: 'center', justifyContent: 'center',
                    }}>
                      <Feather name="book-open" size={13} color={T.rust} />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#991b1b', marginBottom: 2 }}>
                        Knowledge gap
                      </Text>
                      <Text style={{ fontSize: 13, color: '#b91c1c', lineHeight: 19 }}>{result.feedback}</Text>
                    </View>
                  </View>
                  {result.suggested_prereq_id && result.suggested_prereq_subject && (
                    <View style={{
                      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
                      padding: 12, borderRadius: 10, borderWidth: 1, borderColor: colors.border,
                      backgroundColor: colors.muted,
                    }}>
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontSize: 10, color: colors.mutedForeground, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 2 }}>
                          Suggested prerequisite
                        </Text>
                        <Text style={{ fontSize: 13, color: colors.foreground, fontFamily: 'Inter_500Medium' }} numberOfLines={1}>
                          {result.suggested_prereq_subject}
                        </Text>
                      </View>
                      <Pressable
                        onPress={() => loadQuestion(result.suggested_prereq_id, false)}  // exit interleaved to drill specific prereq
                        style={({ pressed }) => ({
                          flexDirection: 'row', alignItems: 'center', gap: 4,
                          paddingHorizontal: 10, paddingVertical: 7, borderRadius: 8,
                          backgroundColor: colors.primary, opacity: pressed ? 0.7 : 1, marginLeft: 8,
                        })}
                      >
                        <Text style={{ fontSize: 12, color: colors.primaryForeground, fontFamily: 'Inter_600SemiBold' }}>Study first</Text>
                        <Feather name="chevron-right" size={12} color={colors.primaryForeground} />
                      </Pressable>
                    </View>
                  )}
                </View>
              )}

              {/* ── correct / generic fallback ─── plain score card */}
              {!result.error_type && (
                <View style={{
                  padding: 12, borderRadius: 10, backgroundColor: scoreBg(result.score),
                }}>
                  <Text style={{ fontSize: 13, color: scoreColor(result.score), lineHeight: 19 }}>
                    {result.feedback}
                  </Text>
                </View>
              )}

              {/* Routing hint (skip for careless_slip — it has its own retry CTA) */}
              {result.error_type !== 'careless_slip' && (
                <Text style={[styles.itemMeta, { color: colors.mutedForeground }]}>
                  → {result.route === 'STEP_FORWARD'
                    ? 'Moving to the next concept'
                    : result.route === 'STEP_BACKWARD'
                    ? 'Revisiting a foundational concept'
                    : 'Keep practising this concept'}
                </Text>
              )}

              {/* Navigation button (skip for careless_slip — its card has the retry CTA) */}
              {result.error_type !== 'careless_slip' && (
                <Pressable
                  onPress={next}
                  style={({ pressed }) => ({
                    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                    gap: 8, paddingVertical: 12, borderRadius: 10,
                    backgroundColor: colors.primary, opacity: pressed ? 0.7 : 1,
                  })}
                >
                  <Feather
                    name={
                      result.summary.mastery_pct === 100 ? 'award'
                      : result.route === 'STEP_FORWARD' ? 'chevron-right'
                      : result.error_type === 'knowledge_gap' ? 'book-open'
                      : 'refresh-cw'
                    }
                    size={14}
                    color={colors.primaryForeground}
                  />
                  <Text style={{ color: colors.primaryForeground, fontFamily: 'Inter_600SemiBold', fontSize: 14 }}>
                    {result.summary.mastery_pct === 100
                      ? 'Done!'
                      : result.route === 'STEP_FORWARD'
                      ? 'Next Concept'
                      : result.error_type === 'knowledge_gap' && result.suggested_prereq_id
                      ? 'Review Prerequisite'
                      : 'Keep Practising'}
                  </Text>
                </Pressable>
              )}
            </View>
          ) : null}
        </View>
      )}
    </ScrollView>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────

// ─── Book Intelligence Tab ─────────────────────────────────────────────────────

const SEV_COLOR: Record<string, string> = {
  high: VELLUM_LIGHT.rust,
  medium: VELLUM_LIGHT.gilt,
  low: '#6366f1',
};

const CHAPTER_STATUS_COLOR: Record<string, string> = {
  present: VELLUM_LIGHT.green,
  incomplete: VELLUM_LIGHT.gilt,
  missing: VELLUM_LIGHT.rust,
};

function BookIntelTab({
  bookIntel,
  loading,
  colors,
  onDiscuss,
  chapters,
  chaptersLoading,
  workId,
}: {
  bookIntel: any;
  loading: boolean;
  colors: ReturnType<typeof useColors>;
  onDiscuss: (seed: string) => void;
  chapters?: any[];
  chaptersLoading?: boolean;
  workId: string;
}) {
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  // Derive bookIntel sections so they can be conditionally included inside a
  // single ScrollView — avoids early returns that would suppress the chapter
  // outline when bookIntel hasn't loaded yet.
  const hasIntel = !!bookIntel;
  const c = bookIntel?.completeness ?? {};
  const dims = [
    { key: 'structural_pct', label: 'Structural', icon: 'layers' as const },
    { key: 'content_pct',    label: 'Content',    icon: 'file-text' as const },
    { key: 'research_pct',   label: 'Research',   icon: 'search' as const },
    { key: 'editorial_pct',  label: 'Editorial',  icon: 'check-circle' as const },
  ];
  const outline: any[] = bookIntel?.outline ?? [];
  const gaps: any[] = bookIntel?.gaps ?? [];
  const topGaps = gaps.slice(0, 5);

  const barColor = (pct: number) =>
    pct >= 70 ? T.green : pct >= 40 ? T.gilt : T.rust;

  // Show a full-screen spinner only while both book-intel AND chapters are
  // still loading and there is nothing to show yet.
  const chaptersReady = !chaptersLoading && chapters !== undefined;
  if (loading && !hasIntel && !chaptersReady) {
    return (
      <View style={{ flex: 1, paddingTop: 8 }}>
        {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 16, paddingBottom: insets.bottom + 24, gap: 16 }}>

      {/* ── "No intelligence yet" placeholder — shown inline when bookIntel
           hasn't loaded, so the chapter outline below still renders ──── */}
      {!hasIntel && (
        <View style={{ alignItems: 'center', paddingVertical: 32, paddingHorizontal: 24, gap: 8 }}>
          <Feather name="book-open" size={32} color={colors.mutedForeground} />
          {loading
            ? <ActivityIndicator color={colors.primary} style={{ marginTop: 4 }} />
            : <>
                <Text style={{ fontSize: 15, fontFamily: 'Inter_600SemiBold', color: colors.foreground, textAlign: 'center' }}>
                  No intelligence yet
                </Text>
                <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center', maxWidth: 280, lineHeight: 20 }}>
                  Link documents to this Work and process them to build the Book Intelligence view.
                </Text>
              </>
          }
        </View>
      )}

      {/* ── Next action card ─────────────────────────────────── */}
      {hasIntel && bookIntel.next_action ? (
        <View style={{
          borderRadius: 12, overflow: 'hidden',
          backgroundColor: colors.primary + '0c',
          borderWidth: 1, borderColor: colors.primary + '38',
        }}>
          <View style={{
            flexDirection: 'row', alignItems: 'center', gap: 8,
            paddingHorizontal: 14, paddingVertical: 10,
            borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.primary + '28',
            backgroundColor: colors.primary + '10',
          }}>
            <Feather name="zap" size={14} color={colors.primary} />
            <Text style={{ fontSize: 12, fontFamily: 'Inter_700Bold', color: colors.primary, letterSpacing: 0.6, textTransform: 'uppercase' }}>
              Next step
            </Text>
          </View>
          <View style={{ paddingHorizontal: 14, paddingVertical: 12 }}>
            <Text style={{ fontSize: 14, fontFamily: 'Inter_500Medium', color: colors.foreground, lineHeight: 21 }}>
              {bookIntel.next_action}
            </Text>
            <Pressable
              onPress={() => onDiscuss(bookIntel.next_action)}
              hitSlop={8}
              style={({ pressed }) => ({
                marginTop: 10, alignSelf: 'flex-start',
                flexDirection: 'row', alignItems: 'center', gap: 5,
                paddingHorizontal: 12, paddingVertical: 6,
                borderRadius: 8, borderWidth: 1,
                borderColor: colors.primary + '55',
                backgroundColor: pressed ? colors.primary + '18' : colors.primary + '0c',
              })}
            >
              <Feather name="message-circle" size={13} color={colors.primary} />
              <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>
                Discuss →
              </Text>
            </Pressable>
          </View>
        </View>
      ) : null}

      {/* ── Completeness ─────────────────────────────────────── */}
      {hasIntel && (
      <View style={{
        borderRadius: 12, borderWidth: 1, borderColor: colors.border,
        backgroundColor: colors.card, overflow: 'hidden',
      }}>
        <View style={{
          paddingHorizontal: 14, paddingVertical: 10,
          borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
          backgroundColor: colors.muted + '44',
          flexDirection: 'row', alignItems: 'center', gap: 8,
        }}>
          <Feather name="bar-chart-2" size={14} color={colors.mutedForeground} />
          <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8, textTransform: 'uppercase' }}>
            Completeness
          </Text>
        </View>
        <View style={{ paddingHorizontal: 14, paddingVertical: 12, gap: 12 }}>
          {dims.map(({ key, label, icon }) => {
            const pct: number = c[key] ?? 0;
            const col = barColor(pct);
            return (
              <View key={key}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginBottom: 5 }}>
                  <Feather name={icon} size={12} color={colors.mutedForeground} />
                  <Text style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }}>
                    {label}
                  </Text>
                  <Text style={{ fontSize: 12, fontFamily: 'Inter_700Bold', color: col }}>{pct}%</Text>
                </View>
                <View style={{ height: 5, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
                  <View style={{
                    height: '100%', borderRadius: 3, backgroundColor: col,
                    width: `${pct}%` as any,
                  }} />
                </View>
              </View>
            );
          })}
          {bookIntel.knowledge_total > 0 && (
            <View style={{
              marginTop: 4, paddingTop: 10,
              borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border,
            }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                  Knowledge reviewed
                </Text>
                <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
                  {bookIntel.knowledge_reviewed ?? 0} / {bookIntel.knowledge_total}
                </Text>
              </View>
              <View style={{ height: 5, backgroundColor: colors.muted, borderRadius: 3, overflow: 'hidden' }}>
                <View style={{
                  height: '100%', borderRadius: 3, backgroundColor: colors.primary,
                  width: `${Math.round(100 * (bookIntel.knowledge_reviewed ?? 0) / bookIntel.knowledge_total)}%` as any,
                }} />
              </View>
            </View>
          )}
        </View>
      </View>
      )}

      {/* ── Top gaps ─────────────────────────────────────────── */}
      {hasIntel && topGaps.length > 0 && (
        <View style={{
          borderRadius: 12, borderWidth: 1, borderColor: colors.border,
          backgroundColor: colors.card, overflow: 'hidden',
        }}>
          <View style={{
            paddingHorizontal: 14, paddingVertical: 10,
            borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
            backgroundColor: colors.muted + '44',
            flexDirection: 'row', alignItems: 'center', gap: 8,
          }}>
            <Feather name="alert-triangle" size={14} color={T.gilt} />
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8, textTransform: 'uppercase' }}>
              Gaps ({gaps.length})
            </Text>
          </View>
          <View style={{ paddingVertical: 6 }}>
            {topGaps.map((gap: any, i: number) => {
              const sevColor = SEV_COLOR[gap.severity] ?? colors.mutedForeground;
              return (
                <Pressable
                  key={i}
                  delayLongPress={450}
                  onLongPress={() => {
                    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                    Alert.alert(
                      gap.title ?? 'Gap',
                      'Open a work-scoped chat about this gap?',
                      [
                        { text: 'Cancel', style: 'cancel' },
                        { text: 'Discuss →', onPress: () => onDiscuss(gap.title ?? '') },
                      ]
                    );
                  }}
                  style={({ pressed }) => ({
                    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
                    paddingHorizontal: 14, paddingVertical: 10,
                    borderBottomWidth: i < topGaps.length - 1 ? StyleSheet.hairlineWidth : 0,
                    borderBottomColor: colors.border,
                    backgroundColor: pressed ? colors.muted + '55' : 'transparent',
                  })}
                >
                  <View style={{
                    width: 6, height: 6, borderRadius: 3,
                    backgroundColor: sevColor, marginTop: 5,
                  }} />
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground, lineHeight: 18 }}>
                      {gap.title}
                    </Text>
                    {gap.description ? (
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 16 }}>
                        {gap.description}
                      </Text>
                    ) : null}
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 2 }}>
                      Hold to discuss →
                    </Text>
                  </View>
                  <View style={{
                    paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
                    backgroundColor: sevColor + '14', borderWidth: 1, borderColor: sevColor + '40',
                  }}>
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: sevColor }}>
                      {gap.severity}
                    </Text>
                  </View>
                </Pressable>
              );
            })}
          </View>
        </View>
      )}

      {/* ── Outline ──────────────────────────────────────────── */}
      {hasIntel && outline.length > 0 && (
        <View style={{
          borderRadius: 12, borderWidth: 1, borderColor: colors.border,
          backgroundColor: colors.card, overflow: 'hidden',
        }}>
          <View style={{
            paddingHorizontal: 14, paddingVertical: 10,
            borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
            backgroundColor: colors.muted + '44',
            flexDirection: 'row', alignItems: 'center', gap: 8,
          }}>
            <Feather name="list" size={14} color={colors.mutedForeground} />
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8, textTransform: 'uppercase' }}>
              Outline ({outline.length})
            </Text>
          </View>
          <View style={{ paddingVertical: 4 }}>
            {outline.map((ch: any, i: number) => {
              const status: string = ch.chapter_status ?? 'missing';
              const statusColor = CHAPTER_STATUS_COLOR[status] ?? colors.mutedForeground;
              const kc: number = ch.knowledge_count ?? 0;
              const indent = Math.max(0, ((ch.level ?? 1) - 1)) * 14;
              const chTitle = ch.title ?? `Chapter ${i + 1}`;
              return (
                <Pressable
                  key={ch.id ?? i}
                  delayLongPress={450}
                  onPress={() => {
                    if (Platform.OS !== 'web') Haptics.selectionAsync().catch(() => {});
                    onDiscuss(`Help me with chapter: ${chTitle}`);
                  }}
                  onLongPress={() => {
                    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                    Alert.alert(
                      chTitle,
                      'Open a work-scoped chat about this chapter?',
                      [
                        { text: 'Cancel', style: 'cancel' },
                        { text: 'Discuss →', onPress: () => onDiscuss(chTitle) },
                      ]
                    );
                  }}
                  style={({ pressed }) => ({
                    flexDirection: 'row', alignItems: 'center', gap: 8,
                    paddingLeft: 14 + indent, paddingRight: 14, paddingVertical: 9,
                    borderBottomWidth: i < outline.length - 1 ? StyleSheet.hairlineWidth : 0,
                    borderBottomColor: colors.border,
                    backgroundColor: pressed ? colors.muted + '55' : 'transparent',
                  })}
                >
                  {/* Status dot */}
                  <View style={{
                    width: 7, height: 7, borderRadius: 4,
                    backgroundColor: statusColor, flexShrink: 0,
                  }} />

                  {/* Title */}
                  <Text
                    style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 18 }}
                    numberOfLines={2}
                  >
                    {chTitle}
                  </Text>

                  {/* Research chip — tapping navigates to the chapter on the
                      Intelligence screen when there is knowledge to show */}
                  <Pressable
                    onPress={kc > 0 ? () => router.push(`/work/${workId}/intelligence?chapterId=${ch.id}` as any) : undefined}
                    hitSlop={6}
                    style={({ pressed }) => ({
                      paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
                      backgroundColor: kc === 0 ? colors.muted : colors.primary + '14',
                      borderWidth: 1, borderColor: kc === 0 ? colors.border : colors.primary + '38',
                      opacity: pressed ? 0.6 : 1,
                    })}
                  >
                    <Text style={{
                      fontSize: 10, fontFamily: 'Inter_600SemiBold',
                      color: kc === 0 ? colors.mutedForeground : colors.primary,
                    }}>
                      {kc}
                    </Text>
                  </Pressable>
                </Pressable>
              );
            })}
          </View>
          {/* Legend */}
          <View style={{
            flexDirection: 'row', gap: 14, paddingHorizontal: 14, paddingVertical: 10,
            borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border,
            backgroundColor: colors.muted + '28',
          }}>
            {([['present','Present'],['incomplete','Incomplete'],['missing','Missing']] as const).map(([s, label]) => (
              <View key={s} style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: CHAPTER_STATUS_COLOR[s] }} />
                <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>{label}</Text>
              </View>
            ))}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
              <View style={{
                width: 14, height: 14, borderRadius: 3, borderWidth: 1,
                alignItems: 'center', justifyContent: 'center',
                borderColor: colors.primary + '38', backgroundColor: colors.primary + '14',
              }}>
                <Text style={{ fontSize: 7, color: colors.primary, fontFamily: 'Inter_700Bold' }}>n</Text>
              </View>
              <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>Research items</Text>
            </View>
          </View>
        </View>
      )}

      {/* ── Chapter outline — always rendered so the empty state is reachable.
           The card shows a spinner while loading, rows when populated, and an
           instructional message when the fetch returns an empty list. ─────── */}
      <View style={{
          borderRadius: 12, borderWidth: 1, borderColor: colors.border,
          backgroundColor: colors.card, overflow: 'hidden',
        }}>
          {/* Header */}
          <View style={{
            paddingHorizontal: 14, paddingVertical: 10,
            borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
            backgroundColor: colors.muted + '44',
            flexDirection: 'row', alignItems: 'center', gap: 8,
          }}>
            <Feather name="align-left" size={14} color={colors.mutedForeground} />
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.8, textTransform: 'uppercase', flex: 1 }}>
              Chapter Outline{chapters && chapters.length > 0 ? ` (${chapters.length})` : ''}
            </Text>
            {chaptersLoading && <ActivityIndicator size="small" color={colors.primary} />}
          </View>

          {/* Chapter rows */}
          {chapters && chapters.length > 0 ? (
            <View style={{ paddingVertical: 4 }}>
              {chapters.map((ch: any, i: number) => {
                const words: number = ch.word_count ?? 0;
                const scenes: number = ch.scene_count ?? 0;
                const readiness: string = ch.readiness ?? 'unknown';
                const readinessColor =
                  readiness === 'ready'    ? T.green :
                  readiness === 'imported' ? T.gilt :
                  readiness === 'error'    ? T.rust :
                  readiness === 'no_text'  ? T.rust :
                  colors.mutedForeground;

                return (
                  <Pressable
                    key={ch.id ?? i}
                    onLongPress={() => {
                      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      Alert.alert(
                        ch.title ?? `Chapter ${i + 1}`,
                        'Open a work-scoped chat about this chapter?',
                        [
                          { text: 'Cancel', style: 'cancel' },
                          { text: 'Discuss →', onPress: () => onDiscuss(ch.title ?? `Chapter ${i + 1}`) },
                        ],
                      );
                    }}
                    delayLongPress={450}
                    style={({ pressed }) => ({
                      flexDirection: 'row', alignItems: 'center', gap: 10,
                      paddingHorizontal: 14, paddingVertical: 10,
                      borderBottomWidth: i < chapters.length - 1 ? StyleSheet.hairlineWidth : 0,
                      borderBottomColor: colors.border,
                      backgroundColor: pressed ? colors.muted + '55' : 'transparent',
                    })}
                  >
                    {/* Chapter number */}
                    <View style={{
                      width: 24, height: 24, borderRadius: 5,
                      backgroundColor: colors.muted, alignItems: 'center', justifyContent: 'center',
                      flexShrink: 0,
                    }}>
                      <Text style={{ fontSize: 10, fontFamily: 'Inter_700Bold', color: colors.mutedForeground }}>
                        {i + 1}
                      </Text>
                    </View>

                    {/* Title + meta */}
                    <View style={{ flex: 1, gap: 2 }}>
                      <Text
                        style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground, lineHeight: 18 }}
                        numberOfLines={2}
                      >
                        {ch.title ?? `Chapter ${i + 1}`}
                      </Text>
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                        {words > 0 ? `${words.toLocaleString()} words` : 'no words'}
                        {scenes > 0 ? ` · ${scenes} scene${scenes !== 1 ? 's' : ''}` : ''}
                        {ch.doc_title ? ` · ${ch.doc_title}` : ''}
                      </Text>
                    </View>

                    {/* Readiness badge */}
                    <View style={{
                      paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
                      backgroundColor: readinessColor + '18',
                      borderWidth: 1, borderColor: readinessColor + '44',
                      flexShrink: 0,
                    }}>
                      <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: readinessColor }}>
                        {readiness}
                      </Text>
                    </View>
                  </Pressable>
                );
              })}
            </View>
          ) : !chaptersLoading ? (
            <View style={{ paddingHorizontal: 14, paddingVertical: 16, alignItems: 'center', gap: 6 }}>
              <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center' }}>
                No chapters detected yet. Process documents linked to this Work to see the outline.
              </Text>
            </View>
          ) : null}

          {/* Footer hint */}
          {chapters && chapters.length > 0 && (
            <View style={{
              paddingHorizontal: 14, paddingVertical: 8,
              borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border,
              backgroundColor: colors.muted + '28',
            }}>
              <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                Hold a chapter to open a work-scoped chat about it
              </Text>
            </View>
          )}
        </View>

    </ScrollView>
  );
}

// ─── Genesis Tab ──────────────────────────────────────────────────────────────

interface GenesisStage {
  code: string;
  name: string;
  status: 'PENDING' | 'PASSED' | 'FAILED';
  gate_description: string;
  is_current: boolean;
}

interface GenesisBook {
  id: string;
  work_id: string;
  mode: string;
  length: number;
  acts: number;
  state: string;
  sealed: boolean;
  manifest: string | null;
  created_at: string;
  updated_at: string;
  stages: GenesisStage[];
  next_stage: string | null;
  ledger_entries: number;
}

interface GenesisStageDetail {
  code: string;
  name: string;
  gate_description: string;
  status: 'PENDING' | 'PASSED' | 'FAILED';
  content: string;
  has_unfilled_placeholders: boolean;
  sha256: string;
  updated_at: string | null;
  decisions: Array<{ kind: string; payload: string; at: string }>;
}

const GENESIS_STATUS_COLOR = {
  PASSED:  { bg: VELLUM_LIGHT.greenSoft, text: VELLUM_LIGHT.green, dot: VELLUM_LIGHT.green  },
  FAILED:  { bg: VELLUM_LIGHT.rustSoft,  text: VELLUM_LIGHT.rust,  dot: VELLUM_LIGHT.rust   },
  PENDING: { bg: 'rgba(148,163,184,0.12)', text: '#94a3b8', dot: '#94a3b8' },
  CURRENT: { bg: VELLUM_LIGHT.giltSoft,  text: VELLUM_LIGHT.gilt,  dot: VELLUM_LIGHT.gilt   },
};

function genesisStatusColor(stage: GenesisStage) {
  if (stage.status === 'PASSED') return GENESIS_STATUS_COLOR.PASSED;
  if (stage.status === 'FAILED') return GENESIS_STATUS_COLOR.FAILED;
  if (stage.is_current)         return GENESIS_STATUS_COLOR.CURRENT;
  return GENESIS_STATUS_COLOR.PENDING;
}

function genesisStatusLabel(stage: GenesisStage) {
  if (stage.status === 'PASSED') return 'PASSED';
  if (stage.status === 'FAILED') return 'FAILED';
  if (stage.is_current)         return 'OPEN';
  return 'PENDING';
}

function GenesisGateRow({
  stage,
  workId,
  colors,
  onRefresh,
}: {
  stage: GenesisStage;
  workId: string;
  colors: any;
  onRefresh: () => void;
}) {
  const T = useVellumTokens();
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<GenesisStageDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [artifactModalOpen, setArtifactModalOpen] = useState(false);
  const [gating, setGating] = useState(false);
  const [authorInput, setAuthorInput] = useState('');
  const [noteInput, setNoteInput] = useState('');
  const [showGateForm, setShowGateForm] = useState(false);

  const col = genesisStatusColor(stage);
  const statusLabel = genesisStatusLabel(stage);

  const loadDetail = useCallback(async () => {
    if (detail || loadingDetail) return;
    setLoadingDetail(true);
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/genesis/stages/${stage.code}`);
      if (r.ok) setDetail(await r.json());
    } catch { /* non-fatal */ }
    finally { setLoadingDetail(false); }
  }, [domain, workId, stage.code, detail, loadingDetail]);

  useEffect(() => {
    if (expanded && !detail) loadDetail();
  }, [expanded, detail, loadDetail]);

  const handlePassGate = async () => {
    if (!authorInput.trim()) {
      Alert.alert('Author required', 'Please enter your name to record the gate decision.');
      return;
    }
    setGating(true);
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/genesis/stages/${stage.code}/gate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision: 'pass', author: authorInput.trim(), note: noteInput.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        Alert.alert('Gate failed', (body as any).detail ?? 'Could not record gate decision');
        return;
      }
      setShowGateForm(false);
      setAuthorInput('');
      setNoteInput('');
      setDetail(null); // force refetch
      onRefresh();
    } catch (e: any) {
      Alert.alert('Error', e?.message ?? 'Gate recording failed');
    } finally {
      setGating(false);
    }
  };

  const artifactPreview = detail?.content
    ? detail.content.slice(0, 500) + (detail.content.length > 500 ? '…' : '')
    : '';

  return (
    <View style={{ marginBottom: 8 }}>
      {/* Gate row header */}
      <Pressable
        onPress={() => setExpanded(e => !e)}
        style={({ pressed }) => ({
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          padding: 12,
          borderRadius: expanded ? 0 : 10,
          borderTopLeftRadius: 10,
          borderTopRightRadius: 10,
          borderBottomLeftRadius: expanded ? 0 : 10,
          borderBottomRightRadius: expanded ? 0 : 10,
          borderWidth: StyleSheet.hairlineWidth,
          borderColor: stage.is_current ? T.giltLine : colors.border,
          backgroundColor: stage.is_current ? T.giltSoft : colors.card,
          opacity: pressed ? 0.85 : 1,
        })}
      >
        {/* Status dot */}
        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: col.dot, flexShrink: 0 }} />

        {/* Code + name */}
        <Text style={{ fontSize: 11, fontFamily: 'Inter_700Bold', color: col.text, width: 28 }}>
          {stage.code}
        </Text>
        <Text style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }} numberOfLines={1}>
          {stage.name}
        </Text>

        {/* Status badge */}
        <View style={{ paddingHorizontal: 7, paddingVertical: 2, borderRadius: 5, backgroundColor: col.bg }}>
          <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: col.text }}>{statusLabel}</Text>
        </View>

        <Feather name={expanded ? 'chevron-up' : 'chevron-down'} size={14} color={colors.mutedForeground} />
      </Pressable>

      {/* Expanded content */}
      {expanded && (
        <View style={{
          borderWidth: StyleSheet.hairlineWidth,
          borderTopWidth: 0,
          borderColor: stage.is_current ? T.giltLine : colors.border,
          borderBottomLeftRadius: 10,
          borderBottomRightRadius: 10,
          backgroundColor: colors.card,
          padding: 12,
          gap: 10,
        }}>
          {/* Gate description */}
          <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 18 }}>
            {stage.gate_description}
          </Text>

          {loadingDetail ? (
            <ActivityIndicator size="small" color={colors.primary} />
          ) : detail ? (
            <>
              {/* Unfilled placeholder warning */}
              {detail.has_unfilled_placeholders && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, padding: 8, borderRadius: 8, borderWidth: 1, borderColor: T.giltLine, backgroundColor: T.giltSoft }}>
                  <Feather name="alert-circle" size={13} color={T.gilt} />
                  <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: T.gilt, flex: 1 }}>
                    Artifact still contains {'<<FILL>>'} placeholders — fill before passing.
                  </Text>
                </View>
              )}

              {/* Artifact preview */}
              {detail.content.length > 0 && (
                <View style={{ gap: 6 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.5, textTransform: 'uppercase' }}>
                      Artifact
                    </Text>
                    <Pressable onPress={() => setArtifactModalOpen(true)} hitSlop={8}>
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.primary }}>View full</Text>
                    </Pressable>
                  </View>
                  <View style={{ backgroundColor: colors.muted + '33', borderRadius: 8, padding: 10 }}>
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 17 }}>
                      {artifactPreview}
                    </Text>
                  </View>
                </View>
              )}

              {/* Last decision */}
              {detail.decisions.length > 0 && (() => {
                const last = detail.decisions[detail.decisions.length - 1];
                let payload: any = {};
                try { payload = JSON.parse(last.payload); } catch {}
                const isPass = last.kind === 'gate.pass';
                return (
                  <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6, padding: 8, borderRadius: 8, borderWidth: 1, borderColor: isPass ? T.green + '44' : T.rust + '44', backgroundColor: isPass ? T.greenSoft : T.rustSoft }}>
                    <Feather name={isPass ? 'check-circle' : 'x-circle'} size={13} color={isPass ? T.green : T.rust} style={{ marginTop: 1 }} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: isPass ? T.green : T.rust }}>
                        {isPass ? 'Passed' : 'Failed'} by {payload.author ?? '—'}
                      </Text>
                      {payload.note ? (
                        <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 2 }}>
                          {payload.note}
                        </Text>
                      ) : null}
                      <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground + '99', marginTop: 2 }}>
                        {last.at ? new Date(last.at).toLocaleString() : ''}
                      </Text>
                    </View>
                  </View>
                );
              })()}

              {/* Pass Gate form — only for current open gate with no fill placeholders */}
              {stage.is_current && stage.status !== 'PASSED' && !detail.has_unfilled_placeholders && (
                <View style={{ gap: 8 }}>
                  {!showGateForm ? (
                    <Pressable
                      onPress={() => setShowGateForm(true)}
                      style={({ pressed }) => ({
                        flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                        gap: 7, paddingVertical: 11, borderRadius: 10,
                        backgroundColor: pressed ? T.green + 'cc' : T.green,
                      })}
                    >
                      <Feather name="check-circle" size={15} color="#fff" />
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_700Bold', color: '#fff' }}>Pass Gate {stage.code}</Text>
                    </Pressable>
                  ) : (
                    <View style={{ gap: 8 }}>
                      <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                        Record gate pass
                      </Text>
                      <TextInput
                        style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground, backgroundColor: colors.background }}
                        placeholder="Your name (required)"
                        placeholderTextColor={colors.mutedForeground}
                        value={authorInput}
                        onChangeText={setAuthorInput}
                        returnKeyType="next"
                      />
                      <TextInput
                        style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground, backgroundColor: colors.background, minHeight: 60, textAlignVertical: 'top' }}
                        placeholder="Rationale for passing (optional)"
                        placeholderTextColor={colors.mutedForeground}
                        value={noteInput}
                        onChangeText={setNoteInput}
                        multiline
                      />
                      <View style={{ flexDirection: 'row', gap: 8 }}>
                        <Pressable
                          onPress={() => { setShowGateForm(false); setAuthorInput(''); setNoteInput(''); }}
                          style={({ pressed }) => ({ flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 8, borderWidth: 1, borderColor: colors.border, opacity: pressed ? 0.7 : 1 })}
                        >
                          <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>Cancel</Text>
                        </Pressable>
                        <Pressable
                          onPress={handlePassGate}
                          disabled={gating || !authorInput.trim()}
                          style={({ pressed }) => ({ flex: 2, alignItems: 'center', flexDirection: 'row', justifyContent: 'center', gap: 6, paddingVertical: 10, borderRadius: 8, backgroundColor: !authorInput.trim() || gating ? T.green + '55' : T.green, opacity: pressed ? 0.8 : 1 })}
                        >
                          {gating ? <ActivityIndicator size="small" color="#fff" /> : <Feather name="check-circle" size={14} color="#fff" />}
                          <Text style={{ fontSize: 13, fontFamily: 'Inter_700Bold', color: '#fff' }}>
                            {gating ? 'Recording…' : 'Confirm Pass'}
                          </Text>
                        </Pressable>
                      </View>
                    </View>
                  )}
                </View>
              )}
            </>
          ) : null}
        </View>
      )}

      {/* Full artifact modal */}
      <Modal visible={artifactModalOpen} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setArtifactModalOpen(false)}>
        <View style={{ flex: 1, backgroundColor: colors.background }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }}>
            <Text style={{ flex: 1, fontSize: 16, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
              {stage.code} — {stage.name}
            </Text>
            <Pressable onPress={() => setArtifactModalOpen(false)} hitSlop={12}>
              <Feather name="x" size={20} color={colors.mutedForeground} />
            </Pressable>
          </View>
          <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 16 }}>
            <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground, lineHeight: 20 }}>
              {detail?.content ?? ''}
            </Text>
          </ScrollView>
        </View>
      </Modal>
    </View>
  );
}

function GenesisTab({ workId, colors }: { workId: string; colors: any }) {
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const [book, setBook] = useState<GenesisBook | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [fetchError, setFetchError] = useState('');

  // Init form state
  const [initMode, setInitMode] = useState<'cold' | 'library'>('cold');
  const [initLength, setInitLength] = useState('80');
  const [initActs, setInitActs] = useState<3 | 4 | 5>(4);
  const [initing, setIniting] = useState(false);

  // Ledger verify state
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Seal state
  const [sealAuthor, setSealAuthor] = useState('');
  const [showSealForm, setShowSealForm] = useState(false);
  const [sealing, setSealing] = useState(false);

  const fetchBook = useCallback(async () => {
    setLoading(true);
    setFetchError('');
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/genesis`);
      if (r.status === 404) { setNotFound(true); setBook(null); return; }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setBook(data);
      setNotFound(false);
    } catch (e: any) {
      setFetchError(e?.message ?? 'Could not load Genesis data');
    } finally {
      setLoading(false);
    }
  }, [domain, workId]);

  useEffect(() => { fetchBook(); }, [fetchBook]);

  const handleInit = async () => {
    const len = parseInt(initLength, 10);
    if (isNaN(len) || len < 10 || len > 500) {
      Alert.alert('Invalid length', 'Target chapters must be between 10 and 500.');
      return;
    }
    setIniting(true);
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/genesis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: initMode, length: len, acts: initActs }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        Alert.alert('Init failed', (body as any).detail ?? 'Could not start GENESIS');
        return;
      }
      const data = await r.json();
      setBook(data);
      setNotFound(false);
    } catch (e: any) {
      Alert.alert('Error', e?.message ?? 'Init failed');
    } finally {
      setIniting(false);
    }
  };

  const handleVerify = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/genesis/verify`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setVerifyResult(await r.json());
    } catch (e: any) {
      setVerifyResult({ ok: false, message: e?.message ?? 'Verify failed' });
    } finally {
      setVerifying(false);
    }
  };

  const handleSeal = async () => {
    if (!sealAuthor.trim()) {
      Alert.alert('Author required', 'Enter your name to sign off the seal.');
      return;
    }
    setSealing(true);
    try {
      const r = await mobileFetch(`https://${domain}/api/works/${workId}/genesis/seal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ author: sealAuthor.trim() }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        Alert.alert('Seal failed', (body as any).detail ?? 'Could not seal package');
        return;
      }
      setShowSealForm(false);
      setSealAuthor('');
      await fetchBook();
    } catch (e: any) {
      Alert.alert('Error', e?.message ?? 'Seal failed');
    } finally {
      setSealing(false);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <View style={{ flex: 1, paddingTop: 8 }}>
        {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
      </View>
    );
  }

  if (fetchError) {
    return (
      <EmptyState
        icon="alert-circle"
        title="Could not load Genesis"
        body={fetchError}
        cta="Retry"
        onCta={fetchBook}
      />
    );
  }

  // Not found — show init form
  if (notFound || !book) {
    return (
      <ScrollView contentContainerStyle={{ padding: 16, gap: 16 }} keyboardShouldPersistTaps="handled">
        {/* Header */}
        <View style={{ alignItems: 'center', paddingVertical: 20, gap: 10 }}>
          <Feather name="book" size={36} color={colors.mutedForeground} />
          <Text style={{ fontSize: 18, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>Start Origination</Text>
          <Text style={{ fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, textAlign: 'center', lineHeight: 19 }}>
            GENESIS is a 10-gate book origination pipeline.{'\n'}Each gate produces a signed artifact in the tamper-evident ledger.
          </Text>
        </View>

        {/* Mode picker */}
        <View style={{ gap: 8 }}>
          <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.6 }}>Mode</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {(['cold', 'library'] as const).map((m) => (
              <Pressable
                key={m}
                onPress={() => setInitMode(m)}
                style={({ pressed }) => ({
                  flex: 1, paddingVertical: 11, alignItems: 'center', borderRadius: 10, borderWidth: 1,
                  borderColor: initMode === m ? colors.primary : colors.border,
                  backgroundColor: initMode === m ? colors.primary + '18' : pressed ? colors.muted : 'transparent',
                })}
              >
                <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: initMode === m ? colors.primary : colors.mutedForeground }}>
                  {m === 'cold' ? 'COLD' : 'LIBRARY'}
                </Text>
                <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 2 }}>
                  {m === 'cold' ? 'Fresh idea' : 'From corpus'}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>

        {/* Length */}
        <View style={{ gap: 6 }}>
          <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.6 }}>Target Chapters</Text>
          <TextInput
            style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14, fontFamily: 'Inter_400Regular', color: colors.foreground, backgroundColor: colors.background }}
            keyboardType="number-pad"
            value={initLength}
            onChangeText={setInitLength}
            placeholder="80"
            placeholderTextColor={colors.mutedForeground}
          />
        </View>

        {/* Acts */}
        <View style={{ gap: 6 }}>
          <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.6 }}>Acts</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {([3, 4, 5] as const).map((a) => (
              <Pressable
                key={a}
                onPress={() => setInitActs(a)}
                style={({ pressed }) => ({
                  flex: 1, paddingVertical: 10, alignItems: 'center', borderRadius: 10, borderWidth: 1,
                  borderColor: initActs === a ? colors.primary : colors.border,
                  backgroundColor: initActs === a ? colors.primary + '18' : pressed ? colors.muted : 'transparent',
                })}
              >
                <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: initActs === a ? colors.primary : colors.mutedForeground }}>{a}</Text>
              </Pressable>
            ))}
          </View>
        </View>

        {/* Start button */}
        <Pressable
          onPress={handleInit}
          disabled={initing}
          style={({ pressed }) => ({
            alignItems: 'center', flexDirection: 'row', justifyContent: 'center', gap: 8,
            paddingVertical: 14, borderRadius: 12,
            backgroundColor: initing ? colors.muted : pressed ? colors.primary + 'cc' : colors.primary,
          })}
        >
          {initing ? <ActivityIndicator size="small" color="#fff" /> : <Feather name="book" size={16} color="#fff" />}
          <Text style={{ fontSize: 14, fontFamily: 'Inter_700Bold', color: '#fff' }}>
            {initing ? 'Starting…' : 'Start GENESIS'}
          </Text>
        </Pressable>
      </ScrollView>
    );
  }

  // Compute seal eligibility
  const allPassed = book.stages.every((s) => s.status === 'PASSED');
  const isSealed  = book.sealed || book.state === 'READY_FOR_B0';
  const passedCount = book.stages.filter((s) => s.status === 'PASSED').length;

  return (
    <ScrollView
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 16, paddingBottom: insets.bottom + 24 }}
      showsVerticalScrollIndicator={false}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={fetchBook} tintColor={colors.primary} />}
    >
      {/* ── Book header card ───────────────────────────────────────────────── */}
      <View style={{ borderWidth: StyleSheet.hairlineWidth, borderColor: colors.border, borderRadius: 12, backgroundColor: colors.card, padding: 14, gap: 10, marginBottom: 16 }}>
        {/* State badge */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <Feather name={isSealed ? 'lock' : 'book'} size={16} color={isSealed ? T.green : colors.primary} />
            <Text style={{ fontSize: 15, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
              {isSealed ? 'SEALED' : 'DRAFT'}
            </Text>
          </View>
          <View style={{ flex: 1 }} />
          <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, backgroundColor: colors.muted }}>
            <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground }}>
              {book.mode.toUpperCase()} · {book.length} ch · {book.acts} acts
            </Text>
          </View>
        </View>

        {/* Progress bar */}
        <View style={{ gap: 4 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
              {passedCount}/10 gates passed
            </Text>
            <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>
              {Math.round((passedCount / 10) * 100)}%
            </Text>
          </View>
          <View style={{ height: 5, borderRadius: 3, backgroundColor: colors.muted, overflow: 'hidden' }}>
            <View style={{ height: '100%', width: `${Math.round((passedCount / 10) * 100)}%` as any, backgroundColor: isSealed ? T.green : colors.primary, borderRadius: 3 }} />
          </View>
        </View>

        {/* Ledger entries */}
        <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
          {book.ledger_entries} ledger entries · state: <Text style={{ fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>{book.state}</Text>
        </Text>

        {/* Seal hash preview */}
        {isSealed && book.manifest && (() => {
          let manifest: any = {};
          try { manifest = typeof book.manifest === 'string' ? JSON.parse(book.manifest) : book.manifest; } catch {}
          return manifest?.seal_hash ? (
            <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }} numberOfLines={1}>
              seal: {manifest.seal_hash}
            </Text>
          ) : null;
        })()}
      </View>

      {/* ── Action bar ─────────────────────────────────────────────────────── */}
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        {/* Verify ledger */}
        <Pressable
          onPress={handleVerify}
          disabled={verifying}
          style={({ pressed }) => ({
            flexDirection: 'row', alignItems: 'center', gap: 6,
            paddingHorizontal: 14, paddingVertical: 9, borderRadius: 10, borderWidth: 1,
            borderColor: verifyResult ? (verifyResult.ok ? T.green + '88' : T.rust + '88') : colors.border,
            backgroundColor: verifyResult ? (verifyResult.ok ? T.greenSoft : T.rustSoft) : (pressed ? colors.muted : 'transparent'),
            opacity: verifying ? 0.6 : 1,
          })}
        >
          {verifying
            ? <ActivityIndicator size="small" color={colors.primary} />
            : <Feather name="shield" size={14} color={verifyResult ? (verifyResult.ok ? T.green : T.rust) : colors.primary} />}
          <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: verifyResult ? (verifyResult.ok ? T.green : T.rust) : colors.primary }}>
            {verifying ? 'Verifying…' : verifyResult ? (verifyResult.ok ? '✓ Intact' : '⛔ Tampered') : 'Verify Ledger'}
          </Text>
        </Pressable>

        {/* Seal package — visible when all gates passed and not yet sealed */}
        {allPassed && !isSealed && (
          <Pressable
            onPress={() => setShowSealForm(true)}
            style={({ pressed }) => ({
              flexDirection: 'row', alignItems: 'center', gap: 6,
              paddingHorizontal: 14, paddingVertical: 9, borderRadius: 10,
              backgroundColor: pressed ? T.green + 'cc' : T.green,
            })}
          >
            <Feather name="lock" size={14} color="#fff" />
            <Text style={{ fontSize: 12, fontFamily: 'Inter_700Bold', color: '#fff' }}>Seal Package</Text>
          </Pressable>
        )}
      </View>

      {/* Verify result message */}
      {verifyResult && (
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8, padding: 10, borderRadius: 10, borderWidth: 1, borderColor: verifyResult.ok ? T.green + '44' : T.rust + '44', backgroundColor: verifyResult.ok ? T.greenSoft : T.rustSoft, marginBottom: 12 }}>
          <Feather name={verifyResult.ok ? 'check-circle' : 'alert-triangle'} size={14} color={verifyResult.ok ? T.green : T.rust} style={{ marginTop: 1 }} />
          <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: verifyResult.ok ? T.green : T.rust, flex: 1 }}>
            {verifyResult.message}
          </Text>
        </View>
      )}

      {/* Seal form */}
      {showSealForm && (
        <View style={{ borderWidth: StyleSheet.hairlineWidth, borderColor: T.green + '55', borderRadius: 12, backgroundColor: T.greenSoft, padding: 14, gap: 10, marginBottom: 16 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Feather name="lock" size={15} color={T.green} />
            <Text style={{ fontSize: 14, fontFamily: 'Inter_700Bold', color: T.green }}>Seal the Origination Package</Text>
          </View>
          <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, lineHeight: 18 }}>
            Sealing locks all ten gates, computes the manifest hash, and marks this Work READY_FOR_B0. The ledger entry is tamper-evident and append-only.
          </Text>
          <TextInput
            style={{ borderWidth: 1, borderColor: colors.border, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground, backgroundColor: colors.background }}
            placeholder="Author sign-off (required)"
            placeholderTextColor={colors.mutedForeground}
            value={sealAuthor}
            onChangeText={setSealAuthor}
          />
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable onPress={() => { setShowSealForm(false); setSealAuthor(''); }} style={({ pressed }) => ({ flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 8, borderWidth: 1, borderColor: colors.border, opacity: pressed ? 0.7 : 1 })}>
              <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.mutedForeground }}>Cancel</Text>
            </Pressable>
            <Pressable
              onPress={handleSeal}
              disabled={sealing || !sealAuthor.trim()}
              style={({ pressed }) => ({ flex: 2, alignItems: 'center', flexDirection: 'row', justifyContent: 'center', gap: 6, paddingVertical: 10, borderRadius: 8, backgroundColor: !sealAuthor.trim() || sealing ? T.green + '55' : T.green, opacity: pressed ? 0.8 : 1 })}
            >
              {sealing ? <ActivityIndicator size="small" color="#fff" /> : <Feather name="lock" size={14} color="#fff" />}
              <Text style={{ fontSize: 13, fontFamily: 'Inter_700Bold', color: '#fff' }}>{sealing ? 'Sealing…' : 'Seal Package'}</Text>
            </Pressable>
          </View>
        </View>
      )}

      {/* ── Gate list ───────────────────────────────────────────────────────── */}
      <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 10 }}>
        Gates
      </Text>
      {book.stages.map((stage) => (
        <GenesisGateRow
          key={stage.code}
          stage={stage}
          workId={workId}
          colors={colors}
          onRefresh={fetchBook}
        />
      ))}
    </ScrollView>
  );
}

// ─── Intelligence Tab ────────────────────────────────────────────────────────
function IntelligenceTab({ workId, onHighGapCount }: { workId: string; onHighGapCount?: (n: number) => void }) {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [completeness, setCompleteness] = useState<any>(null);
  const [gaps, setGaps] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setFetchError(false);
    try {
      const [c, g, s] = await Promise.all([
        mobileFetch(`/api/works/${workId}/completeness`).then(r => r.ok ? r.json() : null).catch(() => null),
        mobileFetch(`/api/works/${workId}/gaps`).then(r => r.ok ? r.json() : null).catch(() => null),
        mobileFetch(`/api/works/${workId}/stats`).then(r => r.ok ? r.json() : null).catch(() => null),
      ]);
      setCompleteness(c);
      setGaps(g);
      setStats(s);
      // Surface high/critical gap count to parent for the tab badge
      const urgentCount = (g?.gaps ?? []).filter(
        (gap: any) => gap.severity === 'critical' || gap.severity === 'high',
      ).length;
      onHighGapCount?.(urgentCount);
    } catch { setFetchError(true); }
    finally { setLoading(false); }
  }, [workId, onHighGapCount]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <View style={{ flex: 1, paddingTop: 8 }}>
        {[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}
      </View>
    );
  }
  if (fetchError) {
    return <ErrorScreen message="Could not load intelligence" detail="Check your connection and try again." onRetry={fetchData} />;
  }

  const dims: any[] = completeness?.dimensions ?? [];
  const gapList: any[] = gaps?.gaps ?? [];
  const critGaps = gapList.filter(g => g.severity === 'critical');
  const highGaps = gapList.filter(g => g.severity === 'high');
  const medGaps  = gapList.filter(g => g.severity === 'medium');
  const overallScore = completeness?.overall ?? 0;
  const coveragePct  = gaps?.coverage_pct ?? 0;

  return (
    <ScrollView
      contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 24, paddingTop: 12 }}
      refreshControl={<RefreshControl refreshing={false} onRefresh={fetchData} tintColor={colors.primary} />}
    >
      {/* Stats strip */}
      {stats && (
        <View style={{ flexDirection: 'row', gap: 10, marginBottom: 14 }}>
          {[
            { label: 'Docs',      value: stats.document_count  ?? 0 },
            { label: 'Knowledge', value: stats.knowledge_count ?? 0 },
            { label: 'Tasks',     value: stats.pending_task_count ?? 0 },
          ].map(s => (
            <View key={s.label} style={{ flex: 1, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 10, alignItems: 'center', gap: 2 }}>
              <Text style={{ fontSize: 17, fontFamily: 'Inter_700Bold', color: colors.foreground }}>{s.value}</Text>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>{s.label}</Text>
            </View>
          ))}
        </View>
      )}

      {/* Completeness card */}
      {completeness && (
        <View style={{ backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 14, marginBottom: 14 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <Feather name="check-circle" size={13} color={colors.primary} />
            <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>Completeness</Text>
            <Text style={{ fontSize: 13, fontFamily: 'Inter_700Bold', color: colors.primary, marginLeft: 'auto' as any }}>{overallScore}%</Text>
          </View>
          <View style={{ height: 6, backgroundColor: colors.muted, borderRadius: 3, marginBottom: 10 }}>
            <View style={{ height: 6, width: `${overallScore}%` as any, backgroundColor: colors.primary, borderRadius: 3 }} />
          </View>
          {dims.map((d: any) => (
            <View key={d.name} style={{ marginBottom: 6 }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 2 }}>
                <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>{d.label ?? d.name}</Text>
                <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>{d.score}%</Text>
              </View>
              <View style={{ height: 4, backgroundColor: colors.muted, borderRadius: 2 }}>
                <View style={{ height: 4, width: `${d.score}%` as any, backgroundColor: colors.primary + 'aa', borderRadius: 2 }} />
              </View>
            </View>
          ))}
          {completeness.summary ? (
            <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 8 }}>{completeness.summary}</Text>
          ) : null}
        </View>
      )}

      {/* Research gaps card */}
      {gaps && (
        <View style={{ backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 14, marginBottom: 14 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <Feather name="alert-circle" size={13} color={colors.primary} />
            <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>Research Gaps</Text>
            <Text style={{ fontSize: 13, fontFamily: 'Inter_700Bold', color: colors.primary, marginLeft: 'auto' as any }}>{coveragePct}% covered</Text>
          </View>
          {gapList.length === 0 ? (
            <Text style={{ fontSize: 13, color: colors.mutedForeground }}>No gaps detected — great coverage!</Text>
          ) : (
            [{ label: 'CRITICAL', items: critGaps, color: T.rust }, { label: 'HIGH', items: highGaps, color: T.rust }, { label: 'MEDIUM', items: medGaps, color: T.gilt }]
              .filter(grp => grp.items.length > 0)
              .map(grp => (
                <View key={grp.label} style={{ marginBottom: 10 }}>
                  <Text style={{ fontSize: 10, fontFamily: 'Inter_700Bold', color: grp.color, letterSpacing: 0.5, marginBottom: 4 }}>{grp.label}</Text>
                  {grp.items.map((gap: any, i: number) => (
                    <View key={i} style={{ paddingVertical: 6, borderTopWidth: i > 0 ? StyleSheet.hairlineWidth : 0, borderTopColor: colors.border }}>
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }}>{gap.title}</Text>
                      {gap.description ? <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 2 }}>{gap.description}</Text> : null}
                    </View>
                  ))}
                </View>
              ))
          )}
          {(gaps.suggested_queries ?? []).length > 0 && (
            <View style={{ marginTop: 6 }}>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, marginBottom: 6 }}>SUGGESTED RESEARCH</Text>
              {(gaps.suggested_queries as string[]).slice(0, 3).map((q, i) => (
                <Text key={i} style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.primary, marginBottom: 3 }}>→ {q}</Text>
              ))}
            </View>
          )}
        </View>
      )}

      {/* Graph shortcut */}
      <Pressable
        onPress={() => router.push(`/graph?work_id=${workId}` as any)}
        style={({ pressed }) => ({
          flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14,
          backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border,
          borderRadius: 10, opacity: pressed ? 0.7 : 1,
        })}
      >
        <Feather name="share-2" size={15} color={colors.primary} />
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 14, fontFamily: 'Inter_600SemiBold', color: colors.foreground }}>Knowledge Graph</Text>
          <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>Explore entity relationships for this Work</Text>
        </View>
        <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
      </Pressable>
    </ScrollView>
  );
}

export default function WorkDetailScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const { id, tab: tabParam, q: qParam } = useLocalSearchParams<{ id: string; tab?: string; q?: string }>();
  const navigation = useNavigation();
  const router = useRouter();
  const validTabs: Tab[] = ['overview','docs','knowledge','tasks','conversations','learn','gaps','book','brainstorm','intelligence'];
  const initTab: Tab = validTabs.includes(tabParam as Tab) ? (tabParam as Tab) : 'overview';
  const [activeTab, setActiveTab] = useState<Tab>(initTab);

  // Animate the dot indicator width transition when switching tabs
  const handleTabSelect = useCallback((newTab: Tab) => {
    if (Platform.OS !== 'web') {
      LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    }
    setActiveTab(newTab);
  }, []);
  // Badge count for the Intelligence tab — updated when the tab loads its gap data
  const [intelHighGaps, setIntelHighGaps] = useState(0);
  const [newTaskText, setNewTaskText] = useState('');
  const [newTaskPriority, setNewTaskPriority] = useState(0);
  const [addingTask, setAddingTask] = useState(false);
  // Brainstorm seed — set when user taps "Brainstorm" on a gap card
  const [brainstormSeed, setBrainstormSeed] = useState<string>(qParam ?? '');
  const [brainstormContext, setBrainstormContext] = useState<string>('general');

  // ── Book / Pipeline tab state ──────────────────────────────────────────────
  const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
  const [pipeline, setPipeline] = useState<any>(null);
  const [pipelineLoading, setPipelineLoading] = useState(false);
  const [advancingPipeline, setAdvancingPipeline] = useState(false);
  const [pipelineToast, setPipelineToast] = useState(false);
  const [chapters, setChapters] = useState<any[]>([]);
  const [chaptersLoading, setChaptersLoading] = useState(false);

  const fetchPipeline = useCallback(async () => {
    if (!id) return;
    setPipelineLoading(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/pipeline`);
      if (res.ok) setPipeline(await res.json());
      else if (res.status === 404) setPipeline(null);
    } catch { /* non-fatal */ }
    finally { setPipelineLoading(false); }
  }, [id, domain]);

  const startPipeline = async () => {
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/pipeline`, { method: 'POST' });
      if (res.ok) {
        await fetchPipeline();
        // Keep the Books tab in sync — invalidate both the books list and the
        // all-works list so the newly promoted work appears immediately and is
        // removed from "Other Works" without waiting for the 60 s cache to expire.
        queryClient.invalidateQueries({ queryKey: ['mobile', 'books'] });
        queryClient.invalidateQueries({ queryKey: ['mobile', 'works-all'] });
        setActiveTab('book');
        setPipelineToast(true);
        setTimeout(() => setPipelineToast(false), 3000);
      } else {
        const json = await res.json().catch(() => ({}));
        Alert.alert('Error', json.detail ?? 'Could not start pipeline');
      }
    } catch { Alert.alert('Error', 'Could not start pipeline'); }
  };

  const advancePipeline = async () => {
    setAdvancingPipeline(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/pipeline/advance`, { method: 'POST' });
      if (res.ok) { fetchPipeline(); }
      else {
        const json = await res.json().catch(() => ({}));
        Alert.alert('Cannot advance', json.detail ?? 'Open blockers must be resolved first.');
      }
    } catch { Alert.alert('Error', 'Could not advance pipeline'); }
    finally { setAdvancingPipeline(false); }
  };

  const fetchChapters = useCallback(async () => {
    if (!id) return;
    setChaptersLoading(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/chapters`);
      if (res.ok) {
        const json = await res.json();
        // Flatten: [{doc_title, chapters:[...]}] → flat list annotated with doc_title
        const flat: any[] = [];
        for (const doc of json.documents ?? []) {
          for (const ch of doc.chapters ?? []) {
            flat.push({ ...ch, doc_title: doc.doc_title });
          }
        }
        setChapters(flat);
      }
    } catch { /* non-fatal */ }
    finally { setChaptersLoading(false); }
  }, [id, domain]);

  const [bookIntel, setBookIntel] = useState<any>(null);
  const [bookIntelLoading, setBookIntelLoading] = useState(false);

  // Work-scoped review items — badge on Overview tab + bottom sheet
  const [reviewItems, setReviewItems] = useState<any[]>([]);
  const [reviewSheetOpen, setReviewSheetOpen] = useState(false);
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    mobileFetch(`/api/review/queue`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.items) setReviewItems(d.items.filter((it: any) => it.work_id === id));
      })
      .catch(() => {});
  }, [id]);

  // Eagerly fetch gap count so the Intelligence badge appears before the tab is visited.
  useEffect(() => {
    if (!id) return;
    mobileFetch(`https://${domain}/api/works/${id}/gaps`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        const urgent = (d.gaps ?? []).filter(
          (g: any) => g.severity === 'critical' || g.severity === 'high',
        ).length;
        setIntelHighGaps(urgent);
      })
      .catch(() => {});
  }, [id, domain]);

  const resolveReviewItem = async (itemId: string, decision: 'approve' | 'reject' | 'defer') => {
    setResolvingId(itemId);
    try {
      await mobileFetch(`/api/review/${itemId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, reason: '' }),
      });
      setReviewItems(prev => prev.filter(it => it.id !== itemId));
    } catch { /* non-fatal */ }
    finally { setResolvingId(null); }
  };

  const fetchBookIntel = useCallback(async () => {
    if (!id) return;
    setBookIntelLoading(true);
    try {
      const res = await mobileFetch(`https://${domain}/api/works/${id}/book-intelligence`);
      if (res.ok) setBookIntel(await res.json());
      else setBookIntel(null);
    } catch { /* non-fatal */ }
    finally { setBookIntelLoading(false); }
  }, [id, domain]);

  // Fetch book intel on mount so the Overview mini-card shows data immediately,
  // without requiring the user to visit the Book tab first.
  useEffect(() => {
    fetchBookIntel();
  }, [fetchBookIntel]);

  // Poll bookIntel every 10 s while the pipeline is in a non-terminal state so
  // word-count and chapter progress bars update live as chapters are extracted.
  // `pipeline.next_status` being truthy means there are still stages ahead;
  // when it is falsy (B17 / no further stages) polling stops automatically.
  const pipelineActive = !!(pipeline && pipeline.next_status);
  useEffect(() => {
    if (!pipelineActive) return;
    const iv = setInterval(fetchBookIntel, 10_000);
    return () => clearInterval(iv);
  }, [pipelineActive, fetchBookIntel]);

  // Eagerly fetch pipeline on mount so the Overview CTA knows whether one exists.
  useEffect(() => { if (id) fetchPipeline(); }, [id, fetchPipeline]);

  useEffect(() => {
    if (activeTab === 'book') { fetchPipeline(); fetchChapters(); fetchBookIntel(); }
  }, [activeTab, fetchPipeline, fetchChapters, fetchBookIntel]);
  const queryClient = useQueryClient();
  const { mutateAsync: createTask } = useCreateWorkTask();

  const { data: workData, isError: workError, error: workFetchError, refetch: refetchWork } = useGetWork(id, { query: { staleTime: 30_000 } } as any);
  const { data: docsData, isLoading: docsLoading, isError: docsError, refetch: refetchDocs } = useGetWorkDocuments(id, { query: { staleTime: 20_000, refetchInterval: (q: any) => (q.state.data?.documents ?? []).some((d: any) => d.readiness === 'imported') ? 4_000 : false } } as any);
  const { data: knData, isLoading: knLoading, isError: knError, refetch: refetchKn } = useGetWorkKnowledge(id, { query: { staleTime: 30_000 } } as any);

  // ── Knowledge offline cache ───────────────────────────────────────────────
  const [cachedKn, setCachedKn] = useState<any[]>([]);
  useEffect(() => {
    if (knData?.knowledge?.length) {
      writeCache(`work:${id}:knowledge`, knData.knowledge);
      setCachedKn(knData.knowledge);
    }
  }, [knData?.knowledge, id]);
  useEffect(() => {
    if (knError && cachedKn.length === 0) {
      readCache<any[]>(`work:${id}:knowledge`).then(entry => {
        if (entry?.data?.length) setCachedKn(entry.data);
      });
    }
  }, [knError, id]);
  const { data: tasksData, isLoading: tasksLoading, isError: tasksError, refetch: refetchTasks } = useGetWorkTasks(id, { query: { staleTime: 30_000 } } as any);
  const { data: convsData, isLoading: convsLoading, isError: convsError, refetch: refetchConvs } = useListConversations(
    { work_id: id, limit: 50 } as any,
    { query: { staleTime: 20_000, refetchInterval: 30_000 } } as any,
  );

  const { mutateAsync: createConversation, isPending: startingConvo } = useCreateConversation();

  const work = workData?.work;

  useEffect(() => {
    if (work?.title) {
      navigation.setOptions({ title: work.title });
    }
  }, [work?.title, navigation]);

  // Work title inline editing
  const [editingWorkTitle, setEditingWorkTitle] = useState(false);
  const [workTitleDraft, setWorkTitleDraft] = useState('');
  const { mutate: updateWorkTitle } = useUpdateWork();

  const saveWorkTitle = () => {
    setEditingWorkTitle(false);
    const trimmed = workTitleDraft.trim();
    if (!trimmed || trimmed === work?.title) return;
    updateWorkTitle({ workId: id, data: { title: trimmed, description: (work as any)?.description ?? null } }, {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: [id] }),
    });
  };

  // Tasks search state
  const [taskSearch, setTaskSearch] = useState('');

  // Knowledge search + kind filter
  const [knSearch, setKnSearch] = useState('');
  const [knKindFilter, setKnKindFilter] = useState<'all' | 'entity' | 'claim' | 'relationship' | 'summary'>('all');

  // Conversations search
  const [convSearch, setConvSearch] = useState('');

  // Conversations archive — deferred commit (server call only fires after the undo window closes)
  // This eliminates archive/unarchive races: Undo simply cancels the pending timer, no server call needed.
  const [hiddenConvIds, setHiddenConvIds] = useState<Set<string>>(new Set());
  const [undoConv, setUndoConv] = useState<{ id: string; title: string } | null>(null);
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingArchiveRef = useRef<{ id: string } | null>(null);
  const { mutateAsync: archiveConvMutation } = useUpdateConversation();

  // Called when the undo window expires (or a second swipe preempts the first).
  const commitArchive = useCallback(async (convId: string) => {
    try {
      await archiveConvMutation({ convId, data: { archived: true } });
      queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
    } catch {
      // Server failed after the undo window — restore the row and inform the user.
      setHiddenConvIds(prev => { const n = new Set(prev); n.delete(convId); return n; });
      Alert.alert('Archive failed', 'Could not archive the conversation. It has been restored.');
    }
  }, [archiveConvMutation, queryClient]);

  const handleArchiveConv = useCallback((convId: string, title: string) => {
    // If there is already a pending archive, commit it immediately before starting a new one.
    if (undoTimerRef.current && pendingArchiveRef.current) {
      const prev = pendingArchiveRef.current;
      clearTimeout(undoTimerRef.current);
      undoTimerRef.current = null;
      pendingArchiveRef.current = null;
      void commitArchive(prev.id);
    }
    // Optimistically hide the row.
    setHiddenConvIds(prev => new Set([...prev, convId]));
    // Record the pending archive (server call deferred until window closes).
    pendingArchiveRef.current = { id: convId };
    setUndoConv({ id: convId, title });
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    // Commit after 4 s unless Undo is pressed first.
    undoTimerRef.current = setTimeout(() => {
      undoTimerRef.current = null;
      const pending = pendingArchiveRef.current;
      pendingArchiveRef.current = null;
      setUndoConv(null);
      if (pending) void commitArchive(pending.id);
    }, 4_000);
  }, [commitArchive]);

  const handleUndoArchive = useCallback(() => {
    // Cancel the pending timer — no server call is needed at all.
    if (undoTimerRef.current) { clearTimeout(undoTimerRef.current); undoTimerRef.current = null; }
    const pending = pendingArchiveRef.current;
    pendingArchiveRef.current = null;
    setUndoConv(null);
    if (pending) {
      setHiddenConvIds(prev => { const n = new Set(prev); n.delete(pending.id); return n; });
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    }
  }, []);

  // Docs sort
  const [docSortKey, setDocSortKey] = useState<'date' | 'name' | 'kind'>('date');

  // Task #13 — start a conversation linked to this work
  const handleStartDiscussion = async () => {
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    try {
      const result = await createConversation({
        data: { title: work?.title ? `Discussion: ${work.title}` : 'New Discussion', work_id: id },
      });
      const convoId = result?.conversation?.id;
      if (convoId) {
        router.push(`/chat/${convoId}`);
      }
    } catch {
      Alert.alert(
        'Could not start discussion',
        'Make sure the Orivellum server is running and try again.',
        [{ text: 'OK' }]
      );
    }
  };

  // Toggle task status between pending/completed.
  const handleToggleTask = async (taskId: string, currentStatus: string | undefined) => {
    const next = (currentStatus === 'done' || currentStatus === 'complete' || currentStatus === 'completed') ? 'pending' : 'completed';
    try {
      await mobileFetch(`https://${domain}/api/works/${id}/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: next }),
      });
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(id) });
      refetchTasks();
    } catch {
      Alert.alert('Error', 'Could not update task');
    }
  };

  // Delete a knowledge item by id (called from KnowledgeRow long-press).
  const handleDeleteKnowledge = async (itemId: string) => {
    try {
      await mobileFetch(`https://${domain}/api/knowledge/${itemId}`, { method: 'DELETE' });
      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(id) });
      refetchKn();
    } catch {
      Alert.alert('Error', 'Could not delete knowledge item');
    }
  };

  // Delete a task by id (called from TaskRow long-press).
  const handleDeleteTask = async (taskId: string) => {
    try {
      await mobileFetch(`https://${domain}/api/works/${id}/tasks/${taskId}`, { method: 'DELETE' });
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(id) });
      refetchTasks();
    } catch {
      Alert.alert('Error', 'Could not delete task');
    }
  };

  // Add Task from Gap: create a Work task pre-filled with the gap title.
  const handleCreateTaskFromGap = async (taskText: string) => {
    try {
      await createTask({ workId: id, data: { text: taskText } });
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
    } catch {
      Alert.alert('Error', 'Could not create task');
    }
  };

  // Brainstorm → : switch to Ideas tab with the gap pre-filled as seed.
  const handleBrainstormGap = (gapTitle: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setBrainstormSeed(gapTitle);
    setBrainstormContext('research_planning');
    setActiveTab('brainstorm');
  };

  // Research → : open a work-linked conversation pre-seeded with the gap title.
  const handleResearchGap = async (gapTitle: string) => {
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    try {
      const result = await createConversation({
        data: {
          title: gapTitle ? `Research: ${gapTitle}` : 'Research gap',
          work_id: id,
        },
      });
      const convoId = result?.conversation?.id;
      if (convoId) {
        const draft = gapTitle
          ? `Help me research this gap: ${gapTitle}`
          : undefined;
        router.push({
          pathname: '/chat/[id]',
          params: draft ? { id: convoId, draft } : { id: convoId },
        } as any);
      }
    } catch {
      Alert.alert(
        'Could not start research',
        'Make sure the Orivellum server is running and try again.',
        [{ text: 'OK' }]
      );
    }
  };

  const handleAddTask = async () => {
    const trimmed = newTaskText.trim();
    if (!trimmed) return;
    setAddingTask(true);
    try {
      await createTask({ workId: id, data: { text: trimmed, priority: newTaskPriority || undefined } });
      setNewTaskText('');
      setNewTaskPriority(0);
      await refetchTasks();
      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(id) });
    } catch {
      Alert.alert('Could not add task', 'Check your connection and try again.', [{ text: 'OK' }]);
    } finally {
      setAddingTask(false);
    }
  };

  const topPad = isWeb ? 67 : insets.top + 44;

  const docs = docsData?.documents ?? [];
  const knowledge = knData?.knowledge ?? cachedKn;
  const tasks = tasksData?.tasks ?? [];

  const renderTabContent = () => {
    switch (activeTab) {
      case 'overview':
        return (
          <OverviewTab
            workId={id}
            onStartDiscussion={handleStartDiscussion}
            starting={startingConvo}
            onNavigateToTab={setActiveTab}
            bookIntel={bookIntel}
            onOpenBook={() => setActiveTab('book')}
            onTargetsSaved={fetchBookIntel}
            pipeline={pipeline}
            pipelineLoading={pipelineLoading}
            onStartPipeline={startPipeline}
            onAdvancePipeline={advancePipeline}
            advancingPipeline={advancingPipeline}
          />
        );
      case 'docs':
        if (docsError && docs.length === 0) {
          return (
            <ErrorScreen
              message="Can't load documents"
              detail="Check your connection and try again."
              onRetry={refetchDocs}
            />
          );
        }
        const DOC_SORT_KEYS = ['date', 'name', 'kind'] as const;
        type DocSortKey = typeof DOC_SORT_KEYS[number];
        const sortedDocs = [...docs].sort((a: any, b: any) => {
          if (docSortKey === 'name') return (a.title ?? a.source ?? '').localeCompare(b.title ?? b.source ?? '');
          if (docSortKey === 'kind') return (a.kind ?? '').localeCompare(b.kind ?? '');
          return ((b as any).created_at ?? '').localeCompare((a as any).created_at ?? '');
        });
        return (
          <>
            {docsError && docs.length > 0 && (
              <OfflineBanner message="Showing cached documents — server unreachable" onRetry={refetchDocs} />
            )}
            {/* Sort control */}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, paddingVertical: 6, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border, backgroundColor: colors.background }}>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: colors.mutedForeground, marginRight: 2 }}>Sort</Text>
              {DOC_SORT_KEYS.map((k) => (
                <Pressable
                  key={k}
                  onPress={() => setDocSortKey(k)}
                  style={{
                    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10,
                    backgroundColor: docSortKey === k ? colors.primary : colors.muted,
                    borderWidth: 1, borderColor: docSortKey === k ? colors.primary : colors.border,
                  }}
                >
                  <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: docSortKey === k ? colors.primaryForeground : colors.mutedForeground, textTransform: 'capitalize' }}>{k}</Text>
                </Pressable>
              ))}
            </View>
            <FlatList
              data={sortedDocs}
              keyExtractor={(d) => d.id ?? ''}
              renderItem={({ item }) => <DocItem doc={item} onReprocess={async (docId) => {
                try {
                  await mobileFetch(`https://${domain}/api/library/${docId}/reprocess`, { method: 'POST' });
                  refetchDocs();
                } catch { /* non-fatal */ }
              }} />}
              contentContainerStyle={[styles.listPad, { paddingBottom: insets.bottom + 24 }]}
              refreshControl={
                <RefreshControl refreshing={docsLoading} onRefresh={refetchDocs} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                docsLoading
                  ? <>{[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}</>
                  : <EmptyState icon="file-text" title="No documents" body="Link documents to this Work to get started." />
              }
            />
          </>
        );
      case 'knowledge':
        if (knError && knowledge.length === 0) {
          return (
            <ErrorScreen
              message="Can't load knowledge"
              detail="Check your connection and try again."
              onRetry={refetchKn}
            />
          );
        }
        return (
          <>
            {knError && knowledge.length > 0 && (
              <OfflineBanner message="Showing cached knowledge — server unreachable" onRetry={refetchKn} />
            )}
            {/* Search + kind filter */}
            <View style={{ paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border, gap: 6, backgroundColor: colors.background }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Feather name="search" size={13} color={colors.mutedForeground} />
                <TextInput
                  style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground }}
                  placeholder="Search knowledge…"
                  placeholderTextColor={colors.mutedForeground}
                  value={knSearch}
                  onChangeText={setKnSearch}
                />
                {knSearch.length > 0 && (
                  <Pressable onPress={() => setKnSearch('')} hitSlop={8}>
                    <Feather name="x" size={13} color={colors.mutedForeground} />
                  </Pressable>
                )}
              </View>
              <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
                {(['all', 'entity', 'claim', 'relationship', 'summary'] as const).map((k) => (
                  <Pressable
                    key={k}
                    onPress={() => setKnKindFilter(k)}
                    style={{
                      paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10,
                      backgroundColor: knKindFilter === k ? colors.primary : colors.muted,
                      borderWidth: 1,
                      borderColor: knKindFilter === k ? colors.primary : colors.border,
                    }}
                  >
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_600SemiBold', color: knKindFilter === k ? colors.primaryForeground : colors.mutedForeground, textTransform: 'capitalize' }}>{k}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
            <FlatList
              data={knowledge.filter((k: any) => {
                const matchesKind = knKindFilter === 'all' || k.kind === knKindFilter;
                const matchesSearch = !knSearch.trim() || (k.text ?? '').toLowerCase().includes(knSearch.toLowerCase());
                return matchesKind && matchesSearch;
              })}
              keyExtractor={(k) => k.id ?? ''}
              renderItem={({ item }) => (
                <KnowledgeRow
                  item={item}
                  onReviewed={refetchKn}
                  onDelete={() => handleDeleteKnowledge((item as any).id)}
                />
              )}
              contentContainerStyle={[styles.listPad, { paddingBottom: insets.bottom + 24 }]}
              refreshControl={
                <RefreshControl refreshing={knLoading} onRefresh={refetchKn} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                knLoading
                  ? <>{[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}</>
                  : <EmptyState
                      icon="cpu"
                      title={knSearch.trim() || knKindFilter !== 'all' ? 'No matching knowledge items' : 'No knowledge nodes'}
                      body={knSearch.trim() || knKindFilter !== 'all' ? 'Try adjusting your search or filters.' : 'Process documents to extract knowledge.'}
                    />
              }
            />
          </>
        );
      case 'tasks':
        if (tasksError && tasks.length === 0) {
          return (
            <ErrorScreen
              message="Can't load tasks"
              detail="Check your connection and try again."
              onRetry={refetchTasks}
            />
          );
        }
        return (
          <>
            {tasksError && tasks.length > 0 && (
              <OfflineBanner message="Showing cached tasks — server unreachable" onRetry={refetchTasks} />
            )}
            {/* Search tasks */}
            <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, paddingVertical: 6, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border, backgroundColor: colors.background, gap: 6 }}>
              <Feather name="search" size={13} color={colors.mutedForeground} />
              <TextInput
                style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground }}
                placeholder="Search tasks…"
                placeholderTextColor={colors.mutedForeground}
                value={taskSearch}
                onChangeText={setTaskSearch}
              />
              {taskSearch.length > 0 && (
                <Pressable onPress={() => setTaskSearch('')} hitSlop={8}>
                  <Feather name="x" size={13} color={colors.mutedForeground} />
                </Pressable>
              )}
            </View>
            {/* Add task input */}
            <View style={[styles.taskInputRow, { borderBottomColor: colors.border, backgroundColor: colors.background }]}>
              <TextInput
                style={[styles.taskInput, { backgroundColor: colors.card, borderColor: colors.border, color: colors.foreground }]}
                placeholder="Add a task…"
                placeholderTextColor={colors.mutedForeground}
                value={newTaskText}
                onChangeText={setNewTaskText}
                onSubmitEditing={handleAddTask}
                returnKeyType="done"
                editable={!addingTask}
              />
              <Pressable
                onPress={handleAddTask}
                disabled={!newTaskText.trim() || addingTask}
                style={[styles.taskAddBtn, { backgroundColor: newTaskText.trim() && !addingTask ? colors.primary : colors.muted }]}
              >
                {addingTask
                  ? <ActivityIndicator size="small" color={colors.primaryForeground} />
                  : <Feather name="plus" size={18} color={newTaskText.trim() ? colors.primaryForeground : colors.mutedForeground} />
                }
              </Pressable>
            </View>
            {/* Priority picker */}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.border }}>
              <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: colors.mutedForeground, marginRight: 4 }}>Priority</Text>
              {([0, 1, 2, 3] as const).map((p) => {
                const labels = ['None', 'P3', 'P2', 'P1'];
                const active = newTaskPriority === p;
                return (
                  <Pressable
                    key={p}
                    onPress={() => setNewTaskPriority(p)}
                    style={{
                      paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12,
                      backgroundColor: active ? colors.primary : colors.muted,
                      borderWidth: 1,
                      borderColor: active ? colors.primary : colors.border,
                    }}
                  >
                    <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: active ? colors.primaryForeground : colors.mutedForeground }}>
                      {labels[p]}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            <FlatList
              data={taskSearch.trim() ? tasks.filter((t: any) => (t.text ?? '').toLowerCase().includes(taskSearch.toLowerCase())) : tasks}
              keyExtractor={(t) => t.id ?? ''}
              renderItem={({ item }) => (
                <TaskRow
                  task={item}
                  onDelete={() => handleDeleteTask((item as any).id)}
                  onToggle={() => handleToggleTask((item as any).id, (item as any).status)}
                />
              )}
              contentContainerStyle={[styles.listPad, { paddingBottom: insets.bottom + 24 }]}
              refreshControl={
                <RefreshControl refreshing={tasksLoading} onRefresh={refetchTasks} tintColor={colors.primary} />
              }
              ListEmptyComponent={
                tasksLoading
                  ? <>{[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}</>
                  : <EmptyState
                      icon="check-square"
                      title={taskSearch.trim() ? `No tasks matching "${taskSearch}"` : 'No tasks yet'}
                      body={taskSearch.trim() ? 'Try a different search term.' : 'Add a task using the input above.'}
                    />
              }
            />
          </>
        );
      case 'gaps':
        return <GapsTab workId={id} colors={colors} onResearch={handleResearchGap} onCreateTask={handleCreateTaskFromGap} onBrainstorm={handleBrainstormGap} pipelineActive={pipelineActive} />;
      case 'completeness':
        return <CompletenessTab workId={id} pipelineActive={pipelineActive} />;
      case 'learn':
        return <MobileLearnTab workId={id} colors={colors} />;
      case 'book':
        return (
          <View style={{ flex: 1 }}>
            {/* ── Book Intelligence ──────────────────────────────── */}
            <BookIntelTab
              bookIntel={bookIntel}
              loading={bookIntelLoading}
              colors={colors}
              onDiscuss={handleResearchGap}
              chapters={chapters}
              chaptersLoading={chaptersLoading}
              workId={id}
            />

            {/* ── Pipeline section (collapsible footer) ─────────── */}
            {(pipeline || pipelineLoading) && (
              <View style={{
                borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border,
                backgroundColor: colors.muted + '28',
              }}>
                <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  contentContainerStyle={{ paddingHorizontal: 16, paddingVertical: 10, gap: 8, flexDirection: 'row', alignItems: 'center' }}
                >
                  {pipelineLoading ? (
                    <ActivityIndicator size="small" color={colors.primary} />
                  ) : pipeline ? (
                    <>
                      {/* Stage badge */}
                      <View style={{
                        paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6,
                        backgroundColor: colors.primary + '18', borderWidth: 1, borderColor: colors.primary + '44',
                      }}>
                        <Text style={{ fontSize: 12, fontFamily: 'Inter_700Bold', color: colors.primary }}>
                          {pipeline.status ?? 'B0'}
                        </Text>
                      </View>
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.foreground }}>
                        {pipeline.stage_label ?? pipeline.status}
                      </Text>
                      {pipeline.next_status && (
                        <Pressable
                          onPress={advancePipeline}
                          disabled={advancingPipeline}
                          style={({ pressed }) => ({
                            flexDirection: 'row', alignItems: 'center', gap: 6,
                            paddingHorizontal: 12, paddingVertical: 5, borderRadius: 6,
                            backgroundColor: pressed || advancingPipeline
                              ? colors.primary + 'aa' : colors.primary,
                          })}
                        >
                          {advancingPipeline
                            ? <ActivityIndicator size="small" color="#fff" />
                            : <Feather name="arrow-right" size={12} color="#fff" />}
                          <Text style={{ fontSize: 12, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>
                            {advancingPipeline ? 'Advancing…' : `→ ${pipeline.next_status}`}
                          </Text>
                        </Pressable>
                      )}
                    </>
                  ) : null}
                </ScrollView>
              </View>
            )}

            {/* Start pipeline CTA — shown whenever no pipeline exists */}
            {!pipeline && !pipelineLoading && (
              <View style={{ padding: 16 }}>
                <Pressable
                  onPress={startPipeline}
                  style={[styles.newChatBtn, { backgroundColor: colors.primary }]}
                >
                  <Feather name="play" size={14} color="#fff" />
                  <Text style={styles.newChatBtnText}>Start Pipeline</Text>
                </Pressable>
              </View>
            )}
          </View>
        );
      case 'conversations': {
        const convs = convsData?.conversations ?? [];
        if (convsError && convs.length === 0) {
          return (
            <ErrorScreen
              message="Can't load conversations"
              detail="Check your connection and try again."
              onRetry={refetchConvs}
            />
          );
        }
        const filteredConvs = (convSearch.trim()
          ? convs.filter((c: any) => (c.title ?? '').toLowerCase().includes(convSearch.toLowerCase()))
          : convs
        ).filter((c: any) => !hiddenConvIds.has(c.id));
        return (
          <>
            {convsError && convs.length > 0 && (
              <OfflineBanner message="Showing cached conversations" onRetry={refetchConvs} />
            )}
            {/* Search */}
            <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, paddingVertical: 6, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border, backgroundColor: colors.background, gap: 6 }}>
              <Feather name="search" size={13} color={colors.mutedForeground} />
              <TextInput
                style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_400Regular', color: colors.foreground }}
                placeholder="Search conversations…"
                placeholderTextColor={colors.mutedForeground}
                value={convSearch}
                onChangeText={setConvSearch}
              />
              {convSearch.length > 0 && (
                <Pressable onPress={() => setConvSearch('')} hitSlop={8}>
                  <Feather name="x" size={13} color={colors.mutedForeground} />
                </Pressable>
              )}
            </View>
            <FlatList
              data={filteredConvs}
              keyExtractor={(c) => (c as any).id ?? ''}
              renderItem={({ item: c }) => (
                <ConvSwipeRow
                  conv={c}
                  colors={colors}
                  onPress={() => router.push(`/chat/${(c as any).id}` as any)}
                  onArchive={handleArchiveConv}
                />
              )}
              contentContainerStyle={[styles.listPad, { paddingBottom: insets.bottom + 24 }]}
              refreshControl={
                <RefreshControl refreshing={convsLoading} onRefresh={refetchConvs} tintColor={colors.primary} />
              }
              ListHeaderComponent={
                <Pressable
                  onPress={handleStartDiscussion}
                  style={[styles.newChatBtn, { backgroundColor: colors.primary, borderColor: colors.primary }]}
                >
                  <Feather name="plus" size={14} color="#fff" />
                  <Text style={styles.newChatBtnText}>Start New Discussion</Text>
                </Pressable>
              }
              ListEmptyComponent={
                convsLoading
                  ? <>{[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}</>
                  : <EmptyState icon="message-circle" title="No conversations yet" body="Start a discussion to begin chatting about this Work." />
              }
            />
            {/* Undo archive toast */}
            {undoConv && (
              <View style={{
                position: 'absolute', bottom: 16, left: 16, right: 16,
                flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
                backgroundColor: '#1f2937', borderRadius: 12,
                paddingVertical: 12, paddingHorizontal: 16,
                shadowColor: '#000', shadowOffset: { width: 0, height: 4 },
                shadowOpacity: 0.3, shadowRadius: 8, elevation: 8,
              }}>
                <Text style={{ color: '#f9fafb', fontSize: 13, fontFamily: 'Inter_400Regular', flex: 1 }} numberOfLines={1}>
                  "{undoConv.title}" archived
                </Text>
                <Pressable
                  onPress={handleUndoArchive}
                  hitSlop={8}
                  style={({ pressed }: { pressed: boolean }) => ({
                    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8,
                    backgroundColor: T.gilt, opacity: pressed ? 0.8 : 1, marginLeft: 12,
                  })}
                >
                  <Text style={{ color: '#fff', fontSize: 13, fontFamily: 'Inter_700Bold' }}>Undo</Text>
                </Pressable>
              </View>
            )}
          </>
        );
      }
      case 'intelligence':
        return <IntelligenceTab workId={id} onHighGapCount={setIntelHighGaps} />;
      case 'brainstorm':
        return <BrainstormTab key={brainstormSeed} workId={id} colors={colors} initialSeed={brainstormSeed || qParam} initialContext={brainstormContext} />;
      case 'trailer':
        return <TrailerTab workId={id} colors={colors} />;
      case 'genesis':
        return <GenesisTab workId={id} colors={colors} />;
      case 'graph':
        return (
          <KnowledgeGraphView
            workId={id}
            onOpenFullGraph={() => router.push({ pathname: '/graph', params: { work_id: id, work_title: work?.title ?? '' } } as any)}
            onReprocess={() => {
              const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
              mobileFetch(`https://${domain}/api/library/reprocess-all`, { method: 'POST' })
                .catch(() => {});
              Alert.alert('Reprocessing', 'Reprocess triggered. The graph will refresh once documents are ready.');
            }}
          />
        );
    }
  };

  // Full-screen error when the work itself can't be loaded.
  // Distinguish a deleted/missing work (404) from a connection failure so the
  // message is accurate rather than suggesting a server problem for a stale link.
  if (workError && !work) {
    const isWorkNotFound = (workFetchError as any)?.message?.includes('HTTP 404');
    return (
      <View style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}>
        <ErrorScreen
          message={isWorkNotFound ? 'Work not found' : "Can't reach your workspace"}
          detail={
            isWorkNotFound
              ? 'This work may have been deleted.'
              : 'Check your connection and make sure the Orivellum server is running.'
          }
          onRetry={isWorkNotFound ? undefined : refetchWork}
        />
      </View>
    );
  }

  return (
    <View style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}>
      {/* Work title + type badge */}
      <View style={[styles.workHeader, { paddingHorizontal: 16, paddingBottom: 10 }]}>
        {editingWorkTitle ? (
          <TextInput
            autoFocus
            style={[styles.workTitle, { color: colors.foreground, borderBottomWidth: 2, borderBottomColor: colors.primary, marginBottom: 6 }]}
            value={workTitleDraft}
            onChangeText={setWorkTitleDraft}
            onBlur={saveWorkTitle}
            onSubmitEditing={saveWorkTitle}
            returnKeyType="done"
          />
        ) : (
          <Pressable
            onPress={() => { setWorkTitleDraft(work?.title ?? ''); setEditingWorkTitle(true); }}
            onLongPress={() => { setWorkTitleDraft(work?.title ?? ''); setEditingWorkTitle(true); }}
            delayLongPress={500}
          >
            <Text style={[styles.workTitle, { color: colors.foreground }]} numberOfLines={2}>
              {work?.title ?? ''}
            </Text>
          </Pressable>
        )}
        <View style={[styles.typeBadge, { backgroundColor: colors.muted }]}>
          <Text style={[styles.typeBadgeText, { color: colors.mutedForeground }]}>
            {work?.work_type ?? 'research'}
          </Text>
        </View>
      </View>

      <TabBar
        active={activeTab}
        onSelect={handleTabSelect}
        colors={colors}
        badges={{
          tasks: (tasksData?.tasks ?? []).filter((t: any) => t.status !== 'completed').length || undefined,
          conversations: (convsData?.conversations ?? []).length || undefined,
          overview: reviewItems.length || undefined,
          intelligence: intelHighGaps || undefined,
        }}
        onNavigateGraph={() =>
          router.push(`/graph?work_id=${id}&work_title=${encodeURIComponent(work?.title ?? '')}` as any)
        }
      />
      <TabDotIndicator tabs={TAB_ORDER} activeTab={activeTab} />

      {/* Review notification chip — shown under the tab bar when Overview is active */}
      {activeTab === 'overview' && reviewItems.length > 0 && (
        <Pressable
          onPress={() => setReviewSheetOpen(true)}
          style={{
            flexDirection: 'row', alignItems: 'center', gap: 6,
            marginHorizontal: 16, marginTop: 10,
            paddingHorizontal: 12, paddingVertical: 8,
            backgroundColor: colors.primary + '14',
            borderWidth: 1, borderColor: colors.primary + '44',
            borderRadius: 10,
          }}
        >
          <Feather name="shield" size={13} color={colors.primary} />
          <Text style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_500Medium', color: colors.primary }}>
            {reviewItems.length} item{reviewItems.length === 1 ? '' : 's'} need{reviewItems.length === 1 ? 's' : ''} review
          </Text>
          <Feather name="chevron-right" size={13} color={colors.primary} />
        </Pressable>
      )}

      <View style={{ flex: 1 }}>{renderTabContent()}</View>

      {/* Book pipeline started toast */}
      {pipelineToast && (
        <View style={{
          position: 'absolute', bottom: insets.bottom + 24, left: 16, right: 16,
          flexDirection: 'row', alignItems: 'center', gap: 8,
          backgroundColor: T.gilt, borderRadius: 12,
          paddingVertical: 12, paddingHorizontal: 16,
          shadowColor: '#000', shadowOffset: { width: 0, height: 4 },
          shadowOpacity: 0.25, shadowRadius: 8, elevation: 8,
        }}>
          <Feather name="book-open" size={15} color="#fff" />
          <Text style={{ flex: 1, fontSize: 13, fontFamily: 'Inter_500Medium', color: '#fff' }}>
            Book pipeline started — tracking begins at B0
          </Text>
        </View>
      )}

      {/* Floating quick-add task button — visible from all tabs except Tasks */}
      {activeTab !== 'tasks' && (
        <Pressable
          onPress={() => setActiveTab('tasks')}
          style={{
            position: 'absolute',
            bottom: insets.bottom + 20,
            right: 20,
            width: 50,
            height: 50,
            borderRadius: 25,
            backgroundColor: colors.primary,
            alignItems: 'center',
            justifyContent: 'center',
            shadowColor: '#000',
            shadowOffset: { width: 0, height: 2 },
            shadowOpacity: 0.25,
            shadowRadius: 4,
            elevation: 5,
          }}
        >
          <Feather name="plus" size={22} color="#fff" />
        </Pressable>
      )}

      {/* Review bottom sheet — work-scoped pending items */}
      <Modal
        visible={reviewSheetOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setReviewSheetOpen(false)}
      >
        <View style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.38)' }}>
          <Pressable style={{ flex: 1 }} onPress={() => setReviewSheetOpen(false)} />
          <View style={{
            backgroundColor: colors.card,
            borderTopLeftRadius: 16, borderTopRightRadius: 16,
            borderTopWidth: 1, borderColor: colors.border,
            paddingHorizontal: 18, paddingTop: 18,
            paddingBottom: insets.bottom + 24,
            maxHeight: '80%',
          }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 14 }}>
              <Feather name="shield" size={15} color={colors.primary} style={{ marginRight: 8 }} />
              <Text style={{ fontSize: 16, fontFamily: 'Inter_700Bold', color: colors.foreground, flex: 1 }}>
                Pending Reviews
              </Text>
              <Pressable onPress={() => setReviewSheetOpen(false)} hitSlop={8}>
                <Feather name="x" size={18} color={colors.mutedForeground} />
              </Pressable>
            </View>
            {reviewItems.length === 0 ? (
              <View style={{ alignItems: 'center', paddingVertical: 24 }}>
                <Feather name="check-circle" size={28} color={colors.mutedForeground} />
                <Text style={{ marginTop: 10, fontSize: 14, color: colors.mutedForeground, fontFamily: 'Inter_400Regular' }}>
                  All caught up!
                </Text>
              </View>
            ) : (
              <FlatList
                data={reviewItems}
                keyExtractor={it => it.id}
                ItemSeparatorComponent={() => <View style={{ height: 1, backgroundColor: colors.border }} />}
                renderItem={({ item }) => {
                  const isResolving = resolvingId === item.id;
                  const typeColor: Record<string, string> = { knowledge: T.gilt, reclassify: T.gilt, suggestion: '#3b82f6', duplicate: T.rust };
                  const tc = typeColor[item.item_type] ?? colors.primary;
                  return (
                    <View style={{ paddingVertical: 12 }}>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <View style={{ backgroundColor: tc + '18', borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2 }}>
                          <Text style={{ fontSize: 10, fontFamily: 'Inter_700Bold', color: tc, letterSpacing: 0.5 }}>{item.item_type.toUpperCase()}</Text>
                        </View>
                      </View>
                      <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.foreground, marginBottom: 2 }}>{item.title}</Text>
                      {item.description ? <Text style={{ fontSize: 12, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginBottom: 8 }}>{item.description}</Text> : null}
                      {isResolving ? (
                        <ActivityIndicator size="small" color={colors.primary} />
                      ) : (
                        <View style={{ flexDirection: 'row', gap: 8 }}>
                          {(['approve', 'reject', 'defer'] as const).map(d => (
                            <Pressable
                              key={d}
                              onPress={() => resolveReviewItem(item.id, d)}
                              style={({ pressed }) => ({
                                flex: 1, paddingVertical: 7, borderRadius: 8, alignItems: 'center',
                                backgroundColor: d === 'approve' ? colors.primary + '18' : d === 'reject' ? T.rustSoft : colors.muted,
                                borderWidth: 1,
                                borderColor: d === 'approve' ? colors.primary + '55' : d === 'reject' ? T.rust + '55' : colors.border,
                                opacity: pressed ? 0.7 : 1,
                                minHeight: 44,
                              })}
                            >
                              <Text style={{ fontSize: 12, ...font('semibold'), color: d === 'approve' ? colors.primary : d === 'reject' ? T.rust : colors.mutedForeground }}>
                                {d.charAt(0).toUpperCase() + d.slice(1)}
                              </Text>
                            </Pressable>
                          ))}
                        </View>
                      )}
                    </View>
                  );
                }}
              />
            )}
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  workHeader: {},
  workTitle: { fontSize: 22, ...fontSerif('bold'), marginBottom: 6 },
  typeBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 12,
  },
  typeBadgeText: { fontSize: 12, fontFamily: 'Inter_500Medium', textTransform: 'capitalize' },
  tabBar: { flexDirection: 'row', borderBottomWidth: 1 },
  tab: { flex: 1, alignItems: 'center', paddingVertical: 12, minHeight: 44, justifyContent: 'center' },
  tabLabel: { fontSize: 13, ...font('regular') },
  overviewPad: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 80 },
  taskInputRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1,
  },
  taskInput: {
    flex: 1, height: 38, borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 10, fontSize: 14, fontFamily: 'Inter_400Regular',
  },
  taskAddBtn: {
    width: 38, height: 38, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },
  listPad: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 32 },
  description: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 22, marginBottom: 20 },
  infoGrid: { borderWidth: 1, borderRadius: 6, overflow: 'hidden' },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderBottomWidth: 1,
  },
  infoLabel: { fontSize: 13, fontFamily: 'Inter_400Regular' },
  infoValue: { fontSize: 13, fontFamily: 'Inter_500Medium', textTransform: 'capitalize' },
  listItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    borderBottomWidth: 1,
    paddingVertical: 12,
  },
  itemIcon: {
    width: 32,
    height: 32,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
  },
  itemBody: { flex: 1 },
  itemTitle: { fontSize: 14, fontFamily: 'Inter_500Medium', lineHeight: 19 },
  itemMeta: { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 2 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 40 },
  emptyText: { fontSize: 14, fontFamily: 'Inter_400Regular' },
  newChatBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    marginHorizontal: 16,
    marginBottom: 12,
    marginTop: 4,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 44,
  },
  newChatBtnText: { fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#fff' },
  // Start Discussion button
  discussBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 24,
    paddingVertical: 14,
    borderRadius: 12,
    minHeight: 44,
  },
  discussBtnText: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
  retryBtn: {
    marginTop: 12,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statusBadge: {
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  statusText: {
    fontSize: 10,
    fontFamily: 'Inter_600SemiBold',
    textTransform: 'capitalize',
  },
});
