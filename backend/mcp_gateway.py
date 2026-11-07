"""Configurable MCP client; no connection or tool execution on configuration save."""
import asyncio,json,os,re,sys
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlsplit
from pathlib import Path
import httpx
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from .credentials import get_secret
from . import domain as d
from .store import dump,Conflict

def validate(config):
 allowed={'id','name','transport','url','command','args','token_env','timeout','allowed_tools','enabled'}
 d.require(set(config)<=allowed,'配置含未知字段；密钥只能通过环境变量引用');cfg={k:config.get(k) for k in allowed};cfg['id']=cfg['id'] or d.uid('MCP');d.require(re.fullmatch(r'[A-Za-z0-9_-]{1,80}',cfg['id'] or ''),'服务器ID无效');d.require(isinstance(cfg['name'],str) and 0<len(cfg['name'])<=100,'服务器名称必填');d.require(cfg['transport'] in ('streamable_http','stdio'),'只支持Streamable HTTP和stdio');d.require(type(cfg['enabled']) is bool,'启用状态无效');d.require(type(cfg['timeout']) in (int,float) and 1<=cfg['timeout']<=120,'超时为1至120秒');d.require(isinstance(cfg['allowed_tools'],list) and all(isinstance(x,str) and len(x)<=150 for x in cfg['allowed_tools']),'工具白名单应为名称列表');cfg['token_env']=cfg['token_env'] or '';d.require(not cfg['token_env'] or re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}',cfg['token_env']),'鉴权填环境变量名，不填实际密钥')
 if cfg['transport']=='streamable_http':
  u=urlsplit(cfg['url'] or '');d.require(u.scheme in ('http','https') and u.hostname and not u.username and not u.password and not u.query and not u.fragment,'输入无凭据和查询参数的HTTP/HTTPS MCP地址');d.require(u.scheme=='https' or u.hostname in ('127.0.0.1','localhost','::1'),'远程MCP应使用HTTPS');cfg['command']='';cfg['args']=[]
 else:
  d.require(isinstance(cfg['command'],str) and bool(cfg['command'].strip()) and len(cfg['command'])<=500,'stdio需要启动程序');d.require(isinstance(cfg['args'],list) and all(isinstance(x,str) and len(x)<=2000 for x in cfg['args']),'args必须为字符串数组');cfg['url']='';d.require(not cfg['token_env'],'stdio鉴权通过子进程本机环境配置')
 return cfg

def public_config(cfg):return {**cfg,'token_configured':bool(cfg.get('token_env') and get_secret(cfg['token_env']))}

@asynccontextmanager
async def session(cfg):
 d.require(cfg['enabled'],'服务器未启用，不会建立连接')
 async def enter():pass
 if cfg['transport']=='stdio':
  command=sys.executable if cfg['command']=='@python' else cfg['command'];args=[str(d.BASE/x.removeprefix('@project/')) if x.startswith('@project/') else x for x in cfg['args']]
  params=StdioServerParameters(command=command,args=args,env={**os.environ,'PYTHONIOENCODING':'utf-8'},cwd=str(d.BASE))
  with open(os.devnull,'w') as errors:
   async with stdio_client(params,errlog=errors) as (read,write):
    async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=cfg['timeout'])) as client:
     await client.initialize();yield client
 else:
  headers={};env=cfg.get('token_env')
  if env:
   d.require(bool(get_secret(env)),'鉴权环境变量未配置');headers['Authorization']='Bearer '+get_secret(env)
  async with httpx.AsyncClient(headers=headers,timeout=cfg['timeout'],follow_redirects=False) as http:
   async with streamable_http_client(cfg['url'],http_client=http) as (read,write,_):
    async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=cfg['timeout'])) as client:
     await client.initialize();yield client

async def perform(cfg,tool=None,arguments=None):
 if tool:d.require(tool in cfg['allowed_tools'],'此工具未加入服务器白名单')
 async with session(cfg) as client:
  if tool:
   d.require(tool in cfg['allowed_tools'],'此工具未加入服务器白名单');listed=await client.list_tools();d.require(any(t.name==tool for t in listed.tools),'服务器没有提供此工具');res=await client.call_tool(tool,arguments or {});return res.model_dump(mode='json',by_alias=True,exclude_none=True)
  tools=[];cursor=None
  for _ in range(20):
   r=await client.list_tools(cursor=cursor);tools.extend(t.model_dump(mode='json',by_alias=True,exclude_none=True) for t in r.tools);cursor=r.nextCursor
   if not cursor:break
  return {'tools':tools,'truncated':bool(cursor),'transport':cfg['transport'],'status':'connected'}

class Gateway:
 def __init__(self,store):self.store=store
 def configs(self):
  with self.store.connect() as c:return [public_config(json.loads(x['payload'])) for x in c.execute('SELECT payload FROM mcp_servers')]
 def save(self,payload):
  cfg=validate(payload)
  with self.store.connect() as c:c.execute('INSERT OR REPLACE INTO mcp_servers VALUES(?,?)',(cfg['id'],dump(cfg)))
  return public_config(cfg)
 def config(self,id):
  with self.store.connect() as c:r=c.execute('SELECT payload FROM mcp_servers WHERE id=?',(id,)).fetchone()
  d.require(r is not None,'MCP配置不存在');return json.loads(r['payload'])
 def calls(self):
  with self.store.connect() as c:return [json.loads(x['payload']) for x in c.execute('SELECT payload FROM mcp_calls ORDER BY rowid DESC LIMIT 50')]
 async def invoke(self,id,request_id,tool=None,arguments=None):
  cfg=self.config(id);fingerprint=dump(dict(server=id,tool=tool,arguments=arguments or {}));import hashlib
  fingerprint=hashlib.sha256(fingerprint.encode()).hexdigest()
  with self.store.connect() as c:
   c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM mcp_calls WHERE id=?',(request_id,)).fetchone()
   if r:
    if r['fingerprint']!=fingerprint:raise Conflict('MCP请求编号与参数不一致')
    return json.loads(r['payload'])
   record=dict(id=request_id,server_id=id,tool=tool or 'tools/list',status='pending',started_at=d.now(),mode='real_mcp_protocol',message='连接尚未完成；不代表业务成功')
   c.execute('INSERT INTO mcp_calls VALUES(?,?,?)',(request_id,fingerprint,dump(record)))
  try:
   answer=await asyncio.wait_for(perform(cfg,tool,arguments),timeout=cfg['timeout']);encoded=dump(answer);d.require(len(encoded)<=2*1024*1024,'MCP结果超过2MB限制');record.update(status='failed' if answer.get('isError') else 'success',result=answer,message='MCP工具返回错误' if answer.get('isError') else '实际MCP协议调用完成；未自动更新企业事实')
  except ValueError as e:record.update(status='failed',message=str(e))
  except Exception as e:record.update(status='unknown' if tool else 'failed',message='MCP连接/调用未确认：'+type(e).__name__+'。请检查配置、授权及服务器状态；不自动重试。')
  record['finished_at']=d.now()
  with self.store.connect() as c:c.execute('UPDATE mcp_calls SET payload=? WHERE id=?',(dump(record),request_id))
  return record
