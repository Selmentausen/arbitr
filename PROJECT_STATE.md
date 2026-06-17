# Arbitr — Project State (Agent Handoff)

**Last updated:** 2026-06-10\
**Purpose:** Read this file at the start of a new agent conversation instead of
scanning the repo.\
**Design spec (older):** `Arbitr_document.md` (architecture intent, Feb 2026).\
**User README:** `README.md` is **outdated** (still says "Phase 1 only" and
PostgreSQL as primary DB).

---

## 1. What this project is

**Arbitr** scrapes Russian arbitration court cases from
[kad.arbitr.ru](https://kad.arbitr.ru), scores them for relevance (focus:
**construction contract disputes** / mediation potential), stores results in a
local DB, and reviews them in a **Streamlit dashboard**.

**MVP scope:** Moscow Arbitration Court (`АС города Москвы`), judge-based
search, keyword filtering, optional deep scrape of case card HTML, PDF download,
and batch/on-demand ML legal-area classification for reviewed cases.

**Scale context:** Site has 45M+ cases; approach is incremental (by judge,
capped per judge, human-like delays, optional residential proxies).

---

## 2. Implementation status (truth table)

| Area                      | Status                         | Notes                                                                                                                                               |
| ------------------------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Config / models / logging | Done                           | `ConfigManager`, Pydantic `Case`/`CaseBase`, `src/utils/logger.py`                                                                                  |
| Playwright scraper        | **Working**                    | `PlaywrightScraper` — UI search, API pagination, case page enrichment; supports `--no-stealth`                                                      |
| Parsers                   | **Working**                    | List page (`parser.py`), case card (`parser_case_page.py`)                                                                                          |
| Filter Stage 1            | **Working**                    | Keyword + judge groups + reject keywords (global). `reject_enabled` flag in config (currently `false`)                                              |
| Filter Stage 2            | **Partial**                    | `stage2_html_analyze` exists; runs **only after** `batch_enrich_cases`, not inside `process_case`                                                   |
| Filter Stage 3 (PDF text) | **Implemented for ML batch**   | `src/analysis/pdf_extractor.py` extracts downloaded PDFs with PyMuPDF; not wired into live scrape filter pipeline                                   |
| Filter Stage 4 (LLM/ML)   | **Working as batch/on-demand** | Local Ollama classifier (`qwen2.5:14b` default, `7b` fast) scores legal-area probabilities; not wired into live scrape pipeline                     |
| PDF Download              | **Working**                    | `src/scraper/pdf_downloader.py` — Intercepts network layer to capture PDF bytes from page-submitted POST token/hash redirects; fallback to `Ctrl+S` |
| Storage                   | **SQLite (not Postgres)**      | `data/arbitr.db` via SQLAlchemy; rich schema (cases, participants, instances, documents, scrape events)                                             |
| Linkage module            | **Stub**                       | `src/linkage/__init__.py` — Phase 5 placeholder                                                                                                     |
| Dashboard                 | **Working**                    | Streamlit: overview, case list, dedicated ML review page, search, PDF categorization, export, live scrape monitor                                   |
| Parallel scrape           | **Working**                    | `scrape_parallel.py` + `ParallelScrapeRunner` — queue of judges, N workers                                                                          |
| Bandwidth test            | **Working**                    | `bandwidth_test.py` — deep crawl measuring browser & PDF traffic breakdown; saves detailed logs to `data/traffic_log.csv`                           |
| ML Export Tool            | **Working**                    | `export_ml.py` — exports case link, category, and reasoning to text file with date filters                                                          |
| Docker Postgres/Redis     | **Infra only**                 | `docker-compose.yml` ready; app does not use them yet                                                                                               |

---

## 3. Tech stack

