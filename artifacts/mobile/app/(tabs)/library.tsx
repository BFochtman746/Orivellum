import React, { useMemo, useRef, useState, useEffect } from 'react';
import { mobileFetch } from '@/lib/api';
import { getApiToken } from '@/lib/token';
import { readCache, writeCache } from '@/lib/cache';
import {
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
import { font } from '@/lib/typography';
import { useRouter } from 'expo-router';
import { OfflineBanner, ErrorScreen } from '@/components/OfflineBanner';
import { useVellumTokens } from '@/lib/tokens';
import { SkeletonItem } from '@/components/SkeletonItem';
import { EmptyState } from '@/components/EmptyState';

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

const READINESS_LABEL: Record<string, string> = {
  ready: 'Ready',
  imported: 'Processing',
  error: 'Error',
  no_text: 'No Text',
};

function DocItem({ doc, colors, T, onPress, onReprocess }: { doc: any; colors: any; T: any; onPress: () => void; onReprocess?: () => void }) {
  const readiness: string = doc.readiness ?? 'imported';

  // Map readiness states to VELLUM tokens
  const statusColor: string =
    readiness === 'ready'
      ? T.green
      : readiness === 'imported'
      ? T.gilt
      : readiness === 'error' || readiness === 'no_text'
      ? T.rust
      : colors.mutedForeground;

  const statusBg: string =
    readiness === 'ready'
      ? T.greenSoft
      : readiness === 'imported'
      ? T.giltSoft
      : readiness === 'error' || readiness === 'no_text'
      ? T.rustSoft
      : 'transparent';

  const statusLabel = READINESS_LABEL[readiness] ?? readiness;
  const icon = KIND_ICON[doc.kind ?? 'file'] ?? 'file';
  const title = doc.title || doc.source?.split('/').pop() || 'Untitled';

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.docRow,
        { backgroundColor: colors.card, borderColor: colors.border, opacity: pressed ? 0.7 : 1, minHeight: 44 },
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
              color: doc.lifecycle === 'canonical' ? T.green
                   : doc.lifecycle === 'superseded' ? colors.mutedForeground
                   : colors.mutedForeground,
              borderColor: doc.lifecycle === 'canonical' ? T.giltLine
                         : doc.lifecycle === 'superseded' ? colors.border
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
            <Text style={[styles.lifecycleBadge, { color: T.gilt, borderColor: T.giltLine }]}>
              semantic
            </Text>
          )}
        </View>
      </View>
      {onReprocess && (doc.readiness === 'error' || doc.readiness === 'no_text') && (
        <Pressable
          onPress={(e) => { e.stopPropagation?.(); onReprocess(); }}
          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          style={({ pressed }) => ({
            minHeight: 44,
            minWidth: 44,
            alignItems: 'center',
            justifyContent: 'center',
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
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const isWeb = Platform.OS === 'web';
  const router = useRouter();

  const [search, setSearch] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0); // 0–100
  const [workFilter, setWorkFilter] = useState<string | undefined>(undefined); // work_id or undefined = all
  const [sortBy, setSortBy] = useState<'newest' | 'oldest' | 'az' | 'za'>('newest');
  const [showFilters, setShowFilters] = useState(false); // progressive disclosure for filter chips
  /** Default to hybrid so users get conceptual matches right away.
   *  Falls back to keyword results silently when embeddings are off. */
  const [searchMode, setSearchMode] = useState<'keyword' | 'semantic' | 'hybrid'>('hybrid');
  const [embeddingsDown, setEmbeddingsDown] = useState(false);
  // User can dismiss the banner for the current search session.
  // Resets automatically whenever searchMode changes (e.g. they switch to Keyword).
  const [embeddingsBannerDismissed, setEmbeddingsBannerDismissed] = useState(false);

  const { data: worksData } = useListWorks({} as any, { query: { staleTime: 60_000 } } as any);
  const works: any[] = (worksData as any)?.works ?? [];

  // Poll embeddings circuit-breaker every 15 s while in a semantic mode so the
  // banner auto-hides as soon as the circuit reopens, without requiring navigation.
  useEffect(() => {
    setEmbeddingsBannerDismissed(false);           // reset dismiss on mode switch
    const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
    if (searchMode === 'keyword') { setEmbeddingsDown(false); return; }
    let cancelled = false;
    const check = () => {
      mobileFetch(`https://${domain}/api/system/embeddings/status`)
        .then(r => r.ok ? r.json() : null)
        .then((data: any) => { if (!cancelled) setEmbeddingsDown(data?.circuit_open === true); })
        .catch(() => { if (!cancelled) setEmbeddingsDown(false); });
    };
    check();
    const interval = setInterval(check, 15_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [searchMode]);

  // Tracks how many files have finished in the current multi-upload run (for the
  // progress bar label). Stored in state so the button text updates reactively.
  const [uploadIndex, setUploadIndex] = useState(0);
  const [uploadTotal, setUploadTotal]  = useState(0);

  const handleUpload = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: '*/*',
        copyToCacheDirectory: true,
        multiple: true,
      } as any);
      if (result.canceled || !result.assets?.length) return;
      const assets = result.assets;

      // Ask which Work to link the documents to (optional). One prompt covers
      // all selected files so the user isn't interrupted N times.
      const uploadWorkId = await new Promise<string | null>((resolve) => {
        const workOptions = works.map((w: any) => w.title as string);
        const workIds    = works.map((w: any) => w.id as string);
        if (workOptions.length === 0) { resolve(null); return; }
        Alert.alert(
          'Link to a Work?',
          `Optionally assign ${assets.length === 1 ? 'this document' : `all ${assets.length} documents`} to an existing Work.`,
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

      setUploading(true);
      setUploadIndex(0);
      setUploadTotal(assets.length);

      const domain = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
      let importedCount  = 0;
      let duplicateCount = 0;
      let failedCount    = 0;
      let singleDocId: string | null = null;

      for (let i = 0; i < assets.length; i++) {
        const asset = assets[i];
        setUploadIndex(i + 1);
        // Reset to 0 for each file so the bar sweeps 0→100% per file.
        setUploadProgress(0);

        try {
          const form = new FormData();
          form.append('file', { uri: asset.uri, name: asset.name, type: asset.mimeType ?? 'application/octet-stream' } as any);
          if (uploadWorkId) form.append('work_id', uploadWorkId);

          // Use XHR so upload.onprogress fires with real byte counts.
          const resp = await new Promise<{ ok: boolean; status: number; json: () => Promise<any> }>(
            (resolve, reject) => {
              const xhr = new XMLHttpRequest();
              xhr.upload.onprogress = (e) => {
                if (e.lengthComputable) {
                  setUploadProgress(Math.round((e.loaded / e.total) * 100));
                }
              };
              xhr.onload = () => {
                const text = xhr.responseText;
                resolve({
                  ok: xhr.status >= 200 && xhr.status < 300,
                  status: xhr.status,
                  json: () => { try { return Promise.resolve(JSON.parse(text)); } catch { return Promise.resolve({}); } },
                });
              };
              xhr.onerror = () => reject(new Error('Network error during upload'));
              xhr.open('POST', `https://${domain}/api/library/upload`);
              const token = getApiToken();
              if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
              xhr.send(form as any);
            }
          );

          if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            const detail = (err as any).detail ?? 'Upload failed';
            failedCount++;
            // Per-file error toast — does not abort the remaining queue.
            Alert.alert('Upload failed', `"${asset.name}": ${detail}`);
            continue;
          }

          const data = await resp.json();

          if (data?.duplicate && data?.document?.id) {
            duplicateCount++;
            if (assets.length === 1) {
              // Single-file duplicate: mirror the old behaviour (prompt + navigate).
              Alert.alert(
                'Already in library',
                `"${asset.name}" is already imported. Opening the existing document.`,
                [{ text: 'OK', onPress: () => router.push(`/library/${data.document.id}` as any) }]
              );
            }
            // Multi-file duplicates: counted silently; summary shown at end.
          } else {
            importedCount++;
            if (data?.document?.id) singleDocId = data.document.id;
          }
        } catch (err: any) {
          failedCount++;
          Alert.alert('Upload error', `"${asset.name}": ${err?.message ?? 'Unknown error'}`);
        }
      }

      setUploadProgress(100);
      refetchList();

      // ── Post-run summary ──────────────────────────────────────────────────
      if (assets.length === 1) {
        // Single file: navigate to the new document (existing behaviour).
        if (importedCount === 1 && singleDocId) {
          router.push(`/library/${singleDocId}` as any);
        }
        // Duplicate and failed cases already showed their own alerts above.
      } else {
        // Multi-file: show a concise summary toast.
        const parts: string[] = [];
        if (importedCount)  parts.push(`${importedCount} imported`);
        if (duplicateCount) parts.push(`${duplicateCount} already existed`);
        if (failedCount)    parts.push(`${failedCount} failed`);
        Alert.alert(
          failedCount > 0 ? 'Import finished with errors' : 'Import complete',
          parts.join(' · ') || 'No files uploaded'
        );
      }
    } catch (err: any) {
      if (!String(err).includes('cancel')) {
        Alert.alert('Error', err?.message ?? 'Upload failed');
      }
    } finally {
      setUploading(false);
      setUploadProgress(0);
      setUploadIndex(0);
      setUploadTotal(0);
    }
  };

  const [cachedDocs, setCachedDocs] = useState<any[]>([]);
  const [usingListCache, setUsingListCache] = useState(false);

  const {
    data: listData,
    isLoading: listLoading,
    isError: listError,
    refetch: refetchList,
  } = useListLibrary(
    workFilter ? { work_id: workFilter } as any : {},
    { query: { refetchInterval: 30_000, staleTime: 20_000 } } as any,
  );

  // Persist last successful library list to AsyncStorage for offline fallback
  useEffect(() => {
    if (listData?.documents?.length) {
      const key = `library:list:${workFilter ?? 'all'}`;
      writeCache(key, listData.documents);
      setCachedDocs(listData.documents);
      setUsingListCache(false);
    }
  }, [listData?.documents, workFilter]);

  useEffect(() => {
    if (listError) {
      const key = `library:list:${workFilter ?? 'all'}`;
      readCache<any[]>(key).then(entry => {
        if (entry?.data?.length) {
          setCachedDocs(entry.data);
          setUsingListCache(true);
        }
      });
    } else {
      setUsingListCache(false);
    }
  }, [listError, workFilter]);

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
  const rawDocs: any[] = isSearching
    ? searchResults
    : (listError && usingListCache ? cachedDocs : (listData?.documents ?? []));
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
              opacity: uploading ? 0.38 : 1,
              marginTop: 4,
              minHeight: 44,
            })}
          >
            <Feather name={uploading ? 'loader' : 'upload'} size={14} color="#fff" />
            <Text style={{ color: '#fff', fontSize: 13, ...font('semibold') }}>
              {uploading
                ? uploadTotal > 1
                  ? `Uploading ${uploadIndex} of ${uploadTotal}…`
                  : 'Uploading…'
                : 'Import'}
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
                backgroundColor: T.gilt,
                borderRadius: 2,
              }}
            />
          </View>
        )}
      </View>

      {/* Search + filter toggle */}
      <View style={[styles.searchRow, { borderBottomColor: colors.border, backgroundColor: colors.background }]}>
        <Feather name="search" size={15} color={colors.mutedForeground} style={styles.searchIcon} />
        <TextInput
          style={[styles.searchInput, { color: colors.foreground, ...font('regular') }]}
          placeholder="Search documents…"
          placeholderTextColor={colors.mutedForeground}
          value={search}
          onChangeText={setSearch}
          returnKeyType="search"
        />
        {search.length > 0 && (
          <Pressable onPress={() => setSearch('')} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
            <Feather name="x" size={15} color={colors.mutedForeground} />
          </Pressable>
        )}
        {!isSearching && (
          <Pressable
            onPress={() => setShowFilters(v => !v)}
            hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
            style={{
              marginLeft: 6,
              paddingHorizontal: 8, paddingVertical: 4,
              borderRadius: 8,
              backgroundColor: (showFilters || workFilter) ? colors.primary + '18' : 'transparent',
              borderWidth: 1,
              borderColor: (showFilters || workFilter) ? colors.primary + '55' : colors.border,
            }}
          >
            <Feather
              name="sliders"
              size={14}
              color={(showFilters || workFilter) ? colors.primary : colors.mutedForeground}
            />
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
          contentContainerStyle={{ flexDirection: 'row', paddingHorizontal: 16, paddingVertical: 5, gap: 6 }}
          style={{ borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }}
        >
          {(['hybrid', 'keyword', 'semantic'] as const).map((mode) => {
            const label = mode === 'hybrid' ? 'Hybrid' : mode === 'keyword' ? 'Keyword' : 'Semantic';
            const active = searchMode === mode;
            return (
              <Pressable
                key={mode}
                onPress={() => setSearchMode(mode)}
                hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }}
                style={{
                  paddingHorizontal: 12,
                  paddingVertical: 5,
                  borderRadius: 12,
                  borderWidth: 1,
                  borderColor: active ? colors.primary : colors.border,
                  backgroundColor: active ? colors.primary + '18' : 'transparent',
                  minHeight: 44,
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Text style={{
                  fontSize: 12,
                  lineHeight: 18,
                  ...font('medium'),
                  color: active ? colors.primary : colors.mutedForeground,
                }}>
                  {label}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      {/* Embeddings unavailability notice — auto-hides every 15 s when circuit closes,
          or immediately when the user taps ✕ */}
      {isSearching && embeddingsDown && searchMode !== 'keyword' && !embeddingsBannerDismissed && (
        <View style={{
          flexDirection: 'row', alignItems: 'center', gap: 8,
          paddingHorizontal: 16, paddingVertical: 9,
          backgroundColor: T.giltSoft,
          borderBottomWidth: StyleSheet.hairlineWidth,
          borderBottomColor: T.giltLine,
        }}>
          <Feather name="alert-triangle" size={13} color={T.gilt} />
          <Text style={{ fontSize: 12, lineHeight: 18, ...font('regular'), color: T.gilt, flex: 1 }}>
            Semantic search is offline — showing keyword results only
          </Text>
          <Pressable
            onPress={() => setEmbeddingsBannerDismissed(true)}
            hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
            accessibilityLabel="Dismiss notice"
            accessibilityRole="button"
          >
            <Feather name="x" size={14} color={T.gilt} />
          </Pressable>
        </View>
      )}

      {/* Work filter chips — revealed by the ⊟ filter toggle */}
      {works.length > 0 && showFilters && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{
            flexDirection: 'row',
            paddingHorizontal: 16,
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
            hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }}
            style={{
              paddingHorizontal: 12,
              paddingVertical: 5,
              borderRadius: 12,
              borderWidth: 1,
              borderColor: !workFilter ? colors.primary : colors.border,
              backgroundColor: !workFilter ? colors.primary + '18' : 'transparent',
              minHeight: 44,
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{
              fontSize: 12,
              lineHeight: 18,
              ...font('medium'),
              color: !workFilter ? colors.primary : colors.mutedForeground,
            }}>All</Text>
          </Pressable>
          {works.slice(0, 6).map((w: any) => (
            <Pressable
              key={w.id}
              onPress={() => setWorkFilter(workFilter === w.id ? undefined : w.id)}
              hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }}
              style={{
                paddingHorizontal: 12,
                paddingVertical: 5,
                borderRadius: 12,
                borderWidth: 1,
                borderColor: workFilter === w.id ? colors.primary : colors.border,
                backgroundColor: workFilter === w.id ? colors.primary + '18' : 'transparent',
                minHeight: 44,
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Text
                style={{
                  fontSize: 12,
                  lineHeight: 18,
                  ...font('medium'),
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

      {/* Topics shortcut — browse library by semantic cluster */}
      {!isSearching && (
        <Pressable
          onPress={() => router.push('/topics' as any)}
          style={({ pressed }) => ({
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            marginHorizontal: 16,
            marginTop: 10,
            marginBottom: 2,
            paddingHorizontal: 14,
            paddingVertical: 10,
            borderRadius: 10,
            borderWidth: 1,
            borderColor: colors.border,
            backgroundColor: pressed ? colors.muted : colors.card,
            minHeight: 44,
          })}
          accessibilityRole="link"
          accessibilityLabel="Browse by Topic"
        >
          <Feather name="layers" size={15} color={colors.primary} />
          <Text style={{ flex: 1, fontSize: 13, lineHeight: 20, ...font('medium'), color: colors.foreground }}>
            Browse by Topic
          </Text>
          <Text style={{ fontSize: 11, lineHeight: 18, ...font('regular'), color: colors.mutedForeground }}>
            Semantic clusters
          </Text>
          <Feather name="chevron-right" size={14} color={colors.mutedForeground} />
        </Pressable>
      )}

      {/* Sort chips — revealed by the filter toggle when not searching */}
      {!isSearching && showFilters && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ flexDirection: 'row', paddingHorizontal: 16, paddingVertical: 5, gap: 6 }}
          style={{ borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }}
        >
          {(['newest', 'oldest', 'az', 'za'] as const).map((opt) => {
            const label = opt === 'newest' ? 'Newest' : opt === 'oldest' ? 'Oldest' : opt === 'az' ? 'A → Z' : 'Z → A';
            const active = sortBy === opt;
            return (
              <Pressable
                key={opt}
                onPress={() => setSortBy(opt)}
                hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }}
                style={{
                  paddingHorizontal: 12,
                  paddingVertical: 5,
                  borderRadius: 12,
                  borderWidth: 1,
                  borderColor: active ? colors.primary : colors.border,
                  backgroundColor: active ? colors.primary + '18' : 'transparent',
                  minHeight: 44,
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Text style={{ fontSize: 12, lineHeight: 18, ...font('medium'), color: active ? colors.primary : colors.mutedForeground }}>
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
        <View style={{ flex: 1, paddingHorizontal: 16, paddingTop: 12 }}>
          {[...Array(4)].map((_, i) => <SkeletonItem key={i} />)}
        </View>
      ) : listError && !hasData ? (
        <ErrorScreen
          message="Can't reach the server"
          detail="Make sure Orivellum is running and your device is on the same network."
          onRetry={refetchList}
        />
      ) : docs.length === 0 ? (
        <EmptyState
          icon="folder"
          title="No documents"
          body="Import files from the Dashboard or use the + button."
        />
      ) : (
        <FlatList
          data={docs}
          keyExtractor={(item) => item.id ?? item.source ?? Math.random().toString()}
          renderItem={({ item }) => (
            <DocItem
              doc={item}
              colors={colors}
              T={T}
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
            paddingBottom: insets.bottom + 24,
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
  title: { fontSize: 28, ...font('bold'), letterSpacing: -0.5 },
  subtitle: { fontSize: 13, ...font('regular'), lineHeight: 18, marginTop: 2 },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    gap: 8,
    minHeight: 44,
  },
  searchIcon: {},
  searchInput: { flex: 1, fontSize: 15, lineHeight: 22, ...font('regular'), paddingVertical: 0 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 32 },
  emptyTitle: { fontSize: 17, ...font('semibold'), lineHeight: 22, textAlign: 'center' },
  emptyText: { fontSize: 15, ...font('regular'), textAlign: 'center', lineHeight: 22 },
  docRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 10,
    borderWidth: 1,
    padding: 14,
    gap: 12,
    minHeight: 44,
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
  docTitle: { fontSize: 15, lineHeight: 22, ...font('semibold'), marginBottom: 4 },
  docBadges: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  kindBadge: { fontSize: 11, letterSpacing: 0.6, ...font('medium') },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusLabel: { fontSize: 12, lineHeight: 18, ...font('medium') },
  wordCount: { fontSize: 12, lineHeight: 18, ...font('regular') },
  lifecycleBadge: {
    fontSize: 11,
    lineHeight: 16,
    ...font('medium'),
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
});
