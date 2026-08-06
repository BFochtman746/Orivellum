import React, { useCallback, useEffect, useRef, useState } from 'react';
import { mobileFetch } from '@/lib/api';
import {
  Animated,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
  useColorScheme,
} from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { BlurView } from 'expo-blur';
import { Tabs, usePathname, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  useGetSystemHealth,
  getGetSystemHealthQueryKey,
} from '@workspace/api-client-react';

// ── Constants ──────────────────────────────────────────────────────────────────

const HEADER_HEIGHT = 56;
const SHEET_CONTENT_HEIGHT = 500;

// ── Review queue badge ──────────────────────────────────────────────────────

const _REVIEW_DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const _REVIEW_API = `https://${_REVIEW_DOMAIN}/api`;

/**
 * Polls GET /api/review/queue every 60 s and returns the pending item count.
 * Used to drive the red badge on the menu button (native) and Works tab (web).
 */
function useReviewCount(): number {
  const [count, setCount] = useState(0);
  const poll = useCallback(async () => {
    try {
      const r = await mobileFetch(`${_REVIEW_API}/review/queue`);
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

// ── Current section label ──────────────────────────────────────────────────────

function useSectionLabel(): string {
  const path = usePathname();
  if (path === '/' || path.endsWith('/index')) return 'Dashboard';
  if (path.includes('/conversations')) return 'Chats';
  if (path.includes('/books')) return 'Books';
  if (path.includes('/learn')) return 'Learn';
  if (path.includes('/intake')) return 'Load Anything';
  if (path.includes('/review')) return 'Review';
  if (path.includes('/backups')) return 'Backups';
  if (path.includes('/graph')) return 'Knowledge Graph';
  if (path.includes('/topics')) return 'Topic Graph';
  if (path.includes('/governance')) return 'Governance';
  if (path.includes('/system')) return 'System';
  if (path.includes('/works')) return 'Works';
  if (path.includes('/library')) return 'Library';
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

      {/* Content row */}
      <View style={styles.headerRow}>
        {/* App wordmark */}
        <Text style={[styles.headerWordmark, { color: colors.primary }]}>
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
  { key: 'library',       label: 'Library',   icon: 'folder',         route: '/library'       },
  { key: 'review',        label: 'Review',    icon: 'shield',         route: '/review'        },
  { key: 'topics',        label: 'Topics',    icon: 'layers',         route: '/topics'        },
  { key: 'governance',    label: 'Governance', icon: 'shield-off',    route: '/governance'    },
  { key: 'system',        label: 'System',    icon: 'settings',       route: '/system'        },
  { key: 'backups',       label: 'Backups',   icon: 'hard-drive',     route: '/backups'       },
];

function currentRoute(path: string): string {
  if (path.includes('/conversations')) return '/conversations';
  if (path.includes('/books')) return '/books';
  if (path.includes('/learn')) return '/learn';
  if (path.includes('/intake')) return '/intake';
  if (path.includes('/review')) return '/review';
  if (path.includes('/works')) return '/works';
  if (path.includes('/library')) return '/library';
  return '/';
}

interface NavBottomSheetProps {
  visible: boolean;
  onClose: () => void;
}

function NavBottomSheet({ visible, onClose }: NavBottomSheetProps) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const path = usePathname();

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
      Animated.parallel([
        Animated.timing(slideAnim, {
          toValue: SHEET_CONTENT_HEIGHT + 60,
          duration: 220,
          useNativeDriver: true,
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

        <Text style={[styles.sheetLabel, { color: colors.mutedForeground }]}>
          Navigate to
        </Text>

        {NAV_ITEMS.map((item) => {
          const isActive = item.route === active;
          return (
            <Pressable
              key={item.key}
              onPress={() => handleNav(item.route)}
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
              accessibilityLabel={item.label}
              accessibilityState={{ selected: isActive }}
            >
              {/* Icon container */}
              <View
                style={[
                  styles.navIconWrap,
                  {
                    backgroundColor: isActive
                      ? `${colors.primary}1A`
                      : colors.muted,
                  },
                ]}
              >
                <Feather
                  name={item.icon as any}
                  size={20}
                  color={isActive ? colors.primary : colors.mutedForeground}
                />
              </View>

              {/* Label */}
              <Text
                style={[
                  styles.navLabel,
                  {
                    color: isActive ? colors.primary : colors.foreground,
                    fontFamily: isActive ? 'Inter_600SemiBold' : 'Inter_400Regular',
                  },
                ]}
              >
                {item.label}
              </Text>

              {isActive && (
                <View style={styles.navCheck}>
                  <Feather name="check" size={15} color={colors.primary} />
                </View>
              )}
            </Pressable>
          );
        })}
      </Animated.View>
    </Modal>
  );
}

// ── Native layout (iOS / Android) — no tab bar ────────────────────────────────

function NativeAppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const reviewCount = useReviewCount();

  return (
    <View style={{ flex: 1 }}>
      <AppHeader
        onMenuPress={() => setMenuOpen(true)}
        reviewCount={reviewCount}
      />
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
        <Tabs.Screen name="library" />
      </Tabs>
      <NavBottomSheet visible={menuOpen} onClose={() => setMenuOpen(false)} />
    </View>
  );
}

// ── Web layout — classic tab bar ───────────────────────────────────────────────

function WebTabLayout() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const reviewCount = useReviewCount();

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
          tabBarIcon: ({ color }) => <Feather name="home" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="conversations"
        options={{
          title: 'Chats',
          tabBarIcon: ({ color }) => (
            <Feather name="message-circle" size={22} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="works"
        options={{
          title: 'Works',
          tabBarIcon: ({ color }) => <Feather name="book-open" size={22} color={color} />,
          tabBarBadge: reviewCount > 0 ? reviewCount : undefined,
        }}
      />
      <Tabs.Screen
        name="intake"
        options={{
          title: 'Load',
          tabBarIcon: ({ color }) => <Feather name="inbox" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="books"
        options={{
          title: 'Books',
          tabBarIcon: ({ color }) => <Feather name="book" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="learn"
        options={{
          title: 'Learn',
          tabBarIcon: ({ color }) => <Feather name="award" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="library"
        options={{
          title: 'Library',
          tabBarIcon: ({ color }) => <Feather name="folder" size={22} color={color} />,
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
});
