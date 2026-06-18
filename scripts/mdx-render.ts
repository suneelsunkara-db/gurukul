// Deterministic MDX renderer. The Student model's job is to fill in a
// strict JSON shape; THIS file is the only place that knows how to turn
// that JSON into MDX. Keeping the conversion deterministic is the
// MDX-safety pass: the model never emits raw JSX, so it can't break the
// build with stray braces or malformed tags.

import type {TopicPayload, TopicEntry, ModelComparison} from './schema.js';

/** Escape characters that have meaning in MDX prose. */
function safeText(s: string): string {
  if (!s) return '';
  return s
    // Curly braces are JSX expression delimiters in MDX.
    .replace(/\{/g, '\\{')
    .replace(/\}/g, '\\}')
    // < that doesn't start a known HTML/JSX tag must be escaped.
    .replace(/</g, '\\<');
}

/**
 * Body fields can contain markdown code fences which we want to keep
 * intact. Strategy: split on triple-backtick blocks, escape only the
 * non-fenced segments. Inside a fence we leave content untouched.
 */
function safeBody(s: string): string {
  if (!s) return '';
  const parts = s.split(/(```[\s\S]*?```)/g);
  return parts
    .map((p) => (p.startsWith('```') ? p : safeText(p)))
    .join('');
}

function jsonAttr(v: unknown): string {
  return JSON.stringify(v).replace(/</g, '\\u003c');
}

export interface RenderInput {
  entry: TopicEntry;
  payload: TopicPayload;
}

export function renderMdx({entry, payload}: RenderInput): string {
  const parts: string[] = [];

  parts.push(`---`);
  parts.push(`id: ${entry.id}`);
  parts.push(`title: ${jsonAttr(entry.title)}`);
  parts.push(`slug: /${entry.slug}`);
  parts.push(`sidebar_position: ${entry.position}`);
  parts.push(`description: ${jsonAttr(payload.summary.slice(0, 160))}`);
  parts.push(`---`);
  parts.push('');
  parts.push(`# ${safeText(entry.title)}`);
  parts.push('');
  parts.push(
    `<TopicHeader protocol="${entry.protocol}" teacher="${entry.teacher}" student="${entry.student}" generatedAt="${entry.generatedAt}" />`,
  );
  parts.push('');
  parts.push(
    `<Summary takeaway={${jsonAttr(payload.takeaway || '')}}>`,
  );
  parts.push('');
  parts.push(safeText(payload.summary));
  parts.push('');
  parts.push(`</Summary>`);
  parts.push('');

  for (const aspect of payload.key_aspects) {
    parts.push(
      `<KeyAspect title=${jsonAttr(aspect.title)} intuition=${jsonAttr(
        aspect.intuition,
      )}>`,
    );
    parts.push('');
    parts.push(safeBody(aspect.body));
    parts.push('');
    parts.push(`</KeyAspect>`);
    parts.push('');
  }

  for (const gist of payload.gists) {
    parts.push(`<Gist caption=${jsonAttr(gist.caption)}>`);
    parts.push('');
    // Body of a gist is expected to already be a markdown code fence.
    parts.push(gist.body);
    parts.push('');
    parts.push(`</Gist>`);
    parts.push('');
  }

  if (payload.model_comparison) {
    parts.push(renderComparison(payload.model_comparison));
    parts.push('');
  }

  if (payload.open_problems.length > 0) {
    parts.push(
      `<OpenProblems topicId="${entry.id}" items={${jsonAttr(
        payload.open_problems,
      )}} />`,
    );
    parts.push('');
  }

  if (payload.references.length > 0) {
    parts.push(
      `<References topicId="${entry.id}" items={${jsonAttr(
        payload.references,
      )}} />`,
    );
    parts.push('');
  }

  if (payload.experiment) {
    parts.push(
      `<Experiment topicId="${entry.id}" title=${jsonAttr(
        payload.experiment.title,
      )} hypothesis=${jsonAttr(payload.experiment.hypothesis)} steps={${jsonAttr(
        payload.experiment.steps,
      )}} />`,
    );
    parts.push('');
  }

  parts.push(`<Critique topicId="${entry.id}" />`);
  parts.push('');
  parts.push(`<ResearchSeed topicId="${entry.id}" />`);
  parts.push('');
  parts.push(`<ConfidenceTracker topicId="${entry.id}" />`);
  parts.push('');

  return parts.join('\n');
}

function renderComparison(c: ModelComparison): string {
  return `<ModelComparison models={${jsonAttr(c.models)}} rows={${jsonAttr(
    c.rows,
  )}} caption=${jsonAttr(c.caption || '')} />`;
}
