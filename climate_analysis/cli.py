"""从本地合成资料形成单期报告与跨季方案比较。"""
import argparse,json
from pathlib import Path
from .evidence import connect,receive
from .report import generate,difference,seasonal_report,seasonal_difference,replay,list_reports

def _load(input_path,db):
 data=json.loads(Path(input_path).read_text(encoding='utf8'))
 for doc in data['documents']:receive(db,doc)
 return data

def _periods(data):
 known=data['known_at']
 return known if isinstance(known,list) else [known]

def inspect(args):
 db=connect(':memory:')
 try:
  data=_load(args.input,db)
  a,b=[generate(db,t) for t in _periods(data)[:2]]
  print(json.dumps({'before':a,'after':b,'change':difference(a,b)},ensure_ascii=False,indent=2))
 finally:db.close()

def seasonal(args):
 db=connect(args.db or ':memory:')
 try:
  if args.input:data=_load(args.input,db)
  else:
   data=json.loads(Path(args.scenario).read_text(encoding='utf8'))
   for doc in data.get('documents',[]):receive(db,doc)
  scenarios=data['scenarios']
  outputs=[]
  for known_at in _periods(data):
   outputs.append(seasonal_report(db,known_at,scenarios,archived_at=known_at))
  result={'reports':outputs}
  if len(outputs)==2:result['change']=seasonal_difference(outputs[0],outputs[1])
  result['archived']=list_reports(db,'seasonal')
  print(json.dumps(result,ensure_ascii=False,indent=2))
 finally:db.close()

def history(args):
 db=connect(args.db)
 try:print(json.dumps(list_reports(db,args.kind),ensure_ascii=False,indent=2))
 finally:db.close()

def show(args):
 db=connect(args.db)
 try:print(json.dumps(replay(db,args.report_id),ensure_ascii=False,indent=2))
 finally:db.close()

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 base=sub.add_parser('inspect');base.add_argument('input')
 one=sub.add_parser('seasonal');one.add_argument('--input');one.add_argument('--scenario')
 one.add_argument('--db')
 hist=sub.add_parser('history');hist.add_argument('--db',required=True);hist.add_argument('--kind')
 show_p=sub.add_parser('replay');show_p.add_argument('--db',required=True);show_p.add_argument('report_id')
 args=p.parse_args()
 {'inspect':inspect,'seasonal':seasonal,'history':history,'replay':show}[args.command](args)
if __name__=='__main__':main()
