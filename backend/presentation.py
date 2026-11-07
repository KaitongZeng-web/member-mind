"""Human-readable documents; audit identifiers stay in the underlying snapshots."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import domain as d


def plain(value):
    if value is None or value == '':
        return '待核实'
    if isinstance(value, bool):
        return '是' if value else '否'
    text = str(value)
    text = re.sub(r'https?://[^\s<>\]）]+', '', text)
    text = re.sub(r'\[[^\]]*(?:EV-|REQ-|SIM-|QCC-)[^\]]*\]', '', text)
    text = re.sub(r'\b(?:EV|REQ|PACK|VISIT|TASK|CH|IS|BATCH|DRAFT)-[A-Za-z0-9-]+', '', text)
    text = re.sub(r'R-[A-Z]+(?:/R-[A-Z]+)*[：:]\s*', '', text)
    text = re.sub(r'DEMO-\d+|F\d{2}(?:[—/-]F?\d{2})?', '', text)
    text = re.sub(r'数据\s*V?\d+\s*/?\s*', '', text)
    text = text.replace('=>', '：').replace('=', '为')
    for word, human in [('AND', '同时满足'), ('OR', '满足任一项'), ('NOT', '排除')]:
        text = re.sub(r'\b' + word + r'\b', human, text)
    return text.strip() or '待核实'


def build_brief(state, company, purpose=''):
    decision = d.decision(state, company)
    facts = []
    for key, label in d.FIELDS.items():
        if key in ('id', 'companyName', 'source', 'sourceDate'):
            continue
        value = d.value(state, company, key)
        evidence = [e for e in state['evidence'] if e['entity_id'] == company['id'] and e['field'] == key and e['value'] == company.get(key) and e['verification'] == '已核对']
        facts.append(dict(label=label, value=value, confirmed=not d.unknown(value),
                          sources=[dict(type=e['source_type'], excerpt=plain(e['excerpt']),
                                        document_date=e.get('document_date'), obtained_at=e.get('obtained_at'),
                                        is_mock=e.get('is_mock', True)) for e in evidence]))
    missing = [fact['label'] for fact in facts if not fact['confirmed']]
    return dict(company=company.get('companyName') or company['id'],
                purpose=purpose or '了解企业情况，核实需求并讨论服务安排',
                source_kind='企查查及操作员确认资料' if company.get('is_mock') is False else '模拟资料',
                facts=facts, missing=missing, status=decision['state'],
                recommendation=plain(decision['service']), next_step=plain(decision['next']),
                considerations=[plain(x) for x in decision['reasons']],
                questions=['目前最需要解决的业务问题是什么？', '有哪些服务资源可以满足已确认的需求？', '后续安排由谁负责，何时反馈？'],
                membership_level='尚未提供会员分级标准，待确认',
                note='服务建议用于沟通准备，实际需求和安排须与企业确认。')


def brief_text(document):
    lines = ['拜访简报｜' + document['company'], '拜访目的：' + plain(document['purpose']),
             '资料类型：' + document['source_kind'], '', '一、企业概况']
    lines.extend(f"{fact['label']}：{plain(fact['value'])}" for fact in document['facts'])
    lines.extend(['', '二、服务建议', '会员级别：' + document['membership_level'],
                  '建议讨论：' + document['recommendation'], '下一步：' + document['next_step'],
                  '', '三、沟通要点', *document['questions'], '', '四、待核实事项'])
    lines.append('资料缺口：' + ('、'.join(document['missing']) or '暂无已知资料缺口'))
    lines.extend(document['considerations'])
    lines.extend(['', document['note']])
    return '\n'.join(plain(line) if line else '' for line in lines)


def result_report(result):
    payload = result['payload']
    if result['type'] == '拜访简报':
        if payload.get('document'):
            return brief_text(payload['document'])
        lines = [plain(line) for line in payload.get('text', '').splitlines()
                 if not re.search(r'生成方式|规则模板|模型未调用|数据V|DEMO-|信息依据', line)]
        return '\n'.join(lines)
    companies = {c['id']: c for c in result.get('companies', [])}
    title = {'企业标签表': '企业画像资料', '会议通知名单': '会议匹配名单'}.get(result['type'], result['type'])
    try:
        when = datetime.fromisoformat(result['generated_at']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y年%m月%d日 %H:%M')
    except (KeyError, ValueError):
        when = '生成时间待确认'
    lines = [title, '生成时间：' + when, '以下为生成时留存的资料。', '']
    if payload.get('info'):
        info = payload['info']
        lines.extend(['会议名称：' + plain(info.get('name')), '会议主题：' + plain(info.get('theme')), ''])
    for row in payload.get('rows', payload.get('companies', [])):
        company = companies.get(row.get('entity_id', row.get('id')), row)
        lines.append(plain(company.get('companyName') or company.get('id') or '企业名称待确认'))
        if row.get('state'):
            lines.append('判断：' + plain(row['state']))
        if row.get('score') is not None:
            lines.append('跟进优先分：' + str(row['score']))
        lines.extend(plain(reason) for reason in row.get('reasons', []))
        if row.get('service'):
            lines.append('建议服务：' + plain(row['service']))
        if 'companyName' in row or result['type'] == '企业标签表':
            lines.extend(label + '：' + plain(row.get(key)) for key, label in d.FIELDS.items() if key not in ('id', 'source', 'sourceDate', 'companyName'))
        lines.append('')
    return '\n'.join(lines)
