import json,argparse
from pathlib import Path
from backend import domain as d
from backend.store import Store
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('file');p.add_argument('--replace',action='store_true');args=p.parse_args();payload=json.loads(Path(args.file).read_text(encoding='utf-8-sig'));s=payload.get('workspace',payload)
 d.require(s.get('schema') in (2,3),'仅支持本项目V2/V3工作空间')
 required=['companies','evidence','issues','imports','results','changes','batches','packs','visits','tasks','logs'];d.require(all(isinstance(s.get(k),list) for k in required),'工作空间集合不完整');d.validate_rules(s['rules']);ids=[]
 for c in s['companies']:
  for k in d.FIELDS:d.valid(k,c.get(k))
  d.require(c.get('id'),'缺少匿名企业编号');ids.append(c['id'])
 d.require(len(set(ids))==len(ids),'存在重复企业编号')
 for e in s['evidence']:d.require(e.get('entity_id') in ids,'证据关联企业不存在')
 for k in ('version','rulesVersion'):d.require(type(s.get(k)) is int and s[k]>=1,'数据/规则版本无效')
 store=Store(d.BASE/'data/workspace.sqlite3');before=store.read();d.require(args.replace or before['_revision']==0,'数据库已有操作记录；备份后用--replace明确替换')
 def restore(state,connection):
  revision=state['_revision'];state.clear();state.update({k:v for k,v in s.items() if not k.startswith('_') and k!='decisions'});state['schema']=3;state['_revision']=revision;return {'imported':len(ids)}
 store.execute(d.uid('MIGRATE'),before['_revision'],'migrate_local',{},restore);print('Imported anonymous records:',len(ids))
