# Gurukul Architecture

This document describes the current Gurukul system architecture: a Databricks App that turns a research seed into a grounded LLM-research knowledge graph, validates generated content, assesses learner understanding, and helps produce research directions and paper scaffolds.

The architecture has two layers that must be kept separate:

- **Learning graph layer**: concepts, topic categories, typed edges, chapters, assessments, misconceptions, and competence.
- **Research reasoning layer**: thesis, claims, evidence, assumptions, unknowns, risks, falsification tests, and experiment plans. This is only partially implemented today.

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ User Experience                                                              │
│ React + Vite UI                                                              │
│ - topic tree, mind map, topic reader, references                             │
│ - model comparisons, MCQ/Socratic challenge panel                            │
│ - eval dashboard, competence map, research panel, paper scaffold             │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ REST + SSE
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ Databricks App Runtime                                                       │
│ uv run start-app                                                             │
│ - FastAPI `/api/*` routes                                                    │
│ - MLflow AgentServer `/responses`                                            │
│ - SSE broadcaster for thought steps, graph mutations, status updates         │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ Agentic Core                                                                 │
│ - Seed Resolver: policy-driven grounding and evidence ranking                │
│ - Teacher Agent: grounded topic decomposition and typed graph edges          │
│ - Student Agents: concurrent chapter generation and comparison content       │
│ - Deepener: expands thin content with mechanisms and trade-offs              │
│ - EvidenceRepairer: removes, hedges, or redacts unsupported claims           │
│ - Examiner/Eval flows: MCQs, Socratic checks, quality scoring, learnings     │
│ - Research Reasoning: thesis, claims, unknowns, assumptions, falsification   │
└───────────────┬───────────────────────────────────────────────┬──────────────┘
                │ evidence                                      │ model calls
┌───────────────▼──────────────────────────┐       ┌────────────▼─────────────┐
│ Grounding Sources                         │       │ Databricks Model Serving │
│ - Lakebase corpus: SPECTER2 + BM25        │       │ - databricks-gpt-5-5     │
│ - arXiv: paper metadata and abstracts     │       │ - claude-sonnet-4-6      │
│ - Semantic Scholar: S2 paper search       │       │ - gurukul-specter2-embed │
│ - Tavily: labeled freshness context       │       └────────────┬─────────────┘
└───────────────┬──────────────────────────┘                    │
                │ read/write                                     │ auth via SDK
┌───────────────▼────────────────────────────────────────────────▼─────────────┐
│ Databricks Platform                                                          │
│ - Lakebase Postgres: graph, payloads, corpus, evals, learning state          │
│ - Serverless Jobs: SPECTER2 endpoint deploy, corpus build/indexing           │
│ - Secrets and app resources: S2, Tavily, Postgres, model endpoints           │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### Interactive Exploration Flow

```
User seed
  │
  ▼
POST /api/explore
  │
  ├─▶ resolve_seed(seed)
  │     ├─ split obvious multi-seeds
  │     ├─ search allowed scholarly sources
  │     ├─ embed seed and candidates with SPECTER2
  │     ├─ classify as specific_entity, general_concept, multi_seed, partial, or unresolved
  │     └─ persist seed_resolutions with evidence, scores, notes, and errors
  │
  ├─▶ Teacher Agent
  │     ├─ receives grounding context and existing graph
  │     ├─ emits topic nodes and typed edges
  │     └─ stores queued topics and graph_edges in Lakebase
  │
  ├─▶ Student Agents
  │     ├─ run concurrently up to AGENT_CONCURRENCY
  │     ├─ fetch arXiv and Tavily context for topic writing
  │     ├─ generate strict JSON topic payloads
  │     └─ deepen thin chapters when needed
  │
  ├─▶ Quality Gate
  │     ├─ validate schema, structure, references, claims, and evidence alignment
  │     ├─ run EvidenceRepairer for high-severity evidence issues
  │     ├─ redact unsupported claims that remain after repair
  │     └─ attach `_quality` evidence metadata to the payload
  │
  └─▶ Lakebase + SSE
        ├─ store completed payloads in topics.payload
        ├─ broadcast thought, node, edge, and status events
        └─ UI refreshes topic tree, mind map, and content panels
```

### Research Reasoning Flow

This is the architectural layer that bridges "I learned a concept" to "I can make a research contribution."

