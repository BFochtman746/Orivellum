import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { font, fontSerif } from '@/lib/typography';

export default function NotFoundScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  return (
    <View style={[styles.container, { backgroundColor: colors.background, paddingBottom: insets.bottom + 24 }]}>
      <View style={[styles.iconWrap, { backgroundColor: colors.muted }]}>
        <Feather name="compass" size={28} color={colors.mutedForeground} />
      </View>

      <Text style={[styles.title, { color: colors.foreground }]}>Page not found</Text>
      <Text style={[styles.body, { color: colors.mutedForeground }]}>
        This screen doesn&apos;t exist. You may have followed a broken link.
      </Text>

      <Pressable
        onPress={() => router.replace('/' as any)}
        style={({ pressed }) => [
          styles.btn,
          { backgroundColor: colors.primary, opacity: pressed ? 0.8 : 1 },
        ]}
        accessibilityRole="button"
        accessibilityLabel="Go to Dashboard"
      >
        <Feather name="home" size={16} color="#fff" />
        <Text style={styles.btnText}>Go to Dashboard</Text>
      </Pressable>

      <Pressable
        onPress={() => router.canGoBack() ? router.back() : router.replace('/' as any)}
        style={({ pressed }) => [
          styles.backLink,
          { opacity: pressed ? 0.6 : 1 },
        ]}
        accessibilityRole="button"
        accessibilityLabel="Go back"
      >
        <Feather name="arrow-left" size={14} color={colors.primary} />
        <Text style={[styles.backLinkText, { color: colors.primary }]}>Go back</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
    gap: 12,
  },
  iconWrap: {
    width: 72, height: 72, borderRadius: 20,
    alignItems: 'center', justifyContent: 'center',
    marginBottom: 8,
  },
  title: {
    fontSize: 22,
    lineHeight: 28,
    ...fontSerif('bold'),
    textAlign: 'center',
  },
  body: {
    fontSize: 15,
    lineHeight: 22,
    ...font('regular'),
    textAlign: 'center',
    maxWidth: 280,
  },
  btn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 13,
    paddingHorizontal: 28,
    borderRadius: 12,
    minHeight: 48,
    marginTop: 8,
  },
  btnText: {
    color: '#fff',
    fontSize: 15,
    lineHeight: 22,
    ...font('semibold'),
  },
  backLink: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    padding: 10,
    minHeight: 44,
  },
  backLinkText: {
    fontSize: 14,
    lineHeight: 20,
    ...font('medium'),
  },
});
