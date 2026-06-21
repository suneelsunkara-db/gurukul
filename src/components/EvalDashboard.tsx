import React, {useCallback, useEffect, useRef, useState} from 'react';

/* ── Types ────────────────────────────────────────────────────────── */

interface DimStats { label: string; mean: number; min: number; max: number }

interface Aggregate {
  overall: number;
  topic_count: number;
  strong: number;
  moderate: number;
  weak: number;
  dimensions: Record<string, DimStats>;
}

interface TopicScore {
  topic_id: string;
  title: string;
  category: string;
  overall: number;
  verdict: 'strong' | 'moderate' | 'weak';
  dimensions: Record<string, number>;
  suggestion_count: number;
  top_suggestion: string | null;
}

interface Insight {
  type: string;
  title: string;
  message: string;
}

interface PlanAction {
  action: string;
  description: string;
  impact: string;
  topic_ids?: string[];
}

interface PlanItem {
  priority: number;
  dimension: string;
  current_score: number | null;
  target_score: number | null;
  status: string;
  actions: PlanAction[];
}

interface Improvements {
  overall: number;
  dimensions: Record<string, number>;
  improved_topics: { topic_id: string; title: string; before: number; after: number; delta: number }[];
  degraded_topics: { topic_id: string; title: string; before: number; after: number; delta: number }[];
}

interface EvalRun {
  run_id: number;
  aggregate: Aggregate;
  topics: TopicScore[];
  insights: Insight[];
  improvement_plan: PlanItem[];
  improvements: Improvements | null;
}

interface HistoryRun {
  id: number;
  run_at: string;
  overall: number;
  topic_count: number;
  strong: number;
  moderate: number;
  weak: number;
  dimensions: Record<string, DimStats | Record<string, number>>;
}

interface TopicDelta {
  title: string;
  before: number;
  after: number;
  overall: number;
  grounding: number;
  epistemic: number;
  references: number;
  structure: number;
}

interface EvalAction {
  id: number;
  dimension: string;
  action_type: string;
  status: string;
  topic_ids: string[];
  delta: Record<string, TopicDelta> | null;
  created_at: string;
}

interface ReviewResult {
  delta: Record<string, TopicDelta>;
  summary: { total: number; improved: number; degraded: number; unchanged: number };
}

interface Props {
  onSelectTopic: (id: string) => void;
}

/* ── Helpers ──────────────────────────────────────────────────────── */

const DIM_KEYS = [
  'depth', 'factual_accuracy', 'comprehensiveness', 'research_readiness',
  'grounding', 'epistemic', 'references', 'structure',
];
const DIM_META: Record<string, { label: string; icon: string; explain: string; group: string }> = {
  depth:              {label: 'Technical Depth',     icon: '🔬', explain: 'Explains mechanisms, edge cases, failure modes — not just descriptions', group: 'LLM Judge'},
  factual_accuracy:   {label: 'Factual Accuracy',    icon: '🎯', explain: 'Are claims verifiable and correct against known literature?', group: 'LLM Judge'},
  comprehensiveness:  {label: 'Comprehensiveness',   icon: '📋', explain: 'Covers all key aspects, trade-offs, and limitations?', group: 'LLM Judge'},
  research_readiness: {label: 'Research Readiness',  icon: '🎓', explain: 'Could someone write a related-works section from this?', group: 'LLM Judge'},
  grounding:          {label: 'Grounding',           icon: '⚓', explain: 'Are claims attributed to sources?', group: 'Heuristic'},
  epistemic:          {label: 'Epistemic Markers',   icon: '◉',  explain: 'Does content signal certainty levels?', group: 'Heuristic'},
  references:         {label: 'References',          icon: '📎', explain: 'Are citations real, complete, and sufficient?', group: 'Heuristic'},
  structure:          {label: 'Structure',            icon: '▦',  explain: 'Is content structurally complete with required fields?', group: 'Heuristic'},
};

function scoreColor(s: number) {
  if (s >= 85) return '#10b981';
  if (s >= 60) return '#f59e0b';
  return '#ef4444';
}

