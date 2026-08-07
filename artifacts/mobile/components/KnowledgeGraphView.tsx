/**
 * KnowledgeGraphView — reusable work-scoped or global knowledge graph.
 *
 * Shared between:
 *   - artifacts/mobile/app/graph.tsx      (full-screen global/work view)
 *   - artifacts/mobile/app/work/[id].tsx  (work-detail "Graph" tab)
 *
 * Props:
 *   workId          — when set, fetches GET /api/graph?work_id=<id>&limit=120
 *                     when undefined, fetches GET /api/graph?limit=150 (global)
 *   onOpenFullGraph — callback for the "Open full graph" link (work-tab context)
 *   style           — optional outer View style override
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Dimensions,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import Animated, {
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Svg, { Circle, G, Line, Text as SvgText } from 'react-native-svg';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import { useColors } from '@/hooks/useColors';
import { useVellumTokens } from '@/lib/tokens';
import { Feather } from '@expo/vector-icons';
import { mobileFetch } from '@/lib/api';

// ── Constants ──────────────────────────────────────────────────────────────────

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');
const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API    = `https://${DOMAIN}/api`;

export const NODE_COLORS: Record<string, string> = {
  person:    '#527A8A',
  place:     '#3C6A4B',
  concept:   '#9A7B2E',
  theme:     '#9A7B2E',
  scripture: '#B2431E',
  document:  '#5C5443',
  file:      '#5C5443',
  default:   '#9A7B2E',
};

export const EDGE_COLORS: Record<string, string> = {
  supports:    '#3C6A4B',
  contradicts: '#B2431E',
  related:     '#8A9BA8',
};

export const ENTITY_KINDS = [
  { value: 'concept',   label: 'Concepts',  color: '#9A7B2E' },
  { value: 'person',    label: 'People',    color: '#527A8A' },
  { value: 'place',     label: 'Places',    color: '#3C6A4B' },
  { value: 'theme',     label: 'Themes',    color: '#9A7B2E' },
  { value: 'scripture', label: 'Scripture', color: '#B2431E' },
];

// ── Types ─────────────────────────────────────────────────────────────────────

export interface GNode {
  id: string;
  label: string;
  type: string;
  kind: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  work_id?: string;
  work_title?: string;
}

export interface GEdge {
  source: string;
  target: string;
  label?: string;
  type?: string;
}

export interface GraphData {
  nodes: GNode[];
  edges: GEdge[];
  node_count: number;
  edge_count: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

export function nodeColor(n: GNode): string {
  if (n.type === 'document') return NODE_COLORS.document;
  return NODE_COLORS[n.kind] ?? NODE_COLORS.default;
}

export function edgeColor(e: GEdge): string {
  return EDGE_COLORS[e.type ?? ''] ?? EDGE_COLORS.related;
}

export function nodeRadius(n: GNode, degree: number): number {
  const base = n.type === 'document' ? 7 : 9;
  return base + Math.min(degree * 1.4, 10);
}

// ── Force simulation (Fruchterman-Reingold, synchronous) ──────────────────────

export function runSimulation(
  rawNodes: GNode[],
  rawEdges: GEdge[],
  W: number,
  H: number,
): GNode[] {
  if (!rawNodes.length) return [];

  const nodes: GNode[] = rawNodes.map(n => ({
    ...n,
    x: n.x && n.x !== 0 ? n.x : W * 0.15 + Math.random() * W * 0.7,
    y: n.y && n.y !== 0 ? n.y : H * 0.15 + Math.random() * H * 0.7,
    vx: 0,
    vy: 0,
  }));

  const idxById = new Map(nodes.map((n, i) => [n.id, i] as [string, number]));
  const cx = W / 2, cy = H / 2;
  const k  = Math.sqrt((W * H) / Math.max(nodes.length, 1)) * 0.75;
  const ITERS = 100;

  for (let iter = 0; iter < ITERS; iter++) {
    const alpha = Math.max(0.01, 1 - iter / ITERS);
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx  = nodes[i].x - nodes[j].x || 0.1;
        const dy  = nodes[i].y - nodes[j].y || 0.1;
        const d   = Math.sqrt(dx * dx + dy * dy) || 0.1;
        const f   = (k * k) / d * alpha * 0.7;
        const fx  = (dx / d) * f, fy = (dy / d) * f;
        nodes[i].vx += fx; nodes[i].vy += fy;
        nodes[j].vx -= fx; nodes[j].vy -= fy;
      }
    }
    for (const e of rawEdges) {
      const si = idxById.get(e.source);
      const ti = idxById.get(e.target);
      if (si == null || ti == null) continue;
      const dx = nodes[ti].x - nodes[si].x;
      const dy = nodes[ti].y - nodes[si].y;
      const d  = Math.sqrt(dx * dx + dy * dy) || 0.1;
      const f  = (d * d) / k * alpha * 0.35;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      nodes[si].vx += fx; nodes[si].vy += fy;
      nodes[ti].vx -= fx; nodes[ti].vy -= fy;
    }
    for (const n of nodes) {
      n.vx += (cx - n.x) * 0.025 * alpha;
      n.vy += (cy - n.y) * 0.025 * alpha;
      n.vx *= 0.82;
      n.vy *= 0.82;
      n.x = Math.max(18, Math.min(W - 18, n.x + n.vx));
      n.y = Math.max(18, Math.min(H - 18, n.y + n.vy));
    }
  }
  return nodes;
}

// ── NodeDetailSheet ───────────────────────────────────────────────────────────

const _SHEET_H = 420;

export function NodeDetailSheet({
  node,
  visible,
  edges,
  allNodes,
  onClose,
}: {
  node: GNode | null;
  visible: boolean;
  edges: GEdge[];
  allNodes: GNode[];
  onClose: () => void;
}) {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const slideAnim = useSharedValue(_SHEET_H + 60);
  const fadeAnim  = useSharedValue(0);
  const [rendered, setRendered] = useState(false);

  useEffect(() => {
    if (visible) {
      setRendered(true);
      slideAnim.value = _SHEET_H + 60;
      fadeAnim.value  = 0;
      slideAnim.value = withSpring(0, { damping: 18, stiffness: 160 });
      fadeAnim.value  = withTiming(1, { duration: 160 });
    } else {
      slideAnim.value = withTiming(_SHEET_H + 60, { duration: 200 });
      fadeAnim.value  = withTiming(0, { duration: 160 }, (finished) => {
        'worklet';
        if (finished) runOnJS(setRendered)(false);
      });
    }
  }, [visible]);

  const sheetStyle    = useAnimatedStyle(() => ({ transform: [{ translateY: slideAnim.value }] }));
  const backdropStyle = useAnimatedStyle(() => ({ opacity: fadeAnim.value }));

  if (!rendered || !node) return null;

  const nodeById  = new Map(allNodes.map(n => [n.id, n]));
  const connected = edges.flatMap(e => {
    if (e.source === node.id) return nodeById.get(e.target) ? [{ n: nodeById.get(e.target)!, label: e.label ?? e.type ?? 'related' }] : [];
    if (e.target === node.id) return nodeById.get(e.source) ? [{ n: nodeById.get(e.source)!, label: e.label ?? e.type ?? 'related' }] : [];
    return [];
  }).slice(0, 8);

  const kindIcon = node.type === 'document' ? 'file-text' : 'cpu';
  const color    = nodeColor(node);

  return (
    <Modal transparent visible={visible} animationType="none" onRequestClose={onClose} statusBarTranslucent>
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.38)' }, backdropStyle]}
        pointerEvents={visible ? 'auto' : 'none'}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      <Animated.View style={[kgStyles.sheet, {
        backgroundColor: colors.card,
        borderColor: colors.border,
        paddingBottom: insets.bottom + 16,
      }, sheetStyle]}>
        <View style={[kgStyles.handle, { backgroundColor: colors.border }]} />

        <View style={kgStyles.sheetHeader}>
          <View style={[kgStyles.kindIcon, { backgroundColor: color + '22' }]}>
            <Feather name={kindIcon as any} size={16} color={color} />
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={[kgStyles.sheetTitle, { color: colors.foreground }]} numberOfLines={2}>
              {node.label}
            </Text>
            <Text style={[kgStyles.sheetMeta, { color: colors.mutedForeground }]}>
              {node.kind || node.type}
              {node.work_title ? `  ·  ${node.work_title}` : ''}
            </Text>
          </View>
          <Pressable onPress={onClose} hitSlop={10}>
            <Feather name="x" size={18} color={colors.mutedForeground} />
          </Pressable>
        </View>

        <ScrollView style={{ flex: 1, paddingHorizontal: 16 }} showsVerticalScrollIndicator={false}>
          {connected.length > 0 && (
            <>
              <Text style={[kgStyles.connLabel, { color: colors.mutedForeground }]}>
                CONNECTIONS ({connected.length})
              </Text>
              {connected.map(({ n, label }, i) => (
                <View key={i} style={[kgStyles.connRow, { borderColor: colors.border }]}>
                  <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: nodeColor(n), flexShrink: 0 }} />
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }} numberOfLines={1}>
                      {n.label}
                    </Text>
                    <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
                      {label}  ·  {n.kind}
                    </Text>
                  </View>
                  {n.type === 'document' && (
                    <Pressable
                      onPress={() => { onClose(); router.push(`/library/${n.id}` as any); }}
                      hitSlop={8}
                      style={({ pressed }) => [kgStyles.viewBtn, { borderColor: colors.border, opacity: pressed ? 0.6 : 1 }]}
                    >
                      <Feather name="arrow-right" size={13} color={colors.primary} />
                    </Pressable>
                  )}
                </View>
              ))}
            </>
          )}
          {connected.length === 0 && (
            <Text style={[kgStyles.connLabel, { color: colors.mutedForeground, marginTop: 8 }]}>
              No connections visible in current view.
            </Text>
          )}
          {node.type === 'document' && (
            <Pressable
              onPress={() => { onClose(); router.push(`/library/${node.id}` as any); }}
              style={({ pressed }) => [kgStyles.openBtn, {
                backgroundColor: pressed ? colors.primary + 'cc' : colors.primary,
                marginTop: 16,
              }]}
            >
              <Feather name="file-text" size={14} color="#fff" />
              <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>Open document</Text>
            </Pressable>
          )}
        </ScrollView>
      </Animated.View>
    </Modal>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────

export function GraphLegend({ colors }: { colors: ReturnType<typeof useColors> }) {
  return (
    <View style={[kgStyles.legend, { backgroundColor: colors.card + 'ee', borderColor: colors.border }]}>
      {([
        { color: EDGE_COLORS.supports,    label: 'Supports'    },
        { color: EDGE_COLORS.related,     label: 'Related'     },
        { color: EDGE_COLORS.contradicts, label: 'Contradicts' },
      ] as const).map(({ color, label }) => (
        <View key={label} style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
          <View style={{ width: 16, height: 2, backgroundColor: color, borderRadius: 1 }} />
          <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
            {label}
          </Text>
        </View>
      ))}
    </View>
  );
}

// ── KnowledgeGraphView ────────────────────────────────────────────────────────

export interface KnowledgeGraphViewProps {
  /** Scope graph to this work. Omit for global view. */
  workId?: string;
  /** Show an "Open full graph" footer link. */
  onOpenFullGraph?: () => void;
  /** Show a "Reprocess" shortcut in the empty state. */
  onReprocess?: () => void;
  style?: any;
}

