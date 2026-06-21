"""
IP block detection for the worker.
"""
from typing import Optional, Tuple


DDOS_GUARD_MARKERS = [
    "ddos-guard",
    "DDoS-Guard",
    "ddos_guard",
    "Check your browser",
    "Checking your browser before accessing",
    "Подождите, идёт проверка",
]

CAPTCHA_MARKERS = [
    "captcha",
    "recaptcha",
    "hcaptcha",
    "challenge-form",
]


def detect_block(
    response_html: Optional[str] = None,
    status_code: Optional[int] = None,
    cases_found: int = -1,
    expected_cases: int = -1,
) -> Tuple[bool, str]:
    """
    Detect if the current request was blocked by kad.arbitr.ru.

    Returns:
        (is_blocked: bool, reason: str)
    """
    # 1. HTTP-level blocks
    if status_code is not None:
        if status_code == 403:
            return True, f"HTTP 403 Forbidden"
        if status_code == 429:
            return True, f"HTTP 429 Too Many Requests"
        if status_code == 503:
            if response_html and any(m in response_html for m in DDOS_GUARD_MARKERS):
                return True, "HTTP 503 with DDoS-Guard challenge"

    # 2. DDoS-Guard challenge page
    if response_html:
        for marker in DDOS_GUARD_MARKERS:
            if marker in response_html:
                return True, f"DDoS-Guard challenge detected (marker: {marker})"

        for marker in CAPTCHA_MARKERS:
            if marker.lower() in response_html.lower():
                return True, f"CAPTCHA challenge detected (marker: {marker})"

    # 3. Soft block: 0 cases when we expect results
    # This is a heuristic — the site may legitimately return 0 results
    if cases_found == 0 and expected_cases > 0:
        return True, f"Soft block: 0 cases found but expected {expected_cases}"

    return False, ""


def is_content_suspicious(html: Optional[str]) -> bool:
    """
    Quick check if page content looks like a block page rather than real data.
    Useful during enrichment (case page scraping) to detect mid-scrape blocks.
    """
    if not html or len(html) < 500:
        return True  # Suspiciously small response

    # Real kad.arbitr.ru pages always contain these
    real_page_markers = ["kad.arbitr.ru", "Электронное правосудие", "Картотека"]
    has_real_content = any(m in html for m in real_page_markers)

    if not has_real_content:
        # Check for block page markers
        for marker in DDOS_GUARD_MARKERS + CAPTCHA_MARKERS:
            if marker in html:
                return True

    return not has_real_content
