// Two-model protocols. Each protocol takes a topic title and returns a
// validated TopicPayload. They differ in HOW they put the Teacher and
// Student together; they all produce the same shape so the MDX
// renderer stays a single code path.

import {call, extractJson, type FmConfig} from './fm-client.js';
import {
  TopicPayloadSchema,
  CritiqueSchema,
  OutlineSchema,
  type TopicPayload,
  type Outline,
} from './schema.js';
import {STUDENT_SYSTEM, TEACHER_SYSTEM, SCHEMA_HINT_TOPIC} from './prompts.js';

export type ProtocolName = 'outline-write-critique' | 'socratic' | 'debate';

export interface RunOpts {
  topicTitle: string;
  protocol: ProtocolName;
  // Optional curated list of topics already covered, so the FM avoids
  // duplication when filling out sections.
  priorTopics?: string[];
  // If true, prompt the model to produce a frontier-model comparison.
  comparison?: boolean;
  log?: (s: string) => void;
}

const noop = () => {
  /* no-op */
};

export async function runProtocol(
  cfg: FmConfig,
  opts: RunOpts,
): Promise<{payload: TopicPayload; outline: Outline}> {
  const log = opts.log ?? noop;
  switch (opts.protocol) {
    case 'outline-write-critique':
      return outlineWriteCritique(cfg, opts, log);
    case 'socratic':
      return socratic(cfg, opts, log);
    case 'debate':
      return debate(cfg, opts, log);
    default:
      throw new Error(`Unknown protocol: ${opts.protocol}`);
  }
}

// --- shared helpers -----------------------------------------------------

async function outlineFromTeacher(
  cfg: FmConfig,
  opts: RunOpts,
  log: (s: string) => void,
): Promise<Outline> {
  log(`Teacher: outlining "${opts.topicTitle}"...`);
  const priorBlock = (opts.priorTopics ?? []).length
    ? `Topics already covered (avoid duplicating these):\n- ${opts.priorTopics!.join(
        '\n- ',
      )}`
    : 'No prior topics yet.';

  const messages = [
    {role: 'system' as const, content: TEACHER_SYSTEM},
    {
      role: 'user' as const,
      content: `Outline a chapter titled "${opts.topicTitle}" for a working LLM researcher.\n\n${priorBlock}\n\nReturn strict JSON only:\n{\n  "sections": [ { "id": "kebab-case-id", "title": "string" } ],\n  "is_comparison": ${opts.comparison ? 'true' : 'false'},\n  "rationale": "one sentence on why this outline (not for the chapter)"\n}\n\nFor a normal topic, include 3-5 sections that build progressively (intuition -> formal -> caveats -> open problems).\nFor a comparison topic (is_comparison true), the outline should describe rows of a ModelComparison table, each section id being a dimension like "attention-variant" or "post-training-pipeline".`,
    },
  ];

  const res = await call(cfg, {
    endpoint: 'teacher',
    messages,
    jsonOnly: true,
  });
  const parsed = OutlineSchema.parse(extractJson(res.text));
  log(`Teacher: ${parsed.sections.length} sections.`);
  return parsed;
}

async function studentWritesChapter(
  cfg: FmConfig,
  opts: RunOpts,
  outline: Outline,
  feedback: string | null,
  log: (s: string) => void,
): Promise<TopicPayload> {
  log(
    feedback
      ? `Student: revising after critique...`
      : `Student: drafting chapter...`,
  );

  const outlineBlock = outline.sections
    .map((s, i) => `${i + 1}. ${s.title} (id: ${s.id})`)
    .join('\n');

  const userParts: string[] = [];
  userParts.push(`Topic: "${opts.topicTitle}"`);
  userParts.push(``);
  userParts.push(`Outline from the teacher:\n${outlineBlock}`);
  if (opts.comparison) {
    userParts.push(
      `\nThis is a FRONTIER MODEL COMPARISON chapter. Fill in "model_comparison" using the schema, and keep "key_aspects" short (one per row category at most).`,
    );
  }
  if (feedback) {
    userParts.push(``);
    userParts.push(`Teacher's critique to address:\n${feedback}`);
  }
  userParts.push(``);
  userParts.push(SCHEMA_HINT_TOPIC);
  userParts.push(``);
  userParts.push(`Return strict JSON only. No prose around it.`);

  const messages = [
    {role: 'system' as const, content: STUDENT_SYSTEM},
    {role: 'user' as const, content: userParts.join('\n')},
  ];

  const res = await call(cfg, {
    endpoint: 'student',
    messages,
    jsonOnly: true,
  });
  const parsed = TopicPayloadSchema.parse(extractJson(res.text));
  log(
    `Student: ${parsed.key_aspects.length} aspects, ${parsed.gists.length} gists, ${parsed.open_problems.length} open problems, ${parsed.references.length} refs.`,
  );
  return parsed;
}

