import React from 'react';

/**
 * Single "key aspect" card — one bite-sized concept the reader needs to
 * internalise. Several of these stacked together form the crux of the
 * topic. Optional `intuition` is a one-liner that captures the gut
 * feeling vs the precise statement in `children`.
 */
export default function KeyAspect({
  title,
  intuition,
  children,
}: {
  title: string;
  intuition?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="gk-card">
      <div className="gk-card__label">Key aspect</div>
      <h3 className="gk-card__title">{title}</h3>
      {intuition && (
        <p style={{margin: '0.2rem 0 0.6rem 0', color: 'var(--gk-muted)'}}>
          <em>Intuition:</em> {intuition}
        </p>
      )}
      <div>{children}</div>
    </section>
  );
}
