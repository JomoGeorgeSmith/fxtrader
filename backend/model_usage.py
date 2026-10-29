"""Provider-reported token ledger; no price or credits inferred from token counts."""
from datetime import datetime, timezone


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS model_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, run_id TEXT NOT NULL,
        stage TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
        input_tokens INTEGER, output_tokens INTEGER, cached_input_tokens INTEGER,
        total_tokens INTEGER, error_type TEXT)''')


def record(db, *, run_id, stage, model, usage=None, error_type=None):
    """Null means the provider did not report usage; it never means zero tokens."""
    usage=usage if isinstance(usage,dict) else None
    prompt=usage.get('prompt_tokens') if usage else None
    completion=usage.get('completion_tokens') if usage else None
    cached=(usage.get('prompt_tokens_details') or {}).get('cached_tokens') if usage else None
    total=usage.get('total_tokens') if usage else None
    status='failed' if error_type else 'reported' if usage else 'unreported'
    db.execute('''INSERT INTO model_usage
        (created,run_id,stage,model,status,input_tokens,output_tokens,cached_input_tokens,total_tokens,error_type)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (datetime.now(timezone.utc).isoformat(),run_id,stage,model,status,prompt,completion,cached,total,error_type))


def report(db):
    columns=('id','created','run_id','stage','model','status','input_tokens','output_tokens','cached_input_tokens','total_tokens','error_type')
    calls=[dict(zip(columns,row)) for row in db.execute(
        'SELECT '+','.join(columns)+' FROM model_usage ORDER BY id DESC LIMIT 200')]
    totals=dict(zip(('calls','reported_calls','unreported_calls','failed_calls','input_tokens','output_tokens','cached_input_tokens','total_tokens'),
        db.execute('''SELECT COUNT(*),SUM(status='reported'),SUM(status='unreported'),SUM(status='failed'),
            SUM(input_tokens),SUM(output_tokens),SUM(cached_input_tokens),SUM(total_tokens) FROM model_usage''').fetchone()))
    stages=[dict(zip(('stage','model','calls','input_tokens','output_tokens','cached_input_tokens','total_tokens'),row))
        for row in db.execute('''SELECT stage,model,COUNT(*),SUM(input_tokens),SUM(output_tokens),
            SUM(cached_input_tokens),SUM(total_tokens) FROM model_usage GROUP BY stage,model ORDER BY total_tokens DESC''')]
    return dict(totals=totals,stages=stages,calls=calls,
        note='Provider-reported tokens only; cached input is part of input, not additional tokens. Credits, prices and past unlogged usage are not available; failed or unreported calls may still incur charges.')
