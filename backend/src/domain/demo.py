from src.domain.spec import DEMO

SAMPLES = {'ledger': 'sample.csv', 'checks': 'checks_sample.csv', 'departures': 'departures_sample.csv'}


def demo_rows(kind='ledger'):
    from src.services.validation import parse_rows
    return parse_rows((DEMO / SAMPLES[kind]).read_text(encoding='utf-8-sig'), kind)
