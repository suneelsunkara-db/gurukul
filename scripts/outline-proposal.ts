// Reads the JSON payload that the /proposal page exported (pasted via
// stdin) and turns it into a research proposal outline. Teacher drafts,
// Student critiques, Teacher revises. Output is written to
// proposals/YYYY-MM-DD-<slug>.md so you can iterate in your editor.

import 'dotenv/config';
import fs from 'node:fs';
import path from 'node:path';
import prompts from 'prompts';
import {
  loadConfig,
  assertConfigured,
  call,
  extractJson,
} from './fm-client.js';
import {TEACHER_SYSTEM, STUDENT_SYSTEM} from './prompts.js';

interface ProposalPayload {
  angle?: string | null;
  topics?: string[];
  picked_seeds?: Array<{id: string; text: string; topicId?: string}>;
  pushback?: Record<string, string>;
  starred_open_problems?: Record<string, string[]>;
}

async function readStdinJson(): Promise<ProposalPayload | null> {
  if (process.stdin.isTTY) return null;
  return new Promise((resolve, reject) => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => (buf += c));
    process.stdin.on('end', () => {
      try {
        resolve(JSON.parse(buf) as ProposalPayload);
      } catch (e) {
        reject(e);
      }
    });
    process.stdin.on('error', reject);
  });
}

async function main() {
  console.log('Gurukul · proposal outline generator');
  console.log('------------------------------------');

  const cfg = loadConfig();
  try {
    assertConfigured(cfg);
  } catch (e) {
    console.error(`✗ ${(e as Error).message}`);
    process.exit(1);
  }

  let payload = await readStdinJson();
  if (!payload) {
    console.log(
      'Tip: easiest is to pipe the JSON in:  pbpaste | npm run outline',
    );
    const {pasted} = await prompts({
      type: 'text',
      name: 'pasted',
      message: 'Paste the JSON exported from /proposal (single line ok):',
    });
    try {
      payload = JSON.parse(pasted) as ProposalPayload;
    } catch (e) {
      console.error('✗ Could not parse JSON:', (e as Error).message);
      process.exit(1);
    }
  }

  const angle = payload!.angle?.trim() || '(no specific angle provided)';
  const topics = payload!.topics ?? [];
  const seeds = payload!.picked_seeds ?? [];
  const pushback = payload!.pushback ?? {};

  console.log(`Angle: ${angle}`);
  console.log(`Topics in journey: ${topics.length}`);
  console.log(`Seeds picked: ${seeds.length}`);
  console.log(`Pushback notes: ${Object.keys(pushback).length}`);

  const context = [
    `Reader's angle: ${angle}`,
    ``,
    `Topics studied so far:`,
    topics.length
      ? topics.map((t) => `  - ${t}`).join('\n')
      : '  (none)',
    ``,
    `Research seeds the reader wants to develop:`,
    seeds.length
      ? seeds
          .map(
            (s) =>
              `  - (${s.topicId ?? '?'}) ${s.text.replace(/\n+/g, ' ')}`,
          )
          .join('\n')
      : '  (none)',
    ``,
    `Where the reader pushed back on the FM-generated content:`,
    Object.keys(pushback).length
      ? Object.entries(pushback)
          .map(([t, v]) => `  - (${t}) ${v.replace(/\n+/g, ' ')}`)
          .join('\n')
      : '  (none)',
  ].join('\n');

  console.log('\nTeacher: drafting outline...');
  const draftRes = await call(cfg, {
    endpoint: 'teacher',
    messages: [
      {role: 'system', content: TEACHER_SYSTEM},
      {
        role: 'user',
        content: `Draft a NeurIPS-style research proposal outline grounded in the reader's journey below. Be specific and falsifiable. Do not write the paper; write the outline a reviewer would expect to see.\n\n${context}\n\nReturn a Markdown outline with these top-level sections (each with bullets):\n1. Title (working)\n2. One-paragraph abstract\n3. Problem statement and why now\n4. Concrete research questions (Q1, Q2, ...)\n5. Hypotheses (H1 matching Q1, ...)\n6. Method sketch (architecture, training, eval)\n7. Experiments and ablations\n8. Expected results and what would falsify them\n9. Risks and limitations (be honest, no fluff)\n10. Related work (paper IDs you'd cite; mark uncertain ones)\n11. Open questions for the advisor`,
      },
    ],
  });
  let outline = draftRes.text;

  console.log('Student: critiquing outline...');
  const critRes = await call(cfg, {
    endpoint: 'student',
    messages: [
      {role: 'system', content: STUDENT_SYSTEM},
      {
        role: 'user',
        content: `You are reviewing a draft research proposal outline as a NeurIPS area chair. Be tough.\n\nOutline:\n${outline}\n\nReturn strict JSON: { "issues": [ { "severity": "major|minor", "note": "string" } ], "ok": true|false }`,
      },
    ],
    jsonOnly: true,
  });
  let critique: {
    issues: Array<{severity: string; note: string}>;
    ok: boolean;
  };
  try {
    critique = extractJson(critRes.text) as typeof critique;
  } catch {
    critique = {issues: [], ok: true};
  }

  if (!critique.ok && critique.issues.length > 0) {
    console.log(
      `Teacher: revising (${critique.issues.length} issues from reviewer)...`,
    );
    const feedback = critique.issues
      .map((i) => `- [${i.severity}] ${i.note}`)
      .join('\n');
    const revRes = await call(cfg, {
      endpoint: 'teacher',
      messages: [
        {role: 'system', content: TEACHER_SYSTEM},
        {
          role: 'user',
          content: `Revise the following NeurIPS-style proposal outline to address the area-chair feedback. Keep the same 11-section structure.\n\nOutline:\n${outline}\n\nFeedback:\n${feedback}\n\nReturn only the revised Markdown outline.`,
        },
      ],
    });
    outline = revRes.text;
  }

  const stamp = new Date().toISOString().slice(0, 10);
  const slugSource =
    payload!.angle?.trim() ||
    (seeds[0]?.text ?? '') ||
    'proposal';
  const slug = slugSource
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 40);
  const outDir = path.join(process.cwd(), 'proposals');
  fs.mkdirSync(outDir, {recursive: true});
  const outFile = path.join(outDir, `${stamp}-${slug || 'proposal'}.md`);

  const header = [
    `<!--`,
    `Generated by Gurukul. Teacher=${cfg.teacherEndpoint}, Student=${cfg.studentEndpoint}.`,
    `Angle: ${angle}`,
    `Seeds used: ${seeds.length}`,
    `Pushback notes used: ${Object.keys(pushback).length}`,
    `Generated: ${new Date().toISOString()}`,
    `-->`,
    '',
  ].join('\n');

  fs.writeFileSync(outFile, header + outline + '\n', 'utf8');
  console.log(`\n✓ Wrote ${path.relative(process.cwd(), outFile)}`);
  console.log('\nReminder: this is an outline, not a paper. The experiments are still on you.');
}

main().catch((err) => {
  console.error('\n✗ Proposal generation failed:');
  console.error(err);
  process.exit(1);
});
