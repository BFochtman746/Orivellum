import React, { useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useGetConversation } from '@workspace/api-client-react';
import { useLocalSearchParams, useNavigation } from 'expo-router';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useEffect } from 'react';
import * as Haptics from 'expo-haptics';
import type { Message } from '@workspace/api-client-react';
import { OfflineBanner } from '@/components/OfflineBanner';

function MessageBubble({ message, colors }: { message: Message & { isError?: boolean }; colors: any }) {
  const isUser = message.role === 'user';
  const isErr = (message as any).isError;
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
        <Text
          style={[
            styles.bubbleText,
            {
              color: isUser
                ? colors.primaryForeground
                : isErr
                ? colors.mutedForeground
                : colors.foreground,
              fontStyle: isErr ? 'italic' : 'normal',
            },
          ]}
        >
          {message.text}
        </Text>
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
  const { id } = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const inputRef = useRef<TextInput>(null);

  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [localMessages, setLocalMessages] = useState<(Message & { isError?: boolean })[]>([]);
  const [initialized, setInitialized] = useState(false);
  const [sendFailed, setSendFailed] = useState(false);

  const { data, isLoading, isError, refetch } = useGetConversation(id);
  const conversation = data?.conversation;
  const serverMessages = data?.messages ?? [];

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
      <View style={{ flex: 1, paddingTop: topPad }}>
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
          renderItem={({ item }) => <MessageBubble message={item} colors={colors} />}
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
