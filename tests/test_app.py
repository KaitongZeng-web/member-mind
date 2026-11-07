import asyncio,json,sqlite3
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend import domain as d
from backend.mcp_gateway import Gateway
from backend.store import Store
@pytest.fixture
def client(tmp_path):
 with TestClient(create_app(tmp_path/'test.sqlite3')) as c:yield c
def info(c):return c.get('/api/workspace').json()
def post(c,path,body):return c.post(path,json=body,headers={'X-Workspace-Token':info(c)['csrf_token']})
def cmd(c,name,args=None,id=None,revision=None):return post(c,'/api/commands',dict(request_id=id or d.uid('TEST'),expected_revision=info(c)['workspace']['_revision'] if revision is None else revision,command=name,args=args or {}))
def update(c):return cmd(c,'update',dict(id='企业 A',field='renewal',value='已续费',note='模拟业务凭证',source='SIM-RENEW',date=d.TODAY,who='核验员01'))
def test_health_frontend(client):
 assert client.get('/api/health').json()['database']=='SQLite';assert 'bootstrap.js' in client.get('/').text
 assert len(info(client)['workspace']['companies'])==36
 assert [info(client)['workspace']['decisions']['企业 '+x]['state'] for x in 'ABC']==['推荐','排除','待核实']
def test_sqlite_persistence(tmp_path):
 path=tmp_path/'db.sqlite3';a=TestClient(create_app(path));assert update(a).status_code==200
 b=TestClient(create_app(path));assert info(b)['workspace']['companies'][0]['renewal']=='已续费'
 with sqlite3.connect(path) as db:assert db.execute('SELECT count(*) FROM evidence').fetchone()[0]>300
def test_revision_and_idempotency(client):
 key=d.uid('KEY');a=cmd(client,'activation',id=key,revision=0);b=cmd(client,'activation',id=key,revision=0);assert a.json()['output']['request_id']==b.json()['output']['request_id'];assert b.json()['replayed']
 assert cmd(client,'activation',revision=0).status_code==409
 assert cmd(client,'companies',id=key).status_code==409
 assert len(info(client)['workspace']['results'])==1
def test_rollback(client):
 before=info(client)['workspace'];r=cmd(client,'update',dict(id='企业 A',field='renewal',value='无效',note='模拟',source='SIM-TEST',date=d.TODAY,who='核验员01'));assert r.status_code==400;after=info(client)['workspace'];assert before['_revision']==after['_revision'];assert before['evidence']==after['evidence']
def test_three_state_and_unsupported(client):
 rows=cmd(client,'filter',{'text':'NOT 可触达状态=可触达'}).json()['output']['payload']['rows'];assert next(x for x in rows if x['entity_id']=='企业 C')['state']=='待核实'
 for text in ['上海制造业','杭州或制造业','营收=100'] :assert post(client,'/api/parse',{'text':text}).status_code==400
def test_parentheses(client):
 s=info(client)['workspace'];assert d.evaluate(s,s['companies'][0],d.parse('行业=制造业 AND (地区=杭州 OR 地区=宁波) AND NOT 续费情况=已续费'))['value'] is True
 assert d.evaluate(s,s['companies'][0],d.parse('地区=宁波 OR 地区=杭州 AND 行业=新能源'))['value'] is False
 assert not d.date_ok('2026-02-31')
def test_import_accounting_and_preview_authenticity(client):
 p=post(client,'/api/import/preview',{'text':'编号,行业\n企业 ZZ,制造业\n\n非法,制造业\n','mapping':{'0':'id','1':'industry'}}).json();assert p['total']==3;assert p['counts']['新增']==1;assert p['counts']['拒绝']==2
 r=cmd(client,'import_apply',{'batch_id':p['batch_id']});assert r.status_code==200;assert len(r.json()['workspace']['companies'])==37
 assert cmd(client,'import_apply',{'batch_id':'FORGED'}).status_code==400
 assert cmd(client,'import_apply',{'batch_id':p['batch_id']}).status_code==400
def test_conflict_resolve(client):
 x=cmd(client,'candidate',dict(id='企业 A',field='risk',value='已核验失信',note='模拟新资料',source='SIM-TEST',date=d.TODAY)).json()['output'];assert info(client)['workspace']['decisions']['企业 A']['state']=='待核实'
 assert cmd(client,'resolve',dict(issue_id=x['issue_id'],use_new=True,note='核对采用新资料',who='核验员01')).status_code==200
 assert info(client)['workspace']['decisions']['企业 A']['state']=='排除'
def test_old_snapshot_and_readable_export(client):
 r=cmd(client,'activation').json()['output'];assert update(client).status_code==200
 old=client.get('/api/results/'+r['request_id']+'/download').json();assert old['companies'][0]['renewal']=='未续费';assert info(client)['workspace']['decisions']['企业 A']['state']=='排除'
 assert 'SIM-001' in client.get('/api/results/'+r['request_id']+'/download?format=readable').text
 b=cmd(client,'brief',{'id':'企业 A'}).json()['output'];assert '待确认' in b['payload']['text']
