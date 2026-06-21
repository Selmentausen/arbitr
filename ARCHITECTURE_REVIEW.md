# Arbitr — Distributed Architecture Plan Review

**Date:** 2026-06-21
**Reviewer:** Agent analysis
**Scope:** `implementation_plan.md` vs. current codebase (`orchestrator/`, `worker/`, `src/`, `deploy/`)

---

## Executive Summary

**The plan is directionally sound and the existing codebase is surprisingly well-advanced.** Much of the "hard" infrastructure work is already implemented: PostgreSQL/SQLite dual support, `FOR UPDATE SKIP LOCKED` job claiming, worker heartbeat monitoring, stale reclamation, FastAPI routers, and the worker event loop. **The plan understates the progress already made and overstates the ease of remaining tasks.**

**Estimated realistic effort:** Not 11–15 days. More like **4–6 weeks** if you want 40 workers running reliably in production, because the remaining work is in the messy 20% of distributed systems: failure modes, observability, data consistency, and operational tooling.

---

## What is Already Done (and Done Well)

| Area | Status | Notes |
|------|--------|-------|
| `database.py` | ✅ Production-ready | JSONB/JSON auto-detection, PG connection pooling, `worker_status`, `judge_progress` tables all exist. |
| `repository.py` | ✅ Distributed-ready | `claim_next_judge()` with `FOR UPDATE SKIP LOCKED` for PG + SQLite fallback. `reclaim_stale_judges()`, worker CRUD, blocked handling. |
| `orchestrator/` | ✅ Core infra ready | FastAPI app, lifespan, all 5 routers, Pydantic schemas, auth middleware, background monitor. |
| `worker/` | ✅ Skeleton ready | Registration → heartbeat → claim → scrape → submit loop exists. Block detector is sensible. |
| `deploy/` | ✅ Good start | docker-compose with PgBouncer, Redis, MinIO, orchestrator, dashboard. `setup_vps.sh` exists. |
| `pyproject.toml` | ✅ Dependencies ready | FastAPI, uvicorn, minio, psycopg2, redis, httpx all listed. |

**This is not a from-scratch rewrite. It's an incremental build on top of solid foundations.**

---

## Critical Concerns & Risks

### 1. The IP Rotation Strategy is Inconsistent

**The plan describes two mutually exclusive rotation strategies and mixes them up.**

- **Option B diagram (Section 3):** "IP rotation without VPS destruction — release blocked IP, order new one (API call). Server stays up, only the blocked worker restarts with new IP."
- **Section 6 (Block Detection):** "DELETE /api/v1/servers/{server_id} — destroy blocked VPS" and "create new VPS from snapshot."

These are **different**. Option B is "swap IP, keep VPS" (fast, ~30s). Section 6 is "destroy VM, recreate" (slow, ~3 min). You need to pick one and align the code.

**Recommendation:** Go with **Option B's IP-swapping** (the whole point of Option B is avoiding VM churn). The orchestrator's `fleet.py` `rotate_worker` endpoint should call the Timeweb API to:
1. Release the blocked IP from the VPS.
2. Order a new floating IP.
3. Assign it to the same VPS via Timeweb API or SSH.
4. Update the worker's proxy binding (restart microsocks on the new IP).
5. Update `worker_status.ip_address` in the DB.
6. Reset worker status to `active` and let it re-register.

**Fix needed:** Rewrite the rotation flow in the plan and the `fleet.py` TODO.

---

### 2. 5 Playwright Instances on 8GB RAM is Optimistic

**The plan estimates "~1.5GB RAM each = ~7.5GB used" for 5 workers per VPS.**

In reality:
- **Chromium + Playwright overhead per browser:** 1.5–2.5 GB at steady state, but **first launch can spike to 3+ GB**.
- **5 simultaneous launches** on a fresh VPS would likely OOM-kill.
- **OS overhead:** Ubuntu + systemd + 5× microsocks + monitoring = ~1 GB.
- **Total realistic:** 5 × 2.0 GB + 1 GB = **11 GB**. You're oversubscribed on 8 GB.

