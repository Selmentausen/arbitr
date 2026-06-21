# Arbitr — Project State (Agent Handoff)

**Last updated:** 2026-06-21 (cleanup + distributed architecture complete)\  
**Purpose:** Read this file at the start of a new agent conversation instead of\  
scanning the repo.\  
**Architecture review:** `ARCHITECTURE_REVIEW.md` (latest review, Jun 2026).\  
**Implementation plan:** `implementation_plan.md` (VPS fleet architecture).\  
**Design spec (older):** `Arbitr_document.md` (architecture intent, Feb 2026).\  
**User README:** `README.md` is **outdated** (still says "Phase 1 only" and\  
PostgreSQL as primary DB).

---

## 1. What this project is

**Arbitr** scrapes Russian arbitration court cases from\  
[kad.arbitr.ru](https://kad.arbitr.ru), scores them for relevance (focus:\  
**construction contract disputes** / mediation potential), stores results in a\  
database, and reviews them in a **Streamlit dashboard**.

**MVP scope:** Moscow Arbitration Court (`АС города Москвы`), judge-based\  
search, keyword filtering, optional deep scrape of case card HTML, PDF download,\  
and batch/on-demand ML legal-area classification for reviewed cases.

**Current architecture:** The project is transitioning from a **single-node\  
monolith** (SQLite + local scraper) to a **distributed VPS fleet**\  
(PostgreSQL + FastAPI orchestrator + stateless VPS workers). The new components\  
(`orchestrator/`, `worker/`, `deploy/`) are **fully implemented** at the code\  
level. The Timeweb API endpoints are **stubbed** (need real API documentation to\  
fill in). The old monolithic path (`scrape_parallel.py`) still works for local\  
development.

**Scale context:** Site has 45M+ cases; approach is incremental (by judge,\  
capped per judge, human-like delays, optional residential proxies). Target:\  
32–40 distributed workers scraping simultaneously.

---

## 2. Implementation status (truth table)

| Area | Status | Notes |
|------|--------|-------|
| Config / models / logging | **Done** | `ConfigManager`, Pydantic `Case`/`CaseBase`, `src/utils/logger.py` |
| Playwright scraper | **Working** | `PlaywrightScraper` — UI search, API pagination, case page enrichment; supports `--no-stealth` |
| Parsers | **Working** | List page (`parser.py`), case card (`parser_case_page.py`) |
| Filter Stage 1 | **Working** | Keyword + judge groups + reject keywords (global). `reject_enabled` flag in config (currently `false`) |
| Filter Stage 2 | **Partial** | `stage2_html_analyze` exists; runs **only after** `batch_enrich_cases`, not inside `process_case` |
| Filter Stage 3 (PDF text) | **Implemented for ML batch** | `src/analysis/pdf_extractor.py` extracts downloaded PDFs with PyMuPDF; **wired into distributed worker** via `worker/scraper.py` |
| Filter Stage 4 (LLM/ML) | **Working as batch/on-demand** | Local Ollama classifier (`qwen2.5:14b` default, `7b` fast); not wired into live scrape pipeline |
| PDF Download | **Working (monolith)** | `pdf_downloader.py` — network interception for PDF capture; writes to local disk. **Refactored for worker: returns bytes via temp dir** |
| Storage | **SQLite + PostgreSQL ready** | `database.py` supports both SQLite (local) and PostgreSQL (via `DATABASE_URL`). `JSONB` auto-detection on PG. `worker_status`, `judge_progress` tables exist. |
| Repository (CRUD) | **Working** | `CaseRepository` — save/update, stats, search, export, scrape events, ML review, **distributed job claiming**, worker management, stale reclamation |
| Linkage module | **Removed** | Was an empty stub (`src/linkage/__init__.py`). Removed in cleanup. |
| Dashboard | **Working (SQLite)** | Streamlit: overview, case list, ML review page, search, PDF categorization, export, live scrape monitor. **Needs PG connection update for production** |
| Parallel scrape (monolith) | **Working** | `scrape_parallel.py` + `ParallelScrapeRunner` — local asyncio queue, N workers on one machine |
| **Orchestrator (FastAPI)** | **Done** | App factory, lifespan, 5 routers, Pydantic schemas, API key auth, background monitor, **S3 presigned URLs**, **Timeweb IP rotation stubs**, **rate-limited rotation service**, **heartbeat commands**, **job release endpoint** |
| **Worker (VPS)** | **Done** | Registration → heartbeat → claim → scrape → submit → S3 upload → block detection → wait → rotate → resume loop. **Graceful shutdown with job release**. Standalone package. |
| **Deployment** | **Done** | `deploy/docker-compose.yml` (PgBouncer, Redis, MinIO, orchestrator, dashboard). `deploy/.env.example` (all env vars). `deploy/setup_vps.sh` (worker provisioning). `deploy/worker@.service` (systemd template). |
| Bandwidth test | **Working** | `bandwidth_test.py` — deep crawl traffic measurement |
| ML Export Tool | **Working** | `export_ml.py` — exports ML classified cases with date filters |
| Docker Postgres/Redis | **Infra ready** | `docker-compose.yml` has PG + Redis. `deploy/docker-compose.yml` adds PgBouncer, MinIO, orchestrator, dashboard |

---

## 3. Tech stack

