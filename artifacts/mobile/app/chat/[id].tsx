import React, { useRef, useState } from 'react';
import { mobileFetch } from '@/lib/api';
import {
  ActionSheetIOS,
  ActivityIndicator,
  Animated,
  Alert,
  FlatList,
  Image,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useColorScheme,
} from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';
import Markdown from 'react-native-markdown-display';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens, alpha } from '@/lib/tokens';
import { Feather } from '@expo/vector-icons';
import {
  useGetConversation,
  useGetSystemModels,
  useUpdateConversation,
  useGetWebSearchStatus,
  useGetWorkDocuments,
} from '@workspace/api-client-react';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useEffect } from 'react';
import * as Haptics from 'expo-haptics';
import * as Clipboard from 'expo-clipboard';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { Message } from '@workspace/api-client-react';
import { OfflineBanner } from '@/components/OfflineBanner';
import { readCache, writeCache } from '@/lib/cache';
import { queueMessage, flushMessageQueue, getOutboxForConversation } from '@/lib/offlineCache';
import { font } from '@/lib/typography';

const LAST_MODEL_KEY = 'orivellum:lastModel';

// `queued` marks a user message that was held in the offline outbox.
// `msgId` is the stable outbox idempotency key — used to reconcile which
// bubbles were actually delivered after a flush (avoids text-based matching
// which breaks for identical repeated messages).
type LocalMessage = Message & { isError?: boolean; localImageUri?: string; queued?: boolean; msgId?: string };

function MessageBubble({ message, colors, T, isDark, onResend, onRetry, highlighted }: { message: LocalMessage; colors: any; T: ReturnType<typeof useVellumTokens>; isDark: boolean; onResend?: () => void; onRetry?: () => void; highlighted?: boolean }) {
  const isUser = message.role === 'user';
  const isErr = (message as any).isError;
  const textColor = isUser ? colors.primaryForeground : isErr ? colors.mutedForeground : colors.foreground;
  const [copied, setCopied] = useState(false);

  // ── Highlight fade-out animation ────────────────────────────────────────────
  // When `highlighted` becomes true the opacity is immediately set to 1.
  // After a 1 400 ms hold (scroll completes + a comfortable read window) it
  // tweens to 0 over 600 ms so the transition feels smooth instead of abrupt.
  // The Animated.View is always in the tree; opacity = 0 when not highlighted.
  const highlightAnim = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!highlighted) {
      // Snap to invisible without animating (covers the case where state is
      // cleared after the animation has already finished).
      highlightAnim.stopAnimation();
      highlightAnim.setValue(0);
      return;
    }
    highlightAnim.setValue(1);
    const fadeTimer = setTimeout(() => {
      Animated.timing(highlightAnim, {
        toValue: 0,
        duration: 600,
        useNativeDriver: false, // backgroundColor requires JS driver
      }).start();
    }, 1400);
    return () => clearTimeout(fadeTimer);
  }, [highlighted]);

  const handleLongPress = async () => {
    if (!message.text) return;
    await Clipboard.setStringAsync(message.text);
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const markdownStyles = {
    body: { color: textColor, fontSize: 17, fontFamily: 'Inter_400Regular', lineHeight: 24 },
    paragraph: { marginTop: 0, marginBottom: 4 },
    strong: { fontFamily: 'Inter_700Bold' },
    em: { fontStyle: 'italic' as const },
    code_inline: {
      backgroundColor: isDark ? '#3f3f46' : '#f4f4f5',
      color: isDark ? '#d4d4d8' : '#3f3f46',
      fontFamily: 'Inter_400Regular',
      fontSize: 13,
      paddingHorizontal: 4,
      borderRadius: 3,
    },
    fence: {
      backgroundColor: isDark ? '#18181b' : '#27272a',
      borderRadius: 6,
      padding: 10,
      marginVertical: 4,
    },
    code_block: {
      backgroundColor: isDark ? '#18181b' : '#27272a',
      color: '#d4d4d8',
      fontFamily: 'Inter_400Regular',
      fontSize: 12,
      borderRadius: 6,
      padding: 10,
      marginVertical: 4,
    },
    bullet_list: { marginBottom: 4 },
    ordered_list: { marginBottom: 4 },
    list_item: { marginVertical: 1 },
    heading1: { fontSize: 17, fontFamily: 'Inter_700Bold', marginVertical: 4 },
    heading2: { fontSize: 15, fontFamily: 'Inter_700Bold', marginVertical: 3 },
    heading3: { fontSize: 14, fontFamily: 'Inter_600SemiBold', marginVertical: 2 },
    blockquote: {
      borderLeftWidth: 2,
      borderLeftColor: colors.border,
      paddingLeft: 10,
      marginVertical: 4,
    },
  };

  // Custom fence renderer: shows code + a copy button overlay
  const fenceRule = (node: any) => {
    const code: string = node.content ?? '';
    return (
      <View
        key={node.key}
        style={{
          backgroundColor: isDark ? '#18181b' : '#27272a',
          borderRadius: 6,
          padding: 10,
          marginVertical: 4,
          position: 'relative',
        }}
      >
        <Text style={{ color: '#d4d4d8', fontFamily: 'Inter_400Regular', fontSize: 12, lineHeight: 18 }}>
          {code.trimEnd()}
        </Text>
        <Pressable
          style={{ position: 'absolute', top: 6, right: 6, padding: 4, opacity: 0.7 }}
          onPress={async () => {
            await Clipboard.setStringAsync(code);
            await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          }}
          hitSlop={8}
        >
          <Feather name="copy" size={12} color="#a1a1aa" />
        </Pressable>
      </View>
    );
  };

  return (
    <Pressable
      onLongPress={handleLongPress}
      delayLongPress={400}
      style={[
        styles.bubbleRow,
        isUser ? styles.bubbleRight : styles.bubbleLeft,
        // Keep the layout expansion when highlighted so the background fills
        // the right area; the colour itself comes from the Animated.View below.
        highlighted && {
          borderRadius: 12,
          marginHorizontal: -4,
          paddingHorizontal: 4,
        },
      ]}
    >
      {/* Animated highlight background — always rendered, opacity tweens 1→0 */}
      <Animated.View
        pointerEvents="none"
        style={[
          StyleSheet.absoluteFillObject,
          {
            borderRadius: 12,
            backgroundColor: colors.primary + '18',
            opacity: highlightAnim,
          },
        ]}
      />
      {!isUser && (
        <View
          style={[
            styles.avatar,
            { backgroundColor: isErr ? colors.muted : colors.primary },
          ]}
        >
          <Feather
            name={isErr ? 'alert-circle' : 'cpu'}
            size={12}
            color={isErr ? colors.mutedForeground : colors.primaryForeground}
          />
        </View>
      )}
      <View style={{ flexShrink: 1, alignItems: isUser ? 'flex-end' : 'flex-start' }}>
        <View
          style={[
            styles.bubble,
            isUser
              ? { backgroundColor: colors.primary, borderBottomRightRadius: 2 }
              : isErr
              ? {
                  backgroundColor: colors.card,
                  borderColor: colors.border,
                  borderWidth: 1,
                  borderBottomLeftRadius: 2,
                  borderStyle: 'dashed' as const,
                }
              : { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderBottomLeftRadius: 2 },
            { maxWidth: '100%' },
          ]}
        >
          {isUser || isErr ? (
            <>
              {/* Attached image thumbnail (user messages only).
                  localImageUri → current-session optimistic message (full-res local URI).
                  meta.image_thumbnail_b64 → server-stored compact JPEG (persists across
                  sessions so history always shows the image, not just "[Image attached]"). */}
              {isUser && ((message as LocalMessage).localImageUri || (message as any).meta?.image_thumbnail_b64) ? (
                <Image
                  source={{
                    uri: (message as LocalMessage).localImageUri
                      ?? `data:image/jpeg;base64,${(message as any).meta.image_thumbnail_b64}`,
                  }}
                  style={{ width: 160, height: 160, borderRadius: 8, marginBottom: message.text && message.text !== '[Image attached]' ? 6 : 0 }}
                  resizeMode="cover"
                />
              ) : null}
              {/* Show text only when it's not the bare placeholder */}
              {(!isUser || (message.text && message.text !== '[Image attached]')) && (
                <Text
                  style={[
                    styles.bubbleText,
                    { color: textColor, fontStyle: isErr ? 'italic' : 'normal' },
                  ]}
                >
                  {/* Strip "[Image] " prefix for display */}
                  {isUser ? message.text?.replace(/^\[Image\] /, '') : message.text}
                </Text>
              )}
            </>
          ) : (
            <>
              {!!(message as any).meta?.thinking && (
                <ReasoningBlock text={(message as any).meta.thinking} colors={colors} />
              )}
              <Markdown style={markdownStyles as any} rules={{ fence: fenceRule }}>{message.text ?? ''}</Markdown>
            </>
          )}
        </View>
        {/* Context-limit truncation → Continue (calls /continue endpoint) */}
        {!isUser && !isErr && !!(message as any).meta?.cut_short && (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6 }}>
            <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, fontStyle: 'italic' }}>
              Response was cut short.
            </Text>
            {onResend && (
              <Pressable
                onPress={onResend}
                style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4, backgroundColor: colors.primary + '22', borderWidth: 1, borderColor: colors.primary + '44' }}
              >
                <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: colors.primary }}>Continue →</Text>
              </Pressable>
            )}
          </View>
        )}
        {/* Stream timeout / incomplete → Retry (re-sends the last user message) */}
        {!isUser && !isErr && !!(message as any).meta?.incomplete && (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6 }}>
            <Feather name="alert-triangle" size={10} color={colors.mutedForeground} style={{ opacity: 0.7 }} />
            <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, fontStyle: 'italic' }}>
              Reply timed out.
            </Text>
            {onRetry && (
              <Pressable
                onPress={onRetry}
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 3,
                  paddingHorizontal: 8,
                  paddingVertical: 3,
                  borderRadius: 4,
                  backgroundColor: T.giltSoft,
                  borderWidth: 1,
                  borderColor: T.giltLine,
                }}
              >
                <Feather name="rotate-ccw" size={9} color={T.gilt} />
                <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: T.gilt }}>Retry</Text>
              </Pressable>
            )}
          </View>
        )}
        {/* Memory recall badge — shown when the reply was generated via the recall intent */}
        {!isUser && !isErr && (message as any).meta?.intent === 'recall' && (
          <View style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 4,
            marginTop: 4,
            paddingHorizontal: 8,
            paddingVertical: 3,
            borderRadius: 6,
            borderWidth: 1,
            borderColor: '#7c3aed44',
            backgroundColor: '#7c3aed10',
            alignSelf: 'flex-start',
          }}>
            <Text style={{ fontSize: 11, color: '#7c3aed' }}>✨</Text>
            <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: '#7c3aed' }}>
              Memory recall
            </Text>
          </View>
        )}
        {/* Model attribution — always shown on assistant messages; fallback to "—" when model unknown (#82) */}
        {!isUser && !isErr && (
          <Text style={{
            fontSize: 13,
            fontFamily: 'Inter_400Regular',
            color: colors.mutedForeground,
            marginTop: 3,
            opacity: 0.6,
            letterSpacing: 0.2,
          }}>
            {(message as any).meta?.model
              ? String((message as any).meta.model).split('/').pop()
              : '—'}
          </Text>
        )}
        {/* Source citations — shown when knowledge was injected for this reply */}
        {!isUser && !isErr &&
          (message as any).meta?.sources &&
          ((message as any).meta.sources as any[]).length > 0 && (
          <MobileSourcesFooter
            sources={(message as any).meta.sources as any[]}
            colors={colors}
          />
        )}
        {/* Generated document download card */}
        {!isUser && !isErr && (message as any).meta?.generated_document && (() => {
          const gd = (message as any).meta.generated_document as {
            filename: string; download_url: string; format: string; size_bytes: number;
          };
          const fmtIcon: Record<string, string> = {
            docx: '📝', pdf: '📄', pptx: '📊', xlsx: '📈',
          };
          const icon = fmtIcon[gd.format] ?? '📁';
          const kb = (gd.size_bytes / 1024).toFixed(1);
          return (
            <Pressable
              onPress={() => {
                const domain = process.env.EXPO_PUBLIC_DOMAIN;
                const url = `https://${domain}${gd.download_url}`;
                (async () => {
                  try {
                    if (Platform.OS === 'web') {
                      const resp = await mobileFetch(url);
                      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                      const href = URL.createObjectURL(await resp.blob());
                      const a = document.createElement('a');
                      a.href = href; a.download = gd.filename; a.click();
                      setTimeout(() => URL.revokeObjectURL(href), 10_000);
                      return;
                    }
                    const FileSystem = await import('expo-file-system/legacy');
                    const Sharing    = await import('expo-sharing');
                    const { getApiToken } = await import('@/lib/token');
                    const token = getApiToken();
                    const safeName = gd.filename.replace(/[^a-zA-Z0-9._-]/g, '_');
                    const dest = `${FileSystem.cacheDirectory}${safeName}`;
                    await FileSystem.deleteAsync(dest, { idempotent: true });
                    const dl = await FileSystem.downloadAsync(url, dest, {
                      headers: token ? { authorization: `Bearer ${token}` } : undefined,
                    });
                    if (dl.status !== 200) throw new Error(`HTTP ${dl.status}`);
                    const info = await FileSystem.getInfoAsync(dl.uri);
                    if (!info.exists) throw new Error('File not found after download');
                    const mimeMap: Record<string, string> = {
                      docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                      pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                      xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                      pdf:  'application/pdf',
                    };
                    await Sharing.shareAsync(dl.uri, {
                      mimeType: mimeMap[gd.format] ?? 'application/octet-stream',
                      dialogTitle: gd.filename,
                    });
                    FileSystem.deleteAsync(dest, { idempotent: true }).catch(() => {});
                  } catch (e: any) {
                    Alert.alert('Download failed', e.message ?? 'Could not download file');
                  }
                })();
              }}
              style={({ pressed }) => ({
                marginTop: 10,
                flexDirection: 'row',
                alignItems: 'center',
                gap: 10,
                padding: 12,
                borderRadius: 10,
                borderWidth: 1,
                borderColor: alpha(T.green, 0.32),
                backgroundColor: pressed ? T.greenSoft : alpha(T.green, 0.06),
              })}
            >
              <Text style={{ fontSize: 22 }}>{icon}</Text>
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: colors.foreground }} numberOfLines={1}>
                  {gd.filename}
                </Text>
                <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                  {gd.format.toUpperCase()} · {kb} KB — tap to download
                </Text>
              </View>
              <Feather name="download" size={18} color={T.green} />
            </Pressable>
          );
        })()}
        {/* Queued indicator — shown when the message is held in the offline outbox */}
        {isUser && !!(message as LocalMessage).queued && (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 3 }}>
            <Feather name="clock" size={10} color={colors.mutedForeground} style={{ opacity: 0.6 }} />
            <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, opacity: 0.7 }}>
              Queued — will send when back online
            </Text>
          </View>
        )}
        {copied && (
          <Text style={{ fontSize: 10, color: colors.mutedForeground, marginTop: 2, fontFamily: 'Inter_400Regular' }}>
            Copied ✓
          </Text>
        )}
      </View>
    </Pressable>
  );
}

