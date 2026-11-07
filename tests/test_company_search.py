import asyncio
import copy
import json

import pytest

from backend import chat_import as ci, credentials, domain as d
from backend.company_search import (PLAN_PROMPT, RANK_PROMPT, SearchEngine, field_states,
                                    public_candidate, rank_candidates)
from backend.mcp_gateway import Gateway
from backend.store import Store, dump


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_SECRETS_PATH', str(tmp_path / 'secrets.json'))
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('QCC_API_KEY', raising=False)
    credentials.save_settings({'deepseek_key': 'semantic-test-deepseek-key', 'qcc_key': 'semantic-test-qcc-key'})
    return tmp_path


def material(name='深圳市翼航科技有限公司', city='深圳市', product='无人机', number=1):
    code = '91440300A00000000' + str(number)
    registry = {'企业名称': name, '统一社会信用代码': code, '登记状态': '存续（在营、开业、在册）',
                '地区信息': {'省份': '广东省', '城市': city, '区域': '南山区'},
                '国标行业': {'门类': '制造业', '小类': product + '制造'},
                '经营范围': product + '的研发、制造与销售。', '注册地址': city + '测试地址',
                '成立日期': '2010-01-01', '法定代表人': '测试角色', '注册资本': '1000万元'}
    profile = {'企业名称': name, '主营产品': [product, '控制系统'], '企业规模': '大型',
               '简介': product + '技术研发企业，入选行业榜单。'}
    search = {'企业信息': [{'企业名称': name, '统一社会信用代码': code, '状态': '存续'}]}
    def source(tool, payload, key):
        return {'id': d.uid('QCC').upper(), 'kind': 'qcc', 'server_id': 'qcc-company', 'tool': tool,
                'arguments': {'searchKey': key}, 'text': dump(payload), 'obtained_at': d.now()}
    sources = [source('get_company_by_query', search, name),
               source('get_company_registration_info', registry, code),
               source('get_company_profile', profile, code)]
    return ci.search_candidates([sources[0]])[0], sources, registry, profile


def assessment(candidate, sources, score=95, influence=True):
    code = candidate['fields']['creditCode']
    product = json.loads(sources[-1]['text'])['主营产品'][0]
    return {'identity': code, 'business_score': score,
            'business_citation': {'source_id': sources[-1]['id'], 'quote': product},
            'influence_citation': {'source_id': sources[-1]['id'], 'quote': '大型'} if influence else None}


def test_public_details_fill_real_city_industry_and_business_with_sources(env):
    candidate, sources, _, _ = material()
    enriched = public_candidate(candidate, sources)
    draft = ci.normalize_candidate(enriched, sources, 'public-details')
    assert draft['fields']['region'] == '深圳'
    assert draft['fields']['industry'] == '制造业'
    assert draft['fields']['mainBusiness'] == '无人机、控制系统'
    assert draft['fields']['operation'] == '存续'
    assert draft['fields']['establishedDate'] == '2010-01-01'
    assert draft['fields']['registeredAddress'] == '深圳市测试地址'
    assert draft['fields']['sourceDate'] is None
    assert all(draft['fields'][key] is None for key in ('member', 'renewal', 'reachable', 'risk'))
    for key in ('region', 'industry', 'mainBusiness', 'businessScope'):
        assert draft['citations'][key]['quote'] in draft['citations'][key]['text']
    states = field_states(draft, sources)
    assert states['member']['label'] == '需内部确认'
    assert states['risk']['label'] == '需人工核验'
    assert states['qualification']['label'] == '需专项核验'
    assert 'region' not in states


def test_different_company_record_cannot_fill_another_company(env):
    candidate, sources, _, _ = material()
    wrong = copy.deepcopy(sources[-2])
    payload = json.loads(wrong['text'])
    payload['企业名称'] = '另一家企业'
    wrong['text'] = dump(payload)
    enriched = public_candidate(candidate, [sources[0], wrong])
    assert enriched['fields'].get('region') is None


def test_semantic_matching_uses_business_not_name_and_excludes_wrong_city(env):
    first, a, _, _ = material()
    wrong_city, b, _, _ = material(name='广州无人机知名企业有限公司', city='广州市', number=2)
    wrong_business, c, _, _ = material(name='深圳无人机文具有限公司', product='文具', number=3)
    candidates = [public_candidate(first, a), public_candidate(wrong_city, b), public_candidate(wrong_business, c)]
    scores = [assessment(first, a), assessment(wrong_city, b, 100), assessment(wrong_business, c, 100)]
    # The last candidate tries to use its company name as a business citation.
    scores[-1]['business_citation']['quote'] = wrong_business['fields']['companyName']
    plan = {'location': '深圳', 'business': '无人机', 'keywords': ['无人机', '无人驾驶航空器']}
    ranked, excluded = rank_candidates(candidates, scores, plan, a + b + c)
    assert [x['fields']['companyName'] for x in ranked] == [first['fields']['companyName']]
    assert '无人机' not in ranked[0]['fields']['companyName']
    assert len(excluded) == 2
    assert ranked[0]['match']['business_evidence']['quote'] == '无人机'