- **Python 3.14+**, **Poetry** (`pyproject.toml`)
- **Playwright** + **playwright-stealth** (anti-bot)
- **BeautifulSoup** (HTML parse)
- **Pydantic v2**, **SQLAlchemy 2** with **SQLite** (local) / **PostgreSQL** (production)
- **FastAPI** + **uvicorn** (orchestrator REST API)
- **httpx** (worker → orchestrator HTTP client, orchestrator → Timeweb API client)
- **Redis** (pub/sub for live dashboard, planned)
- **MinIO** (S3-compatible object storage for PDFs — local dev fallback) or **external S3** (Timeweb, Yandex, Selectel)
- **boto3 / minio** SDK (orchestrator generates presigned URLs)
- **Streamlit** + Plotly dashboard
- **PyYAML** configs; keyword lists in `configs/dictionaries/`
- **psutil** (NIC counters for bandwidth test)
- **Alembic** (listed in deps, not yet used for migrations)
- **OpenAI** (listed in deps, not used in current runtime)
- Local Ollama ML + PDF text extraction are implemented as batch/on-demand\  
  analysis, not live scrape filtering.

### Poetry scripts

```bash
poetry run scrape              # src/cli/scrape.py — single judge/court run
poetry run scrape-parallel     # src/cli/scrape_parallel.py — all judges in judges.txt
poetry run bandwidth-test      # src/cli/bandwidth_test.py — deep crawl traffic measurement
poetry run classify            # src/cli/classify.py — Ollama legal-area classification
poetry run classify-eval       # src/cli/classify_eval.py — golden-set classifier evaluation
poetry run dashboard           # Streamlit app launcher (via src/cli/dashboard.py)
poetry run test-pdf            # src/cli/test_pdf.py — standalone PDF downloader testing script
poetry run export-ml           # src/cli/export_ml.py — export ML classified cases (with date filtering)
```

---

## 4. Repository layout (what matters)

```
Arbitr/
├── configs/
│   ├── main.yaml              # thresholds, scraping delays/proxy, parallel settings, filtering flags
│   ├── classification.yaml    # Ollama model, prompt, few-shot examples, category taxonomy
│   ├── classification_eval.yaml # manual golden set for classifier evaluation
│   ├── areas/construction.yaml
│   └── dictionaries/          # keywords, judges.txt (~99 judges), global reject, etc.
├── src/                       # [UNCHANGED] Core scraping, filtering, models, storage
│   ├── models/case.py         # CaseBase, Case, StatusEnum, CaseDocument, CaseInstance, etc.
│   ├── config/manager.py      # YAML + area dict loading
│   ├── config/classification.py # ML classification config loader
│   ├── analysis/              # Ollama classifier, prompt/context builders, PDF text extraction
│   ├── scraper/
│   │   ├── playwright_scraper.py  # main scraper class
│   │   ├── parser.py                # search results table
│   │   ├── parser_case_page.py      # case card HTML
│   │   ├── pdf_downloader.py        # PDF download via network interception (local disk)
│   │   ├── judge_loader.py          # judges.txt → JudgeEntry
│   │   ├── runner.py                # ParallelScrapeRunner (local monolith)
│   │   ├── traffic_tracker.py       # NetworkTrafficTracker for bandwidth measurement
│   │   └── __init__.py              # exports parser functions
│   ├── filters/
│   │   ├── pipeline.py        # FilterPipeline (reject_enabled flag, cases_for_enrichment)
│   │   ├── stage1_screen.py
│   │   └── stage2_screen.py
│   ├── storage/
│   │   ├── database.py        # SQLAlchemy models + init_db (SQLite + PG dual support)
│   │   └── repository.py      # CaseRepository CRUD + distributed job claiming
│   ├── cli/                   # Command-line entry points
│   │   ├── scrape.py
│   │   ├── scrape_parallel.py
│   │   ├── bandwidth_test.py
│   │   ├── classify.py
│   │   ├── classify_eval.py
│   │   ├── test_pdf.py
│   │   ├── export_ml.py
│   │   └── dashboard.py
│   └── utils/
│       └── logger.py
│
├── orchestrator/              # [NEW — COMPLETE] FastAPI central server
│   ├── app.py                 # FastAPI app factory + lifespan + S3 client + rotation service init
│   ├── config.py              # OrchestratorConfig (all env vars: DATABASE_URL, REDIS_URL, S3_*, MINIO_*, API_KEY, TIMEWEB_API_TOKEN, ROTATION_*)
│   ├── middleware/
│   │   └── auth.py            # API key Bearer auth
│   ├── models/
│   │   └── api_schemas.py     # Pydantic request/response models (WorkerHeartbeatResponse with command, BatchCaseResponse with upload_urls, JobReleaseResponse, RotationStatus, etc.)
│   ├── routers/
│   │   ├── workers.py         # POST /register, /heartbeat (returns commands), /blocked (triggers auto-rotation); GET /list
│   │   ├── jobs.py             # GET /next (claim), /progress, /complete, /failed, /release
│   │   ├── cases.py           # POST /batch (case submission + presigned S3 URL generation)
│   │   ├── dashboard_api.py   # GET /stats, /throughput
│   │   └── fleet.py           # POST /seed-judges, /rotate/{worker_id} (with rate limits), /rotation-stats
│   └── services/
│       ├── s3_client.py       # S3/MinIO presigned URL generation for worker PDF uploads
│       ├── timeweb_client.py  # Timeweb Cloud API client (IP rotation — STUBBED, needs real endpoints)
│       ├── rotation_service.py  # Rate limiting, command queue, rotation history, decision engine
│       └── worker_monitor.py  # Background task: reclaim stale judges, mark stale workers, auto-rotate blocked workers, cleanup
│
├── worker/                    # [NEW — COMPLETE] Standalone VPS worker package
│   ├── main.py                # Entry point: register → poll → scrape → submit → S3 upload → complete → block → wait → rotate → resume
│   ├── client.py              # Orchestrator HTTP client (retries, heartbeat, command execution, direct S3 upload)
│   ├── scraper.py             # Thin wrapper around PlaywrightScraper + FilterPipeline + PDF bytes extraction
│   ├── proxy.py               # Microsocks lifecycle (start/stop/rebind on specific IP)
│   ├── block.py               # DDoS-Guard / 403 / CAPTCHA / soft-block detection (standalone)
│   ├── config.py              # Environment-based configuration (WORKER_ID, ORCHESTRATOR_URL, API_KEY, PROXY_PORT, PROXY_BIND_IP, etc.)
│   ├── models.py              # Internal models: ScrapeResult, PdfAttachment
│   ├── requirements.txt       # Standalone pip install: httpx, pydantic, python-dotenv
│   └── README.md              # Deployment guide for VPS workers
│
├── dashboard/                 # Streamlit UI (still SQLite-focused)
│   ├── app.py
│   ├── components.py
│   └── views/
│
├── deploy/                    # [NEW — COMPLETE] Infrastructure
│   ├── docker-compose.yml     # PgBouncer + Redis + MinIO + orchestrator + dashboard (all env vars wired)
│   ├── .env.example           # Environment variable template (DATABASE_URL, S3_*, MINIO_*, API_KEY, TIMEWEB_API_TOKEN, ROTATION_*)
│   ├── setup_vps.sh           # Worker VPS provisioning script (Ubuntu 24.04)
│   └── worker@.service        # systemd unit template for workers
│
├── data/
│   ├── arbitr.db              # runtime SQLite DB (gitignored, local dev only)
│   ├── pdfs/                  # production PDF storage (gitignored, local dev only)
│   └── pdfs_test/             # test PDF storage (gitignored)
│
├── tests/                     # pytest: config, models, filters, storage, pdf_downloader, classification
├── Arbitr_document.md         # original architecture doc (not current state)
├── ARCHITECTURE_REVIEW.md     # latest distributed architecture review
├── implementation_plan.md     # VPS fleet implementation plan
└── PROJECT_STATE.md           # this file
```

