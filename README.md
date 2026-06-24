# Gurukul

Mentorship-Driven Learning for AI Researchers, powered by Teacher/Student agents on Databricks.

Gurukul (Sanskrit for "place of learning") is an interactive research exploration platform that helps you deeply understand LLM concepts — from transformers and attention to frontier model architectures — and guides you toward publishing your own research paper.

## The Idea

LLM research moves fast. Papers, architectures, and models evolve weekly. Gurukul tackles this by generating a **living knowledge graph** of topics that grows as you explore. Instead of reading static summaries, you interact with an agentic system that:

1. **Decomposes** your question into a structured topic graph (Teacher agent)
2. **Generates** deep, research-grade content for each topic (Student agent)
3. **Challenges** your understanding with Socratic assessment and MCQ quizzes (Examiner agent)
4. **Evaluates** its own output for factual accuracy, grounding, and research quality (LLM-as-a-Judge)
5. **Iteratively improves** weak content through an automated feedback loop
6. **Grounds** model comparisons in real-time data via web search (Tavily) and verified papers (arXiv API)
7. **Guides** you from topic mastery to research paper scaffolding

The end goal: go from "What is ReAct?" to a NeurIPS-ready paper outline, with every step validated.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Gurukul UI (React + Vite)                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ Topic    │  │ Mind Map │  │ Eval     │  │ Research Panel │  │
│  │ Explorer │  │ (React   │  │ Dashboard│  │ (Competence →  │  │
│  │ + Content│  │  Flow)   │  │          │  │  Directions →  │  │
│  │          │  │          │  │          │  │  Paper Scaffold│  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ /api/* (SSE + REST)
┌──────────────────────────┴──────────────────────────────────────┐
│                   FastAPI Agent Server (Python)                  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  MLflow AgentServer (OpenAI Agents SDK)                  │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐               │   │
│  │  │ Teacher  │  │ Student  │  │ Examiner │               │   │
│  │  │ Agent    │  │ Agent    │  │ Agent    │               │   │
│  │  │ (GPT-5.5)│  │ (Claude  │  │ (GPT-5.5)│               │   │
│  │  │          │  │  Sonnet) │  │          │               │   │
│  │  └──────────┘  └──────────┘  └──────────┘               │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                 │
│  │ Eval Engine│  │ arXiv API  │  │ Tavily Web │                 │
│  │ (Heuristic │  │ (Paper     │  │ Search     │                 │
│  │ + LLM      │  │ Verify +   │  │ (Real-time │                 │
│  │   Judge +  │  │ Discovery) │  │  Model     │                 │
│  │ Calibrate) │  │            │  │  Specs)    │                 │
│  └────────────┘  └────────────┘  └────────────┘                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │  Lakebase (Managed      │
              │  Postgres on Databricks)│
              │                         │
              │  • topics & payloads    │
              │  • graph edges          │
              │  • challenge sessions   │
              │  • MCQ questions/answers│
              │  • eval runs & actions  │
              │  • quality learnings    │
              │  • judge calibration    │
              │  • misconceptions       │
              └─────────────────────────┘
```

### Agent Roles

| Agent | Model | Role |
|-------|-------|------|
| **Teacher** | `databricks-gpt-5-5` | Decomposes questions into topic graphs, judges content quality, generates research directions |
| **Student** | `databricks-claude-sonnet-4-6` | Generates deep, structured content for each topic with epistemic markers and references |
| **Examiner** | `databricks-gpt-5-5` | Creates MCQ quizzes, runs Socratic assessment, identifies misconceptions |

### Data Sources

| Source | Purpose | Integration |
|--------|---------|-------------|
| **Databricks FM Serving** | All LLM inference (content generation, evaluation, assessment) | `databricks-openai` SDK with unified OAuth auth |
| **arXiv API** | Paper verification, reference discovery, recent paper search | `agent_server/arxiv.py` — batch verification, TTL cache |
| **Tavily Web Search** | Real-time model specs for comparison chapters (beyond training cutoff) | `agent_server/web_search.py` — parallel family search |
| **Lakebase (Postgres)** | All persistent state: topics, graphs, evals, challenges, learnings | `agent_server/db.py` — `psycopg` with OAuth token rotation |

### Evaluation Dimensions

The eval engine scores every generated topic across **8 dimensions** — 4 cheap heuristics and 4 LLM-as-a-Judge dimensions:

| Dimension | Method | What it measures |
|-----------|--------|-----------------|
| Grounding | Heuristic (attribution check) | Are claims attributed to sources and properly hedged? |
| References | Heuristic + arXiv verification | Are citations real, complete, and sufficient? |
| Structure | Heuristic (field completeness) | Is content structurally complete with all required fields? |
| Epistemic Markers | Heuristic (regex + density) | Does content signal confidence levels for claims? |
| Factual Accuracy | LLM-as-a-Judge (Teacher model) | Are claims verifiable and correct against known literature? |
| Comprehensiveness | LLM-as-a-Judge | Does it cover all key aspects, trade-offs, and limitations? |
| Technical Depth | LLM-as-a-Judge | Does it explain mechanisms (why, not just what), edge cases, failure modes? |
| Research Readiness | LLM-as-a-Judge | Could someone write a related-works section or identify gaps from this? |

The judge is hardened against noise:

- **Strict structured outputs** — every judge (and generator) call is constrained by a JSON schema (`agent_server/schemas.py`), so responses are always valid, complete JSON with all dimensions present. No omitted keys, prose-wrapping, or mid-JSON truncation.
- **Self-consistency** — each dimension is sampled `JUDGE_SAMPLES` times (default 3) and the **median** is taken to damp judge variance. A failed dimension scores `None`, never a silent fallback.
- **Ablation self-test (calibration)** — `/eval/calibrate` deliberately degrades content per dimension and re-judges it; a dimension is only trusted ("calibrated") if its score drops by a threshold. Results persist in the `judge_calibration` table.
- **Learning gate** — the improvement loop only persists a learning for a dimension when the judge is calibrated for it *and* the measured delta is positive, so the system never learns from an uncalibrated signal.

## Project Structure

```
gurukul/
├── agent_server/           # Python backend
│   ├── start_server.py     # FastAPI + MLflow AgentServer entry point
│   ├── agent.py            # Teacher/Student agent logic, content generation
│   ├── routes.py           # REST + SSE API endpoints
│   ├── db.py               # Lakebase (Postgres) data layer
│   ├── prompts.py          # System prompts for all agents
│   ├── schemas.py          # Strict JSON schemas for structured LLM outputs
│   ├── arxiv.py            # arXiv API client (search, verify, batch)
│   ├── web_search.py       # Tavily web search for real-time model specs
│   ├── guardrails.py       # Post-generation content validation
│   ├── sse.py              # Server-Sent Events broadcast
│   └── utils.py            # JSON extraction helpers
├── evals/                  # Evaluation harness
│   ├── run_eval.py         # CLI: uv run gurukul-eval
│   ├── scorers.py          # MLflow GenAI custom scorers
│   └── datasets.py         # Eval dataset builders
├── scripts/
│   ├── start_app.py        # Unified entry point (local + Databricks Apps)
│   └── schema.sql          # Reference SQL schema for Lakebase tables
├── src/                    # React frontend
│   ├── pages/index.tsx     # Main page (SSE, routing, state)
│   ├── components/         # 14 UI components
│   │   ├── TopicTree.tsx   # Sidebar topic browser
│   │   ├── TopicContent.tsx# Content renderer
│   │   ├── MindMap.tsx     # React Flow knowledge graph
│   │   ├── EvalDashboard.tsx # Evaluation metrics + improvement loop
│   │   ├── ChallengePanel.tsx # MCQ + Socratic assessment
│   │   ├── ResearchPanel.tsx  # Research paper pipeline
│   │   ├── References.tsx  # arXiv-verified citations
│   │   └── ...             # Summary, KeyAspect, Experiment, etc.
│   └── css/custom.css      # Styles
├── databricks.yml          # Databricks Asset Bundle config
├── deploy.sh               # Zero-touch Databricks App deployment
├── setup.sh                # Interactive setup (local/deploy/eval)
├── .env.example            # Environment variable template
├── package.json            # Node.js dependencies
└── pyproject.toml          # Python dependencies
```

## Prerequisites

- **Python 3.11+**
- **Node.js 20+** and npm
- **uv** — Python package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **Databricks CLI** — for authentication and deployment (`brew install databricks`)
- **Databricks workspace** with:
  - Model serving endpoints (`databricks-gpt-5-5`, `databricks-claude-sonnet-4-6` or equivalents)
  - Lakebase (Autoscaling Postgres) project with an endpoint
- **Tavily API key** (free at [tavily.com](https://tavily.com)) — for real-time web search grounding

## Local Development Setup

### 1. Clone and configure

```bash
git clone <repo-url> && cd gurukul
cp .env.example .env
```

Edit `.env` with your values:

```env
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
DATABRICKS_CONFIG_PROFILE=your-profile

TEACHER_MODEL=databricks-gpt-5-5
STUDENT_MODEL=databricks-claude-sonnet-4-6

PGHOST=your-lakebase-endpoint.database.us-east-1.cloud.databricks.com
PGDATABASE=databricks_postgres
PGUSER=your.email@databricks.com
ENDPOINT_NAME=projects/your-project/branches/production/endpoints/primary

TAVILY_API_KEY=tvly-your-key-here

AGENT_CONCURRENCY=6
```

### 2. Authenticate with Databricks

```bash
databricks auth login --host $DATABRICKS_HOST
databricks auth token --host $DATABRICKS_HOST   # verify
```

### 3. Install dependencies

```bash
uv sync          # Python
npm ci           # Node.js
```

### 4. Start the dev servers

```bash
npm run dev
```

This starts:
- **Vite** frontend at `http://localhost:3000`
- **FastAPI** agent server at `http://localhost:8000`
- Vite proxies `/api/*` to the backend automatically

Open `http://localhost:3000` in your browser.

### 5. Run evaluations (optional)

```bash
uv run gurukul-eval            # All evaluations
uv run gurukul-eval student    # Student content only
uv run gurukul-eval teacher    # Teacher graph quality
uv run gurukul-eval examiner   # Examiner fairness
```

Or use the interactive setup script:

```bash
chmod +x setup.sh
./setup.sh local
```

## Databricks App Deployment

### One-command deploy

```bash
chmod +x deploy.sh
./deploy.sh          # Build + deploy to Databricks Apps
./deploy.sh --dry    # Build + validate only, don't deploy
```

The deploy script is zero-touch and handles everything in 8 steps:
1. Validates prerequisites (node, npm, uv, databricks CLI)
2. Loads `.env` and authenticates via Databricks CLI OAuth (launches login if needed)
3. Builds the Vite frontend locally into `build/` (no npm runs on the platform)
4. Sets up the Lakebase schema and grants permissions to all roles
5. Creates the `gurukul` secret scope, stores `TAVILY_API_KEY`, and attaches all app resources (Postgres, Teacher/Student serving endpoints, secret)
6. Uploads the staged source + pre-built frontend to the workspace
7. Deploys the app via `databricks apps deploy`
8. Prints the live app URL

### Manual deployment (Databricks Asset Bundle)

`databricks.yml` defines a single `prod` target (the default). The Tavily key is read from a secret scope, never passed inline:

```bash
uv sync && npm ci
npm run build

# one-time: create the secret scope the app references via valueFrom
databricks secrets create-scope gurukul
databricks secrets put-secret gurukul tavily_api_key --string-value "$TAVILY_API_KEY"

databricks bundle validate
databricks bundle deploy
databricks apps start gurukul
```

### Post-deploy

```bash
databricks apps get gurukul        # App status & URL
databricks apps logs gurukul       # View logs
databricks apps stop gurukul       # Stop the app
```

## Dependencies

### Python (managed by uv)

| Package | Purpose |
|---------|---------|
| `fastapi` + `uvicorn` | Web framework and ASGI server |
| `databricks-openai[memory]` | Databricks-native OpenAI client with unified auth |
| `databricks-sdk` | Workspace API, Lakebase OAuth token generation |
| `databricks-agents` + `databricks-ai-bridge[agent-server]` | Agent framework integration |
| `mlflow >= 3.10` | Experiment tracking, GenAI evaluation, agent server |
| `openai-agents` | OpenAI Agents SDK for Teacher/Student/Examiner orchestration |
| `psycopg[binary,pool]` | PostgreSQL adapter with connection pooling (for Lakebase) |
| `httpx` | Async HTTP client for arXiv API and Tavily web search |
| `sse-starlette` | Server-Sent Events for real-time UI updates |
| `python-dotenv` | Environment variable loading |
| `uuid-utils` | Fast UUID generation for topic/session IDs |
| `opentelemetry-exporter-otlp-proto-grpc` | OTLP trace export for MLflow tracing |

### Node.js (managed by npm)

| Package | Purpose |
|---------|---------|
| `react` + `react-dom` | UI framework |
| `@xyflow/react` | Interactive mind map / knowledge graph |
| `vite` | Build tool and dev server |
| `typescript` | Type checking |

## Configuration Reference

| Variable | Required | Where | Description |
|----------|----------|-------|-------------|
| `DATABRICKS_HOST` | Yes | `.env` | Workspace URL |
| `DATABRICKS_CONFIG_PROFILE` | Local only | `.env` | CLI auth profile name |
| `TEACHER_MODEL` | Yes | `.env` + `databricks.yml` | Teacher/Judge model endpoint |
| `STUDENT_MODEL` | Yes | `.env` + `databricks.yml` | Student model endpoint |
| `PGHOST` | Yes | `.env` + `databricks.yml` | Lakebase endpoint hostname |
| `PGDATABASE` | Yes | `.env` + `databricks.yml` | Database name (default: `databricks_postgres`) |
| `PGUSER` | Yes | `.env` + `databricks.yml` | Databricks email (local) or app client ID (deployed) |
| `ENDPOINT_NAME` | Yes | `.env` + `databricks.yml` | Lakebase endpoint resource path |
| `TAVILY_API_KEY` | Optional | `.env` + `databricks.yml` | Tavily API key for web search grounding |
| `AGENT_CONCURRENCY` | Optional | `.env` + `databricks.yml` | Max concurrent Student agents (default: 4) |
| `JUDGE_SAMPLES` | Optional | `.env` | LLM-judge samples per dimension for self-consistency (default: 3) |
| `MLFLOW_TRACKING_URI` | Optional | `databricks.yml` | MLflow tracking backend (set to `databricks` when deployed) |
| `LOG_LEVEL` | Optional | `databricks.yml` | Logging level (default: INFO) |

## How It Works

1. **Explore** — Enter a seed question (e.g., "LLM Agent Patterns"). The Teacher agent decomposes it into a topic graph.
2. **Learn** — Click any topic to read the Student-generated content: summaries, key aspects, experiments, references, and open problems.
3. **Visualize** — Switch to the Mind Map view to see how topics connect.
4. **Challenge** — Take MCQ quizzes or start Socratic assessment to test your understanding.
5. **Evaluate** — Open the Eval Dashboard to see quality scores across 8 dimensions, plus the judge's calibration status. Click Re-evaluate to run the full eval pipeline, or Run self-test to calibrate the judge.
6. **Improve** — Click Apply Fix to regenerate weak topics with targeted quality hints. Learnings are only kept for calibrated dimensions with positive deltas, so improvement is tracked against a trusted signal.
7. **Research** — Once you've mastered topics, open the Research Panel to discover research directions and generate paper scaffolds.

## License

Internal / Databricks use.
