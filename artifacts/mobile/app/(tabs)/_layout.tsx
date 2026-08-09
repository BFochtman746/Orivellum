import React, { useCallback, useEffect, useRef, useState } from 'react';
import { mobileFetch } from '@/lib/api';
import {
  AccessibilityInfo,
  Animated,
  AppState,
  Easing,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
  useColorScheme,
} from 'react-native';
import Reanimated, {
  useSharedValue,
  withSpring,
  withSequence,
  withTiming,
  useAnimatedStyle,
} from 'react-native-reanimated';
import { useAudiobookJobActive } from '@/hooks/useAudiobookJobActive';
import { useColors } from '@/hooks/useColors';
import { useMailAttentionCount } from '@/hooks/useMailAttentionCount';
import { fontSerif } from '@/lib/typography';
import { Feather } from '@expo/vector-icons';
import { BlurView } from 'expo-blur';
import { LinearGradient } from 'expo-linear-gradient';
import { Tabs, usePathname, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  useGetSystemHealth,
  getGetSystemHealthQueryKey,
} from '@workspace/api-client-react';
import { apiOrigin } from '@/lib/server';

// ── Constants ──────────────────────────────────────────────────────────────────

const HEADER_HEIGHT = 56;
const SHEET_CONTENT_HEIGHT = 500;

// ── Audiobook generation pulsing dot ─────────────────────────────────────────

/**
 * A small animated dot shown next to the Studio nav item while an audiobook
 * job is in progress. Pulses between full and half opacity on a 900 ms loop.
 */
function PulsingDot() {
  const opacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    const anim = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 0.25,
          duration: 450,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 1,
          duration: 450,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
      ]),
    );
    anim.start();
    return () => anim.stop();
  }, [opacity]);

  return (
    <Animated.View
      style={[styles.pulsingDot, { opacity }]}
      accessibilityElementsHidden
      importantForAccessibility="no"
    />
  );
}

// ── Review queue badge ──────────────────────────────────────────────────────

const _REVIEW_DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const _REVIEW_API = () => `${_REVIEW_DOMAIN()}/api`;

/**
 * Polls GET /api/review/queue every 60 s and returns the pending item count.
 * Used to drive the red badge on the menu button (native) and Works tab (web).
 */
function useReviewCount(): number {
  const [count, setCount] = useState(0);
  const poll = useCallback(async () => {
    try {
      const r = await mobileFetch(`${_REVIEW_API()}/review/queue`);
      if (r.ok) {
        const data = await r.json();
        setCount((data.count as number) ?? 0);
      }
    } catch {
      // silently fail — badge just won't update until next poll
    }
  }, []);
  useEffect(() => {
    poll();
    const t = setInterval(poll, 60_000);
    return () => clearInterval(t);
  }, [poll]);
  return count;
}


// ── Server status ──────────────────────────────────────────────────────────────

function useServerDotColor(): string {
  const { data, isError } = useGetSystemHealth({
    query: {
      queryKey: getGetSystemHealthQueryKey(),
      refetchInterval: 15_000,
      staleTime: 10_000,
      retry: false,
    },
  });
  if (isError) return '#ef4444';
  if (data?.status !== 'ok') return '#f59e0b';
  return '#22c55e';
}

// ── Reduce-motion guard ────────────────────────────────────────────────────────

/**
 * Returns true when the user has enabled "Reduce Motion" in system settings.
 * All spring/scale animations must be skipped when this is active.
 */
function useReduceMotion(): boolean {
  const [reduceMotion, setReduceMotion] = useState(false);
  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion);
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => sub.remove();
  }, []);
  return reduceMotion;
}

// ── Current section label ──────────────────────────────────────────────────────

