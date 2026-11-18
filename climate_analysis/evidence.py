"""保留决策输入的原始版本、有效区间及取得时间。"""
import hashlib
import json
import sqlite3
from datetime import datetime

SCHEMA="""
CREATE TABLE IF NOT EXISTS documents (
  source TEXT NOT NULL, document_id TEXT NOT NULL, revision TEXT NOT NULL,
  received_at TEXT NOT NULL, payload_json TEXT NOT NULL, sha256 TEXT NOT NULL,
  PRIMARY KEY(source,document_id,revision)
);
CREATE TABLE IF NOT EXISTS observations (
  kind TEXT NOT NULL, record_id TEXT NOT NULL, revision TEXT NOT NULL,
  document_source TEXT NOT NULL, document_id TEXT NOT NULL,
  document_revision TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
  payload_json TEXT NOT NULL, PRIMARY KEY(kind,record_id,revision),
  FOREIGN KEY(document_source,document_id,document_revision)
    REFERENCES documents(source,document_id,revision)
);
CREATE TABLE IF NOT EXISTS reports (
  report_id TEXT PRIMARY KEY, report_type TEXT NOT NULL, known_at TEXT NOT NULL,
  scenario_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
  body_json TEXT NOT NULL, archived_at TEXT NOT NULL
);
"""

def connect(path):
    db=sqlite3.connect(path)
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    columns={row[1] for row in db.execute('PRAGMA table_info(observations)')}
    if 'valid_to' not in columns:
        db.execute('ALTER TABLE observations ADD COLUMN valid_to TEXT')
        db.commit()
    return db

def _validity(value,label):
    """有效时间可写完整日期, 也可只写到月份 (YYYY-MM)。"""
    if not isinstance(value,str):raise ValueError(f'{label}不合法')
    text=value+'-01' if len(value)==7 else value
    try:datetime.fromisoformat(text)
    except ValueError:raise ValueError(f'{label}不合法: {value}') from None
    return value

def receive(db, doc):
    source=doc['source']; identity=doc['document_id']; revision=doc['revision']
    received=datetime.fromisoformat(doc['received_at'])
    payload=json.dumps(doc,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    sha=hashlib.sha256(payload.encode('utf-8')).hexdigest()
    key=(source,identity,revision)
    old=db.execute('SELECT sha256 FROM documents WHERE source=? AND document_id=? AND revision=?',key).fetchone()
    if old and old[0]!=sha:raise ValueError('同一来源版本内容冲突')
    records=doc.get('records',[])
    if not isinstance(records,list):raise ValueError('记录必须是列表')
    with db:
        db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?)',(*key,received.isoformat(),payload,sha))
        for row in records:
            if not isinstance(row.get('payload'),dict):raise ValueError('记录正文不合法')
            _validity(row['valid_from'],'记录有效起始')
            valid_to=row.get('valid_to')
            if valid_to is not None:
                _validity(valid_to,'记录有效结束')
                span_to=valid_to+'-01' if len(valid_to)==7 else valid_to
                span_from=row['valid_from']+'-01' if len(row['valid_from'])==7 else row['valid_from']
                if datetime.fromisoformat(span_to)<datetime.fromisoformat(span_from):
                    raise ValueError('记录有效区间不合法')
            rkey=(row['kind'],row['id'],row['revision'])
            existing=db.execute('SELECT payload_json,document_source,document_id,document_revision,valid_to FROM observations WHERE kind=? AND record_id=? AND revision=?',rkey).fetchone()
            body=json.dumps(row['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
            if existing and existing!=(body,*key,valid_to):raise ValueError('同一业务记录版本来源冲突')
            db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?)',(*rkey,*key,row['valid_from'],valid_to,body))
    return sha

def all_records(db,kind,known_at):
    """known_at 之前收到的该类别全部版本, 供方案绑定旧版本复现。"""
    cutoff=datetime.fromisoformat(known_at)
    rows=db.execute("""SELECT o.record_id,o.revision,o.valid_from,o.valid_to,o.payload_json,
      d.source,d.document_id,d.revision,d.received_at,d.sha256
      FROM observations o JOIN documents d ON d.source=o.document_source
       AND d.document_id=o.document_id AND d.revision=o.document_revision
      WHERE o.kind=? ORDER BY o.record_id,d.received_at,o.revision""",(kind,)).fetchall()
    out=[]
    for rid,revision,valid_from,valid_to,payload,source,doc_id,doc_revision,received_at,sha in rows:
        if datetime.fromisoformat(received_at)>cutoff:continue
        out.append({'id':rid,'revision':revision,'valid_from':valid_from,
            'valid_to':valid_to,'payload':json.loads(payload),
            'source':{'name':source,'document':doc_id,
            'revision':doc_revision,'sha256':sha,'received_at':received_at}})
    return out

def records(db,kind,known_at):
    latest={}
    for row in all_records(db,kind,known_at):
        marker=(row['source']['received_at'],row['revision'])
        if row['id'] not in latest or marker>latest[row['id']][0]:
            latest[row['id']]=(marker,row)
    return [item[1] for item in sorted(latest.values(),key=lambda item:item[1]['id'])]
