import React from 'react';

/**
 * Topic TL;DR card. Rendered at the top of every generated topic page
 * so the reader can decide in 30 seconds whether to dive deeper.
 */
export default function Summary({
  children,
  takeaway,
}: {
  children: React.ReactNode;
  takeaway?: string;
}) {
  return (
    <aside className="gk-card" aria-label="Topic summary">
      <div className="gk-card__label">TL;DR</div>
      <div>{children}</div>
      {takeaway && (
        <p style={{margin: '0.6rem 0 0 0', fontStyle: 'italic'}}>
          <strong>Bottom line:</strong> {takeaway}
        </p>
      )}
    </aside>
  );
}
