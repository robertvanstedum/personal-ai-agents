import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from minimoi_portal.guild_ui import memory_capture as m

NOW=datetime(2026,10,4,12,tzinfo=timezone.utc)
SAMPLES=Path(__file__).parent / 'fixtures' / 'memory_capture'

def payload():return json.loads((SAMPLES/'normal.matrix.json').read_text())
def show(tmp_path,doc,now=NOW):
 (tmp_path/'memory-capture-matrix.json').write_text(json.dumps(doc))
 return m.overview(tmp_path,now=now)

@pytest.mark.parametrize('sample,status',[('normal','current'),('watch','watch'),('failed','watch'),('missing','unknown'),('malformed','unknown'),('stale','unknown')])
def test_producer_contract(tmp_path,sample,status):
 doc=json.loads((SAMPLES/(sample+'.matrix.json')).read_text())
 assert show(tmp_path,doc)['status']==status

def test_report_expiry_retains_counts_but_no_healthy_rows(tmp_path):
 d=payload();result=show(tmp_path,d,NOW+timedelta(days=3))
 assert result['status']=='unknown'
 assert [r['records'] for r in result['rows']]==[1,2]
 assert all(r['status']=='unknown' for r in result['rows'])

@pytest.mark.parametrize('mutation',[lambda d:d.update(schema='v2'),lambda d:d.update(stale_after_s=True),lambda d:d.update(stale_after_s=99999999),lambda d:d.update(generated_at='2099-01-01T00:00:00Z'),lambda d:d.update(rows=[{}]),lambda d:d['rows'][0].update(captured_records=-1),lambda d:d['rows'][0].update(captured_records=True),lambda d:d['rows'][0].update(status='green'),lambda d:d['rows'][0].update(label='<script>'),lambda d:d['rows'][0].update(last_success_at=None),lambda d:d['rows'].append(d['rows'][0])])
def test_bad_contract_is_unknown(tmp_path,mutation):
 d=payload();mutation(d);assert show(tmp_path,d)['status']=='unknown'

def test_missing_symlink_oversize(tmp_path):
 assert m.overview(tmp_path)['status']=='unknown'
 p=tmp_path/'memory-capture-matrix.json';target=tmp_path/'elsewhere';target.write_text(json.dumps(payload()));p.symlink_to(target)
 assert m.overview(tmp_path,now=NOW)['status']=='unknown'
 p.unlink();p.write_bytes(b' '*65537);assert m.overview(tmp_path,now=NOW)['status']=='unknown'

def test_manual_off_check_and_job_separate(tmp_path):
 d=payload();d['job']['state']='failed';d['rows'][0].update(mode='manual',status='off',reason='not_approved',last_success_at=None,last_check_kind='none')
 d['rows'][1]['last_check_kind']='check'
 r=show(tmp_path,d)
 assert r['status']=='current' and r['rows'][0]['manual'] and r['rows'][0]['status']=='off'
 assert r['rows'][1]['kind']=='Check'

def test_empty_is_unknown(tmp_path):
 d=payload();d['rows']=[];assert show(tmp_path,d)['status']=='unknown'
