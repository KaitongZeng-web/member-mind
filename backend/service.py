import copy,json,re
from . import domain as d

def required(a,key):
 v=a.get(key);d.require(v is not None and v!='',key+'必填');return v

def role(v):d.require(isinstance(v,str) and re.fullmatch(r'(服务员|核验员|负责人)\d{2}',v),'使用服务员01、核验员01或负责人01等匿名角色');return v

def item(items,key,id):
 x=next((x for x in items if x[key]==id),None);d.require(x is not None,'记录不存在');return x

def check_version(s,x):d.require(x['data_version']==s['version'] and x['rules_version']==s['rulesVersion'],'资料或规则已变化，请复核并重新确认')

def run(s,c,name,a):
 if name in ('notice_create','notice_send','notice_retry','notice_query'):
  raise ValueError('通知和批次功能已移除，请使用会议匹配查看企业名单')
 if name=='chat_import_apply':
  from .chat_import import apply_drafts
  return apply_drafts(s,c,a)
 if name=='import_apply':
  r=c.execute('SELECT payload FROM import_previews WHERE id=?',(required(a,'batch_id'),)).fetchone();d.require(r is not None,'先执行服务端预检');d.apply_import(s,json.loads(r['payload']));return {}
 if name=='candidate':return d.candidate(s,d.find(s,required(a,'id')),required(a,'field'),a.get('value'),required(a,'note'),required(a,'source'),required(a,'date'))
 if name=='resolve':d.resolve(s,required(a,'issue_id'),bool(a.get('use_new')),required(a,'note'),role(required(a,'who')));return {}
 if name=='update':
  company=d.find(s,required(a,'id'));k=required(a,'field');d.require(not any(x['entity_id']==company['id'] and x['field']==k and x['status']=='待核对' for x in s['issues']),'请先解决字段冲突');return d.commit_field(s,company,k,a.get('value'),required(a,'note'),required(a,'source'),required(a,'date'),role(required(a,'who')))
 if name=='rules':r=required(a,'rules');d.validate_rules(r);old=copy.deepcopy(s['rules']);s['rules']=r;s['rulesVersion']+=1;d.change(s,'规则','rules',old,r,'人工确认演示规则版本');return {}
 if name=='filter':
  text=required(a,'text');tree=d.parse(text);rows=[]
  for company in s['companies']:
   r=d.evaluate(s,company,tree);rows.append(dict(entity_id=company['id'],state='待核实' if r['value'] is None else '匹配' if r['value'] else '排除',reasons=r['reasons'],evidence_ids=d.refs(s,company)))
  return d.result(s,'组合筛选',dict(query=text,tree=tree,rows=rows))
 if name=='brief':
  from .presentation import build_brief,brief_text
  company=d.find(s,required(a,'id'));document=build_brief(s,company,a.get('purpose',''));return d.result(s,'拜访简报',dict(entity_id=company['id'],purpose=a.get('purpose',''),document=document,text=brief_text(document)))
 if name=='activation':return d.result(s,'会员盘活',dict(rows=[d.decision(s,x) for x in s['companies']]))
 if name=='companies':
  companies=[]
  for company in s['companies']:
   company=copy.deepcopy(company);company['tags']=[dict(name=label,value=d.value(s,company,k),type='原始业务事实' if k in ('member','renewal','reachable','industry','region') else '模拟公开资料事实',evidence_ids=d.refs(s,company,k),verification='冲突待核对' if d.value(s,company,k) is None and company.get(k) else '待核实' if d.unknown(company.get(k)) else '已核对',rules_version=s['rulesVersion']) for k,label in d.FIELDS.items() if k not in ('id','source','sourceDate')];companies.append(company)
  return d.result(s,'企业标签表',dict(companies=companies))
 if name=='meeting_match':
  info=required(a,'info');d.require(info.get('name') and info.get('theme'),'会议名称和主题必填');info={k:info.get(k,'') for k in ('name','theme','date','location','history')};info['history']=bool(info['history']);rows=d.activity_rows(s,info);r=d.result(s,'会议匹配',dict(info=info,rows=rows));return dict(**info,info=info,rows=rows,result=r,data_version=s['version'],rules_version=s['rulesVersion'])
 if name=='meeting_export':
  r=item(s['results'],'request_id',required(a,'source_request'));d.require(r['type']=='会议匹配','请选择会议匹配记录');check_version(s,r);rows=d.activity_rows(s,r['payload']['info']);ids=required(a,'ids');d.require(isinstance(ids,list) and all(isinstance(x,str) for x in ids) and len(ids)==len(set(ids)),'名单不可重复');d.require(all(x in {row['entity_id'] for row in rows if row['suitable']} for x in ids),'名单中存在尚不符合会议条件的企业');return d.result(s,'会议匹配名单',dict(info=r['payload']['info'],rows=[row for row in rows if row['entity_id'] in ids]))
 if name=='pack_create':
  company=d.find(s,required(a,'id'));dec=d.decision(s,company);d.require(dec['state']!='排除','企业不符合行动准入');goal=required(a,'goal');owner=a.get('owner') or None;due=a.get('due') or None
  if owner:role(owner)
  if due:d.require(d.date_ok(due),'截止日期无效')
  d.require(not any(p['entity_id']==company['id'] and p['goal'].removeprefix('补查：')==goal and p['status'] not in ('完成','关闭','培育') for p in s['packs']),'已有相同目标未完成行动')
  p=dict(pack_id=d.uid('PACK'),entity_id=company['id'],source_request=a.get('source_request'),data_version=s['version'],rules_version=s['rulesVersion'],mode='mock',goal=('补查：' if dec['state']=='待核实' else '')+goal,owner=owner,due=due,questions='实际需求、可用服务和缺失资料是什么？',next='先核实资料' if dec['state']=='待核实' else '核实需求并联系预约',acceptance='记录核验或沟通证据及下一步',evidence_ids=dec['evidence_ids'],status='待预约' if owner and due else '草案',kind='补查' if dec['state']=='待核实' else '服务',appointment='未预约');s['packs'].insert(0,p);return p
 if name.startswith('pack_'):
  p=item(s['packs'],'pack_id',required(a,'pack_id'))
  if name=='pack_refresh':
   dec=d.decision(s,d.find(s,p['entity_id']),scenario='action');d.require(dec['state']=='推荐' or p['kind']=='补查','当前资料不满足服务准入');p.update(data_version=s['version'],rules_version=s['rulesVersion']);return p
  if name=='pack_save':
   check_version(s,p);d.require(p['status'] not in ('完成','关闭','培育'),'行动已结案，不能直接覆盖状态');owner=a.get('owner') or None;due=a.get('due') or None;appointment=required(a,'appointment');d.require(appointment in ('未预约','已联系待回复','客户确认','改期','拒绝联系'),'预约状态无效')
   if owner:role(owner)
   if due:d.require(d.date_ok(due),'日期无效')
   if appointment in ('客户确认','改期','拒绝联系'):required(a,'proof')
   if appointment=='客户确认':d.require(p['kind']!='补查','补查任务不能确认服务预约');required(a,'when')
   p.update(owner=owner,due=due,appointment=appointment,appointment_proof=a.get('proof',''),when=a.get('when'),status='草案' if not owner or not due else '关闭' if appointment=='拒绝联系' else '已预约' if appointment=='客户确认' else '待预约');return p
  if name=='pack_finish':
   status=required(a,'status');d.require(status in ('完成','培育','关闭'),'结果状态无效');proof=required(a,'proof')
   if status=='培育':required(a,'trigger')
   tasks=[t for t in s['tasks'] if t['pack_id']==p['pack_id']]
   if status=='完成':d.require(tasks and all(t['status']=='完成' for t in tasks),'需有完成证据的任务，且无未完成工作')
   p.update(status=status,outcome_proof=proof,next_trigger=a.get('trigger'));return p
 if name=='visit_create':
  p=item(s['packs'],'pack_id',required(a,'pack_id'));check_version(s,p);d.require(p['status'] not in ('关闭','完成','培育'),'行动已结案');actual=required(a,'actual_at');d.require('T' in actual,'实际时间格式无效');v=dict(visit_id=d.uid('VISIT'),pack_id=p['pack_id'],entity_id=p['entity_id'],actual_at=actual+'+08:00',roles='服务员 / 企业代表（匿名）',original=required(a,'original'),needs=a.get('needs',''),promises=a.get('promises',''),open=a.get('open',''),mode='mock',status='待核对',created_at=d.now());s['visits'].insert(0,v);p['status']='已拜访待核对';return v
 if name=='visit_verify':
  v=item(s['visits'],'visit_id',required(a,'visit_id'));d.require(v['status']=='待核对','纪要已处理');v.update(reviewer=role(required(a,'reviewer')),verification_note=required(a,'note'),verified_at=d.now(),status='已核对');item(s['packs'],'pack_id',v['pack_id'])['status']='跟进中';return v
 if name=='task_create':
  v=item(s['visits'],'visit_id',required(a,'visit_id'));d.require(v['status']=='已核对' and v['needs'],'纪要须核对且有已确认需求');owner=a.get('owner') or None;due=a.get('due') or None
  if owner:role(owner)
  if due:d.require(d.date_ok(due),'日期无效')
  t=dict(task_id=d.uid('TASK'),entity_id=v['entity_id'],visit_id=v['visit_id'],pack_id=v['pack_id'],owner=owner,due=due,deliverable=required(a,'deliverable'),dependency=a.get('dependency',''),next_trigger=a.get('next_trigger') or '交付后人工复访',status='跟进中' if owner and due else '草案',mode='mock',completion_evidence=None);s['tasks'].insert(0,t);return t
 if name=='task_complete':
  t=item(s['tasks'],'task_id',required(a,'task_id'));d.require(t['status']!='完成','任务已完成');owner=role(required(a,'owner'));due=required(a,'due');d.require(d.date_ok(due),'日期无效');proof=required(a,'proof');t.update(owner=owner,due=due,status='完成',completion_evidence=proof,completed_at=d.now());return t
 if name=='reset':
  d.require(a.get('confirmed') is True,'恢复数据需明确确认');revision=s['_revision'];s.clear();s.update(d.initial());s['_revision']=revision;return {}
 raise ValueError('未知业务命令：'+name)
