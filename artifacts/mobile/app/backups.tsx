import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useQuery } from '@tanstack/react-query';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
// expo-file-system v19 ships the legacy (v18-compat) API under /legacy
// eslint-disable-next-line @typescript-eslint/no-var-requires
const FileSystem = require('expo-file-system/legacy') as typeof import('expo-file-system/legacy');
import * as Sharing from 'expo-sharing';
import { mobileFetch } from '@/lib/api';
import { useRouter, Stack } from 'expo-router';
import { useVellumTokens } from '@/lib/tokens';
import { font } from '@/lib/typography';
import { apiOrigin } from '@/lib/server';

// ── Constants ──────────────────────────────────────────────────────────────────

const DOMAIN = () => apiOrigin(); // API origin (user-configurable server)
const API = () => `${DOMAIN()}/api`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface Backup {
  name: string;
  size_bytes: number;
  created_at: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const n = new Date();
  return (
    d.getFullYear() === n.getFullYear() &&
    d.getMonth()    === n.getMonth()    &&
    d.getDate()     === n.getDate()
  );
}

// ── Backup row ─────────────────────────────────────────────────────────────────

function BackupRow({
  backup,
  downloading,
  onDownload,
}: {
  backup: Backup;
  downloading: boolean;
  onDownload: () => void;
}) {
  const colors = useColors();
  const today  = isToday(backup.created_at);
  const label  = backup.name
    .replace('orivellum_backup_', '')
    .replace('.zip', '')
    .replace(/_/g, ' ');

  return (
    <View style={[bkStyles.row, { backgroundColor: colors.card, borderColor: colors.border }]}>
      {/* Icon */}
      <View
        style={[
          bkStyles.rowIcon,
          { backgroundColor: today ? colors.primary + '18' : colors.muted },
        ]}
      >
        <Feather
          name="archive"
          size={16}
          color={today ? colors.primary : colors.mutedForeground}
        />
      </View>

      {/* Info */}
      <View style={{ flex: 1, minWidth: 0 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'nowrap' }}>
          <Text style={[bkStyles.rowName, { color: colors.foreground }]} numberOfLines={1}>
            {label}
          </Text>
          {today && (
            <View style={[bkStyles.todayBadge, { backgroundColor: colors.primary + '20' }]}>
              <Text style={{ fontSize: 9, ...font('semibold'), color: colors.primary }}>
                TODAY
              </Text>
            </View>
          )}
        </View>
        <Text style={[bkStyles.rowMeta, { color: colors.mutedForeground }]}>
          {fmtDate(backup.created_at)} · {fmtBytes(backup.size_bytes)}
        </Text>
      </View>

      {/* Download button */}
      <Pressable
        onPress={onDownload}
        disabled={downloading}
        hitSlop={8}
        style={({ pressed }) => [
          bkStyles.dlBtn,
          {
            borderColor: colors.border,
            backgroundColor: pressed ? colors.muted : 'transparent',
            opacity: downloading ? 0.45 : 1,
          },
        ]}
        accessibilityLabel={`Download ${backup.name}`}
        accessibilityRole="button"
      >
        {downloading ? (
          <ActivityIndicator
            size="small"
            color={colors.primary}
            style={{ transform: [{ scale: 0.7 }] }}
          />
        ) : (
          <Feather name="download" size={15} color={colors.primary} />
        )}
      </Pressable>
    </View>
  );
}

// ── Screen ─────────────────────────────────────────────────────────────────────

