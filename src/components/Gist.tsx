import React from 'react';

/**
 * A "gist" is the smallest learnable artifact for a topic: a snippet of
 * code, math, or pseudocode plus a one-paragraph annotation. We render
 * the children as-is so the FM can choose between fenced code, KaTeX,
 * or prose; the wrapper just provides framing and an optional caption.
 */
export default function Gist({
  caption,
  children,
}: {
  caption?: string;
  children: React.ReactNode;
}) {
  return (
    <figure className="gk-card" style={{margin: '1.2rem 0'}}>
      <div className="gk-card__label">Gist</div>
      <div>{children}</div>
      {caption && (
        <figcaption
          style={{
            marginTop: '0.6rem',
            color: 'var(--gk-muted)',
            fontSize: '0.9rem',
          }}
        >
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
