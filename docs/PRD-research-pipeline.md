# PRD: Unified Research Pipeline — From Exploration to Publication

**Product**: Gurukul  
**Version**: v4.0  
**Author**: Suneel Sunkara  
**Status**: Draft  
**Last Updated**: 2026-07-05  
**Supersedes**: PRD-conceptual-connections-learning-engine.md (v3.0)

---

## Implementation Alignment: Current System Architecture

The current implementation has moved from a standalone learning pipeline into an evidence-grounded Databricks App architecture. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system and data flow.

### Runtime Architecture

```text
React/Vite UI
  -> FastAPI + SSE + MLflow AgentServer
  -> Seed Resolver
  -> Teacher Agent
  -> Student Agents
  -> Quality Gate
  -> Lakebase Postgres
```

The product pipeline still follows Explore -> Assess -> Synthesize -> Identify -> Write, but every stage now depends on a shared substrate:

- **Grounded seed resolution**: `strict_scholarly` policy resolves seeds with Lakebase corpus, arXiv, and Semantic Scholar before Teacher decomposition.
- **Teacher/Student generation**: `databricks-gpt-5-5` maps topics and typed edges; `databricks-claude-sonnet-4-6` writes chapters, deepens thin content, and powers challenge/eval flows.
- **Quality gate before assessment**: Generated content must pass structure, references, claim, and source-evidence checks. High-severity issues trigger repair; remaining unsupported claims are redacted before publication.
- **Learning and research state in Lakebase**: Challenge sessions, MCQs, misconceptions, eval runs, quality learnings, research directions, and paper scaffolds are persisted in the same Lakebase schema as the knowledge graph.
- **Offline corpus jobs**: Serverless jobs deploy the SPECTER2 endpoint and build the local scholarly corpus used for seed resolution and research grounding.

### Architecture Implication For This PRD

The PRD requirements should be read as product behavior on top of this architecture, not as separate features. In particular:

- MCQs and Socratic challenges are generated from already-published topic payloads, so they inherit the content-quality gate.
- Research directions must use grounded open problems, graph topology, and demonstrated competence. They should not invent novelty claims without evidence.
- Paper scaffolds are readiness artifacts, not publication-ready papers; they must preserve source boundaries and call out where external validation is still required.

---

## 0. The Core Problem

Gurukul currently generates content and lets you read it. The eval dashboard tells you if the *content* is good. The Socratic challenge tells you if *you* understand it. But none of these answer the question:

**"What specific research paper can I write, and am I ready to write it?"**

The path from exploration → understanding → contribution → paper is broken into disconnected pieces. This PRD unifies them into a single pipeline where each step feeds the next.

---

## 1. The Pipeline (Not Three Features)

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌───────────┐     ┌──────────┐
│ EXPLORE  │ ──▶ │   ASSESS     │ ──▶ │  SYNTHESIZE  │ ──▶ │ IDENTIFY  │ ──▶ │  WRITE   │
│ topics   │     │ understanding│     │ connections  │     │ gaps &    │     │ paper    │
│          │     │              │     │              │     │ hypotheses│     │          │
└──────────┘     └──────────────┘     └──────────────┘     └───────────┘     └──────────┘
  (exists)         MCQ + Socratic      Competence Map     Research Readiness  Paper Scaffold
                       (A)                 (B)                 (B+C)              (C)
```

Each stage produces data that the next stage consumes. You can't skip steps — a paper scaffold generated without demonstrated understanding is a hollow template.

---

## 2. Stage A: Calibrated Knowledge Assessment (MCQ + Socratic)

### 2.1 Why Both MCQ and Socratic?

They test different things:

| Type | Tests | Speed | Gaming risk | Research signal |
|------|-------|-------|-------------|-----------------|
| MCQ | Recall, recognition, common misconceptions | Fast (30s per Q) | Moderate (can guess) | "Do you know the facts?" |
| Socratic | Reasoning, synthesis, original thinking | Slow (2-3 min per Q) | Low (must articulate) | "Can you think with these facts?" |

**The critical insight**: MCQs are not dumbed-down Socratic questions. They serve a different purpose — **misconception detection at scale**. A well-designed MCQ with plausible distractors reveals *specific* misunderstandings faster than open-ended dialogue.

### 2.2 MCQ Design Principles

**Not trivia. Diagnostic probes.**

Each MCQ must:
1. **Target a specific sub-concept** within the topic (not the whole topic)
2. **Have plausible distractors** that represent common misconceptions
3. **Include an explanation** for every option — why correct, why each wrong answer is wrong
4. **Map to a competence dimension** (recall / mechanism / tradeoff / application)

**Example** (Topic: ReAct Pattern):

```
Q: A ReAct agent calls a search API that returns an error. 
   What happens next in a standard ReAct implementation?

