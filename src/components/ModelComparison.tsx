import React from 'react';

export type Confidence = 'high' | 'medium' | 'low' | 'unknown';

export type ComparisonCell = {
  value: string;
  confidence?: Confidence;
  note?: string;
};

export type ComparisonRow = {
  dimension: string;
  description?: string;
  cells: Record<string, ComparisonCell>;
};

/**
 * First-class component for the comparison chapters. The whole point of
 * Gurukul is to make the *how each model is built* differentiation
 * legible. Cells carry an explicit confidence so readers can see at a
 * glance which claims are well-known vs the FM filling in plausible
 * blanks. "unknown" with a note is the most honest cell when public
 * info is genuinely missing — DO NOT let the FM make something up.
 */
export default function ModelComparison({
  models,
  rows,
  caption,
}: {
  models: string[];
  rows: ComparisonRow[];
  caption?: string;
}) {
  const cls = (c?: Confidence) =>
    c === 'high'
      ? 'gk-confidence-high'
      : c === 'medium'
        ? 'gk-confidence-med'
        : c === 'low' || c === 'unknown'
          ? 'gk-confidence-low'
          : '';

  return (
    <figure style={{margin: '1.5rem 0', overflowX: 'auto'}}>
      <table className="gk-table">
        <thead>
          <tr>
            <th>Dimension</th>
            {models.map((m) => (
              <th key={m}>{m}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.dimension}>
              <td>
                <strong>{row.dimension}</strong>
                {row.description && (
                  <div
                    style={{
                      color: 'var(--gk-muted)',
                      fontSize: '0.8rem',
                      marginTop: '0.2rem',
                    }}
                  >
                    {row.description}
                  </div>
                )}
              </td>
              {models.map((m) => {
                const cell = row.cells[m];
                if (!cell) {
                  return (
                    <td key={m} className="gk-confidence-low">
                      — no public info —
                    </td>
                  );
                }
                return (
                  <td key={m}>
                    <div className={cls(cell.confidence)}>{cell.value}</div>
                    {cell.note && (
                      <div
                        style={{
                          color: 'var(--gk-muted)',
                          fontSize: '0.8rem',
                          marginTop: '0.2rem',
                        }}
                      >
                        {cell.note}
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {caption && (
        <figcaption
          style={{
            color: 'var(--gk-muted)',
            fontSize: '0.85rem',
            marginTop: '0.4rem',
          }}
        >
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
