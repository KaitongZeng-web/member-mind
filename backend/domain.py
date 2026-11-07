"""Authoritative Python domain rules. All writes run inside SQLite transactions."""
from __future__ import annotations
import copy,csv,io,json,re,uuid
from datetime import datetime,date,timezone
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
FIXTURE=json.loads((BASE/'config/seed.json').read_text(encoding='utf-8-sig'))
FIELDS=FIXTURE['fields']; ENUMS=FIXTURE['enums']; TODAY='2026-10-31'
FIELDS.update(companyName='企业名称',creditCode='统一社会信用代码')
FIELDS.update(mainBusiness='主营业务',businessScope='经营范围',registeredAddress='注册地址',
              establishedDate='成立日期',legalRepresentative='法定代表人',registeredCapital='注册资本',
              companyScale='企业规模',companyOverview='企业简介')
def uid(p): return p+'-'+str(uuid.uuid4())
def now(): return datetime.now(timezone.utc).isoformat()
def unknown(v): return v is None or v in ('','待核实')
def date_ok(v):
 try: return isinstance(v,str) and date.fromisoformat(v).isoformat()==v
 except (ValueError,TypeError): return False
def require(cond,message):
 if not cond: raise ValueError(message)
def valid(k,v):
 require(k in FIELDS,'不支持的字段')
 if unknown(v):return
 require(isinstance(v,str) and len(v)<=2000,'字段必须是限定长度的文本')
 if k=='id':require(bool(re.fullmatch(r'企业 [A-Z]+\d*|(?:QCC|FILE)-[A-F0-9]{12}',v)),'企业编号无效')
 if k=='creditCode':require(bool(re.fullmatch(r'[0-9A-HJ-NPQRTUWXY]{18}',v)),'统一社会信用代码应为18位有效字符')
 if k in ENUMS and k not in ('industry','region'):require(v in ENUMS[k],FIELDS[k]+'不在业务字典中')
 if k in ('industry','region'):require(len(v)<=120,'行业或地区内容过长')
 if k in ('sourceDate','qualificationUntil','establishedDate'):require(date_ok(v),'日期必须为有效YYYY-MM-DD')
 if k=='source':require(bool(re.fullmatch(r'(SIM|QCC|FILE)-[A-Z0-9-]+',v)),'资料来源标识无效')
 if k=='companyName':require(1<=len(v)<=200,'企业名称过长')
def initial():
 s=copy.deepcopy(FIXTURE['state'])
 for e in s['evidence']:e['evidence_id']=uid('EV');e['obtained_at']=now()
 s['results']=[];s['logs']=[];s['schema']=3
 return s

def find(s,id):
 c=next((x for x in s['companies'] if x['id']==id),None);require(c is not None,'企业不存在');return c

def value(s,c,k):return None if any(x['entity_id']==c['id'] and x['field']==k and x['status']=='待核对' for x in s['issues']) else c.get(k)
def refs(s,c,k=None):return [e['evidence_id'] for e in s['evidence'] if e['entity_id']==c['id'] and e['verification']=='已核对' and (k is None or (e['field']==k and e['value']==c.get(k)))]
def change(s,entity,k,old,new,reason,ids=None):
 s['version']+=1
 s['changes'].insert(0,dict(event_id=uid('CH'),entity_id=entity,field=k,old_value=copy.deepcopy(old),new_value=copy.deepcopy(new),reason=reason,evidence_ids=ids or [],operator='核验员01',time=now(),data_version=s['version'],affected_outputs=[r['request_id'] for r in s['results']]))
def evidence(s,c,k,v,note,ref,doc,status='已核对',who='核验员01'):
 valid(k,v);valid('source',ref);require(ref and note and (date_ok(doc) or (ref.startswith(('QCC-','FILE-')) and doc is None)),'证据来源、片段及有效资料日期必填')
 real=ref.startswith(('QCC-','FILE-'))
 e=dict(evidence_id=uid('EV'),entity_id=c['id'],field=k,value=None if unknown(v) else v,source_type='操作员上传的企业文件' if ref.startswith('FILE-') else ('操作员提供的业务资料' if k in ('renewal','reachable','member') else '企查查MCP公开资料') if real else '模拟业务记录' if k in ('renewal','reachable','member') else '模拟公开信息',source_ref=ref,excerpt=note,document_date=doc,obtained_at=now(),verification=status,verified_by=who if status=='已核对' else None,is_mock=not real)
 s['evidence'].append(e);return e