A) The agent retries the same tool call with modified parameters  ← Wrong: ReAct has no built-in retry
B) The agent generates a new Thought step reflecting on the      ← CORRECT: ReAct interleaves Thought/Action/Obs
   error, then decides on the next Action
C) The orchestrator catches the error and routes to a fallback   ← Wrong: confuses ReAct with supervisor patterns
   agent
D) The agent replans from scratch using the remaining context    ← Wrong: confuses ReAct with Plan-and-Execute

Dimension: mechanism
Sub-concept: error-handling-in-react-loop
Misconception detected if B or D chosen: "confuses ReAct with Plan-and-Execute"
```

### 2.3 MCQ Generation Strategy

The Examiner agent generates MCQs FROM the topic content, not from general knowledge. This means:
- Questions are grounded in what the Student model actually wrote
- If the content has gaps, the MCQs won't test on hallucinated material
- The quality of MCQs is bounded by the quality of content (which the eval loop improves)

**Prompt structure for MCQ generation:**

```
Given this topic content: [full payload]

Generate 5 multiple-choice questions that test understanding at different levels:
- 1 RECALL question (can the learner identify the correct definition/mechanism?)
- 2 MECHANISM questions (does the learner understand HOW it works, not just WHAT it is?)  
- 1 TRADEOFF question (can the learner reason about when to use this vs alternatives?)
- 1 APPLICATION question (can the learner apply this to a novel scenario?)

For each question:
- 4 options (A-D), exactly one correct
- Each distractor must represent a SPECIFIC, COMMON misconception (not random noise)
- Explanation for each option (why right/wrong)
- Sub-concept ID (kebab-case) identifying which aspect of the topic this tests
- Dimension: recall | mechanism | tradeoff | application
```

### 2.4 Assessment Flow

```
User clicks "Challenge Me" on a topic
    │
    ├─── MCQ Round (5 questions, ~3 minutes)
    │    ├── Immediate feedback per question
    │    ├── Misconceptions flagged and stored
    │    └── Sub-concept scores calculated
    │
    ├─── IF mcq_score >= 60%: Unlock Socratic Round
    │    ├── 2-3 open-ended questions targeting WEAK sub-concepts from MCQ
    │    ├── Agent evaluates depth and reasoning
    │    └── Final understanding level assigned
    │
    └─── IF mcq_score < 60%: "Review these areas first"
         ├── Link to specific sections of the topic content
         └── Suggest re-reading before re-attempting