// ── Mobile sources footer ─────────────────────────────────────────────────────

/**
 * Collapsible "Sources (N)" section rendered below an assistant message.
 * Tapping a source navigates to the library document page when a doc_id is set.
 */
function MobileSourcesFooter({ sources, colors }: { sources: any[]; colors: any }) {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  // Normalize + dedupe by stable id
  const normalized = (sources ?? []).filter(Boolean).map((s: any) => ({
    id: String(s.id ?? s.url ?? s.source_doc_id ?? s.doc_id ?? s.title ?? ''),
    title: s.title ?? s.doc_title ?? s.url ?? 'Document',
    docId: s.source_doc_id ?? s.doc_id ?? null,
    workId: s.work_id ?? null,
    passage: s.passage ?? null,
    isWeb: s.kind === 'web',
  }));
  const seen = new Set<string>();
  const unique = normalized.filter((s) => {
    if (!s.id || seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
  if (unique.length === 0) return null;

  return (
    <View style={{ marginTop: 6 }}>
      <Pressable
        onPress={() => setOpen((v) => !v)}
        hitSlop={8}
        style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}
      >
        <Feather name="book-open" size={11} color={colors.mutedForeground} />
        <Text style={{ fontSize: 10, fontFamily: 'Inter_500Medium', color: colors.mutedForeground, opacity: 0.6 }}>
          Sources ({unique.length})
        </Text>
        <Feather name={open ? 'chevron-up' : 'chevron-right'} size={10} color={colors.mutedForeground} />
      </Pressable>

      {open && (
        <View style={{ marginTop: 4 }}>
          {unique.map((s, i) => (
            <Pressable
              key={i}
              onPress={() => {
                if (s.docId) {
                  router.push(`/library/${s.docId}` as any);
                }
              }}
              style={({ pressed }) => ({
                flexDirection: 'row',
                alignItems: 'flex-start',
                gap: 6,
                paddingHorizontal: 8,
                paddingVertical: 6,
                borderRadius: 8,
                backgroundColor: pressed ? colors.muted : 'transparent',
              })}
            >
              <Feather
                name={s.isWeb ? 'globe' : 'file-text'}
                size={12}
                color={colors.primary}
                style={{ marginTop: 1, opacity: 0.7 }}
              />
              <View style={{ flex: 1 }}>
                <Text
                  style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}
                  numberOfLines={1}
                >
                  {s.title}
                </Text>
                {!!s.passage && (
                  <Text
                    style={{
                      fontSize: 11,
                      fontFamily: 'Inter_400Regular',
                      color: colors.mutedForeground,
                      marginTop: 2,
                      lineHeight: 15,
                    }}
                    numberOfLines={2}
                  >
                    {s.passage}
                  </Text>
                )}
              </View>
              {s.docId && (
                <Feather name="chevron-right" size={11} color={colors.mutedForeground} style={{ opacity: 0.4, marginTop: 1 }} />
              )}
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}

function ReasoningBlock({ text, colors }: { text: string; colors: any }) {
  const [open, setOpen] = useState(false);
  return (
    <View
      style={{
        marginBottom: 8,
        borderRadius: 8,
        borderWidth: 1,
        borderColor: '#7c3aed33',
        backgroundColor: '#7c3aed08',
        overflow: 'hidden',
      }}
    >
      <Pressable
        onPress={() => setOpen((v) => !v)}
        style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 7 }}
        hitSlop={4}
      >
        <Feather name="cpu" size={11} color="#7c3aed99" />
        <Text style={{ flex: 1, fontSize: 11, fontFamily: 'Inter_400Regular', color: '#7c3aed88', letterSpacing: 0.2 }}>
          Reasoning
        </Text>
        <Feather name={open ? 'chevron-up' : 'chevron-down'} size={11} color="#7c3aed66" />
      </Pressable>
      {open && (
        <View
          style={{
            paddingHorizontal: 10,
            paddingBottom: 10,
            paddingTop: 8,
            borderTopWidth: 1,
            borderTopColor: '#7c3aed22',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontFamily: 'Inter_400Regular',
              fontStyle: 'italic',
              color: '#7c3aed88',
              lineHeight: 18,
            }}
          >
            {text}
          </Text>
        </View>
      )}
    </View>
  );
}

function MessageSkeletonRow({ align = 'left', width = '70%' }: { align?: 'left' | 'right'; width?: string }) {
  const colors = useColors();
  const opacity = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.7, duration: 700, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.4, duration: 700, useNativeDriver: true }),
      ])
    ).start();
  }, []);

  return (
    <View style={{ paddingHorizontal: 16, paddingVertical: 6, alignItems: align === 'right' ? 'flex-end' : 'flex-start' }}>
      <Animated.View
        style={{
          height: 40, width, borderRadius: 18,
          backgroundColor: colors.muted,
          opacity,
        }}
      />
    </View>
  );
}

