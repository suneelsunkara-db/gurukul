# PRD: Socratic Assessment & Research Readiness Engine

**Product**: Gurukul — Self-Evolving Knowledge Graph for LLM Research  
**Version**: v3.0  
**Author**: Suneel Sunkara  
**Status**: Draft  
**Last Updated**: 2026-06-19

---

## 1. Problem Statement

Gurukul generates a rich knowledge graph with typed edges and multi-level content. But two critical gaps remain:

1. **"How do I know I truly understand a topic?"** — Self-rated confidence (1–5 stars) is subjective and unreliable. A user who rates themselves 4/5 on ReAct may not actually be able to explain why ReAct outperforms vanilla tool use in multi-step tasks. There is no mechanism to _demonstrate_ understanding.

2. **"How does exploring all this lead to a research paper?"** — The path from topic exploration to NeurIPS-like submission is ambiguous. Users accumulate knowledge but have no structured way to identify where their understanding is deep enough to contribute original work, or which topic intersections represent novel research directions.

These gaps make Gurukul a reading tool instead of a **research co-pilot**.

---

## 2. Core Idea: Replace Self-Rating with Demonstrated Understanding

Instead of asking "how confident are you?", Gurukul should **challenge the user** and determine understanding from the conversation. This is the Socratic method: the system probes, the user responds, and understanding is revealed through the dialogue — not declared.

**Understanding Levels** (system-assessed, not self-reported):

| Level | Label | Observable Criteria | What it means for research |
|-------|-------|--------------------|-----------------------------|
| 0 | Untested | User hasn't been challenged on this topic | Unknown readiness |
| 1 | Surface | Can recall definitions but not explain mechanisms | Needs more study |
| 2 | Structural | Can explain how components interact, identify tradeoffs | Can write a related-work section |
| 3 | Deep | Can teach it, apply it to novel scenarios, identify its limitations | Can write methodology that builds on this |
| 4 | Creative | Can propose improvements, spot unexplored directions, argue both sides | Ready to contribute original work here |

---

## 3. Feature 1: "Challenge Me" — Socratic Assessment

### 3.1 How It Works

A "Challenge Me" button appears at the bottom of every topic content view. When clicked:

