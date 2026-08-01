import React, { useRef, useState } from 'react';
import {
  ActionSheetIOS,
  ActivityIndicator,
  FlatList,
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
import type { Message } from '@workspace/api-client-react';
import { OfflineBanner } from '@/components/OfflineBanner';

function MessageBubble({ message, colors, isDark }: { message: Message & { isError?: boolean }; colors: any; isDark: boolean }) {
  const isUser = message.role === 'user';
  const isErr = (message as any).isError;
  const textColor = isUser ? colors.primaryForeground : isErr ? colors.mutedForeground : colors.foreground;

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

  return (
    <View style={[styles.bubbleRow, isUser ? styles.bubbleRight : styles.bubbleLeft]}>
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
          { maxWidth: '80%' },
        ]}
      >
        {isUser || isErr ? (
          <Text
            style={[
              styles.bubbleText,
              { color: textColor, fontStyle: isErr ? 'italic' : 'normal' },
            ]}
          >
            {message.text}
          </Text>
        ) : (
          <Markdown style={markdownStyles as any}>{message.text ?? ''}</Markdown>
        )}
      </View>
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
  const { id } = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const inputRef = useRef<TextInput>(null);

  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [localMessages, setLocalMessages] = useState<(Message & { isError?: boolean })[]>([]);
  const [initialized, setInitialized] = useState(false);
  const [sendFailed, setSendFailed] = useState(false);
  const [modelPickerVisible, setModelPickerVisible] = useState(false);

  const { data, isLoading, isError, refetch } = useGetConversation(id, { query: { staleTime: 10_000 } } as any);
  const conversation = data?.conversation;
  const serverMessages = data?.messages ?? [];

  const { data: modelsData } = useGetSystemModels();
  const models = modelsData?.models ?? [];
  const updateConv = useUpdateConversation();

  const currentModelId = (conversation as any)?.model;
  const currentModelLabel =
    models.find((m: any) => m.id === currentModelId)?.label ?? currentModelId ?? 'Default';

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

  useEffect(() => {
    navigation.setOptions({ title: conversation?.title || 'Conversation' });
  }, [conversation?.title, navigation]);

  const displayMessages = [...localMessages].reverse();

  const handleSend = async () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setText('');
    setSendFailed(false);

    const userMsg: Message = {
      id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
      conversation_id: id,
      role: 'user',
      text: trimmed,
      created_at: new Date().toISOString(),
    };

    setLocalMessages((prev) => [...prev, userMsg]);
    setSending(true);

    try {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const url = `https://${domain}/api/conversations/${id}/messages`;
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: trimmed, stream: false }),
      });

      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      const body = await resp.json();
      const aiMsg: Message = body.message;
      if (aiMsg) {
        setLocalMessages((prev) => [...prev, aiMsg]);
      }
    } catch (err) {
      const isNetworkError =
        err instanceof TypeError && err.message.toLowerCase().includes('network');
      const errMsg: Message & { isError: boolean } = {
        id: Date.now().toString() + 'err',
        conversation_id: id,
        role: 'assistant',
        text: isNetworkError
          ? 'Cannot reach the server. Check your connection and try again.'
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
        {/* Model badge row */}
        {models.length > 0 && (
          <Pressable
            onPress={handlePickModel}
            style={({ pressed }) => [
              styles.modelBadgeRow,
              { borderBottomColor: colors.border, opacity: pressed ? 0.7 : 1 },
            ]}
          >
            <Feather name="cpu" size={11} color={colors.mutedForeground} />
            <Text style={[styles.modelBadgeText, { color: colors.mutedForeground }]}>
              {currentModelLabel}
            </Text>
            <Feather name="chevron-down" size={11} color={colors.mutedForeground} />
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
            placeholder={isError ? 'Server offline…' : 'Message…'}
            placeholderTextColor={colors.mutedForeground}
            value={text}
            onChangeText={setText}
            multiline
            maxLength={4000}
            returnKeyType="default"
            blurOnSubmit={false}
            editable={!isError}
          />
          <Pressable
            onPress={handleSend}
            disabled={!text.trim() || sending || isError}
            style={({ pressed }) => [
              styles.sendBtn,
              {
                backgroundColor:
                  text.trim() && !sending && !isError ? colors.primary : colors.muted,
                opacity: pressed ? 0.7 : 1,
              },
            ]}
          >
            <Feather
              name="arrow-up"
              size={20}
              color={
                text.trim() && !sending && !isError
                  ? colors.primaryForeground
                  : colors.mutedForeground
              }
            />
          </Pressable>
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
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 10,
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
