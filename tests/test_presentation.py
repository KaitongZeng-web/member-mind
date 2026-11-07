import re

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import domain as d
from backend.presentation import build_brief, brief_text, result_report


def test_brief_and_report_are_business_documents_without_technical_ids():
    state = d.initial()
    company = state['companies'][0]
    document = build_brief(state, company, '核实需求')
    text = brief_text(document)
    assert '企业概况' in text and '沟通要点' in text and '待核实事项' in text
    assert not re.search(r'EV-|REQ-|DEMO-|R-RISK|request_id|https?://', text)
    assert document['facts'] and document['facts'][0]['label'] == '行业'
    result = d.result(state, '拜访简报', {'document': document, 'text': text})
    assert result_report(result) == text


def test_old_brief_download_hides_technical_references_and_links():
    result = {'type': '拜访简报', 'payload': {'text': '拜访简报：企业 A\n行业：制造业 [EV-123-abc]\nR-RISK：风险待核实\n参考 https://example.test/profile'}}
    text = result_report(result)
    assert '制造业' in text and '风险待核实' in text
    assert not re.search(r'EV-|R-RISK|https?://', text)


def test_business_download_uses_plain_text_and_keeps_audit_snapshot(tmp_path):
    with TestClient(create_app(tmp_path / 'business.sqlite3')) as client:
        info = client.get('/api/workspace').json()
        response = client.post('/api/commands', json={'request_id': 'brief-document-01', 'expected_revision': 0,
                                                     'command': 'brief', 'args': {'id': '企业 A', 'purpose': '了解业务需求'}},
                               headers={'X-Workspace-Token': info['csrf_token']})
        assert response.status_code == 200
        result = response.json()['output']
        assert result['evidence'] and result['payload']['document']['company'] == '企业 A'
        report = client.get('/api/results/' + result['request_id'] + '/download?format=report')
        assert report.status_code == 200 and report.headers['content-type'].startswith('text/plain')
        assert not re.search(r'EV-|REQ-|DEMO-|R-RISK|request_id|https?://', report.text)
        snapshot = client.get('/api/results/' + result['request_id'] + '/download').json()
        assert snapshot['request_id'] == result['request_id'] and snapshot['evidence']