1. **The agent generates a question** calibrated to the topic, starting at the structural level
2. **The user types a free-form answer** (not multiple choice — that's too easy to game)
3. **The agent evaluates** the answer for accuracy, depth, and reasoning quality
4. **The agent responds** with:
   - What the user got right (specific praise)
   - What was missing or inaccurate (specific correction)
   - A follow-up question that probes deeper (if the answer was good) or re-approaches from a different angle (if weak)
5. **The dialogue continues** (3–5 rounds) until the agent can confidently assign an understanding level
6. **Understanding level is persisted** in Lakebase and reflected on the mind map

### 3.2 Question Modes

The agent cycles through different question modes to test different dimensions of understanding:

| Mode | Purpose | Example (topic: ReAct) |
|------|---------|------------------------|
| **Explain** | Test conceptual grasp | "Walk me through what happens when a ReAct agent encounters a tool call failure mid-chain. What does it do differently from a simple sequential planner?" |
| **Apply** | Test transfer to novel scenarios | "You're building a customer support agent that needs to check inventory, process refunds, and update CRM records. How would you structure the ReAct loop? Where might it struggle?" |
| **Contrast** | Test awareness of tradeoffs | "Your colleague argues that Plan-and-Execute with re-planning is strictly better than ReAct because it avoids redundant observations. How would you respond?" |
| **Teach-back** | Test depth of reorganized knowledge | "Explain ReAct to someone who understands chain-of-thought prompting but has never heard of tool-augmented agents. What would you build up from?" |
| **Debug** | Test mechanistic understanding | "A ReAct agent keeps calling the same tool in a loop. The LLM is GPT-4. What are the three most likely causes, and how would you diagnose each?" |

### 3.3 Evaluation Rubric (Agent Instructions)

The evaluator agent uses a structured rubric, not vibes:

```
ACCURACY (0-3):
  0 = Fundamentally wrong
  1 = Partially correct but key errors
  2 = Mostly correct with minor gaps
  3 = Fully accurate

DEPTH (0-3):
  0 = Surface-level recall only
  1 = Can describe what, not why
  2 = Explains mechanisms, tradeoffs, limitations
  3 = Proposes improvements, connects to broader context

REASONING (0-2):
  0 = No logical structure
  1 = Logical but follows standard patterns
  2 = Shows original thinking or synthesis

UNDERSTANDING_LEVEL = f(accuracy, depth, reasoning):
  surface    if accuracy < 2 OR depth < 1
  structural if accuracy >= 2 AND depth >= 1
  deep       if accuracy >= 2 AND depth >= 2 AND reasoning >= 1
  creative   if accuracy >= 3 AND depth >= 3 AND reasoning >= 2
```

### 3.4 UI Design

The challenge panel replaces the old Critique + ResearchSeed + ConfidenceTracker section at the bottom of TopicContent:

```
┌─────────────────────────────────────────────┐
│  [Challenge Me on: ReAct]                    │
│                                              │
│  🤔 Agent: Walk me through what happens      │
│     when a ReAct agent encounters a tool     │
│     call failure mid-chain...                │
│                                              │
│  ┌─────────────────────────────────────────┐ │
│  │ Your answer...                          │ │
│  │                                         │ │
│  └─────────────────────────────────────────┘ │
│  [Submit Answer]                             │
│                                              │
│  ───────────────────────────────────────────  │
│  Agent: Good — you correctly identified      │
│  the observation-action loop. But you        │
│  missed that ReAct doesn't have a built-in   │
│  retry mechanism...                          │
│                                              │
│  Follow-up: Given that limitation, how       │
│  would you modify the ReAct loop to handle   │
│  transient API failures?                     │
│                                              │
│  ───────────────────────────────────────────  │
│  Understanding: ██████░░ Structural          │
│  (2 more rounds to potentially reach Deep)   │
└─────────────────────────────────────────────┘
```

### 3.5 Data Model

```sql
CREATE TABLE IF NOT EXISTS gurukul.challenge_sessions (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'default',
    topic_id     TEXT NOT NULL REFERENCES gurukul.topics(id),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    rounds       JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Each round: {question, mode, answer, evaluation: {accuracy, depth, reasoning, feedback}}
    final_level  TEXT,  -- surface | structural | deep | creative
    final_scores JSONB  -- {accuracy: 2.5, depth: 2.0, reasoning: 1.5}
);

CREATE INDEX IF NOT EXISTS idx_challenge_user_topic
    ON gurukul.challenge_sessions(user_id, topic_id);
```

### 3.6 API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/challenge/:topicId/start` | POST | Start a new challenge session; returns first question |
| `POST /api/challenge/:sessionId/answer` | POST | Submit answer; returns evaluation + follow-up question |
| `GET /api/challenge/:topicId/history` | GET | Past challenge sessions for a topic |
| `GET /api/understanding` | GET | Map of topic_id → understanding level |

---

## 4. Feature 2: Cross-Topic Synthesis Challenges

### 4.1 Why This Matters

Understanding individual topics is necessary but not sufficient for research. The ability to **connect concepts across topics** is what separates a reader from a researcher.

### 4.2 How It Works

When the user has demonstrated `structural` or higher understanding in 3+ related topics, the system unlocks synthesis challenges:

1. The agent picks 2-3 topics connected by typed edges (especially `builds_on` and `contrasts` edges)
2. Generates a scenario that requires combining concepts from all selected topics
3. Evaluates whether the user can reason across topic boundaries

**Example**: Topics "ReAct", "Multi-Agent Orchestration", "Tool Use Patterns"

> "You're designing a system where 5 specialized agents need to collaborate on a complex research task. Each agent can use different tools. Agent A discovers that the tool it needs is currently rate-limited. Using what you know about ReAct, multi-agent orchestration, and tool use patterns — describe how the system should handle this. What pattern would you use at the individual agent level? What about at the orchestration level? Where do these patterns conflict?"

### 4.3 Integration with Knowledge Graph

Synthesis challenges are generated using the typed edge graph:

- `prerequisite` edges → test if understanding of A was correctly applied to reasoning about B
- `contrasts` edges → test if user can articulate why approach A was chosen over B
- `builds_on` edges → test if user understands what B adds beyond A

### 4.4 Data Model

Uses the same `challenge_sessions` table with `topic_id` set to a composite key (e.g., `synthesis:react+multi-agent+tool-use`) and `rounds[].mode = 'synthesis'`.

---

## 5. Feature 3: Misconception Tracking

### 5.1 Purpose

Track specific errors and misconceptions across challenge sessions. This is far more useful than a numeric score because it tells the user _what_ they don't understand, not just _how much_.

### 5.2 How It Works

During evaluation, the agent flags specific misconceptions:

```json
{
  "evaluation": {
    "accuracy": 1,
    "misconceptions": [
      {
        "claim": "ReAct agents maintain a plan that they update",
        "correction": "ReAct agents don't maintain an explicit plan — they alternate between thought/action/observation without a persistent plan structure. You may be confusing this with Plan-and-Execute.",
        "related_topics": ["plan-and-execute", "react-pattern"],
        "severity": "conceptual"
      }
    ]
  }
}
```

### 5.3 Misconception Persistence

```sql
CREATE TABLE IF NOT EXISTS gurukul.misconceptions (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'default',
    topic_id     TEXT NOT NULL,
    claim        TEXT NOT NULL,
    correction   TEXT NOT NULL,
    related_topics TEXT[] DEFAULT '{}',
    severity     TEXT NOT NULL DEFAULT 'minor',  -- minor | conceptual | fundamental
    occurrences  INTEGER NOT NULL DEFAULT 1,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at  TIMESTAMPTZ,
    session_id   INTEGER REFERENCES gurukul.challenge_sessions(id)
);
```

### 5.4 UI Surface

A collapsible "Things to revisit" panel in the sidebar:

```
┌─────────────────────────────────────────┐
│  ⚠ Recurring misconceptions (2)        │
│                                         │
│  • You've confused ReAct with Plan-and- │
│    Execute in 3 sessions. ReAct doesn't │
│    maintain an explicit plan.           │
│    [Review: ReAct] [Review: PaE]        │
│                                         │
│  • You describe attention as "matching  │
│    queries to keys" but miss the value  │
│    projection step. Seen in 2 sessions. │
│    [Review: Attention Mechanisms]        │
└─────────────────────────────────────────┘
```

---

## 6. Feature 4: Research Readiness Map

### 6.1 Purpose

This is the concrete bridge between "I've been learning" and "I can write a paper." Instead of vague confidence scores, the research readiness map shows:

- **Where you're strong enough** to contribute original work (topics at `deep` or `creative` level)
- **What's blocking you** from reaching that level elsewhere (misconceptions, untested prerequisites)
- **Which intersections** between your strong topics represent potential research directions

### 6.2 How It Works

```
Research Readiness = f(understanding_levels, knowledge_graph, open_problems)

For each topic cluster where user has 2+ topics at deep/creative:
  1. Identify open problems in those topics (from generated content)
  2. Find edges between the strong topics (especially contrasts + builds_on)
  3. Generate intersection hypotheses:
     "You deeply understand both RLHF and DPO (which contrast each other).
      Open problem: no method combines the stability of DPO with the
      expressiveness of reward modeling from RLHF.
      Potential direction: Hybrid reward-free alignment with learned
      preference boundaries."
  4. Score by: user understanding depth × problem novelty × feasibility
```

### 6.3 UI: Research Readiness Panel

A right-panel view (toggled via the top bar or mind map):

```
┌────────────────────────────────────────────────┐
│  Research Readiness                            │
│                                                │
│  ████████████░░░░░ 68% of graph assessed       │
│                                                │
│  ── Strong Areas (can contribute) ────────────  │
│  • Agent Architectures: deep (ReAct, PaE,      │
│    Multi-Agent) — 3 open problems identified   │
│  • Attention Mechanisms: creative (Attention,   │
│    MHA, Flash Attention) — 2 intersections      │
│                                                │
│  ── Blocking Gaps ──────────────────────────── │
│  • "Reward Modeling" untested — prerequisite    │
│    for 4 alignment topics                      │
│  • Misconception in "KV-Cache" blocking path   │
│    to Inference Optimization cluster           │
│                                                │
│  ── Potential Research Directions ────────────  │
│  1. "Adaptive ReAct with learned action        │
│     spaces" — intersection of your deep        │
│     understanding of ReAct + RL exploration    │
│     strategies. No existing work combines      │
│     these. [Generate Draft Outline]            │
│                                                │
│  2. "Flash Attention for sparse mixture-of-    │
│     experts routing" — you understand both     │
│     deeply and there's an open efficiency      │
│     gap. [Generate Draft Outline]              │
└────────────────────────────────────────────────┘
```

### 6.4 "Generate Draft Outline" Flow

When a user clicks "Generate Draft Outline" on a research direction:

1. The Teacher agent generates a structured paper outline:
   - **Title** (working)
   - **Abstract** (2-3 sentences positioning the contribution)
   - **Key hypothesis**
   - **Required background** (linked to topics the user has demonstrated understanding of)
   - **Proposed method** (sketch)
   - **Expected experiments** (what to benchmark against)
   - **Potential venues** (NeurIPS, ICML, ICLR, etc. with reasoning)

2. This outline is persisted and editable
3. The user can iterate on it through dialogue with the agent

### 6.5 API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /api/readiness` | GET | Full research readiness assessment |
| `GET /api/readiness/directions` | GET | Ranked list of potential research directions |
| `POST /api/readiness/outline` | POST | Generate paper outline for a specific direction |

---

## 7. Feature 5: Knowledge Decay & Re-Assessment

### 7.1 Purpose

Understanding fades. A topic assessed as `deep` two weeks ago may be `structural` today. Periodic re-assessment keeps the readiness map honest.

### 7.2 Mechanics

- After 7 days, a `deep`/`creative` assessment shows a subtle "Re-assess?" indicator
- After 14 days, the understanding level shows as "stale" (dimmed color on mind map)
- The re-assessment is shorter (2-3 rounds) since there's a prior baseline
- If the user drops a level, the system adjusts research directions accordingly

### 7.3 UI

Stale topics show a clock icon in the sidebar and a dotted border on the mind map. Not aggressive — just a gentle nudge.

---

## 8. Removed / Deprecated Features

The following components are removed in v3 as they are superseded by the Socratic direction:

| Removed | Replacement |
|---------|-------------|
| `ConfidenceTracker` (1-5 self-rating) | Challenge Me (system-assessed understanding) |
| `Critique` (free-text pushback in localStorage) | Socratic dialogue captures disagreements structurally |
| `ResearchSeed` (manual "half-formed ideas" in localStorage) | Research Readiness Map generates specific directions from demonstrated understanding |
| `/proposal` page (standalone proposal generator) | "Generate Draft Outline" from research readiness |
| `/author` page (authoring dashboard) | Merged into main Explore page |
| `/journey-dashboard` page (old CLI-based dashboard) | Research Readiness panel in Explore page |

---

## 9. Data Model Summary

### New Tables

```sql
-- Socratic challenge sessions
CREATE TABLE IF NOT EXISTS gurukul.challenge_sessions (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'default',
    topic_id     TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    rounds       JSONB NOT NULL DEFAULT '[]'::jsonb,
    final_level  TEXT,
    final_scores JSONB
);

-- Tracked misconceptions across sessions
CREATE TABLE IF NOT EXISTS gurukul.misconceptions (
    id             SERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    topic_id       TEXT NOT NULL,
    claim          TEXT NOT NULL,
    correction     TEXT NOT NULL,
    related_topics TEXT[] DEFAULT '{}',
    severity       TEXT NOT NULL DEFAULT 'minor',
    occurrences    INTEGER NOT NULL DEFAULT 1,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at    TIMESTAMPTZ,
    session_id     INTEGER REFERENCES gurukul.challenge_sessions(id)
);

-- Research direction outlines
CREATE TABLE IF NOT EXISTS gurukul.research_directions (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    title           TEXT NOT NULL,
    source_topics   TEXT[] NOT NULL,
    hypothesis      TEXT,
    outline         JSONB,
    readiness_score REAL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_challenge_user_topic
    ON gurukul.challenge_sessions(user_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_misconceptions_user
    ON gurukul.misconceptions(user_id, resolved_at);
CREATE INDEX IF NOT EXISTS idx_research_user
    ON gurukul.research_directions(user_id);
```

### Existing Tables (Unchanged)

- `gurukul.topics` — topic content storage
- `gurukul.graph_edges` — typed knowledge graph edges (prerequisite, builds_on, etc.)
- `gurukul.explorations` — seed topic history
- `gurukul.annotations` — generic annotations (kept for notes/bookmarks, no longer used for confidence)

---

## 10. Implementation Phases

### Phase 1: Challenge Me — Core Socratic Loop (2 weeks)

- [ ] Schema: create `challenge_sessions` table
- [ ] Backend: `POST /api/challenge/:topicId/start` — generate first question using topic content + rubric
- [ ] Backend: `POST /api/challenge/:sessionId/answer` — evaluate answer, generate follow-up or final assessment
- [ ] Backend: `GET /api/understanding` — aggregate understanding levels across topics
- [ ] Frontend: `ChallengePanel` component — conversational UI in TopicContent
- [ ] Frontend: understanding level badges on mind map nodes (replaces confidence dots)
- [ ] Frontend: understanding indicators in TopicTree sidebar

### Phase 2: Cross-Topic Synthesis (1 week)

- [ ] Backend: synthesis challenge generation using typed edge graph
- [ ] Frontend: "Synthesis Challenge" button when 3+ related topics are at structural+
- [ ] Backend: evaluation with cross-topic rubric

### Phase 3: Misconception Tracking (1 week)

- [ ] Schema: create `misconceptions` table
- [ ] Backend: extract misconceptions during challenge evaluation
- [ ] Backend: deduplicate and track occurrences across sessions
- [ ] Frontend: "Things to revisit" sidebar panel
- [ ] Frontend: link misconceptions to relevant topics

### Phase 4: Research Readiness Map (2 weeks)

- [ ] Schema: create `research_directions` table
- [ ] Backend: readiness assessment algorithm (understanding × graph × open problems)
- [ ] Backend: intersection mining — find novel research directions from strong-topic clusters
- [ ] Backend: `POST /api/readiness/outline` — generate paper outline
- [ ] Frontend: Research Readiness panel (right-side view)
- [ ] Frontend: "Generate Draft Outline" flow with agent dialogue

### Phase 5: Knowledge Decay (1 week)

- [ ] Backend: staleness detection based on last assessment timestamp
- [ ] Frontend: stale indicators on mind map and sidebar
- [ ] Backend: shortened re-assessment flow (2-3 rounds using prior baseline)

---

## 11. Technical Considerations

### Agent Architecture

The Socratic assessment requires a specialized evaluator prompt, separate from the Teacher and Student prompts. Three agent roles:

| Role | Model | Purpose |
|------|-------|---------|
| Teacher | `databricks-gpt-5-5` | Topic decomposition, edge generation |
| Student | `databricks-claude-sonnet-4-6` | Content generation |
| Examiner | `databricks-claude-sonnet-4-6` | Challenge question generation, answer evaluation, misconception extraction |

The Examiner is a new role. It needs access to:
- The full topic content (to generate relevant questions)
- The user's prior challenge history (to avoid repeating questions)
- The knowledge graph (for synthesis challenges)
- The evaluation rubric (structured in the system prompt)

### Streaming

Challenge responses should stream via SSE so the user sees the evaluation appearing in real time, just like topic content generation.

### Cost Management

- Each challenge session: ~5-8 agent calls (questions + evaluations)
- At `databricks-claude-sonnet-4-6` pricing, ~$0.02-0.05 per session
- Rate limit: max 3 active challenge sessions per hour
- Cache questions: if user restarts a challenge on the same topic within 24h, vary the questions

### Lakebase Performance

- Challenge sessions are write-heavy but small (JSONB rounds)
- Misconception queries need `WHERE resolved_at IS NULL AND user_id = ?` — covered by index
- Research direction generation is infrequent (on-demand only)

---

## 12. Success Criteria

| Metric | Target |
|--------|--------|
| Users complete challenge sessions on 60%+ of explored topics | System-assessed understanding replaces self-rating |
| Misconception resolution rate: 70%+ resolved within 3 sessions | Users are actively fixing gaps |
| At least 1 research direction generated per 15 topics at deep+ | The system actually surfaces paper ideas |
| Users who complete Phase 4 flow report "I know what to write about" | Qualitative — the ambiguity is gone |
| Mind map understanding overlay is more informative than old confidence dots | Users prefer system-assessed levels |

---

## 13. Open Questions

1. **Examiner prompt quality**: Can a single prompt handle all 5 question modes well, or do we need mode-specific prompts?
2. **Cheating resistance**: Users could paste topic content into their answers. Should the Examiner detect this? (Probably: ask "in your own words" and penalize verbatim content.)
3. **Multi-language support**: Should challenges work in languages other than English? (Defer to v4.)
4. **Collaboration**: Could two users challenge each other on the same topic? (Interesting for v4 — peer assessment.)
5. **External grounding**: Should the Examiner verify that its own questions/evaluations are factually correct? (Use topic content as ground truth for now; RAG from papers in v4.)
