import React from 'react';
import {usePersistentState} from '@site/src/lib/storage';

export type Seed = {
  id: string;
  text: string;
  createdAt: number;
};

/**
 * "Research seeds" are half-formed ideas the reader wants to come back
 * to. The dashboard aggregates seeds across all topics, and the
 * proposal outline export lets the reader pick which seeds to develop
 * into a NeurIPS-style draft.
 */
export default function ResearchSeed({topicId}: {topicId: string}) {
  const [seeds, setSeeds] = usePersistentState<Seed[]>(
    `topic:${topicId}:seeds`,
    [],
  );
  const [draft, setDraft] = React.useState('');

  const add = () => {
    if (!draft.trim()) return;
    setSeeds([
      ...seeds,
      {id: cryptoRandomId(), text: draft.trim(), createdAt: Date.now()},
    ]);
    setDraft('');
  };

  const remove = (id: string) =>
    setSeeds(seeds.filter((s) => s.id !== id));

  return (
    <section className="gk-card">
      <div className="gk-card__label">Research seed</div>
      <p style={{margin: '0 0 0.6rem 0', color: 'var(--gk-muted)'}}>
        Capture any half-formed idea you'd want to chase. They aggregate
        on the dashboard and feed your proposal outline.
      </p>
      <textarea
        className="gk-textarea"
        value={draft}
        placeholder="What if we replaced X with Y? / It seems nobody has tried…"
        onChange={(e) => setDraft(e.target.value)}
      />
      <div style={{marginTop: '0.5rem'}}>
        <button type="button" className="gk-button" onClick={add}>
          + Save seed
        </button>
      </div>
      {seeds.length > 0 && (
        <ul style={{marginTop: '0.8rem', paddingLeft: '1.2rem'}}>
          {seeds.map((s) => (
            <li key={s.id} style={{marginBottom: '0.3rem'}}>
              {s.text}{' '}
              <button
                type="button"
                onClick={() => remove(s.id)}
                className="gk-button"
                style={{marginLeft: '0.4rem', fontSize: '0.7rem'}}
              >
                remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function cryptoRandomId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
