# Gurukul

A self-evolving knowledge graph for LLM research, powered by Teacher/Student agents on Databricks.

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
│  │   Judge)   │  │ Discovery) │  │  Model     │                 │
│  │            │  │            │  │  Specs)    │                 │
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

The eval engine scores every generated topic across 5 dimensions:

| Dimension | Method | What it measures |
|-----------|--------|-----------------|
| Epistemic Markers | Heuristic (regex + density) | Does content distinguish established facts from speculation? |
| Grounding | Heuristic (attribution check) | Are claims attributed to sources? |
| References | Heuristic + arXiv verification | Are citations real and verifiable? |
| Structure & Depth | Heuristic (field completeness, word count) | Is content deep enough for research use? |
| Research Quality | LLM-as-a-Judge (Teacher model) | Factual accuracy, comprehensiveness, research readiness |

## Project Structure

```
gurukul/
├── agent_server/           # Python backend
│   ├── start_server.py     # FastAPI + MLflow AgentServer entry point
│   ├── agent.py            # Teacher/Student agent logic, content generation
│   ├── routes.py           # REST + SSE API endpoints
│   ├── db.py               # Lakebase (Postgres) data layer
│   ├── prompts.py          # System prompts for all agents
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
│   └── start_app.py        # Unified entry point (local + Databricks Apps)
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
./deploy.sh          # Deploy to dev target
./deploy.sh prod     # Deploy to prod target
./deploy.sh dev --dry # Validate only
```

The deploy script handles everything:
1. Validates prerequisites (node, npm, uv, databricks CLI)
2. Loads `.env` configuration
3. Authenticates via Databricks CLI OAuth
4. Installs Python + Node.js dependencies
5. Builds the Vite frontend into `build/`
6. Validates the Databricks Asset Bundle
7. Deploys and starts the app, prints the live URL

### Manual deployment

```bash
uv sync && npm ci
npm run build
databricks bundle validate -t dev
databricks bundle deploy -t dev -var="tavily_api_key=$TAVILY_API_KEY"
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
| `databricks-openai` | Databricks-native OpenAI client with unified auth |
| `databricks-sdk` | Workspace API, Lakebase OAuth token generation |
| `databricks-agents` + `databricks-ai-bridge` | Agent framework integration |
| `mlflow >= 3.10` | Experiment tracking, GenAI evaluation, agent server |
| `openai-agents` | OpenAI Agents SDK for Teacher/Student/Examiner orchestration |
| `psycopg[binary,pool]` | PostgreSQL adapter with connection pooling (for Lakebase) |
| `httpx` | Async HTTP client for arXiv API and Tavily web search |
| `sse-starlette` | Server-Sent Events for real-time UI updates |
| `python-dotenv` | Environment variable loading |

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
| `LOG_LEVEL` | Optional | `databricks.yml` | Logging level (default: INFO) |

## How It Works

1. **Explore** — Enter a seed question (e.g., "LLM Agent Patterns"). The Teacher agent decomposes it into a topic graph.
2. **Learn** — Click any topic to read the Student-generated content: summaries, key aspects, experiments, references, and open problems.
3. **Visualize** — Switch to the Mind Map view to see how topics connect.
4. **Challenge** — Take MCQ quizzes or start Socratic assessment to test your understanding.
5. **Evaluate** — Open the Eval Dashboard to see quality scores across 5 dimensions. Click Re-evaluate to run the full eval pipeline.
6. **Improve** — Click Apply Fix to regenerate weak topics with targeted quality hints. Track improvement over time.
7. **Research** — Once you've mastered topics, open the Research Panel to discover research directions and generate paper scaffolds.

## License

Internal / Databricks use.
