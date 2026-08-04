import React from 'react';
import { Platform, StyleSheet, useColorScheme, View } from 'react-native';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { BlurView } from 'expo-blur';
import { isLiquidGlassAvailable } from 'expo-glass-effect';
import { Tabs } from 'expo-router';
import { Icon, Label, NativeTabs } from 'expo-router/unstable-native-tabs';
import { SymbolView } from 'expo-symbols';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useGetSystemHealth, getGetSystemHealthQueryKey } from '@workspace/api-client-react';

/** Small coloured dot showing server reachability. */
function ServerDot() {
  const { data, isError } = useGetSystemHealth({
    query: {
      queryKey: getGetSystemHealthQueryKey(),
      refetchInterval: 15_000,
      staleTime: 10_000,
      retry: false,
    },
  });
  const ok = !isError && data?.status === 'ok';
  const degraded = !isError && data?.status !== 'ok';
  const color = isError ? '#ef4444' : degraded ? '#f59e0b' : '#22c55e';
  return (
    <View
      style={{
        width: 6,
        height: 6,
        borderRadius: 3,
        backgroundColor: color,
        position: 'absolute',
        top: 2,
        right: -2,
      }}
    />
  );
}

function NativeTabLayout() {
  const { data, isError } = useGetSystemHealth({
    query: {
      queryKey: getGetSystemHealthQueryKey(),
      refetchInterval: 15_000,
      staleTime: 10_000,
      retry: false,
    },
  });
  const dotColor = isError ? '#ef4444' : data?.status !== 'ok' ? '#f59e0b' : '#22c55e';

  return (
    <NativeTabs>
      <NativeTabs.Trigger name="index">
        {/* Health dot sits over the Dashboard icon */}
        <View style={{ position: 'relative' }}>
          <Icon sf={{ default: 'house', selected: 'house.fill' }} />
          <View style={{
            width: 6, height: 6, borderRadius: 3,
            backgroundColor: dotColor,
            position: 'absolute', top: -1, right: -4,
          }} />
        </View>
        <Label>Dashboard</Label>
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="conversations">
        <View style={{ position: 'relative' }}>
          <Icon sf={{ default: 'bubble.left', selected: 'bubble.left.fill' }} />
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: dotColor, position: 'absolute', top: -1, right: -4 }} />
        </View>
        <Label>Chats</Label>
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="works">
        <View style={{ position: 'relative' }}>
          <Icon sf={{ default: 'books.vertical', selected: 'books.vertical.fill' }} />
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: dotColor, position: 'absolute', top: -1, right: -4 }} />
        </View>
        <Label>Works</Label>
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="library">
        <View style={{ position: 'relative' }}>
          <Icon sf={{ default: 'folder', selected: 'folder.fill' }} />
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: dotColor, position: 'absolute', top: -1, right: -4 }} />
        </View>
        <Label>Library</Label>
      </NativeTabs.Trigger>
    </NativeTabs>
  );
}

function ClassicTabLayout() {
  const colors = useColors();
  const colorScheme = useColorScheme();
  const isDark = colorScheme === 'dark';
  const isIOS = Platform.OS === 'ios';
  const isWeb = Platform.OS === 'web';
  const insets = useSafeAreaInsets();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.mutedForeground,
        tabBarStyle: {
          position: 'absolute',
          backgroundColor: isIOS ? 'transparent' : colors.background,
          borderTopWidth: isWeb ? 1 : 0,
          borderTopColor: colors.border,
          elevation: 0,
          paddingBottom: isWeb ? 0 : insets.bottom,
          ...(isWeb ? { height: 84 } : {}),
        },
        tabBarBackground: () =>
          isIOS ? (
            <BlurView
              intensity={100}
              tint={isDark ? 'dark' : 'light'}
              style={StyleSheet.absoluteFill}
            />
          ) : isWeb ? (
            <View
              style={[
                StyleSheet.absoluteFill,
                { backgroundColor: colors.background },
              ]}
            />
          ) : null,
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Dashboard',
          tabBarIcon: ({ color }) => (
            <View style={{ position: 'relative' }}>
              {isIOS ? (
                <SymbolView name="house" tintColor={color} size={24} />
              ) : (
                <Feather name="home" size={22} color={color} />
              )}
              <ServerDot />
            </View>
          ),
        }}
      />
      <Tabs.Screen
        name="conversations"
        options={{
          title: 'Chats',
          tabBarIcon: ({ color }) => (
            <View style={{ position: 'relative' }}>
              {isIOS ? (
                <SymbolView name="bubble.left" tintColor={color} size={24} />
              ) : (
                <Feather name="message-circle" size={22} color={color} />
              )}
              <ServerDot />
            </View>
          ),
        }}
      />
      <Tabs.Screen
        name="works"
        options={{
          title: 'Works',
          tabBarIcon: ({ color }) => (
            <View style={{ position: 'relative' }}>
              {isIOS ? (
                <SymbolView name="books.vertical" tintColor={color} size={24} />
              ) : (
                <Feather name="book-open" size={22} color={color} />
              )}
              <ServerDot />
            </View>
          ),
        }}
      />
      <Tabs.Screen
        name="library"
        options={{
          title: 'Library',
          tabBarIcon: ({ color }) => (
            <View style={{ position: 'relative' }}>
              {isIOS ? (
                <SymbolView name="folder" tintColor={color} size={24} />
              ) : (
                <Feather name="folder" size={22} color={color} />
              )}
              <ServerDot />
            </View>
          ),
        }}
      />
    </Tabs>
  );
}

export default function TabLayout() {
  if (isLiquidGlassAvailable()) {
    return <NativeTabLayout />;
  }
  return <ClassicTabLayout />;
}
