# Arbitr

Arbitr scrapes Russian arbitration court cases from [kad.arbitr.ru](https://kad.arbitr.ru). It focuses on construction contract disputes, scores them for relevance, and stores everything in a database with a Streamlit dashboard for reviewing results.

The project has two modes: you can run it as a single local scraper for development, or as a distributed fleet with a central orchestrator server and multiple VPS workers scraping in parallel.

---

## What it does

The site has over 45 million cases. Arbitr approaches it incrementally by judge, with human-like delays and optional proxies. It scrapes case metadata, downloads PDF rulings, extracts text from them, and applies keyword-based filtering to surface construction-related disputes that might be good candidates for mediation.

The current MVP scope is the Moscow Arbitration Court (АС города Москвы), scraping by judge with ~99 judges in the list.

---

## Architecture

There are two ways to run this.

### Local mode (single machine)

For development and testing, you can run everything on one machine with SQLite. This uses the existing `scrape_parallel.py` script with an asyncio queue and multiple Playwright browsers on the same box.

### Distributed mode (production)

This is the new architecture, built for scaling to 30+ workers:

- **Central server** runs a FastAPI orchestrator with PostgreSQL for case storage and Redis for pub/sub. It manages the judge queue, receives case submissions from workers, and handles IP rotation when workers get blocked.
- **VPS workers** are stateless scrapers that run on separate servers. Each worker registers with the orchestrator, claims a judge, scrapes it, submits the results, and uploads any PDFs directly to S3 (bypassing the orchestrator entirely). When a worker's IP gets blocked, it reports it to the orchestrator and waits for a rotation command.

The orchestrator uses PostgreSQL's `FOR UPDATE SKIP LOCKED` to atomically assign judges to workers, so there's no race condition when multiple workers claim jobs simultaneously.

---

## Key components

| Directory | Purpose |
|-----------|---------|
| `src/` | Core scraper, filters, models, database. Used by both local and distributed modes. |
| `orchestrator/` | FastAPI central server. Handles worker registration, job claiming, case submission, and IP rotation. |
| `worker/` | Standalone scraper package that runs on each VPS. Stateless — no DB credentials, no local state. |
| `deploy/` | Docker Compose for the orchestrator stack, VPS setup scripts, and systemd service templates. |
| `dashboard/` | Streamlit UI for reviewing cases, running ML classification, and monitoring. |

---

## Getting started

### Prerequisites

- Python 3.14+
- Poetry
- Playwright (for scraping)
- PostgreSQL (for distributed mode, or SQLite for local dev)

### Install

```bash
git clone https://github.com/YOUR_REPO/Arbitr.git
cd Arbitr
poetry install
poetry run playwright install chromium
```

### Local development (SQLite)

Scrape a single judge:

```bash
poetry run scrape --judge "Титова" --max-cases 50
```

Run the parallel scraper on all judges in the list:

```bash
poetry run scrape-parallel
```

Launch the dashboard:

```bash
poetry run dashboard
```

Run the ML classifier on already-scraped cases:

```bash
poetry run classify
```

### Distributed mode (PostgreSQL + orchestrator + workers)

This is for production. You need at least two servers: one central server and one or more worker VPSs.

**On the central server:**

```bash
cd deploy
cp .env.example .env
# Edit .env with your PostgreSQL credentials and API key
poetry run uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000
```

Seed the judge queue:

```bash
curl -X POST http://localhost:8000/api/fleet/seed-judges \
  -H "Authorization: Bearer YOUR_API_KEY"
```

**On each worker VPS:**

```bash
export ORCHESTRATOR_URL="http://CENTRAL_SERVER_IP:8000"
export API_KEY="YOUR_API_KEY"
export WORKER_ID="vps-01-worker-1"
export VPS_ID="vps-01"
poetry run python -m worker.main
```

For a full step-by-step test deployment guide, see `deploy/TEST_DEPLOYMENT.md`.

---

## Environment variables

### Orchestrator (central server)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | SQLite | PostgreSQL connection string for production |
| `API_KEY` | Yes | `dev-key-change-me` | Bearer token workers use to authenticate |
| `S3_ENDPOINT` | No | — | S3 provider URL (Timeweb, Yandex, Selectel) |
| `S3_ACCESS_KEY` | No | — | S3 access key |
| `S3_SECRET_KEY` | No | — | S3 secret key |
| `TIMEWEB_API_TOKEN` | No | — | Timeweb Cloud API token for automatic IP rotation |
| `ROTATION_MAX_PER_HOUR` | No | 3 | Max IP rotations per VPS per hour |
| `ROTATION_MAX_PER_DAY` | No | 10 | Max IP rotations per VPS per day |

### Worker (each VPS)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ORCHESTRATOR_URL` | Yes | `http://localhost:8000` | Central server URL |
| `API_KEY` | Yes | `dev-key-change-me` | Must match orchestrator's API key |
| `WORKER_ID` | Yes | `worker-{pid}` | Unique ID for this worker |
| `VPS_ID` | Yes | `local` | Logical group (e.g., `vps-01`) |
| `PROXY_PORT` | No | — | Local microsocks port |
| `PROXY_BIND_IP` | No | — | Public IP to bind the proxy to |
| `HEARTBEAT_INTERVAL` | No | 30 | Seconds between heartbeats |
| `NO_STEALTH` | No | false | Disable playwright-stealth if it causes issues |

---

## How the distributed worker works

1. **Register** with the orchestrator on boot.
2. **Heartbeat** every 30 seconds. The orchestrator can embed commands in the response (e.g., "rotate your IP").
3. **Claim a judge** from the queue via `GET /api/jobs/next`.
4. **Scrape** the judge's cases with Playwright, apply Stage 1 filters, enrich case pages, and download PDFs into memory.
5. **Submit case metadata** (JSON) to the orchestrator. No PDF bytes go to the orchestrator.
6. **Receive presigned S3 URLs** from the orchestrator and upload PDFs directly to S3.
7. **Mark the job complete** and claim the next judge.

If the worker gets blocked (DDoS-Guard, 403, CAPTCHA), it reports it to the orchestrator and enters a waiting state. The orchestrator decides whether to rotate the IP (checking rate limits), calls the Timeweb API if configured, and sends a rotation command back to the worker via the next heartbeat. The worker then restarts its local proxy on the new IP and resumes work.

---

## How PDFs are handled

In distributed mode, the orchestrator never sees raw PDF bytes. Here's the flow:

1. Worker downloads PDFs during scraping and extracts text using PyMuPDF.
2. The extracted text is included in the case metadata submitted to the orchestrator.
3. The worker also receives temporary presigned S3 URLs from the orchestrator.
4. The worker uploads the raw PDF bytes directly to S3 using those URLs.
5. The orchestrator stores the S3 object key in PostgreSQL.

If you don't configure S3, the worker skips the upload step but the extracted text is still saved.

---

## Costs (rough estimate)

This is what you're looking at for a 32-worker setup on Timeweb Cloud:

| Item | Monthly |
|------|---------|
| 8 worker VPS (4 CPU / 8GB / 80GB) | ~11,880 RUB |
| 29 extra IPs (32 total - 3 free) | ~5,220 RUB |
| 1 central server (4 CPU / 8GB) | ~1,485 RUB |
| Managed PostgreSQL (4 CPU / 8GB / 220GB) | ~7,000 RUB |
| S3 object storage (~2TB) | ~4,000 RUB |
| **Total** | **~29,585 RUB** |

You can start with 1 worker VPS and scale up gradually.

---

## What's still needed

- **Timeweb API endpoints** are stubbed in `orchestrator/services/timeweb_client.py`. You need real API docs to fill in the actual URL paths and response parsing for automatic IP rotation.
- **Dashboard** still connects to SQLite by default. It needs to be switched to PostgreSQL for production.
- **Observability** is minimal. Consider adding structured logging or metrics if you scale beyond 10 workers.

---

## Project files

```
Arbitr/
├── configs/            # YAML configs, keywords, judges.txt
├── src/                # Core scraper, filters, models, database
├── orchestrator/       # FastAPI central server
├── worker/             # Standalone scraper for VPS deployment
├── dashboard/          # Streamlit review UI
├── deploy/             # Docker Compose, .env.example, setup scripts
├── tests/              # pytest tests
└── PROJECT_STATE.md    # Detailed handoff doc for future agents
```

For more details on the architecture, see `ARCHITECTURE_REVIEW.md` and `implementation_plan.md`.