def test_unsupported_fame_adds_no_priority_and_cannot_beat_direct_business_fit(env):
    first, a, _, _ = material(number=1)
    second, b, _, _ = material(name='深圳市云翼科技有限公司', number=2)
    enriched = [public_candidate(first, a), public_candidate(second, b)]
    scores = [assessment(first, a, 84), assessment(second, b, 90, False)]
    ranked, _ = rank_candidates(enriched, scores, {'location': '深圳', 'business': '无人机'}, a + b)
    assert ranked[0]['fields']['companyName'] == second['fields']['companyName']
    scores[1]['influence_citation'] = {'source_id': b[-1]['id'], 'quote': '全球市场占有率第一'}
    ranked, _ = rank_candidates(enriched, scores, {'location': '深圳', 'business': '无人机'}, a + b)
    assert ranked[0]['match']['influence_evidence'] is None


def test_real_geography_is_not_restricted_to_demo_dictionary(env):
    for city in ('深圳', '成都', '南京', '苏州'):
        d.valid('region', city)
    d.valid('industry', '航空航天与国防')
    with pytest.raises(ValueError):
        d.valid('establishedDate', '2026-02-31')


def test_rendered_product_list_is_grounded_back_to_literal_source_values(env):
    candidate,sources,_,_=material()
    enriched=public_candidate(candidate,sources)
    score=assessment(candidate,sources)
    score['business_citation']['quote']='主营产品：无人机、控制系统'
    plan={'location':'深圳','business':'无人机','keywords':['无人机']}
    ranked,_=rank_candidates([enriched],[score],plan,sources)
    assert ranked[0]['match']['business_evidence']['quote']=='无人机'
    score['business_citation']['quote']='主营产品：无人机、凭空编造的产品'
    ranked,_=rank_candidates([enriched],[score],plan,sources)
    assert not ranked


def test_enriched_record_is_stored_only_after_operator_confirmation(env):
    candidate,sources,_,_=material()
    draft=ci.normalize_candidate(public_candidate(candidate,sources),sources,'enriched-turn')
    store=Store(env/'import.sqlite3')
    with store.connect() as connection:
        connection.execute('INSERT INTO chat_drafts VALUES(?,?,?)',(draft['id'],draft['turn_id'],dump(draft)))
    assert len(store.read()['companies'])==36
    args={'draft_ids':[draft['id']],'confirmed':True,'who':'核验员01'}
    state,output,_=store.execute('confirm-enriched-01',0,'chat_import_apply',args,lambda s,c:ci.apply_drafts(s,c,args))
    company=next(c for c in state['companies'] if c['id']==output['entity_ids'][0])
    assert company['region']=='深圳' and company['mainBusiness']=='无人机、控制系统'
    assert company['renewal'] is None and company['risk'] is None
    assert any(e['field']=='mainBusiness' and not e['is_mock'] and e['source_text'] for e in state['evidence'])


def test_failed_search_is_not_automatically_repeated(env,monkeypatch):
    store=Store(env/'retry.sqlite3');importer=ci.ChatImporter(store,Gateway(store))
    turn={'id':'no-retry-turn-01','session_id':'no-retry-session','status':'running','text':'查询企业','sources':[],'trace':[],'created_at':d.now()}
    with store.connect() as c:c.execute('INSERT INTO chat_turns VALUES(?,?,?,?)',(turn['id'],turn['session_id'],'fp',dump(turn)))
    calls=[]
    async def timeout(*args,**kwargs):
        calls.append(1);raise TimeoutError()
    monkeypatch.setattr(ci,'perform',timeout)
    engine=SearchEngine(importer,turn,{('qcc-company','get_company_profile'):{'inputSchema':{'type':'object'}}})
    async def run():
        assert await engine.call('get_company_profile','企业全称') is None
        assert await engine.call('get_company_profile','企业全称') is None
        assert len(calls)==1 and turn['trace'][0]['status']=='unknown'
    asyncio.run(run())


