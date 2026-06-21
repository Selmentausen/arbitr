# Arbitr — Test Deployment Guide

**Goal:** Run a single end-to-end test with 1 orchestrator + 1 worker.\  
**PDFs:** Extracted text is saved in the case data. PDF bytes are NOT uploaded.\  
**S3:** Not needed for this test.\  
**Assumptions:** You have 2 servers ready: 1 central server + 1 worker VPS.

---

## 1. Pre-flight Checklist

Before you start, ensure:

- [ ] Central server has PostgreSQL running (managed or docker)
- [ ] You can SSH into both servers
- [ ] Git repo is cloned on both servers (or you can `scp` files)
- [ ] Python 3.14+ and Poetry are installed on both servers
- [ ] `configs/judges.txt` exists (or create a short one with 3–5 judge names)

**Minimal `configs/judges.txt` for testing:**
```
Титова О. А.
Иванов И. И.
Петрова Е. В.
```

---

## 2. Central Server Setup (Orchestrator)

### 2.1. Clone / update the repo

```bash
git clone https://github.com/YOUR_REPO/Arbitr.git /opt/arbitr
cd /opt/arbitr
poetry install --no-interaction
```

### 2.2. Set environment variables

Create `/opt/arbitr/.env`:

```bash
# Database (your managed PostgreSQL from Timeweb)
DATABASE_URL=postgresql+psycopg2://arbitr:YOUR_PASSWORD@your-pg-host.timeweb.cloud:5432/arbitr

# API key (workers will use this to authenticate)
API_KEY=CHANGE_ME_TO_A_LONG_RANDOM_STRING

# S3 — LEAVE EMPTY for this test (PDFs disabled)
S3_ENDPOINT=
S3_ACCESS_KEY=
S3_SECRET_KEY=

# Timeweb — LEAVE EMPTY for this test (manual rotation only)
TIMEWEB_API_TOKEN=

# Rotation — not used without Timeweb API, but set defaults
ROTATION_MAX_PER_HOUR=3
ROTATION_MAX_PER_DAY=10
ROTATION_COOLDOWN_MINUTES=15

# Worker monitoring
HEARTBEAT_TIMEOUT_MINUTES=10
WORKER_HEARTBEAT_TIMEOUT_MINUTES=5
MONITOR_INTERVAL_SECONDS=60

# Server
HOST=0.0.0.0
PORT=8000
```

### 2.3. Initialize the database

```bash
cd /opt/arbitr
poetry run python -c "from src.storage.database import init_db; init_db()"
```

You should see: `Database initialized (dialect: postgresql)`.

### 2.4. Seed the judge queue

```bash
cd /opt/arbitr
poetry run python -c "
from src.storage.database import get_session
from src.storage.repository import CaseRepository
from src.storage.database import JudgeProgressRecord

session = get_session()
repo = CaseRepository(session)

judges = ['Титова О. А.', 'Иванов И. И.', 'Петрова Е. В.']
for j in judges:
    existing = repo.get_judge_progress(j)
    if not existing:
        session.add(JudgeProgressRecord(judge_name=j, status='pending'))

session.commit()
print(f'Seeded {len(judges)} judges')
"
```

### 2.5. Start the orchestrator

```bash
cd /opt/arbitr
source .env
poetry run uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000
```

You should see:
```
INFO:     Started server process [X]
INFO:     Waiting for application startup.
INFO:     Database initialized (dialect: postgresql)
WARNING:  S3 not configured. PDF uploads will not be available.
WARNING:  Timeweb API not configured. IP rotation will be disabled.
INFO:     Background worker monitor started
INFO:     Application startup complete.
```

**Keep this terminal running.** Open a new terminal for the next steps.

### 2.6. Verify orchestrator is up

From another terminal on the central server:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok","service":"arbitr-orchestrator"}`

Check workers list:

```bash
curl -H "Authorization: Bearer CHANGE_ME_TO_A_LONG_RANDOM_STRING" \
  http://localhost:8000/api/workers
```

Expected: `[]` (empty, no workers yet)

Check judge queue:

```bash
curl -H "Authorization: Bearer CHANGE_ME_TO_A_LONG_RANDOM_STRING" \
  http://localhost:8000/api/dashboard/stats
```

Expected: `total_judges_queued: 3`, `judges_pending: 3`, etc.

---

## 3. Worker VPS Setup

### 3.1. Clone / copy the repo

```bash
git clone https://github.com/YOUR_REPO/Arbitr.git /opt/arbitr
cd /opt/arbitr
poetry install --no-interaction
poetry run playwright install chromium
```

**Alternative:** If you only want the worker files (not the full repo), you can:
```bash
# On the central server, tar the worker + src directories
tar czf worker-package.tar.gz worker/ src/ configs/ pyproject.toml poetry.lock
# Then scp to the VPS
scp worker-package.tar.gz root@worker-vps:/opt/
# On the VPS
cd /opt && tar xzf worker-package.tar.gz
```

### 3.2. Set environment variables

Create `/opt/arbitr/.env.worker`:

