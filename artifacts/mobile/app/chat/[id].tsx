import React, { useRef, useState } from 'react';
import { mobileFetch } from '@/lib/api';
import {
  ActionSheetIOS,
  ActivityIndicator,
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
import Markdown from 'react-native-markdown-display';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import {
  useGetConversation,
  useGetSystemModels,
  useUpdateConversation,
} from '@workspace/api-client-react';
import { useLocalSearchParams, useNavigation } from 'expo-router';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useEffect } from 'react';
import * as Haptics from 'expo-haptics';
import * as Clipboard from 'expo-clipboard';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { Message } from '@workspace/api-client-react';
import { OfflineBanner } from '@/components/OfflineBanner';

const LAST_MODEL_KEY = 'orivellum:lastModel';

type LocalMessage = Message & { isError?: boolean; localImageUri?: string };

function MessageBubble({ message, colors, isDark }: { message: LocalMessage; colors: any; isDark: boolean }) {
  const isUser = message.role === 'user';
  const isErr = (message as any).isError;
  const textColor = isUser ? colors.primaryForeground : isErr ? colors.mutedForeground : colors.foreground;
  const [copied, setCopied] = useState(false);

  const handleLongPress = async () => {
    if (!message.text) return;
    await Clipboard.setStringAsync(message.text);
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const markdownStyles = {
    body: { color: textColor, fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 21 },
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
      style={[styles.bubbleRow, isUser ? styles.bubbleRight : styles.bubbleLeft]}
    >
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
              {/* Attached image thumbnail (user messages only) */}
              {isUser && (message as LocalMessage).localImageUri ? (
                <Image
                  source={{ uri: (message as LocalMessage).localImageUri }}
                  style={{ width: 180, height: 180, borderRadius: 8, marginBottom: message.text && message.text !== '[Image attached]' ? 6 : 0 }}
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
        {/* Model attribution — shown on assistant messages when meta.model is set */}
        {!isUser && !isErr && (message as any).meta?.model && (
          <Text style={{
            fontSize: 9,
            fontFamily: 'Inter_400Regular',
            color: colors.mutedForeground,
            marginTop: 3,
            opacity: 0.6,
            letterSpacing: 0.2,
          }}>
            {String((message as any).meta.model).split('/').pop()}
          </Text>
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

function TypingIndicator({ colors }: { colors: any }) {
  return (
    <View style={[styles.bubbleRow, styles.bubbleLeft]}>
      <View style={[styles.avatar, { backgroundColor: colors.primary }]}>
        <Feather name="cpu" size={12} color={colors.primaryForeground} />
      </View>
      <View style={[styles.bubble, { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1 }]}>
        <ActivityIndicator size="small" color={colors.mutedForeground} />
      </View>
    </View>
  );
}

export default function ChatScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const colorScheme = useColorScheme();
  const isDark = colorScheme === 'dark';
  const { id, draft } = useLocalSearchParams<{ id: string; draft?: string }>();
  const navigation = useNavigation();
  const inputRef = useRef<TextInput>(null);

  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([]);
  const [initialized, setInitialized] = useState(false);
  const [sendFailed, setSendFailed] = useState(false);
  const [modelPickerVisible, setModelPickerVisible] = useState(false);
  const [deepMode, setDeepMode] = useState(false);
  // Image attachment state
  const [pendingImage, setPendingImage] = useState<{
    uri: string;
    base64: string;
    mediaType: string;
  } | null>(null);

  const { data, isLoading, isError, refetch } = useGetConversation(id, { query: { staleTime: 10_000 } } as any);
  const conversation = data?.conversation;
  const serverMessages = data?.messages ?? [];

  const { data: modelsData } = useGetSystemModels();
  const models = modelsData?.models ?? [];
  const updateConv = useUpdateConversation();

  const currentModelId = (conversation as any)?.model;
  const currentModelLabel =
    models.find((m: any) => m.id === currentModelId)?.label ?? currentModelId ?? 'Default';

  // Persist last-used model so new conversations default to it (#69)
  useEffect(() => {
    if (currentModelId) {
      AsyncStorage.setItem(LAST_MODEL_KEY, currentModelId).catch(() => {});
    }
  }, [currentModelId]);

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

  // Sync server messages into local state on first load
  useEffect(() => {
    if (!initialized && serverMessages.length > 0) {
      setLocalMessages(serverMessages);
      setInitialized(true);
    } else if (!initialized && !isLoading) {
      setInitialized(true);
    }
  }, [serverMessages, isLoading, initialized]);

  // #40 — When the server comes back (isError flips false), clear send-failure state
  //        so the composer re-enables automatically without requiring a manual retry.
  const prevIsErrorRef = useRef(false);
  useEffect(() => {
    if (prevIsErrorRef.current && !isError && initialized) {
      setSendFailed(false);
      // Purge optimistic error bubbles so the conversation looks clean after recovery
      setLocalMessages((prev) => prev.filter((m) => !(m as any).isError));
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

  // ── Send message (with optional image) ────────────────────────────────────
  const handleSend = async () => {
    const trimmed = text.trim();
    // Allow send with image even when text is empty
    if ((!trimmed && !pendingImage) || sending) return;

    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setText('');
    setSendFailed(false);

    // Capture and clear pending image before the async path
    const imageToSend = pendingImage;
    setPendingImage(null);

    // Build display text for the optimistic message
    const displayText = imageToSend
      ? (trimmed ? `[Image] ${trimmed}` : '[Image attached]')
      : trimmed;

    const userMsg: LocalMessage = {
      id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
      conversation_id: id,
      role: 'user',
      text: displayText,
      created_at: new Date().toISOString(),
      // Keep local URI for thumbnail display; never sent to server
      localImageUri: imageToSend?.uri,
    };

    setLocalMessages((prev) => [...prev, userMsg]);
    setSending(true);

    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const url = `https://${domain}/api/conversations/${id}/messages`;
      const payload: Record<string, unknown> = {
        text: trimmed || (imageToSend ? 'What is in this image?' : ''),
        stream: false,
        deep: deepMode,
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
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const topPad = isWeb ? 67 : insets.top + 44;

  // Full-screen loading
  if (isLoading && !initialized) {
    return (
      <View style={[styles.screen, styles.centered, { backgroundColor: colors.background }]}>
        <ActivityIndicator color={colors.primary} />
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
          data={displayMessages}
          keyExtractor={(m) => m.id ?? ''}
          renderItem={({ item }) => <MessageBubble message={item} colors={colors} isDark={isDark} />}
          inverted
          contentContainerStyle={styles.listContent}
          keyboardDismissMode="interactive"
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          ListHeaderComponent={sending ? <TypingIndicator colors={colors} /> : null}
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
          {/* Image attach button */}
          <Pressable
            onPress={pickImage}
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
            onPress={handleSend}
            disabled={(!text.trim() && !pendingImage) || sending || (isError && !initialized)}
            style={({ pressed }) => [
              styles.sendBtn,
              {
                backgroundColor:
                  (text.trim() || pendingImage) && !sending && (!isError || initialized)
                    ? colors.primary : colors.muted,
                opacity: pressed ? 0.7 : 1,
              },
            ]}
          >
            <Feather
              name="arrow-up"
              size={20}
              color={
                (text.trim() || pendingImage) && !sending && (!isError || initialized)
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
    paddingHorizontal: 20,
    paddingVertical: 14,
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
  bubbleText: { fontSize: 15, fontFamily: 'Inter_400Regular', lineHeight: 21 },
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
    paddingHorizontal: 12,
    paddingTop: 10,
    borderTopWidth: 1,
  },
  input: {
    flex: 1,
    minHeight: 42,
    maxHeight: 120,
    borderRadius: 21,
    borderWidth: 1,
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 10,
    fontSize: 15,
  },
  sendBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
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
  },
  retryBtnText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
});
