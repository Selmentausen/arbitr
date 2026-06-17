"""
Alternative scraper using Playwright for better anti-bot evasion.
This uses a real browser to bypass DDOS-Guard protection.

Features:
- Playwright Stealth: Masks automation signals
- Session Persistence: Maintains single browser session for detailed collection
- UI-First Search: Interacts with the page like a real user to "warm up" session
- Proxy Support: Rotates proxies (if configured)
"""

import asyncio
import random
import json
from datetime import datetime
from typing import List, Optional, Tuple
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

from src.models.case import CaseBase
from src.scraper.parser import parse_case_list
from src.utils.logger import get_logger
from src.config.manager import ConfigManager


logger = get_logger(__name__)

COURT_MAP = {
    "АС Московского округа": "FASMO",
    "АС города Москвы": "MSK",
    "АС Московской области": "ASMO",
}


class JudgeCourtNotFoundError(Exception):
    """No autocomplete row matched the target court (e.g. АС города Москвы)."""

    def __init__(self, judge_search: str, detail: str = ""):
        self.judge_search = judge_search
        msg = f"No judge suggestion matching court for {judge_search!r}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class PlaywrightScraper:
    """Scraper using Playwright headless browser for DDOS-Guard bypass."""
    
    def __init__(self, config: ConfigManager, headless: bool = True):
        """
        Initialize Playwright scraper.
        
        Args:
            config: Configuration manager
            headless: Run browser in headless mode
        """
        self.config = config
        self.headless = headless
        self.base_url = config.get("scraping.base_url", "https://kad.arbitr.ru")
        
        # Per-action delay configuration (all in seconds unless noted)
        self.delays = {
            "between_pages":           (config.get("scraping.delays.between_pages.min", 2),           config.get("scraping.delays.between_pages.max", 4)),
            "before_case_page":        (config.get("scraping.delays.before_case_page.min", 3),        config.get("scraping.delays.before_case_page.max", 6)),
            "between_instance_expand": (config.get("scraping.delays.between_instance_expand.min", 1), config.get("scraping.delays.between_instance_expand.max", 2.5)),
            "after_all_expanded":      (config.get("scraping.delays.after_all_expanded.min", 1.5),    config.get("scraping.delays.after_all_expanded.max", 3)),
            "autocomplete_wait":       (config.get("scraping.delays.autocomplete_wait.min", 2.5),     config.get("scraping.delays.autocomplete_wait.max", 4)),
            "after_autocomplete_select":(config.get("scraping.delays.after_autocomplete_select.min", 0.8), config.get("scraping.delays.after_autocomplete_select.max", 1.5)),
            "before_search_click":     (config.get("scraping.delays.before_search_click.min", 0.3),   config.get("scraping.delays.before_search_click.max", 0.8)),
            "after_search_results":    (config.get("scraping.delays.after_search_results.min", 2.5),  config.get("scraping.delays.after_search_results.max", 4)),
            "between_batches":         (config.get("scraping.delays.between_batches.min", 5),         config.get("scraping.delays.between_batches.max", 10)),
        }
        self.typing_delay_ms = config.get("scraping.delays.typing_delay_ms", 100)
        self.target_court_filter = config.get("scraping.target_court_filter", "АС города Москвы")
        self.last_judge_id: Optional[str] = None

        # Proxy configuration
        self.proxy_enabled = config.get("scraping.proxy.enabled", False)
        self.proxy_server = None
        self.proxy_username = None
        self.proxy_password = None
        self.proxy_port: Optional[int] = None

        if self.proxy_enabled:
            proxy_host = config.get("scraping.proxy.host")
            proxy_user = config.get("scraping.proxy.username")
            proxy_pass = config.get("scraping.proxy.password")
            port_min = config.get("scraping.proxy.port_range.min", 10000)
            port_max = config.get("scraping.proxy.port_range.max", 10999)
            forced = config.get("scraping.proxy.forced_port")
            if forced is not None:
                proxy_port = int(forced)
            else:
                proxy_port = random.randint(port_min, port_max)
            self.proxy_port = proxy_port

            self.proxy_server = f"http://{proxy_host}:{proxy_port}"
            self.proxy_username = proxy_user
            self.proxy_password = proxy_pass

            logger.info(f"Playwright proxy enabled: {proxy_host}:{proxy_port}")

        # Bandwidth optimization configuration
        self.opt_enabled = self.config.get("scraping.bandwidth_optimization.enabled", False)
        self.opt_block_images = self.config.get("scraping.bandwidth_optimization.block_images", False)
        self.opt_block_fonts = self.config.get("scraping.bandwidth_optimization.block_fonts", False)
        self.opt_block_media = self.config.get("scraping.bandwidth_optimization.block_media", False)
        self.opt_block_stylesheets = self.config.get("scraping.bandwidth_optimization.block_stylesheets", False)
        self.opt_block_trackers = self.config.get("scraping.bandwidth_optimization.block_trackers", False)
        self.opt_block_extra_scripts = self.config.get("scraping.bandwidth_optimization.block_extra_scripts", False)

        # Stealth toggle configuration
        self.stealth_enabled = self.config.get("scraping.stealth_enabled", True)

    async def __aenter__(self):
        """Async context manager entry."""
        self.playwright_engine = await async_playwright().start()
        self.browser, self.context, self.page = await self._setup_browser(self.playwright_engine)
        self.is_warmed_up = False
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if hasattr(self, 'browser') and self.browser:
            await self.browser.close()
        if hasattr(self, 'playwright_engine') and self.playwright_engine:
            await self.playwright_engine.stop()

    async def _delay(self, action: str):
        """Apply a random delay for the given action using config values."""
        min_s, max_s = self.delays.get(action, (1, 2))
        delay = random.uniform(min_s, max_s)
        logger.debug(f"Delay [{action}]: {delay:.1f}s")
        await asyncio.sleep(delay)

    async def _setup_browser(self, p) -> Tuple[BrowserContext, Page]:
        """Configure and launch browser with stealth settings."""
        launch_options = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
                "--ignore-ssl-errors",
                "--disable-dev-shm-usage"
            ]
        }
        
        if self.proxy_enabled and self.proxy_server:
            launch_options["proxy"] = {
                "server": self.proxy_server,
                "username": self.proxy_username,
                "password": self.proxy_password
            }

        logger.info("Launching browser...")
        browser = await p.chromium.launch(**launch_options)
        
        # Create context with realistic viewport and user agent
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            locale="ru-RU",
            timezone_id="Europe/Moscow"
        )
        
        if self.opt_enabled:
            # Determine blocked resource types
            blocked_types = set()
            if self.opt_block_images:
                blocked_types.add("image")
            if self.opt_block_fonts:
                blocked_types.add("font")
            if self.opt_block_media:
                blocked_types.add("media")
            if self.opt_block_stylesheets:
                blocked_types.add("stylesheet")

            # Determine blocked URL patterns
            blocked_patterns = []
            if self.opt_block_trackers:
                blocked_patterns.extend([
                    "mc.yandex.ru",
                    "yandex.ru/metrika",
                    "google-analytics.com",
                    "googletagmanager.com",
                    "top-fwz1.mail.ru",
                    "mail.ru",
                ])
            if self.opt_block_extra_scripts:
                blocked_patterns.extend([
                    "video",
                    "raphael",
                    "widget",
                    "android-icon",
                    "favicon",
                    ".png",
                    ".jpg",
                    ".ico"
                ])

            async def route_handler(route):
                req = route.request
                r_type = req.resource_type
                url = req.url.lower()

                if r_type in blocked_types:
                    logger.debug(f"BandwidthOpt: Blocking resource type '{r_type}' for URL: {url}")
                    await route.abort()
                    return

                if any(pat in url for pat in blocked_patterns):
                    logger.debug(f"BandwidthOpt: Blocking URL pattern for URL: {url}")
                    await route.abort()
                    return

                await route.continue_()

            await context.route("**/*", route_handler)
            logger.info("Bandwidth optimization route filters successfully registered on browser context.")
        
        page = await context.new_page()
        
        # Apply stealth (masks webdriver property, plugins, etc.)
        if self.stealth_enabled:
            # stealth_async is not available in top-level init, need to use Stealth class
            stealth = Stealth()
            await stealth.apply_stealth_async(page)
        else:
            logger.info("Playwright stealth overrides disabled via configuration.")
        
        return browser, context, page

    async def get_judge_id(self, judge_name: str) -> Optional[str]:
        """Return judge UUID captured during UI autocomplete (pagination API)."""
        return self.last_judge_id

    async def _select_judge_autocomplete(self, page: Page, text_to_type: str) -> Optional[str]:
        """
        Type a judge query, read rendered #b-suggest rows, pick row with target court,
        select via keyboard, store last_judge_id from <a id="...">. Returns Id or None.
        """
        judge_input_selector = "#sug-judges input"
        await page.wait_for_selector(judge_input_selector, state="visible", timeout=20000)
        await page.click(judge_input_selector)
        await page.fill(judge_input_selector, "")
        await page.type(
            judge_input_selector,
            text_to_type,
            delay=self.typing_delay_ms * random.uniform(0.9, 1.1),
        )
        logger.info("Filled judge name for suggest")
        await self._delay("autocomplete_wait")
        # Extra fixed wait for flaky suggest list rendering.
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_selector(
                "#b-suggest[style*='display: block'] .body__i ul li a",
                timeout=5000,
            )
        except Exception:
            logger.warning("Suggest popup did not become visible in time")
            return None

        suggest_rows = page.locator("#b-suggest .body__i ul li a")
        row_count = await suggest_rows.count()
        if row_count == 0:
            logger.warning("No #b-suggest rows rendered after typing judge")
            return None

        target_index = None
        chosen_id = None
        chosen_name = None
        suggest_rows_text = []
        for idx in range(row_count):
            row = suggest_rows.nth(idx)
            txt = (await row.inner_text()).strip()
            row_id = await row.get_attribute("id")
            suggest_rows_text.append(txt)
            if self.target_court_filter in txt:
                if chosen_id is None:
                    target_index = idx
                    chosen_id = row_id
                    chosen_name = txt
                if text_to_type.split()[0] in txt:
                    target_index = idx
                    chosen_id = row_id
                    chosen_name = txt
                    break

        if target_index is None or not chosen_id:
            logger.info("Suggest rows debug: %s", suggest_rows_text)
            logger.warning("No #b-suggest row matched target court filter")
            return None

        self.last_judge_id = chosen_id
        short_label = chosen_name.split(",")[0].strip()
        surname = short_label.split()[0] if short_label else text_to_type.split()[0]

        try:
            # Ensure key presses go to judge input's suggest list.
            await page.click(judge_input_selector)
            for _ in range(target_index):
                await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            logger.info("Suggest rows debug: %s", suggest_rows_text)
            logger.info(
                "Selected judge suggest by keyboard at index=%s (id=%s)",
                target_index,
                chosen_id,
            )
        except Exception as e:
            logger.error(f"Failed keyboard selection in judge suggest: {e}")
            return None

        await self._delay("after_autocomplete_select")
        return chosen_id

    async def search_cases_via_ui(
        self,
        page: Page,
        court_name: str = None,
        judge_name: str = None,
    ) -> str:
        """
        Perform initial search via UI to establish session.
        Uses confirmed CSS selectors.
        """
        logger.info(f"Performing UI search for judge '{judge_name}'...")

        # 1. Fill Judge Name
        if judge_name:
            try:
                chosen = await self._select_judge_autocomplete(page, judge_name)
                if not chosen:
                    raise JudgeCourtNotFoundError(
                        judge_name,
                        f"no suggestion containing {self.target_court_filter!r}",
                    )
            except JudgeCourtNotFoundError:
                raise
            except Exception as e:
                logger.error(f"Failed to fill judge input: {e}")
                raise
        elif court_name:
            logger.info(f"Filling court name: '{court_name}'")
            court_input_selector = "#caseCourt input.js-input"
            try:
                await page.wait_for_selector(court_input_selector, state="visible", timeout=10000)
                await page.click(court_input_selector)
                await page.type(court_input_selector, court_name, delay=self.typing_delay_ms)
                await self._delay("autocomplete_wait")
                await page.keyboard.press("Enter")
                await self._delay("after_autocomplete_select")
            except Exception as e:
                logger.error(f"Failed to fill court input: {e}")
        else:
            logger.error("No court name or judge name provided")
            raise

        # 2. Click Search Button
        search_button_selector = '#b-form-submit button'
        try:
            # Mouse move to button for realism
            button = await page.query_selector(search_button_selector)
            if button:
                box = await button.bounding_box()
                if box:
                    await page.mouse.move(box['x'] + 10, box['y'] + 10)
                    await self._delay("before_search_click")
            
            await page.click(search_button_selector)
            logger.info("Clicked search button")
        except Exception as e:
            logger.error(f"Failed to click search button: {e}")
            raise
        
        # 3. Wait for Results
        logger.info("Waiting for search results...")
        try:
            # Wait for network idle which usually indicates search finished
            await page.wait_for_load_state("networkidle", timeout=30000)
            await self._delay("after_search_results")
        except Exception as e:
            logger.warning(f"Wait for results timed out: {e}")
            
        return await page.content()

    async def fetch_api_page(self, page: Page, court_id: str, judge_id: str, page_num: int, count: int) -> str:

        """
        Fetch subsequent pages using the internal API via JavaScript fetch.
        This leverages the session cookies established by the UI search.
        """
        logger.info(f"Fetching page {page_num} via API injection...")
        
        await page.evaluate(f"""
            async () => {{
                try {{
                    const response = await fetch('/Kad/SearchInstances', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'X-Date-Format': 'iso',
                            'X-Requested-With': 'XMLHttpRequest'
                        }},
                        body: JSON.stringify({{
                            Page: {page_num},
                            Count: {count},
                            Courts: {f'["{court_id}"]' if court_id else '[]'},
                            DateFrom: null,
                            DateTo: null,
                            Sides: [],
                            Judges: [{{JudgeId: "{judge_id}", Type: -1}}],
                            CaseNumbers: [],
                            WithVKSInstances: false
                        }})
                    }});
                    
                    if (response.ok) {{
                        const html = await response.text();
                        window.__searchResults = html;
                        window.__searchError = null;
                    }} else {{
                        window.__searchError = "HTTP " + response.status;
                        window.__searchResults = null;
                    }}
                }} catch (e) {{
                    window.__searchError = e.toString();
                    window.__searchResults = null;
                }}
            }}
        """)
        
        # Wait for result
        for _ in range(20): # 10 seconds max
            result = await page.evaluate("window.__searchResults")
            error = await page.evaluate("window.__searchError")
            if result or error:
                break
            await page.wait_for_timeout(500)
            
        if error:
            logger.error(f"API Search Error: {error}")
            return None
            
        return result

    async def _init_session(
        self,
        page: Page,
        court: str,
        judge_name: str,
    ):
        """Initialize session via UI search."""
        logger.info(f"Navigating to {self.base_url}...")
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            raise

        # Perform UI Search to warm up session
        await self.search_cases_via_ui(page, court, judge_name)

    async def get_case_content(self, case_url: str, judge_name: str = "Титова Е. В.") -> str:
        """
        Fetch full HTML content of a specific case page.
        Requires initializing session via UI search first.
        """
        logger.info(f"Fetching case content: {case_url}")
        
        async with async_playwright() as p:
            browser, context, page = await self._setup_browser(p)
            try:
                # Initialize session
                await self._init_session(page, None, judge_name)
                
                # Navigate to case page
                logger.info(f"Navigating to case URL: {case_url}")
                # Random delay before navigation
                await self._delay("before_case_page")
                
                await page.goto(case_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except:
                    pass
                
                return await page.content()
                
            except Exception as e:
                logger.error(f"Failed to fetch case content: {e}")
                raise
            finally:
                await browser.close()

    async def collect_cases(
        self,
        court_name: str = None,
        judge_name: str = None,
        max_cases: int = 100,
        start_page: int = 1,
        on_page_done: callable = None,
    ) -> tuple[List[CaseBase], dict]:
        """
        Main collection method.
        Returns (cases, pagination) where pagination contains total_count from the site.

        Args:
            start_page: Page to begin collecting from (1 = first page).
                        Pages before start_page are fetched for session warmup but discarded.
            on_page_done: Optional callback(page_num, cases_so_far) called after each page.
        """
        logger.info(f"Starting collection for {judge_name}, max_cases={max_cases}, start_page={start_page}")

        court_id = COURT_MAP.get(court_name) if court_name else None

        if judge_name:
            self.is_warmed_up = False
            self.last_judge_id = None

        all_cases = []
        pagination = {}
        try:
            if not getattr(self, "is_warmed_up", False):
                await self._init_session(
                    self.page,
                    court_name,
                    judge_name,
                )
                self.is_warmed_up = True

            judge_id = self.last_judge_id or await self.get_judge_id(judge_name)
            if judge_name and not judge_id:
                logger.error("Judge ID missing after UI search; cannot paginate via API")
                return [], {}

            html_result = await self.page.content()

            cases, pagination = parse_case_list(html_result)
            if start_page <= 1:
                logger.info(f"Parsed {len(cases)} cases from Page 1")
                all_cases.extend(cases)
                if on_page_done:
                    on_page_done(1, len(all_cases))
            else:
                logger.info(f"Page 1: used for session warmup only — jumping to page {start_page}")

            # Jump directly to start_page (API accepts any page number)
            current_page = max(2, start_page)
            while len(all_cases) < max_cases:
                logger.info(f"Waiting before Page {current_page}...")
                await self._delay("between_pages")

                html_result = await self.fetch_api_page(
                    self.page, court_id, judge_id, current_page, 25
                )

                if not html_result:
                    logger.warning(f"Failed to fetch page {current_page}")
                    break

                cases, _ = parse_case_list(html_result)
                if not cases:
                    logger.info(f"No more cases found on page {current_page}")
                    break

                logger.info(f"Parsed {len(cases)} cases from Page {current_page}")
                all_cases.extend(cases)
                if on_page_done:
                    on_page_done(current_page, len(all_cases))

                current_page += 1

            return all_cases[:max_cases], pagination

        except JudgeCourtNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Collection failed: {e}")
            import traceback
            traceback.print_exc()
            return all_cases, pagination

    async def batch_enrich_cases(self, cases: List[CaseBase], batch_size: int = 10, judge_name: str = None, court_name: str = "АС Московского округа", skip_enriched: bool = False) -> None:
        """
        Enrich a batch of cases by fetching and parsing their full case cards.
        Uses a single browser session to minimize overhead and avoid blocks.
        Updates the Case objects in place.
        
        Closed cases get a shallow scrape unless pdf_download_enabled (then always expand).
        When pdf_download_enabled, all instance PDFs are downloaded after parsing.

        Args:
            skip_enriched: If True, skip cases that already have raw_html set (resume support).
        """
        if not cases:
            return
            
        valid_cases = [c for c in cases if getattr(c, 'case_url', None)]
        if skip_enriched:
            before = len(valid_cases)
            valid_cases = [c for c in valid_cases if not getattr(c, 'raw_html', None)]
            skipped = before - len(valid_cases)
            if skipped:
                logger.info(f"Skipped {skipped} already-enriched cases (resume)")
        if not valid_cases:
            logger.info("No cases with URLs to enrich.")
            return

        # Load closed-case indicators from config file
        closed_indicators = self._load_closed_case_indicators()
        logger.info(f"Loaded {len(closed_indicators)} closed-case indicator patterns")

        pdf_download_enabled = self.config.get("filtering.pdf_download_enabled", False)
        logger.info(f"Starting batch enrichment for {len(valid_cases)} cases (batch_size={batch_size})")

        try:
            # Initialize session once to clear DDOS-Guard
            if not getattr(self, 'is_warmed_up', False):
                await self._init_session(self.page, court_name, judge_name)
                self.is_warmed_up = True
            
            from src.scraper.parser import parse_case_card
                
            for i in range(0, len(valid_cases), batch_size):
                batch = valid_cases[i:i + batch_size]
                logger.info(f"Processing enrichment batch {i//batch_size + 1} ({len(batch)} cases)")
                
                # Delay between batches (except before the first)
                if i > 0:
                    await self._delay("between_batches")
                
                for case in batch:
                    logger.info(f"Fetching case card: {case.case_url}")
                    try:
                        # Configurable delay before navigating to case page
                        await self._delay("before_case_page")
                        
                        await self.page.goto(case.case_url, wait_until="domcontentloaded", timeout=60000)
                        try:
                            await self.page.wait_for_load_state("networkidle", timeout=10000)
                        except:
                            pass

                        is_closed = await self._check_case_closed(closed_indicators)
                        shallow_only = is_closed and not pdf_download_enabled

                        if shallow_only:
                            logger.info(f"Case {case.case_number} is CLOSED — shallow scrape only")
                        else:
                            if is_closed and pdf_download_enabled:
                                logger.info(
                                    f"Case {case.case_number} is CLOSED — expanding for PDF download"
                                )
                            # Wait for at least one chronology item to render (prevents race condition when CSS/scripts are blocked)
                            try:
                                await self.page.wait_for_selector(".b-chrono-item", timeout=10000)
                            except Exception:
                                pass

                            # Deep scrape: expand instance chronologies
                            collapse_buttons = await self.page.query_selector_all('.b-collapse.js-collapse')
                            for btn in collapse_buttons:
                                try:
                                    await btn.evaluate("node => node.click()")
                                    await self._delay("between_instance_expand")
                                except Exception:
                                    pass
                            
                            if collapse_buttons:
                                await self._delay("after_all_expanded")

                        html_content = await self.page.content()
                        
                        # Parse detailed card
                        card_data = parse_case_card(html_content)
                        
                        # Update case object in-place
                        if hasattr(case, 'raw_html'):
                            case.raw_html = html_content
                        
                        if hasattr(case, 'instances'):
                            case.instances = card_data.get("instances", [])
                            
                        if hasattr(case, 'extracted_data'):
                            case.extracted_data.update(card_data.get("extracted_data", {}))
                            # Mark scrape depth
                            case.extracted_data["scrape_depth"] = (
                                "shallow" if shallow_only else "deep"
                            )
                        
                        if hasattr(case, 'participants') and card_data.get("participants"):
                            case.participants = card_data["participants"]
                        
                        # New case-level metadata
                        if hasattr(case, 'case_status_text'):
                            case.case_status_text = card_data.get("case_status_text")
                        if hasattr(case, 'case_category_text'):
                            case.case_category_text = card_data.get("case_category_text")
                        if hasattr(case, 'claim_amount') and card_data.get("claim_amount"):
                            case.claim_amount = card_data["claim_amount"]
                        if hasattr(case, 'case_page_scraped'):
                            case.case_page_scraped = True
                        if hasattr(case, 'last_scraped_at'):
                            case.last_scraped_at = datetime.utcnow()
                            
                        if pdf_download_enabled and hasattr(case, "instances"):
                            from pathlib import Path

                            from src.scraper.pdf_downloader import download_pdfs_for_case

                            pdf_root = Path(
                                self.config.get("scraping.pdf_storage_dir", "data/pdfs")
                            )
                            pdf_stats = getattr(self, "pdf_traffic_stats", None)
                            await download_pdfs_for_case(
                                self.page,
                                case,
                                self.base_url,
                                storage_dir=pdf_root,
                                stats=pdf_stats,
                            )

                        logger.info(
                            f"{'Shallow' if shallow_only else 'Deep'}-scraped {case.case_number}"
                        )

                    except Exception as e:
                        logger.error(f"Failed to enrich case {case.case_number}: {e}")
                            
        except Exception as e:
            logger.error(f"Batch enrichment failed: {e}")

    def _load_closed_case_indicators(self) -> List[str]:
        """Load closed-case indicator patterns from config file."""
        from pathlib import Path
        indicators_path = Path("configs/dictionaries/closed_case_indicators.txt")
        if not indicators_path.exists():
            logger.warning(f"Closed-case indicators file not found: {indicators_path}")
            return []
        
        indicators = []
        for line in indicators_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                indicators.append(line.lower())
        return indicators

    async def _check_case_closed(self, indicators: List[str]) -> bool:
        """
        Check the visible instance header result texts to determine
        if the case is closed/resolved. Returns True if any result
        text matches a closed-case indicator.
        """
        if not indicators:
            return False
        
        try:
            # Get all result text elements from the instance headers (visible without expanding)
            result_elements = await self.page.query_selector_all('h2.b-case-result a, h2.b-case-result span')
            
            for elem in result_elements:
                text = await elem.inner_text()
                text_lower = text.strip().lower()
                for indicator in indicators:
                    if indicator in text_lower:
                        logger.debug(f"Closed-case match: '{text.strip()}' contains '{indicator}'")
                        return True
        except Exception as e:
            logger.warning(f"Failed to check case closure: {e}")
        
        return False


async def main_playwright():
    """Test Playwright scraper."""
    from src.config.manager import ConfigManager
    
    config = ConfigManager("configs/main.yaml")
    # Temporarily disable proxy for debugging
    config._config["scraping"]["proxy"]["enabled"] = False
    
    scraper = PlaywrightScraper(config, headless=False)  # Visible for debugging
    
    print("\n" + "=" * 60)
    print("Testing Playwright Stealth Scraper")
    print("=" * 60)
    
    cases = await scraper.collect_cases(judge_name="Титова Е. В.", max_cases=10)
    
    print(f"\n✓ Collected {len(cases)} cases")
    if cases:
        print(f"\nFirst case:")
        print(f"  Number: {cases[0].case_number}")
        print(f"  Court: {cases[0].court}")
        print(f"  Plaintiff: {cases[0].plaintiff}")
        print(f"  Defendant: {cases[0].defendant}")


if __name__ == "__main__":
    asyncio.run(main_playwright())