```

### 2.5 Data Model

```sql
-- MCQ questions generated for topics
CREATE TABLE IF NOT EXISTS gurukul.mcq_questions (
    id            SERIAL PRIMARY KEY,
    topic_id      TEXT NOT NULL,
    sub_concept   TEXT NOT NULL,
    dimension     TEXT NOT NULL,  -- recall | mechanism | tradeoff | application
    question      TEXT NOT NULL,
    options       JSONB NOT NULL,  -- [{id: "a", text: "...", is_correct: bool, explanation: "..."}]
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- User's MCQ responses
CREATE TABLE IF NOT EXISTS gurukul.mcq_responses (
    id            SERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    question_id   INTEGER NOT NULL REFERENCES gurukul.mcq_questions(id),
    selected      TEXT NOT NULL,  -- "a", "b", "c", "d"
    is_correct    BOOLEAN NOT NULL,
    time_ms       INTEGER,  -- how long they took
    session_id    INTEGER REFERENCES gurukul.challenge_sessions(id),
    answered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tracked misconceptions (persists across sessions)
CREATE TABLE IF NOT EXISTS gurukul.misconceptions (
    id             SERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    topic_id       TEXT NOT NULL,
    sub_concept    TEXT NOT NULL,
    claim          TEXT NOT NULL,
    correction     TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'minor',  -- minor | conceptual | fundamental
    occurrences    INTEGER NOT NULL DEFAULT 1,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at    TIMESTAMPTZ
);
```

---

## 3. Stage B: Competence Map & Learning Engine

### 3.1 What the Competence Map IS

Not a single number. A multi-dimensional profile per topic:

```json
{
  "topic_id": "react-pattern",
  "competence": {
    "breadth": 0.8,     // 4 of 5 sub-concepts tested
    "depth": "deep",    // from Socratic assessment
    "recall": 1.0,      // MCQ recall dimension
    "mechanism": 0.67,  // MCQ mechanism dimension  
    "tradeoff": 0.5,    // MCQ tradeoff dimension
    "application": 1.0, // MCQ application dimension
    "recency_days": 3,  // last assessed 3 days ago
    "misconceptions": 1 // 1 unresolved misconception
  },
  "research_readiness": "can_contribute",  // computed from above
  "blocking_gaps": ["error-handling-in-react-loop"]
}
```

### 3.2 Research Readiness Levels (Per Topic)

| Level | Criteria | What you can do |
|-------|----------|-----------------|
| **not_assessed** | No challenge attempted | Nothing — unknown |
| **needs_study** | MCQ < 60% OR Socratic = surface | Read more before attempting research |
| **can_discuss** | MCQ >= 60%, Socratic >= structural, no fundamental misconceptions | Write a related-work section about this topic |
| **can_build_on** | MCQ >= 80%, Socratic >= deep, tradeoff >= 0.5 | Design methodology that USES this concept |
| **can_contribute** | MCQ >= 80%, Socratic >= deep, application >= 0.7, no unresolved misconceptions | Propose improvements or novel combinations involving this concept |

### 3.3 Cross-Topic Synthesis Assessment

**When it unlocks**: User has 3+ connected topics (via graph edges) at `can_discuss` or higher.

**How it works**: The Examiner generates a scenario requiring reasoning across ALL connected topics. This tests whether the user can:
- Identify which concept applies to which part of the problem
- Reason about interactions between concepts
- Spot conflicts between approaches

**Why this matters for research**: Papers don't exist in one topic. Every contribution bridges multiple concepts. If you can't synthesize ReAct + Multi-Agent + Memory Management in a single coherent argument, you can't write a paper about "memory-aware multi-agent ReAct systems."

### 3.4 Knowledge Decay

Understanding assessed more than 14 days ago is marked "stale." The competence map dims stale assessments and excludes them from research readiness calculations. Re-assessment is shorter (2 MCQ + 1 Socratic) since there's a baseline.

---

## 4. Stage C: Research Direction Discovery & Paper Scaffold

### 4.1 The Gap Detection Algorithm

This is the core intellectual contribution of Gurukul. It's not just "find open problems" — it's:

```
RESEARCH_OPPORTUNITY = intersection(
    your_strong_topics,          -- topics at can_build_on or can_contribute
    open_problems,               -- from topic content
    graph_topology,              -- especially contrasts and builds_on edges
    gap_in_existing_literature   -- no paper combines X and Y this way
)
```

**Concrete example:**

```
You have:
  - ReAct Pattern: can_contribute (deep understanding, high application score)
  - Multi-Agent Orchestration: can_build_on (deep understanding, moderate tradeoff score)
  - Memory & Context Management: can_contribute (creative level, no misconceptions)

The graph has:
  - ReAct ──builds_on──▶ Multi-Agent (agents use ReAct internally)
  - Memory ──applies──▶ Multi-Agent (shared memory coordinates agents)
  - Memory ──applies──▶ ReAct (context window limits ReAct horizon)

Open problems in these topics:
  - "ReAct agents lose context in long task horizons" (from ReAct content)
  - "Multi-agent systems lack efficient shared state" (from Multi-Agent content)
  - "No principled approach to deciding what to keep vs evict from agent memory" (from Memory content)

DETECTED OPPORTUNITY:
  Title: "Selective Memory Persistence for Long-Horizon Multi-Agent ReAct Systems"
  Hypothesis: A memory management policy that uses task-graph structure to 
    decide what each agent retains will reduce redundant observations and 
    improve multi-step task completion rates in multi-agent ReAct systems.
  
  Why you specifically: You understand all three building blocks deeply enough 
    to propose a principled integration. Your competence in Memory is at 
    creative level, meaning you've already shown you can propose improvements.
  
  Why it's novel: Existing work treats agent memory as fixed (full context) or 
    naive (FIFO eviction). No paper combines structured memory policies with 
    ReAct's observation-action-thought loop in a multi-agent setting.
  
  Readiness score: 0.87 (high — all prerequisites met, no blocking gaps)
```

### 4.2 Paper Scaffold Generator

When you select a research direction, the Teacher agent generates a structured paper scaffold:

```json
{
  "working_title": "Selective Memory Persistence for Long-Horizon Multi-Agent ReAct Systems",
  "venue_suggestions": [
    {"venue": "NeurIPS 2026", "track": "Agent Learning", "fit": "strong", 
     "reason": "NeurIPS has had increasing focus on agent systems. This combines memory, multi-agent, and reasoning."},
    {"venue": "ICML 2026", "track": "Reinforcement Learning", "fit": "moderate",
     "reason": "If the memory policy is learned via RL, the optimization angle fits ICML."}
  ],
  "abstract_draft": "...",
  "sections": {
    "introduction": {
      "hook": "LLM agents increasingly tackle multi-step tasks, but...",
      "gap_statement": "No existing approach combines...",
      "contribution_summary": "We propose..."
    },
    "related_work": {
      "clusters": [
        {"label": "ReAct and tool-augmented reasoning", "your_topics": ["react-pattern", "tool-use"], "key_papers": ["(from topic references)"]},
        {"label": "Multi-agent coordination", "your_topics": ["multi-agent-orchestration"], "key_papers": ["..."]},
        {"label": "Context and memory management", "your_topics": ["memory-context-management"], "key_papers": ["..."]}
      ]
    },
    "method": {
      "components": [
        {"name": "Task-graph memory policy", "builds_on": "memory-context-management", "novel_aspect": "..."},
        {"name": "Per-agent ReAct with selective context", "builds_on": "react-pattern", "novel_aspect": "..."}
      ]
    },
    "experiments": {
      "benchmarks": ["WebArena", "SWE-bench", "custom multi-agent task suite"],
      "baselines": ["Full-context ReAct", "FIFO eviction", "No shared memory"],
      "metrics": ["Task completion rate", "Token efficiency", "Latency per step"],
      "ablations": ["Memory policy variants", "Number of agents", "Task horizon length"]
    },
    "discussion": {
      "limitations": ["Assumes task-graph is available", "Tested on synthetic tasks"],
      "future_work": ["Learned task-graph extraction", "Application to code generation agents"]
    }
  },
  "prerequisite_understanding": {
    "met": ["react-pattern", "multi-agent-orchestration", "memory-context-management"],
    "gaps": [],
    "recommendation": "You are ready to begin. Start with the method section."
  }
}
```

### 4.3 Interactive Refinement

The scaffold is not a one-shot generation. The user can:

1. **Challenge the hypothesis** → Agent plays devil's advocate, identifies weaknesses
2. **Narrow the scope** → "Focus only on the memory policy, assume single-agent for now"
3. **Shift the venue** → System adjusts framing (e.g., more theoretical for ICLR, more empirical for NeurIPS)
4. **Request related work deep-dive** → Agent explores a specific cluster and adds/removes papers
5. **Design experiments in detail** → Agent helps specify exact experimental protocol

Each refinement iteration is tracked, so you can see how the paper idea evolved.

---

## 5. UI Design: The Research Journey View

### 5.1 New Tab: "Research" (alongside Explore and Eval)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Explore  │  Eval  │  Research                                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─ COMPETENCE MAP ────────────────────────────────────────────┐   │
│  │                                                              │   │
│  │  [Knowledge graph with competence overlay]                   │   │
│  │  Nodes colored by readiness level:                           │   │
│  │    ░ not_assessed  ▒ needs_study  ▓ can_discuss              │   │
│  │    █ can_build_on  ★ can_contribute                          │   │
│  │                                                              │   │
│  │  Edges show prerequisite chains with completion status       │   │
│  │  Stale nodes have dashed borders                             │   │
│  │                                                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ RESEARCH OPPORTUNITIES ────────────────────────────────────┐   │
│  │                                                              │   │
│  │  📊 3 of 10 topics at can_contribute | 2 blocking gaps      │   │
│  │                                                              │   │
│  │  1. "Selective Memory Persistence for Multi-Agent ReAct"     │   │
│  │     Readiness: 87% | Topics: ReAct + Multi-Agent + Memory   │   │
│  │     [View Scaffold]  [Challenge Hypothesis]                  │   │
│  │                                                              │   │
│  │  2. "Adaptive Tool Selection via Learned Action Spaces"      │   │
│  │     Readiness: 72% | Blocking: tool-use tradeoff score low  │   │
│  │     [Assess Tool Use]  [View Scaffold]                       │   │
│  │                                                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ BLOCKING GAPS ─────────────────────────────────────────────┐   │
│  │                                                              │   │
│  │  ⚠ "Reward Modeling" — not assessed, prerequisite for 3     │   │
│  │    alignment topics  [Take Challenge]                        │   │
│  │                                                              │   │
│  │  ⚠ Misconception: "ReAct maintains explicit plans"          │   │
│  │    Seen 2x, unresolved  [Review Topic]  [Re-assess]         │   │
│  │                                                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─ PAPER SCAFFOLDS ──────────────────────────────────────────┐    │
│  │                                                              │   │
│  │  Draft: "Selective Memory Persistence..."  v3 (Jun 20)      │   │
│  │  Sections: Intro ✓ | Related Work ✓ | Method ◐ | Expts ○   │   │
│  │  [Continue Editing]  [Export LaTeX]  [Challenge Hypothesis] │   │
│  │                                                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Challenge Panel: MCQ + Socratic Combined

```
┌──────────────────────────────────────────────────────────┐
│  Challenge: ReAct Pattern                    Round 2/5   │
│                                                          │
│  Q: A ReAct agent calls a search API that returns an     │
│     error. What happens next?                            │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ ○ A) Retries with modified parameters               │ │
│  │ ● B) Generates a new Thought reflecting on error    │ │  ← selected
│  │ ○ C) Orchestrator routes to fallback agent          │ │
│  │ ○ D) Replans from scratch                           │ │
│  └─────────────────────────────────────────────────────┘ │
│  [Submit]                                                │
│                                                          │
│  ── Previous Round ─────────────────────────────────── │
│  ✓ Correct — You correctly identified that ReAct...    │
│    Dimension: recall | Sub-concept: react-loop-basics  │
│                                                          │
│  ── Progress ──────────────────────────────────────── │
│  recall ████████░░ 1/1    mechanism ░░░░░░░░░░ 0/2    │
│  tradeoff ░░░░░░░░░░ 0/1  application ░░░░░░░░░░ 0/1 │
│                                                          │
│  MCQ score: 100% (1/1) — 4 questions remaining          │
└──────────────────────────────────────────────────────────┘
```

---

## 6. API Design

### Assessment APIs

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/challenge/{topic_id}/mcq/generate` | POST | Generate MCQ set for a topic (5 questions) |
| `POST /api/challenge/{topic_id}/mcq/answer` | POST | Submit MCQ answer, get feedback + misconception tracking |
| `GET /api/challenge/{topic_id}/mcq/questions` | GET | Get cached MCQ questions for a topic |
| `POST /api/challenge/{topic_id}/start` | POST | Start Socratic round (existing, unchanged) |
| `POST /api/challenge/{session_id}/answer` | POST | Submit Socratic answer (existing, unchanged) |

