# AI FINANCE INTELLIGENCE - PROJECT CONSTITUTION (gemini.md)

## 1. IDENTITY & ARCHITECTURAL LAW
- **Project:** AI Finance Intelligence
- **Role:** Autonomous Institutional-Grade Multi-Broker AI Financial Platform
- **Core Principles:** Reliability over speed, strict paper-by-default execution, zero silent failures, decimal precision for monetary operations.
- **Layers:**
  - Layer 1: Directives (`directives/*.md`)
  - Layer 2: Orchestration (FastAPI / Celery / Event Stream / Multi-Agent)
  - Layer 3: Execution (`app/services/`, `app/providers/`, `app/core/`)

## 2. PRODUCTION HARDENING
- **Paper Trading Default:** All broker connections default to Simulation / Paper trading.
- **Live Trading Safety:** Live execution strictly gated behind `TRADING_MODE=live` + validated encrypted credentials.
- **Storage:** PostgreSQL + TimescaleDB for time-series, Redis for cache/distributed locks/task queues, pgvector for semantic retrieval.
