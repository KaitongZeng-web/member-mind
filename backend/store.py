"""SQLite repository: per-object tables, atomic revision checks and idempotent commands."""
import contextlib,hashlib,json,sqlite3
from pathlib import Path
from . import domain as d
COLLECTIONS={'companies':'id','evidence':'evidence_id','issues':'issue_id','imports':'batch_id','results':'request_id','changes':'event_id','batches':'batch_id','packs':'pack_id','visits':'visit_id','tasks':'task_id','logs':None}
def dump(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),allow_nan=False)
class Conflict(Exception):pass
class Store:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.connect() as c:
   c.execute('PRAGMA journal_mode=WAL')
   c.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
   for name in COLLECTIONS:
    c.execute(f'CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, entity_id TEXT, position INTEGER NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)))')
    c.execute(f'CREATE INDEX IF NOT EXISTS ix_{name}_entity ON {name}(entity_id)')
   c.execute('CREATE TABLE IF NOT EXISTS commands (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, output TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS import_previews (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS mcp_servers (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS mcp_calls (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS chat_turns (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS chat_drafts (id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, payload TEXT NOT NULL)')
   if not c.execute("SELECT 1 FROM metadata WHERE key='revision'").fetchone():self.save(c,d.initial(),0)
 @contextlib.contextmanager
 def connect(self):
  c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=15000')
  try:
   yield c
   c.commit()
  except BaseException:
   c.rollback();raise
  finally:c.close()
 def load(self,c):
  meta={x['key']:json.loads(x['value']) for x in c.execute('SELECT key,value FROM metadata')};revision=meta.pop('revision');s=meta
  for name in COLLECTIONS:s[name]=[json.loads(r['payload']) for r in c.execute(f'SELECT payload FROM {name} ORDER BY position')]
  s['_revision']=revision;return s
 def read(self):
  with self.connect() as c:return self.load(c)
 def save(self,c,s,revision):
  for k,v in s.items():
   if k not in COLLECTIONS and not k.startswith('_'):c.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(k,dump(v)))
  c.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('revision',dump(revision)))
  for name,key in COLLECTIONS.items():
   c.execute(f'DELETE FROM {name}')
   for i,obj in enumerate(s[name]):c.execute(f'INSERT INTO {name} VALUES(?,?,?,?)',(str(obj[key]) if key else str(i),obj.get('entity_id') or obj.get('id'),i,dump(obj)))
 def execute(self,request_id,expected,command,args,callback):
  fingerprint=hashlib.sha256(dump({'command':command,'args':args}).encode()).hexdigest()
  with self.connect() as c:
   c.execute('BEGIN IMMEDIATE');prior=c.execute('SELECT * FROM commands WHERE request_id=?',(request_id,)).fetchone()
   if prior:
    if prior['fingerprint']!=fingerprint:raise Conflict('请求编号重复但参数不同')
    return self.load(c),json.loads(prior['output']),True
   s=self.load(c)
   if expected!=s['_revision']:raise Conflict('其他操作已更新数据，已重新读取；请核对后再次操作')
   output=callback(s,c);s['logs'].insert(0,dict(time=d.now(),text='Python执行 '+command,request_id=request_id))
   self.save(c,s,expected+1);c.execute('INSERT INTO commands VALUES(?,?,?)',(request_id,fingerprint,dump(output)))
   s['_revision']=expected+1;return s,output,False
 def preview(self,text,mapping):
  with self.connect() as c:
   b=d.preflight(self.load(c),text,mapping);c.execute('INSERT INTO import_previews VALUES(?,?)',(b['batch_id'],dump(b)));return b
 def backup(self,destination):
  with self.connect() as source,sqlite3.connect(destination) as target:source.backup(target)
