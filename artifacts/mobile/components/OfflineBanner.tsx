import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';

interface OfflineBannerProps {
  message?: string;
  onRetry?: () => void;
}

/**
 * Shown when a network request fails. Sits flush below the header.
 * If onRetry is provided, shows a "Retry" tap target.
 */
export function OfflineBanner({ message = "Can't reach the server", onRetry }: OfflineBannerProps) {
  const colors = useColors();
  return (
    <View style={[styles.banner, { backgroundColor: colors.card, borderBottomColor: colors.border }]}>
      <Feather name="wifi-off" size={13} color={colors.mutedForeground} />
      <Text style={[styles.text, { color: colors.mutedForeground }]}>{message}</Text>
      {onRetry && (
        <Pressable onPress={onRetry} hitSlop={8}>
          <Text style={[styles.retry, { color: colors.primary }]}>Retry</Text>
        </Pressable>
      )}
    </View>
  );
}

/**
 * Full-screen error state — use when the screen has no data to show at all.
 */
export function ErrorScreen({
  message = 'Could not load data',
  detail,
  onRetry,
}: {
  message?: string;
  detail?: string;
  onRetry?: () => void;
}) {
  const colors = useColors();
  return (
    <View style={styles.centered}>
      <Feather name="wifi-off" size={40} color={colors.mutedForeground} />
      <Text style={[styles.errorTitle, { color: colors.foreground }]}>{message}</Text>
      {detail && (
        <Text style={[styles.errorDetail, { color: colors.mutedForeground }]}>{detail}</Text>
      )}
      {onRetry && (
        <Pressable
          onPress={onRetry}
          style={[styles.retryBtn, { backgroundColor: colors.primary }]}
        >
          <Text style={[styles.retryBtnText, { color: colors.primaryForeground }]}>Try again</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderBottomWidth: 1,
  },
  text: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular' },
  retry: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 32 },
  errorTitle: { fontSize: 16, fontFamily: 'Inter_600SemiBold', textAlign: 'center' },
  errorDetail: { fontSize: 13, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 19 },
  retryBtn: {
    marginTop: 8,
    paddingHorizontal: 24,
    paddingVertical: 10,
    borderRadius: 20,
  },
  retryBtnText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
});