---

## 5. End-to-end data flows

### Monolithic path (local development, still works)

```
judges.txt OR --judge CLI
        ↓
PlaywrightScraper.collect_cases()     # UI autocomplete + /Kad/SearchInstances API pages
        ↓
List[CaseBase]  →  FilterPipeline.process_batch()  →  Stage 1 scores/status
        ↓
pipeline.cases_for_enrichment()       # if reject_enabled=false, all cases with URLs pass through
        ↓
PlaywrightScraper.batch_enrich_cases()  # opens each case URL, expands + buttons, parses HTML
        ↓                                 # if pdf_download_enabled: downloads PDFs to local disk via response listener
FilterPipeline.process_stage2_batch()   # stage2 keywords on enriched fields
        ↓
CaseRepository.save_cases()  →  data/arbitr.db (SQLite)
        ↓
streamlit run dashboard/app.py
```

**Parallel path:** `ParallelScrapeRunner` — asyncio queue, one browser per\  
worker on a single machine, optional distinct proxy port per worker.

### Distributed path (production — FULLY IMPLEMENTED)

```
Central Server (FastAPI Orchestrator + PostgreSQL + S3/MinIO)
        ↑
VPS Worker 1 ───┐
VPS Worker 2 ───┼── HTTP REST API (no DB access from workers)
...           ───┘
        ↓
PlaywrightScraper.search_by_judge() → collect cases
FilterPipeline.process_batch() → stage 1
scrape_case_page() → enrichment → download PDFs → extract text
        ↓
POST /api/cases/batch (metadata + extracted text, small JSON)
        ↓
Orchestrator returns presigned S3 URLs
        ↓
Worker PUTs PDF bytes directly to S3 (bypasses orchestrator)
        ↓
Worker confirms uploads → orchestrator marks documents as uploaded
POST /api/workers/{id}/heartbeat → orchestrator returns commands
POST /api/workers/{id}/blocked → orchestrator triggers IP rotation
```

**Job claiming:** Orchestrator uses `FOR UPDATE SKIP LOCKED` on PostgreSQL\  
for atomic judge assignment. SQLite fallback for local dev. Stale workers\  
(heartbeat > 10 min) are auto-reclaimed by the background monitor.

**IP Rotation flow:**
```
Worker detects block → POST /blocked → Orchestrator marks blocked
→ Orchestrator checks rate limits (rotation_service)
→ If allowed: calls Timeweb API (release old IP, assign new IP)
→ Orchestrator queues "rotate_ip" command for worker
→ Worker receives command on next heartbeat
→ Worker stops microsocks → restarts on new IP → re-registers
→ Worker resumes job claiming
```

**Graceful shutdown:**
```
SIGTERM → Worker sets stop_event → breaks enrichment loop
→ Worker calls POST /jobs/{judge}/release → judge returns to pending
→ Worker stops proxy → closes client → exits
```

