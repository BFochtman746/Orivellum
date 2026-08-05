import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
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
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { setBaseUrl } from '@workspace/api-client-react';
import { loadToken, saveToken, validateKey } from '@/lib/token';

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

  useEffect(() => {
    loadToken().then((token) => {
      setAuthState(token ? 'authenticated' : 'unauthenticated');
    });
  }, []);

  useEffect(() => {
    if ((fontsLoaded || fontError) && authState !== 'loading') {
      SplashScreen.hideAsync();
    }
  }, [fontsLoaded, fontError, authState]);

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
              <RootLayoutNav />
            </KeyboardProvider>
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