function useSectionLabel(): string {
  const path = usePathname();
  if (path === '/' || path.endsWith('/index')) return 'Dashboard';
  if (path.includes('/conversations')) return 'Chats';
  if (path.includes('/books')) return 'Books';
  if (path.includes('/learn')) return 'Learn';
  if (path.includes('/projects')) return 'Projects';
  if (path.includes('/intake')) return 'Load Anything';
  if (path.includes('/review')) return 'Review';
  if (path.includes('/backups')) return 'Backups';
  if (path.includes('/graph')) return 'Knowledge Graph';
  if (path.includes('/topics')) return 'Topic Graph';
  if (path.includes('/governance')) return 'Governance';
  if (path.includes('/mcos')) return 'MCOS';
  if (path.includes('/system')) return 'System';
  if (path.includes('/studio')) return 'Studio';
  if (path.includes('/write')) return 'Write';
  if (path.includes('/actions')) return 'Actions';
  if (path.includes('/forge')) return 'Forge';
  if (path.includes('/works')) return 'Works';
  if (path.includes('/library')) return 'Library';
  if (path.includes('/memory')) return 'Memory';
  return 'Orivellum';
}

// ── App header ─────────────────────────────────────────────────────────────────

interface AppHeaderProps {
  onMenuPress: () => void;
  reviewCount?: number;
}

function AppHeader({ onMenuPress, reviewCount = 0 }: AppHeaderProps) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const dotColor = useServerDotColor();
  const section = useSectionLabel();
  const colorScheme = useColorScheme();
  const isDark = colorScheme === 'dark';

  return (
    <View
      style={[
        styles.header,
        {
          height: HEADER_HEIGHT + insets.top,
          paddingTop: insets.top,
          borderBottomColor: colors.border,
        },
      ]}
    >
      {/* Blur background on iOS */}
      {Platform.OS === 'ios' && (
        <BlurView
          intensity={80}
          tint={isDark ? 'dark' : 'light'}
          style={StyleSheet.absoluteFill}
        />
      )}
      {/* Solid background on Android */}
      {Platform.OS !== 'ios' && (
        <View
          style={[StyleSheet.absoluteFill, { backgroundColor: colors.background }]}
        />
      )}

      {/* iOS 26 Liquid Glass — top specular shimmer strip */}
      {Platform.OS === 'ios' && (
        <LinearGradient
          colors={[
            isDark ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.22)',
            isDark ? 'rgba(255,255,255,0.03)' : 'rgba(255,255,255,0.06)',
            'rgba(255,255,255,0)',
          ]}
          locations={[0, 0.3, 1]}
          start={{ x: 0, y: 0 }}
          end={{ x: 0, y: 1 }}
          style={{
            position: 'absolute',
            left: 0, right: 0, top: 0,
            height: 2.5,
            zIndex: 10,
          }}
        />
      )}

      {/* iOS 26 Liquid Glass — bottom edge shimmer */}
      {Platform.OS === 'ios' && (
        <LinearGradient
          colors={[
            isDark ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.30)',
            'rgba(255,255,255,0)',
          ]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 0 }}
          style={{
            position: 'absolute',
            left: 0, right: 0,
            bottom: 0,
            height: StyleSheet.hairlineWidth + 0.5,
            zIndex: 10,
          }}
        />
      )}

      {/* Content row */}
      <View style={styles.headerRow}>
        {/* App wordmark */}
        <Text style={[styles.headerWordmark, { color: colors.primary, ...fontSerif('bold') }]}>
          Orivellum
        </Text>

        {/* Section title (center) */}
        <Text
          style={[styles.headerSection, { color: colors.foreground }]}
          numberOfLines={1}
        >
          {section}
        </Text>

        {/* Right: server dot + menu button (+ review badge when > 0) */}
        <View style={styles.headerRight}>
          <View style={[styles.serverDot, { backgroundColor: dotColor }]} />
          <View style={{ position: 'relative' }}>
            <TouchableOpacity
              onPress={onMenuPress}
              hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
              style={styles.menuButton}
              accessibilityLabel={
                reviewCount > 0
                  ? `Open navigation (${reviewCount} review item${reviewCount !== 1 ? 's' : ''} pending)`
                  : 'Open navigation'
              }
              accessibilityRole="button"
            >
              <Feather name="menu" size={22} color={colors.foreground} />
            </TouchableOpacity>
            {reviewCount > 0 && (
              <View
                style={styles.reviewBadge}
                accessibilityElementsHidden
                importantForAccessibility="no"
              >
                <Text style={styles.reviewBadgeText}>
                  {reviewCount > 99 ? '99+' : String(reviewCount)}
                </Text>
              </View>
            )}
          </View>
        </View>
      </View>
    </View>
  );
}