// stallLevel: 0 = normal typing dots, 1 = "Taking longer…" (≥15 s), 2 = "This is taking a while…" (≥30 s)
function TypingIndicator({ colors, T, stallLevel = 0 }: { colors: any; T: ReturnType<typeof useVellumTokens>; stallLevel?: 0 | 1 | 2 }) {
  const stallText =
    stallLevel === 2
      ? 'This is taking a while — you can retry if it hangs'
      : stallLevel === 1
      ? 'Taking longer than usual…'
      : null;

  return (
    <View style={[styles.bubbleRow, styles.bubbleLeft]}>
      <View style={[styles.avatar, { backgroundColor: colors.primary }]}>
        <Feather name="cpu" size={12} color={colors.primaryForeground} />
      </View>
      <View
        style={[
          styles.bubble,
          {
            backgroundColor: colors.card,
            borderColor: stallLevel > 0 ? T.giltLine : colors.border,
            borderWidth: 1,
            gap: stallText ? 6 : 0,
          },
        ]}
      >
        <ActivityIndicator size="small" color={stallLevel > 0 ? T.gilt : colors.mutedForeground} />
        {!!stallText && (
          <Text
            style={{
              fontSize: 11,
              fontFamily: 'Inter_400Regular',
              color: T.gilt,
              fontStyle: 'italic',
              maxWidth: 220,
            }}
          >
            {stallText}
          </Text>
        )}
      </View>
    </View>
  );
}