```bash
# Worker identity
WORKER_ID=test-worker-1
VPS_ID=vps-test-01

# Central server (use the public IP or hostname of your central server)
ORCHESTRATOR_URL=http://CENTRAL_SERVER_IP:8000
API_KEY=CHANGE_ME_TO_A_LONG_RANDOM_STRING

# Proxy — LEAVE EMPTY for this test (no IP rotation needed)
# When you're ready to test with proxies, set these:
# PROXY_PORT=10001
# PROXY_BIND_IP=WORKER_PUBLIC_IP

# Timing
HEARTBEAT_INTERVAL=30
POLL_INTERVAL=60

# Scraper
CONFIG_PATH=configs/main.yaml
NO_STEALTH=false
```

### 3.3. Start the worker

```bash
cd /opt/arbitr
source .env.worker
poetry run python -m worker.main
```

You should see:
```
INFO:     ============================================================
INFO:     Arbitr Worker starting
INFO:       Worker ID:        test-worker-1
INFO:       VPS ID:           vps-test-01
INFO:       Orchestrator:     http://CENTRAL_SERVER_IP:8000
INFO:       Proxy:            None:None
INFO:     ============================================================
INFO:     Registering with orchestrator...
INFO:     Registration successful: { ... }
INFO:     Polling for next job...
INFO:     Claimed judge: Титова О. А.
```

**Keep this terminal running.**

---

## 4. Verify the Test Run

### 4.1. Check worker is active

On the central server:

```bash
curl -H "Authorization: Bearer CHANGE_ME_TO_A_LONG_RANDOM_STRING" \
  http://localhost:8000/api/workers
```

Expected: A single worker with `status: active`, `current_judge: Титова О. А.`

### 4.2. Watch the worker scrape

The worker will:
1. Search for cases by judge
2. Apply Stage 1 filters
3. Enrich each case (open case page, parse HTML)
4. Download PDFs and extract text
5. Submit cases to the orchestrator

**Normal log output:**
```
INFO:     Collecting cases for judge: Титова О. А.
INFO:     Stage 1: 50 cases → 42 after filter
INFO:     Enriched 5/42 cases for judge Титова О. А.
INFO:     Submitting 42 cases for judge Титова О. А.
INFO:     PDF uploads: 0 successful, 0 failed (S3 not configured)
INFO:     Judge Титова О. А. completed: 42 cases, 15 PDFs
```

### 4.3. Check cases in the database

On the central server:

```bash
cd /opt/arbitr
poetry run python -c "
from src.storage.database import get_session
from src.storage.repository import CaseRepository

session = get_session()
repo = CaseRepository(session)
stats = repo.get_stats()
print(f'Total cases: {stats.get(\"total\", 0)}')
print(f'Judges completed: {stats.get(\"completed\", 0)}')
"
```

Expected: `Total cases > 0`, `Judges completed: 1`.

### 4.4. Check judge queue status

```bash
curl -H "Authorization: Bearer CHANGE_ME_TO_A_LONG_RANDOM_STRING" \
  http://localhost:8000/api/dashboard/stats
```

Expected: `judges_completed: 1`, `judges_pending: 2`.

### 4.5. Verify extracted text is stored

```bash
cd /opt/arbitr
poetry run python -c "
from src.storage.database import get_session
from sqlalchemy import text

session = get_session()
# Get first case with pdf_texts
result = session.execute(text('SELECT id, case_number, pdf_texts FROM cases WHERE pdf_texts IS NOT NULL LIMIT 1'))
row = result.fetchone()
if row:
    print(f'Case: {row.case_number}')
    print(f'PDF texts count: {len(row.pdf_texts or [])}')
    if row.pdf_texts:
        print(f'First text preview: {row.pdf_texts[0][:200]}...')
else:
    print('No cases with pdf_texts found yet')
"
```

---

## 5. Test the Block → Wait → Resume Flow (Manual)

Since you don't have the Timeweb API configured yet, you can manually simulate the rotation flow to verify the worker responds correctly.

### 5.1. Send a block report manually

```bash
curl -X POST \
  -H "Authorization: Bearer CHANGE_ME_TO_A_LONG_RANDOM_STRING" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Manual test block"}' \
  http://localhost:8000/api/workers/test-worker-1/blocked
```

### 5.2. Queue a rotation command manually

```bash
curl -X POST \
  -H "Authorization: Bearer CHANGE_ME_TO_A_LONG_RANDOM_STRING" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://localhost:8000/api/fleet/rotate/test-worker-1
```

**Note:** Without Timeweb API configured, the rotation will fail. But the command queue mechanism works. The worker will receive the command on its next heartbeat.

### 5.3. Check the worker logs

On the worker VPS, you should see:
```
INFO:     Worker blocked. Waiting for IP rotation command from orchestrator...
```

And later (after the rotation command is queued, if Timeweb is configured):
```
INFO:     Executing command: rotate_ip
INFO:     IP rotation: 1.2.3.4 -> 5.6.7.8
INFO:     IP rotation complete. New IP: 5.6.7.8
```

---

## 6. Run Multiple Workers (Same VPS, No Proxy)

