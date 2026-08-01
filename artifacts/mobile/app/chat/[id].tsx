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

function MessageBubble({ message, colors }: { message: Message; colors: any }) {
  const isUser = message.role === 'user';
  return (
    <View style={[styles.bubbleRow, isUser ? styles.bubbleRight : styles.bubbleLeft]}>
      {!isUser && (
        <View style={[styles.avatar, { backgroundColor: colors.primary }]}>
          <Feather name="cpu" size={12} color={colors.primaryForeground} />
        </View>
      )}
      <View
        style={[
          styles.bubble,
          isUser
            ? { backgroundColor: colors.primary, borderBottomRightRadius: 2 }
            : { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderBottomLeftRadius: 2 },
          { maxWidth: '80%' },
        ]}
      >
        <Text
          style={[
            styles.bubbleText,
            { color: isUser ? colors.primaryForeground : colors.foreground },
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
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [initialized, setInitialized] = useState(false);

  const { data, isLoading, refetch } = useGetConversation(id);
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
    if (conversation?.title) {
      navigation.setOptions({ title: conversation.title });
    } else {
      navigation.setOptions({ title: 'Conversation' });
    }
  }, [conversation?.title, navigation]);

  const displayMessages = [...localMessages].reverse();

  const handleSend = async () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    setText('');

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

      if (!resp.ok) throw new Error('Failed to send');
      const body = await resp.json();
      const aiMsg: Message = body.message;
      if (aiMsg) {
        setLocalMessages((prev) => [...prev, aiMsg]);
      }
    } catch {
      // Optimistically keep user message, show error inline
      const errMsg: Message = {
        id: Date.now().toString() + 'err',
        conversation_id: id,
        role: 'assistant',
        text: 'Sorry, something went wrong. Please try again.',
        created_at: new Date().toISOString(),
      };
      setLocalMessages((prev) => [...prev, errMsg]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const topPad = isWeb ? 67 : insets.top + 44;

  return (
    <KeyboardAvoidingView
      style={[styles.screen, { backgroundColor: colors.background }]}
      behavior="padding"
      keyboardVerticalOffset={0}
    >
      <View style={{ flex: 1, paddingTop: topPad }}>
        {isLoading && !initialized ? (
          <View style={styles.centered}>
            <ActivityIndicator color={colors.primary} />
          </View>
        ) : (
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
                    Ask anything about your research
                  </Text>
                </View>
              ) : null
            }
          />
        )}

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
                borderColor: colors.border,
                color: colors.foreground,
                fontFamily: 'Inter_400Regular',
              },
            ]}
            placeholder="Message…"
            placeholderTextColor={colors.mutedForeground}
            value={text}
            onChangeText={setText}
            multiline
            maxLength={4000}
            returnKeyType="default"
            blurOnSubmit={false}
          />
          <Pressable
            onPress={handleSend}
            disabled={!text.trim() || sending}
            style={({ pressed }) => [
              styles.sendBtn,
              {
                backgroundColor:
                  text.trim() && !sending ? colors.primary : colors.muted,
                opacity: pressed ? 0.7 : 1,
              },
            ]}
          >
            <Feather
              name="arrow-up"
              size={20}
              color={text.trim() && !sending ? colors.primaryForeground : colors.mutedForeground}
            />
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
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
});