### Competence & Readiness APIs

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /api/competence` | GET | Full competence map across all topics |
| `GET /api/competence/{topic_id}` | GET | Detailed competence for a single topic |
| `GET /api/misconceptions` | GET | All unresolved misconceptions |
| `POST /api/misconceptions/{id}/resolve` | POST | Mark a misconception as resolved after re-assessment |

### Research Pipeline APIs

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /api/research/opportunities` | GET | Ranked research opportunities based on competence + open problems |
| `POST /api/research/scaffold` | POST | Generate paper scaffold for a selected opportunity |
| `PUT /api/research/scaffold/{id}` | PUT | Update/refine a scaffold (tracks iterations) |
| `POST /api/research/scaffold/{id}/challenge` | POST | Agent plays devil's advocate on the hypothesis |
| `GET /api/research/scaffolds` | GET | All saved paper scaffolds |

---

## 7. Database Schema (New Tables)

```sql
-- ── MCQ questions (generated per topic) ──────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.mcq_questions (
    id            SERIAL PRIMARY KEY,
    topic_id      TEXT NOT NULL,
    sub_concept   TEXT NOT NULL,
    dimension     TEXT NOT NULL,  -- recall | mechanism | tradeoff | application
    question      TEXT NOT NULL,
    options       JSONB NOT NULL,
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── MCQ responses ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.mcq_responses (
    id            SERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    question_id   INTEGER NOT NULL REFERENCES gurukul.mcq_questions(id),
    topic_id      TEXT NOT NULL,
    selected      TEXT NOT NULL,
    is_correct    BOOLEAN NOT NULL,
    time_ms       INTEGER,
    answered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Misconceptions (tracked across sessions) ─────────────────────
CREATE TABLE IF NOT EXISTS gurukul.misconceptions (
    id             SERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    topic_id       TEXT NOT NULL,
    sub_concept    TEXT NOT NULL,
    claim          TEXT NOT NULL,
    correction     TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'minor',
    occurrences    INTEGER NOT NULL DEFAULT 1,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at    TIMESTAMPTZ
);

-- ── Research directions & paper scaffolds ─────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.research_directions (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    title           TEXT NOT NULL,
    source_topics   JSONB NOT NULL,     -- ["react-pattern", "multi-agent", "memory"]
    open_problems   JSONB NOT NULL,     -- [{topic_id, problem_id, text}]
    hypothesis      TEXT,
    readiness_score REAL NOT NULL DEFAULT 0,
    blocking_gaps   JSONB,              -- [{topic_id, gap_type, description}]
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gurukul.paper_scaffolds (
    id              SERIAL PRIMARY KEY,
    direction_id    INTEGER NOT NULL REFERENCES gurukul.research_directions(id),
    user_id         TEXT NOT NULL DEFAULT 'default',
    version         INTEGER NOT NULL DEFAULT 1,
    scaffold        JSONB NOT NULL,     -- full scaffold structure
    refinement_log  JSONB DEFAULT '[]', -- [{action, before, after, timestamp}]
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_mcq_topic ON gurukul.mcq_questions(topic_id);
CREATE INDEX IF NOT EXISTS idx_mcq_resp_user ON gurukul.mcq_responses(user_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_misconceptions_active 
    ON gurukul.misconceptions(user_id, resolved_at) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_research_user ON gurukul.research_directions(user_id);
```

