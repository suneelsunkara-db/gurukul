// Thin client over Databricks model-serving "chat/completions" endpoints.
// Two endpoints are configured: a Teacher (typically larger, cooler) and
// a Student (typically smaller/faster, warmer). The orchestrator chains
// calls between them.
//
// Auth: we do NOT keep client_id/client_secret in .env. Instead we
// shell out to `databricks auth token` (the unified-auth CLI), which
// reads the SP credentials from ~/.databrickscfg or the OS keyring and
// returns a short-lived OAuth access token. We cache it in-process.
//
// We deliberately do NOT depend on any vendor SDK. Databricks foundation
// model endpoints accept the OpenAI-style chat schema at:
//   {DATABRICKS_HOST}/serving-endpoints/{name}/invocations

import 'dotenv/config';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

const execFileAsync = promisify(execFile);

export type Role = 'system' | 'user' | 'assistant';

export interface ChatMessage {
  role: Role;
  content: string;
}

export interface CallOpts {
  endpoint: 'teacher' | 'student';
  messages: ChatMessage[];
  temperature?: number;
  maxTokens?: number;
  // When true, instruct the model to respond as JSON only. The caller
  // is still responsible for parsing & validating.
  jsonOnly?: boolean;
}

export interface CallResult {
  text: string;
  // Best-effort usage info; some endpoints don't return it.
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
  raw: unknown;
}

export interface FmConfig {
  host: string;
  // Databricks CLI binary and optional profile, used to mint OAuth
  // tokens via `databricks auth token`.
  cli: string;
  profile: string | null;
  teacherEndpoint: string;
  studentEndpoint: string;
  teacherTemperature: number;
  studentTemperature: number;
  maxTokens: number;
  dryRun: boolean;
}

export function loadConfig(): FmConfig {
  const host = (process.env.DATABRICKS_HOST ?? '').replace(/\/+$/, '');
  const cli = process.env.DATABRICKS_CLI?.trim() || 'databricks';
  const profile = process.env.DATABRICKS_CONFIG_PROFILE?.trim() || null;
  const teacherEndpoint =
    process.env.DATABRICKS_TEACHER_ENDPOINT ??
    'databricks-meta-llama-3-3-70b-instruct';
  const studentEndpoint =
    process.env.DATABRICKS_STUDENT_ENDPOINT ??
    'databricks-meta-llama-3-1-8b-instruct';
  const teacherTemperature = num(
    process.env.FM_TEMPERATURE_TEACHER,
    0.2,
  );
  const studentTemperature = num(
    process.env.FM_TEMPERATURE_STUDENT,
    0.4,
  );
  const maxTokens = num(process.env.FM_MAX_TOKENS, 2000);
  const dryRun = process.env.JOURNEY_DRY_RUN === '1';

  return {
    host,
    cli,
    profile,
    teacherEndpoint,
    studentEndpoint,
    teacherTemperature,
    studentTemperature,
    maxTokens,
    dryRun,
  };
}