def commit_field(s,c,k,v,note,ref='SIM-MANUAL',doc=TODAY,who='核验员01'):
 require(k not in ('id','source','sourceDate'),'不可直接修改身份或来源字段');e=evidence(s,c,k,v,note,ref,doc,who=who);old=c.get(k);c[k]=e['value'];c['updatedAt']=TODAY;change(s,c['id'],k,old,c[k],note,[e['evidence_id']]);return e

def candidate(s,c,k,v,note,ref,doc):
 require(k not in ('id','source','sourceDate'),'不支持的候选字段');e=evidence(s,c,k,v,note,ref,doc,'待核对')
 x=dict(issue_id=uid('IS'),entity_id=c['id'],field=k,old_value=c.get(k),new_value=e['value'],evidence_id=e['evidence_id'],status='待核对',reason='缺失字段待补充' if unknown(c.get(k)) else '多来源值不一致',blocking=True,remedy='对照来源后采用候选或保留旧值',created_at=now());s['issues'].append(x);change(s,c['id'],k,c.get(k),c.get(k),'新增候选证据，挂起依赖判断',[e['evidence_id']]);return x

def resolve(s,id,use,note,who):
 x=next((x for x in s['issues'] if x['issue_id']==id),None);require(x and x['status']=='待核对','问题不存在或已处理');require(note and who,'核对角色和依据必填');c=find(s,x['entity_id']);e=next(e for e in s['evidence'] if e['evidence_id']==x['evidence_id'])
 require(not use or c.get(x['field'])==x['old_value'],'字段已变化，重新核对来源')
 x.update(status='已采用候选' if use else '已保留旧值',resolved_at=now(),reviewer=who,resolution=note);e.update(verification='已核对' if use else '未采纳',verified_by=who)
 if use and not e.get('is_mock',True):
  old=c.get(x['field']);c[x['field']]=x['new_value'];c['updatedAt']=now()[:10];change(s,c['id'],x['field'],old,x['new_value'],note,[e['evidence_id']])
 elif use:commit_field(s,c,x['field'],x['new_value'],note,e['source_ref'],e['document_date'],who)
 else:change(s,c['id'],x['field'],c.get(x['field']),c.get(x['field']),note,[e['evidence_id']])

def preflight(s,text,mapping):
 from .file_import import preflight as preview_file
 return preview_file(s,text,mapping)

def apply_import(s,b):
 from .file_import import apply_import as apply_file
 return apply_file(s,b)

class Parser:
 def __init__(self,text):
  self.tokens=re.findall(r'AND\b|OR\b|NOT\b|并且|或者|且|或|非|[()（）]|!=|=|[^\s()（）=!]+',text,re.I);self.i=0
  self.tokens=[{'并且':'AND','且':'AND','或者':'OR','或':'OR','非':'NOT','（':'(', '）':')'}.get(t,t.upper() if t.upper() in ('AND','OR','NOT') else t) for t in self.tokens]
 def peek(self):return self.tokens[self.i] if self.i<len(self.tokens) else None
 def take(self):r=self.peek();self.i+=1;return r
 def primary(self):
  if self.peek()=='NOT':self.take();return dict(op='not',child=self.primary())
  if self.peek()=='(':
   self.take();n=self.either();require(self.take()==')','括号不匹配');return n
  label=self.take();k=next((k for k,v in FIELDS.items() if v==label),label);require(k in ENUMS,'不支持的筛选字段');op=self.take();require(op in ('=','!='),'条件格式应为 字段=值');v=self.take();require(v in ENUMS[k],'条件值不在字典中');n=dict(field=k,value=v);return dict(op='not',child=n) if op=='!=' else n
 def both(self):
  n=self.primary()
  while self.peek()=='AND':self.take();n=dict(op='and',children=[n,self.primary()])
  return n
 def either(self):
  n=self.both()
  while self.peek()=='OR':self.take();n=dict(op='or',children=[n,self.both()])
  return n
 def parse(self):n=self.either();require(self.i==len(self.tokens),'存在未解析条件');return n