---

## 8. Implementation Phases

### Phase 1: MCQ Challenge System (Week 1)

**Goal**: User can take MCQ quizzes on any completed topic, see immediate feedback, and have misconceptions tracked.

- [ ] Schema: `mcq_questions`, `mcq_responses`, `misconceptions` tables
- [ ] Backend: MCQ generation prompt for Examiner agent
- [ ] Backend: `POST /api/challenge/{topic_id}/mcq/generate` — generate 5 MCQs from topic content
- [ ] Backend: `POST /api/challenge/{topic_id}/mcq/answer` — evaluate answer, track misconceptions
- [ ] Frontend: MCQ panel in ChallengePanel (radio buttons, submit, feedback per question)
- [ ] Frontend: dimension progress bars (recall/mechanism/tradeoff/application)
- [ ] Integration: MCQ score gates Socratic assessment (>=60% to proceed)

### Phase 2: Competence Map (Week 2)

**Goal**: Visual overlay on knowledge graph showing research readiness per topic, with blocking gaps highlighted.

- [ ] Backend: `GET /api/competence` — aggregate MCQ scores + Socratic levels + misconceptions into competence profile
- [ ] Backend: Research readiness calculation (not_assessed → can_contribute)
- [ ] Backend: Stale detection (14-day threshold)
- [ ] Frontend: Color-coded nodes on mind map by readiness level
- [ ] Frontend: Competence detail panel when clicking a node
- [ ] Frontend: Blocking gaps list with "Take Challenge" links

