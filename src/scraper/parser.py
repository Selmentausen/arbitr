"""
HTML and JSON parsers for kad.arbitr.ru API responses.
Converts raw responses into structured Pydantic models.
"""

import re
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
from datetime import datetime

from src.models.case import CaseBase, CaseParticipant, CaseInstance, CaseDocument
from src.utils.logger import get_logger


logger = get_logger(__name__)


def parse_judge_suggest(response_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Parse judge autocomplete API response.
    
    Args:
        response_data: JSON response from /Suggest/Judges
        
    Returns:
        List of judge dictionaries with Id, Name, CourtName, etc.
    """
    if not response_data.get("Success"):
        logger.warning("Judge suggest API returned Success=false")
        return []
    
    result = response_data.get("Result", {})
    items = result.get("Items", [])
    
    logger.debug(f"Parsed {len(items)} judge suggestions")
    return items


def parse_case_list(html_content: str) -> tuple[List[CaseBase], Dict[str, Any]]:
    """
    Parse HTML table from /Kad/SearchInstances into CaseBase objects.
    
    Args:
        html_content: HTML response containing case table
        
    Returns:
        Tuple of (list of CaseBase objects, pagination metadata)
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    cases = []
    
    # Find all table rows (skip header if exists)
    rows = soup.find_all('tr')
    
    for row in rows:
        try:
            # Extract case data from table cells
            case_data = _extract_case_from_row(row)
            if case_data:
                cases.append(case_data)
        except Exception as e:
            logger.warning(f"Failed to parse table row: {e}")
            continue
    
    # Extract pagination metadata
    pagination = _extract_pagination(soup)
    
    logger.info(f"Parsed {len(cases)} cases from HTML response")
    logger.debug(f"Pagination: {pagination}")
    
    return cases, pagination

def parse_case_card(html_content: str) -> Dict[str, Any]:
    """
    Parse case card HTML into structured components

    Returns a dictionary with parsed participants, documents and extracted data
    that can be merged into a Case object by the calller.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    result = {
        "participants": {},
        "instances": [],
        "extracted_data": {}
    }

    for section in ["plaintiff", "defendant"]:
        for li in soup.select(f".{section}s li"):
            name_elem = li.select_one('a')
            detail_elem =li.select_one('.js-rolloverHtml')

            if name_elem:
                name = name_elem.get_text(strip=True)
                detail = detail_elem.get_text(strip=True) if detail_elem else None
                #TODO actually parse the ogrn, inn and address from detail
                result["participants"].setdefault(section, []).append(CaseParticipant(name=name, address=detail))

    for instance_block in soup.select('.js-chrono-item-header'):
        court_elem = instance_block.select_one('.instantion-name a')
        court_name = court_elem.get_text(strip=True) if court_elem else "Неизвестный суд"
        level_elem = instance_block.select_one('.l-col-strong')
        instance_level = level_elem.get_text(strip=True) if level_elem else None

        docs = []
        doc_links = instance_block.select('a[href*="/PdfDocument/"], a[href*="/Document/"]',)

        for link in doc_links:
            url = link.get('href')
            case_result_text = link.get_text(strip=True)
            filename = url.split('/')[-1] if '/' in url else "document.pdf"

            doc = CaseDocument(
                filename=filename,
                url=url,
                type=instance_level or "document"
            )
            docs.append(doc)
        
            if case_result_text:
                result["extracted_data"].setdefault(court_name, {})["result"] = case_result_text
        
        if docs:
            instance = CaseInstance(court_name=court_name, documents=docs)
            result["instances"].append(instance)

    return result

    

def _extract_case_from_row(row) -> Optional[CaseBase]:
    """
    Extract case data from a single table row.
    
    Args:
        row: BeautifulSoup TR element
        
    Returns:
        CaseBase object or None if row doesn't contain case data
    """
    # Find case number link
    case_link = row.find('a', class_='num_case')
    if not case_link:
        return None  # Not a case row
    
    case_number = case_link.get_text(strip=True)
    case_url = case_link.get('href', '')
    
    # Extract case ID from URL
    # Example: https://kad.arbitr.ru/Card/2fa9c31f-9617-4c7c-8b9b-2b2af75aace1
    case_id_match = re.search(r'/Card/([a-f0-9-]+)', case_url)
    if not case_id_match:
        logger.warning(f"Could not extract case ID from URL: {case_url}")
        return None
    
    case_id = case_id_match.group(1)
    
    # Extract date
    date_div = row.find('div', class_=re.compile(r'civil'))
    date_str = date_div.find('span').get_text(strip=True) if date_div else ""
    filing_date = _parse_date(date_str) if date_str else None
    
    # Extract court and judges
    court_td = row.find('td', class_='court')
    court_name = ""
    judges = []
    
    if court_td:
        # Court name (usually second div)
        court_divs = court_td.find_all('div', recursive=False)
        for div in court_divs:
            if 'judge' not in div.get('class', []):
                court_name = div.get('title') or div.get_text(strip=True)
                break
        
        # Judge name
        judge_div = court_td.find('div', class_='judge')
        if judge_div:
            judge_name = judge_div.get('title') or judge_div.get_text(strip=True)
            if judge_name:
                judges.append(judge_name)
    
    # Extract plaintiff
    plaintiff_td = row.find('td', class_='plaintiff')
    plaintiff = _extract_party_name(plaintiff_td) if plaintiff_td else "Unknown"
    
    # Extract defendant
    defendant_td = row.find('td', class_='respondent')
    defendant = _extract_party_names(defendant_td) if defendant_td else "Unknown"
    
    # Create CaseBase object
    try:
        case = CaseBase(
            id=case_id,
            case_number=case_number,
            court=court_name or "Unknown",
            judges=judges,
            plaintiff=plaintiff,
            defendant=defendant,
            filing_date=filing_date,
            case_url=case_url
        )
        return case
    except Exception as e:
        logger.error(f"Failed to create CaseBase for {case_number}: {e}")
        return None


def _extract_party_name(td_element) -> str:
    """
    Extract first party name from a table cell.
    
    Args:
        td_element: BeautifulSoup TD element
        
    Returns:
        Party name or "Unknown"
    """
    # Find first span with rollover class
    rollover = td_element.find('span', class_=re.compile(r'rollover'))
    if rollover:
        # Get text, excluding the hidden rolloverHtml
        name = rollover.find(text=True, recursive=False)
        if name:
            return name.strip()
        
        # Fallback: get all text
        for child in rollover.children:
            if hasattr(child, 'get') and 'js-rolloverHtml' in child.get('class', []):
                continue
            text = child.get_text(strip=True) if hasattr(child, 'get_text') else str(child).strip()
            if text:
                return text
    
    return "Unknown"


def _extract_party_names(td_element) -> str:
    """
    Extract all party names from a table cell (for multiple defendants).
    
    Args:
        td_element: BeautifulSoup TD element
        
    Returns:
        Comma-separated party names or "Unknown"
    """
    names = []
    
    # Find all rollover spans
    rollovers = td_element.find_all('span', class_=re.compile(r'rollover'))
    for rollover in rollovers:
        # Get text, excluding the hidden rolloverHtml
        for child in rollover.children:
            if hasattr(child, 'get') and 'js-rolloverHtml' in child.get('class', []):
                continue
            text = child.get_text(strip=True) if hasattr(child, 'get_text') else str(child).strip()
            if text:
                names.append(text)
                break  # Only first text node per rollover
    
    return ", ".join(names) if names else "Unknown"


def _parse_date(date_str: str) -> Optional[datetime]:
    """
    Parse Russian date string to datetime.
    
    Args:
        date_str: Date string in format "DD.MM.YYYY"
        
    Returns:
        datetime object or None if parsing fails
    """
    try:
        return datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        logger.warning(f"Failed to parse date: {date_str}")
        return None


def _extract_pagination(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract pagination metadata from HTML.
    
    Args:
        soup: BeautifulSoup object
        
    Returns:
        Dictionary with page, total_count, pages_count, page_size
    """
    pagination = {
        "page": 1,
        "page_size": 25,
        "total_count": 0,
        "pages_count": 0
    }
    
    # Find hidden inputs with pagination data
    page_input = soup.find('input', id='documentsPage')
    if page_input and page_input.get('value'):
        pagination["page"] = int(page_input['value'])
    
    page_size_input = soup.find('input', id='documentsPageSize')
    if page_size_input and page_size_input.get('value'):
        pagination["page_size"] = int(page_size_input['value'])
    
    total_count_input = soup.find('input', id='documentsTotalCount')
    if total_count_input and total_count_input.get('value'):
        pagination["total_count"] = int(total_count_input['value'])
    
    pages_count_input = soup.find('input', id='documentsPagesCount')
    if pages_count_input and pages_count_input.get('value'):
        pagination["pages_count"] = int(pages_count_input['value'])
    
    return pagination
