"""
API client for kad.arbitr.ru with anti-bot protection.
Handles session management, headers, delays, and DDOS-Guard cookies.
"""

import time
import random
import httpx
from typing import Optional, Dict, Any
from urllib.parse import urljoin

from src.utils.logger import get_logger
from src.config.manager import ConfigManager


logger = get_logger(__name__)


class KadApiClient:
    """API client for kad.arbitr.ru with anti-bot protection."""
    
    def __init__(self, config: ConfigManager):
        """
        Initialize API client.
        
        Args:
            config: Configuration manager instance
        """
        self.config = config
        self.base_url = config.get("scraping.base_url", "https://kad.arbitr.ru")
        self.min_delay = config.get("scraping.min_delay", 5)
        self.max_delay = config.get("scraping.max_delay", 10)
        self.timeout = config.get("scraping.timeout", 30)
        self.max_retries = config.get("scraping.max_retries", 3)
        self.retry_delay = config.get("scraping.retry_delay", 15)
        
        # User-Agent rotation
        self.user_agents = config.get("scraping.user_agents", [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        ])
        
        # Proxy configuration
        self.proxy_enabled = config.get("scraping.proxy.enabled", False)
        self.proxy_config = None
        
        if self.proxy_enabled:
            proxy_host = config.get("scraping.proxy.host")
            proxy_user = config.get("scraping.proxy.username")
            proxy_pass = config.get("scraping.proxy.password")
            port_min = config.get("scraping.proxy.port_range.min", 10000)
            port_max = config.get("scraping.proxy.port_range.max", 10999)
            
            # Random port from pool for rotation
            proxy_port = random.randint(port_min, port_max)
            
            self.proxy_config = {
                "host": proxy_host,
                "username": proxy_user,
                "password": proxy_pass,
                "port_min": port_min,
                "port_max": port_max,
                "current_port": proxy_port
            }
            
            proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
            logger.info(f"Proxy enabled: {proxy_host}:{proxy_port}")
        else:
            proxy_url = None
            logger.info("Proxy disabled")
        
        # Create persistent session with proxy
        self.client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers=self._get_base_headers(),
            proxies=proxy_url
        )
        
        self._last_request_time = 0
        logger.info(f"KadApiClient initialized with base_url={self.base_url}")
    
    def _rotate_proxy(self):
        """Rotate to a new proxy port for next request."""
        if not self.proxy_enabled or not self.proxy_config:
            return
        
        # Pick new random port
        new_port = random.randint(
            self.proxy_config["port_min"],
            self.proxy_config["port_max"]
        )
        
        # Update proxy URL
        proxy_url = (
            f"http://{self.proxy_config['username']}:"
            f"{self.proxy_config['password']}@"
            f"{self.proxy_config['host']}:{new_port}"
        )
        
        self.client._transport._pool._proxy_url = proxy_url
        self.proxy_config["current_port"] = new_port
        
        logger.debug(f"Rotated proxy to port {new_port}")
    
    def _get_base_headers(self) -> Dict[str, str]:
        """Get base headers for requests."""
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept-Language": "en-US,en;q=0.9,ru-RU;q=0.8,ru;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
            "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
    
    def _apply_delay(self):
        """Apply delay between requests to avoid rate limiting."""
        elapsed = time.time() - self._last_request_time
        delay = random.uniform(self.min_delay, self.max_delay)
        
        if elapsed < delay:
            sleep_time = delay - elapsed
            logger.debug(f"Sleeping for {sleep_time:.2f}s to avoid rate limiting")
            time.sleep(sleep_time)
        
        self._last_request_time = time.time()
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        retries: int = 0,
        **kwargs
    ) -> httpx.Response:
        """
        Make HTTP request with retry logic.
        
        Args:
            method: HTTP method (GET, POST)
            endpoint: API endpoint (relative to base_url)
            retries: Current retry attempt
            **kwargs: Additional arguments for httpx request
            
        Returns:
            httpx.Response object
            
        Raises:
            httpx.HTTPError: If request fails after all retries
        """
        self._apply_delay()
        
        url = urljoin(self.base_url, endpoint)
        
        # Add X-Requested-With header for AJAX requests
        headers = kwargs.pop("headers", {})
        headers.setdefault("X-Requested-With", "XMLHttpRequest")
        
        try:
            logger.debug(f"{method} {url} (attempt {retries + 1}/{self.max_retries + 1})")
            response = self.client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            
            logger.debug(f"Response status: {response.status_code}, length: {len(response.content)} bytes")
            return response
            
        except (httpx.HTTPError, httpx.RequestError) as e:
            logger.warning(f"Request failed: {e}")
            
            if retries < self.max_retries:
                sleep_time = self.retry_delay * (retries + 1)
                logger.info(f"Retrying in {sleep_time}s... ({retries + 1}/{self.max_retries})")
                time.sleep(sleep_time)
                return self._make_request(method, endpoint, retries + 1, headers=headers, **kwargs)
            else:
                logger.error(f"Request failed after {self.max_retries} retries")
                raise
    
    def get_judge_id(self, judge_name: str) -> Optional[str]:
        """
        Get judge ID by name using autocomplete API.
        
        Args:
            judge_name: Judge name (e.g., "Солдатов Р. С.")
            
        Returns:
            Judge ID (UUID) or None if not found
        """
        logger.info(f"Looking up judge ID for: {judge_name}")
        
        endpoint = self.config.get(
            "scraping.endpoints.judge_suggest",
            "/Suggest/Judges"
        )
        
        params = {
            "count": 10,
            "suggestType": "judge",
            "_": int(time.time() * 1000),  # Timestamp
            "name": judge_name
        }
        
        headers = {
            "Accept": "application/json, text/javascript, */*",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        
        try:
            response = self._make_request("GET", endpoint, params=params, headers=headers)
            data = response.json()
            
            if data.get("Success") and data.get("Result", {}).get("Count", 0) > 0:
                items = data["Result"]["Items"]
                # Find exact match
                for item in items:
                    if item["Name"] == judge_name:
                        judge_id = item["Id"]
                        court_name = item.get("CourtName", "Unknown")
                        logger.info(f"Found judge: {judge_name} (ID: {judge_id}, Court: {court_name})")
                        return judge_id
                
                logger.warning(f"No exact match for '{judge_name}'. Found {len(items)} similar judge(s)")
                return None
            else:
                logger.warning(f"No judges found for '{judge_name}'")
                return None
                
        except Exception as e:
            logger.error(f"Failed to lookup judge ID: {e}")
            return None
    
    def search_cases(
        self,
        judge_id: str,
        page: int = 1,
        count: int = 25
    ) -> str:
        """
        Search cases by judge ID.
        
        Args:
            judge_id: Judge UUID
            page: Page number (1-indexed)
            count: Number of results per page (default: 25)
            
        Returns:
            HTML response containing case table
        """
        logger.info(f"Searching cases: judge_id={judge_id}, page={page}, count={count}")
        
        endpoint = self.config.get(
            "scraping.endpoints.case_search",
            "/Kad/SearchInstances"
        )
        
        payload = {
            "Page": page,
            "Count": count,
            "Courts": [],
            "DateFrom": None,
            "DateTo": None,
            "Sides": [],
            "Judges": [
                {
                    "JudgeId": judge_id,
                    "Type": -1
                }
            ],
            "CaseNumbers": [],
            "WithVKSInstances": False
        }
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "X-Date-Format": "iso",
        }
        
        try:
            response = self._make_request("POST", endpoint, json=payload, headers=headers)
            logger.debug(f"Retrieved HTML response ({len(response.text)} chars)")
            return response.text
            
        except Exception as e:
            logger.error(f"Failed to search cases: {e}")
            raise
    
    def close(self):
        """Close the HTTP client session."""
        self.client.close()
        logger.info("KadApiClient session closed")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
