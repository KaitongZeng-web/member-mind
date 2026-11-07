from pathlib import Path
import csv,io,json,os,secrets
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import FileResponse,Response,JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,ConfigDict,Field
from . import domain as d,service
from .store import Store,Conflict,dump
from .mcp_gateway import Gateway
from .chat_import import ChatImporter, SERVICES, QUERY_PROMPT, STRUCTURE_PROMPT, export_csv
from . import credentials
from .company_search import PLAN_PROMPT,RANK_PROMPT

class Command(BaseModel):
 model_config=ConfigDict(extra='forbid')
 request_id:str=Field(min_length=8,max_length=120)
 expected_revision:int=Field(ge=0)
 command:str=Field(max_length=60)
 args:dict=Field(default_factory=dict)

def create_app(db_path=None):
 app=FastAPI(title='企服智台 Python API',version='3.0.0');db=Store(db_path or os.getenv('APP_DB_PATH',str(d.BASE/'data/workspace.sqlite3')));gateway=Gateway(db);app.add_middleware(TrustedHostMiddleware,allowed_hosts=os.getenv('APP_ALLOWED_HOSTS','127.0.0.1,localhost,testserver').split(','));app.state.db=db;app.state.gateway=gateway;app.state.csrf=secrets.token_urlsafe(32)
 chat=ChatImporter(db,gateway);app.state.chat=chat
 @app.get('/api/chat/settings')
 def chat_settings():return {**credentials.status(),'services':[dict(id=k,name=v[0]) for k,v in SERVICES.items()],'sessions':chat.recent_sessions()}
 @app.post('/api/chat/settings')
 def chat_settings_save(payload:dict):return credentials.save_settings(payload)
 @app.get('/api/chat/prompts')
 def chat_prompts():return {'query':QUERY_PROMPT,'structure':STRUCTURE_PROMPT,'planning':PLAN_PROMPT,'ranking':RANK_PROMPT,'fields':d.FIELDS,'enums':d.ENUMS}
 @app.get('/api/chat/sessions/{id}')
 def chat_history(id:str):return {'turns':chat.history(id)}
 @app.post('/api/chat/turns')
 async def chat_begin(payload:dict):return chat.begin(payload)
 @app.get('/api/chat/turns/{id}')
 def chat_turn(id:str):return chat.get(id)
 @app.get('/api/chat/turns/{id}/download')
 def chat_download(id:str,format:str='json'):
  turn=chat.get(id)
  if format=='table':return Response(export_csv(turn['drafts'],human=True),media_type='text/csv;charset=utf-8',headers={'Content-Disposition':'attachment; filename="companies.csv"'})
  if format=='csv':return Response(export_csv(turn['drafts']),media_type='text/csv;charset=utf-8',headers={'Content-Disposition':'attachment; filename="qcc-candidates.csv"'})
  return Response(dump({'schema':3,'status':'operator_review_required','fields':d.FIELDS,'turn':turn}),media_type='application/json',headers={'Content-Disposition':'attachment; filename="qcc-candidates.json"'})
 @app.middleware('http')
 async def guard(request:Request,call_next):
  if request.method not in ('GET','HEAD','OPTIONS'):
   origin=request.headers.get('origin');allowed={str(request.base_url).rstrip('/'),*os.getenv('APP_ALLOWED_ORIGINS','').split(',')}
   if origin and origin not in allowed:return JSONResponse({'detail':'请求来源不允许'},403)
   if request.headers.get('x-workspace-token')!=app.state.csrf:return JSONResponse({'detail':'刷新页面获取当前请求凭证'},403)
   if 'application/json' not in request.headers.get('content-type',''):return JSONResponse({'detail':'仅接受JSON请求'},415)
   if int(request.headers.get('content-length','0'))>8*1024*1024:return JSONResponse({'detail':'请求超过8MB'},413)
  response=await call_next(request);response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin';response.headers['Cache-Control']='no-store';return response
 @app.exception_handler(ValueError)
 async def value_error(request,exc):return JSONResponse({'detail':str(exc)},400)
 @app.exception_handler(Conflict)
 async def conflict(request,exc):return JSONResponse({'detail':str(exc),'workspace':public_workspace(db.read())},409)
 def public_workspace(s):
  s['decisions']={c['id']:d.decision(s,c) for c in s['companies']};return s
 @app.get('/api/health')
 def health():return {'status':'ok','backend':'Python/FastAPI','database':'SQLite','revision':db.read()['_revision'],'mcp':'configurable_not_connected_by_default'}
 @app.get('/api/workspace')
 def workspace():return {'workspace':public_workspace(db.read()),'csrf_token':app.state.csrf,'fields':d.FIELDS,'enums':d.ENUMS}
 @app.post('/api/parse')
 def parse(payload:dict):return {'tree':d.parse(payload.get('text',''))}
 @app.post('/api/import/preview')
 def preview(payload:dict):return db.preview(payload.get('text',''),payload.get('mapping',{}))
 @app.post('/api/commands')
 def command(payload:Command):
  s,out,replayed=db.execute(payload.request_id,payload.expected_revision,payload.command,payload.args,lambda s,c:service.run(s,c,payload.command,payload.args));return {'workspace':public_workspace(s),'output':out,'replayed':replayed}
 @app.get('/api/results/{id}/download')
 def download(id:str,format:str='json'):
  s=db.read();r=service.item(s['results'],'request_id',id)
  if format=='report':
   from .presentation import result_report
   return Response(result_report(r),media_type='text/plain;charset=utf-8',headers={'Content-Disposition':'attachment; filename="report.txt"'})
  if format=='json':return Response(dump(r),media_type='application/json',headers={'Content-Disposition':'attachment; filename="result.json"'})
  if r['type']=='拜访简报':return Response(r['payload']['text']+'\n\n信息依据\n'+dump(r['evidence']),media_type='text/plain;charset=utf-8',headers={'Content-Disposition':'attachment; filename="brief.txt"'})
  f=io.StringIO();w=csv.writer(f);w.writerow(['请求编号','模拟模式','数据版本','规则版本','企业编号','结果与理由','证据与日期'])
  for x in r['payload'].get('rows',r['payload'].get('companies',[])):
   id=x.get('entity_id',x.get('id'));w.writerow([r['request_id'],r['mode'],r['data_version'],r['rules_version'],id,dump(x),dump([e for e in r['evidence'] if e['entity_id']==id])])
  return Response('\ufeff'+f.getvalue(),media_type='text/csv;charset=utf-8',headers={'Content-Disposition':'attachment; filename="result.csv"'})
 @app.get('/api/backup')
 def backup():return Response(dump(db.read()),media_type='application/json',headers={'Content-Disposition':'attachment; filename="workspace.json"'})
 @app.get('/api/mcp/servers')
 def servers():return {'servers':gateway.configs(),'calls':gateway.calls()}
 @app.post('/api/mcp/servers')
 def server_save(payload:dict):return gateway.save(payload)
 @app.post('/api/mcp/servers/{id}/discover')
 async def discover(id:str,payload:dict):return await gateway.invoke(id,service.required(payload,'request_id'))
 @app.post('/api/mcp/servers/{id}/call')
 async def mcp_call(id:str,payload:dict):
  d.require(payload.get('confirmed') is True,'工具调用需明确确认');return await gateway.invoke(id,service.required(payload,'request_id'),service.required(payload,'tool'),payload.get('arguments',{}))
 app.mount('/',StaticFiles(directory=d.BASE/'frontend',html=True),name='frontend');return app
app=create_app()
