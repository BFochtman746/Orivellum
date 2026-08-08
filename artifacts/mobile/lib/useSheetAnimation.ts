/**
 * useSheetAnimation — shared spring-sheet animation hook.
 *
 * Encapsulates the rendered-gate + spring-slide + fade pattern used by
 * DiagnosticsSheet, HourlySheet, and every other bottom-sheet panel in
 * the app so the motion language is consistent project-wide.
 *
 * Usage:
 *   const { rendered, slideAnim, fadeAnim } = useSheetAnimation(visible, 400);
 *   if (!rendered) return null;
 *   return (
 *     <Modal transparent visible={rendered} animationType="none" ...>
 *       <Animated.View style={[absoluteFill, { opacity: fadeAnim }]}>
 *         <Pressable style={absoluteFill} onPress={onClose} />
 *       </Animated.View>
 *       <Animated.View style={[sheetStyle, { transform: [{ translateY: slideAnim }] }]}>
 *         ...
 *       </Animated.View>
 *     </Modal>
 *   );
 *
 * @param visible     controlled open/close boolean from the parent
 * @param sheetHeight estimated max height of the sheet in logical pixels;
 *                    err on the large side — it only affects the off-screen
 *                    start position, never the rendered size
 *
 * Race-safety: if the sheet is closed and immediately reopened before the
 * 220 ms exit animation completes, the stale exit callback is suppressed via
 * an exitGen counter so `rendered` is never incorrectly set to false while
 * `visible` is already true again.
 */

import { useEffect, useRef, useState } from 'react';
import { Animated } from 'react-native';

export function useSheetAnimation(visible: boolean, sheetHeight: number) {
  const [rendered, setRendered] = useState(false);
  const slideAnim = useRef(new Animated.Value(sheetHeight + 60)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;
  // Incremented whenever a new open begins, so any in-flight exit callback
  // can detect it has been superseded and skip setRendered(false).
  const exitGen   = useRef(0);

  useEffect(() => {
    if (visible) {
      // Invalidate any pending exit completion callback before re-mounting.
      exitGen.current += 1;
      setRendered(true);
      Animated.parallel([
        Animated.spring(slideAnim, {
          toValue: 0,
          useNativeDriver: true,
          tension: 85,
          friction: 13,
        }),
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 180,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      const gen = exitGen.current;
      Animated.parallel([
        Animated.timing(slideAnim, {
          toValue: sheetHeight + 60,
          duration: 220,
          useNativeDriver: true,
        }),
        Animated.timing(fadeAnim, {
          toValue: 0,
          duration: 180,
          useNativeDriver: true,
        }),
      ]).start(({ finished }) => {
        // Only unmount if the animation ran to completion AND no new open
        // has started since this exit was initiated.
        if (finished && exitGen.current === gen) {
          setRendered(false);
        }
      });
    }
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  return { rendered, slideAnim, fadeAnim };
}