def parse(text):
 text=text.strip();require(text,'请输入条件')
 if '=' in text:return Parser(text).parse()
 words={v:k for k,vs in ENUMS.items() for v in vs};words.update({'高新':'qualification','排除失信':'risk','无失信':'risk'})
 hits=list(re.finditer('|'.join(sorted(map(re.escape,words),key=len,reverse=True)),text));remain=text
 for v in sorted(words,key=len,reverse=True):remain=remain.replace(v,'')
 remain=re.sub(r'企业|筛选|寻找|地区|行业|并且|或者|以及|的|且|或|和|中|[，、。\s]','',remain);require(not remain,'存在未支持条件，请改用完整条件式')
 for a,b in zip(hits,hits[1:]):require(not('或' in text[a.end():b.start()] and words[a[0]]!=words[b[0]]),'跨字段或存在歧义，请用OR条件式')
 groups={}
 for h in hits:
  v=h[0];k=words[v];v='高新技术企业' if v=='高新' else '已核验未见规则内风险' if v in ('排除失信','无失信') else v;groups.setdefault(k,[]).append(dict(field=k,value=v))
 nodes=[vs[0] if len(vs)==1 else dict(op='or',children=vs) for vs in groups.values()];require(nodes,'没有可执行条件');return dict(op='and',children=nodes)

def evaluate(s,c,node):
 if node.get('op'):
  rs=[evaluate(s,c,n) for n in node.get('children',[node.get('child')])];vs=[r['value'] for r in rs];op=node['op'];v=(None if vs[0] is None else not vs[0]) if op=='not' else (False if False in vs else None if None in vs else True) if op=='and' else (True if True in vs else None if None in vs else False)
  return dict(value=v,reasons=[f'{op.upper()} => '+('待核实' if v is None else '通过' if v else '不符合')]+[a for r in rs for a in r['reasons']])
 k=node['field'];actual=value(s,c,k);v=None if unknown(actual) else actual==node['value'];why=FIELDS[k]+'='+node['value']+'；实际 '+(actual or '待核实')
 if k=='qualification' and v:
  until=value(s,c,'qualificationUntil');v=None if not date_ok(until) else until>=TODAY;why+='；有效期 '+(until or '待核实')
 return dict(value=v,reasons=[why])

def gate(s,c):
 no=[];review=[]
 for k,key,rule in [('risk','riskExcluded','R-RISK'),('operation','operationExcluded','R-OPS')]:
  v=value(s,c,k)
  if unknown(v):review.append(FIELDS[k]+'待核实')
  elif v in s['rules'][key]:no.append(FIELDS[k]+'为'+v+'，暂不符合准入条件')
 return no,review

def decision(s,c,scenario='activation'):
 no,review=gate(s,c)
 if scenario=='activation':
  v=value(s,c,'renewal')
  if unknown(v):review.append('续费情况尚未核实')
  elif v!='未续费':no.append('企业已续费，不属于本轮盘活范围')
 if s['rules']['requireReach']:
  v=value(s,c,'reachable')
  if unknown(v):review.append('尚未确认能否联系到企业')
  elif v!='可触达':no.append('目前无法联系到企业，暂不安排盘活')
 status='排除' if no else '待核实' if review else '推荐';w=s['rules']['score'];score=None
 if status=='推荐':score=min(100,w['base']+(w['qualification'] if not unknown(value(s,c,'qualification')) and date_ok(value(s,c,'qualificationUntil')) and value(s,c,'qualificationUntil')>=TODAY else 0)+(w['dynamic'] if not unknown(c.get('dynamic')) else 0)+(w['fresh'] if date_ok(c.get('sourceDate')) and c['sourceDate']>='2026-10-01' else 0))
 return dict(entity_id=c['id'],state=status,score=score,reasons=no or review or ['企业未续费、可以联系，经营和风险核验情况符合本轮条件'],evidence_ids=refs(s,c),service=s['rules']['services'].get(c.get('industry'),'待资源确认'),next='先补充并核实关键资料。' if status=='待核实' else '暂缓安排，核对不符合条件的原因。' if status=='排除' else '核实实际需求和服务资源，记录下一步。',rules_version=s['rulesVersion'])