export default function ChatScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const colorScheme = useColorScheme();
  const isDark = colorScheme === 'dark';
  const { id, draft, msgId } = useLocalSearchParams<{ id: string; draft?: string; msgId?: string }>();
  const navigation = useNavigation();
  const router = useRouter();
  const inputRef = useRef<TextInput>(null);
  const flatListRef = useRef<FlatList>(null);

  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([]);
  const [initialized, setInitialized] = useState(false);
  const [highlightedMsgId, setHighlightedMsgId] = useState<string | null>(null);
  const scrolledToMsgRef = useRef(false);
  const scrollTargetRef = useRef<number | null>(null);
  const [sendFailed, setSendFailed] = useState(false);
  const [modelPickerVisible, setModelPickerVisible] = useState(false);
  const [deepMode, setDeepMode] = useState(false);
  // stallLevel: 0 = normal, 1 = "Taking longer…" (15 s), 2 = "This is taking a while…" (30 s)
  const [stallLevel, setStallLevel] = useState<0 | 1 | 2>(0);
  const stallTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  // Document generation state
  const [docGenLoading, setDocGenLoading] = useState(false);
  // Image attachment state
  const [pendingImage, setPendingImage] = useState<{
    uri: string;
    base64: string;
    mediaType: string;
  } | null>(null);
  // Document attachment state (PDF / DOCX / CSV / TXT / XLSX)
  const [pendingFile, setPendingFile] = useState<{ name: string; text: string } | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  // Web search toggle
  const [webSearch, setWebSearch] = useState(false);
  const [webSearchLoading, setWebSearchLoading] = useState(false);
  // Work context controls
  const [contextSheetOpen, setContextSheetOpen] = useState(false);
  const [scopeAll, setScopeAll] = useState(false);
  const [pinnedDocIds, setPinnedDocIds] = useState<Set<string>>(new Set());

  const { data, isLoading, isError, refetch } = useGetConversation(id, { query: { staleTime: 10_000 } } as any);
  const conversation = data?.conversation;
  const serverMessages = data?.messages ?? [];

  const { data: modelsData } = useGetSystemModels();
  const models = modelsData?.models ?? [];
  const updateConv = useUpdateConversation();

  // Gate web search toggle on whether Tavily is configured
  const { data: webSearchStatus } = useGetWebSearchStatus({
    query: { staleTime: 60_000 },
  } as any);
  const webSearchAvailable = webSearchStatus?.configured ?? false;

  const convWorkId: string | null = (conversation as any)?.work_id ?? null;

  // Load work documents for the context sheet (only when a work is linked)
  const { data: workDocsData } = useGetWorkDocuments(
    convWorkId ?? '',
    { query: { enabled: !!convWorkId && contextSheetOpen, staleTime: 30_000 } } as any,
  );

  const currentModelId = (conversation as any)?.model;
  const currentModelLabel =
    models.find((m: any) => m.id === currentModelId)?.label ?? currentModelId ?? 'Default';

  // Persist last-used model so new conversations default to it (#69)
  useEffect(() => {
    if (currentModelId) {
      AsyncStorage.setItem(LAST_MODEL_KEY, currentModelId).catch(() => {});
    }
  }, [currentModelId]);

  // Sync web search state from conversation data
  useEffect(() => {
    if (conversation) {
      setWebSearch(!!(conversation as any).web_search_enabled);
    }
  }, [(conversation as any)?.web_search_enabled]);

  // Apply last-used model when conversation has none (#69)
  useEffect(() => {
    if (!currentModelId && models.length > 0 && id) {
      AsyncStorage.getItem(LAST_MODEL_KEY).then((saved) => {
        if (saved && models.some((m: any) => m.id === saved)) {
          updateConv.mutate({ convId: id, data: { model: saved } });
        }
      }).catch(() => {});
    }
  }, [currentModelId, models.length, id]);

  const handlePickModel = () => {
    if (models.length === 0) return;
    if (Platform.OS === 'ios') {
      const options = [...models.map((m: any) => m.label ?? m.id), 'Cancel'];
      ActionSheetIOS.showActionSheetWithOptions(
        { options, cancelButtonIndex: options.length - 1, title: 'Select AI Model' },
        (idx) => {
          if (idx < models.length) {
            const chosen = models[idx] as any;
            updateConv.mutate(
              { convId: id, data: { model: chosen.id } },
              { onSuccess: () => refetch() }
            );
          }
        }
      );
    } else {
      setModelPickerVisible(true);
    }
  };

  // ── Message cache: persist to disk so messages survive offline / restart ──
  // Write to cache whenever the server delivers messages.
  useEffect(() => {
    if (serverMessages.length > 0 && id) {
      writeCache(`conversation:${id}:messages`, serverMessages);
    }
  }, [serverMessages, id]);

  // Sync server messages into local state on first load.
  // If the server is unreachable and we have no data, fall back to disk cache.
  useEffect(() => {
    if (!initialized && serverMessages.length > 0) {
      setLocalMessages(serverMessages);
      setInitialized(true);
    } else if (!initialized && !isLoading && !isError) {
      setInitialized(true);
    } else if (!initialized && !isLoading && isError && id) {
      // Server unreachable — try disk cache so past messages are still readable.
      readCache<Message[]>(`conversation:${id}:messages`).then(entry => {
        if (entry?.data?.length) {
          setLocalMessages(entry.data);
        }
        setInitialized(true);
      });
    }
  }, [serverMessages, isLoading, isError, initialized, id]);

  // ── Hydrate queued bubbles from the outbox on mount / init ─────────────────
  // When the screen opens (or comes back online after a cache-only load),
  // restore any messages that are still sitting in the offline outbox so the
  // user can see they are pending — even after app restart.
  // Deduplication uses the stable msgId, not text, so identical repeated
  // messages are each tracked as separate pending entries.
  const outboxHydratedRef = useRef(false);
  useEffect(() => {
    if (!initialized || !id || outboxHydratedRef.current) return;
    outboxHydratedRef.current = true;
    getOutboxForConversation(id).then((pending) => {
      if (!pending.length) return;
      const queuedBubbles: LocalMessage[] = pending.map((entry) => ({
        id: `queued-${entry.msgId}`,
        conversation_id: id,
        role: 'user' as const,
        text: entry.text,
        created_at: new Date(entry.ts).toISOString(),
        queued: true,
        msgId: entry.msgId,
      }));
      setLocalMessages((prev) => {
        // Avoid duplicates — skip entries whose msgId is already in the list.
        const existingMsgIds = new Set(
          prev.filter((m) => (m as LocalMessage).msgId).map((m) => (m as LocalMessage).msgId)
        );
        const fresh = queuedBubbles.filter((b) => !existingMsgIds.has(b.msgId));
        return fresh.length ? [...prev, ...fresh] : prev;
      });
    }).catch(() => {});
  }, [initialized, id]);

  // #40 — When the server comes back (isError flips false), flush queued messages
  //        and reconcile the UI using stable msgIds — not text — so identical
  //        repeated messages are tracked independently.
  const prevIsErrorRef = useRef(false);
  useEffect(() => {
    if (prevIsErrorRef.current && !isError && initialized) {
      setSendFailed(false);
      // Remove only hard-error bubbles immediately; queued bubbles stay until
      // we confirm delivery below so messages are never visually lost.
      setLocalMessages((prev) => prev.filter((m) => !(m as any).isError));

      // Flush, then read what remains in the outbox for *this* conversation.
      // Only remove bubbles whose msgId is no longer in the outbox (confirmed
      // delivered).  Failed entries keep their queued bubble and msgId.
      flushMessageQueue()
        .then((sent) =>
          getOutboxForConversation(id).then((remaining) => {
            const stillPendingIds = new Set(remaining.map((m) => m.msgId));
            setLocalMessages((prev) =>
              prev.filter(
                (m) =>
                  !(m as LocalMessage).queued ||
                  stillPendingIds.has((m as LocalMessage).msgId ?? ''),
              ),
            );
            // Refresh server messages only when at least one was delivered.
            if (sent > 0) refetch();
          }),
        )
        .catch(() => {});
    }
    prevIsErrorRef.current = isError;
  }, [isError, initialized]);

  useEffect(() => {
    navigation.setOptions({ title: conversation?.title || 'Conversation' });
  }, [conversation?.title, navigation]);

  // Pre-seed the composer from a `draft` navigation param (e.g. a research gap).
  const seededRef = useRef(false);
  useEffect(() => {
    if (!seededRef.current && typeof draft === 'string' && draft.trim()) {
      setText(draft);
      seededRef.current = true;
    }
  }, [draft]);

  // Scroll to a specific message (from search-result navigation) and briefly highlight it.
  // displayMessages is the reversed copy of localMessages so the display index is
  // (localMessages.length - 1 - originalIndex).  The FlatList is inverted so index 0
  // sits at the bottom — scrollToIndex still works correctly with the inverted prop.
  //
  // Because FlatList only measures items in its initial render window, scrollToIndex
  // fails for older messages (onScrollToIndexFailed fires).  The failure handler jumps
  // to an estimated offset, causing the list to extend further, and then retries the
  // exact scroll via scrollTargetRef so the highlight always lands on the right item.
  useEffect(() => {
    if (!msgId || scrolledToMsgRef.current || localMessages.length === 0) return;
    const originalIndex = localMessages.findIndex((m) => m.id === msgId);
    if (originalIndex === -1) return;
    scrolledToMsgRef.current = true;
    const displayIndex = localMessages.length - 1 - originalIndex;
    scrollTargetRef.current = displayIndex;

    // Track both timers so effect cleanup can cancel them even if the component unmounts
    // or a re-render interrupts before they fire.
    let highlightTimer: ReturnType<typeof setTimeout> | null = null;
    const scrollTimer = setTimeout(() => {
      flatListRef.current?.scrollToIndex({ index: displayIndex, animated: true, viewPosition: 0.5 });
      setHighlightedMsgId(msgId);
      // 1 400 ms hold + 600 ms fade + 200 ms buffer = 2 200 ms before clearing.
      // The Animated.View is already at opacity 0 by the time this fires so
      // setting highlightedMsgId to null causes no visible jump.
      highlightTimer = setTimeout(() => setHighlightedMsgId(null), 2200);
    }, 400);

    return () => {
      clearTimeout(scrollTimer);
      if (highlightTimer) clearTimeout(highlightTimer);
    };
  }, [msgId, localMessages]);

  // ── Stall indicator timers ────────────────────────────────────────────────
  // When sending, fire at 15 s (level 1) and 30 s (level 2) to update the
  // TypingIndicator caption.  All timers are cleared whenever sending stops
  // so a fast reply leaves zero residue.
  useEffect(() => {
    // Clear any previously running timers first
    stallTimersRef.current.forEach(clearTimeout);
    stallTimersRef.current = [];

    if (!sending) {
      setStallLevel(0);
      return;
    }

    const t1 = setTimeout(() => setStallLevel(1), 15_000);
    const t2 = setTimeout(() => setStallLevel(2), 30_000);
    stallTimersRef.current = [t1, t2];

    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      setStallLevel(0);
    };
  }, [sending]);

  const displayMessages = [...localMessages].reverse();

  // ── Pick an image from the photo library ──────────────────────────────────
  const pickImage = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert(
        'Permission needed',
        'Allow access to your photo library to attach images to chat.'
      );
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: false,
      quality: 0.7,
      base64: true,
    });
    if (!result.canceled && result.assets[0]) {
      const asset = result.assets[0];
      setPendingImage({
        uri: asset.uri,
        base64: asset.base64 ?? '',
        mediaType: asset.mimeType ?? 'image/jpeg',
      });
    }
  };

  // ── Take a photo with the camera ─────────────────────────────────────────
  const takePhoto = async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert(
        'Camera permission needed',
        'Allow camera access to take a photo and ask the AI about it.'
      );
      return;
    }
    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: false,
      quality: 0.7,
      base64: true,
    });
    if (!result.canceled && result.assets[0]) {
      const asset = result.assets[0];
      setPendingImage({
        uri: asset.uri,
        base64: asset.base64 ?? '',
        mediaType: asset.mimeType ?? 'image/jpeg',
      });
    }
  };

  // ── Pick a document for chat context injection ───────────────────────────
  const handleFileAttach = async () => {
    if (fileLoading || sending) return;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['*/*'],
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.[0]) return;
      const asset = result.assets[0];
      setFileLoading(true);
      const b64 = await FileSystem.readAsStringAsync(asset.uri, {
        encoding: 'base64' as any,
      });
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const resp = await mobileFetch(`https://${domain}/api/extract-file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: asset.name, content_b64: b64 }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err?.detail ?? `Server error ${resp.status}`);
      }
      const data = await resp.json();
      setPendingFile({ name: asset.name, text: data.extracted_text });
    } catch (e: any) {
      Alert.alert('File error', e.message ?? 'Could not read file. Try PDF, DOCX, XLSX, CSV or TXT.');
    } finally {
      setFileLoading(false);
    }
  };

  // ── Download a generated document (authenticated) ────────────────────────
  const downloadGeneratedDoc = async (downloadPath: string, filename: string) => {
    const domain = process.env.EXPO_PUBLIC_DOMAIN;
    const url = `https://${domain}${downloadPath}`;
    try {
      if (Platform.OS === 'web') {
        const resp = await mobileFetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const href = URL.createObjectURL(await resp.blob());
        const a = document.createElement('a');
        a.href = href;
        a.download = filename;
        a.click();
        setTimeout(() => URL.revokeObjectURL(href), 10_000);
        return;
      }
      const FileSystem = await import('expo-file-system/legacy');
      const Sharing    = await import('expo-sharing');
      const { getApiToken } = await import('@/lib/token');
      const token = getApiToken();
      const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_');
      const dest = `${FileSystem.cacheDirectory}${safeName}`;
      await FileSystem.deleteAsync(dest, { idempotent: true });
      const dl = await FileSystem.downloadAsync(url, dest, {
        headers: token ? { authorization: `Bearer ${token}` } : undefined,
      });
      if (dl.status !== 200) throw new Error(`HTTP ${dl.status}`);
      const info = await FileSystem.getInfoAsync(dl.uri);
      if (!info.exists || (info as any).size === 0) throw new Error('Downloaded file is empty');
      const mimeMap: Record<string, string> = {
        docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        pdf:  'application/pdf',
      };
      const ext = filename.split('.').pop()?.toLowerCase() ?? '';
      await Sharing.shareAsync(dl.uri, {
        mimeType: mimeMap[ext] ?? 'application/octet-stream',
        dialogTitle: filename,
      });
      FileSystem.deleteAsync(dest, { idempotent: true }).catch(() => {});
    } catch (e: any) {
      Alert.alert('Download failed', e.message ?? 'Could not download file');
    }
  };

  // ── Generate a document from the current chat prompt ─────────────────────
  const handleGenerateDoc = async (format: string) => {
    const prompt = text.trim();
    if (!prompt) {
      Alert.alert('Type a prompt first', 'Describe what you want — e.g. "Write a 5-slide presentation on climate change"');
      return;
    }
    if (docGenLoading || sending) return;

    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setDocGenLoading(true);
    setText('');

    const clientMsgId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const userMsg: LocalMessage = {
      id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
      conversation_id: id,
      role: 'user',
      text: `📄 Generate ${format.toUpperCase()}: ${prompt}`,
      created_at: new Date().toISOString(),
      msgId: clientMsgId,
    };
    setLocalMessages(prev => [...prev, userMsg]);

    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const resp = await mobileFetch(`https://${domain}/api/generate/from-prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          format,
          work_id: (conversation as any)?.work_id || null,
          conversation_id: id,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err?.detail ?? `Server error ${resp.status}`);
      }
      const data = await resp.json();
      const aiMsg: LocalMessage = {
        id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
        conversation_id: id,
        role: 'assistant',
        text: `✅ Your **${data.filename}** is ready (${(data.size_bytes / 1024).toFixed(1)} KB).`,
        created_at: new Date().toISOString(),
        meta: {
          generated_document: {
            filename: data.filename,
            download_url: data.download_url,
            format: format.toLowerCase(),
            size_bytes: data.size_bytes,
          },
        } as any,
      };
      setLocalMessages(prev => [...prev, aiMsg]);
    } catch (e: any) {
      const errMsg: LocalMessage = {
        id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
        conversation_id: id,
        role: 'assistant',
        text: `⚠️ Document generation failed: ${e.message ?? 'Unknown error'}`,
        created_at: new Date().toISOString(),
        isError: true,
      };
      setLocalMessages(prev => [...prev, errMsg]);
    } finally {
      setDocGenLoading(false);
    }
  };

  // ── Show document format picker ───────────────────────────────────────────
  const handleDocGenPress = () => {
    if (!text.trim()) {
      Alert.alert(
        'Type your request first',
        'Describe what to create — e.g. "Research quantum computing and create a PowerPoint"',
      );
      return;
    }
    const formats = ['DOCX (Word)', 'PDF', 'PPTX (PowerPoint)', 'XLSX (Excel)', 'Cancel'];
    const formatKeys = ['docx', 'pdf', 'pptx', 'xlsx'];
    if (Platform.OS === 'ios') {
      ActionSheetIOS.showActionSheetWithOptions(
        { options: formats, cancelButtonIndex: formats.length - 1, title: 'Save as…' },
        (idx) => { if (idx < formatKeys.length) handleGenerateDoc(formatKeys[idx]); },
      );
    } else {
      Alert.alert('Save as…', 'Choose a document format', [
        { text: 'DOCX (Word)',       onPress: () => handleGenerateDoc('docx') },
        { text: 'PDF',              onPress: () => handleGenerateDoc('pdf')  },
        { text: 'PPTX (PowerPoint)', onPress: () => handleGenerateDoc('pptx') },
        { text: 'XLSX (Excel)',      onPress: () => handleGenerateDoc('xlsx') },
        { text: 'Cancel', style: 'cancel' },
      ]);
    }
  };

  // ── Toggle Tavily web search for this conversation ────────────────────────
  const handleWebSearchToggle = async () => {
    if (webSearchLoading || sending) return;
    const next = !webSearch;
    setWebSearch(next); // optimistic
    setWebSearchLoading(true);
    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      await mobileFetch(`https://${domain}/api/conversations/${id}/web-search`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      });
    } catch {
      setWebSearch(!next); // revert on failure
    } finally {
      setWebSearchLoading(false);
    }
  };

  // ── Show image source picker (library or camera) ──────────────────────────
  const handleImageAttach = () => {
    if (Platform.OS === 'ios') {
      ActionSheetIOS.showActionSheetWithOptions(
        {
          options: ['Cancel', 'Photo Library', 'Take Photo'],
          cancelButtonIndex: 0,
          title: 'Attach Image',
        },
        (idx) => {
          if (idx === 1) pickImage();
          else if (idx === 2) takePhoto();
        }
      );
    } else {
      Alert.alert('Attach Image', 'Choose a source', [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Photo Library', onPress: pickImage },
        { text: 'Take Photo', onPress: takePhoto },
      ]);
    }
  };

  // ── Continue a cut-short reply ───────────────────────────────────────────
  const handleContinue = async (messageId: string) => {
    if (!messageId || sending) return;
    setSending(true);

    // Find the base (clean) partial text in the current message list
    const allMsgs = data?.messages ?? [];
    const targetMsg = allMsgs.find((m) => m.id === messageId);
    const rawText = targetMsg?.text ?? '';
    const SUFFIX = '\n\n*(Response was cut short — re-send to continue.)*';
    const partialBase = rawText.endsWith(SUFFIX) ? rawText.slice(0, -SUFFIX.length) : rawText;

    // Optimistically update the bubble to show it's loading
    setLocalMessages((prev) => {
      const base = allMsgs.map((m) => ({ ...m, isError: false } as LocalMessage));
      return base.map((m) =>
        m.id === messageId ? { ...m, text: partialBase } as LocalMessage : m as LocalMessage
      );
    });

    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const url = `https://${domain}/api/conversations/${id}/continue`;
      const resp = await mobileFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stream: false }),
      });
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody?.detail ?? `Server returned ${resp.status}`);
      }
      const body = await resp.json();
      const updatedMsg = body.message;
      if (updatedMsg) {
        setLocalMessages((prev) =>
          prev.map((m) => m.id === messageId ? { ...updatedMsg, isError: false } as LocalMessage : m)
        );
      }
      // Refetch to sync with server state
      refetch();
    } catch (err) {
      const errMsg: LocalMessage = {
        id: Date.now().toString() + 'cont-err',
        conversation_id: id,
        role: 'assistant',
        text: 'Could not continue — please try again.',
        created_at: new Date().toISOString(),
        isError: true,
      };
      setLocalMessages((prev) => [...prev, errMsg]);
    } finally {
      setSending(false);
    }
  };

  // ── Retry a timed-out / incomplete reply ─────────────────────────────────
  // Strategy: retain the timed-out exchange in history so local and server
  // state stay consistent across reloads.  handleSend adds fresh user + AI
  // messages below the incomplete exchange rather than mutating server state.
  //
  // Image turns: the original binary is not persisted after the session ends —
  // only a lossy thumbnail is kept.  For image-only turns, retry is disabled
  // with an explanation.  For text+image turns, the user is asked to confirm
  // a text-only retry before we proceed.
  const handleRetry = (incompleteMessageId: string) => {
    if (sending) return;
    const msgs = localMessages;
    const idx = msgs.findIndex((m) => m.id === incompleteMessageId);
    const searchEnd = idx === -1 ? msgs.length - 1 : idx - 1;
    let lastUserMsg: LocalMessage | null = null;
    for (let i = searchEnd; i >= 0; i--) {
      if (msgs[i].role === 'user' && !(msgs[i] as any).isError) {
        lastUserMsg = msgs[i];
        break;
      }
    }
    if (!lastUserMsg) return;

    // Detect whether the original turn included an image.
    const hadImage =
      !!(lastUserMsg as any).meta?.image_thumbnail_b64 ||
      !!(lastUserMsg as LocalMessage).localImageUri;
    // Strip the "[Image] " display-prefix that handleSend prepends.
    const rawText = (lastUserMsg.text ?? '').replace(/^\[Image\] /, '').trim();
    const isImageOnly = hadImage && !rawText;

    if (isImageOnly) {
      // Can't retry without the binary — thumbnail is lossy and not re-sendable.
      Alert.alert(
        'Cannot retry image message',
        'The original image is no longer available. Please re-attach the image and send again.',
      );
      return;
    }

    if (hadImage) {
      // Text + image turn: warn that the image won't be re-sent.
      Alert.alert(
        'Retry without image?',
        'The original image cannot be re-sent from history. Only your text question will be retried.',
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Retry text only',
            onPress: () => {
              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
              handleSend(rawText);
            },
          },
        ],
      );
      return;
    }

    if (!rawText) return;

    // Text-only retry: re-send immediately.  The timed-out exchange stays in
    // history — local and server state remain in sync across reloads.
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    handleSend(rawText);
  };

  // ── Send message (with optional image or document) ───────────────────────
  const handleSend = async (forceText?: string) => {
    const trimmed = (forceText ?? text).trim();
    // Allow send with image or file even when text is empty
    if ((!trimmed && !pendingImage && !pendingFile) || sending) return;

    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    if (!forceText) setText('');
    setSendFailed(false);

    // Capture and clear pending attachments before the async path
    const imageToSend = pendingImage;
    setPendingImage(null);
    const fileToSend = pendingFile;
    setPendingFile(null);

    // Build display text for the optimistic message bubble
    const displayText = imageToSend
      ? (trimmed ? `[Image] ${trimmed}` : '[Image attached]')
      : fileToSend
      ? (trimmed ? `[${fileToSend.name}]\n${trimmed}` : `[${fileToSend.name}]`)
      : trimmed;

    // Stable client-side ID used for the idempotency key and queued-bubble
    // reconciliation.  Generated once per send attempt so a retry of the
    // same message reuses the same key and the server suppresses the duplicate.
    const clientMsgId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

    const userMsg: LocalMessage = {
      id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
      conversation_id: id,
      role: 'user',
      text: displayText,
      created_at: new Date().toISOString(),
      // Keep local URI for thumbnail display; never sent to server
      localImageUri: imageToSend?.uri,
      // Carry the idempotency key so queued reconciliation can match by ID.
      msgId: clientMsgId,
    };

    setLocalMessages((prev) => [...prev, userMsg]);
    setSending(true);

    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const url = `https://${domain}/api/conversations/${id}/messages`;
      // Build the API text: prepend extracted document content when a file is attached
      let apiText = trimmed || (imageToSend ? 'What is in this image?' : '');
      if (fileToSend) {
        const docHeader = `[Document: ${fileToSend.name}]\n\n${fileToSend.text}\n\n---\n\n`;
        apiText = docHeader + (apiText || 'Please analyze this document.');
      }
      const payload: Record<string, unknown> = {
        text: apiText,
        stream: false,
        deep: deepMode,
        // Server persists this in messages.client_msg_id (schema v86) and
        // checks it before storing so retries after a lost response are safe.
        client_msg_id: clientMsgId,
        // Work context controls
        ...(convWorkId ? { scope: scopeAll ? 'all' : 'work' } : {}),
        ...(pinnedDocIds.size > 0 ? { context_doc_ids: Array.from(pinnedDocIds) } : {}),
      };
      if (imageToSend?.base64) {
        payload.image_b64 = imageToSend.base64;
        payload.image_media_type = imageToSend.mediaType;
      }
      const resp = await mobileFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        // Try to surface a helpful message for vision failures
        let errText = `Server returned ${resp.status}`;
        try {
          const errBody = await resp.json();
          errText = errBody?.detail ?? errText;
        } catch { /* ignore */ }
        throw new Error(errText);
      }
      // Also check for a 200 vision-not-supported marker (future-proofing)
      // Primary detection is via 422 + VISION_NOT_SUPPORTED prefix above.
      const body = await resp.json();
      const aiMsg: Message = body.message;
      if (aiMsg) {
        setLocalMessages((prev) => [...prev, aiMsg]);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      const isNetworkError = err instanceof TypeError && msg.toLowerCase().includes('network');
      // Backend returns VISION_NOT_SUPPORTED prefix via HTTP 422 when the
      // configured model rejects image input — match it reliably.
      const isVisionError = msg.startsWith('VISION_NOT_SUPPORTED') ||
        msg.toLowerCase().includes('vision') ||
        msg.toLowerCase().includes('multimodal') ||
        msg.toLowerCase().includes('does not support image');

      if (isNetworkError && trimmed && !imageToSend) {
        // Server unreachable and no image — queue the message for delivery
        // once connectivity returns, and mark the optimistic bubble as "queued"
        // so the user can see it's pending rather than lost.
        // Pass the same clientMsgId that was put on the optimistic bubble so
        // the outbox entry and bubble share a stable key for reconciliation.
        await queueMessage(id, trimmed, clientMsgId).catch(() => {});
        setLocalMessages((prev) =>
          prev.map((m) => m.id === userMsg.id ? { ...m, queued: true } : m)
        );
        setSendFailed(true);
      } else {
        const errMsg: LocalMessage = {
          id: Date.now().toString() + 'err',
          conversation_id: id,
          role: 'assistant',
          text: isNetworkError
            ? 'Cannot reach the server. Check your connection and try again.'
            : isVisionError
            ? 'This model does not support images. Set a vision-capable model in System Settings (e.g. llava, qwen2-vl).'
            : 'Something went wrong sending your message. Please try again.',
          created_at: new Date().toISOString(),
          isError: true,
        };
        setLocalMessages((prev) => [...prev, errMsg]);
        setSendFailed(true);
      }
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const topPad = isWeb ? 67 : insets.top + 44;

  // Full-screen loading — show skeleton bubbles instead of a bare spinner
  if (isLoading && !initialized && localMessages.length === 0) {
    return (
      <View style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}>
        <MessageSkeletonRow width="60%" />
        <MessageSkeletonRow align="right" width="45%" />
        <MessageSkeletonRow width="75%" />
        <MessageSkeletonRow align="right" width="50%" />
        <MessageSkeletonRow width="65%" />
      </View>
    );
  }

  // Full-screen error — no data loaded at all
  if (isError && !initialized && !data) {
    return (
      <View
        style={[styles.screen, { backgroundColor: colors.background, paddingTop: topPad }]}
      >
        <View style={styles.centered}>
          <Feather name="wifi-off" size={40} color={colors.mutedForeground} />
          <Text style={[styles.errorTitle, { color: colors.foreground }]}>
            Can't reach the server
          </Text>
          <Text style={[styles.errorDetail, { color: colors.mutedForeground }]}>
            Make sure Orivellum is running, then tap retry.
          </Text>
          <Pressable
            onPress={() => refetch()}
            style={[styles.retryBtn, { backgroundColor: colors.primary }]}
          >
            <Text style={[styles.retryBtnText, { color: colors.primaryForeground }]}>Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={[styles.screen, { backgroundColor: colors.background }]}
      behavior="padding"
      keyboardVerticalOffset={0}
    >
      {/* Work context sheet — scope toggle + file pin list */}
      <Modal
        visible={contextSheetOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setContextSheetOpen(false)}
      >
        <View style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.4)' }}>
          <Pressable style={{ flex: 1 }} onPress={() => setContextSheetOpen(false)} />
          <View style={{
            backgroundColor: colors.card,
            borderTopLeftRadius: 20,
            borderTopRightRadius: 20,
            borderTopWidth: 1,
            borderColor: colors.border,
            paddingTop: 20,
            paddingHorizontal: 16,
            paddingBottom: insets.bottom + 20,
            maxHeight: '75%',
          }}>
            {/* Header */}
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 16 }}>
              <Text style={{ fontSize: 16, ...font('bold'), color: colors.foreground, flex: 1 }}>
                Context
              </Text>
              <Pressable onPress={() => setContextSheetOpen(false)} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
                <Feather name="x" size={18} color={colors.mutedForeground} />
              </Pressable>
            </View>

            {/* Scope toggle */}
            <Text style={{ fontSize: 11, ...font('medium'), color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 }}>
              Knowledge scope
            </Text>
            <View style={{ flexDirection: 'row', gap: 8, marginBottom: 20 }}>
              {[{ label: 'This Work', value: false }, { label: 'All Works', value: true }].map(({ label, value }) => (
                <Pressable
                  key={label}
                  onPress={() => setScopeAll(value)}
                  style={({ pressed }) => ({
                    flex: 1,
                    paddingVertical: 10,
                    paddingHorizontal: 14,
                    minHeight: 44,
                    borderRadius: 10,
                    borderWidth: 1.5,
                    borderColor: scopeAll === value ? colors.primary : colors.border,
                    backgroundColor: pressed ? colors.muted : scopeAll === value ? colors.primary + '10' : 'transparent',
                    alignItems: 'center',
                    justifyContent: 'center',
                  })}
                >
                  <Text style={{
                    fontSize: 13,
                    ...font(scopeAll === value ? 'semibold' : 'regular'),
                    color: scopeAll === value ? colors.primary : colors.mutedForeground,
                  }}>
                    {label}
                  </Text>
                </Pressable>
              ))}
            </View>

            {/* Pinned documents from work */}
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 10 }}>
              <Text style={{ fontSize: 11, ...font('medium'), color: colors.mutedForeground, textTransform: 'uppercase', letterSpacing: 0.8, flex: 1 }}>
                Pin documents
              </Text>
              {pinnedDocIds.size > 0 && (
                <Pressable onPress={() => setPinnedDocIds(new Set())} hitSlop={8}>
                  <Text style={{ fontSize: 11, color: colors.primary, ...font('medium') }}>Clear all</Text>
                </Pressable>
              )}
            </View>

            {!convWorkId ? (
              <Text style={{ fontSize: 13, color: colors.mutedForeground, ...font('regular'), textAlign: 'center', paddingVertical: 20 }}>
                No work linked to this conversation
              </Text>
            ) : !workDocsData?.documents?.length ? (
              <Text style={{ fontSize: 13, color: colors.mutedForeground, ...font('regular'), textAlign: 'center', paddingVertical: 20 }}>
                No documents in this work yet
              </Text>
            ) : (
              <ScrollView showsVerticalScrollIndicator={false} style={{ maxHeight: 300 }}>
                {(workDocsData.documents as any[]).map((doc: any) => {
                  const pinned = pinnedDocIds.has(doc.id);
                  return (
                    <Pressable
                      key={doc.id}
                      onPress={() => {
                        setPinnedDocIds(prev => {
                          const next = new Set(prev);
                          if (next.has(doc.id)) next.delete(doc.id);
                          else next.add(doc.id);
                          return next;
                        });
                      }}
                      style={({ pressed }) => ({
                        flexDirection: 'row',
                        alignItems: 'center',
                        gap: 10,
                        paddingVertical: 11,
                        paddingHorizontal: 12,
                        marginBottom: 6,
                        minHeight: 44,
                        borderRadius: 10,
                        borderWidth: 1,
                        borderColor: pinned ? colors.primary + '55' : colors.border,
                        backgroundColor: pressed ? colors.muted : pinned ? colors.primary + '08' : 'transparent',
                      })}
                    >
                      <View style={{
                        width: 20, height: 20, borderRadius: 5, borderWidth: 1.5,
                        borderColor: pinned ? colors.primary : colors.border,
                        backgroundColor: pinned ? colors.primary : 'transparent',
                        alignItems: 'center', justifyContent: 'center',
                      }}>
                        {pinned && <Feather name="check" size={11} color={colors.primaryForeground} />}
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontSize: 13, ...font('medium'), color: colors.foreground }} numberOfLines={1}>
                          {doc.title || doc.source?.split('/').pop() || 'Untitled'}
                        </Text>
                        <Text style={{ fontSize: 11, ...font('regular'), color: colors.mutedForeground }}>
                          {(doc.kind ?? doc.readiness ?? '').replace(/_/g, ' ')}
                        </Text>
                      </View>
                    </Pressable>
                  );
                })}
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>

      {/* Model picker modal — Android / web */}
      <Modal
        visible={modelPickerVisible}
        transparent
        animationType="slide"
        onRequestClose={() => setModelPickerVisible(false)}
      >
        <Pressable
          style={[styles.modalOverlay]}
          onPress={() => setModelPickerVisible(false)}
        >
          <View
            style={[styles.modelSheet, { backgroundColor: colors.card, borderColor: colors.border }]}
            onStartShouldSetResponder={() => true}
          >
            <Text style={[styles.modelSheetTitle, { color: colors.foreground }]}>Select AI Model</Text>
            <ScrollView>
              {models.map((m: any) => (
                <Pressable
                  key={m.id}
                  style={({ pressed }) => [
                    styles.modelRow,
                    { borderColor: colors.border, backgroundColor: pressed ? colors.muted : 'transparent' },
                    m.id === currentModelId && { backgroundColor: colors.muted },
                  ]}
                  onPress={() => {
                    updateConv.mutate(
                      { convId: id, data: { model: m.id } },
                      { onSuccess: () => refetch() }
                    );
                    setModelPickerVisible(false);
                  }}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={[styles.modelLabel, { color: colors.foreground }]}>{m.label ?? m.id}</Text>
                    {m.description ? (
                      <Text style={[styles.modelDesc, { color: colors.mutedForeground }]} numberOfLines={1}>
                        {m.description}
                      </Text>
                    ) : null}
                  </View>
                  {m.id === currentModelId && (
                    <Feather name="check" size={16} color={colors.primary} />
                  )}
                </Pressable>
              ))}
            </ScrollView>
          </View>
        </Pressable>
      </Modal>

      <View style={{ flex: 1, paddingTop: topPad }}>
        {/* Model badge row — always shown once a conversation is loaded; falls back to "Default" */}
        {conversation && (
          <Pressable
            onPress={models.length > 0 ? handlePickModel : undefined}
            style={({ pressed }) => [
              styles.modelBadgeRow,
              { borderBottomColor: colors.border, opacity: pressed && models.length > 0 ? 0.7 : 1 },
            ]}
          >
            <Feather name="cpu" size={11} color={colors.mutedForeground} />
            <Text style={[styles.modelBadgeText, { color: colors.mutedForeground }]}>
              {currentModelLabel}
            </Text>
            {models.length > 0 && (
              <Feather name="chevron-down" size={11} color={colors.mutedForeground} />
            )}
          </Pressable>
        )}

        {/* Work badge + context controls — shown when conversation is linked to a Work */}
        {conversation && (conversation as any).work_id && (
          <View style={{
            flexDirection: 'row',
            alignItems: 'center',
            borderBottomWidth: StyleSheet.hairlineWidth,
            borderBottomColor: colors.border,
            backgroundColor: colors.primary + '0a',
          }}>
            <Pressable
              onPress={() => router.push(`/work/${(conversation as any).work_id}` as any)}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 5,
                flex: 1,
                paddingHorizontal: 16,
                paddingVertical: 5,
              }}
            >
              <Feather name="book-open" size={11} color={colors.primary} />
              <Text style={{ fontSize: 11, ...font('medium'), color: colors.primary, flex: 1 }} numberOfLines={1}>
                {(conversation as any).work_title ?? 'Work'}
              </Text>
            </Pressable>
            {/* Scope indicator chip */}
            <Pressable
              onPress={() => setContextSheetOpen(true)}
              hitSlop={8}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
                paddingHorizontal: 10,
                paddingVertical: 5,
                marginRight: 8,
                borderRadius: 8,
                borderWidth: 1,
                borderColor: (pinnedDocIds.size > 0 || scopeAll) ? colors.primary + '55' : colors.border,
                backgroundColor: (pinnedDocIds.size > 0 || scopeAll) ? colors.primary + '10' : 'transparent',
              }}
            >
              <Feather
                name="layers"
                size={11}
                color={(pinnedDocIds.size > 0 || scopeAll) ? colors.primary : colors.mutedForeground}
              />
              <Text style={{
                fontSize: 10,
                ...font('medium'),
                color: (pinnedDocIds.size > 0 || scopeAll) ? colors.primary : colors.mutedForeground,
              }}>
                {scopeAll ? 'All Works' : pinnedDocIds.size > 0 ? `${pinnedDocIds.size} pinned` : 'Context'}
              </Text>
            </Pressable>
          </View>
        )}

        {/* Soft offline banner — shown when we have loaded data but subsequent fetches fail */}
        {isError && initialized && (
          <OfflineBanner
            message={
              sendFailed
                ? 'Messages may not be saving — server unreachable'
                : 'Server unreachable — showing cached messages'
            }
            onRetry={refetch}
          />
        )}

        <FlatList
          ref={flatListRef}
          data={displayMessages}
          keyExtractor={(m) => m.id ?? ''}
          renderItem={({ item }) => {
            const isCutShort = !!(item as any).meta?.cut_short;
            const isIncomplete = !!(item as any).meta?.incomplete;
            return (
              <MessageBubble
                message={item}
                colors={colors}
                T={T}
                isDark={isDark}
                highlighted={item.id === highlightedMsgId}
                onResend={isCutShort ? () => handleContinue(item.id ?? '') : undefined}
                onRetry={isIncomplete ? () => handleRetry(item.id ?? '') : undefined}
              />
            );
          }}
          inverted
          contentContainerStyle={styles.listContent}
          keyboardDismissMode="interactive"
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          onScrollToIndexFailed={({ highestMeasuredFrameIndex, averageItemLength }) => {
            // The target item hasn't been measured yet.  Jump to an estimated offset so
            // the list extends its render window to cover the target, then retry the
            // exact scrollToIndex after a layout pass.
            const target = scrollTargetRef.current ?? highestMeasuredFrameIndex;
            flatListRef.current?.scrollToOffset({
              offset: target * averageItemLength,
              animated: false,
            });
            setTimeout(() => {
              if (scrollTargetRef.current !== null) {
                flatListRef.current?.scrollToIndex({
                  index: scrollTargetRef.current,
                  animated: true,
                  viewPosition: 0.5,
                });
              }
            }, 200);
          }}
          ListHeaderComponent={sending ? <TypingIndicator colors={colors} T={T} stallLevel={stallLevel} /> : null}
          ListEmptyComponent={
            !sending ? (
              <View style={styles.emptyWrap}>
                <Feather name="message-circle" size={40} color={colors.mutedForeground} />
                <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
                  {isError ? 'Server is offline — messages cannot be sent' : 'Ask anything about your research'}
                </Text>
              </View>
            ) : null
          }
          ListFooterComponent={
            (conversation as any)?.context_summary ? (
              <View style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 8,
                paddingHorizontal: 16,
                paddingVertical: 12,
              }}>
                <View style={{ flex: 1, height: 1, backgroundColor: colors.border, opacity: 0.4 }} />
                <View style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 5,
                  paddingHorizontal: 10,
                  paddingVertical: 5,
                  borderRadius: 99,
                  borderWidth: 1,
                  borderColor: colors.border,
                  backgroundColor: colors.muted + '60',
                }}>
                  <Feather name="clock" size={10} color={colors.mutedForeground} style={{ opacity: 0.6 }} />
                  <Text style={{
                    fontSize: 10,
                    ...font('regular'),
                    color: colors.mutedForeground,
                    opacity: 0.7,
                  }}>
                    Earlier context summarized
                  </Text>
                </View>
                <View style={{ flex: 1, height: 1, backgroundColor: colors.border, opacity: 0.4 }} />
              </View>
            ) : null
          }
        />

        {/* Input bar */}
        <View
          style={[
            styles.inputBar,
            {
              borderTopColor: colors.border,
              backgroundColor: colors.background,
              paddingBottom: isWeb ? 34 : insets.bottom + 8,
            },
          ]}
        >
          {/* Pending image preview strip */}
          {pendingImage ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6, gap: 8 }}>
              <Image
                source={{ uri: pendingImage.uri }}
                style={{ width: 56, height: 56, borderRadius: 8, borderWidth: 1, borderColor: colors.border }}
                resizeMode="cover"
              />
              <Pressable
                onPress={() => setPendingImage(null)}
                hitSlop={8}
                style={{ backgroundColor: colors.muted, borderRadius: 12, padding: 3 }}
              >
                <Feather name="x" size={12} color={colors.mutedForeground} />
              </Pressable>
            </View>
          ) : null}

          {/* Pending document chip */}
          {pendingFile ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6, gap: 8 }}>
              <View style={{
                flex: 1, flexDirection: 'row', alignItems: 'center', gap: 7,
                backgroundColor: colors.primary + '12', borderRadius: 8,
                borderWidth: 1, borderColor: colors.primary + '30',
                paddingHorizontal: 10, paddingVertical: 6,
              }}>
                <Feather name="file-text" size={13} color={colors.primary} />
                <Text style={{ flex: 1, fontSize: 12, ...font('medium'), color: colors.foreground }} numberOfLines={1}>
                  {pendingFile.name}
                </Text>
                <Text style={{ fontSize: 10, ...font('regular'), color: colors.mutedForeground }}>
                  {pendingFile.text.length.toLocaleString()} chars
                </Text>
              </View>
              <Pressable
                onPress={() => setPendingFile(null)}
                hitSlop={8}
                style={{ backgroundColor: colors.muted, borderRadius: 12, padding: 3 }}
              >
                <Feather name="x" size={12} color={colors.mutedForeground} />
              </Pressable>
            </View>
          ) : null}

          {/* Row: deep toggle + image button + input + send */}
          <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 6 }}>
          {/* Deep mode toggle */}
          <Pressable
            onPress={() => setDeepMode((d) => !d)}
            style={[
              styles.deepToggle,
              deepMode
                ? { backgroundColor: colors.primary + '18', borderColor: colors.primary + '44' }
                : { backgroundColor: colors.muted, borderColor: colors.border },
            ]}
            hitSlop={6}
          >
            <Feather name="cpu" size={12} color={deepMode ? colors.primary : colors.mutedForeground} />
            <Text style={[styles.deepToggleText, { color: deepMode ? colors.primary : colors.mutedForeground }]}>
              {deepMode ? 'Deep' : 'Fast'}
            </Text>
          </Pressable>
          {/* Web search toggle — only when Tavily is configured */}
          {webSearchAvailable && (
            <Pressable
              onPress={handleWebSearchToggle}
              disabled={webSearchLoading || sending}
              hitSlop={6}
              style={[
                styles.deepToggle,
                webSearch
                  ? { backgroundColor: '#0891b218', borderColor: '#0891b244' }
                  : { backgroundColor: colors.muted, borderColor: colors.border },
              ]}
            >
              <Feather name="globe" size={12} color={webSearch ? '#0891b2' : colors.mutedForeground} />
              <Text style={[styles.deepToggleText, { color: webSearch ? '#0891b2' : colors.mutedForeground }]}>
                Web
              </Text>
            </Pressable>
          )}
          {/* Image attach button — opens action sheet: Photo Library or Take Photo */}
          <Pressable
            onPress={handleImageAttach}
            disabled={sending}
            hitSlop={6}
            style={[
              styles.deepToggle,
              pendingImage
                ? { backgroundColor: colors.primary + '18', borderColor: colors.primary + '44' }
                : { backgroundColor: colors.muted, borderColor: colors.border },
            ]}
          >
            <Feather
              name="image"
              size={14}
              color={pendingImage ? colors.primary : colors.mutedForeground}
            />
          </Pressable>
          {/* Document / file attach button */}
          <Pressable
            onPress={handleFileAttach}
            disabled={fileLoading || sending}
            hitSlop={6}
            style={[
              styles.deepToggle,
              pendingFile
                ? { backgroundColor: colors.primary + '18', borderColor: colors.primary + '44' }
                : { backgroundColor: colors.muted, borderColor: colors.border },
            ]}
          >
            {fileLoading
              ? <ActivityIndicator size="small" color={colors.mutedForeground} style={{ width: 14, height: 14 }} />
              : <Feather name="paperclip" size={14} color={pendingFile ? colors.primary : colors.mutedForeground} />
            }
          </Pressable>
          {/* Generate document button */}
          <Pressable
            onPress={handleDocGenPress}
            disabled={docGenLoading || sending}
            hitSlop={6}
            style={[
              styles.deepToggle,
              docGenLoading
                ? { backgroundColor: T.greenSoft, borderColor: alpha(T.green, 0.32) }
                : { backgroundColor: colors.muted, borderColor: colors.border },
            ]}
          >
            {docGenLoading
              ? <ActivityIndicator size="small" color={T.green} style={{ width: 14, height: 14 }} />
              : <Feather name="file-plus" size={14} color={docGenLoading ? T.green : colors.mutedForeground} />
            }
          </Pressable>
          <TextInput
            ref={inputRef}
            style={[
              styles.input,
              {
                backgroundColor: colors.card,
                borderColor: isError ? colors.border : colors.border,
                color: colors.foreground,
                fontFamily: 'Inter_400Regular',
              },
            ]}
            placeholder={isError && !initialized ? 'Server offline…' : 'Message…'}
            placeholderTextColor={colors.mutedForeground}
            value={text}
            onChangeText={setText}
            multiline
            maxLength={4000}
            returnKeyType="default"
            blurOnSubmit={false}
            editable={!isError || initialized}
          />
          <Pressable
            onPress={() => handleSend()}
            disabled={(!text.trim() && !pendingImage && !pendingFile) || sending || (isError && !initialized)}
            style={({ pressed }) => [
              styles.sendBtn,
              {
                backgroundColor:
                  (text.trim() || pendingImage || pendingFile) && !sending && (!isError || initialized)
                    ? colors.primary : colors.muted,
                opacity: pressed ? 0.7 : 1,
              },
            ]}
          >
            <Feather
              name="arrow-up"
              size={20}
              color={
                (text.trim() || pendingImage || pendingFile) && !sending && (!isError || initialized)
                  ? colors.primaryForeground
                  : colors.mutedForeground
              }
            />
          </Pressable>
          </View>{/* end row */}
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  modelBadgeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    paddingVertical: 6,
    borderBottomWidth: 1,
  },
  modelBadgeText: { fontSize: 11, fontFamily: 'Inter_500Medium' },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'flex-end',
  },
  modelSheet: {
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderWidth: 1,
    paddingTop: 16,
    paddingBottom: 32,
    maxHeight: '60%',
  },
  modelSheetTitle: {
    fontSize: 15,
    fontFamily: 'Inter_600SemiBold',
    textAlign: 'center',
    marginBottom: 12,
    paddingHorizontal: 16,
  },
  modelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 14,
    minHeight: 44,
    borderBottomWidth: 1,
    gap: 12,
  },
  modelLabel: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  modelDesc: { fontSize: 12, fontFamily: 'Inter_400Regular', marginTop: 2 },
  deepToggle: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 8, paddingVertical: 5, borderRadius: 7, borderWidth: 1,
    alignSelf: 'flex-end', marginBottom: 6,
  },
  deepToggleText: { fontSize: 11, fontFamily: 'Inter_600SemiBold' },
  listContent: { paddingHorizontal: 16, paddingVertical: 12 },
  bubbleRow: { flexDirection: 'row', alignItems: 'flex-end', marginBottom: 10, gap: 8 },
  bubbleLeft: {},
  bubbleRight: { flexDirection: 'row-reverse' },
  avatar: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  bubble: { borderRadius: 16, paddingHorizontal: 14, paddingVertical: 10 },
  bubbleText: { fontSize: 17, fontFamily: 'Inter_400Regular', lineHeight: 24 },
  emptyWrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 80,
    gap: 12,
  },
  emptyText: {
    fontSize: 14,
    fontFamily: 'Inter_400Regular',
    textAlign: 'center',
    paddingHorizontal: 40,
  },
  inputBar: {
    flexDirection: 'column',
    paddingHorizontal: 16,
    paddingTop: 10,
    borderTopWidth: 1,
  },
  input: {
    flex: 1,
    minHeight: 44,
    maxHeight: 120,
    borderRadius: 22,
    borderWidth: 1,
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 10,
    fontSize: 16,
  },
  sendBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  errorTitle: { fontSize: 16, fontFamily: 'Inter_600SemiBold', textAlign: 'center' },
  errorDetail: {
    fontSize: 13,
    fontFamily: 'Inter_400Regular',
    textAlign: 'center',
    lineHeight: 19,
    paddingHorizontal: 16,
  },
  retryBtn: {
    marginTop: 8,
    paddingHorizontal: 24,
    paddingVertical: 10,
    borderRadius: 20,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  retryBtnText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
});
