import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useListLibrary, useSearchLibrary } from '@workspace/api-client-react';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';

const READINESS_COLOR: Record<string, string> = {
  ready: '#22c55e',
  imported: '#f59e0b',
  error: '#ef4444',
  no_text: '#ef4444',
};

const READINESS_LABEL: Record<string, string> = {
  ready: 'Ready',
  imported: 'Processing',
  error: 'Error',
  no_text: 'No Text',
};

const KIND_ICON: Record<string, string> = {
  pdf: 'file-text',
  docx: 'file-text',
  csv: 'table',
  excel: 'bar-chart-2',
  pptx: 'monitor',
  text: 'align-left',
  markdown: 'hash',
  code: 'code',
  image: 'image',
};

function DocItem({ doc, colors, onPress }: { doc: any; colors: any; onPress: () => void }) {
  const readiness: string = doc.readiness ?? 'imported';
  const statusColor = READINESS_COLOR[readiness] ?? colors.mutedForeground;
  const statusLabel = READINESS_LABEL[readiness] ?? readiness;
  const icon = KIND_ICON[doc.kind ?? 'file'] ?? 'file';
  const title = doc.title || doc.source?.split('/').pop() || 'Untitled';

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.docRow,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
      ]}
    >
      <View style={[styles.docIcon, { backgroundColor: colors.muted }]}>
        <Feather name={icon as any} size={16} color={colors.primary} />
      </View>
      <View style={styles.docMeta}>
        <Text style={[styles.docTitle, { color: colors.foreground }]} numberOfLines={1}>
          {title}
        </Text>
        <View style={styles.docBadges}>
          <Text style={[styles.kindBadge, { color: colors.mutedForeground }]}>
            {(doc.kind ?? 'file').toUpperCase()}
          </Text>
          <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
          <Text style={[styles.statusLabel, { color: statusColor }]}>{statusLabel}</Text>
          {doc.word_count ? (
            <Text style={[styles.wordCount, { color: colors.mutedForeground }]}>
              {doc.word_count.toLocaleString()} words
            </Text>
          ) : null}
        </View>
      </View>
      <Feather name="chevron-right" size={16} color={colors.mutedForeground} />
    </Pressable>
  );
}

