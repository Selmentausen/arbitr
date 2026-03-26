import asyncio
from src.config.manager import ConfigManager
from src.scraper.playwright_scraper import PlaywrightScraper
from src.scraper.parser import parse_case_card
from src.models.case import Case, CaseInstance

async def test_stage2_parsing():
    # 1. Setup config and scraper
    config = ConfigManager("configs/main.yaml")
    config._config["scraping"]["proxy"]["enabled"] = False
    
    scraper = PlaywrightScraper(config, headless=False)
    
    url = "https://kad.arbitr.ru/Card/e043af28-5d80-4022-9848-ed09df41f802"
    print(f"Fetching case content from {url}...")
    
    # 2. Get the HTML of the case card 
    html = await scraper.get_case_content(url, judge_name="Солдатов Р. С.")
    print(f"Scraped {len(html)} bytes of HTML. Parsing now...")
    
    # 3. Run the parser we just wrote!
    result = parse_case_card(html)

    instances = []
    if result.get("documents"):
        instances.append(CaseInstance(court_name="АС Московской области", documents=result["documents"]))
    
    # 4. Print out what we extracted
    print("\n--- RESULTS ---")
    print("\nPlaintiffs:")
    for p in result.get("participants", {}).get("plaintiff", []):
        print(f"  - {p.name}\n    Details: {p.address}")
        
    print("\nDefendants:")
    for d in result.get("participants", {}).get("defendant", []):
        print(f"  - {d.name}\n    Details: {d.address}")
        
    print("\nDocuments Found:")
    for instance in result.get("instances", []):
        for doc in instance.documents:
            print(f"  - [{doc.type}] {doc.filename}")
            print(f"    Link: {doc.url}")

if __name__ == "__main__":
    asyncio.run(test_stage2_parsing())
