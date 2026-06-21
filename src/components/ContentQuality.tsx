import React, {useCallback, useEffect, useState} from 'react';

interface QualityDimension {
  label: string;
  description: string;
  score: number;
  suggestions: string[];
}

interface QualityData {
  topic_id: string;
  overall: number;
  verdict: 'strong' | 'moderate' | 'weak';
  verdict_text: string;
  dimensions: Record<string, QualityDimension>;
  suggestions: string[];
  can_regenerate: boolean;
}

interface Props {
  topicId: string;
  onRegenerate?: () => void;
}

const VERDICT_COLORS: Record<string, string> = {
  strong: '#10b981',
  moderate: '#f59e0b',
  weak: '#ef4444',
};

const DIM_ORDER = ['grounding', 'epistemic', 'references', 'structure'];

export default function ContentQuality({topicId, onRegenerate}: Props) {
  const [data, setData] = useState<QualityData | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const fetchQuality = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/quality/${topicId}`);
      if (res.ok) setData(await res.json());
    } catch {}
    setLoading(false);
  }, [topicId]);

  useEffect(() => {
    fetchQuality();
  }, [fetchQuality]);

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      await fetch(`/api/topic/${topicId}/regenerate`, {method: 'POST'});
      onRegenerate?.();
    } catch {}
    setRegenerating(false);
  };

  if (loading) return <div className="gk-quality gk-quality--loading">Analyzing content quality...</div>;
  if (!data) return null;

  const verdictColor = VERDICT_COLORS[data.verdict] ?? '#6b7280';

  return (
    <div className="gk-quality">
      {/* Compact header — always visible */}
      <button
        type="button"
        className="gk-quality__header"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="gk-quality__header-left">
          <span
            className="gk-quality__score-ring"
            style={{'--ring-color': verdictColor, '--ring-pct': `${data.overall}%`} as React.CSSProperties}
          >
            {data.overall}
          </span>
          <span className="gk-quality__verdict" style={{color: verdictColor}}>
            Content Quality: {data.verdict.charAt(0).toUpperCase() + data.verdict.slice(1)}
          </span>
          {data.suggestions.length > 0 && (
            <span className="gk-quality__suggestion-count">
              {data.suggestions.length} suggestion{data.suggestions.length > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <span className="gk-quality__chevron">{expanded ? '▾' : '▸'}</span>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="gk-quality__body">
          <p className="gk-quality__verdict-text">{data.verdict_text}</p>

          {/* Dimension bars */}
          <div className="gk-quality__dims">
            {DIM_ORDER.map((key) => {
              const dim = data.dimensions[key];
              if (!dim) return null;
              const barColor = dim.score >= 80 ? '#10b981' : dim.score >= 50 ? '#f59e0b' : '#ef4444';
              return (
                <div key={key} className="gk-quality__dim">
                  <div className="gk-quality__dim-header">
                    <span className="gk-quality__dim-label">{dim.label}</span>
                    <span className="gk-quality__dim-score" style={{color: barColor}}>
                      {dim.score}%
                    </span>
                  </div>
                  <div className="gk-quality__dim-bar">
                    <div
                      className="gk-quality__dim-fill"
                      style={{width: `${dim.score}%`, background: barColor}}
                    />
                  </div>
                  <div className="gk-quality__dim-desc">{dim.description}</div>
                  {dim.suggestions.length > 0 && (
                    <ul className="gk-quality__dim-suggestions">
                      {dim.suggestions.map((s, i) => (
                        <li key={i}>{s}</li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>

          {/* Regenerate button */}
          {data.can_regenerate && (
            <div className="gk-quality__actions">
              <button
                type="button"
                className="gk-quality__regen"
                onClick={handleRegenerate}
                disabled={regenerating}
              >
                {regenerating ? 'Regenerating...' : 'Regenerate with improved prompts'}
              </button>
              <span className="gk-quality__regen-hint">
                Re-runs the Student agent with hardened grounding guardrails
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
