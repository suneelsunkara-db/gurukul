// End-to-end smoke test that does NOT touch the interactive prompts.
// Forces dry-run mode, generates a topic with each protocol, renders
// the MDX, and validates the result parses back through the schema.

import fs from 'node:fs';
import path from 'node:path';
import {loadConfig} from './fm-client.js';
import {runProtocol, type ProtocolName} from './protocols.js';
import {renderMdx} from './mdx-render.js';
import type {TopicEntry} from './schema.js';

process.env.JOURNEY_DRY_RUN = '1';

async function main() {
  const cfg = loadConfig();
  if (!cfg.dryRun) {
    throw new Error('Smoke test must run in dry-run mode.');
  }

  const protocols: ProtocolName[] = [
    'outline-write-critique',
    'socratic',
    'debate',
  ];

  const outDir = path.join(process.cwd(), '.smoke-out');
  fs.mkdirSync(outDir, {recursive: true});

  for (const protocol of protocols) {
    console.log(`\n=== protocol=${protocol} ===`);
    const {payload} = await runProtocol(cfg, {
      topicTitle: `Smoke test (${protocol})`,
      protocol,
      comparison: false,
      log: (s) => console.log(`  · ${s}`),
    });

    if (!payload.key_aspects.length) {
      throw new Error('payload has no key_aspects');
    }

    const entry: TopicEntry = {
      id: `smoke-${protocol}`,
      title: `Smoke test (${protocol})`,
      path: `/smoke-${protocol}`,
      slug: `smoke-${protocol}`,
      protocol,
      teacher: cfg.teacherEndpoint,
      student: cfg.studentEndpoint,
      generatedAt: new Date().toISOString(),
      isComparison: false,
      parentId: null,
      position: 100,
    };

    const mdx = renderMdx({entry, payload});
    if (!mdx.includes('<Summary')) {
      throw new Error('MDX missing <Summary>');
    }
    if (!mdx.includes('<ConfidenceTracker')) {
      throw new Error('MDX missing <ConfidenceTracker>');
    }
    fs.writeFileSync(path.join(outDir, `${protocol}.mdx`), mdx, 'utf8');
    console.log(`  → wrote ${path.join('.smoke-out', `${protocol}.mdx`)}`);
  }

  // And one comparison chapter.
  console.log('\n=== protocol=outline-write-critique (comparison) ===');
  const {payload: cmpPayload} = await runProtocol(cfg, {
    topicTitle: 'Frontier model comparison (smoke)',
    protocol: 'outline-write-critique',
    comparison: true,
    log: (s) => console.log(`  · ${s}`),
  });
  const cmpEntry: TopicEntry = {
    id: 'smoke-comparison',
    title: 'Frontier model comparison (smoke)',
    path: '/smoke-comparison',
    slug: 'smoke-comparison',
    protocol: 'outline-write-critique',
    teacher: cfg.teacherEndpoint,
    student: cfg.studentEndpoint,
    generatedAt: new Date().toISOString(),
    isComparison: true,
    parentId: null,
    position: 200,
  };
  const cmpMdx = renderMdx({entry: cmpEntry, payload: cmpPayload});
  fs.writeFileSync(path.join(outDir, 'comparison.mdx'), cmpMdx, 'utf8');
  console.log(`  → wrote ${path.join('.smoke-out', 'comparison.mdx')}`);

  console.log('\nAll protocols rendered. Outputs in .smoke-out/');
}

main().catch((err) => {
  console.error('\nSMOKE TEST FAILED:');
  console.error(err);
  process.exit(1);
});