### Phase 3: Research Direction Discovery (Week 3)

**Goal**: System automatically identifies research opportunities from the intersection of user competence + open problems + graph topology.

- [ ] Schema: `research_directions` table
- [ ] Backend: Gap detection algorithm (competence × open_problems × graph_edges)
- [ ] Backend: `GET /api/research/opportunities` — ranked list with readiness scores
- [ ] Backend: Hypothesis generation from Teacher agent
- [ ] Frontend: Research tab with opportunities list
- [ ] Frontend: Blocking gaps that prevent higher readiness scores

### Phase 4: Paper Scaffold Generator (Week 4)

**Goal**: User can select a research direction and get a structured paper outline with sections, related work from explored topics, experiment design, and venue suggestions.

- [ ] Schema: `paper_scaffolds` table
- [ ] Backend: `POST /api/research/scaffold` — generate full scaffold
- [ ] Backend: `POST /api/research/scaffold/{id}/challenge` — devil's advocate mode
- [ ] Backend: `PUT /api/research/scaffold/{id}` — refine and version
- [ ] Frontend: Scaffold editor with section-by-section view
- [ ] Frontend: "Challenge Hypothesis" button
- [ ] Frontend: Export to LaTeX/Markdown

### Phase 5: Feedback Loop & Polish (Week 5)

