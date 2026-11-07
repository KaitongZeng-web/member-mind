import csv
import io

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import domain as d


def preview(state, rows):
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return d.preflight(state, output.getvalue(), {str(i): key for i, key in enumerate(rows[0])})


def test_real_file_ids_regions_and_sources_survive_confirmation():
    state = d.initial()
    rows = [['id', 'companyName', 'region', 'industry', 'source', 'dynamic', 'risk']]
    rows += [[f'TEST-{i:03}', f'样例科技{i}有限公司', '江西', '智能制造', 'CNINFO-002460', '开展新产品研发。', '待核实']
             for i in range(50)]
    batch = preview(state, rows)
    assert batch['counts']['新增'] == 50 and batch['counts']['拒绝'] == 0
    assert len(state['companies']) == 36  # A preview never imports companies.
    d.apply_import(state, batch)
    company = state['companies'][-1]
    assert len(state['companies']) == 86
    assert company['external_id'] == 'TEST-049' and company['is_mock'] is False
    assert company['source_label'] == 'CNINFO-002460' and company['sourceDate'] is None
    assert company['risk'] is None and company['region'] == '江西'
    evidence = [e for e in state['evidence'] if e['entity_id'] == company['id']]
    assert evidence and all(e['source_type'] == '操作员上传的企业文件' for e in evidence)
    assert all(e['document_date'] is None and not e['is_mock'] for e in evidence)
    assert all(e['source_label'] == 'CNINFO-002460' for e in evidence)


def test_name_only_import_deduplicates_and_routes_differences_to_review():
    state = d.initial()
    first = preview(state, [['companyName', 'region'], ['示例科技有限公司', '深圳']])
    d.apply_import(state, first)
    repeated = preview(state, [['companyName', 'region'], ['示例科技有限公司', '深圳']])
    assert repeated['counts']['无变化'] == 1
    changed = preview(state, [['companyName', 'region'], ['示例科技有限公司', '上海']])
    assert changed['counts']['待确认'] == 1
    d.apply_import(state, changed)
    assert len(state['companies']) == 37 and state['companies'][-1]['region'] == '深圳'
    issue = state['issues'][-1]
    assert issue['new_value'] == '上海'
    d.resolve(state, issue['issue_id'], True, '已对照上传文件核实', '核验员01')
    assert state['companies'][-1]['region'] == '上海'


def test_code_matches_company_from_chat_but_conflicting_identity_is_rejected():
    state = d.initial()
    state['companies'].append(dict(id='QCC-123456789ABC', companyName='示例公司', creditCode='914403007954257495', region='深圳'))
    batch = preview(state, [['id', 'companyName', 'creditCode', 'region'],
                            ['CUSTOM-1', '示例公司', '914403007954257495', '深圳'],
                            ['CUSTOM-2', '示例公司', '91440300306077254B', '深圳']])
    assert batch['items'][0]['record']['id'] == 'QCC-123456789ABC'
    assert batch['counts']['无变化'] == 1 and batch['counts']['拒绝'] == 1
    assert '信用代码不一致' in batch['items'][1]['reason']


def test_invalid_dates_codes_and_unidentified_rows_remain_rejected():
    state = d.initial()
    batch = preview(state, [['companyName', 'creditCode', 'sourceDate'],
                            ['示例一', '', '2026-02-31'], ['示例二', 'INVALID', ''], ['', '', '']])
    assert batch['counts']['拒绝'] == 3


def test_name_only_file_api_preview_then_explicit_confirmation(tmp_path):
    with TestClient(create_app(tmp_path / 'file.sqlite3')) as client:
        info = client.get('/api/workspace').json()
        headers = {'X-Workspace-Token': info['csrf_token']}
        batch = client.post('/api/import/preview', headers=headers,
                            json={'text': '企业名称,地区,来源说明\n示例企业有限公司,山东,公开年报\n',
                                  'mapping': {'0': 'companyName', '1': 'region', '2': 'source'}}).json()
        assert batch['counts']['新增'] == 1
        assert len(client.get('/api/workspace').json()['workspace']['companies']) == 36
        payload = {'request_id': 'real-file-confirm-01', 'expected_revision': 0,
                   'command': 'import_apply', 'args': {'batch_id': batch['batch_id']}}
        response = client.post('/api/commands', headers=headers, json=payload)
        assert response.status_code == 200
        assert len(response.json()['workspace']['companies']) == 37
        repeated = client.post('/api/commands', headers=headers, json=payload)
        assert repeated.status_code == 200 and repeated.json()['replayed']
        assert len(repeated.json()['workspace']['companies']) == 37