You can test multiple workers on the same VPS without proxies by using different worker IDs:

```bash
# Terminal 1
WORKER_ID=test-worker-1 poetry run python -m worker.main

# Terminal 2
WORKER_ID=test-worker-2 poetry run python -m worker.main
```

Both workers will share the same IP (no proxy), which is fine for testing. They'll claim different judges from the queue.

---

## 7. Enable Proxy for Real Test (1 Worker, 1 IP)

If your VPS has a second IP assigned, configure the proxy:

```bash
# On the worker VPS
sudo apt-get update && sudo apt-get install -y microsocks

# Check available IPs
ip addr show
# Look for your secondary IP (e.g., 85.119.150.42)
```

Edit `.env.worker`:
```bash
PROXY_PORT=10001
PROXY_BIND_IP=85.119.150.42
```

Restart the worker:
```bash
source .env.worker
poetry run python -m worker.main
```

You should see:
```
INFO:     Proxy started on 85.119.150.42:10001
```

---

## 8. Production Deployment Checklist

After the test succeeds, here's what to do for production:

### 8.1. Central Server
- [ ] Set up systemd service for orchestrator (auto-restart on crash)
- [ ] Configure reverse proxy (nginx / Caddy) for HTTPS
- [ ] Set up log rotation
- [ ] Configure S3 provider (Timeweb, Yandex, or Selectel) and set `S3_*` env vars
- [ ] Configure Timeweb API token and set `TIMEWEB_API_TOKEN`
- [ ] Set up monitoring (Prometheus or at least log aggregation)

### 8.2. Worker VPS (per VPS)
- [ ] Install microsocks: `apt-get install microsocks` (or build from source)
- [ ] Assign multiple IPs to the VPS (one per worker)
- [ ] Create systemd service for each worker (`deploy/worker@.service` template)
- [ ] Set up log rotation
- [ ] Create a golden VM image (snapshot) with everything pre-installed

### 8.3. Scale-out
- [ ] Order additional VPS instances from the golden snapshot
- [ ] Run 4 workers per VPS (4 IPs per VPS)
- [ ] Monitor: throughput per worker, block rate, cost per case
- [ ] Adjust rotation rate limits if block rate is too high

---

## 9. Troubleshooting

### Problem: Worker can't connect to orchestrator
```
Connection refused to http://CENTRAL_SERVER_IP:8000
```
**Fix:**
- Check orchestrator is running: `curl http://localhost:8000/health` on central server
- Check firewall: `ufw status` — port 8000 must be open
- Check the IP is correct (use the public IP, not localhost)

### Problem: Worker shows "No jobs available"
```
INFO:     No jobs available, sleeping 60s...
```
**Fix:**
- Seed judges: `POST /api/fleet/seed-judges`
- Check stats: `GET /api/dashboard/stats` — should show `judges_pending > 0`
- Check database connection: orchestrator logs should show PostgreSQL dialect

### Problem: Worker fails to scrape
```
ERROR:    Scraper error for judge X: JudgeCourtNotFoundError
```
**Fix:**
- The judge name must match the autocomplete exactly (e.g., `Титова О. А.`)
- Check `configs/main.yaml` → `scraping.target_court_filter` is set to `АС города Москвы`
- Try `NO_STEALTH=true` if DDOS-Guard is blocking the browser

### Problem: Orchestrator shows "S3 not configured"
```
WARNING:  S3 not configured. PDF uploads will not be available.
```
**Fix:** This is expected for the test run. Extracted text is still saved.\  
To enable S3, set `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` in `.env`.

### Problem: Worker gets blocked immediately
```
WARNING:  Block detected after search: DDoS-Guard challenge detected
```
**Fix:**
- The site blocks non-Russian IPs. Ensure your VPS is in Russia.
- Try `NO_STEALTH=true` (sometimes stealth mode triggers the block).
- If you have a proxy configured, try a different IP.
- Without proxy, the worker uses the VPS's main IP — if that's blocked, you need proxy rotation.

### Problem: Database connection errors
```
psycopg2.OperationalError: could not connect to server
```
**Fix:**
- Verify `DATABASE_URL` is correct
- Check PostgreSQL is accessible from the central server
- Check firewall rules (port 5432 must be open to the central server IP)
- If using PgBouncer, verify it's running: `docker ps | grep pgbouncer`

---

## 10. Rollback

If you need to stop everything:

```bash
# On the worker VPS
Ctrl+C in the worker terminal
# Or if running via systemd:
systemctl stop worker@10001

# On the central server
Ctrl+C in the orchestrator terminal
# Or:
kill $(lsof -t -i:8000)
```

To clear the test data and start fresh:

```bash
cd /opt/arbitr
poetry run python -c "
from src.storage.database import get_session
from sqlalchemy import text
session = get_session()
session.execute(text('DELETE FROM cases'))
session.execute(text('DELETE FROM judge_progress'))
session.execute(text('DELETE FROM worker_status'))
session.commit()
print('Test data cleared')
"
```

---

**Questions?** Check the worker logs first (`worker/main.py` logs everything),\  
then the orchestrator logs, then the database.
