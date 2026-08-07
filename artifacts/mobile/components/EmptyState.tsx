/**
 * EmptyState — editorial zero-data placeholder.
 *
 * Centered icon + headline + optional body text + optional CTA.
 * Uses VELLUM token colors and Fraunces-inspired sizing.
 *
 * @example
 *   <EmptyState
 *     icon="book-open"
 *     title="No works yet"
 *     body="Import documents to create your first Work."
 *     cta="Load something"
 *     onCta={() => router.push('/intake')}
 *   />
 */
import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { font } from '@/lib/typography';

interface Props {
  icon: string;
  title: string;
  body?: string;
  cta?: string;
  onCta?: () => void;
}

export function EmptyState({ icon, title, body, cta, onCta }: Props) {
  const colors = useColors();

  return (
    <View style={styles.wrap} accessibilityLiveRegion="polite">
      {/* Icon circle */}
      <View style={[styles.iconWrap, { backgroundColor: colors.muted }]}>
        <Feather name={icon as any} size={26} color={colors.mutedForeground} />
      </View>

      {/* Headline */}
      <Text style={[styles.title, { color: colors.foreground }]}>{title}</Text>

      {/* Body */}
      {!!body && (
        <Text style={[styles.body, { color: colors.mutedForeground }]}>{body}</Text>
      )}

      {/* CTA */}
      {!!cta && !!onCta && (
        <Pressable
          onPress={onCta}
          style={({ pressed }) => [
            styles.cta,
            { backgroundColor: colors.primary, opacity: pressed ? 0.8 : 1 },
          ]}
          accessibilityRole="button"
        >
          <Text style={[styles.ctaText, { color: '#fff' }]}>{cta}</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
    paddingVertical: 48,
    gap: 12,
  },
  iconWrap: {
    width: 56,
    height: 56,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 4,
  },
  title: {
    fontSize: 18,
    lineHeight: 24,
    textAlign: 'center',
    ...font('semibold'),
  },
  body: {
    fontSize: 14,
    lineHeight: 20,
    textAlign: 'center',
    ...font('regular'),
  },
  cta: {
    marginTop: 8,
    paddingHorizontal: 20,
    paddingVertical: 11,
    borderRadius: 10,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaText: {
    fontSize: 15,
    ...font('semibold'),
  },
});