function num(v: string | undefined, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export function assertConfigured(cfg: FmConfig): void {
  if (cfg.dryRun) return;
  if (!cfg.host) {
    throw new Error(
      `Missing DATABRICKS_HOST. Copy .env.example to .env and fill in your workspace URL, or use JOURNEY_DRY_RUN=1 to test the flow.`,
    );
  }
}

// --- Token minting via `databricks auth token` -------------------------
// We cache the token in-process and refresh when it's within 60s of
// expiry. The CLI itself caches longer-lived OAuth state in the OS
// keyring, so each fresh process just does one fast shellout.

interface TokenEntry {
  accessToken: string;
  expiresAt: number; // epoch ms
}

let tokenCache: TokenEntry | null = null;

async function getAccessToken(cfg: FmConfig): Promise<string> {
  const now = Date.now();
  if (tokenCache && tokenCache.expiresAt > now + 60_000) {
    return tokenCache.accessToken;
  }
  const args = ['auth', 'token', '--host', cfg.host];
  if (cfg.profile) {
    args.push('--profile', cfg.profile);
  }
  let stdout: string;
  try {
    const res = await execFileAsync(cfg.cli, args, {
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
    });
    stdout = res.stdout;
  } catch (err) {
    const e = err as NodeJS.ErrnoException & {stderr?: string};
    if (e.code === 'ENOENT') {
      throw new Error(
        `Could not find the \`${cfg.cli}\` CLI on PATH. Install it with \`brew install databricks\` (or \`pip install databricks-cli\`) and run \`${cfg.cli} auth login --host ${cfg.host}\`.`,
      );
    }
    throw new Error(
      `\`${cfg.cli} auth token\` failed: ${e.stderr?.toString().trim() || e.message}. ` +
        `Have you run \`${cfg.cli} auth login --host ${cfg.host}\` and bound a service principal?`,
    );
  }
  let parsed: {access_token?: string; expiry?: string};
  try {
    parsed = JSON.parse(stdout);
  } catch {
    throw new Error(
      `Unexpected output from \`databricks auth token\`: ${stdout.slice(0, 200)}`,
    );
  }
  if (!parsed.access_token) {
    throw new Error(
      `\`databricks auth token\` returned no access_token. Raw: ${stdout.slice(0, 200)}`,
    );
  }
  const expiresAt = parsed.expiry
    ? Date.parse(parsed.expiry)
    : now + 50 * 60 * 1000; // assume 50 min if expiry missing
  tokenCache = {accessToken: parsed.access_token, expiresAt};
  return parsed.access_token;
}

/**
 * One call. Retries network errors and 5xx three times with backoff.
 * Returns the assistant's text content; the caller decides how to parse
 * it (markdown vs JSON).
 */
export async function call(
  cfg: FmConfig,
  opts: CallOpts,
): Promise<CallResult> {
  if (cfg.dryRun) {
    return stub(opts);
  }

  const endpointName =
    opts.endpoint === 'teacher' ? cfg.teacherEndpoint : cfg.studentEndpoint;
  const url = `${cfg.host}/serving-endpoints/${encodeURIComponent(
    endpointName,
  )}/invocations`;

  const body: Record<string, unknown> = {
    messages: opts.messages,
    temperature:
      opts.temperature ??
      (opts.endpoint === 'teacher'
        ? cfg.teacherTemperature
        : cfg.studentTemperature),
    max_tokens: opts.maxTokens ?? cfg.maxTokens,
  };
  if (opts.jsonOnly) {
    body.response_format = {type: 'json_object'};
  }

  let lastErr: unknown = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const accessToken = await getAccessToken(cfg);
      const res = await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text();
        // 401: token may have expired in flight. Drop the cache and
        // retry once. 5xx: transient — retry with backoff.
        if (res.status === 401 && attempt < 3) {
          tokenCache = null;
          continue;
        }
        if (res.status >= 500 && attempt < 3) {
          await sleep(500 * attempt);
          continue;
        }
        throw new Error(
          `Databricks ${endpointName} returned ${res.status}: ${text.slice(0, 400)}`,
        );
      }
      const json = (await res.json()) as {
        choices?: Array<{
          message?: {content?: string};
          text?: string;
        }>;
        usage?: CallResult['usage'];
      };
      const text =
        json.choices?.[0]?.message?.content ??
        json.choices?.[0]?.text ??
        '';
      if (!text) {
        throw new Error(
          `Empty completion from ${endpointName}. Raw: ${JSON.stringify(json).slice(0, 400)}`,
        );
      }
      return {text, usage: json.usage, raw: json};
    } catch (err) {
      lastErr = err;
      if (attempt < 3) await sleep(500 * attempt);
    }
  }
  throw lastErr instanceof Error
    ? lastErr
    : new Error(`Databricks call failed: ${String(lastErr)}`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// --- Dry-run stub -------------------------------------------------------
// Returns a deterministic plausible response so the whole pipeline can be
// exercised end-to-end without burning Databricks tokens. The stub is
// intentionally well-formed JSON when jsonOnly is set, otherwise plain
// prose. It is NOT meant to be educational content.

function stub(opts: CallOpts): CallResult {
  const last = opts.messages[opts.messages.length - 1]?.content ?? '';
  if (opts.jsonOnly) {
    const text = JSON.stringify(stubJson(opts.endpoint, last));
    return {text, raw: {stub: true}};
  }
  const text = `[stub:${opts.endpoint}] Acknowledged: ${last.slice(0, 120)}...`;
  return {text, raw: {stub: true}};
}

function stubJson(endpoint: 'teacher' | 'student', prompt: string): unknown {
  // Classify by the *requested schema shape* embedded in the prompt,
  // not by fuzzy keywords (the student's chapter prompt also contains
  // the word "outline" because it references the teacher's outline).
  const wantsOutline =
    /"sections"\s*:/.test(prompt) && /"is_comparison"/.test(prompt);
  const wantsCritique = /"ok_to_publish"/.test(prompt) || /"ok"\s*:\s*true/.test(prompt);
  const wantsSuggestions = /"suggestions"\s*:/.test(prompt);

  if (wantsSuggestions) {
    return {
      suggestions: [
        {
          title: 'Foundations of language modeling',
          is_comparison: false,
          rationale: '[stub] start at the beginning',
        },
        {
          title: 'Transformer architecture: attention is all you need',
          is_comparison: false,
          rationale: '[stub] the building block',
        },
        {
          title: 'Frontier model comparison: GPT, Claude, Kimi, Qwen, DeepSeek',
          is_comparison: true,
          rationale: '[stub] the payoff chapter',
        },
      ],
    };
  }
  if (wantsOutline) {
    return {
      sections: [
        {id: 'intuition', title: 'Intuition'},
        {id: 'formalism', title: 'Formalism'},
        {id: 'pitfalls', title: 'Pitfalls and limits'},
      ],
      is_comparison: /is_comparison.*?true/i.test(prompt),
      rationale: '[stub] progressive structure',
    };
  }
  if (wantsCritique) {
    return {
      issues: [
        {severity: 'minor', note: 'Tighten the intuition paragraph.'},
      ],
      ok_to_publish: true,
      ok: true,
    };
  }
  return {
    summary: '[stub] One-paragraph TL;DR.',
    takeaway: '[stub] Bottom line.',
    key_aspects: [
      {
        title: 'Stub aspect',
        intuition: 'A short intuition.',
        body: 'A paragraph of body text.',
      },
    ],
    gists: [{caption: 'Stub gist caption', body: '`code()`'}],
    experiment: {
      title: 'Stub experiment',
      hypothesis: 'Something interesting will happen.',
      steps: [
        {id: 's1', text: 'Do the first thing.'},
        {id: 's2', text: 'Measure something.'},
      ],
    },
    open_problems: [
      {
        id: 'op1',
        question: 'What is unknown?',
        why: 'Because empirical X is unexplained.',
      },
    ],
    references: [
      {
        id: 'ref1',
        title: 'Stub paper',
        authors: 'Stub et al.',
        year: 2024,
        arxiv: '0000.00000',
      },
    ],
    endpoint,
  };
}

/**
 * Try hard to extract a JSON object from a model response. Models often
 * wrap JSON in ``` fences or add a chatty preamble; this strips both.
 */
export function extractJson(text: string): unknown {
  // Strip Markdown code fences if present.
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fence ? fence[1] : text;
  // Find the first { ... } block that parses.
  const start = candidate.indexOf('{');
  if (start === -1) {
    throw new Error('No JSON object found in model output.');
  }
  // Try progressively larger suffixes until JSON.parse succeeds.
  for (let end = candidate.length; end > start; end--) {
    const slice = candidate.slice(start, end);
    if (slice[slice.length - 1] !== '}') continue;
    try {
      return JSON.parse(slice);
    } catch {
      /* keep shrinking */
    }
  }
  throw new Error('Could not parse JSON from model output.');
}