// ── Nav bottom sheet ───────────────────────────────────────────────────────────

interface NavItem {
  key: string;
  label: string;
  icon: string;
  route: string;
}

const NAV_ITEMS: NavItem[] = [
  { key: 'index',         label: 'Dashboard', icon: 'home',           route: '/'              },
  { key: 'intake',        label: 'Load',      icon: 'inbox',          route: '/intake'        },
  { key: 'conversations', label: 'Chats',     icon: 'message-circle', route: '/conversations' },
  { key: 'works',         label: 'Works',     icon: 'book-open',      route: '/works'         },
  { key: 'books',         label: 'Books',     icon: 'book',           route: '/books'         },
  { key: 'learn',         label: 'Learn',     icon: 'award',          route: '/learn'         },
  { key: 'projects',      label: 'Projects',  icon: 'compass',        route: '/projects'      },
  { key: 'library',       label: 'Library',   icon: 'folder',         route: '/library'       },
  { key: 'forge',         label: 'Forge',     icon: 'globe',          route: '/forge'         },
  { key: 'studio',        label: 'Studio',    icon: 'mic',            route: '/studio'        },
  { key: 'write',         label: 'Write',     icon: 'edit-3',         route: '/write'         },
  { key: 'actions',       label: 'Actions',   icon: 'zap',            route: '/actions'       },
  { key: 'mail',          label: 'Mail',      icon: 'mail',           route: '/mail'          },
  { key: 'graph',         label: 'Graph',     icon: 'share-2',        route: '/graph'         },
  { key: 'review',        label: 'Review',    icon: 'shield',         route: '/review'        },
  { key: 'topics',        label: 'Topics',    icon: 'layers',         route: '/topics'        },
  { key: 'governance',    label: 'Governance', icon: 'shield-off',    route: '/governance'    },
  { key: 'mcos',          label: 'MCOS',      icon: 'bar-chart-2',    route: '/mcos'          },
  { key: 'system',        label: 'System',    icon: 'settings',       route: '/system'        },
  { key: 'backups',       label: 'Backups',   icon: 'hard-drive',     route: '/backups'       },
];

function currentRoute(path: string): string {
  if (path.includes('/conversations')) return '/conversations';
  if (path.includes('/books')) return '/books';
  if (path.includes('/learn')) return '/learn';
  if (path.includes('/projects')) return '/projects';
  if (path.includes('/intake')) return '/intake';
  if (path.includes('/review')) return '/review';
  if (path.includes('/studio')) return '/studio';
  if (path.includes('/write')) return '/write';
  if (path.includes('/actions')) return '/actions';
  if (path.includes('/mail')) return '/mail';
  if (path.includes('/graph')) return '/graph';
  if (path.includes('/topics')) return '/topics';
  if (path.includes('/governance')) return '/governance';
  if (path.includes('/mcos')) return '/mcos';
  if (path.includes('/system')) return '/system';
  if (path.includes('/backups')) return '/backups';
  if (path.includes('/forge')) return '/forge';
  if (path.includes('/works')) return '/works';
  if (path.includes('/library')) return '/library';
  return '/';
}

interface NavBottomSheetProps {
  visible: boolean;
  onClose: () => void;
  /** True while a background audiobook job exists — shows a pulsing dot on Studio. */
  audiobookActive?: boolean;
  /** High-attention mail count — shows a red badge on the Mail item when > 0. */
  mailAttentionCount?: number;
}

// ── Animated nav item ──────────────────────────────────────────────────────────

/**
 * A single row in the NavBottomSheet that spring-animates its icon on selection:
 *   • Tapping while inactive: icon pulses 1 → 1.15 → 1 (spring, tension 200 / friction 10)
 *   • Route becoming active: same pulse + label fades to full opacity over 120 ms
 *   • Route becoming inactive: icon springs from 1.15 → 1, label fades to 72 %
 *   • reduceMotion=true: no animation; static colors only
 */
