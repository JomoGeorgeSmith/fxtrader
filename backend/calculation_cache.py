"""Disk cache for unchanged deterministic calculations, never AI advice."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .calculations import evaluate


def cached_evaluate(data,rules,directory,now=None):
    now=now or datetime.now()
    # Day changes affect forecasts; crossing the quote-age threshold affects
    # freshness warnings even when no workbook value changes.
    freshness={r['currency']:(now-datetime.fromisoformat(r['as_of'])).total_seconds()/3600>rules['rate_max_age_hours'] for r in data['rates']}
    code=''.join((Path(__file__).parent/name).read_text(encoding='utf-8') for name in ('calculations.py','liquidity_metrics.py','calculation_cache.py'))
    payload=dict(data=data,rules=rules,date=now.date().isoformat(),stale=freshness,code=hashlib.sha256(code.encode()).hexdigest())
    key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    path=Path(directory)/f'{key}.json'
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8')),True
        except (ValueError,OSError):
            pass
    result=evaluate(data,rules,now)
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result),encoding='utf-8')
        temporary.replace(path)
    except OSError:
        # Caching is optional, not a reason to suppress valid calculations.
        pass
    return result,False
