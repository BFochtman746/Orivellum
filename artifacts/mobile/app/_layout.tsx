import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  AppState,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { syncToCache, flushMessageQueue } from '@/lib/offlineCache';
import { TtsProvider } from '@/context/TtsContext';
import { TtsMiniPlayer } from '@/components/TtsMiniPlayer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  useFonts,
} from '@expo-google-fonts/inter';
import { router, Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { setBaseUrl } from '@workspace/api-client-react';
import { loadToken, saveToken, validateKey } from '@/lib/token';

// ── Push notifications ────────────────────────────────────────────────────────

// Configure how notifications are presented while the app is in the foreground.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

/**
 * Request permission, obtain an Expo push token, and register it with the
 * Orivellum API server.  Safe to call multiple times — idempotent server-side.
 */
async function registerForPushNotificationsAsync(
  apiBaseUrl: string,
  bearerToken: string,
): Promise<void> {
  // Expo push tokens are only available on real devices.
  if (!Device.isDevice) return;

  try {
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;

    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }

    // User declined — respect the decision silently.
    if (finalStatus !== 'granted') return;

    // getExpoPushTokenAsync needs a projectId for production EAS builds.
    // Read it from env; fall back to omitting it (works in Expo Go / dev builds).
    const projectId = process.env.EXPO_PUBLIC_PROJECT_ID as string | undefined;
    const expoPushToken = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined,
    );

    // Register with the Orivellum server so backend can fan-out notifications.
    await fetch(`${apiBaseUrl}/api/users/push-token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${bearerToken}`,
      },
      body: JSON.stringify({
        token: expoPushToken.data,
        platform: Platform.OS,
      }),
    });
  } catch (err) {
    // Non-fatal — push is optional.  Log for dev debugging only.
    if (__DEV__) console.warn('[push] registerForPushNotificationsAsync:', err);
  }
}

const BASE_URL = `https://${process.env.EXPO_PUBLIC_DOMAIN ?? ''}`;

// Set API base URL for Expo — bundles run outside the web proxy and need absolute URLs
setBaseUrl(BASE_URL);

SplashScreen.preventAutoHideAsync();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
    },
  },
});

// ── Login screen ─────────────────────────────────────────────────────────────