```text
Grounded topic graph + learner competence
  │
  ├─▶ collect candidate open_problems from topic payloads
  ├─▶ select connected concepts using graph_edges
  ├─▶ check learner readiness using MCQ/Socratic/misconception state
  │
  ▼
Research Direction
  ├─ thesis: the core argument or contribution claim
  ├─ supporting claims: smaller claims the thesis depends on
  ├─ evidence: papers, topic payload snippets, experiment results
  ├─ assumptions: things believed but not yet verified
  ├─ unknowns: details not publicly known or not yet tested
  ├─ risks: reasons the thesis may fail
  ├─ falsification tests: what result would disprove the thesis
  └─ experiment plan: concrete steps to reduce unknowns

Paper Scaffold
  ├─ sections
  ├─ key arguments
  ├─ evaluation strategy
  └─ unresolved unknowns that must be resolved before writing
```

Current implementation stores `research_directions` and `paper_scaffolds`, but does **not yet** store thesis, claim, assumption, unknown, or falsification objects as first-class rows. That is the next architecture gap to close.

### Offline Corpus And Endpoint Flow

```
./scripts/deploy_specter2.sh
  └─ Databricks Job: gurukul-specter2-deploy
       ├─ register SPECTER2 pyfunc in Unity Catalog
       ├─ create or update `gurukul-specter2-embed`
       ├─ wait for endpoint READY
       └─ write progress to gurukul.long_running_jobs

./scripts/build_corpus.sh
  └─ Databricks Job: gurukul-corpus-build
       ├─ load S2_API_KEY from Databricks Secrets
       ├─ search Semantic Scholar with curated LLM-research queries
       ├─ normalize, deduplicate, and filter low-quality papers
       ├─ embed paper title/abstract pairs with SPECTER2
       ├─ upsert `corpus_papers`
       ├─ clean duplicate or incomplete corpus rows
       ├─ create/repair Lakebase ANN and BM25 indexes
       └─ write progress to gurukul.long_running_jobs
```

## Runtime Components

| Component | Files | Responsibility |
| --- | --- | --- |
| React client | `src/` | Exploration, content reading, mind map, challenge, evaluation, and research panels |
| FastAPI routes | `agent_server/routes.py` | REST API, SSE route, challenges, evals, quality routes, research routes |
| Agent orchestration | `agent_server/agent.py` | Teacher decomposition, Student generation, deepening, repair, MLflow ResponsesAgent wrapper |
| Databricks OpenAI client | `agent_server/llm_client.py` | Builds `openai.AsyncOpenAI` using Databricks SDK auth and `/serving-endpoints` base URL |
| Grounding policy | `agent_server/grounding/policy.py` | Defines `strict_scholarly` and `freshness_augmented` source rules |
| Grounding resolver | `agent_server/grounding/resolver.py` | Splits seeds, searches sources, embeds candidates, classifies route, formats evidence context |
| Corpus retrieval | `agent_server/corpus.py` | S2 normalization, corpus upsert, SPECTER2 embedding, hybrid vector/BM25 search |
| Guardrails | `agent_server/guardrails.py` | Payload schema cleanup, reference checks, claim checks, evidence alignment, redaction |
| Lakebase persistence | `agent_server/db.py` | Schema management, async pool, OAuth database credentials, graph/content/eval persistence |
| Serverless jobs | `jobs/` and `scripts/` | Remote SPECTER2 endpoint deployment and corpus build/indexing |

## Learning Graph Vs Research Reasoning Objects

Gurukul currently has strong representation for learning concepts and weaker representation for research reasoning.

| Object | Current status | Where it lives today | Architectural role |
| --- | --- | --- | --- |
| Topic concept | Implemented | `topics.title`, `topics.category` | Unit of learning and content generation |
| Concept category | Implemented | `foundations`, `architecture`, `training`, `inference`, `models`, `applications` | Coarse taxonomy for graph coverage |
| Concept relation | Implemented | `graph_edges.type` | `prerequisite`, `builds_on`, `contrasts`, `applies`, `related` |
| Sub-concept | Implemented for assessment | `mcq_questions.sub_concept`, `misconceptions.sub_concept` | Fine-grained understanding probe |
| Open problem | Implemented inside payload | `topics.payload.open_problems` | Candidate source for research direction generation |
| Hypothesis | Partially implemented | `topics.payload.experiment.hypothesis`, `research_directions.hypothesis` | Early experiment/research claim |
| Thesis | Missing first-class object | Usually implicit in scaffold title/abstract | Core argument of a proposed paper |
| Claim | Validated but not stored | Guardrails inspect text, no claim table | Atomic statement that needs evidence |
| Evidence | Partially stored | `_quality.evidence_*`, references, seed_resolutions | Boundary for what the system may assert |
| Assumption | Missing first-class object | Usually implicit in generated prose | Belief required for thesis to hold |
| Unknown | Partially expressed | `unresolved`, `unknown` confidence, "undisclosed" text | What must be resolved before confident contribution |
| Falsification test | Missing first-class object | Sometimes implied in experiment steps | Result that would disprove the thesis |

