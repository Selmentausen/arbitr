import json
from src.config.manager import ConfigManager
from src.filters.stage1_screen import _score_case_for_area, _check_judge_groups
from src.models.case import Case

def main():
    config = ConfigManager('configs/main.yaml')
    rules = config.get_rules('construction')
    print("Keywords:", rules.get('keywords', []))
    
    with open('data/raw_cases.json', 'r', encoding='utf-8') as f:
        cases = json.load(f)
        
    print(f"Loaded {len(cases)} cases")
    for row in cases[:5]:
        c = Case(**row)
        sc = _score_case_for_area(c, rules)
        jb = _check_judge_groups(c, config)
        
        text = " ".join([c.plaintiff, c.defendant, c.court]).lower()
        keys = [k for k in rules['keywords'] if k.lower() in text]
        
        print(f"Case: {c.case_number}")
        print(f"Plaintiff: {c.plaintiff}")
        print(f"Defendant: {c.defendant}")
        print(f"Score: {sc}, Bonus: {jb}")
        print(f"Keys matched: {keys}")
        print("-" * 40)

if __name__ == '__main__':
    main()
