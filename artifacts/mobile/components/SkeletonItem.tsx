/**
 * SkeletonItem — shimmer loading placeholder.
 *
 * Use when data is loading instead of a bare ActivityIndicator.
 * Renders animated shimmer bars that mirror the shape of a list row.
 *
 * @example
 *   {isLoading ? (
 *     <>{[...Array(5)].map((_, i) => <SkeletonItem key={i} lines={2} />)}</>
 *   ) : <RealList />}
 */
import React, { useEffect, useRef } from 'react';
import { Animated, StyleSheet, View } from 'react-native';
import { useColors } from '@/hooks/useColors';

interface Props {
  /** Number of text lines to fake. Default 2. */
  lines?: number;
  /** Whether to show a leading icon placeholder. Default true. */
  icon?: boolean;
}

export function SkeletonItem({ lines = 2, icon = true }: Props) {
  const colors = useColors();
  const shimmer = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const anim = Animated.loop(
      Animated.sequence([
        Animated.timing(shimmer, {
          toValue: 1,
          duration: 900,
          useNativeDriver: true,
        }),
        Animated.timing(shimmer, {
          toValue: 0,
          duration: 900,
          useNativeDriver: true,
        }),
      ]),
    );
    anim.start();
    return () => anim.stop();
  }, [shimmer]);

  const opacity = shimmer.interpolate({
    inputRange: [0, 1],
    outputRange: [0.35, 0.65],
  });

  const bone = (width: `${number}%` | number, height = 13) => (
    <Animated.View
      style={[
        styles.bone,
        {
          width,
          height,
          borderRadius: height / 2,
          backgroundColor: colors.border,
          opacity,
        },
      ]}
    />
  );

  return (
    <View
      style={[styles.row, { borderBottomColor: colors.border }]}
      accessibilityElementsHidden
      importantForAccessibility="no"
    >
      {icon && (
        <Animated.View
          style={[
            styles.iconBone,
            { backgroundColor: colors.border, opacity },
          ]}
        />
      )}
      <View style={styles.lines}>
        {bone('72%', 14)}
        {lines >= 2 && <View style={styles.gap}>{bone('48%', 11)}</View>}
        {lines >= 3 && <View style={styles.gap}>{bone('58%', 11)}</View>}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 13,
    paddingHorizontal: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 12,
  },
  iconBone: {
    width: 36,
    height: 36,
    borderRadius: 10,
    flexShrink: 0,
  },
  lines: {
    flex: 1,
    gap: 6,
  },
  bone: {},
  gap: { marginTop: 0 },
});
