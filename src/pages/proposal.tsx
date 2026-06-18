import React, {useEffect, useMemo, useState} from 'react';
import Layout from '@theme/Layout';
import {readAll} from '@site/src/lib/storage';
import topicsData from '@site/docs/_topics.json';

const topicsJson: Array<{id: string; title: string; path: string}> =
  (topicsData as {topics?: Array<{id: string; title: string; path: string}>})
    .topics ?? [];

type Seed = {id: string; text: string};

/**
 * Bundles the reader's seeds, pushback notes, and starred open problems
 * into a JSON payload that can be pasted into the journey CLI's
 * `npm run outline` command. We deliberately keep this purely a
 * client-side aggregator — the actual proposal-outline generation
 * happens offline against your Databricks endpoints, so we never have
 * to ship a backend with the static site.
 */
export default function ProposalPage() {
  const [seeds, setSeeds] = useState<Record<string, Seed[]>>({});
  const [critiques, setCritiques] = useState<Record<string, string>>({});
  const [starred, setStarred] = useState<
    Record<string, Record<string, boolean>>
  >({});
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [angle, setAngle] = useState<string>('');

  useEffect(() => {
    const raw = readAll();
    const s: Record<string, Seed[]> = {};
    const c: Record<string, string> = {};
    const st: Record<string, Record<string, boolean>> = {};
    for (const [k, v] of Object.entries(raw)) {
      const m = k.match(/^topic:([^:]+):(.+)$/);
      if (!m) continue;
      const [, topicId, field] = m;
      if (field === 'seeds' && Array.isArray(v)) s[topicId] = v as Seed[];
      else if (field === 'critique' && typeof v === 'string')
        c[topicId] = v;
      else if (field === 'openProblems' && v && typeof v === 'object')
        st[topicId] = v as Record<string, boolean>;
    }
    setSeeds(s);
    setCritiques(c);
    setStarred(st);
  }, []);

  const allSeeds = useMemo(
    () =>
      Object.entries(seeds).flatMap(([topicId, list]) =>
        list.map((seed) => ({topicId, ...seed})),
      ),
    [seeds],
  );

  const payload = useMemo(() => {
    return JSON.stringify(
      {
        angle: angle.trim() || null,
        topics: topicsJson.map((t) => t.title),
        picked_seeds: allSeeds.filter((s) => picked[s.id]),
        pushback: Object.fromEntries(
          Object.entries(critiques).filter(([, v]) => v?.trim()),
        ),
        starred_open_problems: Object.fromEntries(
          Object.entries(starred).map(([topicId, map]) => [
            topicId,
            Object.entries(map)
              .filter(([, on]) => on)
              .map(([id]) => id),
          ]),
        ),
      },
      null,
      2,
    );
  }, [angle, allSeeds, picked, critiques, starred]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(payload);
      alert('Copied. Now run: npm run outline   and paste when prompted.');
    } catch {
      alert(
        'Copy failed. Select the JSON manually and pipe it into npm run outline.',
      );
    }
  };

  return (
    <Layout
      title="Proposal Outline"
      description="From your journey notes to a research proposal outline"
    >
      <main className="container margin-vert--lg">
        <h1>Proposal outline export</h1>
        <p style={{color: 'var(--gk-muted)'}}>
          Pick the research seeds you want to develop. The exported JSON,
          combined with the topics you've studied, your pushback notes,
          and your starred open problems, becomes the prompt for the
          offline outline generator (<code>npm run outline</code>). The
          Teacher model drafts a NeurIPS-style outline; the Student model
          critiques and tightens it. Output lands in{' '}
          <code>proposals/</code>.
        </p>

        <h2>1. Sharpen the angle (optional)</h2>
        <textarea
          className="gk-textarea"
          value={angle}
          placeholder="e.g. 'Sample-efficient post-training for small MoE models' or 'Reasoning RL without verifier hacking'"
          onChange={(e) => setAngle(e.target.value)}
        />

        <h2 style={{marginTop: '1.5rem'}}>2. Pick seeds</h2>
        {allSeeds.length === 0 ? (
          <p style={{color: 'var(--gk-muted)'}}>
            You haven't captured any seeds yet. Open a topic and use the{' '}
            <em>Research seed</em> widget.
          </p>
        ) : (
          <ul style={{listStyle: 'none', paddingLeft: 0}}>
            {allSeeds.map((s) => (
              <li key={s.id} className="gk-checkbox-row">
                <input
                  type="checkbox"
                  checked={!!picked[s.id]}
                  onChange={(e) =>
                    setPicked((prev) => ({...prev, [s.id]: e.target.checked}))
                  }
                />
                <span>
                  <strong>{s.topicId}:</strong> {s.text}
                </span>
              </li>
            ))}
          </ul>
        )}

        <h2 style={{marginTop: '1.5rem'}}>3. Copy your payload</h2>
        <pre
          style={{
            maxHeight: 320,
            overflow: 'auto',
            padding: '0.8rem',
            border: '1px solid var(--gk-card-border)',
            borderRadius: 8,
          }}
        >
          {payload}
        </pre>
        <button type="button" className="gk-button" onClick={copy}>
          Copy JSON
        </button>
        <p style={{marginTop: '0.8rem', color: 'var(--gk-muted)'}}>
          Then in your terminal: <code>npm run outline</code> and paste
          when prompted.
        </p>
      </main>
    </Layout>
  );
}
