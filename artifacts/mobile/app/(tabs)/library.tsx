import React, { useMemo, useRef, useState } from 'react';
import { mobileFetch } from '@/lib/api';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useListLibrary, useSearchLibrary, useListWorks } from '@workspace/api-client-react';
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

function DocItem({ doc, colors, onPress, onReprocess }: { doc: any; colors: any; onPress: () => void; onReprocess?: () => void }) {
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
          {doc.lifecycle && doc.lifecycle !== 'draft' ? (
            <Text style={[styles.lifecycleBadge, {
              color: doc.lifecycle === 'canonical' ? '#16a34a'
                   : doc.lifecycle === 'superseded' ? '#6b7280'
                   : colors.mutedForeground,
              borderColor: doc.lifecycle === 'canonical' ? '#16a34a44'
                         : doc.lifecycle === 'superseded' ? '#6b728044'
                         : colors.border,
            }]}>
              {doc.lifecycle}
            </Text>
          ) : null}
          {doc.word_count ? (
            <Text style={[styles.wordCount, { color: colors.mutedForeground }]}>
              {doc.word_count.toLocaleString()} words
            </Text>
          ) : null}
          {/* Shown on search results when the hit came from semantic (vector)
              matching rather than keyword — helps users understand why a doc
              appeared even though the query words aren't in the title. */}
          {doc.match_type === 'semantic' && (
            <Text style={[styles.lifecycleBadge, { color: '#7c3aed', borderColor: '#7c3aed44' }]}>
              semantic
            </Text>
          )}
        </View>
      </View>
      {onReprocess && (doc.readiness === 'error' || doc.readiness === 'no_text') && (
        <Pressable
          onPress={(e) => { e.stopPropagation?.(); onReprocess(); }}
          hitSlop={8}
          style={({ pressed }) => ({
            padding: 6,
            borderRadius: 6,
            backgroundColor: colors.muted,
            opacity: pressed ? 0.6 : 1,
            marginLeft: 4,
          })}
        >
          <Feather name="refresh-cw" size={14} color={colors.primary} />
        </Pressable>
      )}
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
  const [uploadProgress, setUploadProgress] = useState(0); // 0–100
  const [workFilter, setWorkFilter] = useState<string | undefined>(undefined); // work_id or undefined = all
  const [sortBy, setSortBy] = useState<'newest' | 'oldest' | 'az' | 'za'>('newest');
  /** Default to hybrid so users get conceptual matches right away.
   *  Falls back to keyword results silently when embeddings are off. */
  const [searchMode, setSearchMode] = useState<'keyword' | 'semantic' | 'hybrid'>('hybrid');

  const { data: worksData } = useListWorks({} as any, { query: { staleTime: 60_000 } } as any);
  const works: any[] = (worksData as any)?.works ?? [];

  const handleUpload = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: '*/*',
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.length) return;
      const asset = result.assets[0];
      setUploading(true);
      setUploadProgress(5);
      // Read file as base64 using fetch + FileReader
      const fileResp = await fetch(asset.uri);
      const blob = await fileResp.blob();
      setUploadProgress(15);
      const reader = new FileReader();
      const b64: string = await new Promise((resolve, reject) => {
        reader.onprogress = (evt) => {
          if (evt.lengthComputable) {
            // Scale reading progress to 15–80%
            const pct = 15 + Math.round((evt.loaded / evt.total) * 65);
            setUploadProgress(pct);
          }
        };
        reader.onload = () => {
          setUploadProgress(82);
          const dataUrl = reader.result as string;
          resolve(dataUrl.split(',')[1] ?? '');
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      setUploadProgress(90);
      const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';

      // Ask which Work to link this document to (optional)
      const uploadWorkId = await new Promise<string | null>((resolve) => {
        const workOptions = works.map((w: any) => w.title as string);
        const workIds = works.map((w: any) => w.id as string);
        if (workOptions.length === 0) { resolve(null); return; }
        Alert.alert(
          'Link to a Work?',
          'Optionally assign this document to an existing Work.',
          [
            { text: 'No Work', onPress: () => resolve(null) },
            ...workOptions.slice(0, 8).map((label, idx) => ({
              text: label.length > 30 ? label.slice(0, 28) + '…' : label,
              onPress: () => resolve(workIds[idx] ?? null),
            })),
          ],
          { cancelable: true, onDismiss: () => resolve(null) }
        );
      });

      const resp = await mobileFetch(`https://${domain}/api/library/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: asset.name,
          content_b64: b64,
          mime_type: asset.mimeType ?? 'application/octet-stream',
          ...(uploadWorkId ? { work_id: uploadWorkId } : {}),
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        Alert.alert('Upload failed', err.detail ?? 'Could not import document');
        return;
      }
      setUploadProgress(100);
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
      setUploadProgress(0);
    }
  };

  const {
    data: listData,
    isLoading: listLoading,
    isError: listError,
    refetch: refetchList,
  } = useListLibrary(
    workFilter ? { work_id: workFilter } as any : {},
    { query: { refetchInterval: 30_000, staleTime: 20_000 } } as any,
  );

  // Offline search cache — keep last successful results so they remain visible
  // even when the network drops mid-search.
  const lastSearchCache = useRef<{ query: string; results: any[] }>({ query: '', results: [] });

  const {
    data: searchData,
    isLoading: searchLoading,
    isError: searchError,
  } = useSearchLibrary(
    { q: search, mode: searchMode },
    { query: { enabled: search.length > 1, staleTime: 60_000 } } as any,
  );

  // Update cache whenever we get fresh results
  if (searchData?.results) {
    lastSearchCache.current = { query: search, results: searchData.results };
  }

  const isSearching = search.length > 1;
  const isLoading = isSearching ? searchLoading : listLoading;
  // When offline during a search, fall back to last cached results for that query
  const searchResults: any[] = searchData?.results ?? (searchError && lastSearchCache.current.results.length > 0 ? lastSearchCache.current.results : []);
  const isOfflineSearch = searchError && lastSearchCache.current.results.length > 0;
  const rawDocs: any[] = isSearching ? searchResults : (listData?.documents ?? []);
  const docs: any[] = useMemo(() => {
    if (isSearching) return rawDocs;
    return [...rawDocs].sort((a, b) => {
      if (sortBy === 'newest') return new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime();
      if (sortBy === 'oldest') return new Date(a.created_at ?? 0).getTime() - new Date(b.created_at ?? 0).getTime();
      const ta = (a.title || a.source?.split('/').pop() || '').toLowerCase();
      const tb = (b.title || b.source?.split('/').pop() || '').toLowerCase();
      return sortBy === 'az' ? ta.localeCompare(tb) : tb.localeCompare(ta);
    });
  }, [rawDocs, sortBy, isSearching]);
  const hasData = rawDocs.length > 0 || (listData?.documents?.length ?? 0) > 0;

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
        {/* Upload progress bar — shown while uploading a file */}
        {uploading && (
          <View style={{ marginTop: 8, height: 4, backgroundColor: colors.muted, borderRadius: 2, overflow: 'hidden' }}>
            <View
              style={{
                height: '100%',
                width: `${uploadProgress}%`,
                backgroundColor: colors.primary,
                borderRadius: 2,
              }}
            />
          </View>
        )}
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

      {/* Search mode picker — shown while the user is actively searching.
          Hybrid is the default: it combines keyword + semantic results.
          Falls back to keyword silently when the embeddings endpoint is off. */}
      {isSearching && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ flexDirection: 'row', paddingHorizontal: 12, paddingVertical: 5, gap: 6 }}
          style={{ borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }}
        >
          {(['hybrid', 'keyword', 'semantic'] as const).map((mode) => {
            const label = mode === 'hybrid' ? 'Hybrid' : mode === 'keyword' ? 'Keyword' : 'Semantic';
            const active = searchMode === mode;
            return (
              <Pressable
                key={mode}
                onPress={() => setSearchMode(mode)}
                style={{
                  paddingHorizontal: 10,
                  paddingVertical: 3,
                  borderRadius: 12,
                  borderWidth: 1,
                  borderColor: active ? colors.primary : colors.border,
                  backgroundColor: active ? colors.primary + '18' : 'transparent',
                }}
              >
                <Text style={{
                  fontSize: 11,
                  fontFamily: 'Inter_500Medium',
                  color: active ? colors.primary : colors.mutedForeground,
                }}>
                  {label}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      {/* Work filter chips — only shown when works exist */}
      {works.length > 0 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{
            flexDirection: 'row',
            paddingHorizontal: 12,
            paddingVertical: 6,
            gap: 6,
          }}
          style={{
            borderBottomWidth: StyleSheet.hairlineWidth,
            borderBottomColor: colors.border,
          }}
        >
          <Pressable
            onPress={() => setWorkFilter(undefined)}
            style={{
              paddingHorizontal: 10,
              paddingVertical: 4,
              borderRadius: 12,
              borderWidth: 1,
              borderColor: !workFilter ? colors.primary : colors.border,
              backgroundColor: !workFilter ? colors.primary + '18' : 'transparent',
            }}
          >
            <Text style={{
              fontSize: 11,
              fontFamily: 'Inter_500Medium',
              color: !workFilter ? colors.primary : colors.mutedForeground,
            }}>All</Text>
          </Pressable>
          {works.slice(0, 6).map((w: any) => (
            <Pressable
              key={w.id}
              onPress={() => setWorkFilter(workFilter === w.id ? undefined : w.id)}
              style={{
                paddingHorizontal: 10,
                paddingVertical: 4,
                borderRadius: 12,
                borderWidth: 1,
                borderColor: workFilter === w.id ? colors.primary : colors.border,
                backgroundColor: workFilter === w.id ? colors.primary + '18' : 'transparent',
              }}
            >
              <Text
                style={{
                  fontSize: 11,
                  fontFamily: 'Inter_500Medium',
                  color: workFilter === w.id ? colors.primary : colors.mutedForeground,
                  maxWidth: 100,
                }}
                numberOfLines={1}
              >
                {w.title ?? 'Untitled'}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      )}

      {/* Sort chips — only shown when not searching */}
      {!isSearching && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ flexDirection: 'row', paddingHorizontal: 12, paddingVertical: 5, gap: 6 }}
          style={{ borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }}
        >
          {(['newest', 'oldest', 'az', 'za'] as const).map((opt) => {
            const label = opt === 'newest' ? 'Newest' : opt === 'oldest' ? 'Oldest' : opt === 'az' ? 'A → Z' : 'Z → A';
            const active = sortBy === opt;
            return (
              <Pressable
                key={opt}
                onPress={() => setSortBy(opt)}
                style={{
                  paddingHorizontal: 10,
                  paddingVertical: 3,
                  borderRadius: 12,
                  borderWidth: 1,
                  borderColor: active ? colors.primary : colors.border,
                  backgroundColor: active ? colors.primary + '18' : 'transparent',
                }}
              >
                <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: active ? colors.primary : colors.mutedForeground }}>
                  {label}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      {/* Offline banners */}
      {listError && hasData && !isSearching && (
        <OfflineBanner message="Showing cached documents — server unreachable" onRetry={refetchList} />
      )}
      {isOfflineSearch && (
        <OfflineBanner message="Showing last search results — you appear to be offline" />
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
            {isSearching ? 'Try a different search term' : 'Tap Import above to add a document'}
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
              onReprocess={
                item.readiness === 'error' || item.readiness === 'no_text'
                  ? async () => {
                      try {
                        const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
                        await mobileFetch(`https://${domain}/api/library/${item.id}/reprocess`, { method: 'POST' });
                        refetchList();
                      } catch {
                        Alert.alert('Error', 'Could not queue reprocess');
                      }
                    }
                  : undefined
              }
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
  lifecycleBadge: {
    fontSize: 10,
    fontFamily: 'Inter_500Medium',
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
});