def result(s,type,payload):
 mode='mixed_sources' if any(c.get('is_mock') is False for c in s['companies']) else 'mock'
 r=dict(request_id=uid('REQ'),mode=mode,ai_mode='not_called_rule_template',data_time=TODAY,generated_at=now(),data_version=s['version'],rules_version=s['rulesVersion'],status='待核对' if any(x.get('state')=='待核实' for x in payload.get('rows',[])) else '成功',type=type,payload=copy.deepcopy(payload),evidence=copy.deepcopy(s['evidence']),companies=copy.deepcopy(s['companies']),rules=copy.deepcopy(s['rules']));s['results'].insert(0,r);return r

def activity_rows(s,info):
 inds=[k for k in ENUMS['industry'] if k in info['name']+info['theme']]
 if re.search('制造|数字化',info['name']+info['theme']) and '制造业' not in inds:inds.append('制造业')
 rows=[]
 for c in s['companies']:
  no,review=gate(s,c)
  if not inds:review.append('主题未识别行业')
  elif unknown(value(s,c,'industry')):review.append('企业行业待核实')
  elif value(s,c,'industry') not in inds:no.append('企业行业与会议主题不符')
  if not info['history']:
   if unknown(value(s,c,'member')):review.append('会员状态待核实')
   elif value(s,c,'member')!='现有会员':no.append('本次会议仅面向现有会员')
  suitable=not no and not review
  rows.append(dict(entity_id=c['id'],state='排除' if no else '待核实' if review else '匹配',suitable=suitable,reasons=no or review or ['企业行业与会议主题匹配，符合参会条件'],evidence_ids=refs(s,c)))
 return rows

def brief_text(s,c):
 facts=[f'{label}：{value(s,c,k) or "待核实"} [{", ".join(refs(s,c,k)) or "无核验依据"}]' for k,label in FIELDS.items() if k not in ('id','source','sourceDate')];d=decision(s,c)
 source_mode='企查查及操作员确认资料' if c.get('is_mock') is False else '模拟资料'
 return f'拜访简报：{c["id"]}\n生成方式：Python规则模板，本简报未调用模型\n数据V{s["version"]} / DEMO-{s["rulesVersion"]}\n\n一、已知事实（{source_mode}）\n'+ '\n'.join(facts)+f'\n实控人：未提供独立核验资料，待核实。\n舆情：未提供核验资料。\n\n二、规则建议\n会员级别：无正式规则，待确认。\n建议服务：{d["service"]}\n提问：实际需求是什么？机构能提供哪些服务？\n建议不代表客户需求或续费意愿。\n\n三、待核实/风险\n'+ '；'.join(d['reasons'])

def validate_rules(r):
 require(set(r)=={'riskExcluded','operationExcluded','requireReach','score','services'},'规则字段不完整')
 require(isinstance(r['requireReach'],bool),'requireReach应为布尔值')
 for key,field in [('riskExcluded','risk'),('operationExcluded','operation')]:require(isinstance(r[key],list) and all(x in ENUMS[field] for x in r[key]),'排除列表无效')
 require(set(r['score'])=={'base','qualification','dynamic','fresh'} and all(type(x) in (int,float) and x>=0 for x in r['score'].values()) and sum(r['score'].values())<=100,'评分应非负且总和不超过100')
 require(set(r['services'])==set(ENUMS['industry']) and all(isinstance(v,str) and 0<len(v)<=1000 for v in r['services'].values()),'服务目录无效')
