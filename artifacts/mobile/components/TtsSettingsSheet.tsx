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
  Animated,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { VOICES } from '@/lib/voices';
import { useSheetAnimation } from '@/lib/useSheetAnimation';

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
  const { rendered, slideAnim, fadeAnim } = useSheetAnimation(visible, 460);

  if (!rendered) return null;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose}>
      {/* Animated backdrop */}
      <Animated.View style={[StyleSheet.absoluteFill, styles.backdrop, { opacity: fadeAnim }]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      {/* Animated sheet */}
      <Animated.View
        style={[
          styles.sheet,
          {
            backgroundColor: colors.card,
            borderTopColor: colors.border,
            paddingBottom: insets.bottom + 20,
            transform: [{ translateY: slideAnim }],
          },
        ]}
      >
        {/* Drag handle */}
        <View style={[styles.handle, { backgroundColor: colors.border }]} />

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
      </Animated.View>
    </Modal>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  backdrop: {
    backgroundColor: 'rgba(0,0,0,0.45)',
  },
  sheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    borderTopWidth: 1,
    padding: 16,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    alignSelf: 'center',
    marginBottom: 14,
  },
});