function AnimatedNavItem({
  item,
  isActive,
  showAudiobookDot,
  badgeCount = 0,
  onPress,
  reduceMotion,
  colors,
}: {
  item: NavItem;
  isActive: boolean;
  showAudiobookDot: boolean;
  badgeCount?: number;
  onPress: () => void;
  reduceMotion: boolean;
  colors: ReturnType<typeof useColors>;
}) {
  const scale        = useSharedValue(1);
  const labelOpacity = useSharedValue(isActive ? 1 : 0.72);
  // Ref tracks previous active state so the effect only fires on changes.
  const prevActive = useRef(isActive);

  useEffect(() => {
    const wasActive = prevActive.current;
    prevActive.current = isActive;

    if (reduceMotion) {
      scale.value        = 1;
      labelOpacity.value = isActive ? 1 : 0.72;
      return;
    }

    if (isActive && !wasActive) {
      // Newly selected → pulse icon and fade label in
      scale.value        = withSequence(
        withSpring(1.15, { stiffness: 200, damping: 10 }),
        withSpring(1.0,  { stiffness: 200, damping: 12 }),
      );
      labelOpacity.value = withTiming(1, { duration: 120 });
    } else if (!isActive && wasActive) {
      // Deselected → spring icon back to rest, dim label
      scale.value        = withSpring(1, { stiffness: 200, damping: 12 });
      labelOpacity.value = withTiming(0.72, { duration: 120 });
    }
  }, [isActive, reduceMotion]);

  const iconAnimStyle  = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
  }));
  const labelAnimStyle = useAnimatedStyle(() => ({
    opacity: labelOpacity.value,
  }));

  const handlePress = () => {
    // Immediate scale pulse on tap — gives instant tactile feedback before
    // the route change propagates and re-triggers the effect above.
    if (!reduceMotion && !isActive) {
      scale.value = withSequence(
        withSpring(1.15, { stiffness: 200, damping: 10 }),
        withSpring(1.0,  { stiffness: 200, damping: 12 }),
      );
    }
    onPress();
  };

  return (
    <Pressable
      onPress={handlePress}
      style={({ pressed }) => [
        styles.navItem,
        {
          backgroundColor: isActive
            ? `${colors.primary}14`
            : pressed
            ? `${colors.muted}80`
            : 'transparent',
        },
      ]}
      accessibilityRole="menuitem"
      accessibilityLabel={
        showAudiobookDot
          ? `${item.label} (audiobook generating)`
          : item.label
      }
      accessibilityState={{ selected: isActive }}
    >
      {/* Icon container — Reanimated.View drives the spring scale */}
      <View style={{ position: 'relative' }}>
        <Reanimated.View
          style={[
            styles.navIconWrap,
            {
              backgroundColor: isActive
                ? `${colors.primary}1A`
                : colors.muted,
            },
            iconAnimStyle,
          ]}
        >
          <Feather
            name={item.icon as any}
            size={20}
            color={isActive ? colors.primary : colors.mutedForeground}
          />
        </Reanimated.View>
        {showAudiobookDot && <PulsingDot />}
        {badgeCount > 0 && (
          <View
            style={styles.navBadge}
            accessibilityElementsHidden
            importantForAccessibility="no"
          >
            <Text style={styles.navBadgeText}>
              {badgeCount > 99 ? '99+' : String(badgeCount)}
            </Text>
          </View>
        )}
      </View>

      {/* Label — Reanimated.Text drives the opacity fade */}
      <Reanimated.Text
        style={[
          styles.navLabel,
          {
            color:      isActive ? colors.primary : colors.foreground,
            fontFamily: isActive ? 'Inter_600SemiBold' : 'Inter_400Regular',
          },
          labelAnimStyle,
        ]}
      >
        {item.label}
      </Reanimated.Text>

      {/* Right side: check mark (active) or "Generating" badge (audiobook) */}
      {isActive ? (
        <View style={styles.navCheck}>
          <Feather name="check" size={15} color={colors.primary} />
        </View>
      ) : showAudiobookDot ? (
        <Text style={[styles.audiobookBadgeLabel, { color: '#f97316' }]}>
          Generating
        </Text>
      ) : null}
    </Pressable>
  );
}