The research reasoning layer should eventually persist these as inspectable objects rather than burying them inside prose.

### Proposed Research Reasoning Schema

The following tables are not yet implemented, but they describe the missing architecture:

```sql
research_theses(
  id, user_id, title, thesis_statement, source_direction_id,
  readiness_score, status, created_at
)

research_claims(
  id, thesis_id, claim, claim_type,
  confidence, evidence_ids, assumption_ids, unknown_ids
)

research_unknowns(
  id, thesis_id, description, why_unknown,
  resolution_strategy, blocking_level, status
)

research_assumptions(
  id, thesis_id, assumption, risk_if_false,
  validation_method, status
)

research_falsification_tests(
  id, thesis_id, test_name, disconfirming_result,
  experiment_steps, metric, threshold
)
```

This would make paper generation auditable: every scaffold section can trace back to claims, claims trace back to evidence or assumptions, and unknowns remain visible instead of being smoothed into confident prose.

## Source Policy

Gurukul has explicit grounding policies instead of implicit fallback behavior.

| Policy | Allowed sources | Behavior |
| --- | --- | --- |
| `strict_scholarly` | Lakebase corpus, arXiv, Semantic Scholar | Used for seed resolution. Missing evidence results in `unresolved`; web search is not a substitute. |
| `freshness_augmented` | Scholarly sources plus labeled web evidence | Used when freshness is needed, especially model comparisons. Web context cannot override scholarly evidence. |

## Databricks Resources

`databricks.yml` and `app.yaml` attach these resources to the Databricks App:

| Resource | Current default | Purpose |
| --- | --- | --- |
| Lakebase Postgres | `projects/gurukul-lakebase/branches/production/endpoints/primary` | Persistent state and corpus storage |
| Teacher model | `databricks-gpt-5-5` | Topic graph decomposition, judge-style reasoning |
| Student model | `databricks-claude-sonnet-4-6` | Chapter writing, deepening, repair, challenge/eval flows |
| Embedding model | `gurukul-specter2-embed` | Seed and paper embeddings for scholarly retrieval |
| Tavily secret | `gurukul/tavily_api_key` | Fresh web context |
| Semantic Scholar secret | `gurukul/s2_api_key` | S2 live search and corpus ingestion |

## Lakebase Schema Groups

| Domain | Tables | Role |
| --- | --- | --- |
| Knowledge graph | `topics`, `graph_edges`, `explorations` | Core documentary graph and generated content |
| Learning | `annotations`, `challenge_sessions`, `mcq_questions`, `mcq_responses`, `misconceptions` | Understanding checks and learner state |
| Quality loop | `eval_runs`, `eval_actions`, `quality_learnings`, `judge_calibration` | Evaluation, improvement actions, reusable hints, judge calibration |
| Research output | `research_directions`, `paper_scaffolds` | Open-problem synthesis and paper scaffold generation |
| Grounded retrieval | `corpus_papers`, `seed_resolutions` | Scholarly corpus, embeddings, transparent seed decisions |
| Operations | `long_running_jobs` | Remote job progress and failure visibility |

## Deployment Boundary

The deployed Databricks App uses a dedicated workspace source path:

```text
/Workspace/Users/suneel.sunkara@databricks.com/apps/gurukul/app-src
```

Serverless job source is kept separate under the jobs workspace path. `deploy.sh` deletes and recreates `app-src` before upload so stale files, lockfiles, or job artifacts cannot be included in the Databricks Apps build snapshot.

The deployed app command is:

```bash
uv run start-app
```

Local development uses:

```bash
npm run dev
```

which starts Vite and the FastAPI agent server with `/api/*` proxied to the backend.
