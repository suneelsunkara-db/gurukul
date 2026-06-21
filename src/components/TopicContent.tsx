import React, {useState} from 'react';
import Summary from './Summary';
import KeyAspect from './KeyAspect';
import Gist from './Gist';
import OpenProblems from './OpenProblems';
import References from './References';
import Experiment from './Experiment';
import ModelComparison from './ModelComparison';
import ChallengePanel from './ChallengePanel';
import ContentQuality from './ContentQuality';
import type {TreeNode} from './TopicTree';

interface TopicPayload {
  summary: string;
  takeaway: string;
  eli5?: string;
  key_aspects: Array<{title: string; intuition: string; body: string}>;
  gists: Array<{caption: string; body: string}>;
  open_problems: Array<{id: string; question: string; why: string}>;
  references: Array<{
    id: string;
    title: string;
    authors?: string;
    year?: number | string;
    arxiv?: string | null;
  }>;
  experiment?: {
    title: string;
    hypothesis: string;
    steps: Array<{id: string; text: string}>;
  } | null;
  model_comparison?: {
    models: string[];
    rows: Array<{
      dimension: string;
      description?: string;
      cells: Record<string, {value: string; confidence?: string; note?: string}>;
    }>;
    caption?: string;
  } | null;
  connections?: string[];
}

export type ContentLevel = 'beginner' | 'intermediate' | 'advanced';

interface Props {
  node: TreeNode;
  payload: TopicPayload;
  onExploreDeeper: (direction: string) => void;
  level: ContentLevel;
  connectedTopics?: Array<{id: string; title: string}>;
  onSelectTopic?: (id: string) => void;
  onUnderstandingChange?: (topicId: string, level: string) => void;
  onRegenerate?: () => void;
}

export default function TopicContent({
  node,
  payload,
  onExploreDeeper,
  level,
  connectedTopics = [],
  onSelectTopic,
  onUnderstandingChange,
  onRegenerate,
}: Props) {
  const [branchInput, setBranchInput] = useState('');
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    aspects: true,
    gists: level === 'advanced',
    comparison: true,
    problems: level !== 'beginner',
    references: level === 'advanced',
    experiment: level === 'advanced',
  });

  const toggleSection = (key: string) =>
    setExpandedSections((prev) => ({...prev, [key]: !prev[key]}));

  return (
    <article className="gk-content">
      <h1>{node.title}</h1>

      {/* Level indicator */}
      <div className="gk-content__level-hint">
        {level === 'beginner' && 'Showing simplified explanations'}
        {level === 'intermediate' && 'Showing standard explanations'}
        {level === 'advanced' && 'Showing full technical detail'}
      </div>

      {/* ELI5 for beginner mode */}
      {level === 'beginner' && payload.eli5 && (
        <div className="gk-card gk-eli5">
          <div className="gk-card__label">In plain English</div>
          <p style={{margin: 0, fontSize: '1.05rem', lineHeight: 1.6}}>{payload.eli5}</p>
        </div>
      )}

      {/* Summary (always shown) */}
      <Summary takeaway={payload.takeaway}>{payload.summary}</Summary>

      {/* Content quality analysis */}
      <ContentQuality topicId={node.id} onRegenerate={onRegenerate} />

      {/* Connected topics */}
      {connectedTopics.length > 0 && (
        <div className="gk-connections">
          <span className="gk-connections__label">Connected to:</span>
          {connectedTopics.map((t) => (
            <button
              key={t.id}
              type="button"
              className="gk-pill gk-pill--clickable"
              onClick={() => onSelectTopic?.(t.id)}
            >
              {t.title}
            </button>
          ))}
        </div>
      )}

      {/* Key Aspects */}
      <CollapsibleSection
        title={`Key Aspects (${payload.key_aspects.length})`}
        sectionKey="aspects"
        expanded={expandedSections.aspects}
        onToggle={toggleSection}
      >
        {payload.key_aspects.map((a, i) => (
          <KeyAspect key={i} title={a.title} intuition={a.intuition}>
            {level === 'beginner'
              ? renderBeginner(a.body)
              : renderBody(a.body)}
          </KeyAspect>
        ))}
      </CollapsibleSection>

      {/* Code Gists */}
      {payload.gists.length > 0 && (
        <CollapsibleSection
          title={`Code Gists (${payload.gists.length})`}
          sectionKey="gists"
          expanded={expandedSections.gists}
          onToggle={toggleSection}
        >
          {payload.gists.map((g, i) => (
            <Gist key={i} caption={g.caption}>
              <pre><code>{stripFence(g.body)}</code></pre>
            </Gist>
          ))}
        </CollapsibleSection>
      )}

      {/* Model Comparison */}
      {payload.model_comparison && (
        <CollapsibleSection
          title="Model Comparison"
          sectionKey="comparison"
          expanded={expandedSections.comparison}
          onToggle={toggleSection}
        >
          <ModelComparison
            models={payload.model_comparison.models}
            rows={payload.model_comparison.rows as any}
            caption={payload.model_comparison.caption}
          />
        </CollapsibleSection>
      )}

      {/* Open Problems */}
      {payload.open_problems.length > 0 && (
        <CollapsibleSection
          title={`Open Problems (${payload.open_problems.length})`}
          sectionKey="problems"
          expanded={expandedSections.problems}
          onToggle={toggleSection}
        >
          <OpenProblems topicId={node.id} items={payload.open_problems} />
        </CollapsibleSection>
      )}

      {/* References */}
      {payload.references.length > 0 && (
        <CollapsibleSection
          title={`References (${payload.references.length})`}
          sectionKey="references"
          expanded={expandedSections.references}
          onToggle={toggleSection}
        >
          <References topicId={node.id} items={payload.references as any} />
        </CollapsibleSection>
      )}

      {/* Experiment */}
      {payload.experiment && (
        <CollapsibleSection
          title="Try it yourself"
          sectionKey="experiment"
          expanded={expandedSections.experiment}
          onToggle={toggleSection}
        >
          <Experiment
            topicId={node.id}
            title={payload.experiment.title}
            hypothesis={payload.experiment.hypothesis}
            steps={payload.experiment.steps}
          />
        </CollapsibleSection>
      )}

      {/* Branch / Explore deeper */}
      <section className="gk-card">
        <div className="gk-card__label">Explore deeper</div>
        {payload.open_problems.length > 0 && (
          <div className="gk-branch-suggestions">
            <span style={{fontSize: '0.8rem', color: 'var(--gk-muted)'}}>
              Quick branches from open problems:
            </span>
            <div className="gk-branch-chips">
              {payload.open_problems.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className="gk-pill gk-pill--clickable"
                  onClick={() => onExploreDeeper(p.question)}
                >
                  {p.question.length > 60
                    ? p.question.slice(0, 57) + '...'
                    : p.question}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="gk-row" style={{marginTop: '0.6rem'}}>
          <input
            type="text"
            className="gk-textarea"
            style={{minHeight: 0, height: '2.2rem', flex: 1}}
            placeholder="e.g. 'How does GQA differ from MQA at scale?'"
            value={branchInput}
            onChange={(e) => setBranchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && branchInput.trim()) {
                onExploreDeeper(branchInput.trim());
                setBranchInput('');
              }
            }}
          />
          <button
            type="button"
            className="gk-button"
            disabled={!branchInput.trim()}
            onClick={() => {
              if (branchInput.trim()) {
                onExploreDeeper(branchInput.trim());
                setBranchInput('');
              }
            }}
          >
            Branch
          </button>
        </div>
      </section>

      {/* Socratic assessment */}
      <ChallengePanel
        topicId={node.id}
        topicTitle={node.title}
        onUnderstandingChange={onUnderstandingChange}
      />
    </article>
  );
}

