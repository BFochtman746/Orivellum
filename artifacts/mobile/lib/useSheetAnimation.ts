/**
 * useSheetAnimation — shared spring-sheet animation hook.
 *
 * Encapsulates the rendered-gate + spring-slide + fade pattern used by every
 * bottom-sheet panel in the app so the motion language is consistent project-wide.
 *
 * Usage:
 *   const { rendered, slideAnim, fadeAnim, panHandlers, scrollHandler } =
 *     useSheetAnimation(visible, 400, onClose);
 *   if (!rendered) return null;
 *   return (
 *     <Modal transparent visible={rendered} animationType="none" ...>
 *       <Animated.View style={[absoluteFill, { opacity: fadeAnim }]}>
 *         <Pressable style={absoluteFill} onPress={onClose} />
 *       </Animated.View>
 *       <Animated.View
 *         {...panHandlers}
 *         style={[sheetStyle, { transform: [{ translateY: slideAnim }] }]}
 *       >
 *         // For sheets with a vertical ScrollView or FlatList, attach
 *         // scrollHandler so the capture guard knows the scroll position
 *         // and only intercepts when the list is at the very top:
 *         // <ScrollView onScroll={scrollHandler} scrollEventThrottle={16}>
 *       </Animated.View>
 *     </Modal>
 *   );
 *
 * @param visible     controlled open/close boolean from the parent
 * @param sheetHeight estimated max height of the sheet in logical pixels;
 *                    err on the large side — it only affects the off-screen
 *                    start position, never the rendered size
 * @param onClose     called when the user swipes the sheet down past the
 *                    dismiss threshold (or with a fast flick); spread
 *                    `panHandlers` onto the sheet's Animated.View to enable
 *                    the gesture
 *
 * Race-safety: if the sheet is closed and immediately reopened before the
 * 220 ms exit animation completes, the stale exit callback is suppressed via
 * an exitGen counter so `rendered` is never incorrectly set to false while
 * `visible` is already true again.
 *
 * Swipe-to-dismiss with nested ScrollView:
 *   The PanResponder uses onMoveShouldSetPanResponderCapture (capture phase,
 *   fires before ScrollView's bubble-phase onMoveShouldSetResponder) to claim
 *   the gesture when the list is at the top (scrollY ≤ 1 px). Pass
 *   `scrollHandler` to the ScrollView/FlatList's `onScroll` prop so the hook
 *   tracks the current position. Sheets without a scrollable list work
 *   identically because scrollY stays at 0 permanently.
 */

import { useEffect, useRef, useState } from 'react';
import { Animated, PanResponder } from 'react-native';

/** Downward drag distance (logical px) that triggers auto-dismiss on release. */
const DISMISS_THRESHOLD = 120;
/** PanResponder vy (units/ms) — a fast flick dismisses even on a short drag. */
const DISMISS_VELOCITY  = 0.5;

export function useSheetAnimation(
  visible: boolean,
  sheetHeight: number,
  onClose?: () => void,
) {
  const [rendered, setRendered] = useState(false);
  const slideAnim = useRef(new Animated.Value(sheetHeight + 60)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;
  // Incremented whenever a new open begins, so any in-flight exit callback
  // can detect it has been superseded and skip setRendered(false).
  const exitGen   = useRef(0);

  // Keep stable refs so the PanResponder (created once in useRef) always
  // calls the latest onClose and uses the latest sheetHeight — both can
  // change between renders without the PanResponder being recreated.
  const onCloseRef     = useRef(onClose);
  const sheetHeightRef = useRef(sheetHeight);
  useEffect(() => { onCloseRef.current = onClose; },       [onClose]);
  useEffect(() => { sheetHeightRef.current = sheetHeight; }, [sheetHeight]);

  // Tracks the vertical scroll offset of any nested ScrollView / FlatList.
  // The capture-phase handler reads this to decide whether to intercept
  // (scrolled to top) or yield (scrolled down).  Reset on every open so
  // a freshly-opened sheet always has the dismiss gesture available.
  const scrollY = useRef(0);

  // Stable scroll event handler — attach to the ScrollView / FlatList inside
  // the sheet via: onScroll={scrollHandler} scrollEventThrottle={16}
  // The ref ensures the function identity never changes between renders,
  // so ScrollView's event listener is not re-attached on every render.
  const scrollHandler = useRef(
    (e: { nativeEvent: { contentOffset: { y: number } } }) => {
      scrollY.current = e.nativeEvent.contentOffset.y;
    },
  ).current;

  const panResponder = useRef(
    PanResponder.create({
      // ── Capture phase ────────────────────────────────────────────────────
      // Fires top-down BEFORE any child (ScrollView, FlatList, Pressable)
      // can claim the gesture via their own bubble-phase handlers.
      // Only intercept when:
      //   (a) the nested list is scrolled to the very top (≤1 px handles
      //       sub-pixel float imprecision from the native layer), AND
      //   (b) the gesture is predominantly downward.
      // Sheets with no ScrollView always have scrollY===0, so (a) is
      // trivially satisfied and the gesture behaves identically.
      onMoveShouldSetPanResponderCapture: (_, { dy, dx }) =>
        scrollY.current <= 1 && dy > 8 && Math.abs(dy) > Math.abs(dx) * 1.5,

      // ── Bubble phase fallback ─────────────────────────────────────────────
      // Catches downward drags on non-scrollable areas of the sheet (the
      // drag handle, header, empty space) in case the capture check missed.
      onMoveShouldSetPanResponder: (_, { dy, dx }) =>
        dy > 8 && Math.abs(dy) > Math.abs(dx) * 1.5,

      onPanResponderMove: (_, { dy }) => {
        // Only allow dragging DOWN (positive dy → positive translateY).
        if (dy > 0) slideAnim.setValue(dy);
      },

      onPanResponderRelease: (_, { dy, vy }) => {
        if (dy > DISMISS_THRESHOLD || vy > DISMISS_VELOCITY) {
          // Dismiss: slide off-screen + fade backdrop, then signal the parent.
          // We set rendered=false immediately so the Modal unmounts without
          // waiting for the parent to set visible=false (which would otherwise
          // run a redundant but harmless invisible exit animation).
          Animated.parallel([
            Animated.timing(slideAnim, {
              toValue: sheetHeightRef.current + 60,
              duration: 180,
              useNativeDriver: true,
            }),
            Animated.timing(fadeAnim, {
              toValue: 0,
              duration: 150,
              useNativeDriver: true,
            }),
          ]).start(({ finished }) => {
            if (finished) {
              setRendered(false);
              onCloseRef.current?.();
            }
          });
        } else {
          // Below threshold — spring back to the fully-open position.
          Animated.spring(slideAnim, {
            toValue: 0,
            useNativeDriver: true,
            tension: 85,
            friction: 13,
          }).start();
        }
      },

      // Another responder claimed the gesture — snap back without dismissing.
      onPanResponderTerminate: () => {
        Animated.spring(slideAnim, {
          toValue: 0,
          useNativeDriver: true,
          tension: 85,
          friction: 13,
        }).start();
      },
    })
  ).current;

  useEffect(() => {
    if (visible) {
      // Reset scroll position so the dismiss gesture is always available
      // when the sheet first opens, regardless of what the list showed last time.
      scrollY.current = 0;
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

  return {
    rendered,
    slideAnim,
    fadeAnim,
    panHandlers: panResponder.panHandlers,
    scrollHandler,
  };
}