**Recommendation:**
- **Option 1:** Reduce to **4 workers per VPS** (8 GB ÷ 4 ≈ 2 GB each). This gives headroom and reduces swap thrashing. You'd need 10 VPS instead of 8. Cost: +2,970₽/mo (still cheaper than Option A).
- **Option 2:** Keep 5 workers but stagger browser launches with a `sleep 15` between each worker's startup.
- **Option 3:** Upgrade to 6 CPU / 12 GB VPS if Timeweb offers it.

**Also:** `setup_vps.sh` installs `microsocks` instead of `3proxy` (which the plan mentions). `microsocks` is simpler but needs 5 separate processes. That's fine — just be consistent in the docs.

---

### 3. The Worker Does Not Actually Handle PDFs or Documents

**`worker/main.py` currently only does case collection + enrichment. It does NOT:**
- Call `download_pdfs_for_case()` during enrichment.
- Extract PDF text via `pdf_extractor.py`.
- Upload documents via `upload_document()`.
- Set `document.storage_key` on any document.

**This is a major gap.** The plan says Phase 5 covers "Modify `pdf_downloader.py` for in-memory bytes + MinIO upload," but the worker's main loop needs to actually invoke this.

**Recommendation:** The worker should:
1. During `scrape_case_page`, after parsing the case card, call `download_pdfs_for_case()` with `storage_dir=None` (or a temp path) to get bytes in memory.
2. Extract text with `pdf_extractor.extract_from_bytes()` (or add this method — currently the extractor reads from disk paths).
3. Upload each PDF to the orchestrator via `/api/documents/upload`.
4. The orchestrator stores the bytes in MinIO and returns a `storage_key`.
5. The worker includes `storage_key` in the case submission.

**Without this, your 20M cases will have no documents attached.**

---

### 4. The Case Batch Submission is Brittle and Slow

**`orchestrator/routers/cases.py` manually reconstructs every nested Pydantic object from a flat dict.** This is:
- **Slow:** 50 cases × nested loops = lots of CPU.
- **Brittle:** If the Pydantic model changes, this breaks silently.
- **Unnecessary:** The worker already serializes `Case` objects. The orchestrator can deserialize them directly.

**Current code:**
```python
for p in case_data.participants:
    participants.append(CaseParticipant(...))
for inst in case_data.instances:
    docs = []
    for d in inst.get("documents", []):
        docs.append(CaseDocument(...))
    ...
```

**Better approach:**
```python
from src.models.case import Case
case = Case.model_validate(case_data)
repo.save_case(case)
```

But this requires the `CaseSubmission` schema to match the `Case` model exactly (or use `Case.model_validate` with `from_attributes=True`). Since you already have a good Pydantic model, **use it directly**.

**Also:** The worker's `_case_to_submission` uses `case_data.id` (case UUID) but the `CaseSubmission` schema has `id` as a string. It should be fine, but ensure no type mismatches on dates (Pydantic v2 `datetime` serialization over JSON can be tricky).

---

### 5. No Retry / Backoff on Worker → Orchestrator Calls

**`OrchestratorClient` uses `httpx.AsyncClient` with no retries.**

In a distributed system over the public internet:
- Transient 502s/503s from the orchestrator (e.g., during DB failover) will fail batches.
- A worker uploading 50 cases will lose all of them on one `HTTPError`.
- The `fail_job()` endpoint is called on any exception, but many exceptions are recoverable.

**Recommendation:**
- Add `httpx` retries with exponential backoff (e.g., 3 retries, 1s/2s/4s backoff) for idempotent operations (`heartbeat`, `claim_job`, `submit_cases`).
- For `submit_cases`, make it idempotent by including a `batch_id` (UUID generated by the worker) so the orchestrator can deduplicate if the same batch is submitted twice.
- Distinguish between "network failure" (retry) and "logic failure" (fail the job).

---

### 6. Block Detection Only Happens During Collection, Not Enrichment

**`worker/main.py` checks `detect_block` after `search_by_judge()` but never during the enrichment loop.**

If a worker gets blocked mid-enrichment (e.g., after 20 case pages), it will:
1. Keep trying to load case pages.
2. Fail each page with an exception.
3. Eventually call `fail_job()` with a generic error.
4. The judge goes back to `failed` status, not `pending`.