- **Python 3.14+**, **Poetry** (`pyproject.toml`)
- **Playwright** + **playwright-stealth** (anti-bot)
- **BeautifulSoup** (HTML parse)
- **Pydantic v2**, **SQLAlchemy 2**, **SQLite** file DB
- **Streamlit** + Plotly dashboard
- **PyYAML** configs; keyword lists in `configs/dictionaries/`
- **psutil** (NIC counters for bandwidth test)
- Planned but unused in runtime: PostgreSQL, Redis, Alembic migrations, OpenAI.
  Local Ollama ML + PDF text extraction are implemented as batch/on-demand
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
│   │   ├── playwright_scraper.py  # main scraper class
│   │   ├── parser.py                # search results table
│   │   ├── parser_case_page.py      # case card HTML
│   │   ├── pdf_downloader.py        # PDF download via response listener + fallback Ctrl+S
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
│   ├── cli/                   # Command-line entry points
│   │   ├── scrape.py
│   │   ├── scrape_parallel.py
│   │   ├── bandwidth_test.py
│   │   ├── classify.py
│   │   ├── classify_eval.py
│   │   ├── test_pdf.py
│   │   ├── export_ml.py
│   │   └── dashboard.py
│   └── linkage/               # empty stub
├── dashboard/app.py           # Streamlit UI
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

**Parallel path:** `ParallelScrapeRunner` — asyncio queue, one browser per
worker, optional **distinct proxy port per worker**
(`scraping.proxy.forced_port`), scrape events logged to DB for dashboard "Live"
page.

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

**Important:** ML classification is currently **batch/on-demand only**
(`poetry run classify` or dashboard button). It is not called from the scraper.

---

## 6. Scraper behavior (important details)

**Class:** `src/scraper/playwright_scraper.py` — `PlaywrightScraper`

**Search flow:**

1. Open `https://kad.arbitr.ru`, warm session (DDOS-Guard challenge resolved by
   browser).
2. Type judge name in autocomplete (`Фамилия И. О.` format from `judge_loader`).
3. Pick suggestion matching `scraping.target_court_filter` (default:
   `АС города Москвы`).
4. Paginate via internal API `fetch_api_page` (court id from `COURT_MAP`, judge
   id from suggest).

**Enrichment (`batch_enrich_cases`):** For eligible cases — navigate to
`case_url`, click "+" on instance chronology, parse
participants/instances/documents into `Case` model. If `pdf_download_enabled`,
also downloads PDFs.

**Delays:** All tunable under `configs/main.yaml` → `scraping.delays.*`
(seconds, per action).

**Proxy:** `scraping.proxy.enabled` in config. Parallel runner assigns
`port_range` ports per worker.

**Stealth:** Playwright-stealth can be disabled using `--no-stealth` in the CLI
tools if it causes issues rendering or resolving gray canvas captchas.

**Errors:** `JudgeCourtNotFoundError` if autocomplete has no row for target
court.

---

## 7. PDF Download

**Module:** `src/scraper/pdf_downloader.py`

**How it works:** kad.arbitr.ru serves PDFs behind DDOS-Guard. Direct HTTP
requests get HTML challenge stubs. To retrieve a PDF, the page submits a
token/hash calculation POST request to:
`https://kad.arbitr.ru/Document/Pdf/...pdf?isAddStamp=True`

**Solution:** The script registers a Playwright network interception layer using
`context.on("response")`. When the page submits the calculation POST, the
browser handles the cookies, tokens, and redirect chains automatically. The
response listener intercepts this `application/pdf` response, checks for the
`%PDF` header magic bytes, and extracts the binary data directly from the
network stream. It falls back to `Ctrl+S` key events in the active browser tab
if the listener fails.

**Storage:** Flat content-addressed directory (`data/pdfs/`). Filenames:
`{readable_name}_{sha256[:16]}.pdf`.

**Typical PDF size:** ~200–320 KB per document (court rulings, decisions).

**Config flags:**

- `filtering.pdf_download_enabled: true` — master switch
- `scraping.pdf_storage_dir: data/pdfs` — storage location

---

## 8. Filtering

### StatusEnum (`src/models/case.py`)

- `high_relevant`, `reject`, `insufficient_info`, `uncertain`

### Config flags (`configs/main.yaml` → `filtering`)

- `reject_enabled: false` — when false, cases that would be rejected become
  `uncertain` instead, and all cases with URLs proceed to enrichment
- `pdf_download_enabled: true` — enables PDF download during enrichment

### Stage 1 (`stage1_initial_screen`)

