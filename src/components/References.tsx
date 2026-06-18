import React from 'react';
import {usePersistentState} from '@site/src/lib/storage';

export type Reference = {
  id: string;
  title: string;
  authors?: string;
  year?: number | string;
  arxiv?: string; // e.g. "2305.18290"
  url?: string;
};

type Status = 'unverified' | 'verified' | 'wrong' | 'missing';

const STATUS_LABEL: Record<Status, string> = {
  unverified: '○ unverified',
  verified: '✓ verified',
  wrong: '✗ wrong',
  missing: '? not found',
};

/**
 * Every reference the FM cites starts as `unverified`. The reader is
 * expected to click through and mark each one. The dashboard reports
 * the verification ratio — that ratio is the single best honesty
 * metric for the whole journey, because the FM's training-data
 * citations *will* sometimes be fabricated.
 */
export default function References({
  topicId,
  items,
}: {
  topicId: string;
  items: Reference[];
}) {
  const [status, setStatus] = usePersistentState<Record<string, Status>>(
    `topic:${topicId}:refs`,
    {},
  );

  const cycle = (s: Status): Status =>
    s === 'unverified'
      ? 'verified'
      : s === 'verified'
        ? 'wrong'
        : s === 'wrong'
          ? 'missing'
          : 'unverified';

  return (
    <section className="gk-card">
      <div className="gk-card__label">References</div>
      <p style={{margin: '0 0 0.6rem 0', color: 'var(--gk-muted)'}}>
        Click each status badge to cycle{' '}
        <span className="gk-pill">unverified → verified → wrong → missing</span>
        . Don't cite anything you haven't verified yourself.
      </p>
      <ol style={{paddingLeft: '1.4rem'}}>
        {items.map((r) => {
          const s = status[r.id] ?? 'unverified';
          const href =
            r.url ??
            (r.arxiv ? `https://arxiv.org/abs/${r.arxiv}` : undefined);
          return (
            <li key={r.id} style={{marginBottom: '0.4rem'}}>
              <button
                type="button"
                className="gk-button"
                onClick={() =>
                  setStatus((prev) => ({...prev, [r.id]: cycle(s)}))
                }
                style={{marginRight: '0.6rem'}}
              >
                {STATUS_LABEL[s]}
              </button>
              {href ? (
                <a href={href} target="_blank" rel="noreferrer">
                  {r.title}
                </a>
              ) : (
                <span>{r.title}</span>
              )}
              {r.authors && (
                <span style={{color: 'var(--gk-muted)'}}> — {r.authors}</span>
              )}
              {r.year && (
                <span style={{color: 'var(--gk-muted)'}}> ({r.year})</span>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