export default function BackupsScreen() {
  const colors = useColors();
  const T = useVellumTokens();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [creating, setCreating]         = useState(false);
  const [createErr, setCreateErr]       = useState('');
  const [dlName, setDlName]             = useState<string | null>(null);

  // ── List query ──────────────────────────────────────────────────────────────

  const { data, isLoading, isError, refetch } = useQuery<{ backups: Backup[]; count: number }>({
    queryKey: ['mobile', 'backups'],
    queryFn: async () => {
      const r = await mobileFetch(`${API()}/backups`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 30_000,
    staleTime: 25_000,
  });

  const backups        = data?.backups ?? [];
  const latestBackup   = backups[0] ?? null;
  const todayExists    = !!latestBackup && isToday(latestBackup.created_at);

  // ── Create ──────────────────────────────────────────────────────────────────

  const handleCreate = useCallback(async () => {
    setCreating(true);
    setCreateErr('');
    try {
      const r = await mobileFetch(`${API()}/backups`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refetch();
    } catch (e: any) {
      setCreateErr(e?.message ?? 'Backup creation failed');
    } finally {
      setCreating(false);
    }
  }, [refetch]);

  // ── Download & share ────────────────────────────────────────────────────────
  //
  // mobileFetch carries the auth header; we fetch the ZIP via it, read as
  // arrayBuffer, base64-encode, write to FileSystem cache, then hand off to
  // the system share sheet via expo-sharing.

  const handleDownload = useCallback(async (backup: Backup) => {
    setDlName(backup.name);
    try {
      // 1. Fetch with auth
      const r = await mobileFetch(
        `${API()}/backups/${encodeURIComponent(backup.name)}/download`,
      );
      if (!r.ok) throw new Error(`Server returned HTTP ${r.status}`);

      // 2. Read as arrayBuffer → Uint8Array → base64
      const buf    = await r.arrayBuffer();
      const bytes  = new Uint8Array(buf);
      const chunkSize = 0x8000;
      let binary = '';
      for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
      }
      const b64 = btoa(binary);

      // 3. Write to cache directory
      const dest = `${FileSystem.cacheDirectory}${backup.name}`;
      await FileSystem.writeAsStringAsync(dest, b64, {
        encoding: FileSystem.EncodingType.Base64,
      });

      // 4. Share / save via system sheet
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(dest, {
          mimeType: 'application/zip',
          dialogTitle: `Save ${backup.name}`,
          UTI: 'public.zip-archive',
        });
      } else {
        Alert.alert(
          'Saved to cache',
          `Backup saved to app cache:\n${dest}\n\nOpen the Files app to find it.`,
        );
      }
    } catch (e: any) {
      Alert.alert('Download failed', e?.message ?? 'Unknown error. Please try again.');
    } finally {
      setDlName(null);
    }
  }, []);

  // ── Primary action: create new, or download today's if it already exists ──

  const primaryBusy = creating || dlName === latestBackup?.name;

  const handlePrimary = useCallback(() => {
    if (todayExists && latestBackup) {
      handleDownload(latestBackup);
    } else {
      handleCreate();
    }
  }, [todayExists, latestBackup, handleDownload, handleCreate]);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <View style={[bkStyles.container, { backgroundColor: colors.background }]}>
      <Stack.Screen options={{ title: 'Backups', headerShown: true, headerStyle: { backgroundColor: colors.background }, headerTintColor: colors.foreground }} />
      {/* Header */}
      <View
        style={[
          bkStyles.header,
          {
            paddingTop: insets.top + 12,
            borderBottomColor: colors.border,
            backgroundColor: colors.card,
          },
        ]}
      >
        <Pressable
          onPress={() => router.back()}
          hitSlop={10}
          style={bkStyles.backBtn}
          accessibilityRole="button"
          accessibilityLabel="Back"
        >
          <Feather name="arrow-left" size={20} color={colors.foreground} />
        </Pressable>
        <Text style={[bkStyles.headerTitle, { color: colors.foreground }]}>
          Backups
        </Text>
        {/* Spacer so title stays centred */}
        <View style={{ width: 36 }} />
      </View>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={[bkStyles.scroll, { paddingBottom: insets.bottom + 32 }]}
        showsVerticalScrollIndicator={false}
      >
        {/* Info banner */}
        <View style={[bkStyles.infoCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
          <View style={[bkStyles.infoIcon, { backgroundColor: colors.primary + '18' }]}>
            <Feather name="hard-drive" size={22} color={colors.primary} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={[bkStyles.infoTitle, { color: colors.foreground }]}>
              Data Safety
            </Text>
            <Text style={[bkStyles.infoSub, { color: colors.mutedForeground }]}>
              Backups package your database and library files into a ZIP archive.
              Nightshift creates one automatically each night.
            </Text>
          </View>
        </View>

        {/* Primary action button */}
        <Pressable
          onPress={handlePrimary}
          disabled={primaryBusy}
          style={({ pressed }) => [
            bkStyles.primaryBtn,
            {
              backgroundColor: pressed ? colors.primary + 'cc' : colors.primary,
              opacity: primaryBusy ? 0.6 : 1,
            },
          ]}
          accessibilityRole="button"
          accessibilityLabel={todayExists ? "Download today's backup" : 'Create backup'}
        >
          {primaryBusy ? (
            <ActivityIndicator
              size="small"
              color="#fff"
              style={{ transform: [{ scale: 0.8 }] }}
            />
          ) : (
            <Feather
              name={todayExists ? 'download' : 'shield'}
              size={16}
              color="#fff"
            />
          )}
          <Text style={bkStyles.primaryBtnText}>
            {creating
              ? 'Creating backup…'
              : todayExists && dlName === latestBackup?.name
                ? 'Preparing file…'
                : todayExists
                  ? "Download today's backup"
                  : 'Create backup'}
          </Text>
        </Pressable>

        {!!createErr && (
          <Text style={[bkStyles.errMsg, { color: T.rust }]}>{createErr}</Text>
        )}

        {/* Restore note */}
        <View style={[bkStyles.noteCard, { backgroundColor: colors.muted + '50', borderColor: colors.border }]}>
          <Feather name="info" size={13} color={colors.mutedForeground} style={{ marginTop: 1 }} />
          <Text style={[bkStyles.noteText, { color: colors.mutedForeground }]}>
            <Text style={{ ...font('semibold') }}>Restore requires the desktop web interface.</Text>
            {'  '}Save the ZIP to Files or send via AirDrop, then use the web Backups page to restore.
          </Text>
        </View>

        {/* Backup list */}
        <Text style={[bkStyles.sectionLabel, { color: colors.mutedForeground }]}>
          SAVED BACKUPS
        </Text>

        {isLoading && (
          <View style={bkStyles.centreRow}>
            <ActivityIndicator color={colors.primary} />
          </View>
        )}

        {!isLoading && isError && (
          <View style={[bkStyles.emptyCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Feather name="wifi-off" size={32} color={colors.mutedForeground} style={{ opacity: 0.5 }} />
            <Text style={[bkStyles.emptyText, { color: colors.mutedForeground }]}>
              Could not load backups. Check your server connection.
            </Text>
            <Pressable
              onPress={() => refetch()}
              style={({ pressed }) => [
                bkStyles.retryBtn,
                { borderColor: colors.border, backgroundColor: pressed ? colors.muted : 'transparent' },
              ]}
            >
              <Feather name="refresh-cw" size={13} color={colors.foreground} />
              <Text style={[bkStyles.retryText, { color: colors.foreground }]}>Retry</Text>
            </Pressable>
          </View>
        )}

        {!isLoading && !isError && backups.length === 0 && (
          <View style={[bkStyles.emptyCard, { backgroundColor: colors.card, borderColor: colors.border }]}>
            <Feather name="hard-drive" size={32} color={colors.mutedForeground} style={{ opacity: 0.4 }} />
            <Text style={[bkStyles.emptyText, { color: colors.mutedForeground }]}>
              No backups yet. Tap "Create backup" to make your first snapshot.
            </Text>
          </View>
        )}

        {backups.map(b => (
          <BackupRow
            key={b.name}
            backup={b}
            downloading={dlName === b.name}
            onDownload={() => handleDownload(b)}
          />
        ))}
      </ScrollView>
    </View>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const bkStyles = StyleSheet.create({
  container: { flex: 1 },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  backBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { fontSize: 17, ...font('semibold') },

  scroll: { padding: 16, gap: 12 },

  infoCard: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    padding: 14,
    borderRadius: 10,
    borderWidth: 1,
  },
  infoIcon: {
    width: 42, height: 42, borderRadius: 10,
    alignItems: 'center', justifyContent: 'center',
    flexShrink: 0,
  },
  infoTitle: { fontSize: 14, ...font('semibold'), marginBottom: 3 },
  infoSub:   { fontSize: 12, ...font('regular'), lineHeight: 17 },

  primaryBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 8, paddingVertical: 14, borderRadius: 12,
  },
  primaryBtnText: { fontSize: 15, ...font('semibold'), color: '#fff' },

  errMsg: { fontSize: 12, ...font('regular'), textAlign: 'center' },

  noteCard: {
    flexDirection: 'row', alignItems: 'flex-start',
    gap: 8, padding: 12, borderRadius: 8, borderWidth: 1,
  },
  noteText: { flex: 1, fontSize: 12, ...font('regular'), lineHeight: 17 },

  sectionLabel: {
    fontSize: 11, ...font('semibold'),
    textTransform: 'uppercase', letterSpacing: 0.8,
    marginTop: 8,
  },

  centreRow: { alignItems: 'center', paddingVertical: 24 },

  emptyCard: {
    alignItems: 'center', gap: 10, padding: 32,
    borderRadius: 10, borderWidth: 1,
  },
  emptyText: {
    fontSize: 13, ...font('regular'),
    textAlign: 'center', lineHeight: 19,
  },

  retryBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 16, paddingVertical: 8,
    borderRadius: 8, borderWidth: 1,
  },
  retryText: { fontSize: 12, ...font('medium') },

  row: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    padding: 12, borderRadius: 10, borderWidth: 1,
  },
  rowIcon: {
    width: 36, height: 36, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  rowName: { fontSize: 13, ...font('medium'), flexShrink: 1 },
  rowMeta: { fontSize: 11, ...font('regular'), marginTop: 2 },
  todayBadge: { paddingHorizontal: 5, paddingVertical: 2, borderRadius: 4, flexShrink: 0 },

  dlBtn: {
    width: 34, height: 34, borderRadius: 8, borderWidth: 1,
    alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
});
