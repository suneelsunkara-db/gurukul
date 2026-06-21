import React, {useMemo, useState} from 'react';

export type NodeStatus = 'queued' | 'generating' | 'done' | 'failed';
export type Category = 'foundations' | 'architecture' | 'training' | 'inference' | 'models' | 'applications';

export interface TreeNode {
  id: string;
  title: string;
  category: Category;
  status: NodeStatus;
  isComparison: boolean;
  rationale: string;
  connectsTo: string[];
  position: number;
  error?: string;
}

const CATEGORY_LABELS: Record<Category, string> = {
  foundations: 'Foundations',
  architecture: 'Architecture',
  training: 'Training',
  inference: 'Inference',
  models: 'Models',
  applications: 'Applications',
};

const CATEGORY_ORDER: Category[] = ['foundations', 'architecture', 'training', 'inference', 'models', 'applications'];

export interface UnderstandingEntry {
  level: string;
  scores?: {accuracy: number; depth: number; reasoning: number};
  assessed_at?: string;
}

const LEVEL_COLORS: Record<string, string> = {
  surface: '#ef4444',
  structural: '#f59e0b',
  deep: '#10b981',
  creative: '#6366f1',
};

const LEVEL_SHORT: Record<string, string> = {
  surface: 'S',
  structural: 'St',
  deep: 'D',
  creative: 'C',
};

interface Props {
  nodes: Record<string, TreeNode>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  readSet: Set<string>;
  understandingMap?: Record<string, UnderstandingEntry>;
}

export default function TopicTree({nodes, selectedId, onSelect, readSet, understandingMap = {}}: Props) {
  const all = Object.values(nodes);
  if (all.length === 0) return null;

  const done = all.filter((n) => n.status === 'done').length;
  const pct = all.length > 0 ? (done / all.length) * 100 : 0;

  // Group by category
  const groups = useMemo(() => {
    const map: Record<Category, TreeNode[]> = {
      foundations: [], architecture: [], training: [],
      inference: [], models: [], applications: [],
    };
    for (const node of all) {
      const cat = CATEGORY_ORDER.includes(node.category) ? node.category : 'foundations';
      map[cat].push(node);
    }
    // Sort within each group by position
    for (const cat of CATEGORY_ORDER) {
      map[cat].sort((a, b) => a.position - b.position);
    }
    return map;
  }, [all]);

  return (
    <nav className="gk-tree" aria-label="Knowledge graph">
      <div className="gk-tree__bar">
        <div className="gk-tree__bar-fill" style={{width: `${pct}%`}} />
      </div>
      {CATEGORY_ORDER.map((cat) => {
        const items = groups[cat];
        if (items.length === 0) return null;
        return (
          <CategoryGroup
            key={cat}
            category={cat}
            label={CATEGORY_LABELS[cat]}
            items={items}
            selectedId={selectedId}
            onSelect={onSelect}
            readSet={readSet}
            understandingMap={understandingMap}
          />
        );
      })}
    </nav>
  );
}

function CategoryGroup({
  category, label, items, selectedId, onSelect, readSet, understandingMap,
}: {
  category: Category;
  label: string;
  items: TreeNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  readSet: Set<string>;
  understandingMap: Record<string, UnderstandingEntry>;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const doneCount = items.filter((n) => n.status === 'done').length;

  return (
    <div className="gk-cat">
      <button
        type="button"
        className="gk-cat__header"
        onClick={() => setCollapsed((c) => !c)}
      >
        <span className="gk-cat__arrow">{collapsed ? '▸' : '▾'}</span>
        <span className={`gk-cat__dot gk-cat__dot--${category}`} />
        <span className="gk-cat__label">{label}</span>
        <span className="gk-cat__count">{doneCount}/{items.length}</span>
      </button>
      {!collapsed && (
        <ul className="gk-cat__list">
          {items.map((node) => {
            const isSelected = selectedId === node.id;
            const isClickable = node.status === 'done' || node.status === 'generating';
            const isUnread = node.status === 'done' && !readSet.has(node.id);
            const understanding = understandingMap[node.id];

            return (
              <li key={node.id}>
                <button
                  type="button"
                  className={`gk-tree__btn ${isSelected ? 'gk-tree__btn--sel' : ''}`}
                  onClick={() => isClickable && onSelect(node.id)}
                  disabled={!isClickable}
                >
                  <span className={`gk-dot gk-dot--${node.status}`} />
                  <span className="gk-tree__title">{node.title}</span>
                  {understanding && (
                    <span
                      className="gk-understanding-badge"
                      style={{
                        background: LEVEL_COLORS[understanding.level] || '#94a3b8',
                      }}
                      title={`Understanding: ${understanding.level}`}
                    >
                      {LEVEL_SHORT[understanding.level] || '?'}
                    </span>
                  )}
                  {isUnread && !understanding && <span className="gk-unread" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