**Recommendation:** Check for blocks inside the enrichment loop. The `PlaywrightScraper` should expose `last_response_html` after each page navigation, and the worker should call `detect_block()` periodically. If blocked, `report_blocked()` and return `-1` immediately so the orchestrator can rotate the IP and reclaim the judge as `pending` (not `failed`).

---

### 7. PgBouncer with Managed PostgreSQL May Not Work

**The plan says "PgBouncer" but also "Timeweb Managed PostgreSQL."** Most managed PostgreSQL providers (AWS RDS, Google Cloud SQL, etc.) do not allow external PgBouncer instances to connect through their private network unless you're in the same VPC. Timeweb's managed PG likely runs on a different network than your central VPS.

**Options:**
1. **Run PgBouncer on the central server** and connect to managed PG over the public internet. This adds ~5–20ms latency per query, but works. You need to whitelist the central server IP in Timeweb's PG firewall.
2. **Skip PgBouncer and use SQLAlchemy's built-in pooling.** With 40 workers, you might have 40–80 concurrent DB connections. A 4 CPU / 8 GB managed PG instance can probably handle 100 connections. Check Timeweb's connection limit.
3. **Use a VPS for PostgreSQL instead of managed.** You control everything, but you lose automated backups/patching.

**Recommendation:** Verify Timeweb Managed PG's max connections and whether you can run PgBouncer externally. If max connections ≥ 100, **skip PgBouncer initially** and add it later if you hit connection limits. It's a premature optimization.

---

### 8. MinIO Storage on 80GB SSD is a Hard Blocker for Scale

**The plan says MinIO runs on the central server (80GB SSD).** At 20M cases with PDFs, you need 2–5 TB. This is physically impossible on 80GB.

**The open question suggests deferring PDF storage.** But if you defer PDFs:
- You cannot run ML classification (which requires PDF text extraction).
- You lose the primary value of enrichment (downloading court rulings).
- The dashboard's "PDF categorization" feature breaks.

**Recommendation:** Do not defer PDF storage. Instead, solve it in **Phase 1** before deploying workers:
- **Option A:** Buy Timeweb Block Storage (attach a 1–2 TB disk to the central server). Check pricing.
- **Option B:** Use **Timeweb's S3-compatible Object Storage** if available. This is the cheapest per GB and your MinIO client code already works with any S3 API. Just point `minio_endpoint` to Timeweb's S3 endpoint.
- **Option C:** Use **Yandex Cloud Object Storage** or **Selectel** (both have S3 APIs and are cheap in Russia). You don't need MinIO at all — just use `boto3` or `minio` SDK against their S3 endpoint.

If you go with Option B/C, you can **remove MinIO from the docker-compose** entirely, saving RAM and complexity on the central server.

---

### 9. The Worker Has No Graceful Shutdown for In-Progress Judges

**When a worker receives SIGTERM, it sets `_shutdown = True` and breaks the main loop.** But if it was in the middle of enriching a judge, that judge stays in `collecting`/`enriching` status until the heartbeat monitor reclaims it (up to 10 minutes).

**Recommendation:** In the worker's shutdown handler:
1. Before exiting, call `client.update_progress(judge_name, status="interrupted")` or a new `POST /jobs/{judge}/release` endpoint.
2. The orchestrator should immediately set the judge to `pending` (not wait for heartbeat timeout).
3. This reduces the "lost work" window from 10 minutes to near-zero.

---

### 10. No Rate Limiting or Backpressure on the Orchestrator

**The orchestrator's `/api/cases/batch` has no rate limiting.** 40 workers submitting 50-case batches = 2,000 cases per batch. If all 40 hit at once, that's 80,000 cases in one thundering herd. PostgreSQL will choke.

**Recommendation:**
- Add a `MAX_BATCH_SIZE` limit (e.g., 100 cases).
- Add a per-worker rate limit (e.g., 1 batch per 5 seconds).
- Consider using Redis as a queue: workers publish to a Redis stream, and the orchestrator consumes in a controlled manner. This decouples submission from ingestion and protects the DB.
- Alternatively, use `asyncio.Semaphore` in the orchestrator to limit concurrent DB writes.

---

### 11. Observability is Missing

**For 40 workers, you need to know:**
- Which worker is scraping which judge right now?
- How long has it been since the last case was submitted?
- Is the orchestrator queue draining or backing up?
- Are workers failing repeatedly on the same judge?

