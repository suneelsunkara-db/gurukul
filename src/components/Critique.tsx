import React from 'react';
import {usePersistentState} from '@site/src/lib/storage';

/**
 * Pushback widget. The single most valuable thing for paper-writing:
 * the reader records where they *disagree* with the FM-generated
 * content. These notes feed the proposal outline export, because
 * disagreements are where novel angles live.
 */
export default function Critique({topicId}: {topicId: string}) {
  const [text, setText] = usePersistentState<string>(
    `topic:${topicId}:critique`,
    '',
  );

  return (
    <section className="gk-card">
      <div className="gk-card__label">Your pushback</div>
      <p style={{margin: '0 0 0.6rem 0', color: 'var(--gk-muted)'}}>
        Where does this explanation feel hand-wavy, incomplete, or wrong?
        Where would <em>you</em> push? Disagreements feed your proposal
        outline.
      </p>
      <textarea
        className="gk-textarea"
        value={text}
        placeholder="The teacher glosses over… / I doubt that… / What if instead…"
        onChange={(e) => setText(e.target.value)}
      />
    </section>
  );
}
