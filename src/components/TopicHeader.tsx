import React from 'react';

/**
 * Lightweight header for every generated topic page. Surfaces the
 * protocol used to generate it, the two endpoints, and a generated-at
 * timestamp so the reader knows this is an AI artifact and roughly how
 * stale it is.
 */
export default function TopicHeader({
  protocol,
  teacher,
  student,
  generatedAt,
}: {
  protocol: string;
  teacher: string;
  student: string;
  generatedAt: string;
}) {
  const when = (() => {
    try {
      return new Date(generatedAt).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
    } catch {
      return generatedAt;
    }
  })();

  return (
    <div
      style={{
        margin: '0.5rem 0 1rem 0',
        color: 'var(--gk-muted)',
        fontSize: '0.85rem',
      }}
    >
      <span className="gk-pill">{protocol}</span>
      <span className="gk-pill">teacher: {teacher}</span>
      <span className="gk-pill">student: {student}</span>
      <span className="gk-pill">generated {when}</span>
    </div>
  );
}