### ML classification path (unchanged)

```
CaseRepository.list_cases_for_classification()
        ↓
src/analysis/pdf_extractor.py       # reads local PDFs from data/pdfs with PyMuPDF
        ↓
src/analysis/context_builder.py     # compact case dossier
        ↓
src/analysis/prompt_builder.py      # categories + few-shot examples
        ↓
src/analysis/ollama_client.py       # /api/chat, format=json
        ↓
src/analysis/classifier.py          # validate/normalize probabilities
        ↓
case.extracted_data["ml_classification"] + optional category/relevance_score update
        ↓
Dashboard "ML — Проверка" review page
```

**Important:** ML classification is currently **batch/on-demand only**\  
(`poetry run classify` or dashboard button). It is not called from the scraper.

---

## 6. Scraper behavior (important details)

**Class:** `src/scraper/playwright_scraper.py` — `PlaywrightScraper`

**Search flow:**

1. Open `https://kad.arbitr.ru`, warm session (DDOS-Guard challenge resolved by\  
   browser).
2. Type judge name in autocomplete (`Фамилия И. О.` format from `judge_loader`).
3. Pick suggestion matching `scraping.target_court_filter` (default:\  
   `АС города Москвы`).
4. Paginate via internal API `fetch_api_page` (court id from `COURT_MAP`, judge\  
   id from suggest).

**Enrichment (`batch_enrich_cases`):** For eligible cases — navigate to\  
`case_url`, click "+" on instance chronology, parse\  
participants/instances/documents into `Case` model. If `pdf_download_enabled`,\  
also downloads PDFs to local disk.

**Delays:** All tunable under `configs/main.yaml` → `scraping.delays.*`\  
(seconds, per action).

**Proxy:** `scraping.proxy.enabled` in config. Parallel runner assigns\  
`port_range` ports per worker. Distributed workers use `PROXY_PORT` env var\  
to connect to a local SOCKS proxy (e.g., `microsocks`) bound to a specific IP.

**Stealth:** Playwright-stealth can be disabled using `--no-stealth` in the CLI\  
tools if it causes issues rendering or resolving gray canvas captchas.

**Errors:** `JudgeCourtNotFoundError` if autocomplete has no row for target\  
court.

---

## 7. PDF Download & S3 Upload

**Module:** `src/scraper/pdf_downloader.py` (monolith) / `worker/scraper.py` (distributed)

**How it works (monolith):** kad.arbitr.ru serves PDFs behind DDOS-Guard. The browser\  
loads them through a redirect chain (GIF → 301 → challenge → real PDF). The\  
script registers a Playwright route handler to intercept POST requests to\  
`**/Document/Pdf/**`, captures the PDF bytes, and fulfills the route with a dummy\  
response to prevent browser overhead. Falls back to direct JS `fetch()` if interception\  
fails.

**Distributed worker approach:**
1. `worker/scraper.py` creates a temporary directory.
2. Calls `download_pdfs_for_case()` to write PDFs to temp dir (same interception logic).
3. Reads each PDF back into memory as `bytes`.
4. Extracts text via `pdf_extractor.extract_text_from_pdf()` (PyMuPDF).
5. Includes `bytes` and `extracted_text` in `PdfAttachment` objects.
6. Submits case metadata + extracted text to orchestrator (small JSON).
7. Receives presigned S3 URLs from orchestrator.
8. Uploads PDF bytes directly to S3 using `PUT` (orchestrator never sees bytes).
9. Confirms successful uploads to orchestrator.

**Storage path:** S3 object key is `pdfs/{case_id}/{doc_id}_{filename}`.

**Config flags:**

- `filtering.pdf_download_enabled: true` — master switch
- `scraping.pdf_storage_dir: data/pdfs` — storage location (local only, monolith)

---

## 8. Filtering

### StatusEnum (`src/models/case.py`)

- `high_relevant`, `reject`, `insufficient_info`, `uncertain`

### Config flags (`configs/main.yaml` → `filtering`)

- `reject_enabled: false` — when false, cases that would be rejected become\  
  `uncertain` instead, and all cases with URLs proceed to enrichment
- `pdf_download_enabled: true` — enables PDF download during enrichment

### Stage 1 (`stage1_initial_screen`)

- Area keywords from `configs/areas/*.yaml` → dictionary files
- Global reject: `configs/dictionaries/global_reject_keywords.txt`
- Judge group bonuses from `main.yaml` → `judge_groups`
- Thresholds: `high: 80`, `low: 20`, gray zone 40–60 remains config-only for the\  
  live filter pipeline
- Fixed bugs: `matched_area` → `best_area_name`, `len(matched_keywords - 1, 3)`\  
  → `min(len(...) - 1, 3)`

### Stage 2 (`stage2_html_analyze`)

- Only cases still `uncertain` / `insufficient_info` after enrichment
- Uses `stage2_keywords` from `configs/dictionaries/construction/stage2.txt`
- **Not** called from `FilterPipeline.process_case` — only via\  
  `process_stage2_batch` after scrape enrichment

---

## 9. Bandwidth measurement

**Script:** `bandwidth_test.py` — single-judge deep crawl (default 200 cases,\  
going back in time to find cases with actual documents).

**Approach:**

- `TrackingScraper` extends `PlaywrightScraper` with `NetworkTrafficTracker`\  
  (counts HTTP bytes via browser context events).