async function teacherCritiques(
  cfg: FmConfig,
  opts: RunOpts,
  payload: TopicPayload,
  log: (s: string) => void,
): Promise<{ok: boolean; feedback: string}> {
  log(`Teacher: critiquing draft...`);
  const messages = [
    {role: 'system' as const, content: TEACHER_SYSTEM},
    {
      role: 'user' as const,
      content: `Critique the student's draft for the topic "${opts.topicTitle}". Be tough: where is the explanation hand-wavy, factually shaky, or missing a key open problem? Where would a reviewer push back?\n\nDraft (JSON):\n${JSON.stringify(payload).slice(0, 6000)}\n\nReturn strict JSON:\n{\n  "issues": [ { "severity": "major|minor", "note": "string" } ],\n  "ok_to_publish": true|false\n}\n\nSet ok_to_publish to true only if the draft has no major issues.`,
    },
  ];
  const res = await call(cfg, {
    endpoint: 'teacher',
    messages,
    jsonOnly: true,
  });
  const parsed = CritiqueSchema.parse(extractJson(res.text));
  const feedback = parsed.issues
    .map((i) => `- [${i.severity}] ${i.note}`)
    .join('\n');
  log(
    parsed.ok_to_publish
      ? `Teacher: OK to publish (${parsed.issues.length} minor issues).`
      : `Teacher: needs revision (${parsed.issues.length} issues).`,
  );
  return {ok: parsed.ok_to_publish, feedback};
}

// --- protocols ----------------------------------------------------------

async function outlineWriteCritique(
  cfg: FmConfig,
  opts: RunOpts,
  log: (s: string) => void,
): Promise<{payload: TopicPayload; outline: Outline}> {
  const outline = await outlineFromTeacher(cfg, opts, log);
  let payload = await studentWritesChapter(cfg, opts, outline, null, log);
  const {ok, feedback} = await teacherCritiques(cfg, opts, payload, log);
  if (!ok && feedback) {
    payload = await studentWritesChapter(cfg, opts, outline, feedback, log);
  }
  return {payload, outline};
}

async function socratic(
  cfg: FmConfig,
  opts: RunOpts,
  log: (s: string) => void,
): Promise<{payload: TopicPayload; outline: Outline}> {
  // Socratic: Teacher asks layered questions, Student answers, Teacher
  // refines. We materialise the Q&A pairs as the section bodies via
  // outline-write-critique with a different system instruction layered
  // on top, but keep the same final JSON shape for the renderer.
  const outline = await outlineFromTeacher(cfg, opts, log);
  log(`Teacher: turning sections into Socratic prompts...`);
  // Reuse the standard student writer; the difference is purely
  // stylistic, captured in the schema hint preamble. We append guidance.
  const styledOpts: RunOpts = {
    ...opts,
    topicTitle: `${opts.topicTitle} (Socratic style: each key_aspect.body must open with a sharp question and then answer it).`,
  };
  const payload = await studentWritesChapter(
    cfg,
    styledOpts,
    outline,
    null,
    log,
  );
  return {payload, outline};
}

async function debate(
  cfg: FmConfig,
  opts: RunOpts,
  log: (s: string) => void,
): Promise<{payload: TopicPayload; outline: Outline}> {
  // Debate: produce two short drafts (pro / con), then synthesise. We
  // implement it as Student-draft -> Teacher-counter -> Student-synth.
  const outline = await outlineFromTeacher(cfg, opts, log);

  const proOpts: RunOpts = {
    ...opts,
    topicTitle: `${opts.topicTitle} (PRO stance: argue the strongest case FOR the conventional view).`,
  };
  const conOpts: RunOpts = {
    ...opts,
    topicTitle: `${opts.topicTitle} (CON stance: argue the strongest case AGAINST the conventional view).`,
  };

  log(`Student: pro draft...`);
  const pro = await studentWritesChapter(cfg, proOpts, outline, null, log);
  log(`Teacher: con draft...`);
  const con = await studentWritesChapter(cfg, conOpts, outline, null, log);

  log(`Teacher: synthesising debate...`);
  const synthMessages = [
    {role: 'system' as const, content: TEACHER_SYSTEM},
    {
      role: 'user' as const,
      content: `Synthesise these two debate drafts into one balanced chapter for the topic "${opts.topicTitle}". Surface where they agree and where they genuinely disagree; do not paper over disagreement.\n\nPRO:\n${JSON.stringify(pro).slice(0, 4000)}\n\nCON:\n${JSON.stringify(con).slice(0, 4000)}\n\n${SCHEMA_HINT_TOPIC}\n\nReturn strict JSON only matching the topic schema. Add a key_aspect titled "Where the field actually disagrees" listing the genuine cruxes.`,
    },
  ];
  const res = await call(cfg, {
    endpoint: 'teacher',
    messages: synthMessages,
    jsonOnly: true,
  });
  const payload = TopicPayloadSchema.parse(extractJson(res.text));
  return {payload, outline};
}
