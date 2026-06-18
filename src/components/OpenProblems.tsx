import React from 'react';
import {usePersistentState} from '@site/src/lib/storage';

export type OpenProblem = {
  id: string;
  question: string;
  why: string;
};

/**
 * Surfaces unsettled research questions for a topic. Each one can be
 * "starred" by the reader; starred problems are aggregated on the
 * dashboard and feed the proposal-outline export. This is the spine of
 * the journal-to-research-proposal pipeline.
 */
export default function OpenProblems({
  topicId,
  items,
}: {
  topicId: string;
  items: OpenProblem[];
}) {
  const [starred, setStarred] = usePersistentState<Record<string, boolean>>(
    `topic:${topicId}:openProblems`,
    {},
  );

  return (
    <section className="gk-card">
      <div className="gk-card__label">Open problems</div>
      <p style={{margin: '0 0 0.6rem 0', color: 'var(--gk-muted)'}}>
        Star the ones you want to chase. They'll show up on your dashboard
        and feed your proposal outline.
      </p>
      <ul style={{listStyle: 'none', paddingLeft: 0, margin: 0}}>
        {items.map((p) => {
          const on = !!starred[p.id];
          return (
            <li key={p.id} style={{marginBottom: '0.6rem'}}>
              <button
                type="button"
                aria-label={on ? 'Unstar' : 'Star'}
                onClick={() =>
                  setStarred((prev) => ({...prev, [p.id]: !on}))
                }
                className="gk-button"
                style={{
                  marginRight: '0.6rem',
                  color: on ? 'var(--gk-accent)' : 'var(--gk-muted)',
                  fontWeight: on ? 700 : 400,
                }}
              >
                {on ? '★' : '☆'}
              </button>
              <strong>{p.question}</strong>
              <div style={{color: 'var(--gk-muted)', marginLeft: '2.2rem'}}>
                {p.why}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