- Separate `pdf_stats` for PDF-specific traffic.
- Reports a clean traffic breakdown summary (Warmup, Case card load, Chronology\  
  expand, PDF downloads, and Other/API traffic) after each enriched case.
- Detailed network request tables are saved to `data/traffic_log.csv` instead of\  
  printing to clean console outputs.
- Supports `--no-stealth` to disable playwright-stealth.

**Usage:**

```bash
poetry run bandwidth-test --max-cases 10 --duration-minutes 10 --no-stealth
poetry run bandwidth-test --judge "Титова" --max-cases 300 --proxy
```

---

## 10. Configuration

| File | Role |
|------|------|
| `configs/main.yaml` | Thresholds, judge_groups, linkage_rules, scraping delays/proxy, filtering flags |
| `configs/classification.yaml` | Ollama legal-area classifier config: categories, model names, prompt v1.0, few-shot examples, disambiguation rules, context limits |
| `configs/classification_eval.yaml` | Manual golden set for `poetry run classify-eval` |
| `configs/areas/construction.yaml` | Area rules, paths to keyword files, weights |
| `configs/dictionaries/judges.txt` | ~99 judge full names (parallel scrape input) |
| `configs/dictionaries/construction/keywords.txt` | Stage 1 keywords |
| `configs/dictionaries/construction/stage2.txt` | Stage 2 keywords |
| `configs/dictionaries/global_reject_keywords.txt` | Global reject list |
| `configs/dictionaries/closed_case_indicators.txt` | Used when checking closed cases |
| `configs/dictionaries/document_priorities.yaml` | PDF priority rules used during scraping/dashboard PDF categorization |

---

## 11. Database

### Local development (SQLite)

- **Path:** `data/arbitr.db` (gitignored)
- **Init:** `init_db("data/arbitr.db")` or `init_db(":memory:")` for tests
- **Access:** `CaseRepository` via `get_session()`

### Production (PostgreSQL)

- **Connection:** `DATABASE_URL` environment variable (e.g.,\  
  `postgresql+psycopg2://user:pass@host:5432/arbitr`)
- **Connection pooling:** `pool_pre_ping=True`, `pool_size=10`, `max_overflow=20`,\  
  `pool_recycle=1800` (configured automatically in `init_db()` when PG is detected)
- **PgBouncer:** `deploy/docker-compose.yml` includes PgBouncer for transaction\  
  pooling. Orchestrator and dashboard connect through it on port 6432.
- **JSON columns:** `JSONB` on PostgreSQL (with `JSONBOrText` TypeDecorator in\  
  `database.py`), `TEXT` on SQLite. `repository.py` handles serialization\  
  (`_serialize_json` / `_deserialize_json`) transparently.
- **Schema tables:** `cases`, `participants`, `case_participants`, `documents`,\  
  `instances`, `instance_updates`, `judges`, `scrape_events`, `judge_progress`,\  
  `scrape_meta`, `worker_status`

### Key distributed tables

- **`judge_progress`** — per-judge queue for job claiming. Columns: `judge_name`,\  
  `status` (pending/collecting/enriching/completed/failed), `claimed_by`,\  
  `heartbeat`, `retry_count`.
- **`worker_status`** — fleet health tracking. Columns: `id`, `vps_id`,\  
  `ip_address`, `status` (active/blocked/offline/rotating), `current_judge`,\n  `proxy_port`, `last_heartbeat`, `blocked_at`, `total_cases_scraped`,\  
  `total_judges_completed`.

---

## 12. Orchestrator API (FastAPI)

**Run:** `uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000`

**Worker-facing endpoints:**

```
POST /api/workers/register              # Worker registers on boot
POST /api/workers/{id}/heartbeat        # Alive ping (every 30s). Response includes command if any.
POST /api/workers/{id}/blocked          # Report IP block → triggers auto-rotation (rate limited)
GET  /api/jobs/next?worker_id=X         # Claim next judge (atomic, FOR UPDATE SKIP LOCKED)
POST /api/jobs/{judge}/progress         # Progress update + heartbeat
POST /api/jobs/{judge}/complete         # Mark judge done
POST /api/jobs/{judge}/failed           # Mark judge failed
POST /api/jobs/{judge}/release          # Release job back to queue (graceful shutdown / block)
POST /api/cases/batch                   # Submit scraped cases (JSON). Returns presigned S3 URLs for PDFs.
POST /api/jobs/{judge}/uploads/complete  # Confirm PDF uploads (optional)
```

**Dashboard/Admin endpoints:**

```
GET  /api/dashboard/stats               # Aggregate stats (cases, judges, workers)
GET  /api/dashboard/throughput           # Cases/hour metrics
GET  /api/workers                        # Fleet status list
POST /api/fleet/rotate/{worker_id}      # Manual VM rotation (with rate limits)
POST /api/fleet/seed-judges             # Seed judge queue from judges.txt
GET  /api/fleet/rotation-stats          # Rotation rate limits, history, pending commands
GET  /health                            # No-auth health check
```

**Auth:** `Authorization: Bearer <API_KEY>` header on all endpoints except\  
`/health`. `API_KEY` is set via env var (default: `dev-key-change-me`).

