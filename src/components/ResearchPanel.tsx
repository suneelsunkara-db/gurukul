import React, {useCallback, useEffect, useState} from 'react';

interface CompetenceTopic {
  topic_id: string;
  title: string;
  category: string;
  assessed: boolean;
  mcq_score: number | null;
  mcq_total: number;
  dimensions: Record<string, number>;
  misconceptions: {concept: string; pattern: string}[];
  connections: string[];
}

interface CompetenceData {
  topics: CompetenceTopic[];
  summary: {
    total_topics: number;
    assessed: number;
    not_assessed: number;
    average_score: number | null;
    strong_count: number;
    weak_count: number;
  };
  ready_for_research: boolean;
}

interface ResearchDirection {
  title: string;
  abstract_seed: string;
  builds_on: string[];
  gap_addressed: string;
  methodology_hint: string;
  difficulty: string;
  related_work_topics: string[];
}

interface PaperScaffold {
  title: string;
  abstract: string;
  sections: {
    heading: string;
    purpose: string;
    key_points: string[];
    source_topics: string[];
  }[];
  key_arguments: string[];
  evaluation_strategy: string;
  potential_venues: string[];
}

const DIFFICULTY_COLORS: Record<string, string> = {
  accessible: '#10b981',
  moderate: '#f59e0b',
  ambitious: '#ef4444',
};