function NavBottomSheet({ visible, onClose, audiobookActive = false, mailAttentionCount = 0 }: NavBottomSheetProps) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const path = usePathname();
  const reduceMotion = useReduceMotion();

  // Keep mounted during close animation
  const [rendered, setRendered] = useState(visible);
  const slideAnim = useRef(new Animated.Value(SHEET_CONTENT_HEIGHT + 60)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
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
      // Close: overdamped spring (tension 180, friction 26) instead of the
      // old linear 220 ms timing.  High friction prevents any overshoot while
      // the higher tension makes the sheet snap away faster, giving the same
      // physical feel as the open spring.
      Animated.parallel([
        Animated.spring(slideAnim, {
          toValue: SHEET_CONTENT_HEIGHT + 60,
          useNativeDriver: true,
          tension: 180,
          friction: 26,
        }),
        Animated.timing(fadeAnim, {
          toValue: 0,
          duration: 180,
          useNativeDriver: true,
        }),
      ]).start(() => setRendered(false));
    }
  }, [visible]);

  const handleNav = (route: string) => {
    onClose();
    setTimeout(() => router.navigate(route as any), 80);
  };

  const active = currentRoute(path);

  if (!rendered) return null;

  return (
    <Modal
      transparent
      visible={rendered}
      animationType="none"
      onRequestClose={onClose}
      statusBarTranslucent
    >
      {/* Backdrop */}
      <Animated.View
        style={[styles.backdrop, { opacity: fadeAnim }]}
        pointerEvents={visible ? 'auto' : 'none'}
      >
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      {/* Sheet */}
      <Animated.View
        style={[
          styles.sheet,
          {
            backgroundColor: colors.card,
            borderColor: colors.border,
            paddingBottom: insets.bottom + 16,
            transform: [{ translateY: slideAnim }],
          },
        ]}
      >
        {/* Drag handle */}
        <View style={[styles.sheetHandle, { backgroundColor: colors.border }]} />

        <Text style={[styles.sheetTitle, { color: colors.foreground }]}>
          Orivellum
        </Text>

        <Text style={[styles.sheetLabel, { color: colors.mutedForeground }]}>
          Navigate to
        </Text>

        {NAV_ITEMS.map((item) => {
          const isActive = item.route === active;
          const showAudiobookDot = audiobookActive && item.key === 'studio';
          const badgeCount = item.key === 'mail' ? mailAttentionCount : 0;
          return (
            <AnimatedNavItem
              key={item.key}
              item={item}
              isActive={isActive}
              showAudiobookDot={showAudiobookDot}
              badgeCount={badgeCount}
              onPress={() => handleNav(item.route)}
              reduceMotion={reduceMotion}
              colors={colors}
            />
          );
        })}
      </Animated.View>
    </Modal>
  );
}

// ── Audiobook progress banner ─────────────────────────────────────────────────

const PROGRESS_ORANGE = '#f97316';

/**
 * Compact banner rendered below the AppHeader while an audiobook is generating.
 * Shows a thin orange progress bar and "Narrating chapter N of M — WorkTitle" text.
 * Tapping it navigates to /studio.
 */