**Background tasks:** `orchestrator/services/worker_monitor.py` runs every 60s\  
to:
1. Reclaim judges from workers with stale heartbeats (> 10 min).
2. Mark workers with stale heartbeats as `offline` (> 5 min).
3. Auto-rotate blocked workers after 5 minutes (via `rotation_service`).
4. Run `rotation_service.cleanup()` (expired commands, old history).

**Lifespan (startup):**
1. Initialize DB connection.
2. Initialize S3 client (`app.state.s3_client`).
3. Initialize rotation service (`app.state.rotation_service`).
4. Start background monitor task.

---

## 13. Worker (VPS)

**Run:** `python -m worker.main` (or via systemd unit)

**Environment variables:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `WORKER_ID` | `worker-{pid}` | Unique worker ID (e.g., `vps-tw-01-w1`) |
| `VPS_ID` | `local` | Logical VPS group (e.g., `vps-tw-01`) |
| `ORCHESTRATOR_URL` | `http://localhost:8000` | Central server URL |
| `API_KEY` | `dev-key-change-me` | Auth key |
| `PROXY_PORT` | — | Local microsocks port (e.g., `10001`) |
| `PROXY_BIND_IP` | — | Public IP to bind microsocks to |
| `HEARTBEAT_INTERVAL` | `30` | Seconds between heartbeats |
| `POLL_INTERVAL` | `60` | Seconds between job polls when idle |
| `BATCH_SIZE` | `50` | Cases per submission batch |
| `MAX_RETRIES` | `3` | HTTP retries per request |
| `RETRY_BASE_DELAY` | `1.0` | Initial retry backoff (seconds) |
| `S3_UPLOAD_TIMEOUT` | `60` | Seconds for S3 PUT |
| `CONFIG_PATH` | `configs/main.yaml` | Scraper config path |
| `NO_STEALTH` | `false` | Disable playwright-stealth |

**Loop:**

```
Register with orchestrator
→ Start heartbeat task (background)
→ Poll /api/jobs/next
  → No job: sleep 60s, retry
  → Job claimed: scrape judge
    → search_by_judge() → collect cases
    → detect_block() → if blocked, report_blocked() → release_job() → wait for rotation command
    → FilterPipeline.process_batch() → stage 1
    → scrape_case_page() → enrichment (per case)
    → download PDFs → extract text → store in memory
    → POST /api/cases/batch (metadata + extracted_text)
    → Receive presigned S3 URLs
    → PUT PDF bytes directly to S3
    → Confirm uploads
    → POST /api/jobs/{judge}/complete
  → Loop
```

**Graceful shutdown:** SIGINT/SIGTERM sets `asyncio.Event` → breaks enrichment loop\  
→ calls `release_job()` → judge returns to `pending` immediately → proxy stops → client closes.

**Block → Rotate flow:**
```
Detect block during search or enrichment
→ POST /api/workers/{id}/blocked
→ Enter waiting state (stop claiming jobs)
→ Heartbeat every 30s checks for commands
→ On "rotate_ip" command: stop microsocks → restart on new IP → re-register
→ Resume normal operation
```

---

## 14. Block Detection

**Module:** `worker/block.py` (standalone, no external dependencies)

Detects IP blocks by checking:
1. HTTP status codes (403, 429, 503 with DDoS-Guard markers)
2. DDoS-Guard challenge page content ("Check your browser", "Подождите, идёт проверка")
3. CAPTCHA markers ("captcha", "recaptcha", "challenge-form")
4. Soft block: 0 cases found when results are expected (heuristic)
5. Suspicious page content during enrichment (`is_content_suspicious()`)

**Block detection is checked:**
- After `search_by_judge()` (case collection)
- During the enrichment loop (after each case page scrape)

**If blocked:** Worker immediately reports to orchestrator, releases the judge,\  
and enters a waiting state until the orchestrator sends an IP rotation command.

---

## 15. IP Rotation

**Module:** `orchestrator/services/rotation_service.py`

**Rate limits:**
- Max 3 rotations per VPS per hour
- Max 10 rotations per VPS per day
- 15-minute cooldown between rotations on the same VPS
- Commands expire after 30 minutes if not executed

**Decision flow:**
```
Worker reports blocked
→ Orchestrator checks rate limits
→ If allowed: calls Timeweb API (release old IP, assign new IP)
→ If new IP assigned: queue "rotate_ip" command for worker
→ Worker receives command on heartbeat
→ Worker executes: restart proxy on new IP → re-register
→ If not allowed: worker stays blocked, logged for manual review
```

**Auto-rotation:** Background monitor checks blocked workers every 60s. If a worker\  
has been blocked for > 5 minutes, it triggers automatic rotation (subject to\  
rate limits).

**Manual rotation:** Admin can trigger via `POST /api/fleet/rotate/{worker_id}`.

**Timeweb API:** `orchestrator/services/timeweb_client.py` — stubs for the real\  
Timeweb Cloud API endpoints. Need to fill in actual URLs and response parsing\  
once API documentation is available.

---

## 16. S3 / Object Storage

**Module:** `orchestrator/services/s3_client.py`

**Supports:** Any S3-compatible provider (MinIO, AWS S3, Timeweb Object Storage,\  
Yandex Cloud, Selectel).

**Environment:**
- `S3_ENDPOINT` — primary (overrides MinIO if set)
- `S3_ACCESS_KEY`, `S3_SECRET_KEY`
- `S3_BUCKET` (default: `arbitr-pdfs`)
- `S3_REGION` (default: `ru-1`)
- `S3_SECURE` (default: `true`)
- `MINIO_*` — fallback for local dev