def test_semantic_pipeline_verifies_leads_and_never_imports_automatically(env, monkeypatch):
    materials = [material(), material(name='深圳无人机文具有限公司', product='文具', number=2),
                 material(name='广州无人机有限公司', city='广州市', number=3)]
    by_key = {}
    for candidate, _, registry, profile in materials:
        for key in (candidate['fields']['companyName'], candidate['fields']['creditCode']):
            by_key[key] = (candidate, registry, profile)
    store = Store(env / 'semantic.sqlite3')
    importer = ci.ChatImporter(store, Gateway(store))
    calls = []
    schema = {'type': 'object', 'properties': {'searchKey': {'type': 'string'}}, 'required': ['searchKey']}

    async def fake_perform(config, tool=None, arguments=None):
        if tool is None:
            return {'tools': [{'name': name, 'description': name, 'inputSchema': schema} for name in
                              ('get_company_by_query', 'get_company_registration_info', 'get_company_profile')]}
        calls.append((tool, arguments['searchKey']))
        candidate, registry, profile = by_key[arguments['searchKey']]
        payload = {'企业信息': [{'企业名称': candidate['fields']['companyName'], '统一社会信用代码': candidate['fields']['creditCode'], '状态': '存续'}]} if tool == 'get_company_by_query' else registry if tool == 'get_company_registration_info' else profile
        return {'content': [{'type': 'text', 'text': dump(payload)}]}

    async def fake_complete(messages, model, *, tools=None, structured=False):
        if messages[0]['content'] == PLAN_PROMPT:
            return {'content': dump({'mode': 'semantic', 'location': '深圳', 'business': '无人机', 'keywords': ['无人机'],
                                     'summary': '寻找深圳主营无人机业务的企业',
                                     'leads': [{'query': c['fields']['companyName']} for c, *_ in materials]})}
        assert messages[0]['content'] == RANK_PROMPT
        companies = json.loads(messages[1]['content'])['companies']
        rankings = []
        for company in companies:
            source = next(s for s in company['sources'] if s['tool'] == 'get_company_profile')
            product = json.loads(source['text'])['主营产品'][0]
            rankings.append({'identity': company['identity'], 'business_score': 95,
                             'business_citation': {'source_id': source['id'], 'quote': product},
                             'influence_citation': {'source_id': source['id'], 'quote': '大型'}})
        return {'content': dump({'rankings': rankings})}

    monkeypatch.setattr(ci, 'perform', fake_perform)
    monkeypatch.setattr(importer, 'complete', fake_complete)

    async def run():
        turn = importer.begin({'request_id': 'semantic-test-01', 'session_id': 'semantic-session-01',
                               'text': '深圳无人机公司', 'services': ['qcc-company']})
        await importer.jobs[turn['id']]
        result = importer.get(turn['id'])
        assert result['status'] == 'completed', result
        assert len(result['drafts']) == 1
        assert result['drafts'][0]['fields']['companyName'] == materials[0][0]['fields']['companyName']
        assert result['drafts'][0]['fields']['region'] == '深圳'
        assert result['drafts'][0]['field_states']['member']['label'] == '需内部确认'
        assert result['drafts'][0]['match']['kind'] == 'semantic'
        assert len(result['search_intent']['excluded']) == 2
        assert len(calls) == len(set(calls)) == 9
        assert len(store.read()['companies']) == 36
    asyncio.run(run())


def test_ambiguous_extra_conditions_are_not_silently_ignored(env, monkeypatch):
    store = Store(env / 'conditions.sqlite3')
    importer = ci.ChatImporter(store, Gateway(store))

    async def fake_complete(*args, **kwargs):
        return {'content': dump({'mode': 'semantic', 'business': '无人机', 'location': '深圳', 'leads': [], 'keywords': [],
                                 'unsupported_conditions': ['资金大于一百万'], 'clarification': '资金指注册资本、融资额还是营收？'})}
    async def no_discovery(*args, **kwargs):
        raise AssertionError('不能忽略未明确条件直接查询')
    monkeypatch.setattr(importer, 'complete', fake_complete)
    monkeypatch.setattr(importer, 'discover', no_discovery)
    async def run():
        turn = importer.begin({'request_id': 'conditions-test-01', 'session_id': 'conditions-session-01',
                               'text': '深圳无人机企业，资金大于一百万', 'services': ['qcc-company']})
        await importer.jobs[turn['id']]
        result = importer.get(turn['id'])
        assert result['status'] == 'completed' and not result['drafts']
        assert '资金指' in result['reply']
    asyncio.run(run())