function AudiobookProgressBanner({
  chapterIdx,
  totalChapters,
  workTitle,
  onPress,
}: {
  chapterIdx: number;
  totalChapters: number;
  workTitle: string;
  onPress: () => void;
}) {
  const colors  = useColors();
  // chapterIdx is the count of chapters *completed*; display it as the chapter
  // currently being narrated (1-based) until all are done.
  const current = Math.min(chapterIdx + 1, Math.max(totalChapters, 1));
  const pct     = totalChapters > 0
    ? Math.min(100, Math.max(2, (chapterIdx / totalChapters) * 100))
    : 2; // indeterminate — show a thin sliver

  const label = totalChapters > 0
    ? `Narrating chapter ${current} of ${totalChapters}${workTitle ? ` — ${workTitle}` : ''}`
    : `Narrating${workTitle ? ` — ${workTitle}` : ''}…`;

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${label}. Tap to open Studio.`}
      style={[
        styles.progressBanner,
        { backgroundColor: colors.background, borderBottomColor: colors.border },
      ]}
    >
      {/* Thin progress track */}
      <View style={[styles.progressTrack, { backgroundColor: `${PROGRESS_ORANGE}22` }]}>
        <View
          style={[styles.progressFill, { width: `${pct}%`, backgroundColor: PROGRESS_ORANGE }]}
        />
      </View>

      {/* Text row */}
      <View style={styles.progressTextRow}>
        <Feather name="mic" size={11} color={PROGRESS_ORANGE} />
        <Text
          style={[styles.progressLabel, { color: colors.mutedForeground }]}
          numberOfLines={1}
        >
          {label}
        </Text>
        <Feather name="chevron-right" size={11} color={colors.mutedForeground} />
      </View>
    </Pressable>
  );
}

// ── Audiobook ready banner ────────────────────────────────────────────────────

const READY_GREEN = '#22c55e';

/**
 * Compact banner shown for ~8 s after a background audiobook job finishes.
 * Replaces the progress banner in the same slot; tapping navigates to /studio.
 */
function AudiobookReadyBanner({ onPress }: { onPress: () => void }) {
  const colors = useColors();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel="Your audiobook is ready. Tap to open Studio."
      style={[
        styles.progressBanner,
        { backgroundColor: colors.background, borderBottomColor: colors.border },
      ]}
    >
      {/* Solid green bar — signals 100 % complete */}
      <View style={[styles.progressTrack, { backgroundColor: `${READY_GREEN}22` }]}>
        <View style={[styles.progressFill, { width: '100%', backgroundColor: READY_GREEN }]} />
      </View>

      <View style={styles.progressTextRow}>
        <Feather name="check-circle" size={11} color={READY_GREEN} />
        <Text
          style={[styles.progressLabel, { color: colors.mutedForeground }]}
          numberOfLines={1}
        >
          Your audiobook is ready — tap to play
        </Text>
        <Feather name="chevron-right" size={11} color={colors.mutedForeground} />
      </View>
    </Pressable>
  );
}

// ── Native layout (iOS / Android) — no tab bar ────────────────────────────────

/** Auto-dismiss duration for the "ready" banner (ms of foreground time). */
const READY_BANNER_MS = 8_000;

function NativeAppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const reviewCount        = useReviewCount();
  const mailAttentionCount = useMailAttentionCount();
  const audiobookProgress  = useAudiobookJobActive();
  const router            = useRouter();

  // 8 s auto-dismiss timer — only runs while the app is in the foreground so
  // the banner doesn't silently expire while the user is in another app.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!audiobookProgress.justCompleted) {
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
      return;
    }

    const tryStart = () => {
      if (timerRef.current) return; // already ticking
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        audiobookProgress.dismissReady();
      }, READY_BANNER_MS);
    };

    // Start immediately if already foregrounded; otherwise wait for focus.
    if (AppState.currentState === 'active') tryStart();

    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') {
        tryStart();
      } else {
        // App moved to background — pause the timer so the full 8 s is
        // shown when the user actually returns.
        if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
      }
    });

    return () => {
      sub.remove();
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    };
  }, [audiobookProgress.justCompleted, audiobookProgress.dismissReady]);

  const handleReadyPress = () => {
    audiobookProgress.dismissReady();
    router.navigate('/studio' as any);
  };

  return (
    <View style={{ flex: 1 }}>
      <AppHeader
        onMenuPress={() => setMenuOpen(true)}
        reviewCount={reviewCount}
      />

      {/* Ready banner — shown briefly after successful background completion */}
      {audiobookProgress.justCompleted && !audiobookProgress.active && (
        <AudiobookReadyBanner onPress={handleReadyPress} />
      )}

      {/* Progress banner — visible only while a background audiobook job is active */}
      {audiobookProgress.active && !audiobookProgress.justCompleted && (
        <AudiobookProgressBanner
          chapterIdx={audiobookProgress.chapterIdx}
          totalChapters={audiobookProgress.totalChapters}
          workTitle={audiobookProgress.workTitle}
          onPress={() => router.navigate('/studio' as any)}
        />
      )}

      <Tabs
        screenOptions={{
          headerShown: false,
          tabBarStyle: { display: 'none' },
        }}
      >
        <Tabs.Screen name="index" />
        <Tabs.Screen name="intake" />
        <Tabs.Screen name="conversations" />
        <Tabs.Screen name="works" />
        <Tabs.Screen name="books" />
        <Tabs.Screen name="learn" />
        <Tabs.Screen name="projects" />
        <Tabs.Screen name="library" />
        <Tabs.Screen name="forge" />
        <Tabs.Screen name="mcos" />
        <Tabs.Screen name="write" />
        <Tabs.Screen name="actions" />
      </Tabs>
      <NavBottomSheet
        visible={menuOpen}
        onClose={() => setMenuOpen(false)}
        audiobookActive={audiobookProgress.active}
        mailAttentionCount={mailAttentionCount}
      />
    </View>
  );
}

// ── Animated tab bar icon (web) ────────────────────────────────────────────────

/**
 * Wraps a Feather icon in a React Native core Animated.View for the web tab bar.
 *
 * On native the NavBottomSheet uses Reanimated sharedValues; here we use the
 * react-native Animated API which translates to CSS transitions via
 * react-native-web — reliably supported without extra Reanimated web config.
 *
 * When the tab is selected the icon springs 1 → 1.15 → 1.
 * When deselected mid-animation it snaps back to 1 immediately.
 * All motion is suppressed when reduceMotion=true.
 */
function AnimatedTabIcon({
  name,
  color,
  size,
  focused,
  reduceMotion,
}: {
  name: string;
  color: string;
  size: number;
  focused: boolean;
  reduceMotion: boolean;
}) {
  const scale      = useRef(new Animated.Value(1)).current;
  const prevFocused = useRef(focused);

  useEffect(() => {
    const wasActive = prevFocused.current;
    prevFocused.current = focused;

    if (reduceMotion) {
      scale.setValue(1);
      return;
    }

    if (focused && !wasActive) {
      // Tab selected — spring pulse up then back
      Animated.sequence([
        Animated.spring(scale, {
          toValue: 1.15,
          useNativeDriver: true,
          speed: 28,
          bounciness: 10,
        }),
        Animated.spring(scale, {
          toValue: 1.0,
          useNativeDriver: true,
          speed: 22,
          bounciness: 4,
        }),
      ]).start();
    } else if (!focused && wasActive) {
      // Tab deselected mid-animation — spring immediately back to rest
      Animated.spring(scale, {
        toValue: 1.0,
        useNativeDriver: true,
        speed: 22,
        bounciness: 4,
      }).start();
    }
  }, [focused, reduceMotion]);

  return (
    <Animated.View style={{ transform: [{ scale }] }}>
      <Feather name={name as any} size={size} color={color} />
    </Animated.View>
  );
}

// ── Web layout — classic tab bar ───────────────────────────────────────────────

function WebTabLayout() {
  const colors             = useColors();
  const insets             = useSafeAreaInsets();
  const reviewCount        = useReviewCount();
  const mailAttentionCount = useMailAttentionCount();
  const reduceMotion       = useReduceMotion();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.mutedForeground,
        tabBarStyle: {
          position: 'absolute',
          backgroundColor: colors.background,
          borderTopWidth: 1,
          borderTopColor: colors.border,
          elevation: 0,
          height: 84,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Dashboard',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="home" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="conversations"
        options={{
          title: 'Chats',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="message-circle" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="works"
        options={{
          title: 'Works',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="book-open" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
          tabBarBadge: reviewCount > 0 ? reviewCount : undefined,
        }}
      />
      <Tabs.Screen
        name="intake"
        options={{
          title: 'Load',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="inbox" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="books"
        options={{
          title: 'Books',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="book" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="learn"
        options={{
          title: 'Learn',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="award" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="library"
        options={{
          title: 'Library',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="folder" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="write"
        options={{
          title: 'Write',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="edit-3" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="actions"
        options={{
          title: 'Actions',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="zap" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
        }}
      />
      <Tabs.Screen
        name="mail"
        options={{
          title: 'Mail',
          tabBarIcon: ({ color, size, focused }) => (
            <AnimatedTabIcon name="mail" color={color} size={size} focused={focused} reduceMotion={reduceMotion} />
          ),
          tabBarBadge: mailAttentionCount > 0 ? mailAttentionCount : undefined,
        }}
      />
    </Tabs>
  );
}

// ── Export ────────────────────────────────────────────────────────────────────

export default function TabLayout() {
  if (Platform.OS === 'web') {
    return <WebTabLayout />;
  }
  return <NativeAppLayout />;
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  // Header
  header: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    zIndex: 10,
  },
  headerRow: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingBottom: 10,
  },
  headerWordmark: {
    fontSize: 12,
    fontFamily: 'Inter_600SemiBold',
    letterSpacing: 1,
    textTransform: 'uppercase',
    width: 80,
  },
  headerSection: {
    flex: 1,
    fontSize: 17,
    fontFamily: 'Inter_600SemiBold',
    textAlign: 'center',
  },
  headerRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    width: 80,
    justifyContent: 'flex-end',
  },
  serverDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
  },
  menuButton: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },

  // Backdrop
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.42)',
  },

  // Bottom sheet
  sheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingTop: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.12,
    shadowRadius: 14,
    elevation: 24,
  },
  sheetHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    alignSelf: 'center',
    marginBottom: 18,
    marginTop: 6,
  },
  sheetTitle: {
    fontSize: 22,
    fontFamily: 'Fraunces_700Bold',
    fontWeight: '700',
    letterSpacing: -0.3,
    paddingHorizontal: 4,
    marginBottom: 4,
  },
  sheetLabel: {
    fontSize: 11,
    fontFamily: 'Inter_500Medium',
    letterSpacing: 0.9,
    textTransform: 'uppercase',
    paddingHorizontal: 4,
    marginBottom: 8,
  },

  // Nav items
  navItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 11,
    paddingHorizontal: 10,
    borderRadius: 12,
    gap: 14,
    marginBottom: 4,
    minHeight: 44,
  },
  navIconWrap: {
    width: 40,
    height: 40,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  navLabel: {
    fontSize: 16,
    flex: 1,
  },
  navCheck: {
    width: 24,
    alignItems: 'center',
  },

  // Review badge — red dot on the menu button
  reviewBadge: {
    position: 'absolute',
    top: 7,
    right: 7,
    minWidth: 15,
    height: 15,
    borderRadius: 8,
    backgroundColor: '#ef4444',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 2,
  },
  reviewBadgeText: {
    color: '#fff',
    fontSize: 9,
    fontFamily: 'Inter_700Bold',
    lineHeight: 10,
  },

  // Mail attention badge — red dot on the Mail nav icon in the bottom sheet
  navBadge: {
    position: 'absolute',
    top: -3,
    right: -3,
    minWidth: 15,
    height: 15,
    borderRadius: 8,
    backgroundColor: '#ef4444',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 2,
  },
  navBadgeText: {
    color: '#fff',
    fontSize: 9,
    fontFamily: 'Inter_700Bold',
    lineHeight: 10,
  },

  // Audiobook generation badge — pulsing orange dot on the Studio nav icon
  pulsingDot: {
    position: 'absolute',
    top: -3,
    right: -3,
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#f97316',
    borderWidth: 1.5,
    borderColor: 'transparent',
  },
  // "Generating" label shown to the right of the Studio nav item label
  audiobookBadgeLabel: {
    fontSize: 10,
    fontFamily: 'Inter_600SemiBold',
    letterSpacing: 0.3,
  },

  // Audiobook progress banner (below AppHeader during background generation)
  progressBanner: {
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  progressTrack: {
    height: 3,
    width: '100%',
    overflow: 'hidden',
  },
  progressFill: {
    height: 3,
    borderRadius: 2,
  },
  progressTextRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
  progressLabel: {
    flex: 1,
    fontSize: 11,
    fontFamily: 'Inter_400Regular',
  },
});
