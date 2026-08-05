/**
 * Knowledge Graph Browser — full-screen SVG force-directed graph.
 *
 * Reachable via /graph?work_id=<id> from the Work Intelligence tab.
 * Supports pan (drag), pinch-to-zoom, node tap (detail sheet), entity-kind
 * filter chips, and a Work ↔ Global toggle.
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
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';
import { useColors } from '@/hooks/useColors';
import { Feather } from '@expo/vector-icons';
import { mobileFetch } from '@/lib/api';

// ── Constants ──────────────────────────────────────────────────────────────────

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');
const DOMAIN = process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000';
const API    = `https://${DOMAIN}/api`;

const NODE_COLORS: Record<string, string> = {
  person:    '#6366f1',
  place:     '#10b981',
  concept:   '#8b5cf6',
  theme:     '#f59e0b',
  scripture: '#ef4444',
  document:  '#64748b',
  file:      '#64748b',
  default:   '#a855f7',
};

const EDGE_COLORS: Record<string, string> = {
  supports:    '#22c55e',
  contradicts: '#ef4444',
  related:     '#94a3b8',
};

const ENTITY_KINDS = [
  { value: 'concept',   label: 'Concepts',  color: '#8b5cf6' },
  { value: 'person',    label: 'People',    color: '#6366f1' },
  { value: 'place',     label: 'Places',    color: '#10b981' },
  { value: 'theme',     label: 'Themes',    color: '#f59e0b' },
  { value: 'scripture', label: 'Scripture', color: '#ef4444' },
];

// ── Types ─────────────────────────────────────────────────────────────────────

interface GNode {
  id: string;
  label: string;
  type: string;   // 'document' | 'entity'
  kind: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  work_id?: string;
  work_title?: string;
}

interface GEdge {
  source: string;
  target: string;
  label?: string;
  type?: string;
}

interface GraphData {
  nodes: GNode[];
  edges: GEdge[];
  node_count: number;
  edge_count: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function nodeColor(n: GNode): string {
  if (n.type === 'document') return NODE_COLORS.document;
  return NODE_COLORS[n.kind] ?? NODE_COLORS.default;
}

function edgeColor(e: GEdge): string {
  return EDGE_COLORS[e.type ?? ''] ?? EDGE_COLORS.related;
}

function nodeRadius(n: GNode, degree: number): number {
  const base = n.type === 'document' ? 7 : 9;
  return base + Math.min(degree * 1.4, 10);
}

// ── Force simulation (Fruchterman-Reingold, synchronous) ──────────────────────

function runSimulation(
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

    // Repulsion (every pair)
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

    // Attraction (edges)
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

    // Center pull + damping + clamp
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

// ── Node detail bottom sheet ───────────────────────────────────────────────────

const _SHEET_H = 420;

function NodeDetailSheet({
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

  // `rendered` keeps the Modal mounted during the exit animation so users
  // don't see the sheet disappear immediately when closing.
  const [rendered, setRendered] = useState(false);

  useEffect(() => {
    if (visible) {
      setRendered(true);
      // Reset to off-screen so the open animation always starts from hidden
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

  const sheetStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: slideAnim.value }],
  }));
  const backdropStyle = useAnimatedStyle(() => ({
    opacity: fadeAnim.value,
  }));

  if (!rendered || !node) return null;

  // Find connected nodes
  const nodeById = new Map(allNodes.map(n => [n.id, n]));
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

      <Animated.View style={[gStyles.sheet, {
        backgroundColor: colors.card,
        borderColor: colors.border,
        paddingBottom: insets.bottom + 16,
      }, sheetStyle]}>
        <View style={[gStyles.handle, { backgroundColor: colors.border }]} />

        {/* Header */}
        <View style={gStyles.sheetHeader}>
          <View style={[gStyles.kindIcon, { backgroundColor: color + '22' }]}>
            <Feather name={kindIcon as any} size={16} color={color} />
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={[gStyles.sheetTitle, { color: colors.foreground }]} numberOfLines={2}>
              {node.label}
            </Text>
            <Text style={[gStyles.sheetMeta, { color: colors.mutedForeground }]}>
              {node.kind || node.type}
              {node.work_title ? `  ·  ${node.work_title}` : ''}
            </Text>
          </View>
          <Pressable onPress={onClose} hitSlop={10}>
            <Feather name="x" size={18} color={colors.mutedForeground} />
          </Pressable>
        </View>

        {/* Connected nodes */}
        <ScrollView
          style={{ flex: 1, paddingHorizontal: 16 }}
          showsVerticalScrollIndicator={false}
        >
          {connected.length > 0 && (
            <>
              <Text style={[gStyles.connLabel, { color: colors.mutedForeground }]}>
                CONNECTIONS ({connected.length})
              </Text>
              {connected.map(({ n, label }, i) => (
                <View key={i} style={[gStyles.connRow, { borderColor: colors.border }]}>
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
                      style={({ pressed }) => [gStyles.viewBtn, { borderColor: colors.border, opacity: pressed ? 0.6 : 1 }]}
                    >
                      <Feather name="arrow-right" size={13} color={colors.primary} />
                    </Pressable>
                  )}
                </View>
              ))}
            </>
          )}

          {connected.length === 0 && (
            <Text style={[gStyles.connLabel, { color: colors.mutedForeground, marginTop: 8 }]}>
              No connections visible in current view.
            </Text>
          )}

          {/* Open document button */}
          {node.type === 'document' && (
            <Pressable
              onPress={() => { onClose(); router.push(`/library/${node.id}` as any); }}
              style={({ pressed }) => [gStyles.openBtn, {
                backgroundColor: pressed ? colors.primary + 'cc' : colors.primary,
                marginTop: 16,
              }]}
            >
              <Feather name="file-text" size={14} color="#fff" />
              <Text style={{ fontSize: 13, fontFamily: 'Inter_600SemiBold', color: '#fff' }}>
                Open document
              </Text>
            </Pressable>
          )}
        </ScrollView>
      </Animated.View>
    </Modal>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend({ colors }: { colors: ReturnType<typeof useColors> }) {
  const items = [
    { color: EDGE_COLORS.supports,    label: 'Supports' },
    { color: EDGE_COLORS.related,     label: 'Related' },
    { color: EDGE_COLORS.contradicts, label: 'Contradicts' },
  ];
  return (
    <View style={[gStyles.legend, { backgroundColor: colors.card + 'ee', borderColor: colors.border }]}>
      {items.map(({ color, label }) => (
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

// ── Main screen ───────────────────────────────────────────────────────────────

export default function GraphScreen() {
  const colors = useColors();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { work_id, work_title } = useLocalSearchParams<{ work_id?: string; work_title?: string }>();

  const [isGlobal,     setIsGlobal]     = useState(!work_id);
  const [hiddenKinds,  setHiddenKinds]  = useState<Set<string>>(new Set());
  const [selectedNode, setSelectedNode] = useState<GNode | null>(null);
  const [sheetVisible, setSheetVisible] = useState(false);

  // Canvas dimensions measured from actual layout so the SVG and hit-test math
  // stay correct across orientations, split-screen, and safe-area changes.
  // Fall back to screen estimates until the first layout event fires.
  const HEADER_H_EST = insets.top + 52;
  const [canvasDims, setCanvasDims] = useState({
    w: SCREEN_W,
    h: Math.max(100, SCREEN_H - HEADER_H_EST - 44 - 34),
  });
  const canvasW = canvasDims.w;
  const canvasH = canvasDims.h;

  // ── Fetch ────────────────────────────────────────────────────────────────────

  const url = (!isGlobal && work_id)
    ? `${API}/graph?work_id=${work_id}&limit=120`
    : `${API}/graph?limit=150`;

  const { data, isLoading, isError, refetch } = useQuery<GraphData>({
    queryKey: ['graph', isGlobal ? 'global' : (work_id ?? 'global')],
    queryFn: async () => {
      const r = await mobileFetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    staleTime: 120_000,
  });

  // ── Force simulation ─────────────────────────────────────────────────────────

  const { simNodes, degreeMap } = useMemo(() => {
    if (!data?.nodes?.length) return { simNodes: [] as GNode[], degreeMap: new Map<string, number>() };

    const deg = new Map<string, number>();
    for (const e of (data.edges ?? [])) {
      deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
      deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
    }

    const visible = data.nodes.filter(n =>
      n.type === 'document' || !hiddenKinds.has(n.kind),
    );

    return { simNodes: runSimulation(visible, data.edges ?? [], canvasW, canvasH), degreeMap: deg };
  }, [data, hiddenKinds, canvasW, canvasH]);

  // ── Pan / pinch shared values ────────────────────────────────────────────────

  const txVal  = useSharedValue(0);
  const tyVal  = useSharedValue(0);
  const scaleV = useSharedValue(1);
  const savedTX    = useSharedValue(0);
  const savedTY    = useSharedValue(0);
  const savedScale = useSharedValue(1);

  // Canvas half-dimensions — shared so the pan worklet can read them without
  // capturing a JS-thread-only closure variable.  Kept in sync whenever the
  // measured layout changes (orientation, split-screen, etc.).
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
      if (dist <= r && dist < bestDist) {
        best = n;
        bestDist = dist;
      }
    }
    if (best) {
      setSelectedNode(best);
      setSheetVisible(true);
    } else {
      setSheetVisible(false);
    }
  }, [simNodes, degreeMap]);

  const panGesture = Gesture.Pan()
    .onStart(() => {
      'worklet';
      savedTX.value = txVal.value;
      savedTY.value = tyVal.value;
    })
    .onUpdate(e => {
      'worklet';
      txVal.value = savedTX.value + e.translationX;
      tyVal.value = savedTY.value + e.translationY;
    })
    .onEnd(e => {
      'worklet';
      // Short movement ≈ tap → resolve touch → SVG coordinates.
      //
      // React Native applies `scale` around the VIEW CENTER (50 %, 50 %), not
      // the origin.  The forward transform is:
      //   screen_x = tx + hw + (svgX - hw) * s
      //   screen_y = ty + hh + (svgY - hh) * s
      // Inverting:
      //   svgX = hw + (e.x - tx - hw) / s
      //   svgY = hh + (e.y - ty - hh) / s
      if (Math.abs(e.translationX) < 8 && Math.abs(e.translationY) < 8) {
        const hw   = halfWV.value;
        const hh   = halfHV.value;
        const svgX = hw + (e.x - txVal.value - hw) / scaleV.value;
        const svgY = hh + (e.y - tyVal.value - hh) / scaleV.value;
        runOnJS(selectNodeAt)(svgX, svgY);
      }
    });

  const pinchGesture = Gesture.Pinch()
    .onStart(() => {
      'worklet';
      savedScale.value = scaleV.value;
    })
    .onUpdate(e => {
      'worklet';
      scaleV.value = Math.min(4, Math.max(0.2, savedScale.value * e.scale));
    });

  const composed = Gesture.Simultaneous(panGesture, pinchGesture);

  const animStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: txVal.value },
      { translateY: tyVal.value },
      { scale: scaleV.value },
    ],
  }));

  // ── SVG elements ─────────────────────────────────────────────────────────────

  const nodeById = useMemo(() => new Map(simNodes.map(n => [n.id, n])), [simNodes]);

  const renderedEdges = useMemo(() =>
    (data?.edges ?? []).flatMap((e, i) => {
      const s = nodeById.get(e.source);
      const t = nodeById.get(e.target);
      if (!s || !t) return [];
      return [(
        <Line
          key={`e${i}`}
          x1={s.x} y1={s.y} x2={t.x} y2={t.y}
          stroke={edgeColor(e)}
          strokeWidth={1.5}
          strokeOpacity={0.4}
        />
      )];
    }),
  [data?.edges, nodeById]);

  const renderedNodes = useMemo(() =>
    simNodes.map(n => {
      const deg     = degreeMap.get(n.id) ?? 0;
      const r       = nodeRadius(n, deg);
      const color   = nodeColor(n);
      const isSelected = selectedNode?.id === n.id;
      const showLabel  = deg >= 3 || isSelected;
      const short      = n.label.length > 15 ? n.label.slice(0, 14) + '…' : n.label;
      return (
        <G key={n.id}>
          <Circle
            cx={n.x} cy={n.y}
            r={isSelected ? r + 3 : r}
            fill={color}
            fillOpacity={isSelected ? 1 : 0.78}
            stroke={isSelected ? '#fff' : color}
            strokeWidth={isSelected ? 2.5 : 0.8}
            strokeOpacity={0.9}
          />
          {showLabel && (
            <SvgText
              x={n.x} y={n.y + r + 10}
              textAnchor="middle"
              fontSize={8.5}
              fill={color}
              fontFamily="Inter_500Medium"
            >
              {short}
            </SvgText>
          )}
        </G>
      );
    }),
  [simNodes, degreeMap, selectedNode]);

  // ── Kind filter chips ────────────────────────────────────────────────────────

  const toggleKind = useCallback((kind: string) => {
    setHiddenKinds(prev => {
      const next = new Set(prev);
      next.has(kind) ? next.delete(kind) : next.add(kind);
      return next;
    });
  }, []);

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <View style={[gStyles.container, { backgroundColor: colors.background }]}>
      {/* ── Header ── */}
      <View style={[gStyles.header, {
        paddingTop: insets.top + 8,
        backgroundColor: colors.card,
        borderBottomColor: colors.border,
      }]}>
        <Pressable onPress={() => router.back()} hitSlop={10} style={gStyles.backBtn}
          accessibilityRole="button" accessibilityLabel="Back">
          <Feather name="arrow-left" size={20} color={colors.foreground} />
        </Pressable>

        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={[gStyles.headerTitle, { color: colors.foreground }]} numberOfLines={1}>
            {isGlobal ? 'Knowledge Graph' : (work_title ?? 'Work Graph')}
          </Text>
          {data && (
            <Text style={[gStyles.headerSub, { color: colors.mutedForeground }]}>
              {data.node_count} nodes · {data.edge_count} edges
            </Text>
          )}
        </View>

        {/* Global toggle (only when work_id is present) */}
        {!!work_id && (
          <Pressable
            onPress={() => setIsGlobal(g => !g)}
            style={({ pressed }) => [
              gStyles.toggleBtn,
              {
                backgroundColor: isGlobal ? colors.primary : colors.muted,
                opacity: pressed ? 0.75 : 1,
              },
            ]}
          >
            <Feather name="globe" size={11} color={isGlobal ? '#fff' : colors.mutedForeground} />
            <Text style={{
              fontSize: 11, fontFamily: 'Inter_600SemiBold',
              color: isGlobal ? '#fff' : colors.mutedForeground,
            }}>
              {isGlobal ? 'Global' : 'This work'}
            </Text>
          </Pressable>
        )}

        <Pressable onPress={() => refetch()} hitSlop={8} style={{ marginLeft: 8 }}>
          <Feather name="refresh-cw" size={16} color={colors.mutedForeground} />
        </Pressable>
      </View>

      {/* ── Entity-kind filter chips ── */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={{ height: 44, flexGrow: 0, backgroundColor: colors.background }}
        contentContainerStyle={{ paddingHorizontal: 12, alignItems: 'center', gap: 6, paddingVertical: 6 }}
      >
        {ENTITY_KINDS.map(({ value, label, color }) => {
          const active = !hiddenKinds.has(value);
          return (
            <Pressable
              key={value}
              onPress={() => toggleKind(value)}
              style={[
                gStyles.chip,
                {
                  backgroundColor: active ? color + '22' : colors.muted,
                  borderColor:     active ? color : 'transparent',
                },
              ]}
            >
              <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: active ? color : colors.mutedForeground }} />
              <Text style={{ fontSize: 11, fontFamily: 'Inter_500Medium', color: active ? color : colors.mutedForeground }}>
                {label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {/* ── Graph canvas ── */}
      <View
        style={{ flex: 1, overflow: 'hidden' }}
        onLayout={e => {
          const { width, height } = e.nativeEvent.layout;
          if (width > 0 && height > 0) {
            setCanvasDims({ w: width, h: height });
          }
        }}
      >
        {isLoading && (
          <View style={gStyles.centreAbs}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={[gStyles.loadText, { color: colors.mutedForeground }]}>
              Laying out graph…
            </Text>
          </View>
        )}

        {isError && !isLoading && (
          <View style={gStyles.centreAbs}>
            <Feather name="wifi-off" size={36} color={colors.mutedForeground} style={{ opacity: 0.5 }} />
            <Text style={[gStyles.loadText, { color: colors.mutedForeground }]}>
              Could not load graph data.
            </Text>
            <Pressable
              onPress={() => refetch()}
              style={({ pressed }) => [gStyles.retryBtn, { borderColor: colors.border, backgroundColor: pressed ? colors.muted : 'transparent' }]}
            >
              <Feather name="refresh-cw" size={13} color={colors.foreground} />
              <Text style={{ fontSize: 12, fontFamily: 'Inter_500Medium', color: colors.foreground }}>Retry</Text>
            </Pressable>
          </View>
        )}

        {!isLoading && !isError && simNodes.length === 0 && (
          <View style={gStyles.centreAbs}>
            <Feather name="share-2" size={36} color={colors.mutedForeground} style={{ opacity: 0.4 }} />
            <Text style={[gStyles.loadText, { color: colors.mutedForeground }]}>
              No nodes yet. Process some documents first.
            </Text>
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

      {/* ── Legend ── */}
      <Legend colors={colors} />

      {/* ── Node detail sheet ── */}
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

const gStyles = StyleSheet.create({
  container: { flex: 1 },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingBottom: 10,
    gap: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  backBtn: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold' },
  headerSub:   { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 1 },

  toggleBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 20,
  },

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

  centreAbs: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center', justifyContent: 'center', gap: 12,
  },
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
  sheetHeader: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    paddingHorizontal: 16, marginBottom: 12,
  },
  kindIcon: { width: 36, height: 36, borderRadius: 8, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  sheetTitle: { fontSize: 15, fontFamily: 'Inter_600SemiBold', lineHeight: 20 },
  sheetMeta:  { fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 2, textTransform: 'capitalize' },
  connLabel:  { fontSize: 10, fontFamily: 'Inter_600SemiBold', textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 },
  connRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 7, borderBottomWidth: StyleSheet.hairlineWidth,
  },
  viewBtn: { width: 28, height: 28, borderRadius: 6, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  openBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 7, paddingVertical: 12, borderRadius: 10, marginBottom: 8,
  },
});
