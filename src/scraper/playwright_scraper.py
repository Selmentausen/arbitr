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
        self.min_delay = config.get("scraping.min_delay", 5)
        self.max_delay = config.get("scraping.max_delay", 10)
        
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
                await page.type(judge_input_selector, judge_name, delay=100)
                logger.info("Filled judge name")
                
                # Wait for autocomplete
                await page.wait_for_timeout(3000)
                
                # Press Enter to select
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(1000)
                
            except Exception as e:
                logger.error(f"Failed to fill judge input: {e}")
                raise
        elif court_name:
            logger.info(f"Filling court name: '{court_name}'")
            court_input_selector = "#caseCourt input.js-input"
            try:
                await page.wait_for_selector(court_input_selector, state="visible", timeout=10000)
                await page.click(court_input_selector)
                await page.type(court_input_selector, court_name, delay=100)
                await page.wait_for_timeout(2000)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1000)
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
                    await page.wait_for_timeout(500)
            
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
            await page.wait_for_timeout(3000)
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
                await asyncio.sleep(random.uniform(2, 5))
                
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
        
        async with async_playwright() as p:
            browser, context, page = await self._setup_browser(p)
            
            all_cases = []
            try:
                # 1. Initialize Session via UI
                await self._init_session(page, court_name, judge_name)
                
                html_result = await page.content()
                
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
                        
                    # Random delay
                    delay = random.uniform(self.min_delay, self.max_delay)
                    logger.info(f"Waiting {delay:.1f}s before Page {current_page}...")
                    await asyncio.sleep(delay)
                    
                    # Fetch next page
                    html_result = await self.fetch_api_page(page, court_id, judge_id, current_page, 25)
                    
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
            finally:
                await browser.close()


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