function verdictLabel(overall: number): { text: string; color: string; research: string } {
  if (overall >= 90) return {text: 'Excellent', color: '#10b981', research: 'Research-ready. Content is well-grounded and properly attributed.'};
  if (overall >= 85) return {text: 'Strong', color: '#10b981', research: 'Mostly research-ready. Verify specific claims before citing.'};
  if (overall >= 70) return {text: 'Moderate', color: '#f59e0b', research: 'Use as study material. Cross-check before using in papers.'};
  if (overall >= 50) return {text: 'Needs Work', color: '#f59e0b', research: 'Good for learning concepts. Do not cite without verification.'};
  return {text: 'Weak', color: '#ef4444', research: 'Draft quality only. Regenerate before relying on this content.'};
}

function delta(n: number) { return n > 0 ? `+${n}` : `${n}`; }

function relTime(iso: string) {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString();
}

/* ── Component ────────────────────────────────────────────────────── */

export default function EvalDashboard({onSelectTopic}: Props) {
  const [run, setRun] = useState<EvalRun | null>(null);
  const [history, setHistory] = useState<HistoryRun[]>([]);
  const [actions, setActions] = useState<EvalAction[]>([]);
  const [reviews, setReviews] = useState<Record<number, ReviewResult>>({});
  const [loading, setLoading] = useState(true);
  const [evaluating, setEvaluating] = useState(false);
  const [applyingDim, setApplyingDim] = useState<string | null>(null);
  const [reviewingId, setReviewingId] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ── helpers ── */

  const loadActions = useCallback(async (runId: number | null) => {
    if (runId) {
      const r = await fetch(`/api/eval/actions/${runId}`);
      if (r.ok) {
        const acts = (await r.json()).actions || [];
        if (acts.length > 0) { setActions(acts); return; }
      }
    }
    const r2 = await fetch('/api/eval/actions');
    if (r2.ok) setActions((await r2.json()).actions || []);
  }, []);

  const loadHistory = useCallback(async () => {
    const r = await fetch('/api/eval/history');
    if (r.ok) setHistory((await r.json()).runs || []);
  }, []);

  /* Load latest eval snapshot on mount (no new run created) */
  const loadLatest = useCallback(async () => {
    try {
      const res = await fetch('/api/eval/latest');
      if (res.ok) {
        const d = await res.json();
        if (!d.error) {
          setRun(d);
          await loadActions(d.run_id);
        }
      }
      await loadHistory();
    } catch {}
  }, [loadActions, loadHistory]);

  /* Record a new eval benchmark (only on explicit user click) */
  const evaluate = useCallback(async () => {
    setEvaluating(true);
    try {
      const res = await fetch('/api/eval/run', {method: 'POST'});
      if (res.ok) {
        const d = await res.json();
        if (!d.error) {
          setRun(d);
          setActions([]);
          setReviews({});
        }
      }
      await loadHistory();
    } catch {}
    setEvaluating(false);
  }, [loadHistory]);

  /* Mount: load latest (read-only) */
  useEffect(() => {
    setLoading(true);
    loadLatest().then(() => setLoading(false));
  }, [loadLatest]);

  /* SSE: listen for live eval_action + status events */
  useEffect(() => {
    const es = new EventSource('/api/events');

    const onEvalAction = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        setActions(prev => prev.map(a =>
          a.id === data.action_id ? {...a, status: data.status, delta: data.delta ?? a.delta} : a
        ));
        if (data.status === 'done' || data.status === 'failed') {
          loadActions(run?.run_id ?? null);
        }
      } catch {}
    };

    const onStatus = (_e: MessageEvent) => {
      // No-op: don't re-score on every topic status change.
      // User clicks "Re-evaluate" for a fresh score.
    };

    es.addEventListener('eval_action', onEvalAction);
    es.addEventListener('status', onStatus);

    return () => {
      es.removeEventListener('eval_action', onEvalAction);
      es.removeEventListener('status', onStatus);
      es.close();
    };
  }, [run?.run_id, loadActions]);

  /* Fallback poll: if any action is running, poll actions until done */
  useEffect(() => {
    const hasRunning = actions.some(a => a.status === 'running');
    if (hasRunning && !pollRef.current) {
      pollRef.current = setInterval(async () => {
        const r = await fetch('/api/eval/actions');
        if (r.ok) {
          const acts = (await r.json()).actions || [];
          setActions(acts);
          if (!acts.some((a: EvalAction) => a.status === 'running')) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      }, 5000);
    }
    if (!hasRunning && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [actions]);

  const applyAction = useCallback(async (item: PlanItem, action: PlanAction) => {
    if (!run) return;
    setApplyingDim(item.dimension);
    try {
      const res = await fetch('/api/eval/action/apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          eval_run_id: run.run_id,
          dimension: item.dimension,
          action_type: action.action,
          description: action.description,
          topic_ids: action.topic_ids || [],
        }),
      });
      if (res.ok) {
        const d = await res.json();
        if (d.ok) {
          setActions(prev => [...prev, {
            id: d.action_id,
            dimension: item.dimension,
            action_type: action.action,
            status: 'running',
            topic_ids: d.topic_ids || [],
            delta: null,
            created_at: new Date().toISOString(),
          }]);
        }
      }
    } catch {}
    setApplyingDim(null);
  }, [run]);

  const reviewAction = useCallback(async (actionId: number) => {
    setReviewingId(actionId);
    try {
      const res = await fetch(`/api/eval/action/${actionId}/review`, {method: 'POST'});
      if (res.ok) {
        const d = await res.json();
        if (d.ok) setReviews(prev => ({...prev, [actionId]: {delta: d.delta, summary: d.summary}}));
      }
    } catch {}
    setReviewingId(null);
  }, []);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  /* ── Loading ── */
  if (loading) {
    return (
      <div className="ev">
        <div className="ev-loading">
          <div className="ev-loading__spinner" />
          <span>Evaluating content quality across all topics...</span>
        </div>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="ev">
        <div className="ev-empty">
          <h2>No content to evaluate</h2>
          <p>Explore a seed topic first, then come back to assess content quality.</p>
        </div>
      </div>
    );
  }

  const {aggregate: agg, topics, insights, improvement_plan, improvements} = run;
  const v = verdictLabel(agg.overall);

  const actionsByDim: Record<string, EvalAction[]> = {};
  for (const a of actions) {
    if (!actionsByDim[a.dimension]) actionsByDim[a.dimension] = [];
    actionsByDim[a.dimension].push(a);
  }

  const historyReversed = [...history].reverse();

  return (
    <div className="ev">

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 1: Health Overview
          ═══════════════════════════════════════════════════════════════ */}
      <section className="ev-health">
        <div className="ev-health__gauge">
          <svg viewBox="0 0 120 120" className="ev-health__ring">
            <circle cx="60" cy="60" r="52" fill="none" stroke="var(--gk-card-border)" strokeWidth="8" />
            <circle cx="60" cy="60" r="52" fill="none" stroke={v.color} strokeWidth="8"
              strokeDasharray={`${(agg.overall / 100) * 327} 327`}
              strokeLinecap="round" transform="rotate(-90 60 60)" />
          </svg>
          <div className="ev-health__center">
            <span className="ev-health__score" style={{color: v.color}}>{agg.overall}</span>
            <span className="ev-health__verdict" style={{color: v.color}}>{v.text}</span>
          </div>
        </div>

        <div className="ev-health__detail">
          <p className="ev-health__research">{v.research}</p>
          <div className="ev-health__counts">
            <span className="ev-health__count">
              <b style={{color: '#10b981'}}>{agg.strong}</b> strong
            </span>
            <span className="ev-health__count">
              <b style={{color: '#f59e0b'}}>{agg.moderate}</b> moderate
            </span>
            <span className="ev-health__count">
              <b style={{color: '#ef4444'}}>{agg.weak}</b> weak
            </span>
            <span className="ev-health__count ev-health__count--total">
              of <b>{agg.topic_count}</b> topics
            </span>
          </div>
          {improvements && improvements.overall !== 0 && (
            <div className="ev-health__delta" style={{color: improvements.overall > 0 ? '#10b981' : '#ef4444'}}>
              {delta(improvements.overall)} pts since last evaluation
            </div>
          )}
          <button type="button" className="ev-health__refresh"
            onClick={evaluate} disabled={evaluating}>
            {evaluating ? 'Re-evaluating...' : '↻ Re-evaluate'}
          </button>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 2: Dimension Breakdown
          ═══════════════════════════════════════════════════════════════ */}
      <section className="ev-dims">
        <h3 className="ev-section-title">Quality Dimensions</h3>
        {['LLM Judge', 'Heuristic'].map((group) => {
          const groupKeys = DIM_KEYS.filter((dk) => DIM_META[dk].group === group);
          const groupDims = groupKeys.map((dk) => agg.dimensions[dk]).filter(Boolean);
          if (groupDims.length === 0) return null;
          return (
            <div key={group}>
              <div className="ev-dims__group-label">{group === 'LLM Judge' ? 'LLM-Evaluated (per topic)' : 'Heuristic Checks'}</div>
              <div className="ev-dims__grid">
                {groupKeys.map((dk) => {
                  const dim = agg.dimensions[dk];
                  if (!dim || dim.mean === 0) return null;
                  const meta = DIM_META[dk];
                  const impDelta = improvements?.dimensions?.[dk];
                  return (
                    <div key={dk} className="ev-dim-card">
                      <div className="ev-dim-card__head">
                        <span className="ev-dim-card__icon">{meta.icon}</span>
                        <span className="ev-dim-card__label">{meta.label}</span>
                        <span className="ev-dim-card__score" style={{color: scoreColor(dim.mean)}}>
                          {dim.mean}%
                          {impDelta != null && impDelta !== 0 && (
                            <small style={{color: impDelta > 0 ? '#10b981' : '#ef4444'}}>
                              {' '}{delta(impDelta)}
                            </small>
                          )}
                        </span>
                      </div>
                      <div className="ev-dim-card__bar">
                        <div className="ev-dim-card__fill" style={{width: `${dim.mean}%`, background: scoreColor(dim.mean)}} />
                      </div>
                      <div className="ev-dim-card__explain">{meta.explain}</div>
                      <div className="ev-dim-card__range">Range: {dim.min}% – {dim.max}%</div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 3: What This Means (Insights)
          ═══════════════════════════════════════════════════════════════ */}
      {insights.length > 0 && (
        <section className="ev-insights">
          <h3 className="ev-section-title">What This Means</h3>
          {insights.map((ins, i) => (
            <div key={i} className={`ev-insight ev-insight--${ins.type}`}>
              <strong className="ev-insight__title">{ins.title}</strong>
              <p className="ev-insight__msg">{ins.message}</p>
            </div>
          ))}
        </section>
      )}

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 4: Topic Heatmap
          ═══════════════════════════════════════════════════════════════ */}
      <section className="ev-heatmap">
        <h3 className="ev-section-title">Topic Quality Map</h3>
        <div className="ev-heatmap__grid">
          {[...topics].sort((a, b) => a.overall - b.overall).map((t) => (
            <button key={t.topic_id} type="button"
              className="ev-heatmap__cell"
              style={{background: scoreColor(t.overall) + '20', borderColor: scoreColor(t.overall)}}
              onClick={() => onSelectTopic(t.topic_id)}
              title={`${t.title}: ${t.overall}/100`}>
              <span className="ev-heatmap__cell-score" style={{color: scoreColor(t.overall)}}>
                {t.overall}
              </span>
              <span className="ev-heatmap__cell-title">{t.title}</span>
              <div className="ev-heatmap__cell-bars">
                {DIM_KEYS.filter((dk) => (t.dimensions[dk] ?? 0) > 0).map((dk) => (
                  <div key={dk} className="ev-heatmap__mini-bar" title={`${DIM_META[dk].label}: ${t.dimensions[dk]}%`}>
                    <div className="ev-heatmap__mini-fill"
                      style={{width: `${t.dimensions[dk]}%`, background: scoreColor(t.dimensions[dk])}} />
                  </div>
                ))}
              </div>
              {t.suggestion_count > 0 && (
                <span className="ev-heatmap__badge">{t.suggestion_count}</span>
              )}
            </button>
          ))}
        </div>
        <div className="ev-heatmap__legend">
          {DIM_KEYS.filter((dk) => agg.dimensions[dk]?.mean > 0).map((dk) => (
            <span key={dk}>{DIM_META[dk].icon} {DIM_META[dk].label}</span>
          ))}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 5: Improvement Actions (Apply → Review loop)
          ═══════════════════════════════════════════════════════════════ */}
      {improvement_plan.length > 0 && (
        <section className="ev-actions">
          {improvement_plan.every(item => (item.current_score ?? 0) >= 75) ? (
            <>
              <h3 className="ev-section-title">Quality Status</h3>
              <div className="ev-action-card__next" style={{margin: 0}}>
                <span className="ev-action-card__next-icon" style={{fontSize: '1.2rem'}}>✓</span>
                <span className="ev-action-card__next-text">
                  All quality dimensions are above 75%. Content is <strong>research-ready</strong>.
                  Continue exploring topics and re-evaluate periodically.
                </span>
              </div>
            </>
          ) : (
            <h3 className="ev-section-title">Improve</h3>
          )}
          {improvement_plan.length > 0 &&
          improvement_plan.map((item, i) => {
            const dimActs = actionsByDim[item.dimension] || [];
            const hasRunning = dimActs.some(a => a.status === 'running');
            const hasDone = dimActs.some(a => a.status === 'done');
            const isApplying = applyingDim === item.dimension;

            const dimKey = item.dimension.toLowerCase().replace(/ /g, '_')
              .replace('epistemic_markers', 'epistemic');
            const firstRun = historyReversed[0];
            const firstDimScore = firstRun?.dimensions?.[dimKey];
            const firstMean = firstDimScore && typeof firstDimScore === 'object' && 'mean' in firstDimScore
              ? (firstDimScore as DimStats).mean : null;
            const cumulativeDelta = firstMean != null && item.current_score != null
              ? item.current_score - firstMean : null;
            const iterationLabel = cumulativeDelta != null && cumulativeDelta > 0
              ? `+${cumulativeDelta} pts since first evaluation`
              : null;
            const weakCount = (item as any).weak_topic_count ?? 0;
            const scoreGoodEnough = (item.current_score ?? 0) >= 85 || ((item.current_score ?? 0) >= 70 && weakCount === 0);

            return (
              <div key={i} className="ev-action-card">
                <div className="ev-action-card__head">
                  <span className={`ev-action-card__badge ev-action-card__badge--${item.status}`}>
                    P{item.priority}
                  </span>
                  <span className="ev-action-card__dim">{item.dimension}</span>
                  {item.current_score != null && (
                    <span className="ev-action-card__target">
                      {item.current_score}%{item.target_score != null && item.current_score < item.target_score
                        ? ` (target: ${item.target_score}%)` : ''}
                    </span>
                  )}
                </div>

                {iterationLabel && (
                  <div className="ev-action-card__iteration">
                    ↑ {iterationLabel}
                  </div>
                )}

                {scoreGoodEnough ? (
                  <div className="ev-action-card__next">
                    <span className="ev-action-card__next-icon">✓</span>
                    <span className="ev-action-card__next-text">
                      Score is at <strong>{item.current_score}%</strong> — all topics meet quality threshold.
                      {iterationLabel && <> ({iterationLabel})</>}
                      {' '}No further action needed for this dimension.
                    </span>
                  </div>
                ) : (
                  <>
                    {item.actions.map((a, j) => {
                      const canApply = a.action.startsWith('regenerate');
                      return (
                        <div key={j} className="ev-action-card__row">
                          <span className={`ev-action-card__impact ev-action-card__impact--${a.impact}`}>
                            {a.impact}
                          </span>
                          <span className="ev-action-card__desc">{a.description}</span>
                          {canApply && !hasDone && (
                            <button type="button" className="ev-action-card__apply"
                              onClick={() => applyAction(item, a)}
                              disabled={isApplying || hasRunning}>
                              {isApplying || hasRunning ? '⟳ Running...' : 'Apply Fix'}
                            </button>
                          )}
                        </div>
                      );
                    })}

                    {/* Show weak topics breakdown */}
                    {(item as any).weak_topics?.length > 0 && (
                      <div className="ev-action-card__weak-list">
                        <div className="ev-action-card__weak-title">Topics needing improvement:</div>
                        {(item as any).weak_topics.map((wt: any) => (
                          <div key={wt.id} className="ev-action-card__weak-item">
                            <span className="ev-action-card__weak-name"
                              style={{cursor: 'pointer', textDecoration: 'underline'}}
                              onClick={() => onSelectTopic?.(wt.id)}
                            >{wt.title}</span>
                            <span className="ev-action-card__weak-score" style={{
                              color: wt.score < 50 ? '#ef4444' : wt.score < 70 ? '#f59e0b' : '#10b981'
                            }}>{wt.score}%</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {hasDone && !hasRunning && (
                      <div className="ev-action-card__next">
                        <span className="ev-action-card__next-icon">✓</span>
                        <span className="ev-action-card__next-text">
                          Fix applied. Click <strong>Re-evaluate</strong> above to record the new benchmark and see next improvements.
                        </span>
                      </div>
                    )}
                  </>
                )}

                {/* Action results */}
                {dimActs.filter(a => a.status === 'done' || a.status === 'running').map((act) => {
                  const review = reviews[act.id];
                  return (
                    <div key={act.id} className={`ev-action-result ev-action-result--${act.status}`}>
                      <div className="ev-action-result__head">
                        <span className="ev-action-result__status">
                          {act.status === 'running' ? '⟳ Regenerating...' : '✓ Applied'}
                        </span>
                        <span className="ev-action-result__meta">
                          {act.topic_ids.length} topic(s) · {relTime(act.created_at)}
                        </span>
                        {act.status === 'done' && !review && (
                          <button type="button" className="ev-action-result__review"
                            onClick={() => reviewAction(act.id)}
                            disabled={reviewingId === act.id}>
                            {reviewingId === act.id ? 'Reviewing...' : 'Review Changes'}
                          </button>
                        )}
                      </div>

                      {/* Quick preview */}
                      {act.status === 'done' && act.delta && !review && (
                        <div className="ev-action-result__preview">
                          {Object.values(act.delta).slice(0, 4).map((d, k) => (
                            <span key={k} className="ev-action-result__chip"
                              style={{color: d.overall > 0 ? '#10b981' : d.overall < 0 ? '#ef4444' : '#6b7280'}}>
                              {d.title?.slice(0, 20)}: {delta(d.overall)}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Full review */}
                      {review && (
                        <div className="ev-review">
                          <div className="ev-review__summary">
                            <span className="ev-review__pill" style={{background: '#10b98120', color: '#10b981'}}>
                              {review.summary.improved} improved
                            </span>
                            <span className="ev-review__pill" style={{background: '#6b728020', color: '#6b7280'}}>
                              {review.summary.unchanged} unchanged
                            </span>
                            {review.summary.degraded > 0 && (
                              <span className="ev-review__pill" style={{background: '#ef444420', color: '#ef4444'}}>
                                {review.summary.degraded} degraded
                              </span>
                            )}
                          </div>
                          <div className="ev-review__topics">
                            {Object.entries(review.delta).map(([tid, d]) => (
                              <button key={tid} type="button" className="ev-review__topic"
                                onClick={() => onSelectTopic(tid)}>
                                <span className="ev-review__topic-name">{d.title}</span>
                                <span className="ev-review__topic-scores">
                                  <span style={{color: '#6b7280'}}>{d.before}</span>
                                  <span className="ev-review__arrow">→</span>
                                  <span style={{color: scoreColor(d.after), fontWeight: 700}}>{d.after}</span>
                                  <span style={{
                                    color: d.overall > 0 ? '#10b981' : d.overall < 0 ? '#ef4444' : '#6b7280',
                                    fontSize: '0.65rem', fontWeight: 600,
                                  }}>
                                    ({delta(d.overall)})
                                  </span>
                                </span>
                              </button>
                            ))}
                          </div>
                          <p className="ev-review__next">
                            {review.summary.improved > 0 && review.summary.degraded === 0
                              ? '↑ All changes positive. Re-evaluate to record the new benchmark.'
                              : review.summary.degraded > 0
                              ? '⚠ Some topics degraded. Inspect them or apply the fix again.'
                              : '— No change. Try a different model or review content manually.'}
                          </p>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </section>
      )}

      {/* ═══════════════════════════════════════════════════════════════
          SECTION 6: Trend Over Time
          ═══════════════════════════════════════════════════════════════ */}
      {historyReversed.length > 1 && (
        <section className="ev-trend">
          <h3 className="ev-section-title">Quality Trend</h3>
          {(() => {
            const scores = historyReversed.map(h => h.overall);
            const mn = Math.min(...scores);
            const mx = Math.max(...scores);
            const pad = Math.max(5, Math.round((mx - mn) * 0.3));
            const lo = Math.max(0, mn - pad);
            const hi = Math.min(100, mx + pad);
            const range = Math.max(1, hi - lo);
            return (
              <div className="ev-trend__chart">
                <div className="ev-trend__axis">
                  <span>{hi}</span>
                  <span>{Math.round(lo + range / 2)}</span>
                  <span>{lo}</span>
                </div>
                {historyReversed.map((h, i) => {
                  const isLatest = i === historyReversed.length - 1;
                  const pct = ((h.overall - lo) / range) * 100;
                  const prev = i > 0 ? historyReversed[i - 1].overall : null;
                  const d = prev != null ? h.overall - prev : null;
                  return (
                    <div key={h.id} className={`ev-trend__col ${isLatest ? 'ev-trend__col--latest' : ''}`}>
                      <div className="ev-trend__bar-area">
                        <div className="ev-trend__bar"
                          style={{height: `${pct}%`, background: scoreColor(h.overall)}} />
                      </div>
                      <span className="ev-trend__val" style={{color: scoreColor(h.overall)}}>
                        {h.overall}
                        {d != null && d !== 0 && (
                          <small style={{color: d > 0 ? '#10b981' : '#ef4444', marginLeft: 2}}>
                            {delta(d)}
                          </small>
                        )}
                      </span>
                      <span className="ev-trend__time">{relTime(h.run_at)}</span>
                    </div>
                  );
                })}
              </div>
            );
          })()}

          {/* Dimension trend lines */}
          <div className="ev-trend__dims">
            {DIM_KEYS.filter((dk) => agg.dimensions[dk]?.mean > 0).map((dk) => {
              const vals = historyReversed.map(h => {
                const d = h.dimensions?.[dk];
                return (d && typeof d === 'object' && 'mean' in d) ? (d as DimStats).mean : 0;
              });
              const first = vals[0] || 0;
              const last = vals[vals.length - 1] || 0;
              const d = last - first;
              return (
                <div key={dk} className="ev-trend__dim-row">
                  <span className="ev-trend__dim-icon">{DIM_META[dk].icon}</span>
                  <span className="ev-trend__dim-name">{DIM_META[dk].label}</span>
                  <div className="ev-trend__dim-sparkline">
                    {vals.map((v, i) => (
                      <span key={i} className="ev-trend__dim-dot" style={{
                        background: scoreColor(v),
                        height: `${Math.max(4, (v / 100) * 20)}px`,
                      }} />
                    ))}
                  </div>
                  <span className="ev-trend__dim-now" style={{color: scoreColor(last)}}>{last}%</span>
                  {d !== 0 && (
                    <span style={{color: d > 0 ? '#10b981' : '#ef4444', fontSize: '0.65rem', fontWeight: 600}}>
                      {delta(d)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
