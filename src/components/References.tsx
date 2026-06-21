import React, { useCallback, useEffect, useState } from 'react';

export type Reference = {
  id: string;
  title: string;
  authors?: string;
  year?: number | string;
  arxiv?: string;
  url?: string;
  verified?: boolean;
};

type VerifiedRef = {
  claimed: string;
  actual: string | null;
  arxiv_id: string | null;
  match: number;
  issues: string[];
};

type InvalidRef = {
  claimed: string;
  claimed_id: string | null;
  issues: string[];
};

type RecentPaper = {
  title: string;
  authors: string[];
  year: number;
  arxiv_id: string;
  summary: string;
  url: string;
};

type VerificationData = {
  verification_rate: number;
  freshness_score: number;
  verified: VerifiedRef[];
  invalid: InvalidRef[];
  missing_important: RecentPaper[];
  recent: RecentPaper[];
};

export default function References({
  topicId,
  items,
}: {
  topicId: string;
  items: Reference[];
}) {
  const [verification, setVerification] = useState<VerificationData | null>(null);
  const [loading, setLoading] = useState(false);
  const [showRecent, setShowRecent] = useState(false);

  const runVerification = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`/api/arxiv/verify/${topicId}`);
      if (resp.ok) {
        setVerification(await resp.json());
      }
    } catch (e) {
      console.warn('arXiv verification failed', e);
    } finally {
      setLoading(false);
    }
  }, [topicId]);

  useEffect(() => {
    setVerification(null);
    setShowRecent(false);
  }, [topicId]);

  const getStatus = (ref: Reference): { label: string; cls: string } => {
    if (!verification) {
      return { label: '○ unverified', cls: 'ref-unverified' };
    }
    const found = verification.verified.find(
      v => v.claimed.toLowerCase().startsWith(ref.title.slice(0, 30).toLowerCase())
    );
    if (found) {
      return found.match >= 50
        ? { label: `✓ verified (${found.match}%)`, cls: 'ref-verified' }
        : { label: `⚠ weak match (${found.match}%)`, cls: 'ref-weak' };
    }
    const inv = verification.invalid.find(
      v => v.claimed.toLowerCase().startsWith(ref.title.slice(0, 30).toLowerCase())
    );
    if (inv) {
      return { label: '✗ not found on arXiv', cls: 'ref-invalid' };
    }
    return { label: '○ unverified', cls: 'ref-unverified' };
  };

  return (
    <section className="gk-card">
      <div className="gk-card__label" style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
        References
        {verification && (
          <span className={`ref-rate ${verification.verification_rate >= 70 ? 'ref-rate--good' : 'ref-rate--low'}`}>
            {verification.verification_rate}% verified via arXiv
          </span>
        )}
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', margin: '0.4rem 0 0.8rem' }}>
        <button
          className="gk-button gk-button--sm"
          onClick={runVerification}
          disabled={loading || items.length === 0}
        >
          {loading ? 'Verifying…' : verification ? 'Re-verify via arXiv' : 'Verify via arXiv'}
        </button>
        <button
          className="gk-button gk-button--sm gk-button--ghost"
          onClick={() => setShowRecent(!showRecent)}
        >
          {showRecent ? 'Hide' : 'Show'} Recent Papers
        </button>
      </div>

      <ol style={{ paddingLeft: '1.4rem' }}>
        {items.map((r) => {
          const st = getStatus(r);
          const href = r.url ?? (r.arxiv ? `https://arxiv.org/abs/${r.arxiv}` : undefined);
          return (
            <li key={r.id} style={{ marginBottom: '0.5rem' }}>
              <span className={`ref-badge ${st.cls}`}>{st.label}</span>{' '}
              {href ? (
                <a href={href} target="_blank" rel="noreferrer">{r.title}</a>
              ) : (
                <span>{r.title}</span>
              )}
              {r.authors && <span className="ref-meta"> — {r.authors}</span>}
              {r.year && <span className="ref-meta"> ({r.year})</span>}
            </li>
          );
        })}
      </ol>

      {verification && verification.invalid.length > 0 && (
        <div className="ref-warning">
          <strong>⚠ {verification.invalid.length} reference(s) could not be verified:</strong>
          <ul>
            {verification.invalid.map((inv, i) => (
              <li key={i}>
                "{inv.claimed}" — {inv.issues.join('; ')}
              </li>
            ))}
          </ul>
        </div>
      )}

      {verification && verification.missing_important.length > 0 && (
        <div className="ref-suggestion">
          <strong>Papers you might want to cite:</strong>
          <ul>
            {verification.missing_important.map((p, i) => (
              <li key={i}>
                <a href={p.url} target="_blank" rel="noreferrer">{p.title}</a>{' '}
                ({p.authors.join(', ')}, {p.year})
                <span className="ref-meta"> arXiv:{p.arxiv_id}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {showRecent && <RecentPapers topicId={topicId} />}
    </section>
  );
}


function RecentPapers({ topicId }: { topicId: string }) {
  const [papers, setPapers] = useState<RecentPaper[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`/api/arxiv/recent/${topicId}`);
        if (resp.ok && !cancelled) {
          const data = await resp.json();
          setPapers(data.papers || []);
        }
      } catch (e) {
        console.warn('Recent papers fetch failed', e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [topicId]);

  if (loading) return <div className="ref-loading">Loading recent papers from arXiv…</div>;
  if (papers.length === 0) return <div className="ref-empty">No recent papers found.</div>;

  return (
    <div className="ref-recent">
      <div className="ref-recent__title">Recent Papers from arXiv</div>
      {papers.map((p, i) => (
        <div key={i} className="ref-recent__paper">
          <a href={p.url} target="_blank" rel="noreferrer" className="ref-recent__paper-title">
            {p.title}
          </a>
          <div className="ref-recent__paper-meta">
            {p.authors.slice(0, 3).join(', ')} ({p.year}) · arXiv:{p.arxiv_id}
          </div>
          <div className="ref-recent__paper-summary">{p.summary}</div>
        </div>
      ))}
    </div>
  );
}
