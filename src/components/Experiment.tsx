import React from 'react';
import {usePersistentState} from '@site/src/lib/storage';

export type ExperimentStep = {
  id: string;
  text: string;
};

/**
 * A small, runnable thing the reader can do to internalise the topic.
 * Could be a colab notebook to fork, a derivation to redo by hand, or
 * an ablation to try on a toy model. Each step is a checkbox that
 * persists across reloads. A free-text "notes" box lets the reader
 * record what they actually saw.
 */
export default function Experiment({
  topicId,
  title,
  steps,
  hypothesis,
}: {
  topicId: string;
  title: string;
  hypothesis?: string;
  steps: ExperimentStep[];
}) {
  const [done, setDone] = usePersistentState<Record<string, boolean>>(
    `topic:${topicId}:experiment:done`,
    {},
  );
  const [notes, setNotes] = usePersistentState<string>(
    `topic:${topicId}:experiment:notes`,
    '',
  );

  return (
    <section className="gk-card">
      <div className="gk-card__label">Experiment</div>
      <h3 className="gk-card__title">{title}</h3>
      {hypothesis && (
        <p>
          <strong>Hypothesis:</strong> {hypothesis}
        </p>
      )}
      <ol style={{listStyle: 'none', paddingLeft: 0}}>
        {steps.map((s) => (
          <li key={s.id} className="gk-checkbox-row">
            <input
              type="checkbox"
              checked={!!done[s.id]}
              onChange={(e) =>
                setDone((prev) => ({...prev, [s.id]: e.target.checked}))
              }
            />
            <span style={{textDecoration: done[s.id] ? 'line-through' : 'none'}}>
              {s.text}
            </span>
          </li>
        ))}
      </ol>
      <label
        style={{
          display: 'block',
          marginTop: '0.6rem',
          fontWeight: 600,
          fontSize: '0.85rem',
        }}
      >
        What did you observe?
      </label>
      <textarea
        className="gk-textarea"
        value={notes}
        placeholder="Numbers, surprises, things that broke your mental model…"
        onChange={(e) => setNotes(e.target.value)}
      />
    </section>
  );
}
