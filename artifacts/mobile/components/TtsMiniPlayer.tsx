/**
 * Sticky Read Aloud mini-player.
 *
 * Rendered at the root layout level (inside TtsProvider, sibling of the
 * Stack navigator) so it stays visible on every route — including
 * /library/[id] and any non-tab screen — not just within the tab navigator.
 *
 * When TTS is idle the component returns null and occupies zero height.
 * When active it renders as a flex row at the bottom of the root view,
 * pushing the navigator content up rather than overlaying it, so nothing
 * is hidden behind the bar.
 *
 * A settings gear opens TtsSettingsSheet so users can change voice/speed
 * without navigating back to the document detail page.  Voice/speed are
 * read from tts.session (always current after any applySettings call) and
 * persisted to AsyncStorage so the detail page stays in sync on next mount.
 */

import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useColors } from '@/hooks/useColors';
import { useTts } from '@/context/TtsContext';
import {
  TtsSettingsSheet,
  SPEED_LABELS,
  TTS_VOICE_KEY,
  TTS_SPEED_KEY,
  type TtsSpeed,
  SPEED_OPTIONS,
} from '@/components/TtsSettingsSheet';
import { VOICES } from '@/lib/voices';

export function TtsMiniPlayer() {
  const tts = useTts();
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [settingsOpen, setSettingsOpen] = useState(false);

  if (tts.playbackState === 'idle' || !tts.session) return null;

  const { session, index, playbackState } = tts;
  const totalParts = session.parts.length;

  // Read active voice/speed directly from the session — these are always
  // current after any applySettings() call, so no extra state load is needed.
  const voice = session.voice;
  const speed = session.speed as TtsSpeed;

  // Derived display: show "Voice · Speed" in the bar subtitle
  const voiceName = VOICES.find(v => v.id === voice)?.name ?? 'Voice';
  const speedLabel = SPEED_LABELS[speed] ?? '1×';

  /**
   * Called by TtsSettingsSheet when the user picks a new voice.
   * Applies immediately (clears cache, re-synthesises current part) and
   * persists to AsyncStorage so library/[id].tsx is in sync on next mount.
   */
  const handleVoiceChange = (v: string) => {
    AsyncStorage.setItem(TTS_VOICE_KEY, v).catch(() => {});
    tts.applySettings(v, speed);
  };

  /**
   * Called by TtsSettingsSheet when the user picks a new speed.
   * Same persistence + immediate-apply pattern as handleVoiceChange.
   */
  const handleSpeedChange = (s: TtsSpeed) => {
    AsyncStorage.setItem(TTS_SPEED_KEY, String(s)).catch(() => {});
    tts.applySettings(voice, s);
  };

  return (
    <>
      <View
        style={[
          styles.bar,
          {
            backgroundColor: colors.card,
            borderTopColor: colors.border,
            // Respect home-indicator / gesture bar at the bottom
            paddingBottom: insets.bottom > 0 ? insets.bottom : 10,
          },
        ]}
      >
        {/* Tap the left area to jump back to the document detail page */}
        <Pressable
          style={styles.info}
          onPress={() => router.push(`/library/${session.docId}` as any)}
          accessibilityRole="link"
          accessibilityLabel={`Return to ${session.docTitle}`}
        >
          <Feather
            name="headphones"
            size={14}
            color={colors.primary}
            style={{ marginRight: 8 }}
          />
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text
              style={[styles.title, { color: colors.foreground }]}
              numberOfLines={1}
            >
              {session.docTitle}
            </Text>
            <Text style={[styles.sub, { color: colors.mutedForeground }]}>
              {voiceName} · {speedLabel}
              {totalParts > 1 ? `  ·  Part ${index + 1}/${totalParts}` : ''}
            </Text>
          </View>
        </Pressable>

        {/* Playback controls */}
        <View style={styles.controls}>
          {/* ← Skip back — only shown when there are multiple parts */}
          {totalParts > 1 && (
            <Pressable
              onPress={() => tts.skipTo(index - 1)}
              disabled={index === 0}
              hitSlop={10}
              style={[styles.skipBtn, { opacity: index === 0 ? 0.3 : 1 }]}
              accessibilityRole="button"
              accessibilityLabel="Previous part"
              accessibilityState={{ disabled: index === 0 }}
            >
              <Feather name="skip-back" size={18} color={colors.primary} />
            </Pressable>
          )}

          {/* Play / Pause / Loading */}
          {playbackState === 'loading' ? (
            <ActivityIndicator size="small" color={colors.primary} />
          ) : playbackState === 'playing' ? (
            <Pressable
              onPress={tts.pause}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel="Pause"
            >
              <Feather name="pause" size={22} color={colors.primary} />
            </Pressable>
          ) : (
            <Pressable
              onPress={tts.resume}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel="Resume"
            >
              <Feather name="play" size={22} color={colors.primary} />
            </Pressable>
          )}

          {/* → Skip forward — only shown when there are multiple parts */}
          {totalParts > 1 && (
            <Pressable
              onPress={() => tts.skipTo(index + 1)}
              disabled={index >= totalParts - 1}
              hitSlop={10}
              style={[styles.skipBtn, { opacity: index >= totalParts - 1 ? 0.3 : 1 }]}
              accessibilityRole="button"
              accessibilityLabel="Next part"
              accessibilityState={{ disabled: index >= totalParts - 1 }}
            >
              <Feather name="skip-forward" size={18} color={colors.primary} />
            </Pressable>
          )}

          {/* Settings — opens voice/speed picker without going back to the doc */}
          <Pressable
            onPress={() => setSettingsOpen(true)}
            hitSlop={10}
            style={{ marginLeft: 14 }}
            accessibilityRole="button"
            accessibilityLabel={`Read Aloud settings — ${voiceName}, ${speedLabel}`}
          >
            <Feather name="settings" size={17} color={colors.mutedForeground} />
          </Pressable>

          {/* Stop */}
          <Pressable
            onPress={tts.stop}
            hitSlop={10}
            style={{ marginLeft: 12 }}
            accessibilityRole="button"
            accessibilityLabel="Stop Read Aloud"
          >
            <Feather name="x" size={20} color={colors.mutedForeground} />
          </Pressable>
        </View>
      </View>

      {/* Voice/speed picker sheet — rendered outside the bar View so the
          Modal can cover the full screen on all platforms */}
      <TtsSettingsSheet
        visible={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        voice={voice}
        onVoiceChange={handleVoiceChange}
        speed={speed}
        onSpeedChange={handleSpeedChange}
      />
    </>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  info: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    minWidth: 0,
    marginRight: 12,
  },
  title: {
    fontSize: 13,
    fontFamily: 'Inter_600SemiBold',
  },
  sub: {
    fontSize: 11,
    fontFamily: 'Inter_400Regular',
    marginTop: 1,
  },
  controls: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  skipBtn: {
    marginHorizontal: 10,
  },
});