export default function ResearchPanel() {
  const [competence, setCompetence] = useState<CompetenceData | null>(null);
  const [directions, setDirections] = useState<ResearchDirection[]>([]);
  const [scaffold, setScaffold] = useState<PaperScaffold | null>(null);
  const [selectedDirection, setSelectedDirection] = useState<ResearchDirection | null>(null);
  const [loading, setLoading] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    loadCompetence();
  }, []);

  const loadCompetence = useCallback(async () => {
    try {
      const res = await fetch('/api/research/competence');
      const data = await res.json();
      setCompetence(data);
    } catch (e) {
      setError('Failed to load competence map');
    }
  }, []);

  const discoverDirections = useCallback(async () => {
    setLoading('directions');
    setError('');
    try {
      const res = await fetch('/api/research/directions', {method: 'POST'});
      if (!res.ok) {
        const d = await res.json();
        setError(d.detail || 'Failed to generate directions');
        return;
      }
      const data = await res.json();
      setDirections(data.directions || []);
    } catch (e) {
      setError('Failed to generate directions');
    } finally {
      setLoading('');
    }
  }, []);

  const generateScaffold = useCallback(async (dir: ResearchDirection) => {
    setSelectedDirection(dir);
    setLoading('scaffold');
    setError('');
    try {
      const res = await fetch('/api/research/scaffold', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({direction: dir}),
      });
      const data = await res.json();
      setScaffold(data.scaffold);
    } catch (e) {
      setError('Failed to generate scaffold');
    } finally {
      setLoading('');
    }
  }, []);

  if (!competence) {
    return <div className="rp-loading">Loading competence map...</div>;
  }

  const {summary, topics} = competence;
  const assessed = topics.filter(t => t.assessed);
  const notAssessed = topics.filter(t => !t.assessed);

  return (
    <div className="rp-panel">
      <h2 className="rp-title">Research Paper Pipeline</h2>
      <p className="rp-subtitle">
        Explore → Assess → Discover directions → Generate paper scaffold
      </p>

      {/* Stage 1: Competence Map */}
      <div className="rp-section">
        <h3 className="rp-section-title">
          <span className="rp-stage">1</span> Your Competence Map
        </h3>

        <div className="rp-stats">
          <div className="rp-stat">
            <span className="rp-stat-value">{summary.assessed}</span>
            <span className="rp-stat-label">Topics assessed</span>
          </div>
          <div className="rp-stat">
            <span className="rp-stat-value">{summary.average_score ?? '—'}%</span>
            <span className="rp-stat-label">Average score</span>
          </div>
          <div className="rp-stat">
            <span className="rp-stat-value" style={{color: '#10b981'}}>{summary.strong_count}</span>
            <span className="rp-stat-label">Strong</span>
          </div>
          <div className="rp-stat">
            <span className="rp-stat-value" style={{color: '#ef4444'}}>{summary.weak_count}</span>
            <span className="rp-stat-label">Weak</span>
          </div>
        </div>

        {assessed.length > 0 && (
          <div className="rp-topic-grid">
            {assessed.sort((a, b) => (b.mcq_score ?? 0) - (a.mcq_score ?? 0)).map(t => (
              <div key={t.topic_id} className={`rp-topic-card ${(t.mcq_score ?? 0) >= 80 ? 'rp-topic-card--strong' : (t.mcq_score ?? 0) >= 60 ? 'rp-topic-card--moderate' : 'rp-topic-card--weak'}`}>
                <div className="rp-topic-card__head">
                  <span className="rp-topic-card__title">{t.title}</span>
                  <span className="rp-topic-card__score">{t.mcq_score}%</span>
                </div>
                {Object.entries(t.dimensions).length > 0 && (
                  <div className="rp-topic-card__dims">
                    {Object.entries(t.dimensions).map(([dim, score]) => (
                      <span key={dim} className="rp-dim-chip" style={{
                        background: score >= 80 ? '#d1fae5' : score >= 50 ? '#fef3c7' : '#fee2e2',
                        color: score >= 80 ? '#065f46' : score >= 50 ? '#92400e' : '#991b1b',
                      }}>
                        {dim}: {score}%
                      </span>
                    ))}
                  </div>
                )}
                {t.misconceptions.length > 0 && (
                  <div className="rp-topic-card__miscon">
                    {t.misconceptions.map((m, i) => (
                      <div key={i} className="rp-miscon">
                        <span className="rp-miscon-icon">!</span> {m.concept}: {m.pattern}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {notAssessed.length > 0 && (
          <div className="rp-not-assessed">
            <span className="rp-not-assessed-label">Not yet assessed ({notAssessed.length}):</span>
            {notAssessed.map(t => (
              <span key={t.topic_id} className="rp-na-chip">{t.title}</span>
            ))}
          </div>
        )}
      </div>

      {/* Stage 2: Research Directions */}
      <div className="rp-section">
        <h3 className="rp-section-title">
          <span className="rp-stage">2</span> Research Directions
        </h3>

        {!competence.ready_for_research ? (
          <div className="rp-gate">
            Complete MCQ challenges on at least 3 topics with 60%+ average to unlock research direction discovery.
          </div>
        ) : directions.length === 0 ? (
          <button
            type="button"
            className="rp-discover-btn"
            onClick={discoverDirections}
            disabled={loading === 'directions'}
          >
            {loading === 'directions' ? 'Analyzing your competence map...' : 'Discover Research Directions'}
          </button>
        ) : (
          <div className="rp-directions">
            {directions.map((dir, i) => (
              <div key={i} className="rp-direction-card">
                <div className="rp-direction-card__head">
                  <h4 className="rp-direction-card__title">{dir.title}</h4>
                  <span className="rp-direction-card__diff" style={{
                    color: DIFFICULTY_COLORS[dir.difficulty] || '#94a3b8'
                  }}>{dir.difficulty}</span>
                </div>
                <p className="rp-direction-card__abstract">{dir.abstract_seed}</p>
                <div className="rp-direction-card__meta">
                  <div><strong>Gap:</strong> {dir.gap_addressed}</div>
                  <div><strong>Method:</strong> {dir.methodology_hint}</div>
                  <div><strong>Builds on:</strong> {dir.builds_on.join(', ')}</div>
                </div>
                <button
                  type="button"
                  className="rp-scaffold-btn"
                  onClick={() => generateScaffold(dir)}
                  disabled={loading === 'scaffold'}
                >
                  {loading === 'scaffold' && selectedDirection === dir
                    ? 'Generating scaffold...'
                    : 'Generate Paper Scaffold'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Stage 3: Paper Scaffold */}
      {scaffold && (
        <div className="rp-section">
          <h3 className="rp-section-title">
            <span className="rp-stage">3</span> Paper Scaffold
          </h3>

          <div className="rp-scaffold">
            <h4 className="rp-scaffold__title">{scaffold.title}</h4>

            <div className="rp-scaffold__block">
              <div className="rp-scaffold__label">Abstract</div>
              <p className="rp-scaffold__abstract">{scaffold.abstract}</p>
            </div>

            <div className="rp-scaffold__block">
              <div className="rp-scaffold__label">Key Arguments</div>
              <ol className="rp-scaffold__args">
                {scaffold.key_arguments.map((arg, i) => (
                  <li key={i}>{arg}</li>
                ))}
              </ol>
            </div>

            <div className="rp-scaffold__block">
              <div className="rp-scaffold__label">Paper Structure</div>
              {scaffold.sections.map((sec, i) => (
                <div key={i} className="rp-scaffold__section">
                  <div className="rp-scaffold__section-head">
                    <strong>{sec.heading}</strong>
                    <span className="rp-scaffold__purpose">{sec.purpose}</span>
                  </div>
                  <ul>
                    {sec.key_points.map((pt, j) => <li key={j}>{pt}</li>)}
                  </ul>
                  {sec.source_topics.length > 0 && (
                    <div className="rp-scaffold__sources">
                      Sources: {sec.source_topics.join(', ')}
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div className="rp-scaffold__block">
              <div className="rp-scaffold__label">Evaluation Strategy</div>
              <p>{scaffold.evaluation_strategy}</p>
            </div>

            <div className="rp-scaffold__block">
              <div className="rp-scaffold__label">Potential Venues</div>
              <div className="rp-scaffold__venues">
                {scaffold.potential_venues.map((v, i) => (
                  <span key={i} className="rp-venue-chip">{v}</span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {error && <div className="rp-error">{error}</div>}
    </div>
  );
}
