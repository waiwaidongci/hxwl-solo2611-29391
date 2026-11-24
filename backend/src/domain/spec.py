import json
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / 'demo'
BUSINESS = json.loads((DEMO / 'business.json').read_text(encoding='utf-8'))
TYPES = {'str': 'string', 'float': 'number', 'choice': 'select'}


def api_fields(items):
    return [{**item, 'type': TYPES.get(item['type'], item['type'])} for item in items]


business = BUSINESS
ledger, run, result, delivery = business['pages']
method = business['method']
reconcile = business.get('reconcile') or {}
dataset_kinds = [{'key': 'ledger', 'label': '合并台账', 'fields': business['fields'],
                  'sample': business['dataset']}] + list(reconcile.get('kinds') or [])
SPEC = {
    'title': business['title'], 'short_name': 'hxwl-solo2611-2', 'subtitle': business['description'],
    'organization': business['team'], 'workflow': business['description'], 'pains': business['pain_points'],
    'steps': business['script'], 'methodology': method if isinstance(method, list) else [method],
    'sample_name': business['dataset'], 'default_dataset': business['dataset'].removesuffix('.csv'),
    'entity': ledger, 'run': run, 'decision': '业务处理决定', 'action': '保存复核决定',
    'decision_guide': business['decision'], 'recipients': business['recipients'],
    'pages': ['值班概览', ledger, run, result, '业务复核', delivery],
    'fields': api_fields(business['fields']), 'params': api_fields(business['parameters']),
    'dataset_kinds': [{**item, 'fields': api_fields(item['fields']), 'sample_name': item['sample']}
                      for item in dataset_kinds],
    'run_modes': reconcile.get('run_modes') or [{'key': 'analysis', 'label': '台账关联分析'}],
    'reconcile_guide': reconcile.get('guide') or '',
    'scheduled': True, 'schedule_date_param': business.get('schedule_date_param'),
    'strict_test_params': business.get('strict_test_params'),
}
KIND_FIELDS = {item['key']: item['fields'] for item in SPEC['dataset_kinds']}
KIND_SAMPLES = {item['key']: item['sample_name'] for item in SPEC['dataset_kinds']}