export default function LibraryScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const router = useRouter();

  const [search, setSearch] = useState('');
  const [uploading, setUploading] = useState(false);

  const handleUpload = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: '*/*',
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.length) return;
      const asset = result.assets[0];
      setUploading(true);
      // Read file as base64 using fetch
      const fileResp = await fetch(asset.uri);
      const blob = await fileResp.blob();
      const reader = new FileReader();
      const b64: string = await new Promise((resolve, reject) => {
        reader.onload = () => {
          const dataUrl = reader.result as string;
          resolve(dataUrl.split(',')[1] ?? '');
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      const apiBase = (global as any).__ORIVELLUM_API_BASE__ ?? 'http://localhost:8000';
      const resp = await fetch(`${apiBase}/api/library/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: asset.name,
          content_b64: b64,
          mime_type: asset.mimeType ?? 'application/octet-stream',
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        Alert.alert('Upload failed', err.detail ?? 'Could not import document');
        return;
      }
      const data = await resp.json();
      Alert.alert('Uploaded', `"${asset.name}" added to your library`);
      refetchList();
      if (data?.document?.id) router.push(`/library/${data.document.id}` as any);
    } catch (err: any) {
      if (!String(err).includes('cancel')) {
        Alert.alert('Error', err?.message ?? 'Upload failed');
      }
    } finally {
      setUploading(false);
    }
  };

  const {
    data: listData,
    isLoading: listLoading,
    isError: listError,
    refetch: refetchList,
  } = useListLibrary({}, { query: { refetchInterval: 30_000, staleTime: 20_000 } } as any);

  const {
    data: searchData,
    isLoading: searchLoading,
  } = useSearchLibrary({ q: search }, { query: { enabled: search.length > 1 } } as any);

  const isSearching = search.length > 1;
  const isLoading = isSearching ? searchLoading : listLoading;
  const docs: any[] = isSearching
    ? (searchData?.results ?? [])
    : (listData?.documents ?? []);
  const hasData = docs.length > 0 || (listData?.documents?.length ?? 0) > 0;

  const topPad = isWeb ? 67 : insets.top;

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View
        style={[
          styles.header,
          { paddingTop: topPad + 12, borderBottomColor: colors.border, backgroundColor: colors.background },
        ]}
      >
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <View>
            <Text style={[styles.title, { color: colors.foreground }]}>Library</Text>
            <Text style={[styles.subtitle, { color: colors.mutedForeground }]}>
              {isLoading ? '…' : `${listData?.count ?? docs.length} documents`}
            </Text>
          </View>
          <Pressable
            onPress={handleUpload}
            disabled={uploading}
            style={({ pressed }) => ({
              backgroundColor: colors.primary + (pressed ? 'cc' : ''),
              borderRadius: 10,
              paddingHorizontal: 14,
              paddingVertical: 9,
              flexDirection: 'row',
              alignItems: 'center',
              gap: 6,
              opacity: uploading ? 0.6 : 1,
              marginTop: 4,
            })}
          >
            <Feather name={uploading ? 'loader' : 'upload'} size={14} color="#fff" />
            <Text style={{ color: '#fff', fontSize: 13, fontFamily: 'Inter_600SemiBold' }}>
              {uploading ? 'Uploading…' : 'Import'}
            </Text>
          </Pressable>
        </View>
      </View>

      {/* Search */}
      <View style={[styles.searchRow, { borderBottomColor: colors.border, backgroundColor: colors.background }]}>
        <Feather name="search" size={15} color={colors.mutedForeground} style={styles.searchIcon} />
        <TextInput
          style={[styles.searchInput, { color: colors.foreground, fontFamily: 'Inter_400Regular' }]}
          placeholder="Search documents…"
          placeholderTextColor={colors.mutedForeground}
          value={search}
          onChangeText={setSearch}
          returnKeyType="search"
        />
        {search.length > 0 && (
          <Pressable onPress={() => setSearch('')} hitSlop={8}>
            <Feather name="x" size={15} color={colors.mutedForeground} />
          </Pressable>
        )}
      </View>

      {/* Offline banner */}
      {listError && hasData && (
        <OfflineBanner message="Showing cached documents — server unreachable" onRetry={refetchList} />
      )}

      {/* Body */}
      {isLoading && !hasData ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : listError && !hasData ? (
        <ErrorScreen
          message="Can't reach the server"
          detail="Make sure Orivellum is running and your device is on the same network."
          onRetry={refetchList}
        />
      ) : docs.length === 0 ? (
        <View style={styles.centered}>
          <Feather name="inbox" size={44} color={colors.mutedForeground} />
          <Text style={[styles.emptyTitle, { color: colors.foreground }]}>
            {isSearching ? 'No results' : 'No documents yet'}
          </Text>
          <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
            {isSearching ? 'Try a different search term' : 'Import documents from the web app to get started'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={docs}
          keyExtractor={(item) => item.id ?? item.source ?? Math.random().toString()}
          renderItem={({ item }) => (
            <DocItem
              doc={item}
              colors={colors}
              onPress={() => router.push(`/library/${item.id}`)}
            />
          )}
          refreshControl={
            <RefreshControl refreshing={listLoading && hasData} onRefresh={refetchList} tintColor={colors.primary} />
          }
          contentContainerStyle={{
            paddingHorizontal: 16,
            paddingTop: 12,
            paddingBottom: isWeb ? 34 + 50 : insets.bottom + 100,
          }}
          showsVerticalScrollIndicator={false}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: 1,
  },
  title: { fontSize: 28, fontFamily: 'Inter_700Bold', letterSpacing: -0.5 },
  subtitle: { fontSize: 13, fontFamily: 'Inter_400Regular', marginTop: 2 },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    gap: 8,
  },
  searchIcon: {},
  searchInput: { flex: 1, fontSize: 14, paddingVertical: 0 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 32 },
  emptyTitle: { fontSize: 16, fontFamily: 'Inter_600SemiBold', textAlign: 'center' },
  emptyText: { fontSize: 13, fontFamily: 'Inter_400Regular', textAlign: 'center', lineHeight: 19 },
  docRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    gap: 12,
  },
  docIcon: {
    width: 36,
    height: 36,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  docMeta: { flex: 1 },
  docTitle: { fontSize: 14, fontFamily: 'Inter_600SemiBold', marginBottom: 4 },
  docBadges: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  kindBadge: { fontSize: 10, fontFamily: 'Inter_500Medium' },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusLabel: { fontSize: 10, fontFamily: 'Inter_500Medium' },
  wordCount: { fontSize: 10, fontFamily: 'Inter_400Regular' },
});
