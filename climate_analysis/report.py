"""保留已知时点报告, 比较区域判断变化; 归档报告不得覆盖。"""
import hashlib,json
from datetime import datetime
from .metrics import compare
from .context import decide
from .seasonal import compare_scenarios,compare_reports

def generate(db,known_at):
 measures=compare(db,known_at);decisions=[decide(db,known_at,row) for row in measures]
 out={'known_at':known_at,'measurements':measures,'decisions':decisions}
 out['fingerprint']=hashlib.sha256(json.dumps(out,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
 return out
def difference(a,b):
 old={r['id']:r for r in a['decisions']};new={r['id']:r for r in b['decisions']}
 return {'before':a['fingerprint'],'after':b['fingerprint'],'changed':sorted(k for k in old.keys()|new.keys() if old.get(k)!=new.get(k))}

def _fingerprint(body):
 return hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()

def _report_id(kind,known_at,scenario):
 digest=_fingerprint([kind,known_at,scenario])[:16]
 return f'{kind}:{known_at}:{digest}'

def archive(db,kind,body,known_at,scenario,archived_at=None):
 """保存一份已生成的报告。相同输入必须得到相同正文; 不同正文不得覆盖。"""
 body={key:value for key,value in body.items() if key!='fingerprint'}
 body['fingerprint']=_fingerprint(body)
 report_id=_report_id(kind,known_at,scenario)
 scenario_json=json.dumps(scenario,ensure_ascii=False,sort_keys=True,separators=(',',':'))
 stored=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
 stamp=(archived_at or datetime.now().astimezone().isoformat())
 existing=db.execute('SELECT fingerprint,body_json FROM reports WHERE report_id=?',(report_id,)).fetchone()
 if existing:
  if existing[1]!=stored:raise ValueError('同一报告标识的正文冲突, 不得覆盖历史决定')
  return {'report_id':report_id,'fingerprint':existing[0],'replayed':True}
 with db:
  db.execute('INSERT INTO reports VALUES(?,?,?,?,?,?,?)',
   (report_id,kind,known_at,scenario_json,body['fingerprint'],stored,stamp))
 return {'report_id':report_id,'fingerprint':body['fingerprint'],'replayed':False}

def replay(db,report_id):
 """按归档时保存的正文复现报告, 不重新读取可能已修订的来源。"""
 row=db.execute('SELECT body_json,fingerprint,known_at FROM reports WHERE report_id=?',(report_id,)).fetchone()
 if not row:raise KeyError(f'未找到报告 {report_id}')
 body=json.loads(row[0])
 check={key:value for key,value in body.items() if key!='fingerprint'}
 if _fingerprint(check)!=row[1]:raise ValueError('归档报告正文与指纹不符')
 out=dict(body);out['report_id']=report_id
 return out

def list_reports(db,kind=None):
 sql='SELECT report_id,known_at,fingerprint,archived_at FROM reports'
 args=()
 if kind:sql+=' WHERE report_type=?';args=(kind,)
 rows=db.execute(sql+' ORDER BY known_at,report_id',args).fetchall()
 return [{'report_id':r[0],'known_at':r[1],'fingerprint':r[2],'archived_at':r[3]} for r in rows]

def seasonal_report(db,known_at,scenarios,archived_at=None):
 """生成并归档跨季方案比较; 返回正文与归档标识。"""
 out=compare_scenarios(db,known_at,scenarios)
 out['fingerprint']=_fingerprint(out)
 saved=archive(db,'seasonal',out,known_at,scenarios,archived_at)
 out['report_id']=saved['report_id']
 out['replayed']=saved['replayed']
 return out

def seasonal_difference(before,after):
 """比较两份跨季报告, 按方案逐月说明影响与差异原因。"""
 pairs=[]
 for scenario_id in sorted(before['scenarios'].keys()&after['scenarios'].keys()):
  result=compare_reports(before['scenarios'][scenario_id],after['scenarios'][scenario_id])
  result['before']=before.get('report_id');result['after']=after.get('report_id')
  pairs.append(result)
 return {'before':before.get('report_id'),'after':after.get('report_id'),
  'changed_inputs':sorted({kind for row in pairs for kind in row['changed_inputs']}),
  'affected_months':sorted({month for row in pairs for month in row['affected_months']}),
  'scenarios':pairs}

