import React from 'react';
import { mobileFetch } from '@/lib/api';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { Feather } from '@expo/vector-icons';
import { useColors } from '@/hooks/useColors';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useGetDocument } from '@workspace/api-client-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';

const READINESS_COLOR: Record<string, string> = {
  ready: '#4A8C65',
  error: '#dc2626',
  failed: '#dc2626',
  imported: '#d97706',
};

const READINESS_LABEL: Record<string, string> = {
  ready: 'Ready',
  error: 'Error',
  failed: 'Failed',
  imported: 'Processing…',
};

export default function LibraryDocDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const navigation = useNavigation();
  const isWeb = Platform.OS === 'web';

  const { data: docData, isLoading: docLoading, isError: docError } =
    useGetDocument(id ?? '', { query: { enabled: !!id, staleTime: 15_000 } } as any);
  const { data: knData, isLoading: knLoading } = useQuery({
    queryKey: ['library-knowledge', id],
    queryFn: async () => {
      const domain = process.env.EXPO_PUBLIC_DOMAIN;
      const res = await mobileFetch(`https://${domain}/api/library/${id}/knowledge`);
      if (!res.ok) throw new Error('Failed to load knowledge');
      return res.json();
    },
    enabled: !!id,
    staleTime: 30_000,
  });

  const doc = (docData as any)?.document;
  const knowledge = (knData as any)?.knowledge ?? [];

  useEffect(() => {
    if (doc?.title) navigation.setOptions({ title: doc.title });
  }, [doc?.title]);

  const topPad = isWeb ? 67 : insets.top;

  if (docLoading) {
    return (
      <View style={[styles.container, styles.centered, { backgroundColor: colors.background }]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (docError || !doc) {
    return (
      <View style={[styles.container, styles.centered, { backgroundColor: colors.background }]}>
        <Feather name="alert-circle" size={32} color={colors.mutedForeground} />
        <Text style={[styles.emptyText, { color: colors.mutedForeground, marginTop: 12 }]}>
          Could not load document
        </Text>
        <Pressable
          onPress={() => router.back()}
          style={[styles.backBtn, { borderColor: colors.border }]}
        >
          <Text style={[styles.backBtnText, { color: colors.foreground }]}>Go back</Text>
        </Pressable>
      </View>
    );
  }

  const readinessColor = READINESS_COLOR[doc.readiness ?? 'imported'] ?? colors.mutedForeground;
  const readinessLabel = READINESS_LABEL[doc.readiness ?? 'imported'] ?? doc.readiness;
  const docTitle = doc.title || doc.source?.split('/').pop() || 'Untitled';

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      {/* Header */}
      <View
        style={[
          styles.header,
          {
            paddingTop: topPad + 8,
            borderBottomColor: colors.border,
            backgroundColor: colors.background,
          },
        ]}
      >
        <Pressable onPress={() => router.back()} style={styles.backRow} hitSlop={8}>
          <Feather name="arrow-left" size={18} color={colors.primary} />
          <Text style={[styles.backLabel, { color: colors.primary }]}>Library</Text>
        </Pressable>
        <Text style={[styles.title, { color: colors.foreground }]} numberOfLines={2}>
          {docTitle}
        </Text>
        <View style={styles.metaRow}>
          <View style={[styles.badge, { backgroundColor: colors.muted }]}>
            <Text style={[styles.badgeText, { color: colors.foreground }]}>
              {(doc.kind ?? 'file').toUpperCase()}
            </Text>
          </View>
          <View style={[styles.badge, { backgroundColor: readinessColor + '22' }]}>
            <Text style={[styles.badgeText, { color: readinessColor }]}>{readinessLabel}</Text>
          </View>
          {doc.word_count ? (
            <Text style={[styles.metaText, { color: colors.mutedForeground }]}>
              {doc.word_count.toLocaleString()} words
            </Text>
          ) : null}
        </View>
      </View>

      <ScrollView
        contentContainerStyle={{
          padding: 16,
          paddingBottom: isWeb ? 50 : insets.bottom + 40,
        }}
      >
        {/* Error message */}
        {doc.error_message && (
          <View style={[styles.errorBox, { backgroundColor: '#fee2e2', borderColor: '#fca5a5' }]}>
            <Feather name="alert-triangle" size={14} color="#dc2626" />
            <Text style={[styles.errorText, { color: '#dc2626' }]}>{doc.error_message}</Text>
          </View>
        )}

        {/* Overview */}
        <View style={[styles.section, { borderColor: colors.border }]}>
          <Text style={[styles.sectionTitle, { color: colors.mutedForeground }]}>OVERVIEW</Text>
          {doc.source && (
            <View style={styles.row}>
              <Text style={[styles.rowLabel, { color: colors.mutedForeground }]}>Source</Text>
              <Text style={[styles.rowValue, { color: colors.foreground }]} numberOfLines={2}>
                {doc.source.split('/').pop()}
              </Text>
            </View>
          )}
          {doc.created_at && (
            <View style={styles.row}>
              <Text style={[styles.rowLabel, { color: colors.mutedForeground }]}>Imported</Text>
              <Text style={[styles.rowValue, { color: colors.foreground }]}>
                {new Date(doc.created_at).toLocaleDateString()}
              </Text>
            </View>
          )}
          {doc.chunk_count != null && (
            <View style={styles.row}>
              <Text style={[styles.rowLabel, { color: colors.mutedForeground }]}>Chunks</Text>
              <Text style={[styles.rowValue, { color: colors.foreground }]}>{doc.chunk_count}</Text>
            </View>
          )}
        </View>

        {/* Knowledge */}
        <View style={[styles.section, { borderColor: colors.border }]}>
          <Text style={[styles.sectionTitle, { color: colors.mutedForeground }]}>
            KNOWLEDGE {knowledge.length > 0 ? `(${knowledge.length})` : ''}
          </Text>
          {knLoading ? (
            <ActivityIndicator color={colors.primary} style={{ marginVertical: 12 }} />
          ) : knowledge.length === 0 ? (
            <Text style={[styles.emptyText, { color: colors.mutedForeground }]}>
              No knowledge extracted yet
            </Text>
          ) : (
            knowledge.map((item: any) => (
              <View
                key={item.id}
                style={[styles.knowledgeItem, { borderColor: colors.border, backgroundColor: colors.muted + '55' }]}
              >
                <Text style={[styles.knText, { color: colors.foreground }]}>{item.text}</Text>
                <Text style={[styles.knMeta, { color: colors.mutedForeground }]}>
                  {item.kind} · {Math.round((item.confidence ?? 0) * 100)}%
                  {item.review_status === 'ai_auto' || item.source === 'llm' ? ' · ✦ AI' : ''}
                </Text>
              </View>
            ))
          )}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: {
    paddingHorizontal: 16,
    paddingBottom: 14,
    borderBottomWidth: 1,
  },
  backRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 },
  backLabel: { fontSize: 14, fontFamily: 'Inter_500Medium' },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', letterSpacing: -0.3, marginBottom: 8 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 5,
  },
  badgeText: { fontSize: 11, fontFamily: 'Inter_600SemiBold', letterSpacing: 0.3 },
  metaText: { fontSize: 12, fontFamily: 'Inter_400Regular' },
  section: {
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    marginBottom: 14,
  },
  sectionTitle: { fontSize: 10, fontFamily: 'Inter_700Bold', letterSpacing: 1, marginBottom: 10 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 },
  rowLabel: { fontSize: 13, fontFamily: 'Inter_400Regular', flex: 1 },
  rowValue: { fontSize: 13, fontFamily: 'Inter_500Medium', flex: 2, textAlign: 'right' },
  errorBox: {
    flexDirection: 'row',
    gap: 8,
    padding: 12,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 14,
    alignItems: 'flex-start',
  },
  errorText: { fontSize: 13, fontFamily: 'Inter_400Regular', flex: 1 },
  knowledgeItem: {
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 8,
  },
  knText: { fontSize: 13, fontFamily: 'Inter_400Regular', lineHeight: 18 },
  knMeta: { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 4 },
  emptyText: { fontSize: 13, fontFamily: 'Inter_400Regular', textAlign: 'center', marginVertical: 12 },
  backBtn: {
    marginTop: 16,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
  },
  backBtnText: { fontSize: 14, fontFamily: 'Inter_500Medium' },
});