**Current state:** The dashboard has a "Fleet" page concept, but no real-time metrics. The background monitor only logs to stdout.

**Recommendation:**
- Add Prometheus metrics (or at least a `/metrics` endpoint with basic counters: `cases_submitted_total`, `judges_claimed_total`, `workers_blocked_total`, `api_request_duration_seconds`).
- Add structured logging to a central location (e.g., JSON logs to a file that can be shipped to Loki/Grafana or just grep'd).
- Add a `GET /api/dashboard/queue` endpoint showing pending judges, in-progress judges, and worker load.

---

### 12. The Plan's Timeline is Unrealistic

| Phase | Plan Estimate | Realistic Estimate | Why |
|-------|---------------|--------------------|-----|
| 1: Deploy central server | 1 day | 2–3 days | Docker networking, PgBouncer vs managed PG, env vars, first-time deployment always takes longer |
| 2: Modify `database.py` | 1 day | ✅ Already done | The code is already PG-ready. |
| 3: Build FastAPI orchestrator | 3–4 days | 1–2 days | Skeleton exists. Need document upload, auth polish, edge cases. |
| 4: Build worker package | 2–3 days | 3–5 days | Skeleton exists. Need PDF pipeline, retries, block detection during enrichment, systemd units, cloud-init. |
| 5: PDF bytes + MinIO | 1 day | 2–3 days | Need to refactor `pdf_downloader` to return bytes, add `extract_from_bytes`, wire into worker loop, test round-trip. |
| 6: Dashboard PG + fleet | 1–2 days | 3–4 days | Dashboard currently reads SQLite. Needs full DB connection rewrite, new fleet page, real-time updates. Streamlit is single-threaded — may need caching. |
| 7: Timeweb automation | 1–2 days | 5–7 days | Timeweb API research, snapshot creation, IP ordering API, proxy re-binding, error handling, testing. This is the hardest phase. |
| 8: Deploy & scale | 1–2 days | 3–5 days | Staged rollout (2→5→10→40 workers), debugging, tuning, cost monitoring. |
| **Total** | **11–15 days** | **20–30 days** | |

**Recommendation:** Start with **2 workers** (1 VPS) and validate the full pipeline before ordering 37 additional IPs. This lets you debug Timeweb API quirks, proxy binding, and worker-orchestrator communication without burning 6,660₽/mo on IPs you might not need.

---

### 13. Testing Strategy is Thin

**The verification plan mentions:**
- pytest against PostgreSQL (good)
- Job claiming concurrency test (good)
- PDF round-trip (good)
- Manual block simulation (good)

**Missing tests:**
- Orchestrator restart mid-scrape: does the worker reconnect? Does it re-claim the same judge or a new one?
- Worker crash during enrichment: does the judge get reclaimed with partial data?
- Duplicate case submission: if a worker submits the same case twice, does the DB deduplicate or create duplicates?
- Network partition: worker can't reach orchestrator for 2 minutes. Does it retry or fail the job?
- Timeweb API failure during IP rotation: what happens if `DELETE /api/v1/servers` fails? Is the worker stuck in `rotating` forever?

**Recommendation:** Write chaos-engineering style tests before scaling. Use `pytest-asyncio` to simulate these scenarios.

---

### 14. Data Migration from SQLite is Actually Worth Considering

**The plan says "Start fresh — current SQLite data is only a few days of scraping."** This is reasonable, but check: do you have ML classifications, human reviews, or golden-set data in SQLite that you don't want to lose? If so, write a one-off `sqlite_to_pg.py` migration script. It can be a simple Python script that reads SQLite via `CaseRepository` and writes to PostgreSQL.

---

## Positive Highlights (Don't Lose These)

1. **Job claiming with `FOR UPDATE SKIP LOCKED`:** This is exactly right. It prevents race conditions without locking the whole table.
2. **Dual SQLite/PostgreSQL support:** Lets you develop locally without a PG server. Keep this.
3. **Worker statelessness:** The worker has no DB credentials, no file state. This is perfect for auto-scaling.
4. **Staleness-based reclamation:** The 10-minute heartbeat timeout + 5-minute worker stale detection is a good balance.
5. **Block detection module:** `block_detector.py` is clean and extensible.
6. **Poetry + Pydantic v2 + SQLAlchemy 2:** Modern stack, good choices.
7. **Existing docker-compose:** Good separation of concerns (PgBouncer, Redis, MinIO, orchestrator, dashboard).

---

## Recommended Revised Plan

### Phase 0: Validate Foundations (Before Spending Money)
- [ ] Decide on PDF storage: **Timeweb S3-compatible Object Storage** or Yandex/Selectel S3. Remove MinIO from docker-compose if using external S3.
- [ ] Verify Timeweb Managed PG max connections. Decide on PgBouncer vs. SQLAlchemy pooling.
- [ ] Fix `cases.py` batch submission to use `Case.model_validate()` directly.
- [ ] Add retry logic to `OrchestratorClient`.
- [ ] Add graceful shutdown with judge release.
- [ ] Cost estimate: 0₽ (local testing).

### Phase 1: Central Server (Local Docker)
- [ ] Run orchestrator + dashboard + PostgreSQL locally via docker-compose.
- [ ] Seed `judge_progress` with 5 test judges.
- [ ] Run 2 workers locally (different terminals, different `WORKER_ID`).
- [ ] Verify: job claiming, case submission, dashboard fleet page.
- [ ] Cost estimate: 0₽.

### Phase 2: PDF Pipeline (Local)
- [ ] Refactor `pdf_downloader.py` to return `bytes` + metadata (no file write).
- [ ] Add `extract_from_bytes()` to `pdf_extractor.py`.
- [ ] Worker: during enrichment, download PDFs → extract text → upload to orchestrator.
- [ ] Orchestrator: store in S3 (or MinIO), return `storage_key`.
- [ ] Verify PDF round-trip: worker uploads → orchestrator stores → dashboard serves.
- [ ] Cost estimate: 0₽.

### Phase 3: First VPS (2 Workers, 1 Extra IP)
- [ ] Order 1 Timeweb VPS (4 CPU / 8 GB / 80 GB) + 1 extra IP.
- [ ] Run `setup_vps.sh` on it.
- [ ] Bind 2 microsocks instances to the 2 IPs.
- [ ] Run 2 workers with `PROXY_PORT` set.
- [ ] Verify end-to-end scraping, block detection, and IP rotation (manually via `fleet/rotate` endpoint).
- [ ] Cost estimate: ~1,485₽ + 180₽ = ~1,665₽/mo.

### Phase 4: Timeweb API Automation (Still 1 VPS)
- [ ] Build `orchestrator/services/timeweb_client.py`.
- [ ] Implement IP rotation: release old IP, order new IP, update OS, restart proxy.
- [ ] Test: simulate block → auto-rotate → worker resumes.
- [ ] Cost estimate: same as Phase 3.

### Phase 5: Scale to 8 VPS × 4 Workers = 32 Workers
- [ ] Order 7 more VPS + 30 more IPs (total 8 VPS, 32 workers, 32 IPs).
- [ ] Use a golden snapshot (VPS image with everything pre-installed) instead of `setup_vps.sh` per VM.
- [ ] Deploy systemd units for all workers.
- [ ] Monitor: throughput, cost, block rate, DB connection count.
- [ ] Cost estimate: 8×1,485₽ + 31×180₽ + 1,485₽ (central) + 7,000₽ (PG) = ~21,905₽/mo.

### Phase 6: Scale to 40 Workers (Optional)
- [ ] Add 2 more VPS if block rate is high.
- [ ] Monitor cost vs. throughput. If marginal cost per case is too high, stop.
- [ ] Cost estimate: ~27,025₽/mo.

---

## Final Verdict

**The architecture is correct. The code is further along than the plan suggests. The main risks are operational, not architectural.**

1. **Fix the IP rotation strategy** to be consistent (IP swap, not VM destroy).
2. **Add PDF handling to the worker** before scaling — without it, you're just collecting metadata.
3. **Start with 1 VPS + 2 workers** to validate Timeweb quirks before committing to 27k₽/mo.
4. **Add retries, graceful shutdown, and rate limiting** before going to 32+ workers.
5. **Consider external S3 instead of MinIO** to avoid the 80GB disk bottleneck entirely.

You've built a solid foundation. The next steps should be **validation, not more architecture.**
