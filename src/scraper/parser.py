"""
HTML and JSON parsers for kad.arbitr.ru API responses.
Converts raw responses into structured Pydantic models.
"""

import re
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
from datetime import datetime

from src.models.case import (
    CaseBase, CaseParticipant, CaseInstance, CaseDocument,
    InstanceUpdate, PartyInfo
    )
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
        "extracted_data": {},
        "case_status_text": None,
        "case_category_text": None,
        "claim_amount": None,
    }

    # -- Case-level metadata ---
    
    status_elem = soup.select_one('.b-case-header-desc')
    if status_elem:
        result["case_status_text"] = status_elem.get_text(strip=True)
    
    category_elem = soup.select_one('#case-category')
    if category_elem:
        result["case_category_text"] = category_elem.get_text(strip=True)

    dur_elem = soup.select_one('.b-case-overview .case-dur')
    if dur_elem:
        result["extracted_data"]["duration"] = dur_elem.get_text(strip=True)

    date_elem = soup.select_one('.b-case-overview .case-date a')
    if date_elem:
        result["extracted_data"]["registration_date"] = date_elem.get_text(strip=True)

    # --- Participants ---

    participant_classes = {
        ".plaintiffs li": "plaintiff",
        ".defendants li": "defendant",
        ".third li": "third_party",
        ".others li": "other_party"
    }
    for html_class, section in participant_classes.items():
        for li in soup.select(html_class):
            name_elem = li.select_one('a')
            rollover_elem =li.select_one('.js-rolloverHtml')

            if name_elem:
                name = name_elem.get_text(strip=True)
                inn = None
                address = None
                if rollover_elem:
                    inn_div = rollover_elem.find('div')
                    if inn_div:
                        inn_match = re.search(r'ИНН:\s*(\d+)', inn_div.get_text(strip=True))
                        if inn_match:
                            inn = inn_match.group(1)
                    rollover_copy = BeautifulSoup(str(rollover_elem), 'html.parser')
                    for tag in rollover_copy.find_all(['strong', 'div']):
                        tag.decompose()
                    addr_text = rollover_copy.get_text(strip=True)
                    if addr_text and addr_text != "Данные скрыты":
                        address = addr_text
                result["participants"].setdefault(section, []).append(
                    CaseParticipant(name=name, address=address, inn=inn, role=section)
                )

    # --- Instances and their update history ---

    for instance_block in soup.select('.b-chrono-item-header.js-chrono-item-header'):
        l_col = instance_block.select_one('.l-col')
        r_col = instance_block.select_one('.r-col')

        # Instance level (Первая инстанция, Апелляционная инстанция)
        level_elem = l_col.select_one('strong') if l_col else None
        instance_level = level_elem.get_text(strip=True) if level_elem else None

        # Date on the header
        reg_date_elem = l_col.select_one('.b-reg-date') if l_col else None
        reg_date = reg_date_elem.get_text(strip=True) if reg_date_elem else None

        # Instance number and court
        num_elem = r_col.select_one('.b-case-instance-number') if r_col else None
        case_num = num_elem.get_text(strip=True) if num_elem else None

        court_elem = r_col.select_one('.instantion-name a') if r_col else None
        court_name = court_elem.get_text(strip=True) if court_elem else "Неизвестный суд"

        # Header result text and PDF
        result_text = None
        result_pdf_url = None
        result_h2 = r_col.select_one('h2.b-case-result') if r_col else None
        if result_h2:
            result_link = result_h2.select_one('a[href]')
            if result_link:
                result_pdf_url = result_link.get('href')
                result_text = result_link.get_text(strip=True)
            else:
                result_text = result_h2.get_text(strip=True)

        # --- Parse update history (items inside the expanded section) ---
        updates = []
        docs = []

        # The items container is the next sibling div after the header
        items_container = instance_block.find_next_sibling(
            'div', class_='b-chrono-items-container'
        )
        if items_container:
            for item in items_container.select('.b-chrono-item.js-chrono-item'):
                update = _parse_chrono_item(item)
                updates.append(update)

                # If this update has a PDF, also track it as a document
                if update.pdf_url:
                    docs.append(CaseDocument(
                        id=item.get('data-id') or None,
                        filename=update.content,
                        url=update.pdf_url,
                        date=update.date,
                        type=update.update_type,
                        publish_date=update.pdf_publish_date,
                    ))

        instance = CaseInstance(
            court_name=court_name,
            case_number=case_num,
            instance_level=instance_level,
            date=reg_date,
            result_text=result_text,
            result_pdf_url=result_pdf_url,
            updates=updates,
            documents=docs,
        )
        result["instances"].append(instance)

    # --- Extract claim amount from the initial filing's additional-info ---
    for item in soup.select('.b-chrono-item .additional-info'):
        text = item.get_text(strip=True)
        amount_match = re.search(r'Сумма исковых требований\s*([\d\s,\.]+)', text)
        if amount_match:
            try:
                amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                result["claim_amount"] = float(amount_str)
            except ValueError:
                pass
            break

    return result