export function KnowledgeGraphView({
  workId,
  onOpenFullGraph,
  onReprocess,
  style,
}: KnowledgeGraphViewProps) {
  const colors = useColors();
  const T      = useVellumTokens();

  /**
   * Map the static VELLUM_LIGHT hex constants used in ENTITY_KINDS to their
   * scheme-aware token counterparts.  Without this, dark-mode chip labels show
   * e.g. #3C6A4B (dark forest green) on a near-black background — ~2.5:1
   * contrast, which fails WCAG AA for small text.  All five ENTITY_KINDS now
   * have explicit VELLUM_DARK equivalents:
   *   #9A7B2E (gilt)  → T.gilt  = #C9A25A  (~6:1 on dark)
   *   #3C6A4B (green) → T.green = #8FC2A1  (~7:1 on dark)
   *   #B2431E (rust)  → T.rust  = #D46A43  (~5:1 on dark)
   *   #527A8A (slate) → T.slate = #89BDD3  (~5:1 on dark)
   */
  const chipColor = (staticColor: string): string => {
    if (staticColor === '#9A7B2E') return T.gilt;
    if (staticColor === '#3C6A4B') return T.green;
    if (staticColor === '#B2431E') return T.rust;
    if (staticColor === '#527A8A') return T.slate;
    return staticColor;
  };

  const [hiddenKinds,  setHiddenKinds]  = useState<Set<string>>(new Set());
  const [selectedNode, setSelectedNode] = useState<GNode | null>(null);
  const [sheetVisible, setSheetVisible] = useState(false);

  const [canvasDims, setCanvasDims] = useState({
    w: SCREEN_W,
    h: Math.max(100, SCREEN_H - 200),
  });
  const canvasW = canvasDims.w;
  const canvasH = canvasDims.h;

  // ── Fetch ──────────────────────────────────────────────────────────────────

  const url = workId
    ? `${API}/graph?work_id=${workId}&limit=120`
    : `${API}/graph?limit=150`;

  const { data, isLoading, isError, refetch } = useQuery<GraphData>({
    queryKey: ['graph', workId ?? 'global'],
    queryFn: async () => {
      const r = await mobileFetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    staleTime: 120_000,
  });

  // ── Simulation ─────────────────────────────────────────────────────────────

  const { simNodes, degreeMap } = useMemo(() => {
    if (!data?.nodes?.length) return { simNodes: [] as GNode[], degreeMap: new Map<string, number>() };
    const deg = new Map<string, number>();
    for (const e of (data.edges ?? [])) {
      deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
      deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
    }
    const visible = data.nodes.filter(n => n.type === 'document' || !hiddenKinds.has(n.kind));
    return { simNodes: runSimulation(visible, data.edges ?? [], canvasW, canvasH), degreeMap: deg };
  }, [data, hiddenKinds, canvasW, canvasH]);

  // ── Pan / pinch ────────────────────────────────────────────────────────────

  const txVal  = useSharedValue(0);
  const tyVal  = useSharedValue(0);
  const scaleV = useSharedValue(1);
  const savedTX    = useSharedValue(0);
  const savedTY    = useSharedValue(0);
  const savedScale = useSharedValue(1);
  const halfWV = useSharedValue(canvasW / 2);
  const halfHV = useSharedValue(canvasH / 2);

  useEffect(() => {
    halfWV.value = canvasW / 2;
    halfHV.value = canvasH / 2;
  }, [canvasW, canvasH]);

  const selectNodeAt = useCallback((svgX: number, svgY: number) => {
    let best: GNode | null = null;
    let bestDist = Infinity;
    for (const n of simNodes) {
      const dx = n.x - svgX, dy = n.y - svgY;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const r = nodeRadius(n, degreeMap.get(n.id) ?? 0) + 10;
      if (dist <= r && dist < bestDist) { best = n; bestDist = dist; }
    }
    if (best) { setSelectedNode(best); setSheetVisible(true); }
    else { setSheetVisible(false); }
  }, [simNodes, degreeMap]);

  const panGesture = Gesture.Pan()
    .onStart(() => { 'worklet'; savedTX.value = txVal.value; savedTY.value = tyVal.value; })
    .onUpdate(e => { 'worklet'; txVal.value = savedTX.value + e.translationX; tyVal.value = savedTY.value + e.translationY; })
    .onEnd(e => {
      'worklet';
      if (Math.abs(e.translationX) < 8 && Math.abs(e.translationY) < 8) {
        const hw   = halfWV.value;
        const hh   = halfHV.value;
        const svgX = hw + (e.x - txVal.value - hw) / scaleV.value;
        const svgY = hh + (e.y - tyVal.value - hh) / scaleV.value;
        runOnJS(selectNodeAt)(svgX, svgY);
      }
    });

  const pinchGesture = Gesture.Pinch()
    .onStart(() => { 'worklet'; savedScale.value = scaleV.value; })
    .onUpdate(e => { 'worklet'; scaleV.value = Math.min(4, Math.max(0.2, savedScale.value * e.scale)); });

  const composed  = Gesture.Simultaneous(panGesture, pinchGesture);
  const animStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: txVal.value }, { translateY: tyVal.value }, { scale: scaleV.value }],
  }));

  // ── SVG elements ───────────────────────────────────────────────────────────

  const nodeById = useMemo(() => new Map(simNodes.map(n => [n.id, n])), [simNodes]);

  const renderedEdges = useMemo(() =>
    (data?.edges ?? []).flatMap((e, i) => {
      const s = nodeById.get(e.source);
      const t = nodeById.get(e.target);
      if (!s || !t) return [];
      return [(<Line key={`e${i}`} x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke={edgeColor(e)} strokeWidth={1.5} strokeOpacity={0.4} />)];
    }),
  [data?.edges, nodeById]);

  const renderedNodes = useMemo(() =>
    simNodes.map(n => {
      const deg = degreeMap.get(n.id) ?? 0;
      const r   = nodeRadius(n, deg);
      const col = nodeColor(n);
      const isSelected = selectedNode?.id === n.id;
      const showLabel  = deg >= 3 || isSelected;
      const short      = n.label.length > 15 ? n.label.slice(0, 14) + '…' : n.label;
      return (
        <G key={n.id}>
          <Circle
            cx={n.x} cy={n.y}
            r={isSelected ? r + 3 : r}
            fill={col}
            fillOpacity={isSelected ? 1 : 0.78}
            stroke={isSelected ? '#fff' : col}
            strokeWidth={isSelected ? 2.5 : 0.8}
            strokeOpacity={0.9}
          />
          {showLabel && (
            // Use colors.foreground (not col) so labels are readable in both
            // light and dark mode. Node colors like #9A7B2E / #527A8A are too
            // dark to be legible on a dark canvas background.
            <SvgText x={n.x} y={n.y + r + 10} textAnchor="middle" fontSize={8.5} fill={colors.foreground} fillOpacity={0.75} fontFamily="Inter_500Medium">
              {short}
            </SvgText>
          )}
        </G>
      );
    }),
  [simNodes, degreeMap, selectedNode, colors.foreground]);

  // ── Kind filter toggle ─────────────────────────────────────────────────────

  const toggleKind = useCallback((kind: string) => {
    setHiddenKinds(prev => {
      const next = new Set(prev);
      next.has(kind) ? next.delete(kind) : next.add(kind);
      return next;
    });
  }, []);

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <View style={[{ flex: 1 }, style]}>
      {/* Filter chips + stats + refresh */}
      <View style={{ backgroundColor: colors.background, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border }}>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={{ height: 44, flexGrow: 0 }}
          contentContainerStyle={{ paddingHorizontal: 12, alignItems: 'center', gap: 6, paddingVertical: 6 }}
        >
          {ENTITY_KINDS.map(({ value, label, color }) => {
            const active = !hiddenKinds.has(value);
            // chipColor() maps static VELLUM_LIGHT hex values to scheme-aware
            // tokens so labels are readable in dark mode (see definition above).
            const cc = chipColor(color);
            return (
              <Pressable
                key={value}
                onPress={() => toggleKind(value)}
                style={[kgStyles.chip, {
                  backgroundColor: active ? cc + '22' : colors.muted,
                  borderColor:     active ? cc : 'transparent',
                }]}
              >
                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: active ? cc : colors.mutedForeground }} />
                <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: active ? cc : colors.mutedForeground }}>
                  {label}
                </Text>
              </Pressable>
            );
          })}
          {/* Spacer + refresh */}
          <View style={{ width: 8 }} />
          <Pressable onPress={() => refetch()} hitSlop={8} style={{ opacity: 0.7 }}>
            <Feather name="refresh-cw" size={15} color={colors.mutedForeground} />
          </Pressable>
        </ScrollView>
        {/* Node / edge count */}
        {data && !isLoading && (
          <View style={{ paddingHorizontal: 14, paddingBottom: 5 }}>
            <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>
              {data.node_count} nodes · {data.edge_count} edges
            </Text>
          </View>
        )}
        {/* Edge type legend */}
        {data && !isLoading && data.edge_count > 0 && (
          <View style={{ paddingHorizontal: 14, paddingBottom: 6, flexDirection: 'row', gap: 12 }}>
            {[
              { label: 'Supports', color: '#3C6A4B' },
              { label: 'Contradicts', color: '#B2431E' },
              { label: 'Related', color: '#8A9BA8' },
            ].map(({ label, color }) => (
              <View key={label} style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <View style={{ width: 16, height: 2, backgroundColor: color, borderRadius: 1, opacity: 0.8 }} />
                <Text style={{ fontSize: 10, fontFamily: 'Inter_400Regular', color: colors.mutedForeground }}>{label}</Text>
              </View>
            ))}
          </View>
        )}
      </View>

      {/* Graph canvas — backgroundColor: colors.card gives the canvas a distinct
          themed surface in both light and dark mode.  In light mode this is a
          warm off-white; in dark mode it is a slightly elevated dark surface
          that lifts the graph away from the screen background. */}
      <View
        style={{ flex: 1, overflow: 'hidden', backgroundColor: colors.card }}
        onLayout={e => {
          const { width, height } = e.nativeEvent.layout;
          if (width > 0 && height > 0) setCanvasDims({ w: width, h: height });
        }}
      >
        {isLoading && (
          <View style={kgStyles.centreAbs}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={[kgStyles.loadText, { color: colors.mutedForeground }]}>Laying out graph…</Text>
          </View>
        )}

        {isError && !isLoading && (
          <View style={kgStyles.centreAbs}>
            <Feather name="wifi-off" size={36} color={colors.mutedForeground} style={{ opacity: 0.5 }} />
            <Text style={[kgStyles.loadText, { color: colors.mutedForeground }]}>Could not load graph data.</Text>
            <Pressable
              onPress={() => refetch()}
              style={({ pressed }) => [kgStyles.retryBtn, { borderColor: colors.border, backgroundColor: pressed ? colors.muted : 'transparent' }]}
            >
              <Feather name="refresh-cw" size={13} color={colors.foreground} />
              <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}>Retry</Text>
            </Pressable>
          </View>
        )}

        {!isLoading && !isError && simNodes.length === 0 && (
          <View style={kgStyles.centreAbs}>
            <Feather name="share-2" size={36} color={colors.mutedForeground} style={{ opacity: 0.4 }} />
            <Text style={[kgStyles.loadText, { color: colors.mutedForeground }]}>
              No entities yet.{'\n'}Process some documents first.
            </Text>
            {onReprocess && (
              <Pressable
                onPress={onReprocess}
                style={({ pressed }) => [kgStyles.retryBtn, { borderColor: colors.border, backgroundColor: pressed ? colors.muted : 'transparent' }]}
              >
                <Feather name="refresh-cw" size={13} color={colors.primary} />
                <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.primary }}>Reprocess documents</Text>
              </Pressable>
            )}
          </View>
        )}

        {!isLoading && simNodes.length > 0 && (
          <GestureDetector gesture={composed}>
            <Animated.View style={[{ width: canvasW, height: canvasH }, animStyle]}>
              <Svg width={canvasW} height={canvasH}>
                {renderedEdges}
                {renderedNodes}
              </Svg>
            </Animated.View>
          </GestureDetector>
        )}
      </View>

      {/* Legend */}
      <GraphLegend colors={colors} />

      {/* "Open full graph" footer — only when embedded in a tab */}
      {onOpenFullGraph && (
        <Pressable
          onPress={onOpenFullGraph}
          style={({ pressed }) => ({
            flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
            paddingVertical: 10, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border,
            backgroundColor: pressed ? colors.muted : 'transparent',
          })}
        >
          <Feather name="external-link" size={13} color={colors.primary} />
          <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.primary }}>
            Open full graph
          </Text>
        </Pressable>
      )}

      {/* Node detail sheet */}
      <NodeDetailSheet
        node={selectedNode}
        visible={sheetVisible}
        edges={data?.edges ?? []}
        allNodes={simNodes}
        onClose={() => setSheetVisible(false)}
      />
    </View>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const kgStyles = StyleSheet.create({
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 20, borderWidth: 1,
  },
  legend: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 16, paddingVertical: 8, paddingHorizontal: 16,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  centreAbs: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center', gap: 12 },
  loadText: { fontSize: 13, fontFamily: 'Inter_400Regular', textAlign: 'center' },
  retryBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: 8, borderWidth: 1,
  },
  // Sheet
  sheet: {
    position: 'absolute', bottom: 0, left: 0, right: 0,
    borderTopLeftRadius: 20, borderTopRightRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    paddingTop: 8, maxHeight: '70%',
    shadowColor: '#000', shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.12, shadowRadius: 14, elevation: 24,
  },
  handle: { width: 36, height: 4, borderRadius: 2, alignSelf: 'center', marginBottom: 12 },
  sheetHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingHorizontal: 16, marginBottom: 12 },
  kindIcon: { width: 36, height: 36, borderRadius: 8, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  sheetTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold', lineHeight: 20 },
  sheetMeta:  { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 2, textTransform: 'capitalize' },
  connLabel:  { fontSize: 10, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 },
  connRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 7, borderBottomWidth: StyleSheet.hairlineWidth },
  viewBtn: { width: 28, height: 28, borderRadius: 6, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  openBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7, paddingVertical: 12, borderRadius: 10, marginBottom: 8 },
});