function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    const trimmed = key.trim();
    if (!trimmed) return;
    setError('');
    setLoading(true);

    const valid = await validateKey(trimmed, BASE_URL);
    if (valid) {
      await saveToken(trimmed);
      setLoading(false);
      onSuccess();
    } else {
      setLoading(false);
      setError('Invalid key — check your API server startup logs.');
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView
        style={styles.centered}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Text style={styles.title}>Orivellum</Text>
        <Text style={styles.subtitle}>Enter your API key to connect</Text>

        <TextInput
          style={styles.input}
          placeholder="API key"
          placeholderTextColor="#94a3b8"
          value={key}
          onChangeText={setKey}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          editable={!loading}
          onSubmitEditing={handleSubmit}
          returnKeyType="go"
        />

        {!!error && <Text style={styles.errorText}>{error}</Text>}

        <TouchableOpacity
          style={[styles.button, (!key.trim() || loading) && styles.buttonDisabled]}
          onPress={handleSubmit}
          disabled={!key.trim() || loading}
        >
          {loading
            ? <ActivityIndicator color="#fff" size="small" />
            : <Text style={styles.buttonText}>Continue</Text>
          }
        </TouchableOpacity>

        <Text style={styles.hint}>
          Find your key in the API server startup logs or data/api_key.txt
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// ── Stack navigator ───────────────────────────────────────────────────────────

function RootLayoutNav() {
  return (
    <Stack screenOptions={{ headerBackTitle: 'Back' }}>
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen
        name="work/[id]"
        options={{
          headerShown: true,
          headerBackTitle: 'Works',
          headerStyle: { backgroundColor: 'transparent' },
          headerTransparent: true,
          headerBlurEffect: 'regular',
          title: '',
        }}
      />
      <Stack.Screen
        name="chat/[id]"
        options={{
          headerShown: true,
          headerBackTitle: 'Back',
          headerStyle: { backgroundColor: 'transparent' },
          headerTransparent: true,
          headerBlurEffect: 'regular',
          title: '',
        }}
      />
      <Stack.Screen
        name="library/[id]"
        options={{
          headerShown: true,
          headerBackTitle: 'Library',
          headerStyle: { backgroundColor: 'transparent' },
          headerTransparent: true,
          headerBlurEffect: 'regular',
          title: '',
        }}
      />
      <Stack.Screen
        name="studio"
        options={{
          headerShown: true,
          headerBackTitle: 'Back',
          headerStyle: { backgroundColor: 'transparent' },
          headerTransparent: true,
          headerBlurEffect: 'regular',
          title: '',
        }}
      />
      {/* Screens with fully-custom headers — suppress the native Stack header */}
      <Stack.Screen name="graph"   options={{ headerShown: false }} />
      <Stack.Screen name="backups" options={{ headerShown: false }} />
      <Stack.Screen name="review"  options={{ headerShown: false }} />
      <Stack.Screen name="topics"  options={{ headerShown: false }} />
    </Stack>
  );
}

// ── Root layout ───────────────────────────────────────────────────────────────

type AuthState = 'loading' | 'authenticated' | 'unauthenticated';

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
  });
  const [authState, setAuthState] = useState<AuthState>('loading');
  // Store the bearer token so push registration can authenticate with the server.
  const bearerTokenRef = useRef<string | null>(null);
  // Tracks whether a background cache sync is in progress.
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    loadToken().then((token) => {
      bearerTokenRef.current = token;
      setAuthState(token ? 'authenticated' : 'unauthenticated');
    });
  }, []);

  useEffect(() => {
    if ((fontsLoaded || fontError) && authState !== 'loading') {
      SplashScreen.hideAsync();
    }
  }, [fontsLoaded, fontError, authState]);

  // Register for push notifications once the user is authenticated.
  useEffect(() => {
    if (authState !== 'authenticated') return;
    const token = bearerTokenRef.current;
    if (!token) return;
    registerForPushNotificationsAsync(BASE_URL, token);
  }, [authState]);

  // ── Offline cache sync ────────────────────────────────────────────────────
  //
  // Run an initial sync as soon as the user is authenticated, then re-sync
  // every time the app comes back to the foreground (AppState 'active').
  // flushMessageQueue() is called first on foreground so any messages the
  // user typed while offline are delivered before we overwrite cache data.

  useEffect(() => {
    if (authState !== 'authenticated') return;
    // On first auth (including cold start after an offline session): flush any
    // messages that were queued before the app was closed, then sync the cache.
    // flushMessageQueue uses a single-flight lock so concurrent calls (e.g. an
    // immediate AppState 'active' event) are collapsed into one in-flight fetch.
    setSyncing(true);
    flushMessageQueue()
      .then(() => syncToCache())
      .finally(() => setSyncing(false));
  }, [authState]);

  useEffect(() => {
    if (authState !== 'authenticated') return;
    const sub = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        setSyncing(true);
        // Flush queued messages first, then refresh cache.
        // The single-flight lock in flushMessageQueue() means this is a no-op
        // if the initial-auth flush above is still running.
        flushMessageQueue()
          .then(() => syncToCache())
          .finally(() => setSyncing(false));
      }
    });
    return () => sub.remove();
  }, [authState]);

  // ── Notification deep-link routing ───────────────────────────────────────────
  //
  // `navigateToScreen` is the single routing handler for notification taps.
  // It is called from two places:
  //   1. `addNotificationResponseReceivedListener` — fires when the app is in
  //      the foreground or already in the background (resumed via tap).
  //   2. `getLastNotificationResponseAsync()` — fires on cold start when the
  //      app was killed and the user tapped a notification to open it.
  //
  // Both paths must use the same logic so the experience is consistent.

  const navigateToScreen = React.useCallback((screen: string) => {
    if (screen.startsWith('library/')) {
      const docId = screen.slice('library/'.length);
      router.push(`/library/${docId}` as any);
    } else if (screen === 'studio') {
      router.push('/studio' as any);
    } else if (screen === 'governance') {
      // Governance lives in the Works tab on mobile.
      router.push('/(tabs)' as any);
    } else {
      // Fallback: trust whatever screen path the server specified.
      router.push(`/${screen}` as any);
    }
  }, []);

  // Foreground / background tap listener.
  useEffect(() => {
    const subscription = Notifications.addNotificationResponseReceivedListener(
      (response) => {
        const screen = response.notification.request.content.data?.screen as
          | string
          | undefined;
        if (screen) navigateToScreen(screen);
      },
    );
    return () => subscription.remove();
  }, [navigateToScreen]);

  // Cold-start: app was killed and user tapped a notification to open it.
  // `getLastNotificationResponseAsync` returns the pending response (if any)
  // that Expo held while the app was not running — we route it here once the
  // navigator is mounted and the auth state is resolved.
  useEffect(() => {
    if (authState === 'loading') return;
    Notifications.getLastNotificationResponseAsync().then((response) => {
      if (!response) return;
      const screen = response.notification.request.content.data?.screen as
        | string
        | undefined;
      if (screen) navigateToScreen(screen);
    }).catch(() => {
      // Non-fatal — cold-start routing is best-effort.
    });
  }, [authState, navigateToScreen]);

  if ((!fontsLoaded && !fontError) || authState === 'loading') return null;

  if (authState === 'unauthenticated') {
    return (
      <SafeAreaProvider>
        <LoginScreen onSuccess={() => setAuthState('authenticated')} />
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <GestureHandlerRootView style={{ flex: 1 }}>
            <KeyboardProvider>
              <TtsProvider>
                {/*
                  Wrap navigator + mini-player in a flex column.
                  TtsMiniPlayer returns null when idle (zero height) and renders
                  as a bar below the navigator when TTS is active — the navigator
                  shrinks slightly rather than being overlaid, so no content is
                  hidden behind the bar. This also keeps the bar visible on every
                  route (including non-tab screens like /library/[id]).
                */}
                <View style={{ flex: 1 }}>
                  <RootLayoutNav />
                  {Platform.OS === 'web' ? (
                    /*
                      On web the tab bar is absolutely positioned at bottom:0
                      (height 84) and would cover a flex-sibling mini-player.
                      Render the player as a root-level absolute overlay instead
                      so it sits just above the tab bar on tab screens and
                      remains mounted and visible on every route — including
                      stack screens like /library/[id] — keeping playback
                      controls accessible after any navigation.
                    */
                    <View
                      style={{
                        position: 'absolute',
                        left: 0,
                        right: 0,
                        bottom: 84,
                        zIndex: 50,
                      }}
                      pointerEvents="box-none"
                    >
                      <TtsMiniPlayer />
                    </View>
                  ) : (
                    <TtsMiniPlayer />
                  )}
                </View>
              </TtsProvider>
            </KeyboardProvider>
            {/* ── Sync indicator ─────────────────────────────────────────
                Shown briefly while the background cache sync is running.
                Positioned above the native tab bar (approx 80pt).       */}
            {syncing && (
              <View
                style={{
                  position: 'absolute',
                  bottom: Platform.OS === 'ios' ? 90 : 70,
                  left: 20,
                  right: 20,
                  backgroundColor: 'rgba(15,23,42,0.82)',
                  borderRadius: 10,
                  paddingHorizontal: 14,
                  paddingVertical: 8,
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 8,
                  pointerEvents: 'none',
                }}
              >
                <ActivityIndicator size="small" color="#7c9e8e" />
                <Text style={{ color: '#e2e8f0', fontSize: 12 }}>
                  Syncing offline cache…
                </Text>
              </View>
            )}
          </GestureHandlerRootView>
        </QueryClientProvider>
      </ErrorBoundary>
    </SafeAreaProvider>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#f8f7f4',
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 32,
    gap: 12,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#0f172a',
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 14,
    color: '#64748b',
    marginBottom: 12,
  },
  input: {
    width: '100%',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 12,
    fontSize: 14,
    color: '#0f172a',
    backgroundColor: '#ffffff',
  },
  button: {
    width: '100%',
    paddingVertical: 13,
    borderRadius: 8,
    backgroundColor: '#7c9e8e',
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  buttonText: {
    color: '#ffffff',
    fontWeight: '600',
    fontSize: 14,
  },
  errorText: {
    color: '#ef4444',
    fontSize: 13,
    textAlign: 'center',
  },
  hint: {
    fontSize: 12,
    color: '#94a3b8',
    textAlign: 'center',
    marginTop: 8,
  },
});
