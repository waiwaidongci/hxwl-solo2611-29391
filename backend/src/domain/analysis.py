from src.services import analysis


def validate_rows(rows):
    validator = getattr(analysis, 'validate_rows', None)
    if validator:
        validator(rows)


def wrap(raw):
    tables = []
    for item in raw['tables']:
        columns = [{'key': str(i), 'label': label} for i, label in enumerate(item['columns'])]
        tables.append({'title': item['title'], 'columns': columns, 'rows': [
            dict(zip((col['key'] for col in columns), row, strict=True)) for row in item['rows']
        ]})
    series = raw.get('series') or []
    return {
        'headline': raw['summary'], 'note': ' '.join(raw.get('notes') or []),
        'metrics': raw['metrics'], 'tables': tables,
        'charts': [{'kind': 'bar', 'title': raw.get('series_title', '业务指标对比'), 'series': series}] if series else [],
        'ready': raw.get('ready', True),
    }


def analyze(rows, params):
    return wrap(analysis.analyze(rows, params))
