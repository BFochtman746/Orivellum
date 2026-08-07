/**
 * Shared TTS settings bottom sheet.
 *
 * Used by:
 *   - artifacts/mobile/app/library/[id].tsx (document detail page)
 *   - artifacts/mobile/components/TtsMiniPlayer.tsx (global mini-player)
 *
 * The sheet itself is stateless — callers own the voice/speed values and
 * the open/close toggle, so both surfaces stay in sync through their own
 * persistence + TtsContext.applySettings() calls.
 */

import React from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { VOICES } from '@/lib/voices';

// ── Shared constants ─────────────────────────────────────────────────────────

export const SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5] as const;
export type TtsSpeed = typeof SPEED_OPTIONS[number];

export const SPEED_LABELS: Record<number, string> = {
  0.75: '0.75×', 1.0: '1×', 1.25: '1.25×', 1.5: '1.5×',
};

/** AsyncStorage keys — match the web implementation for cross-platform consistency. */
export const TTS_VOICE_KEY = 'orivellum:tts_voice';
export const TTS_SPEED_KEY = 'orivellum:tts_speed';

// ── Component ────────────────────────────────────────────────────────────────

export function TtsSettingsSheet({
  visible,
  onClose,
  voice,
  onVoiceChange,
  speed,
  onSpeedChange,
}: {
  visible: boolean;
  onClose: () => void;
  voice: string;
  onVoiceChange: (v: string) => void;
  speed: TtsSpeed;
  onSpeedChange: (s: TtsSpeed) => void;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <View style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.45)' }}>
        {/* Tap backdrop to close */}
        <Pressable style={{ flex: 1 }} onPress={onClose} />

        <View
          style={{
            backgroundColor: colors.card,
            borderTopLeftRadius: 22,
            borderTopRightRadius: 22,
            borderWidth: 1,
            borderColor: colors.border,
            padding: 16,
            paddingBottom: insets.bottom + 20,
          }}
        >
          {/* Drag handle */}
          <View style={{ width: 36, height: 4, borderRadius: 2, backgroundColor: colors.border, alignSelf: 'center', marginBottom: 14 }} />

          {/* Header */}
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7 }}>
              <Feather name="headphones" size={16} color={colors.primary} />
              <Text style={{ fontSize: 17, fontFamily: 'Inter_700Bold', color: colors.foreground }}>
                Read Aloud Settings
              </Text>
            </View>
            <Pressable onPress={onClose} hitSlop={10}>
              <Feather name="x" size={20} color={colors.mutedForeground} />
            </Pressable>
          </View>

          {/* Speed picker */}
          <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.6, marginBottom: 8 }}>
            SPEED
          </Text>
          <View style={{ flexDirection: 'row', gap: 8, marginBottom: 22 }}>
            {SPEED_OPTIONS.map(s => {
              const active = s === speed;
              return (
                <Pressable
                  key={s}
                  onPress={() => onSpeedChange(s)}
                  style={{
                    flex: 1,
                    paddingVertical: 10,
                    borderRadius: 8,
                    borderWidth: 1,
                    alignItems: 'center',
                    borderColor: active ? colors.primary : colors.border,
                    backgroundColor: active ? colors.primary + '18' : 'transparent',
                  }}
                >
                  <Text
                    style={{
                      fontSize: 13,
                      fontFamily: active ? 'Inter_700Bold' : 'Inter_400Regular',
                      color: active ? colors.primary : colors.foreground,
                    }}
                  >
                    {SPEED_LABELS[s]}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          {/* Voice picker */}
          <Text style={{ fontSize: 11, fontFamily: 'Inter_600SemiBold', color: colors.mutedForeground, letterSpacing: 0.6, marginBottom: 8 }}>
            VOICE
          </Text>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ gap: 8, paddingBottom: 4 }}
          >
            {VOICES.map(v => {
              const active = v.id === voice;
              const accentColor = v.accent === 'british' ? '#3b82f6' : '#f59e0b';
              const genderSym = v.gender === 'feminine' ? '♀' : v.gender === 'masculine' ? '♂' : '';
              return (
                <Pressable
                  key={v.id}
                  onPress={() => onVoiceChange(v.id)}
                  style={{
                    width: 84,
                    padding: 10,
                    borderRadius: 10,
                    borderWidth: 1,
                    borderColor: active ? colors.primary : colors.border,
                    backgroundColor: active ? colors.primary + '15' : colors.background,
                    gap: 4,
                  }}
                >
                  <Text
                    style={{
                      fontSize: 13,
                      fontFamily: active ? 'Inter_700Bold' : 'Inter_600SemiBold',
                      color: active ? colors.primary : colors.foreground,
                    }}
                    numberOfLines={1}
                  >
                    {v.name}
                  </Text>
                  <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: accentColor }}>
                    {v.accent === 'american' ? 'US' : v.accent === 'british' ? 'UK' : (v.accent ?? '')}
                    {genderSym ? ` · ${genderSym}` : ''}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>

          {/* Changes take effect immediately when audio is active, or on the
              next Listen if nothing is playing. */}
          <Text style={{ fontSize: 11, fontFamily: 'Inter_400Regular', color: colors.mutedForeground, marginTop: 14, textAlign: 'center' }}>
            Changes apply immediately — or on the next Listen
          </Text>
        </View>
      </View>
    </Modal>
  );
}
