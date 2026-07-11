import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import TopicTree, {type TreeNode} from '@/components/TopicTree';
import TopicContent, {type ContentLevel} from '@/components/TopicContent';
import MindMap, {type TypedEdge} from '@/components/MindMap';
import EvalDashboard from '@/components/EvalDashboard';
import ResearchPanel from '@/components/ResearchPanel';



interface GraphState {
  nodes: Record<string, TreeNode>;
  edges: TypedEdge[];
  seed: string | null;
}

interface ThoughtStep {
  id: string;
  step: string;
  message: string;
  model?: string;
  topics?: string[];
  topic_id?: string;
  timestamp: number;
}

type TopicPayload = Parameters<typeof TopicContent>[0]['payload'];
type RightView = 'welcome' | 'topic' | 'map' | 'evals' | 'research';

export default function ExplorePage() {
  const [graph, setGraph] = useState<GraphState>({nodes: {}, edges: [], seed: null});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [payload, setPayload] = useState<TopicPayload | null>(null);
  const [loadingPayload, setLoadingPayload] = useState(false);
  const [payloadError, setPayloadError] = useState<string | null>(null);
  const [progressMap, setProgressMap] = useState<Record<string, string>>({});
  const [seedInput, setSeedInput] = useState('');
  const [exploring, setExploring] = useState(false);
  const [stoppingExplore, setStoppingExplore] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState<ContentLevel>('intermediate');
  const [rightView, setRightView] = useState<RightView>('welcome');
  const [thoughts, setThoughts] = useState<ThoughtStep[]>([]);
  const [showThoughts, setShowThoughts] = useState(true);
  const [understandingMap, setUnderstandingMap] = useState<Record<string, {level: string; scores?: any; assessed_at?: string}>>({});
  const thoughtsEndRef = useRef<HTMLDivElement>(null);
  const payloadCache = useRef<Map<string, any>>(new Map());
  const [readSet, setReadSet] = useState<Set<string>>(() => {
    if (typeof window === 'undefined') return new Set();
    try { return new Set(JSON.parse(localStorage.getItem('gk:read') ?? '[]')); }
    catch { return new Set(); }
  });

  useEffect(() => {
    if (typeof window !== 'undefined')
      localStorage.setItem('gk:read', JSON.stringify([...readSet]));
  }, [readSet]);

  // Load understanding map on mount
  useEffect(() => {
    fetch(`/api/understanding`)
      .then((r) => r.json())
      .then((data) => setUnderstandingMap(data))
      .catch(() => {});
  }, []);

  const handleUnderstandingChange = useCallback((topicId: string, level: string) => {
    setUnderstandingMap((prev) => ({
      ...prev,
      [topicId]: {level, assessed_at: new Date().toISOString()},
    }));
  }, []);

  // ── SSE with reconnection ───────────────────────────────────

  useEffect(() => {
    let es: EventSource | null = null;
    let retryDelay = 1000;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let unmounted = false;

    function connectSSE() {
      if (unmounted) return;
      es = new EventSource(`/api/events`);

      es.addEventListener('init', (e) => {
        const data = JSON.parse(e.data);
        setGraph({
          nodes: data.nodes || {},
          edges: data.edges || [],
          seed: data.seed || null,
        });
        setConnected(true);
        retryDelay = 1000;
      });

      es.addEventListener('node', (e) => {
        const node = JSON.parse(e.data) as TreeNode;
        setGraph((prev) => ({
          ...prev,
          nodes: {...prev.nodes, [node.id]: node},
        }));
      });

      es.addEventListener('edge', (e) => {
        const edge = JSON.parse(e.data) as TypedEdge;
        setGraph((prev) => ({
          ...prev,
          edges: [...prev.edges, edge],
        }));
      });

      es.addEventListener('status', (e) => {
        const {id, status, error: err} = JSON.parse(e.data);
        setGraph((prev) => {
          const n = prev.nodes[id];
          if (!n) return prev;
          return {...prev, nodes: {...prev.nodes, [id]: {...n, status, error: err ?? n.error}}};
        });
        if (status === 'done') {
          setProgressMap((p) => { const next = {...p}; delete next[id]; return next; });
          payloadCache.current.delete(id);
        }
      });

      es.addEventListener('progress', (e) => {
        const {id, msg} = JSON.parse(e.data);
        setProgressMap((p) => ({...p, [id]: msg}));
      });

      es.addEventListener('thought', (e) => {
        const data = JSON.parse(e.data);
        setThoughts((prev) => [...prev.slice(-49), {
          ...data,
          id: `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          timestamp: Date.now(),
        }]);
      });

      es.addEventListener('explore:done', () => {
        setExploring(false);
        setStoppingExplore(false);
        setSelectedId((prev) => {
          if (prev) return prev;
          setGraph((g) => {
            const firstDone = Object.values(g.nodes).find((n) => n.status === 'done');
            if (firstDone) setTimeout(() => setSelectedId(firstDone.id), 0);
            return g;
          });
          return prev;
        });
      });

      es.addEventListener('reset', () => {
        setGraph({nodes: {}, edges: [], seed: null});
        setSelectedId(null);
        setPayload(null);
        setProgressMap({});
        setThoughts([]);
        setRightView('welcome');
      });

      es.addEventListener('error', (e) => {
        try { setError(JSON.parse((e as MessageEvent).data).message); } catch {}
        setExploring(false);
        setStoppingExplore(false);
      });

      es.onerror = () => {
        setConnected(false);
        es?.close();
        if (!unmounted) {
          retryTimer = setTimeout(() => {
            retryDelay = Math.min(retryDelay * 2, 30000);
            connectSSE();
          }, retryDelay);
        }
      };
    }

    connectSSE();

    return () => {
      unmounted = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
  }, []);

  // ── Select ──────────────────────────────────────────────────

  const selectTopic = useCallback(async (id: string) => {
    setSelectedId(id);
    setRightView('topic');
    setPayload(null);
    setPayloadError(null);
    setReadSet((prev) => new Set([...prev, id]));

    const cached = payloadCache.current.get(id);
    if (cached) {
      setPayload(cached);
      setLoadingPayload(false);
      return;
    }

    setLoadingPayload(true);
    try {
      const res = await fetch(`/api/topic/${id}`);
      if (!res.ok) throw new Error(`${res.status}`);
      const body = await res.json();
      const data = body.payload ? normalizeTopicPayload(body.payload) : null;
      setPayload(data);
      if (data) payloadCache.current.set(id, data);
      else {
        setPayloadError(
          body.status && body.status !== 'done'
            ? `Topic is ${body.status}. Content is not ready yet.`
            : 'This topic is marked done, but no content payload was returned.'
        );
      }
    } catch (err) {
      setPayload(null);
      setPayloadError(`Could not load topic content: ${(err as Error).message}`);
    }
    setLoadingPayload(false);
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    const node = graph.nodes[selectedId];
    if (node?.status === 'done' && !payload && !loadingPayload) selectTopic(selectedId);
  }, [graph.nodes, selectedId, payload, loadingPayload, selectTopic]);

  // ── Explore ─────────────────────────────────────────────────

  const explore = useCallback(async (seed: string, parentId: string | null = null) => {
    setError(null);
    setExploring(true);
    setSelectedId(null);
    setPayload(null);
    setRightView('topic');
    try {
      const res = await fetch(`/api/explore`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({seed, parentId}),
      });
      if (!res.ok) throw new Error(await res.text());
    } catch (err) { setError((err as Error).message); setExploring(false); }
  }, []);

  const handleSeedSubmit = () => {
    if (!seedInput.trim()) return;
    explore(seedInput.trim());
    setSeedInput('');
  };

  const handleStopExplore = useCallback(async () => {
    setStoppingExplore(true);
    try {
      const res = await fetch('/api/explore/cancel', {method: 'POST'});
      if (!res.ok) throw new Error(await res.text());
    } catch (err) {
      setError((err as Error).message);
      setStoppingExplore(false);
    }
  }, []);

  const handleExploreDeeper = useCallback((direction: string) => {
    if (selectedId) explore(direction, selectedId);
  }, [selectedId, explore]);

  // ── Derived ─────────────────────────────────────────────────

  const connectedTopics = useMemo(() => {
    if (!selectedId) return [];
    const node = graph.nodes[selectedId];
    if (!node) return [];
    return node.connectsTo
      .map((cid) => { const n = graph.nodes[cid]; return n ? {id: cid, title: n.title} : null; })
      .filter(Boolean) as Array<{id: string; title: string}>;
  }, [selectedId, graph.nodes]);

  useEffect(() => {
    thoughtsEndRef.current?.scrollIntoView({behavior: 'smooth'});
  }, [thoughts]);

  const hasTopics = Object.keys(graph.nodes).length > 0;
  const selectedNode = selectedId ? graph.nodes[selectedId] : null;
  const hasActiveGeneration = Object.values(graph.nodes).some(
    (node) => node.status === 'queued' || node.status === 'generating'
  );
  const canStopExplore = exploring || hasActiveGeneration;
  const activeThoughts = thoughts.filter((t) => Date.now() - t.timestamp < 120_000);

  // ── Admin actions ──────────────────────────────────────────
  const [resetting, setResetting] = useState(false);
  const [evalRunning, setEvalRunning] = useState(false);

  const handleReset = useCallback(async () => {
    if (!window.confirm('This will delete ALL topics, edges, evaluations, and challenges. Continue?')) return;
    setResetting(true);
    try {
      const res = await fetch('/api/reset', {method: 'POST'});
      if (!res.ok) throw new Error(await res.text());
      setGraph({nodes: {}, edges: [], seed: null});
      setSelectedId(null);
      setPayload(null);
      setThoughts([]);
      setRightView('welcome');
      payloadCache.current.clear();
    } catch (err) { setError((err as Error).message); }
    finally { setResetting(false); }
  }, []);

  const handleRunEval = useCallback(async () => {
    setEvalRunning(true);
    setRightView('evals');
    try {
      const res = await fetch('/api/eval/run', {method: 'POST'});
      if (!res.ok) throw new Error(await res.text());
    } catch (err) { setError((err as Error).message); }
    finally { setEvalRunning(false); }
  }, []);

  // ── Render ──────────────────────────────────────────────────

  if (!connected) {
    return (
      <main className="gk-explore">
        <div className="gk-explore__empty">
          <h1>Gurukul</h1>
          <div className="gk-card">
            <div className="gk-card__label">Connecting...</div>
            <p>Waiting for agent server...</p>
            <p>Run <code>npm run dev</code> locally, or check the app deployment.</p>
          </div>
        </div>
      </main>
    );
  }

  return (
      <main className="gk-explore">
        <aside className="gk-sidebar">
          <div className="gk-sidebar__seed">
            <input
              type="text" className="gk-sidebar__input"
              placeholder={hasTopics ? 'New seed...' : 'Seed topic, e.g. "LLM"'}
              value={seedInput}
              onChange={(e) => setSeedInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSeedSubmit()}
              disabled={canStopExplore}
            />
            {canStopExplore ? (
              <button type="button" className="gk-sidebar__stop"
                onClick={handleStopExplore}
                disabled={stoppingExplore}
              >{stoppingExplore ? '...' : 'Stop'}</button>
            ) : (
              <button type="button" className="gk-sidebar__go"
                onClick={handleSeedSubmit}
                disabled={!seedInput.trim()}
              >Go</button>
            )}
          </div>

          {hasTopics && (
            <nav className="gk-sidebar__nav">
              {([
                {view: 'topic' as RightView, icon: '☰', label: 'Topics'},
                {view: 'map' as RightView, icon: '◎', label: 'Map'},
                {view: 'evals' as RightView, icon: '△', label: 'Evals'},
                {view: 'research' as RightView, icon: '◇', label: 'Research'},
              ] as const).map(({view, icon, label}) => (
                <button key={view} type="button"
                  className={`gk-nav-tab ${rightView === view ? 'gk-nav-tab--active' : ''}`}
                  onClick={() => setRightView(view)}
                  title={label}
                >
                  <span className="gk-nav-tab__icon">{icon}</span>
                  <span className="gk-nav-tab__label">{label}</span>
                </button>
              ))}
            </nav>
          )}

          <TopicTree
            nodes={graph.nodes}
            selectedId={selectedId}
            onSelect={selectTopic}
            readSet={readSet}
            understandingMap={understandingMap}
          />

          {hasTopics && (
            <div className="gk-sidebar__footer">
              <button type="button"
                className="gk-footer-btn gk-footer-btn--eval"
                onClick={handleRunEval}
                disabled={evalRunning}
                title="Run full quality evaluation with LLM judge"
              >{evalRunning ? '⟳ Evaluating...' : '△ Evaluate'}</button>
              <button type="button"
                className="gk-footer-btn gk-footer-btn--reset"
                onClick={handleReset}
                disabled={resetting}
                title="Delete all topics, edges, evaluations, and challenges"
              >{resetting ? '⟳ Resetting...' : '⟲ Reset'}</button>
            </div>
          )}
        </aside>

        <section className="gk-main">
          {hasTopics && rightView !== 'map' && rightView !== 'evals' && rightView !== 'research' && (
            <div className="gk-toolbar">
              <div className="gk-ai-warning">AI-generated. Verify before citing.</div>
              <div className="gk-level-toggle">
                {(['beginner', 'intermediate', 'advanced'] as ContentLevel[]).map((l) => (
                  <button key={l} type="button"
                    className={`gk-level-btn ${level === l ? 'gk-level-btn--on' : ''}`}
                    onClick={() => setLevel(l)}
                  >{l.charAt(0).toUpperCase() + l.slice(1)}</button>
                ))}
              </div>
            </div>
          )}

          {activeThoughts.length > 0 && (
            <div className="gk-thoughts">
              <button type="button" className="gk-thoughts__toggle"
                onClick={() => setShowThoughts((v) => !v)}
              >
                <span className="gk-thoughts__icon">{showThoughts ? '▾' : '▸'}</span>
                Agent thinking ({activeThoughts.length})
              </button>
              {showThoughts && (
                <div className="gk-thoughts__list">
                  {activeThoughts.map((t) => (
                    <div key={t.id} className={`gk-thoughts__item gk-thoughts__item--${t.step.includes('done') || t.step.includes('_done') ? 'done' : t.step.includes('fail') ? 'fail' : 'active'}`}>
                      <span className="gk-thoughts__dot" />
                      <span className="gk-thoughts__msg">{t.message}</span>
                      {t.model && <span className="gk-thoughts__model">{t.model}</span>}
                    </div>
                  ))}
                  <div ref={thoughtsEndRef} />
                </div>
              )}
            </div>
          )}

          {error && <div className="gk-card" style={{borderColor: '#ef4444', color: '#ef4444'}}>{error}</div>}

          {rightView === 'welcome' && !hasTopics && !exploring && (
            <div className="gk-welcome">
              <h1>Gurukul</h1>
              <p className="gk-welcome__sub">Mentorship-Driven Learning for AI Researchers</p>
              <p className="gk-welcome__desc">
                Type a seed topic and the Teacher maps the knowledge landscape — concepts,
                architectures, training recipes, and model families — organized by category
                with conceptual connections. Student agents generate each chapter concurrently.
              </p>
              <div className="gk-welcome__chips">
                {['LLM', 'Transformer Architecture', 'RLHF', 'Mixture of Experts'].map((s) => (
                  <button key={s} type="button" className="gk-chip" onClick={() => setSeedInput(s)}>{s}</button>
                ))}
              </div>
            </div>
          )}

          {exploring && !selectedNode && rightView !== 'map' && (
            <div className="gk-status-card">
              <div className="gk-card__label">Mapping the landscape...</div>
              <p>Teacher is decomposing your topic into a knowledge graph with categories and connections.</p>
              <div className="gk-pulse" />
            </div>
          )}

          {rightView === 'map' && (
            <div className="gk-map-container">
              <MindMap
                nodes={graph.nodes}
                edges={graph.edges}
                selectedId={selectedId}
                onSelect={(id) => { selectTopic(id); setRightView('topic'); }}
                understandingMap={understandingMap}
              />
            </div>
          )}

          {rightView === 'evals' && (
            <EvalDashboard
              onSelectTopic={(id) => { selectTopic(id); setRightView('topic'); }}
            />
          )}

          {rightView === 'research' && <ResearchPanel />}

          {rightView === 'topic' && selectedNode && selectedNode.status === 'generating' && (
            <div className="gk-status-card">
              <div className="gk-card__label">Generating</div>
              <h3>{selectedNode.title}</h3>
              <p>{progressMap[selectedNode.id] ?? 'Waiting for Student agent...'}</p>
              <div className="gk-pulse" />
            </div>
          )}

          {rightView === 'topic' && selectedNode && selectedNode.status === 'queued' && (
            <div className="gk-status-card">
              <div className="gk-card__label">Queued</div>
              <h3>{selectedNode.title}</h3>
              <p style={{color: 'var(--gk-muted)'}}>Waiting for an agent slot.</p>
            </div>
          )}

          {rightView === 'topic' && selectedNode && selectedNode.status === 'failed' && (
            <div className="gk-status-card" style={{borderColor: '#ef4444'}}>
              <div className="gk-card__label" style={{color: '#ef4444'}}>Failed</div>
              <h3>{selectedNode.title}</h3>
              <p>{selectedNode.error}</p>
            </div>
          )}

          {rightView === 'topic' && selectedNode && selectedNode.status === 'done' && loadingPayload && (
            <div className="gk-status-card"><div className="gk-pulse" /></div>
          )}

          {rightView === 'topic' && selectedNode && selectedNode.status === 'done' && !payload && !loadingPayload && (
            <div className="gk-status-card">
              <div className="gk-card__label">Content unavailable</div>
              <h3>{selectedNode.title}</h3>
              <p style={{color: 'var(--gk-muted)'}}>
                {payloadError ?? 'This topic is marked complete, but its generated content is missing.'}
              </p>
              <button
                type="button"
                className="gk-button"
                onClick={() => {
                  payloadCache.current.delete(selectedNode.id);
                  selectTopic(selectedNode.id);
                }}
              >
                Retry loading
              </button>
            </div>
          )}

          {rightView === 'topic' && selectedNode && selectedNode.status === 'done' && payload && !loadingPayload && (
            <TopicContent
              node={selectedNode}
              payload={payload}
              onExploreDeeper={handleExploreDeeper}
              level={level}
              connectedTopics={connectedTopics}
              onSelectTopic={selectTopic}
              onUnderstandingChange={handleUnderstandingChange}
              onRegenerate={() => selectTopic(selectedNode.id)}
            />
          )}
        </section>
      </main>
  );
}

function normalizeTopicPayload(raw: any): TopicPayload {
  return {
    summary: typeof raw?.summary === 'string' ? raw.summary : '',
    takeaway: typeof raw?.takeaway === 'string' ? raw.takeaway : '',
    eli5: typeof raw?.eli5 === 'string' ? raw.eli5 : '',
    key_aspects: Array.isArray(raw?.key_aspects) ? raw.key_aspects : [],
    gists: Array.isArray(raw?.gists) ? raw.gists : [],
    open_problems: Array.isArray(raw?.open_problems) ? raw.open_problems : [],
    references: Array.isArray(raw?.references) ? raw.references : [],
    experiment: raw?.experiment && typeof raw.experiment === 'object'
      ? {
          title: typeof raw.experiment.title === 'string' ? raw.experiment.title : '',
          hypothesis: typeof raw.experiment.hypothesis === 'string' ? raw.experiment.hypothesis : '',
          steps: Array.isArray(raw.experiment.steps) ? raw.experiment.steps : [],
        }
      : null,
    model_comparison: raw?.model_comparison ?? null,
    connections: Array.isArray(raw?.connections) ? raw.connections : [],
  };
}
