import {z} from 'zod';

// Zod schemas mirror the prompt schema in prompts.ts. The CLI uses these
// to validate model output before writing MDX, so a malformed response
// fails loudly instead of producing a broken build.

export const KeyAspectSchema = z.object({
  title: z.string().min(1),
  intuition: z.string().default(''),
  body: z.string().min(1),
});

export const GistSchema = z.object({
  caption: z.string().default(''),
  body: z.string().min(1),
});

export const OpenProblemSchema = z.object({
  id: z.string().min(1),
  question: z.string().min(1),
  why: z.string().min(1),
});

export const ReferenceSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  authors: z.string().optional().default(''),
  year: z
    .union([z.number(), z.string()])
    .optional()
    .transform((v) => (v === undefined || v === '' ? undefined : String(v))),
  arxiv: z.string().nullable().optional(),
});

export const ExperimentStepSchema = z.object({
  id: z.string().min(1),
  text: z.string().min(1),
});

export const ExperimentSchema = z.object({
  title: z.string().min(1),
  hypothesis: z.string().default(''),
  steps: z.array(ExperimentStepSchema).min(1),
});

export const ComparisonCellSchema = z.object({
  value: z.string().min(1),
  confidence: z
    .enum(['high', 'medium', 'low', 'unknown'])
    .default('unknown'),
  note: z.string().optional(),
});

export const ComparisonRowSchema = z.object({
  dimension: z.string().min(1),
  description: z.string().optional().default(''),
  cells: z.record(z.string(), ComparisonCellSchema),
});

export const ModelComparisonSchema = z.object({
  models: z.array(z.string()).min(2),
  rows: z.array(ComparisonRowSchema).min(1),
  caption: z.string().optional().default(''),
});

export const TopicPayloadSchema = z.object({
  summary: z.string().min(1),
  takeaway: z.string().default(''),
  key_aspects: z.array(KeyAspectSchema).min(1),
  gists: z.array(GistSchema).default([]),
  open_problems: z.array(OpenProblemSchema).default([]),
  references: z.array(ReferenceSchema).default([]),
  experiment: ExperimentSchema.nullable().optional(),
  model_comparison: ModelComparisonSchema.nullable().optional(),
});

export type TopicPayload = z.infer<typeof TopicPayloadSchema>;
export type ModelComparison = z.infer<typeof ModelComparisonSchema>;

export const OutlineSchema = z.object({
  sections: z
    .array(z.object({id: z.string(), title: z.string()}))
    .min(1),
  is_comparison: z.boolean().default(false),
  rationale: z.string().default(''),
});

export type Outline = z.infer<typeof OutlineSchema>;

export const CritiqueSchema = z.object({
  issues: z
    .array(
      z.object({
        severity: z.enum(['major', 'minor']).default('minor'),
        note: z.string(),
      }),
    )
    .default([]),
  ok_to_publish: z.boolean().default(true),
});

export type Critique = z.infer<typeof CritiqueSchema>;

// Journey graph entry persisted in docs/_topics.json
export const TopicEntrySchema = z.object({
  id: z.string(),
  title: z.string(),
  path: z.string(),
  slug: z.string(),
  protocol: z.string(),
  teacher: z.string(),
  student: z.string(),
  generatedAt: z.string(),
  isComparison: z.boolean().default(false),
  parentId: z.string().nullable().optional(),
  position: z.number(),
});

export type TopicEntry = z.infer<typeof TopicEntrySchema>;

export const JourneyGraphSchema = z.object({
  topics: z.array(TopicEntrySchema).default([]),
});

export type JourneyGraph = z.infer<typeof JourneyGraphSchema>;
