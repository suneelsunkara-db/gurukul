import React, {useCallback, useMemo} from 'react';
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  type Node, type Edge, type NodeTypes, type EdgeTypes,
  Handle, Position,
  useNodesState, useEdgesState, useReactFlow,
  BaseEdge, EdgeLabelRenderer, getBezierPath,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type {TreeNode, Category} from './TopicTree';

export interface TypedEdge {
  source: string;
  target: string;
  type: 'prerequisite' | 'builds_on' | 'contrasts' | 'applies' | 'related';
  label?: string | null;
  strength: number;
}

interface UnderstandingEntry {
  level: string;
  scores?: {accuracy: number; depth: number; reasoning: number};
  assessed_at?: string;
}

interface Props {
  nodes: Record<string, TreeNode>;
  edges?: TypedEdge[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  understandingMap?: Record<string, UnderstandingEntry>;
}

const CAT_COLORS: Record<Category, {bg: string; border: string; text: string}> = {
  foundations:   {bg: '#eff6ff', border: '#3b82f6', text: '#1e40af'},
  architecture:  {bg: '#fef3c7', border: '#f59e0b', text: '#92400e'},
  training:      {bg: '#ecfdf5', border: '#10b981', text: '#065f46'},
  inference:     {bg: '#fdf2f8', border: '#ec4899', text: '#9d174d'},
  models:        {bg: '#ede9fe', border: '#8b5cf6', text: '#5b21b6'},
  applications:  {bg: '#fff7ed', border: '#f97316', text: '#9a3412'},
};

const EDGE_TYPE_COLORS: Record<string, string> = {
  prerequisite: '#3b82f6',
  builds_on: '#10b981',
  contrasts: '#ef4444',
  applies: '#8b5cf6',
  related: '#94a3b8',
};

const EDGE_TYPE_LABELS: Record<string, string> = {
  prerequisite: 'prereq',
  builds_on: 'extends',
  contrasts: 'vs',
  applies: 'uses',
  related: '',
};

const STATUS_OPACITY: Record<string, number> = {
  done: 1, generating: 0.8, queued: 0.4, failed: 0.5,
};

const UNDERSTANDING_LABELS: Record<string, string> = {
  surface: 'S', structural: 'St', deep: 'D', creative: 'C',
};

function TopicNode({data}: {data: {
  label: string; category: Category; status: string;
  isComparison: boolean; selected: boolean; highlighted: boolean;
  understanding: string | null;
}}) {
  const colors = CAT_COLORS[data.category] ?? CAT_COLORS.foundations;
  const opacity = STATUS_OPACITY[data.status] ?? 0.5;
  const uColor = data.understanding ? (UNDERSTANDING_BORDER[data.understanding] || null) : null;

  return (
    <div style={{
      padding: '8px 14px', borderRadius: 8,
      background: data.selected ? colors.border : colors.bg,
      color: data.selected ? '#fff' : colors.text,
      border: `2px solid ${uColor || (data.highlighted ? '#6366f1' : colors.border)}`,
      fontSize: 12, fontWeight: data.selected ? 700 : 500,
      maxWidth: 180, textAlign: 'center',
      cursor: data.status === 'done' ? 'pointer' : 'default',
      opacity: data.highlighted ? 1 : opacity,
      boxShadow: data.selected
        ? `0 0 14px ${colors.border}44`
        : uColor ? `0 0 10px ${uColor}44`
        : data.highlighted ? `0 0 10px #6366f144` : 'none',
      transition: 'all 0.2s ease', lineHeight: 1.3,
      position: 'relative',
    }}>
      <Handle type="target" position={Position.Top} style={{visibility: 'hidden'}} />
      <div style={{fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.05em', opacity: 0.7, marginBottom: 2}}>
        {data.category}
      </div>
      {data.label}
      {data.isComparison && (
        <div style={{fontSize: 9, opacity: 0.6, marginTop: 2}}>compare</div>
      )}
      {uColor && (
        <div style={{
          position: 'absolute', top: -4, right: -4,
          width: 14, height: 14, borderRadius: '50%',
          background: uColor, border: '2px solid white',
          fontSize: 7, fontWeight: 700, color: '#fff',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} title={`Understanding: ${data.understanding}`}>
          {data.understanding ? UNDERSTANDING_LABELS[data.understanding] || '?' : '?'}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={{visibility: 'hidden'}} />
    </div>
  );
}

function TypedEdgeComponent({id, sourceX, sourceY, targetX, targetY, data, style}: any) {
  const [edgePath, labelX, labelY] = getBezierPath({sourceX, sourceY, targetX, targetY});
  const edgeType = data?.edgeType || 'related';
  const label = data?.edgeLabel;
  const highlighted = data?.highlighted;
  const color = highlighted ? '#6366f1' : (EDGE_TYPE_COLORS[edgeType] || '#94a3b8');

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          ...style,
          stroke: color,
          strokeWidth: highlighted ? 3 : (style?.strokeWidth || 1.5),
          opacity: highlighted ? 1 : (style?.opacity || 0.5),
        }}
      />
      {label && (
        <EdgeLabelRenderer>
          <div style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            fontSize: 9,
            color,
            background: 'var(--ifm-background-color, white)',
            padding: '1px 4px',
            borderRadius: 3,
            pointerEvents: 'all',
            opacity: highlighted ? 1 : 0.7,
            whiteSpace: 'nowrap',
            maxWidth: 120,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }} title={label} className="nodrag nopan">
            {EDGE_TYPE_LABELS[edgeType] ? `${EDGE_TYPE_LABELS[edgeType]}: ` : ''}{label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes: NodeTypes = {topic: TopicNode as any};
const edgeTypes: EdgeTypes = {typed: TypedEdgeComponent as any};

const UNDERSTANDING_BORDER: Record<string, string> = {
  surface: '#ef4444',
  structural: '#f59e0b',
  deep: '#10b981',
  creative: '#6366f1',
};

function layoutGraph(
  treeNodes: Record<string, TreeNode>,
  typedEdges: TypedEdge[],
  selectedId: string | null,
  understandingMap: Record<string, UnderstandingEntry> = {},
): {rfNodes: Node[]; rfEdges: Edge[]} {
  const rfNodes: Node[] = [];
  const rfEdges: Edge[] = [];

  const all = Object.values(treeNodes);
  if (all.length === 0) return {rfNodes, rfEdges};

  // Compute highlight set: selected node + 2-hop neighbors
  const highlightSet = new Set<string>();
  const highlightEdgeSet = new Set<string>();
  if (selectedId) {
    highlightSet.add(selectedId);
    const adjMap = new Map<string, Set<string>>();
    for (const e of typedEdges) {
      if (!adjMap.has(e.source)) adjMap.set(e.source, new Set());
      if (!adjMap.has(e.target)) adjMap.set(e.target, new Set());
      adjMap.get(e.source)!.add(e.target);
      adjMap.get(e.target)!.add(e.source);
    }
    const hop1 = adjMap.get(selectedId) || new Set();
    for (const n of hop1) {
      highlightSet.add(n);
      const hop2 = adjMap.get(n) || new Set();
      for (const n2 of hop2) highlightSet.add(n2);
    }
  }

  const catOrder: Category[] = ['foundations', 'architecture', 'training', 'inference', 'models', 'applications'];
  const groups: Record<string, TreeNode[]> = {};
  for (const cat of catOrder) groups[cat] = [];
  for (const node of all) {
    const cat = catOrder.includes(node.category) ? node.category : 'foundations';
    groups[cat].push(node);
  }

  const COL_GAP = 280;
  const ROW_GAP = 90;
  let colIdx = 0;

  for (const cat of catOrder) {
    const items = groups[cat];
    if (items.length === 0) continue;
    const x = colIdx * COL_GAP;
    items.sort((a, b) => a.position - b.position);
    for (let row = 0; row < items.length; row++) {
      const node = items[row];
      const isHighlighted = selectedId ? highlightSet.has(node.id) : false;
      rfNodes.push({
        id: node.id, type: 'topic',
        position: {x, y: row * ROW_GAP},
        data: {
          label: node.title, category: node.category, status: node.status,
          isComparison: node.isComparison, selected: node.id === selectedId,
          highlighted: isHighlighted,
          understanding: understandingMap[node.id]?.level || null,
        },
      });
    }
    colIdx++;
  }

  // Use typed edges if available, else fall back to connectsTo
  if (typedEdges.length > 0) {
    const edgeSet = new Set<string>();
    for (const e of typedEdges) {
      if (!treeNodes[e.source] || !treeNodes[e.target]) continue;
      const edgeKey = [e.source, e.target].sort().join('::');
      if (edgeSet.has(edgeKey)) continue;
      edgeSet.add(edgeKey);

      const isHighlighted = selectedId
        ? (highlightSet.has(e.source) && highlightSet.has(e.target))
        : false;

      rfEdges.push({
        id: edgeKey,
        source: e.source,
        target: e.target,
        type: 'typed',
        data: {edgeType: e.type, edgeLabel: e.label, highlighted: isHighlighted},
        style: {
          strokeWidth: Math.max(1, e.strength * 3),
          opacity: isHighlighted ? 0.9 : 0.35,
        },
        animated: treeNodes[e.source].status === 'generating' || treeNodes[e.target].status === 'generating',
      });
    }
  } else {
    const edgeSet = new Set<string>();
    for (const node of all) {
      for (const targetId of node.connectsTo) {
        if (!treeNodes[targetId]) continue;
        const edgeKey = [node.id, targetId].sort().join('::');
        if (edgeSet.has(edgeKey)) continue;
        edgeSet.add(edgeKey);
        rfEdges.push({
          id: edgeKey, source: node.id, target: targetId,
          type: 'typed',
          data: {edgeType: 'related', edgeLabel: null, highlighted: false},
          style: {strokeWidth: 1.5, opacity: 0.3},
        });
      }
    }
  }

  return {rfNodes, rfEdges};
}

function MindMapInner({nodes: treeNodes, edges: typedEdges = [], selectedId, onSelect, understandingMap = {}}: Props) {
  const {rfNodes: layoutNodes, rfEdges: layoutEdges} = useMemo(
    () => layoutGraph(treeNodes, typedEdges, selectedId, understandingMap),
    [treeNodes, typedEdges, selectedId, understandingMap],
  );

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(layoutNodes);
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(layoutEdges);
  const {fitView} = useReactFlow();
  const prevCountRef = React.useRef(layoutNodes.length);

  React.useEffect(() => {
    setFlowNodes(layoutNodes);
    setFlowEdges(layoutEdges);
    if (layoutNodes.length !== prevCountRef.current) {
      prevCountRef.current = layoutNodes.length;
      setTimeout(() => fitView({padding: 0.2, duration: 300}), 50);
    }
  }, [layoutNodes, layoutEdges, setFlowNodes, setFlowEdges, fitView]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const tn = treeNodes[node.id];
      if (tn?.status === 'done') onSelect(node.id);
    },
    [treeNodes, onSelect],
  );

  return (
    <ReactFlow
      nodes={flowNodes} edges={flowEdges}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      fitView fitViewOptions={{padding: 0.2}}
      minZoom={0.2} maxZoom={1.5}
      proOptions={{hideAttribution: true}}
      nodesDraggable nodesConnectable={false} elementsSelectable={false}
    >
      <Background gap={24} size={1} color="var(--gk-card-border)" />
      <Controls showInteractive={false} />
      <MiniMap
        nodeColor={(n) => {
          const cat = n.data?.category as Category;
          return CAT_COLORS[cat]?.border || '#94a3b8';
        }}
        maskColor="rgba(0,0,0,0.08)"
        style={{borderRadius: 8}}
      />
    </ReactFlow>
  );
}

export default function MindMap(props: Props) {
  if (Object.keys(props.nodes).length === 0) return null;

  return (
    <div className="gk-mindmap">
      <div className="gk-mindmap__legend">
        {(['foundations', 'architecture', 'training', 'inference', 'models', 'applications'] as Category[]).map((cat) => (
          <span key={cat} className="gk-mindmap__legend-item">
            <span className={`gk-cat__dot gk-cat__dot--${cat}`} style={{width: 8, height: 8}} />
            {cat}
          </span>
        ))}
        <span className="gk-mindmap__legend-sep">|</span>
        {Object.entries(EDGE_TYPE_COLORS).map(([type, color]) => (
          <span key={type} className="gk-mindmap__legend-item">
            <span style={{width: 12, height: 2, background: color, borderRadius: 1}} />
            {type.replace('_', ' ')}
          </span>
        ))}
      </div>
      <ReactFlowProvider>
        <MindMapInner {...props} />
      </ReactFlowProvider>
    </div>
  );
}