- [ ] Knowledge decay: stale indicators, shortened re-assessment
- [ ] Cross-topic synthesis challenges
- [ ] Misconception resolution tracking (re-assess after studying)
- [ ] Research direction iteration tracking (how the idea evolved)
- [ ] Scaffold refinement history

---

## 9. Critical Design Decisions

### 9.1 MCQ Quality is Bounded by Content Quality

If the Student model generates vague or incorrect content, the MCQs generated from it will be vague or test incorrect claims. The quality improvement loop (eval dashboard) must run BEFORE assessment is meaningful. The pipeline is:

```
Generate content → Evaluate quality → Fix quality issues → THEN generate MCQs
```

MCQs should be regenerated whenever topic content is regenerated.

### 9.2 Research Opportunities Must Be Honest

The system should NOT generate a research direction just because the user explored some topics. Requirements:
- At least 2 topics at `can_build_on` or higher that share graph edges
- At least 1 open problem in those topics
- No fundamental misconceptions in the relevant sub-concepts
- If these conditions aren't met, show "Keep exploring — you need deeper understanding in X, Y to identify research directions"

### 9.3 The Scaffold Is a Starting Point, Not a Paper

The scaffold should explicitly state:
- "This is a research direction sketch, not a finished proposal"
- "You still need to: read the cited papers, verify the gap exists, prototype the method"
- "Readiness score reflects YOUR understanding, not the idea's merit"

### 9.4 Venue Suggestions Are Directional

The system should suggest venues based on topic area and contribution type, but always caveat that venue fit depends on execution quality, timing, and reviewer assignment — things no system can predict.

---

## 10. Success Criteria

| Metric | Target | How Measured |
|--------|--------|--------------|
| MCQ completion rate | 70%+ of explored topics have MCQ attempts | DB query |
| Misconception resolution | 60%+ resolved within 3 sessions | DB query |
| Research directions surfaced | >= 1 per 10 topics at deep+ | Algorithm output |
| "I know what to write about" | User reports clarity after Phase 4 | Qualitative |
| Scaffold-to-actual-writing | >= 1 scaffold exported/continued outside Gurukul | User action |
| Time from first topic to first scaffold | < 2 hours of active use | Session tracking |

---

## 11. What This PRD Does NOT Cover

- **Actually writing the paper** — Gurukul gets you to a scaffold. Writing is your job (perhaps with an LLM writing assistant, but that's a different tool).
- **Running experiments** — The scaffold suggests experiments, but execution requires compute, code, datasets.
- **Peer review simulation** — Interesting future direction: have agents review your draft as if they were NeurIPS reviewers.
- **Citation graph analysis** — Using real citation data to validate novelty claims. Requires web search or Semantic Scholar API.
- **Collaboration** — Multi-user research direction co-exploration. Defer to v5.
