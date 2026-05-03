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
        
        # Proxy configuration
        self.proxy_enabled = config.get("scraping.proxy.enabled", False)
        self.proxy_server = None
        self.proxy_username = None
        self.proxy_password = None
        
        if self.proxy_enabled:
            proxy_host = config.get("scraping.proxy.host")
            proxy_user = config.get("scraping.proxy.username")
            proxy_pass = config.get("scraping.proxy.password")
            port_min = config.get("scraping.proxy.port_range.min", 10000)
            port_max = config.get("scraping.proxy.port_range.max", 10999)
            
            # Random port from pool
            proxy_port = random.randint(port_min, port_max)
            
            self.proxy_server = f"http://{proxy_host}:{proxy_port}"
            self.proxy_username = proxy_user
            self.proxy_password = proxy_pass
            
            logger.info(f"Playwright proxy enabled: {proxy_host}:{proxy_port}")

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
        
        page = await context.new_page()
        
        # Apply stealth (masks webdriver property, plugins, etc.)
        # stealth_async is not available in top-level init, need to use Stealth class
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        return browser, context, page

    async def get_judge_id(self, judge_name: str) -> Optional[str]:
        """
        Get judge ID.
        This is now implicitly handled during the UI search, 
        but we keep this method for API compatibility or future use.
        """
        # Hardcoded for the specific task to save time/complexity if needed
        # In a generic scraper, we would parse this from the suggestion dropdown
        return "ec53dc1c-d1a2-42ad-a444-343fea428f92"

    async def search_cases_via_ui(self, page: Page, court_name: str = None, judge_name: str = None) -> str:
        """
        Perform initial search via UI to establish session.
        Uses confirmed CSS selectors.
        """
        logger.info(f"Performing UI search for judge '{judge_name}'...")
        
        # 1. Fill Judge Name
        if judge_name:
            judge_input_selector = '#sug-judges input'
            try:
                await page.wait_for_selector(judge_input_selector, state="visible", timeout=20000)
                await page.click(judge_input_selector)
                
                # Mimic human typing
                await page.type(judge_input_selector, judge_name, delay=self.typing_delay_ms * random.uniform(0.9, 1.1))
                logger.info("Filled judge name")
                
                # Wait for autocomplete
                await self._delay("autocomplete_wait")
                
                # Press Enter to select
                await page.keyboard.press('Enter')
                await self._delay("after_autocomplete_select")
                
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

    async def _init_session(self, page: Page, court: str, judge_name: str):
        """Initialize session via UI search."""
        logger.info(f"Navigating to {self.base_url}...")
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            raise
        
        # Perform UI Search to warm up session
        await self.search_cases_via_ui(page, court, judge_name)

    async def get_case_content(self, case_url: str, judge_name: str = "Солдатов Р. С.") -> str:
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

    async def collect_cases(self, court_name: str = None, judge_name: str = None, max_cases: int = 100) -> List[CaseBase]:
        """
        Main collection method.
        Manages the browser lifecycle to keep one persistent session.
        """
        logger.info(f"Starting collection for {judge_name}, max_cases={max_cases}")
        
        # Determine Judge ID (using hardcoded for now, or could extract from UI)
        judge_id = await self.get_judge_id(judge_name)
        court_id = COURT_MAP.get(court_name) if court_name else None
        
        all_cases = []
        try:
            # 1. Initialize Session via UI if not warmed up
            if not getattr(self, 'is_warmed_up', False):
                await self._init_session(self.page, court_name, judge_name)
                self.is_warmed_up = True
            
            html_result = await self.page.content()
            
            # Debug: Save Page 1 HTML
            # with open("debug_page_1.html", "w", encoding="utf-8") as f:
                # f.write(html_result)
            # logger.info("Saved Page 1 HTML to debug_page_1.html")
            
            # Parse Page 1
            cases, pagination = parse_case_list(html_result)
            logger.info(f"Parsed {len(cases)} cases from Page 1")
            all_cases.extend(cases)
            
            # 2. Pagination Loop (Page 2+)
            current_page = 2
            while len(all_cases) < max_cases:
                if len(cases) == 0: # Stop if previous page was empty
                    break
                    
                # Configurable delay between pages
                logger.info(f"Waiting before Page {current_page}...")
                await self._delay("between_pages")
                
                # Fetch next page
                html_result = await self.fetch_api_page(self.page, court_id, judge_id, current_page, 25)
                
                if not html_result:
                    logger.warning(f"Failed to fetch page {current_page}")
                    break
                    
                # Debug: Save API Page HTML
                # with open(f"debug_page_{current_page}.html", "w", encoding="utf-8") as f:
                #     f.write(html_result)
                    
                cases, _ = parse_case_list(html_result)
                if not cases:
                    logger.info(f"No more cases found on page {current_page}")
                    break
                    
                logger.info(f"Parsed {len(cases)} cases from Page {current_page}")
                all_cases.extend(cases)
                
                current_page += 1
                
            # Trim to max_cases
            return all_cases[:max_cases]
                
        except Exception as e:
            logger.error(f"Collection failed: {e}")
            import traceback
            traceback.print_exc()
            return all_cases

    async def batch_enrich_cases(self, cases: List[CaseBase], batch_size: int = 10, judge_name: str = None, court_name: str = "АС Московского округа") -> None:
        """
        Enrich a batch of cases by fetching and parsing their full case cards.
        Uses a single browser session to minimize overhead and avoid blocks.
        Updates the Case objects in place.
        
        Closed cases (detected by result text) get a shallow scrape only —
        no instance expansion, no PDF text extraction.
        """
        if not cases:
            return
            
        # Get just the cases that actually need enrichment and have URLs
        valid_cases = [c for c in cases if getattr(c, 'case_url', None)]
        if not valid_cases:
            logger.info("No cases with URLs to enrich.")
            return

        # Load closed-case indicators from config file
        closed_indicators = self._load_closed_case_indicators()
        logger.info(f"Loaded {len(closed_indicators)} closed-case indicator patterns")

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

                        # --- Pre-filter: check if case is closed before deep scraping ---
                        is_closed = await self._check_case_closed(closed_indicators)
                        
                        if is_closed:
                            logger.info(f"Case {case.case_number} is CLOSED — shallow scrape only")
                        else:
                            # Deep scrape: expand instance chronologies
                            collapse_buttons = await self.page.query_selector_all('.b-collapse.js-collapse')
                            for btn in collapse_buttons:
                                try:
                                    await btn.click()
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
                            case.extracted_data["scrape_depth"] = "shallow" if is_closed else "deep"
                        
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
                            
                        logger.info(f"{'Shallow' if is_closed else 'Deep'}-scraped {case.case_number}")
                            
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
    
    cases = await scraper.collect_cases(judge_name="Солдатов Р. С.", max_cases=10)
    
    print(f"\n✓ Collected {len(cases)} cases")
    if cases:
        print(f"\nFirst case:")
        print(f"  Number: {cases[0].case_number}")
        print(f"  Court: {cases[0].court}")
        print(f"  Plaintiff: {cases[0].plaintiff}")
        print(f"  Defendant: {cases[0].defendant}")


if __name__ == "__main__":
    asyncio.run(main_playwright())
