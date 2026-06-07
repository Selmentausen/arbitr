# Arbitr — Project State (Agent Handoff)

**Last updated:** 2026-06-07  
**Purpose:** Read this file at the start of a new agent conversation instead of scanning the repo.  
**Design spec (older):** `Arbitr_document.md` (architecture intent, Feb 2026).  
**User README:** `README.md` is **outdated** (still says "Phase 1 only" and PostgreSQL as primary DB).

---

## 1. What this project is

**Arbitr** scrapes Russian arbitration court cases from [kad.arbitr.ru](https://kad.arbitr.ru), scores them for relevance (focus: **construction contract disputes** / mediation potential), stores results in a local DB, and reviews them in a **Streamlit dashboard**.

**MVP scope:** Moscow Arbitration Court (`АС города Москвы`), judge-based search, keyword filtering, optional deep scrape of case card HTML, PDF download, and batch/on-demand ML legal-area classification for reviewed cases.

**Scale context:** Site has 45M+ cases; approach is incremental (by judge, capped per judge, human-like delays, optional residential proxies).

---

## 2. Implementation status (truth table)

| Area | Status | Notes |
|------|--------|-------|
| Config / models / logging | Done | `ConfigManager`, Pydantic `Case`/`CaseBase`, `src/utils/logger.py` |
| Playwright scraper | **Working** | `PlaywrightScraper` — UI search, API pagination, case page enrichment |
| Parsers | **Working** | List page (`parser.py`), case card (`parser_case_page.py`) |
| Filter Stage 1 | **Working** | Keyword + judge groups + reject keywords (global). `reject_enabled` flag in config (currently `false`) |
| Filter Stage 2 | **Partial** | `stage2_html_analyze` exists; runs **only after** `batch_enrich_cases`, not inside `process_case` |
| Filter Stage 3 (PDF text) | **Implemented for ML batch** | `src/analysis/pdf_extractor.py` extracts downloaded PDFs with PyMuPDF; not wired into live scrape filter pipeline |
| Filter Stage 4 (LLM/ML) | **Working as batch/on-demand** | Local Ollama classifier (`qwen2.5:14b` default, `7b` fast) scores legal-area probabilities; not wired into live scrape pipeline |
| PDF Download | **Working** | `src/scraper/pdf_downloader.py` — response listener captures PDF bytes from browser; content-addressed flat storage |
| Storage | **SQLite (not Postgres)** | `data/arbitr.db` via SQLAlchemy; rich schema (cases, participants, instances, documents, scrape events) |
| Linkage module | **Stub** | `src/linkage/__init__.py` — Phase 5 placeholder |
| Dashboard | **Working** | Streamlit: overview, case list, dedicated ML review page, search, PDF categorization, export, live scrape monitor |
| Parallel scrape | **Working** | `scrape_parallel.py` + `ParallelScrapeRunner` — queue of judges, N workers |
| Bandwidth test | **Working** | `bandwidth_test.py` — single-judge deep crawl with PDF traffic measurement |
| Docker Postgres/Redis | **Infra only** | `docker-compose.yml` ready; app does not use them yet |

---

## 3. Tech stack

- **Python 3.14+**, **Poetry** (`pyproject.toml`)
- **Playwright** + **playwright-stealth** (anti-bot)
- **BeautifulSoup** (HTML parse)
- **Pydantic v2**, **SQLAlchemy 2**, **SQLite** file DB
- **Streamlit** + Plotly dashboard
- **PyYAML** configs; keyword lists in `configs/dictionaries/`
- **psutil** (NIC counters for bandwidth test)
- Planned but unused in runtime: PostgreSQL, Redis, Alembic migrations, OpenAI. Local Ollama ML + PDF text extraction are implemented as batch/on-demand analysis, not live scrape filtering.

### Poetry scripts

```bash
poetry run scrape              # scrape_to_dashboard.py — single judge/court run
poetry run scrape-parallel     # scrape_parallel.py — all judges in judges.txt
poetry run bandwidth-test      # bandwidth_test.py — 1 judge deep crawl traffic measurement
poetry run classify            # classify_cases.py — Ollama legal-area classification
poetry run classify-eval       # classify_eval.py — golden-set classifier evaluation
poetry run dashboard           # Streamlit app
```

---

## 4. Repository layout (what matters)

```
Arbitr/
├── configs/
│   ├── main.yaml              # thresholds, scraping delays, proxy, parallel settings, filtering flags
│   ├── classification.yaml    # Ollama model, prompt, few-shot examples, category taxonomy
│   ├── classification_eval.yaml # manual golden set for classifier evaluation
│   ├── areas/construction.yaml
│   └── dictionaries/          # keywords, judges.txt (~99 judges), global reject, etc.
├── src/
│   ├── models/case.py         # CaseBase, Case, StatusEnum, nested instance/doc models
│   ├── config/manager.py      # YAML + area dict loading
│   ├── config/classification.py # ML classification config loader
│   ├── analysis/              # Ollama classifier, prompt/context builders, PDF text extraction
│   ├── scraper/
│   │   ├── playwright_scraper.py  # main scraper
│   │   ├── parser.py                # search results table
│   │   ├── parser_case_page.py      # case card HTML
│   │   ├── pdf_downloader.py        # PDF download via response listener + content-addressed storage
│   │   ├── judge_loader.py          # judges.txt → JudgeEntry
│   │   ├── runner.py                # ParallelScrapeRunner
│   │   ├── traffic_tracker.py       # NetworkTrafficTracker for bandwidth measurement
│   │   ├── api_client.py            # KadApiClient (secondary)
│   │   └── collector.py             # older collector helper
│   ├── filters/
│   │   ├── pipeline.py        # FilterPipeline (reject_enabled flag, cases_for_enrichment)
│   │   ├── stage1_screen.py
│   │   └── stage2_screen.py
│   ├── storage/
│   │   ├── database.py        # SQLAlchemy models + init_db
│   │   └── repository.py      # CaseRepository CRUD, stats, scrape events
│   └── linkage/               # empty stub
├── dashboard/app.py           # Streamlit UI
├── scrape_to_dashboard.py     # single-run E2E pipeline
├── scrape_parallel.py         # multi-worker judge queue
├── classify_cases.py          # batch/on-demand ML legal-area classification
├── classify_eval.py           # manual golden-set classifier evaluation
├── bandwidth_test.py          # single-judge deep crawl bandwidth test
├── test_pdf_live.py           # quick PDF download verification (one case)
├── data/
│   ├── arbitr.db              # runtime DB (gitignored)
│   ├── pdfs/                  # production PDF storage (gitignored)
│   └── pdfs_test/             # test PDF storage (gitignored)
├── tests/                     # pytest: config, models, filters, storage, pdf_downloader, classification
├── Arbitr_document.md         # original architecture doc (not current state)
└── PROJECT_STATE.md           # this file
```

---

## 5. End-to-end data flow

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
        ↓                                 # if pdf_download_enabled: downloads PDFs via response listener
FilterPipeline.process_stage2_batch()   # stage2 keywords on enriched fields
        ↓
CaseRepository.save_cases()  →  data/arbitr.db
        ↓
streamlit run dashboard/app.py
```

**Parallel path:** `ParallelScrapeRunner` — asyncio queue, one browser per worker, optional **distinct proxy port per worker** (`scraping.proxy.forced_port`), scrape events logged to DB for dashboard "Live" page.

### ML classification path

```
CaseRepository.list_cases_for_classification()
        ↓
src/analysis/pdf_extractor.py       # reads local PDFs from data/pdfs with PyMuPDF
        ↓
src/analysis/context_builder.py     # compact case dossier: parties, metadata, chronology, PDF excerpts
        ↓
src/analysis/prompt_builder.py      # categories + few-shot examples + disambiguation rules
        ↓
src/analysis/ollama_client.py       # /api/chat, format=json
        ↓
src/analysis/classifier.py          # validate/normalize probabilities
        ↓
case.extracted_data["ml_classification"] + optional category/relevance_score update
        ↓
Dashboard "ML — Проверка" review page
```

**Important:** ML classification is currently **batch/on-demand only** (`poetry run classify` or dashboard button). It is not called from `scrape_to_dashboard.py`, `scrape_parallel.py`, or `FilterPipeline.process_case`.

---

## 6. Scraper behavior (important details)

**Class:** `src/scraper/playwright_scraper.py` — `PlaywrightScraper`

**Search flow:**
1. Open `https://kad.arbitr.ru`, warm session (DDOS-Guard challenge resolved by browser).
2. Type judge name in autocomplete (`Фамилия И. О.` format from `judge_loader`).
3. Pick suggestion matching `scraping.target_court_filter` (default: `АС города Москвы`).
4. Paginate via internal API `fetch_api_page` (court id from `COURT_MAP`, judge id from suggest).

**Enrichment (`batch_enrich_cases`):** For eligible cases — navigate to `case_url`, click "+" on instance chronology, parse participants/instances/documents into `Case` model. If `pdf_download_enabled`, also downloads PDFs.

**Delays:** All tunable under `configs/main.yaml` → `scraping.delays.*` (seconds, per action).

**Proxy:** `scraping.proxy.enabled` in config (credentials in yaml — treat as secret). Parallel runner assigns `port_range` ports per worker. `scrape_to_dashboard.py` **forces proxy off** at runtime.

**Errors:** `JudgeCourtNotFoundError` if autocomplete has no row for target court.

---

## 7. PDF Download

**Module:** `src/scraper/pdf_downloader.py`

**How it works:** kad.arbitr.ru serves PDFs behind DDOS-Guard. Direct HTTP requests (`page.request.get`, `fetch()` in JS, `route.fetch()`) all get HTML stubs. The browser itself loads the real PDF through a redirect chain:

1. Initial request → GIF tracking pixel (DDOS-Guard)
2. 301 redirect → challenge page (HTML)
3. Challenge resolved → real PDF served (`application/pdf`)

**Solution:** Register a `context.on("response")` event listener that captures any response with `content-type: application/pdf` and body starting with `%PDF`. Falls back to `Ctrl+S` download if listener doesn't capture.

**Storage:** Flat content-addressed directory (`data/pdfs/`). Filenames: `{readable_name}_{sha256[:16]}.pdf`. Automatic deduplication — same content = same hash = skip. No per-case folder nesting.

**Typical PDF size:** ~200–320 KB per document (court rulings, decisions).

**Config flags:**
- `filtering.pdf_download_enabled: true` — master switch
- `scraping.pdf_storage_dir: data/pdfs` — storage location

**PDF text extraction for ML:** `src/analysis/pdf_extractor.py` uses PyMuPDF (`fitz`) to extract local downloaded PDFs before Ollama classification. It fills `case.pdf_texts` and `CaseDocument.extracted_text`, then stores them through the existing JSON/DB columns. It does **not** download missing PDFs and does **not** run during scraping.

**Test script:** `test_pdf_live.py` — opens one known case, warms session, expands chronology, downloads all PDFs, reports pass/fail per file. Human-like delays throughout.

---

## 8. Filtering

### StatusEnum (`src/models/case.py`)

- `high_relevant`, `reject`, `insufficient_info`, `uncertain`

### Config flags (`configs/main.yaml` → `filtering`)

- `reject_enabled: false` — when false, cases that would be rejected become `uncertain` instead, and all cases with URLs proceed to enrichment
- `pdf_download_enabled: true` — enables PDF download during enrichment

### Stage 1 (`stage1_initial_screen`)

- Area keywords from `configs/areas/*.yaml` → dictionary files
- Global reject: `configs/dictionaries/global_reject_keywords.txt`
- Judge group bonuses from `main.yaml` → `judge_groups`
- Thresholds: `high: 80`, `low: 20`, gray zone 40–60 remains config-only for the live filter pipeline
- Fixed bugs: `matched_area` → `best_area_name`, `len(matched_keywords - 1, 3)` → `min(len(...) - 1, 3)`

### Stage 2 (`stage2_html_analyze`)

- Only cases still `uncertain` / `insufficient_info` after enrichment
- Uses `stage2_keywords` from `configs/dictionaries/construction/stage2.txt`
- **Not** called from `FilterPipeline.process_case` — only via `process_stage2_batch` after scrape enrichment

### Stages 3–4 / ML classifier

- `FilterPipeline.process_case()` still has commented placeholders; live scrape filtering remains Stage 1 + post-enrichment Stage 2.
- A separate ML classifier exists under `src/analysis/` and is run by `classify_cases.py` or from the dashboard.
- Classifier taxonomy: `construction`, `bankruptcy`, `other`.
- Default model: local Ollama `qwen2.5:14b`; fast iteration model: `qwen2.5:7b`.
- Output stored in `case.extracted_data["ml_classification"]`: probabilities, primary category, confidence, reasoning, key signals, uncertainty, prompt version, model, analyzed_at.
- New classifications also store `prompt_audit` with the exact system/user prompts and PDF inclusion stats.
- If confidence ≥ `thresholds.auto_assign_category` in `configs/classification.yaml` and primary category is not `other`, `case.category` and `relevance_score` may be updated.

---

## 9. Bandwidth measurement

**Script:** `bandwidth_test.py` — single-judge deep crawl (default 200 cases, going back in time to find cases with actual documents).

**Approach:**
- `TrackingScraper` extends `PlaywrightScraper` with `NetworkTrafficTracker` (counts HTTP bytes via browser context events)
- Separate `pdf_stats` for PDF-specific traffic
- Reports: browser traffic, PDF traffic, combined, extrapolation per-hour/day, per-case averages, system NIC comparison

**Usage:**
```bash
poetry run bandwidth-test                                    # 10 min, first judge, 200 cases
poetry run bandwidth-test --judge "Солдатов" --max-cases 300 # specific judge, 300 deep
poetry run bandwidth-test --duration-minutes 5 --proxy       # with proxy
```

**Findings:**
- PDFs are ~200–320 KB each (lighter than expected)
- Browser overhead per PDF popup: ~200 KB Chrome PDF viewer assets (JS/CSS)
- DDOS-Guard challenge adds tracking GIFs + redirect overhead per request

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

**ConfigManager** loads all `configs/areas/*.yaml` into `config["areas"]`.

**ClassificationConfig** (`src/config/classification.py`) loads `configs/classification.yaml` separately.

### ML classification config

- Categories: `construction`, `bankruptcy`, `other`
- Ollama: `base_url=http://localhost:11434`, `model=qwen2.5:14b`, `fast_model=qwen2.5:7b`
- Limits: `max_pdf_chars=6000`, `max_total_context_chars=16000`, `max_chronology_entries=20`
- Prompt engineering lives in YAML: few-shot examples, disambiguation rules, system/user templates
- Bump `prompt_version` when changing examples/rules so old and new results can be compared

### Known config bug

`construction.yaml` has typo `reject_keywrods_file` but `manager.py` checks `reject_keywords_file` — **area-specific reject dictionary may not load** (global reject still works).

---

## 11. Database (SQLite)

- **Path:** `data/arbitr.db` (both pipelines and dashboard use this path)
- **Init:** `init_db(DB_PATH)` from `src/storage/database.py`
- **Access:** `CaseRepository` — save/update cases, stats, search, export, scrape event tracking

**Main tables:** `cases`, `participants`, `case_participants`, `documents`, `instances`, `instance_updates`, `judges`, `scrape_events`, `scrape_meta`

**Documents table** has `local_path` column — designed to track where PDFs are stored on disk.

**Review fields on cases:** `reviewed`, `review_notes`, `reviewed_at` (dashboard can mark reviewed)

**ML fields:** No DB migration was added. ML state uses existing columns:
- `cases.extracted_data_json["ml_classification"]` — full classifier output + `prompt_audit`
- `cases.extracted_data_json["ml_review"]` — dashboard human review (`correct` / `wrong`, correct_category, notes, reviewed_at)
- `cases.pdf_texts_json` — extracted PDF text blocks
- `documents.extracted_text` — per-document PDF text when resolved

**Repository helpers:** `CaseRepository.list_cases_for_classification()`, `get_ml_cases()`, `get_ml_stats()`, `save_ml_review()`.

**Note:** README/Docker describe PostgreSQL; **production path today is SQLite file**. Migrating to Postgres would be a deliberate Phase 4 completion task.

---

## 12. Dashboard

**Run:** `poetry run dashboard` or `poetry run streamlit run dashboard/app.py`

**Pages:**
- **Скрапинг — Live** — watches `scrape_events` (parallel runs)
- **Обзор** — stats charts
- **ML — Проверка** — dedicated view for ML-classified cases only; filters by ML category, review verdict, uncertainty, and ML/keyword disagreement
- **Список дел** — browse/filter cases
- **Поиск** — text search
- **Категоризация PDF** — manage document priority dictionary
- **Экспорт** — CSV/JSON export via repository

Uses `@st.cache_resource` for DB connection.

### ML review UI

- Case list is separate from the massive general case list; default page size is fixed at 50.
- Loads cases in a lightweight mode: participants + PDF document metadata, not full chronology.
- Shows party names, claim amount, filing date, court/category/status, PDF count, and clickable PDF links.
- High-priority/downloaded PDFs open local files from `data/pdfs`; others link to kad.arbitr.ru.
- Shows ML probability chart, reasoning, key signals, uncertainty, model, prompt version, and prompt audit.
- Buttons: classify/re-classify, mark ML result correct/wrong, save correct category/notes.
- Prompt inspector: show saved Ollama system/user prompt or rebuild a live preview with PDF extraction without calling Ollama.

---

## 13. Typical commands

```bash
# Install
poetry install
poetry run playwright install chromium

# Single judge scrape (visible browser helps with DDOS-Guard)
poetry run scrape --judge "Солдатов Р. С." --max-cases 25
poetry run scrape --judge "Солдатов Р. С." --max-cases 25 --headless

# Parallel: all judges in judges.txt (5 workers default from config)
poetry run scrape-parallel --workers 5 --max-cases-per-judge 100 --headless

# Bandwidth test (1 judge deep crawl, default 10 min)
poetry run bandwidth-test
poetry run bandwidth-test --judge "Солдатов" --max-cases 300 --proxy

# Quick PDF download test (one known case, no pipeline)
poetry run python test_pdf_live.py
poetry run python test_pdf_live.py --headless

# Dashboard
poetry run dashboard

# ML classification
poetry run classify                         # classify unclassified cases, qwen2.5:14b
poetry run classify --limit 50              # classify next 50 unclassified cases
poetry run classify --fast                  # use qwen2.5:7b for quicker prompt iteration
poetry run classify --case-id <uuid>        # classify one case
poetry run classify --force --case-id <uuid> # reclassify one case
poetry run classify --dry-run --case-id <uuid> # print full Ollama prompt, no API call
poetry run classify --skip-pdf              # metadata/chronology only
poetry run classify-eval                    # evaluate configs/classification_eval.yaml

# Tests
poetry run pytest
```

---

## 14. Testing

- **pytest** under `tests/`: `test_config`, `test_models`, `test_filters`, `test_storage`, `test_stage1_regressions`, `test_pdf_downloader`, `test_stage2`, `test_classification_config`, `test_prompt_builder`, `test_context_builder`, `test_classifier`
- Fixtures: `tests/case_page.html`, `tests/main_page_html_after_load.html`
- Coverage configured in `pyproject.toml` (`--cov=src`)
- 11 pre-existing test failures (config area loading, filter scoring, storage plaintiff field) — unrelated to recent work
- New classification unit tests can be run without Ollama: `poetry run pytest tests/test_classification_config.py tests/test_prompt_builder.py tests/test_context_builder.py tests/test_classifier.py -v --no-cov`

---

## 15. Operational gotchas

1. **DDOS-Guard / CAPTCHA** — scraping fails if blocked; try `headless=False`, slower delays, or enable proxy in config.
2. **Proxy credentials** in `main.yaml` — rotate/replace if expired; `scrape_to_dashboard` disables proxy by default.
3. **Site HTML changes** — parsers in `parser.py` / `parser_case_page.py` break first; check `data/raw_cases.json` or debug HTML dumps.
4. **Judge name format** — site expects `Surname I. O.`; full names in `judges.txt` are converted by `judge_loader`.
5. **README phase checklist** — do not trust it; use section 2 of this doc instead.
6. **Linkage** — still a stub. **LLM/PDF analysis exists only as batch/on-demand ML classification**, not in live scrape filtering.
7. **PDF download** — requires warm session (navigate main page → search first). Direct navigation to PDF URLs returns HTML stubs.
8. **PDF false positives** — DDOS-Guard sometimes returns `content-type: application/pdf` with HTML body. Always verify `%PDF` magic bytes.
9. **Ollama dependency** — `poetry run classify` needs Ollama running locally and the configured model pulled (`qwen2.5:14b`; optional `qwen2.5:7b`).
10. **PDF text inclusion** — use dashboard prompt inspector or `poetry run classify --dry-run --case-id <uuid>` to confirm `Фрагменты PDF:` is included. Missing local PDFs will not be downloaded by the classifier.
11. **Prompt changes** — update `configs/classification.yaml`, bump `prompt_version`, and use `classification_eval.yaml` + `poetry run classify-eval` for objective comparison.

---

## 16. How agents should use this document

**Do:**
- Read `PROJECT_STATE.md` first for context.
- Open only the 1–3 files relevant to the task (paths in sections 4–9).
- Run targeted `grep` if something changed since this doc's date.

**Do not:**
- Full-repo exploration unless this doc is stale or the task requires it.
- Assume PostgreSQL/Redis/OpenAI are active without verifying code. Local Ollama ML is active only via batch/on-demand paths, not live scraping.

**Refresh this doc when:** scraper contract changes, new filter stages wired, DB backend switches, or major new entry points added.

---

## 17. Product roadmap (from design, not all built)

1. **Bandwidth optimization** — block Chrome PDF viewer assets, tracking GIFs, unnecessary images during scraping
2. **PDF priority filtering** — use `document_priorities.yaml` to download only high-priority docs (rulings, decisions)
3. Decide whether to wire ML classifier into live scrape flow or keep it as a separate batch review step
4. Build a larger golden eval set in `classification_eval.yaml`; tune prompt versions with measured precision/recall
5. Implement `src/linkage/` (entity graphs, dispute counts, re-scoring)
6. Optional: migrate SQLite → PostgreSQL from docker-compose
7. Additional legal areas: new `configs/areas/<area>.yaml` + dictionaries

---

## 18. Key code entry points (quick reference)

| Task | Start here |
|------|------------|
| Change scrape behavior | `src/scraper/playwright_scraper.py` |
| Fix list parsing | `src/scraper/parser.py` |
| Fix case page parsing | `src/scraper/parser_case_page.py` |
| PDF download logic | `src/scraper/pdf_downloader.py` |
| Change scoring rules | `src/filters/stage1_screen.py`, `stage2_screen.py`, `configs/areas/` |
| Change ML taxonomy/prompt/model | `configs/classification.yaml` |
| ML classification code | `src/analysis/classifier.py`, `context_builder.py`, `prompt_builder.py`, `ollama_client.py` |
| PDF text extraction for ML | `src/analysis/pdf_extractor.py`, `pdf_paths.py` |
| DB schema / queries | `src/storage/database.py`, `repository.py` |
| Dashboard UI | `dashboard/app.py` |
| Parallel orchestration | `src/scraper/runner.py`, `scrape_parallel.py` |
| Single E2E run | `scrape_to_dashboard.py` |
| Batch ML classification | `classify_cases.py` |
| ML golden-set evaluation | `classify_eval.py`, `configs/classification_eval.yaml` |
| Bandwidth measurement | `bandwidth_test.py`, `src/scraper/traffic_tracker.py` |
| Quick PDF test | `test_pdf_live.py` |
