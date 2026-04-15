# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**WebClaw** — a RAG-enhanced LLM-driven automated penetration testing platform. Entry for the 17th China University Students Service Outsourcing Innovation & Entrepreneurship Competition (Topic A10).

The platform orchestrates security tools via LLM decision-making, augmented by a continuously updated Qdrant vector knowledge base populated by the OpenClaw agent crawler.

## Commands

### RAG Knowledge Crawler

```bash
# Install Python dependencies
pip install -r rag_crawler/requirements.txt

# Run the main knowledge acquisition crawler
cd rag_crawler && python crawler.py

# Skip LLM calls (raw fetch + ingest only)
OPENCLAW_SKIP_LLM=1 python rag_crawler/crawler.py

# Single-topic run
python rag_crawler/crawler.py --topic "Struts2 S2-045"
```

### RAG Skills (Node.js)

```bash
# Install dependencies for each skill
cd skills/rag-query && npm install
cd skills/rag-ingest && npm install

# Semantic search
node skills/rag-query/scripts/query.mjs "渗透测试流程"
node skills/rag-query/scripts/query.mjs --query "CVE-2017-5638" --top-k 5 --topic-tags "cve,poc"

# Ingest a document
node skills/rag-ingest/scripts/ingest.mjs --input <file_or_text>
```

### Content Utilities

```bash
python clean_content.py [input_file]
python clean_skills.py
```

## Environment Configuration

Copy and fill `rag_crawler/env.example` → `rag_crawler/.env`.

| Variable | Used By | Notes |
|---|---|---|
| `OPENAI_API_KEY` | crawler.py, rag-query, rag-ingest | Also fallback for embedding |
| `OPENAI_BASE_URL` | crawler.py | Defaults to `https://api.openai.com/v1` |
| `OPENAI_MODEL` | crawler.py | Defaults to `gpt-4o-mini` |
| `TAVILY_API_KEY` | crawler.py | Required for web search |
| `QDRANT_URL` | rag-query, rag-ingest | Defaults to `http://127.0.0.1:6333` |
| `EMBED_BASE_URL` | rag-query, rag-ingest | Defaults to VectorEngine API |
| `EMBED_API_KEY` / `VECTORENGINE_API_KEY` | rag-query, rag-ingest | Falls back to `OPENAI_API_KEY` |
| `RAG_INGEST_EMBED_MODEL` | rag-query, rag-ingest | Defaults to `text-embedding-3-large` |

## Architecture

Five modules communicate through standardized REST interfaces (JSON Schema / OpenAPI 3.0). All module state is owned by Orchestrator — other modules are stateless.

```
Orchestrator (state machine + phase task table — single source of truth)
    ├── Knowledge Module
    │   ├── (OpenClaw): 24/7 crawler → search → refine → ingest to Qdrant
    │   └── (RAG): POST /v1/retrieve — semantic search for decision context
    ├── LLM Decision Engine: POST /v1/decide — returns EXECUTE_SKILL | NEXT_PHASE | FINISH
    ├── Skill Executor: POST /v1/execute — runs atomic security tools with optional sandboxing
    └── Trace & Report Service: POST /v1/events (fire-and-forget), POST /v1/reports/generate
```

**Phase flow (PTES-mapped):** Pre-engagement → Intelligence Gathering → Threat Modeling → Vulnerability Analysis → Exploitation → Post-Exploitation → Reporting

**Key design rules:**
- Orchestrator is the only stateful component; attack graph is a static specification only
- Sync calls for short tasks (≤2 min timeout); async task queue for long-running scans
- Phase transitions only after all phase tasks reach terminal state (done/failed/timeout)
- Repetition prevention injects "duplicate warnings" into LLM input — no separate state
- All cross-module APIs must have JSON Schema; mocks must validate against schema in CI

## Repository Layout

```
skills/              # rag-query and rag-ingest (Node.js, runs in OpenClaw)
openclawSkills/      # Pre-built OpenClaw skills (deep-research-pro, pdf-extract, etc.)
rag_crawler/         # Python knowledge acquisition pipeline
  crawler.py         # Main loop: Tavily search → fetch → LLM refine → rag-ingest
  skills_impl/       # Local skill implementations used by crawler
  CONFIG.md          # Detailed API/env configuration guide
  topics.txt         # Topics seeded for crawling
kb-openclaw/         # Knowledge base architecture docs and mission configs
systemPrompt/        # OpenClaw agent system prompt files (SOUL, TOOL, AGENTS, etc.)
docs/                # Design documents (RAG final design)
```

## Core API Contracts (MVP)

| Caller → Callee | Endpoint | Key fields |
|---|---|---|
| Orchestrator → LLM | `POST /v1/decide` | `task_id, current_phase, target_context, history_summary` → `action_type, skill_id, params` |
| Orchestrator → Skill | `POST /v1/execute` | `task_id, skill_id, target, params` → `status, parsed_artifacts, raw_stdout` |
| Any → Trace | `POST /v1/events` | `task_id, timestamp, event_type, source_module, payload` |
| Orchestrator → RAG | `POST /v1/retrieve` | `task_id, phase, query, target_context_snapshot` → `chunks[]` |
| Any → Report | `POST /v1/reports/generate` | `task_id, options` → `report_id, status, download_url` |

See `自动化渗透测试平台-架构设计文档.md` §12 for full schema definitions.