// ── Collapsible section wrapper ─────────────────────────────────

function CollapsibleSection({
  title,
  sectionKey,
  expanded,
  onToggle,
  children,
}: {
  title: string;
  sectionKey: string;
  expanded: boolean;
  onToggle: (key: string) => void;
  children: React.ReactNode;
}) {
  return (
    <section className="gk-collapsible">
      <button
        type="button"
        className="gk-collapsible__header"
        onClick={() => onToggle(sectionKey)}
      >
        <span className="gk-collapsible__arrow">{expanded ? '▾' : '▸'}</span>
        <span className="gk-collapsible__title">{title}</span>
      </button>
      {expanded && (
        <div className="gk-collapsible__body">{children}</div>
      )}
    </section>
  );
}

// ── Text rendering helpers ──────────────────────────────────────

function renderBeginner(body: string): React.ReactNode {
  // For beginner mode, show only the first paragraph
  const firstPara = body.split(/\n\n/)[0] || body;
  return (
    <span>
      {firstPara.split('\n').map((line, j) => (
        <React.Fragment key={j}>
          {j > 0 && <br />}
          {line}
        </React.Fragment>
      ))}
    </span>
  );
}

function renderBody(body: string): React.ReactNode {
  const parts = body.split(/(```[\s\S]*?```)/g);
  return parts.map((p, i) => {
    if (p.startsWith('```')) {
      return (
        <pre key={i}><code>{stripFence(p)}</code></pre>
      );
    }
    return (
      <span key={i}>
        {p.split('\n').map((line, j) => (
          <React.Fragment key={j}>
            {j > 0 && <br />}
            {line}
          </React.Fragment>
        ))}
      </span>
    );
  });
}

function stripFence(s: string): string {
  return s.replace(/^```\w*\n?/, '').replace(/\n?```$/, '');
}
