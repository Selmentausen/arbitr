# Arbitr Worker

Standalone scraper for the distributed VPS fleet.

## What it does

- Registers with the FastAPI orchestrator
- Claims judges via atomic job queue
- Scrapes cases with Playwright
- Detects IP blocks and reports them
- **Uploads PDFs directly to S3** via presigned URLs (orchestrator never sees
  bytes)
- Receives IP rotation commands from the orchestrator via heartbeat piggyback

## Architecture

```
Worker                          Orchestrator                    S3
───────────────────────────────────────────────────────────────────
  │  1. Register                  │                              │
  │──────────────────────────────>│                              │
  │                               │                              │
  │  2. Heartbeat (every 30s)     │                              │
  │──────────────────────────────>│                              │
  │  3. Response with command     │                              │
  │<──────────────────────────────│                              │
  │                               │                              │
  │  4. Claim job                 │                              │
  │──────────────────────────────>│                              │
  │                               │                              │
  │  5. Scrape → collect cases    │                              │
  │  6. Filter → enrich → download PDFs → extract text           │
  │                               │                              │
  │  7. Submit metadata (JSON)    │                              │
  │  8. Receive presigned URLs    │                              │
  │<──────────────────────────────│                              │
  │                               │                              │
  │  9. PUT PDF bytes directly    │                              │
  │─────────────────────────────────────────────────────────────>│
  │                               │                              │
  │  10. Confirm uploads          │                              │
  │──────────────────────────────>│                              │
  │                               │                              │
  │  [Blocked]                    │                              │
  │  POST /workers/{id}/blocked   │                              │
  │──────────────────────────────>│  11. Call Timeweb API        │
  │  Wait for command...          │  12. Return new IP           │
  │<──────────────────────────────│                              │
  │  13. Restart proxy on new IP  │                              │
```

## Key design decisions

1. **Worker is stateless** — no DB, no file state, no cloud credentials.
2. **Orchestrator controls rotation** — worker reports "blocked", orchestrator
   decides when/how to rotate.
3. **PDFs bypass orchestrator** — worker uploads directly to S3 using presigned
   URLs. No bytes flow through the orchestrator.
4. **Heartbeat piggyback** — commands are embedded in heartbeat responses. No
   WebSocket, no web server on the worker.

## Environment variables

| Variable             | Required | Default                 | Description                             |
| -------------------- | -------- | ----------------------- | --------------------------------------- |
| `WORKER_ID`          | Yes      | `worker-{pid}`          | Unique worker ID (e.g., `vps-tw-01-w1`) |
| `VPS_ID`             | Yes      | `local`                 | Logical VPS group (e.g., `vps-tw-01`)   |
| `ORCHESTRATOR_URL`   | Yes      | `http://localhost:8000` | Central server URL                      |
| `API_KEY`            | Yes      | `dev-key-change-me`     | Bearer token                            |
| `PROXY_PORT`         | No       | —                       | Local microsocks port (e.g., `10001`)   |
| `PROXY_BIND_IP`      | No       | —                       | Public IP to bind microsocks to         |
| `HEARTBEAT_INTERVAL` | No       | `30`                    | Seconds between heartbeats              |
| `POLL_INTERVAL`      | No       | `60`                    | Seconds between job polls when idle     |
| `BATCH_SIZE`         | No       | `50`                    | Cases per submission batch              |
| `MAX_RETRIES`        | No       | `3`                     | HTTP retries per request                |
| `RETRY_BASE_DELAY`   | No       | `1.0`                   | Initial retry backoff (seconds)         |
| `S3_UPLOAD_TIMEOUT`  | No       | `60`                    | Seconds for S3 PUT                      |
| `CONFIG_PATH`        | No       | `configs/main.yaml`     | Scraper config path                     |
| `NO_STEALTH`         | No       | `false`                 | Disable playwright-stealth              |

## Deployment

### Option 1: From project root (recommended)

```bash
# On the VPS, clone the full repo
git clone https://github.com/YOUR_REPO/Arbitr.git /opt/arbitr
cd /opt/arbitr

# Install dependencies (same as orchestrator)
poetry install --no-interaction
poetry run playwright install chromium

# Set environment variables
export WORKER_ID="vps-tw-01-w1"
export VPS_ID="vps-tw-01"
export ORCHESTRATOR_URL="https://your-orchestrator:8000"
export API_KEY="your-secret-key"
export PROXY_PORT="10001"
export PROXY_BIND_IP="1.2.3.4"

# Run
poetry run python -m worker.main
```

### Option 2: With systemd

```bash
# Copy the service template
cp deploy/worker@.service /etc/systemd/system/worker@10001.service

# Edit the service file to set env vars, then:
systemctl enable worker@10001
systemctl start worker@10001
```

### Option 3: Docker (future)

```bash
docker build -f deploy/Dockerfile.worker -t arbitr-worker .
docker run -e ORCHESTRATOR_URL=... -e API_KEY=... -e WORKER_ID=... arbitr-worker
```

## Multi-worker per VPS

On a VPS with 4 IPs, run 4 workers:

```bash
# IP 1: 1.2.3.4 → worker 1
PROXY_PORT=10001 PROXY_BIND_IP=1.2.3.4 WORKER_ID=vps-tw-01-w1 python -m worker.main &

# IP 2: 1.2.3.5 → worker 2
PROXY_PORT=10002 PROXY_BIND_IP=1.2.3.5 WORKER_ID=vps-tw-01-w2 python -m worker.main &

# etc.
```

Each worker:

- Runs its own `microsocks` process
- Uses its own outgoing IP
- Registers separately with the orchestrator
- Gets its own jobs from the queue

## Files

```
worker/
├── __init__.py
├── config.py          # Environment-based configuration
├── models.py          # Internal data models (ScrapeResult, PdfAttachment)
├── proxy.py           # Microsocks lifecycle (start/stop/rebind)
├── client.py          # Orchestrator HTTP client + S3 upload
├── block.py           # Block detection (DDoS-Guard, CAPTCHA, 403)
├── scraper.py         # Wrapper around PlaywrightScraper + FilterPipeline
├── main.py            # Entry point and event loop
├── requirements.txt   # For standalone pip installs
└── README.md          # This file
```

## What the worker does NOT do

- ❌ Talk to PostgreSQL
- ❌ Run a web server
- ❌ Store cases locally
- ❌ Run ML classification
- ❌ Make infrastructure API calls (Timeweb)
- ❌ Decide rotation policy

These are all orchestrator responsibilities. The worker is intentionally dumb.