- Area keywords from `configs/areas/*.yaml` → dictionary files
- Global reject: `configs/dictionaries/global_reject_keywords.txt`
- Judge group bonuses from `main.yaml` → `judge_groups`
- Thresholds: `high: 80`, `low: 20`, gray zone 40–60 remains config-only for the
  live filter pipeline
- Fixed bugs: `matched_area` → `best_area_name`, `len(matched_keywords - 1, 3)`
  → `min(len(...) - 1, 3)`

### Stage 2 (`stage2_html_analyze`)

- Only cases still `uncertain` / `insufficient_info` after enrichment
- Uses `stage2_keywords` from `configs/dictionaries/construction/stage2.txt`
- **Not** called from `FilterPipeline.process_case` — only via
  `process_stage2_batch` after scrape enrichment

---

## 9. Bandwidth measurement

**Script:** `bandwidth_test.py` — single-judge deep crawl (default 200 cases,
going back in time to find cases with actual documents).

**Approach:**

- `TrackingScraper` extends `PlaywrightScraper` with `NetworkTrafficTracker`
  (counts HTTP bytes via browser context events).
- Separate `pdf_stats` for PDF-specific traffic.
- Reports a clean traffic breakdown summary (Warmup, Case card load, Chronology
  expand, PDF downloads, and Other/API traffic) after each enriched case.
- Detailed network request tables are saved to `data/traffic_log.csv` instead of
  printing to clean console outputs.
- Supports `--no-stealth` to disable playwright-stealth.

**Usage:**

```bash
poetry run bandwidth-test --max-cases 10 --duration-minutes 10 --no-stealth
poetry run bandwidth-test --judge "Титова" --max-cases 300 --proxy
```

---

## 10. Configuration

| File                                              | Role                                                                                                                               |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `configs/main.yaml`                               | Thresholds, judge_groups, linkage_rules, scraping delays/proxy, filtering flags                                                    |
| `configs/classification.yaml`                     | Ollama legal-area classifier config: categories, model names, prompt v1.0, few-shot examples, disambiguation rules, context limits |
| `configs/classification_eval.yaml`                | Manual golden set for `poetry run classify-eval`                                                                                   |
| `configs/areas/construction.yaml`                 | Area rules, paths to keyword files, weights                                                                                        |
| `configs/dictionaries/judges.txt`                 | ~99 judge full names (parallel scrape input)                                                                                       |
| `configs/dictionaries/construction/keywords.txt`  | Stage 1 keywords                                                                                                                   |
| `configs/dictionaries/construction/stage2.txt`    | Stage 2 keywords                                                                                                                   |
| `configs/dictionaries/global_reject_keywords.txt` | Global reject list                                                                                                                 |
| `configs/dictionaries/closed_case_indicators.txt` | Used when checking closed cases                                                                                                    |
| `configs/dictionaries/document_priorities.yaml`   | PDF priority rules used during scraping/dashboard PDF categorization                                                               |

---

## 11. Database (SQLite)

- **Path:** `data/arbitr.db` (both pipelines and dashboard use this path)
- **Init:** `init_db(DB_PATH)` from `src/storage/database.py`
- **Access:** `CaseRepository` — save/update cases, stats, search, export,
  scrape event tracking

---

## 12. Dashboard

**Run:** `poetry run dashboard`

---

## 13. ML Export Tool

**Script:** `src/cli/export_ml.py` — exports ML classified cases from the SQLite
database to a text file.

**Features:**

- Formats exported cases with case link, classification category, confidence,
  analyzed date, and formatted reasoning.
- Supports filtering by date and timeframe using the following parameters:
  - `--date YYYY-MM-DD`: Filter for cases classified on a specific day (in local
    timezone).
  - `--since YYYY-MM-DD[THH:MM:SS]`: Filter for cases classified on or after
    this datetime.
  - `--until YYYY-MM-DD[THH:MM:SS]`: Filter for cases classified on or before
    this datetime.
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

## 14. Testing

- **pytest** under `tests/`
- New classification unit tests can be run without Ollama:
  `poetry run pytest tests/test_classification_config.py tests/test_prompt_builder.py tests/test_context_builder.py tests/test_classifier.py -v --no-cov`
