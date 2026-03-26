from bs4 import BeautifulSoup
from typing import Dict, List, Optional
import logging
from src.models.case import CaseParticipant, CaseInstance, CaseDocument

logger = logging.getLogger(__name__)

def parse_participants(html_content: str) -> Dict[str, List[CaseParticipant]]:
    """
    Parses participants from the case page HTML.
    Target: div#gr_case_partps > table.b-case-info > tbody > tr > td
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    participants = {
        'plaintiffs': [],
        'defendants': [],
        'third_parties': [],
        'others': []
    }
    
    # Scope to the specific container for participants
    container = soup.find('div', id='gr_case_partps')
    if not container:
        logger.warning("Container div#gr_case_partps not found")
        return participants

    # Find the specific table body row
    # Structure: div#gr_case_partps -> table.b-case-info -> tbody -> tr
    # There is usually only one table in this div, but we can be specific
    table = container.find('table', class_='b-case-info')
    if not table:
        logger.warning("Table .b-case-info not found in #gr_case_partps")
        return participants
        
    tbody = table.find('tbody')
    if not tbody:
         return participants
         
    tr = tbody.find('tr')
    if not tr:
        return participants

    mapping = {
        'plaintiffs': 'plaintiffs',
        'defendants': 'defendants',
        'third': 'third_parties',
        'others': 'others'
    }
    
    for cls, key in mapping.items():
        # Find the specific td for this role within the row
        # The class is on the TD, e.g. <td class="plaintiffs">
        # Note: The header also uses these classes, but we are in tbody > tr
        td = tr.find('td', class_=cls)
        
        if td:
            ul = td.find('ul')
            if ul:
                for li in ul.find_all('li', recursive=False):
                    try:
                        name_tag = li.find('a')
                        if not name_tag:
                            # Sometimes name is just text or in span
                            name_tag = li.find('span', class_='js-rollover')
                        
                        name = name_tag.get_text(strip=True) if name_tag else "Unknown"
                        
                        # Address/Details often in hidden span
                        details_span = li.find('span', class_='js-rolloverHtml')
                        address = details_span.get_text(strip=True) if details_span else None
                        
                        participants[key].append(CaseParticipant(name=name, address=address))
                    except Exception as e:
                        logger.warning(f"Failed to parse participant in {key}: {e}")
        else:
             # logger.debug(f"No td found for {cls} in participant table")
             pass
            
    return participants

def parse_instances(html_content: str) -> List[CaseInstance]:
    """
    Parses instance headers from the chronology section.
    Does NOT parse the full chronology items (loaded dynamically).
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    instances = []
    
    # Find all instance headers
    headers = soup.find_all('div', class_='b-chrono-item-header')
    
    for header in headers:
        try:
            # Court Name
            court_tag = header.find('span', class_='instantion-name')
            court_name = court_tag.get_text(strip=True) if court_tag else "Unknown Court"
            
            # Case Number (Instance specific)
            case_num_tag = header.find('strong', class_='b-case-instance-number')
            case_number = case_num_tag.get_text(strip=True) if case_num_tag else None
            
            # Incoming Number
            inc_num_tag = header.find('span', class_='b-reg-incoming_num')
            incoming_number = inc_num_tag.get_text(strip=True) if inc_num_tag else None
            
            # Date
            date_tag = header.find('span', class_='b-reg-date')
            date = date_tag.get_text(strip=True) if date_tag else None
            
            instances.append(CaseInstance(
                court_name=court_name,
                case_number=case_number,
                incoming_number=incoming_number,
                date=date
            ))
        except Exception as e:
            logger.warning(f"Failed to parse instance header: {e}")
            
    return instances

def verify_main_data(html_content: str, expected_data: Dict[str, str]) -> Dict[str, bool]:
    """
    Verifies if key data (Judge, Plaintiff, Defendant) matches expected values.
    Returns a dict of verification results (e.g. {'plaintiff_match': True, ...}).
    """
    results = {
        'plaintiff_match': False,
        'defendant_match': False,
        'judge_match': False
    }
    
    participants = parse_participants(html_content)
    
    # Check Plaintiffs
    expected_plaintiff = expected_data.get('plaintiff')
    if expected_plaintiff:
        # Simple substring check or exact match? doing substring for robustness
        for p in participants.get('plaintiffs', []):
            if expected_plaintiff.lower() in p.name.lower():
                results['plaintiff_match'] = True
                break
                
    # Check Defendants
    expected_defendant = expected_data.get('defendant')
    if expected_defendant:
        for p in participants.get('defendants', []):
            if expected_defendant.lower() in p.name.lower():
                results['defendant_match'] = True
                break
                
    # Judge verification (Note: Judge is often missing in static HTML)
    # expected_judge = expected_data.get('judge')
    # if expected_judge:
    #     # Look in parsed judges if we had them, or scan text?
    #     if expected_judge in html_content:
    #          pass
             
    return results