**How it works:**
1. Worker submits case metadata + `documents` list (case_id, doc_id, filename).
2. Orchestrator generates a presigned PUT URL for each document (15-min expiry).
3. Response: `upload_urls: {case_id: {doc_id: "https://s3...?signature=..."}}`.
4. Worker PUTs PDF bytes directly to that URL (no auth headers needed).
5. S3 stores the file at `pdfs/{case_id}/{doc_id}_{filename}`.
6. Orchestrator stores `storage_key` in PostgreSQL when worker confirms upload.

**Object key format:** `pdfs/{case_id}/{doc_id}_{filename}`

**Typical PDF size:** ~200–320 KB per document (court rulings, decisions).

---

## 17. Dashboard

**Run:** `poetry run dashboard` (or `streamlit run dashboard/app.py`)

**Current state:** Reads from SQLite (`data/arbitr.db`). The connection is\  
hardcoded in `src/cli/dashboard.py`.

**Pages:** Overview, case list, search, ML review ("ML — Проверка"), export,\  
live scrape monitor.

**Target state:** Connect to PostgreSQL via `DATABASE_URL`. Add a "Fleet" page\  
showing worker status, active/blocked/offline counts, and IP rotation log.\  
Serve PDFs from S3/MinIO instead of local disk.

---

## 18. ML Export Tool

**Script:** `src/cli/export_ml.py` — exports ML classified cases from the\  
database to a text file.

**Features:**

- Formats exported cases with case link, classification category, confidence,\  
  analyzed date, and formatted reasoning.
- Supports filtering by date and timeframe:
  - `--date YYYY-MM-DD`: Filter for cases classified on a specific day.
  - `--since YYYY-MM-DD[THH:MM:SS]`: Filter for cases classified on or after.
  - `--until YYYY-MM-DD[THH:MM:SS]`: Filter for cases classified on or before.
- Supports `--limit <N>` to cap the number of exported cases.
- Outputs defaults to `data/ml_export.txt`.

**Usage:**

```bash
poetry run export-ml                                          # export all classified cases
poetry run export-ml --date 2026-06-08                        # export cases classified on a specific date
poetry run export-ml --since 2026-06-08 --until 2026-06-09    # export timeframe
poetry run export-ml --output data/custom_export.txt --limit 50
```

---

## 19. Known gaps & next work items

| Priority | Item | Status | Why it matters |
|----------|------|--------|----------------|
| **P0** | **Fill in Timeweb API endpoints** | Stubbed | `timeweb_client.py` has the right interface but needs real URLs and response parsing. This is the only blocker to automatic IP rotation. |
| **P0** | **Test end-to-end locally** | Not done | Run 2 workers locally against the orchestrator. Validate: job claiming, case submission, S3 upload, block detection, IP rotation simulation. |
| **P1** | **Dashboard PostgreSQL migration** | Not done | Update DB connection, add fleet monitoring page, serve PDFs from S3. |
| **P1** | **Orchestrator rate limiting** | Not done | 40 workers submitting simultaneously can thundering-herd the DB. Add per-worker rate limits or Redis queue. |
| **P2** | **Cloud-init + golden snapshot** | Not done | Instead of `setup_vps.sh` on every new VM, create a snapshot with Python + Poetry + Playwright + microsocks pre-installed. |
| **P2** | **Observability** | Not done | Add Prometheus-style metrics or structured JSON logging for fleet health, throughput, and error rates. |
| **P3** | **Data migration from SQLite** | Optional | Current SQLite data is only a few days of scraping. If you have ML classifications or golden-set data to preserve, write a migration script. |

---

## 20. Testing

- **pytest** under `tests/`
- Classification unit tests can be run without Ollama:\  
  `poetry run pytest tests/test_classification_config.py tests/test_prompt_builder.py tests/test_context_builder.py tests/test_classifier.py -v --no-cov`
- **Distributed tests needed:**
  - Job claiming concurrency (multiple workers claiming simultaneously)
  - Orchestrator restart mid-scrape (worker reconnect behavior)
  - Duplicate case submission (same `batch_id` submitted twice)
  - Worker crash during enrichment (stale judge reclamation)
  - Network partition (worker retries vs. fail logic)
  - IP rotation end-to-end (simulate block → command → proxy restart → re-register)

---

## 21. Cost estimate (revised)

| Item | Unit Cost | Qty | Monthly |
|------|-----------|-----|---------|
| Worker VPS (4 CPU / 8GB / 80GB) | ~1,485₽ | 8 | 11,880₽ |
| Extra IPs (32 total − 3 free) | 180₽ | 29 | 5,220₽ |
| Central server (4 CPU / 8GB / 80GB) | 1,485₽ | 1 | 1,485₽ |
| DB: Managed PG (4 CPU / 8GB / 220GB) | 7,000₽ | 1 | 7,000₽ |
| S3: Object Storage (2TB) | ~2₽/GB | 2048 | ~4,096₽ |
| **Total (8 VPS × 4 workers = 32)** | | | **~29,681₽/mo** |

> **Note:** Reduced from 5 workers/VPS to 4 to avoid OOM.\  
> S3 cost is estimated; depends on provider (Timeweb, Yandex, Selectel).\  
> If using MinIO locally, S3 cost is $0 but limited to 80GB SSD.

