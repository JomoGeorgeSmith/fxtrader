"""Optional integration check against the running app. Restores generated input bytes."""
import json
import urllib.request
from pathlib import Path
from openpyxl import load_workbook

BASE='http://127.0.0.1:8000'
FILE=Path(__file__).resolve().parents[1]/'shared'/'Bank_Balances.xlsx'

def state():
    with urllib.request.urlopen(BASE+'/api/state') as response:
        return json.load(response)

def wait_for_run(previous, expected):
    with urllib.request.urlopen(BASE+'/api/events',timeout=180) as response:
        for raw in response:
            if not raw.startswith(b'data: '):
                continue
            current=json.loads(raw[6:])
            if current['stage']=='error':
                raise AssertionError(current['message'])
            a=current.get('analysis')
            if current['stage']=='ready' and a and a['id']!=previous:
                assert expected(a),a['positions']
                return a
    raise AssertionError('No new analysis received')

if __name__=='__main__':
    original=FILE.read_bytes()
    initial=state()['analysis']
    assert initial,'Initial run must be ready first'
    try:
        wb=load_workbook(FILE)
        original_balance=wb['Accounts']['D3'].value
        wb['Accounts']['D3']=original_balance+1600000
        wb.save(FILE)
        wb.close()
        updated=wait_for_run(initial['id'],lambda a:a.get('suggestion_origin')=='analyst' and not any(f['id']=='liquidity-USD' for f in a['findings']))
        print('PASS: meaningful Excel update -> Kafka -> both agents -> USD funding warning clears')
        print('Modes:',updated['standardizer_mode'], '/',updated['ai_mode'])
    finally:
        previous=state()['analysis']['id']
        FILE.write_bytes(original)
    restored=wait_for_run(previous,lambda a:a.get('suggestion_origin')=='analyst' and any('liquidity-USD' in s.get('finding_ids',[]) for s in a['suggestions']))
    print('PASS: workbook restored -> fresh analysis -> original warning returns')