def test_meeting_matching_keeps_only_match_results(client):
 m=cmd(client,'meeting_match',{'info':{'name':'制造业交流','theme':'制造业','date':'2026-11-12T14:00','location':'模拟会议室','history':True}}).json()['output']
 assert m['result']['type']=='会议匹配' and 'body' not in m
 assert all('sendable' not in row for row in m['rows'])
 ids=[x['entity_id'] for x in m['rows'] if x['suitable']]
 assert ids
 exported=cmd(client,'meeting_export',{'source_request':m['result']['request_id'],'ids':ids}).json()['output']
 assert exported['type']=='会议匹配名单' and 'body' not in exported['payload']
 assert not info(client)['workspace']['batches']

def test_notification_commands_have_been_removed(client):
 for command in ('notice_create','notice_send','notice_retry','notice_query'):
  response=cmd(client,command,{})
  assert response.status_code==400 and '已移除' in response.json()['detail']
 assert not info(client)['workspace']['batches']

def test_meeting_matching_does_not_require_contact_details(client):
 state=info(client)['workspace'];company=state['companies'][0];company['reachable']=None
 rows=d.activity_rows(state,{'name':'制造业交流','theme':'制造业','history':True})
 assert next(x for x in rows if x['entity_id']==company['id'])['suitable']
 company['industry']=None
 rows=d.activity_rows(state,{'name':'制造业交流','theme':'制造业','history':True})
 assert next(x for x in rows if x['entity_id']==company['id'])['state']=='待核实'

def test_full_action_and_delivery(client):
 p=cmd(client,'pack_create',dict(id='企业 A',goal='需求核实',owner='服务员01',due='2026-11-10')).json()['output'];assert cmd(client,'pack_create',dict(id='企业 B',goal='拜访')).status_code==400
 v=cmd(client,'visit_create',dict(pack_id=p['pack_id'],actual_at='2026-11-05T14:00',original='模拟纪要',needs='模拟活动材料需求')).json()['output'];assert cmd(client,'task_create',dict(visit_id=v['visit_id'],deliverable='材料')).status_code==400
 assert cmd(client,'visit_verify',dict(visit_id=v['visit_id'],reviewer='服务员01',note='模拟核验')).status_code==200
 t=cmd(client,'task_create',dict(visit_id=v['visit_id'],deliverable='材料',owner='服务员01',due='2026-11-10')).json()['output']
 assert cmd(client,'task_complete',dict(task_id=t['task_id'],owner='服务员01',due='2026-11-10',proof='')).status_code==400
 assert cmd(client,'task_complete',dict(task_id=t['task_id'],owner='服务员01',due='2026-11-10',proof='模拟交付凭证')).status_code==200
 assert info(client)['workspace']['companies'][0]['renewal']=='未续费'
def test_csrf_origin(client):
 assert client.post('/api/commands',json={}).status_code==403
 assert client.post('/api/commands',json={},headers={'X-Workspace-Token':info(client)['csrf_token'],'Origin':'https://untrusted.invalid'}).status_code==403
def test_mcp_config_does_not_connect(client):
 cfg=dict(name='预留接口',transport='streamable_http',url='https://example.invalid/mcp',command='',args=[],token_env='FUTURE_MCP_TOKEN',timeout=10,allowed_tools=[],enabled=False)
 r=post(client,'/api/mcp/servers',cfg);assert r.status_code==200;assert not client.get('/api/mcp/servers').json()['calls'];assert not r.json()['token_configured']
 assert post(client,'/api/mcp/servers',{**cfg,'token_env':'actual secret'}).status_code==400

def test_actual_stdio_mcp(tmp_path):
 g=Gateway(Store(tmp_path/'mcp.sqlite3'));cfg=g.save(dict(name='只读本机联调',transport='stdio',command='@python',args=['@project/sample_mcp_server.py'],url='',token_env='',timeout=30,allowed_tools=['get_demo_profile'],enabled=True))
 r=asyncio.run(g.invoke(cfg['id'],d.uid('DISC')));assert r['status']=='success',r;assert r['result']['tools'][0]['name']=='get_demo_profile'
 key=d.uid('CALL');r=asyncio.run(g.invoke(cfg['id'],key,'get_demo_profile',{'entity_id':'企业 A'}));assert r['status']=='success',r;assert (r['result'].get('structuredContent') or json.loads(r['result']['content'][0]['text']))['is_mock']
 assert asyncio.run(g.invoke(cfg['id'],key,'get_demo_profile',{'entity_id':'企业 A'}))==r
 assert asyncio.run(g.invoke(cfg['id'],d.uid('CALL'),'not_allowed',{}))['status']=='failed'