---

## 22. Quick-start commands

### Local development (SQLite)

```bash
poetry install
poetry run playwright install chromium
poetry run scrape --judge "Титова" --max-cases 50
poetry run dashboard
```

### Local orchestrator + worker (PostgreSQL via Docker)

```bash
cd deploy
cp .env.example .env  # Edit with your values

# Start infrastructure (PgBouncer, Redis, MinIO)
docker-compose up -d pgbouncer redis minio

# Terminal 1: orchestrator
DATABASE_URL=postgresql+psycopg2://arbitr:arbitr@localhost:6432/arbitr \
  API_KEY=dev-key-change-me \
  uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000

# Terminal 2: seed judges
curl -X POST http://localhost:8000/api/fleet/seed-judges \
  -H "Authorization: Bearer dev-key-change-me"

# Terminal 3: worker 1
ORCHESTRATOR_URL=http://localhost:8000 \
  API_KEY=dev-key-change-me \
  WORKER_ID=local-test-1 \
  python -m worker.main

# Terminal 4: worker 2 (different port, same IP for local testing)
ORCHESTRATOR_URL=http://localhost:8000 \
  API_KEY=dev-key-change-me \
  WORKER_ID=local-test-2 \
  python -m worker.main
```

### VPS deployment (worker only)

```bash
# On the VPS
export WORKER_ID="vps-tw-01-w1"
export VPS_ID="vps-tw-01"
export ORCHESTRATOR_URL="https://your-orchestrator:8000"
export API_KEY="your-secret-key"
export PROXY_PORT="10001"
export PROXY_BIND_IP="1.2.3.4"

python -m worker.main
```

---

## 23. Files changed in this update

### Orchestrator (new + modified)
- `orchestrator/services/s3_client.py` — **NEW** (presigned URL generation)
- `orchestrator/services/timeweb_client.py` — **NEW** (Timeweb API — stubbed)
- `orchestrator/services/rotation_service.py` — **NEW** (rate limits, command queue, decision engine)
- `orchestrator/services/worker_monitor.py` — **MODIFIED** (auto-rotate blocked workers, cleanup)
- `orchestrator/models/api_schemas.py` — **MODIFIED** (WorkerHeartbeatResponse with command, BatchCaseResponse with upload_urls, JobReleaseResponse, RotationStatus)
- `orchestrator/routers/workers.py` — **MODIFIED** (heartbeat returns commands, block triggers auto-rotation)
- `orchestrator/routers/jobs.py` — **MODIFIED** (added `/release` endpoint)
- `orchestrator/routers/cases.py` — **MODIFIED** (refactored submission, returns presigned S3 URLs)
- `orchestrator/routers/fleet.py` — **MODIFIED** (rotate with rate limits, rotation-stats endpoint)
- `orchestrator/app.py` — **MODIFIED** (lifespan initializes S3 + rotation service)
- `orchestrator/config.py` — **MODIFIED** (S3_*, ROTATION_* env vars)

### Worker (new package — complete)
- `worker/config.py` — **NEW** (environment-based config)
- `worker/models.py` — **NEW** (ScrapeResult, PdfAttachment)
- `worker/proxy.py` — **NEW** (microsocks lifecycle)
- `worker/client.py` — **NEW** (orchestrator client + S3 upload)
- `worker/block.py` — **NEW** (standalone block detection)
- `worker/scraper.py` — **NEW** (Playwright wrapper + PDF bytes extraction)
- `worker/main.py` — **NEW** (event loop with block → wait → rotate → resume)
- `worker/requirements.txt` — **NEW** (standalone deps)
- `worker/README.md` — **NEW** (deployment guide)

### Deploy
- `deploy/.env.example` — **MODIFIED** (all new env vars documented)
- `deploy/docker-compose.yml` — **MODIFIED** (S3_*, ROTATION_* env vars wired)

### Superseded (safe to delete)
- `worker/orchestrator_client.py` — replaced by `worker/client.py`
- `worker/block_detector.py` — replaced by `worker/block.py`


---

## 24. Cleanup (this session)

The following files and directories were identified as outdated, unused, or superseded and were removed:

### Deleted directories
- `test_fleet/` — old in-memory test orchestrator (superseded by `orchestrator/`)
- `scratch/` — debug JavaScript file (`decoded_challenge.js`)
- `docs/reference/` — random Russian text reference files (`КОРПОРАТИВНЫЙ.txt`, `ПОДРЯД.txt`)
- `docs/` — empty after reference removal

### Deleted files
- `Dockerfile` — old Python 3.10 Dockerfile (did not include orchestrator/worker)
- `docker-compose.yml` (root) — old dev compose (superseded by `deploy/docker-compose.yml`)
- `src/scraper/collector.py` — dead code (unused `CaseCollector`)
- `src/scraper/api_client.py` — dead code (`KadApiClient` only used by collector)
- `logs/arbitr.log` — runtime log
- `.coverage` — coverage data
- `tests/case_page.html` — test fixture HTML
- `tests/main_page_html_after_load.html` — test fixture HTML

### Updated files
- `src/scraper/__init__.py` — removed `KadApiClient`, `CaseCollector`, `collect_and_save` exports
- `.gitignore` — added `scratch/`, `test_fleet/`, `docs/reference/`, `.coverage`

**Result:** 92 Python files pass `py_compile` syntax check with zero errors.