def _parse_chrono_item(item) -> InstanceUpdate:
    """Parse a single chronology item (.b-chrono-item) into an InstanceUpdate."""
    l_col = item.select_one('.l-col')
    r_col = item.select_one('.r-col')

    # Date
    date_elem = l_col.select_one('.case-date') if l_col else None
    date = date_elem.get_text(strip=True) if date_elem else None

    # Update type (Определение, Письмо, Жалоба, etc.)
    type_elem = l_col.select_one('.case-type') if l_col else None
    update_type = type_elem.get_text(strip=True) if type_elem else None

    # Subject (who filed / judge name)
    subject = None
    subject_elem = r_col.select_one('.case-subject') if r_col else None
    if subject_elem:
        subject = subject_elem.get_text(strip=True)

    # Content text and PDF URL
    content = None
    pdf_url = None
    if r_col:
        result_elem = r_col.select_one('.b-case-result')
        if result_elem:
            pdf_link = result_elem.select_one('a.js-case-result-text--doc_link')
            if pdf_link:
                pdf_url = pdf_link.get('href')
                content = pdf_link.get_text(strip=True)
            else:
                text_span = result_elem.select_one('.b-case-result-text')
                if text_span:
                    content = text_span.get_text(strip=True)

    # Clean up content — remove [Подписано] prefix if present
    if content:
        content = re.sub(r'^\s*\[Подписано\]\s*', '', content).strip()

    # Publication date
    pdf_publish_date = None
    publish_elem = r_col.select_one('.b-case-publish_info') if r_col else None
    if publish_elem:
        pdf_publish_date = publish_elem.get_text(strip=True)

    # Additional info (barcode, claim amount, response-to references)
    additional_info = None
    info_elem = r_col.select_one('.additional-info') if r_col else None
    if info_elem:
        additional_info = info_elem.get_text(strip=True)

    # Judge panel info from rollover
    judge_panel = None
    reporting_judge = None
    judge_rollover = r_col.select_one('.js-judges-rolloverHtml') if r_col else None
    if judge_rollover:
        rollover_text = judge_rollover.get_text(separator="\n", strip=True)
        panel_match = re.search(r'Судебный состав:\s*(.+)', rollover_text)
        if panel_match:
            judge_panel = panel_match.group(1).strip()
        reporter_match = re.search(r'Судья-докладчик:\s*(.+)', rollover_text)
        if reporter_match:
            reporting_judge = reporter_match.group(1).strip()

    return InstanceUpdate(
        date=date,
        update_type=update_type,
        subject=subject,
        content=content,
        pdf_url=pdf_url,
        pdf_publish_date=pdf_publish_date,
        additional_info=additional_info,
        judge_panel=judge_panel,
        reporting_judge=reporting_judge,
    )


def _extract_party_info_from_rollover(td_element) -> Optional[PartyInfo]:
    rollover = td_element.find('span', class_='js-rolloverHtml')
    if not rollover:
        return None

    # Get the party name from the link
    name_link = td_element.find('a')
    name = name_link.get_text(strip=True) if name_link else None
    if not name:
        return None

    # Extract INN from dedicated div
    inn = None
    inn_div = rollover.find('div')
    if inn_div:
        inn_text = inn_div.get_text(strip=True)
        inn_match = re.search(r'ИНН:\s*(\d+)', inn_text)
        if inn_match:
            inn = inn_match.group(1)
    
    rollover_copy = BeautifulSoup(str(rollover), 'html.parser')
    for tag in rollover_copy.find_all(['strong', 'div']):
        tag.decompose()
    address = rollover_copy.get_text(strip=True)
    if address == "Данные скрыты" or not address:
        address = None
    
    return PartyInfo(name=name, inn=inn, address=address)



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
    
    case_id = case_url.split("/")[-1]
    
    # Extract date
    case_type = None
    date_div = None
    for ct in ["civil", "administrative", "bankruptcy"]:
        date_div = row.find('div', class_=ct)
        if date_div:
            case_type = ct
            break

    date_str = date_div.find('span').get_text(strip=True) if date_div else ""
    filing_date = _parse_date(date_str) if date_str else None
    
    # Extract court and judges
    court_td = row.find('td', class_='court')
    court_name = ""
    judges = []
    
    if court_td:
        # First isolate the judge name so it doesn't bleed into the court text
        judge_div = court_td.find('div', class_='judge')
        if judge_div:
            judge_name = judge_div.get('title') or judge_div.get_text(strip=True)
            if judge_name:
                judges.append(judge_name)
            # Remove the judge element completely from the HTML tree
            judge_div.decompose()
            
        # The remaining text inside court_td defines the court
        court_name = court_td.get('title') or court_td.get_text(separator=" ", strip=True)
    
    # Extract plaintiff
    plaintiff_td = row.find('td', class_='plaintiff')
    plaintiff = _extract_party_name(plaintiff_td) if plaintiff_td else "Unknown"
    
    # Extract defendant
    defendant_td = row.find('td', class_='respondent')
    defendant = _extract_party_names(defendant_td) if defendant_td else "Unknown"

    plaintiff_info = _extract_party_info_from_rollover(plaintiff_td) if plaintiff_td else None

    defendant_info = []
    if defendant_td:
        for rollover_span in defendant_td.find_all('span', class_=re.compile(r'rollover')):
            info = _extract_party_info_from_rollover(rollover_span)
            if info:
                defendant_info.append(info)
    
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
            case_url=case_url,
            case_type=case_type,
            plaintiff_info=plaintiff_info,
            defendant_info=defendant_info,
            scraped_at=datetime.utcnow(),
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
