import React from 'react';
import {usePersistentState} from '@site/src/lib/storage';

/**
 * Honest self-rating: how confident the reader is that they could
 * *explain this topic to someone else without notes*. Deliberately not
 * called a "learning score" because a self-rating does not measure
 * learning — it measures confidence. The dashboard aggregates these
 * and highlights topics where confidence is low so the reader can
 * revisit.
 */
export default function ConfidenceTracker({topicId}: {topicId: string}) {
  const [score, setScore] = usePersistentState<number>(
    `topic:${topicId}:confidence`,
    0,
  );

  return (
    <section className="gk-card">
      <div className="gk-card__label">Confidence tracker</div>
      <p style={{margin: '0 0 0.6rem 0', color: 'var(--gk-muted)'}}>
        Could you explain this topic to a smart friend without notes?
      </p>
      <div
        className="gk-stars"
        role="radiogroup"
        aria-label="Confidence rating"
      >
        {[1, 2, 3, 4, 5].map((n) => (
          <span
            key={n}
            role="radio"
            aria-checked={score === n}
            tabIndex={0}
            className={`gk-star ${score >= n ? 'gk-star--on' : ''}`}
            onClick={() => setScore(score === n ? 0 : n)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setScore(score === n ? 0 : n);
              }
            }}
          >
            ★
          </span>
        ))}
        <span style={{marginLeft: '0.6rem', color: 'var(--gk-muted)'}}>
          {score === 0
            ? 'not yet rated'
            : ['', 'no idea', 'shaky', 'getting there', 'solid', 'I could teach this'][
                score
              ]}
        </span>
      </div>
    </section>
  );
}
