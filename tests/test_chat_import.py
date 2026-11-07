import asyncio
import copy
import json
import stat

import pytest
from fastapi.testclient import TestClient

from backend import chat_import as ci, credentials, domain as d
from backend.app import create_app
from backend.mcp_gateway import Gateway
from backend.store import Store, dump

NAME = '测试资料有限公司'
CODE = '91320594088140947F'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_SECRETS_PATH', str(tmp_path / '.secrets.json'))
    monkeypatch.delenv('QCC_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    return tmp_path


def material(name=NAME, operation='存续'):
    source = dict(id=d.uid('QCC').upper(), text=f'企业名称：{name}；信用代码：{CODE}；经营状态：{operation}；有公开电话。',
                  kind='qcc', tool='get_company_registration_info', server_id='qcc-company',
                  arguments={'searchKey': name}, obtained_at=d.now())
    candidate = dict(fields=dict(companyName=name, creditCode=CODE, operation=operation),
                     citations={k: dict(source_id=source['id'], quote=source['text']) for k in ('companyName', 'creditCode', 'operation')}, notes=[])
    return source, candidate


def put_draft(store, candidate=None, sources=None):
    source, raw = material()
    draft = ci.normalize_candidate(candidate or raw, sources or [source], 'test-turn-01')
    with store.connect() as c:
        c.execute('INSERT INTO chat_drafts VALUES(?,?,?)', (draft['id'], draft['turn_id'], dump(draft)))
    return draft


def apply(store, drafts, **extra):
    args = dict(draft_ids=[x['id'] for x in drafts], confirmed=True, who='核验员01', **extra)
    return store.execute(d.uid('TEST'), store.read()['_revision'], 'chat_import_apply', args,
                         lambda s, c: ci.apply_drafts(s, c, args))


def test_secret_settings_are_private_and_never_exported(env):
    app = create_app(env / 'workspace.sqlite3')
    with TestClient(app) as client:
        info = client.get('/api/workspace').json()
        secret = 'test-deepseek-key-private'
        response = client.post('/api/chat/settings', json={'deepseek_key': secret, 'qcc_key': 'Bearer test-qcc-key-private'},
                               headers={'X-Workspace-Token': info['csrf_token']})
        assert response.status_code == 200
        assert response.json()['deepseek_configured']
        assert secret not in response.text
        assert secret not in client.get('/api/chat/settings').text
        assert secret not in client.get('/api/backup').text
        assert stat.S_IMODE(credentials.secret_path().stat().st_mode) == 0o600
        assert credentials.get_secret('QCC_API_KEY') == 'test-qcc-key-private'
        assert credentials.redact(secret) == '[REDACTED]'
        assert client.post('/api/chat/settings', json={'deepseek_key': 'change-key'}).status_code == 403
        assert client.post('/api/chat/turns', json={'request_id': 'missing-01', 'session_id': 'session-01', 'text': '查询企业', 'services': ['evil']},
                           headers={'X-Workspace-Token': info['csrf_token']}).status_code == 400


def test_missing_model_key_returns_actionable_error(env):
    with TestClient(create_app(env / 'db.sqlite3')) as client:
        token = client.get('/api/workspace').json()['csrf_token']
        response = client.post('/api/chat/turns', json=dict(request_id='test-turn-01', session_id='session-01', text='查询企业'), headers={'X-Workspace-Token': token})
        assert response.status_code == 400
        assert 'DeepSeek API Key' in response.json()['detail']
        assert len(client.get('/api/workspace').json()['workspace']['companies']) == 36


def test_keys_remain_saved_after_refresh_and_app_restart(env):
    secret, qcc = 'test-deepseek-persistent-1234', 'test-qcc-persistent-5678'
    with TestClient(create_app(env / 'persistent.sqlite3')) as client:
        token = client.get('/api/workspace').json()['csrf_token']
        saved = client.post('/api/chat/settings', json={'deepseek_key': secret, 'qcc_key': qcc}, headers={'X-Workspace-Token': token})
        assert saved.status_code == 200
        first = client.get('/api/chat/settings').json()
        assert first['deepseek_key_hint'] == '••••••••1234'
        assert first['qcc_key_hint'] == '••••••••5678'
        retained = client.post('/api/chat/settings', json={'deepseek_key': '', 'qcc_key': '', 'model': 'deepseek-flash'}, headers={'X-Workspace-Token': token})
        assert retained.status_code == 200
        assert client.get('/api/chat/settings').json()['deepseek_key_hint'] == first['deepseek_key_hint']
    with TestClient(create_app(env / 'persistent.sqlite3')) as restarted:
        info = restarted.get('/api/chat/settings')
        assert info.json()['deepseek_configured'] and info.json()['qcc_configured']
        assert info.json()['deepseek_key_hint'] == first['deepseek_key_hint']
        assert secret not in info.text and qcc not in info.text
    assert credentials.get_secret('DEEPSEEK_API_KEY') == secret
    assert credentials.get_secret('QCC_API_KEY') == qcc


def test_masked_display_value_cannot_overwrite_a_saved_key(env):
    credentials.save_settings({'deepseek_key': 'test-deepseek-keep-1234'})
    with pytest.raises(ValueError, match='完整的新密钥'):
        credentials.save_settings({'deepseek_key': credentials.status()['deepseek_key_hint']})
    assert credentials.get_secret('DEEPSEEK_API_KEY') == 'test-deepseek-keep-1234'


def test_grounding_private_business_and_risk_stay_unknown(env):
    source, raw = material()
    raw['fields'].update(reachable='可触达', renewal='未续费', industry='新能源', risk='已核验未见规则内风险')
    for key in ('reachable', 'renewal', 'industry', 'risk'):
        raw['citations'][key] = dict(source_id=source['id'], quote='编造的原文' if key == 'industry' else source['text'])
    draft = ci.normalize_candidate(raw, [source], 'test-turn')
    assert draft['importable']
    assert all(draft['fields'][k] is None for k in ('reachable', 'renewal', 'industry', 'risk'))
    assert draft['fields']['companyName'] == NAME
    assert draft['fields']['sourceDate'] is None
    d.valid('id', draft['fields']['id'])
    d.valid('source', draft['fields']['source'])


def test_cross_entity_and_unparseable_search_cannot_establish_identity(env):
    source, raw = material()
    source['arguments']['searchKey'] = '另一家企业'
    assert not ci.normalize_candidate(raw, [source], 'turn')['importable']
    source['arguments']['searchKey'] = NAME
    source['tool'] = 'get_company_by_query'
    assert not ci.normalize_candidate(raw, [source], 'turn')['importable']


def search_material(count=3):
    rows = [{'企业名称': name, '统一社会信用代码': CODE[:-1] + last, '状态': operation, '成立日期': '2020-01-01'}
            for name, last, operation in [
                ('测试科技有限公司', 'F', '在业'),
                ('测试软件有限公司', 'G', '存续'),
                ('测试产业有限公司', 'H', '注销')]][:count]
    source = dict(id=d.uid('QCC').upper(), kind='qcc', tool='get_company_by_query', server_id='qcc-company',
                  arguments={'searchKey': '测试'}, obtained_at=d.now(),
                  text=dump({'匹配结果': '多候选', '企业信息': rows}))
    return source, rows


def test_multiple_real_matches_keep_provider_order_even_if_model_omits_them(env):
    source, rows = search_material()
    drafts = ci.assemble_drafts([], [source], [source], 'search-turn')
    assert [x['fields']['companyName'] for x in drafts] == [x['企业名称'] for x in rows]
    assert [x['match']['rank'] for x in drafts] == [1, 2, 3]
    assert all(x['importable'] and x['fields']['risk'] is None for x in drafts)
    assert drafts[0]['fields']['operation'] == '存续'
    assert drafts[0]['citations']['operation']['quote'] == '在业'
    assert ci.normalize_candidate({'fields': {'companyName': rows[0]['企业名称'], 'creditCode': rows[1]['统一社会信用代码']},
                                   'citations': {k: {'source_id': source['id'], 'quote': source['text']} for k in ('companyName', 'creditCode')}},
                                  [source], 'mixed-turn')['importable'] is False


def test_search_does_not_invent_extra_matches_or_mix_previous_turns(env):
    source, rows = search_material(1)
    drafts = ci.assemble_drafts([], [source], [source], 'search-turn')
    assert len(drafts) == 1
    registry, raw = material()
    selected = ci.assemble_drafts([raw], [source, registry], [registry], 'details-turn')
    assert len(selected) == 1 and selected[0]['fields']['companyName'] == NAME


def test_search_row_cannot_borrow_another_candidates_status(env):
    source, rows = search_material()
    raw = {'fields': {'companyName': rows[0]['企业名称'], 'creditCode': rows[0]['统一社会信用代码'], 'operation': '注销'},
           'citations': {key: {'source_id': source['id'], 'quote': source['text']} for key in ('companyName', 'creditCode', 'operation')}}
    draft = ci.normalize_candidate(raw, [source], 'mixed-status')
    assert draft['importable'] and draft['fields']['operation'] is None


def test_previous_search_reply_gains_choices_without_query_or_business_write(env, monkeypatch):
    store = Store(env / 'db.sqlite3')
    source, rows = search_material()
    turn = dict(id='old-search-01', session_id='old-session-01', text='查询测试', status='completed',
                reply='请明确选择企业', sources=[source], trace=[], created_at=d.now(), model='test-model')
    with store.connect() as c:
        c.execute('INSERT INTO chat_turns VALUES(?,?,?,?)', (turn['id'], turn['session_id'], 'fingerprint', dump(turn)))
    importer = ci.ChatImporter(store, Gateway(store))
    result = importer.get(turn['id'])
    assert len(result['drafts']) == 3 and '勾选' in result['reply']
    assert result['previous_reply'] == turn['reply']
    assert len(store.read()['companies']) == 36 and store.read()['_revision'] == 0
    assert len(ci.ChatImporter(store, Gateway(store)).get(turn['id'])['drafts']) == 3


def test_search_pipeline_returns_choices_and_imports_only_selected(env, monkeypatch):
    credentials.save_settings({'qcc_key': 'qcc-test-private', 'deepseek_key': 'deepseek-test-private'})
    store = Store(env / 'db.sqlite3')
    importer = ci.ChatImporter(store, Gateway(store))
    source, rows = search_material()
    tool_calls = []

    async def fake_perform(cfg, tool=None, arguments=None):
        if tool is None:
            return {'tools': [dict(name='get_company_by_query', description='企业搜索', inputSchema=dict(type='object', properties={'searchKey': {'type': 'string'}}, required=['searchKey']))]}
        tool_calls.append((tool, arguments))
        return {'content': [{'type': 'text', 'text': source['text']}]}

    async def fake_complete(messages, model, *, tools=None, structured=False):
        if messages[0]['content'] == ci.PLAN_PROMPT:
            return dict(role='assistant',content=dump({'mode':'entity'}))
        if structured:
            return dict(role='assistant', content=dump(dict(reply='请选择企业', companies=[])))
        if any(m['role'] == 'tool' for m in messages):
            return dict(role='assistant', content='找到数个匹配企业')
        return dict(role='assistant', content=None, tool_calls=[dict(id='search-call', type='function', function=dict(name='query_qcc', arguments=dump(dict(server_id='qcc-company', tool_name='get_company_by_query', arguments_json=dump({'searchKey': '测试'})))))])

    monkeypatch.setattr(ci, 'perform', fake_perform)
    monkeypatch.setattr(importer, 'complete', fake_complete)

    async def run():
        first = importer.begin(dict(request_id='multiple-turn-01', session_id='multiple-session-01', text='查询测试公司', services=['qcc-company']))
        await importer.jobs[first['id']]
        turn = importer.get(first['id'])
        assert turn['status'] == 'completed', turn
        assert len(turn['drafts']) == 3 and len(store.read()['companies']) == 36
        assert '找到 3 家' in turn['reply'] and '勾选' in turn['reply']
        assert tool_calls == [('get_company_by_query', {'searchKey': '测试'})]
        state, _, _ = apply(store, [turn['drafts'][0], turn['drafts'][2]])
        assert len(state['companies']) == 38
        assert [x['companyName'] for x in state['companies'][-2:]] == [rows[0]['企业名称'], rows[2]['企业名称']]
        statuses = [d['status'] for d in importer.get(first['id'])['drafts']]
        assert statuses == ['imported', 'pending', 'imported']

    asyncio.run(run())


def test_explicit_operator_values_are_cited(env):
    source, raw = material()
    user = dict(id='USER-01', kind='operator', text=NAME + '是历史会员，未续费。', obtained_at=d.now())
    raw['fields'].update(member='历史会员', renewal='未续费')
    for key in ('member', 'renewal'):
        raw['citations'][key] = dict(source_id=user['id'], quote=user['text'])
    draft = ci.normalize_candidate(raw, [source, user], 'turn')
    assert draft['fields']['renewal'] == '未续费'
    assert draft['citations']['renewal']['kind'] == 'operator'


def test_review_gate_atomic_import_and_idempotency(env):
    store = Store(env / 'db.sqlite3')
    draft = put_draft(store)
    with pytest.raises(ValueError, match='核对'):
        ci.apply_drafts(store.read(), None, dict(draft_ids=[draft['id']], confirmed=False, who='核验员01'))
    with pytest.raises(ValueError, match='未知字段'):
        ci.apply_drafts(store.read(), None, dict(draft_ids=[draft['id']], confirmed=True, who='核验员01', fields={'risk': '已核验未见规则内风险'}))
    args = dict(draft_ids=[draft['id']], confirmed=True, who='核验员01')
    request = d.uid('TEST')
    before = store.read()
    state, out, replayed = store.execute(request, before['_revision'], 'chat_import_apply', args, lambda s, c: ci.apply_drafts(s, c, args))
    assert len(state['companies']) == 37
    assert state['companies'][-1]['companyName'] == NAME
    assert state['companies'][-1]['is_mock'] is False
    assert state['companies'][-1]['risk'] is None
    evidence = [e for e in state['evidence'] if e['entity_id'] == out['entity_ids'][0]]
    assert all(not e['is_mock'] and e['document_date'] is None and e['verification'] == '已核对' for e in evidence)
    assert all(e['source_text'] and e['excerpt'] in e['source_text'] for e in evidence)
    again = store.execute(request, before['_revision'], 'chat_import_apply', args, lambda s, c: ci.apply_drafts(s, c, args))
    assert again[2] is True and len(again[0]['companies']) == 37
    with pytest.raises(ValueError, match='已处理'):
        apply(store, [draft])


def test_duplicate_identity_keeps_old_value_then_resolves_with_original_evidence(env):
    store = Store(env / 'db.sqlite3')
    first = put_draft(store)
    apply(store, [first])
    source, raw = material(operation='注销')
    second = put_draft(store, raw, [source])
    state, out, _ = apply(store, [second])
    assert len(state['companies']) == 37 and out['conflicts'] == 1
    company = state['companies'][-1]
    assert company['operation'] == '存续'
    issue = state['issues'][-1]
    assert issue['old_value'] == '存续' and issue['new_value'] == '注销'
    args = dict(id=issue['issue_id'])
    state, _, _ = store.execute(d.uid('TEST'), state['_revision'], 'resolve', args,
                                lambda s, c: d.resolve(s, issue['issue_id'], True, '已对照企查查原文', '核验员01'))
    assert state['companies'][-1]['operation'] == '注销'
    assert next(e for e in state['evidence'] if e['evidence_id'] == issue['evidence_id'])['is_mock'] is False


def test_invalid_selection_rolls_back_all_candidates(env):
    store = Store(env / 'db.sqlite3')
    draft = put_draft(store)
    with pytest.raises(ValueError):
        apply(store, [draft, dict(id='does-not-exist')])
    assert len(store.read()['companies']) == 36
    with store.connect() as c:
        assert json.loads(c.execute('SELECT payload FROM chat_drafts').fetchone()[0])['status'] == 'pending'


def test_bounded_pipeline_generates_draft_without_importing(env, monkeypatch):
    credentials.save_settings({'qcc_key': 'qcc-test-private', 'deepseek_key': 'deepseek-test-private'})
    store = Store(env / 'db.sqlite3')
    importer = ci.ChatImporter(store, Gateway(store))
    query_count = 0
    source, raw = material()

    async def fake_perform(cfg, tool=None, arguments=None):
        nonlocal query_count
        if tool is None:
            return {'tools': [dict(name='get_company_registration_info', description='工商信息', inputSchema=dict(type='object', properties={'searchKey': {'type': 'string'}}, required=['searchKey'], additionalProperties=False))]}
        query_count += 1
        return {'content': [{'type': 'text', 'text': source['text']}]}

    async def fake_complete(messages, model, *, tools=None, structured=False):
        if messages[0]['content'] == ci.PLAN_PROMPT:
            return dict(role='assistant',content=dump({'mode':'entity'}))
        if structured:
            body = json.loads(messages[-1]['content'])
            actual_source = next(s for s in body['sources'] if s['kind'] == 'qcc')
            for citation in raw['citations'].values():
                citation['source_id'] = actual_source['id']
            return dict(role='assistant', content=dump(dict(reply='请人工确认', companies=[raw])))
        if any(m['role'] == 'tool' for m in messages):
            return dict(role='assistant', content='已查到工商资料')
        return dict(role='assistant', content=None, tool_calls=[dict(id='call-01', type='function', function=dict(name='query_qcc', arguments=dump(dict(server_id='qcc-company', tool_name='get_company_registration_info', arguments_json=dump({'searchKey': NAME})))) )])

    monkeypatch.setattr(ci, 'perform', fake_perform)
    monkeypatch.setattr(importer, 'complete', fake_complete)

    async def run():
        payload = dict(request_id='pipeline-turn-01', session_id='pipeline-session-01', text='查询' + NAME, services=['qcc-company'])
        first = importer.begin(payload)
        second = importer.begin(payload)
        assert first['id'] == second['id']
        await importer.jobs[first['id']]
        turn = importer.get(first['id'])
        assert turn['status'] == 'completed', turn
        assert len(turn['drafts']) == 1
        assert turn['drafts'][0]['fields']['companyName'] == NAME
        assert query_count == 1
        assert len(store.read()['companies']) == 36
        assert 'companyName' in ci.export_csv(turn['drafts']).splitlines()[0]
        assert 'deepseek-test-private' not in dump(turn)
        assert not importer.begin(payload)['status'] == 'pending'

    asyncio.run(run())


def test_timeout_has_durable_unknown_marker_and_is_not_retried(env, monkeypatch):
    store = Store(env / 'db.sqlite3')
    importer = ci.ChatImporter(store, Gateway(store))
    turn = dict(id='timeout-turn-01', session_id='session-01', status='running', sources=[], trace=[], text='查询', created_at=d.now())
    with store.connect() as c:
        c.execute('INSERT INTO chat_turns VALUES(?,?,?,?)', (turn['id'], turn['session_id'], 'fp', dump(turn)))

    async def timeout(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(ci, 'perform', timeout)
    catalog = {('qcc-company', 'get_company_registration_info'): {'inputSchema': {'type': 'object'}}}
    args = dict(server_id='qcc-company', tool_name='get_company_registration_info', arguments_json='{}')
    seen = set()

    async def run():
        answer = await importer.query(turn, catalog, args, seen)
        assert 'error' in answer
        assert turn['trace'][-1]['status'] == 'unknown'
        assert not turn['sources']
        with pytest.raises(ValueError, match='已执行'):
            await importer.query(turn, catalog, args, seen)

    asyncio.run(run())
    restarted = ci.ChatImporter(store, Gateway(store))
    assert restarted.get(turn['id'])['status'] == 'failed'


def test_changed_qcc_endpoint_cannot_receive_saved_key(env):
    store = Store(env / 'db.sqlite3')
    gateway = Gateway(store)
    ci.setup_qcc(gateway)
    cfg = gateway.config('qcc-company')
    cfg['url'] = 'https://example.invalid/mcp'
    gateway.save(cfg)
    with pytest.raises(ValueError, match='更改'):
        ci.config_for(gateway, 'qcc-company')
