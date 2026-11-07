"""CSV imports retain operator sources and reconcile real company identities."""
import copy
import csv
import hashlib
import io
import re

from . import domain as d


def fingerprint(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:12].upper()


def identity(record, companies):
    external = record.get('id')
    name, code = record.get('companyName'), record.get('creditCode')
    internal = bool(external and re.fullmatch(r'企业 [A-Z]+\d*|(?:QCC|FILE)-[A-F0-9]{12}', external))
    if not name and external and re.search(r'公司|企业|集团|事务所|合作社|研究院|工作室', external):
        record['companyName'] = name = external
    d.require(internal or not d.unknown(name) or not d.unknown(code), '请提供企业名称或统一社会信用代码')
    if code:
        d.valid('creditCode', code)
    if name:
        d.valid('companyName', name)
    by_id = next((c for c in companies if internal and c['id'] == external), None)
    by_code = [c for c in companies if code and c.get('creditCode') == code]
    by_name = [c for c in companies if name and c.get('companyName') == name]
    d.require(len(by_code) <= 1 and len(by_name) <= 1, '现有档案存在重复名称或信用代码，请先核对')
    matches = [c for c in [by_id, *by_code, *by_name] if c]
    d.require(len({c['id'] for c in matches}) <= 1, '编号、名称或信用代码分别对应不同企业，请先核对')
    existing = matches[0] if matches else None
    d.require(not (existing and code and existing.get('creditCode') and existing['creditCode'] != code),
              '同名企业的信用代码不一致，请先核对')
    record['id'] = existing['id'] if existing else external if internal else 'FILE-' + fingerprint(code or name)
    if external and external != record['id']:
        record['external_id'] = external
    record['is_mock'] = bool(not name and not code and record['id'].startswith('企业 '))
    return existing


def preflight(state, text, mapping):
    d.require(isinstance(text, str) and len(text.encode('utf-8')) <= 5 * 1024 * 1024, 'CSV超过5MB或内容无效')
    try:
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff')), strict=True))
    except csv.Error as exc:
        raise ValueError('CSV格式错误') from exc
    d.require(rows, '文件为空')
    d.require(isinstance(mapping, dict), '请先匹配资料项目')
    mapped = [value for value in mapping.values() if value]
    d.require(any(k in mapped for k in ('id', 'companyName', 'creditCode')), '请匹配企业名称或统一社会信用代码')
    d.require(len(mapped) == len(set(mapped)), '目标字段不可重复映射')
    d.require(all(value in d.FIELDS for value in mapped), '目标字段无效')
    d.require(all(str(key).isdigit() and int(key) < len(rows[0]) for key in mapping), '文件列匹配无效')
    working, items = copy.deepcopy(state['companies']), []
    for index, row in enumerate(rows[1:], 2):
        record = {key: row[int(column)].strip() if int(column) < len(row) else None
                  for column, key in mapping.items() if key}
        record = {key: None if d.unknown(value) else value for key, value in record.items()}
        errors = []
        if len(row) > len(rows[0]):
            errors.append('列数多于表头')
        original_source = record.get('source')
        # A source supplied in a file is an operator assertion, never a live QCC lookup.
        source_label = original_source or '操作员上传的企业文件（未提供来源说明）'
        record['source_label'] = source_label
        record['source'] = ('SIM-IMPORT' if not record.get('companyName') and not record.get('creditCode')
                            and (record.get('id') or '').startswith('企业 ') else 'FILE-' + fingerprint(source_label))
        try:
            existing = identity(record, working)
        except ValueError as exc:
            errors.append(str(exc))
            existing = None
        for key, value in record.items():
            if key not in d.FIELDS or key == 'id':
                continue
            try:
                d.valid(key, value)
            except ValueError as exc:
                errors.append(d.FIELDS[key] + '：' + str(exc))
        if original_source and len(original_source) > 2000:
            errors.append('来源说明过长')
        diff = [key for key in record if key in d.FIELDS and key not in ('id', 'source', 'sourceDate')
                and not d.unknown(record[key]) and existing and existing.get(key) != record[key]]
        status = '拒绝' if errors else '新增' if not existing else '待确认' if diff else '无变化'
        reason = '；'.join(errors) if errors else '字段差异：' + '、'.join(d.FIELDS[key] for key in diff) if diff else '新增企业档案' if not existing else '与现有资料一致'
        items.append(dict(row=index, status=status, reason=reason,
                          remedy='修正后重新上传' if errors else '到核对队列处理差异' if diff else '无需处理',
                          record=record, diff=diff))
        if status == '新增':
            working.append(record)
    return dict(batch_id=d.uid('IM'), mode='file', created_at=d.now(), base_version=state['version'],
                total=len(items), counts={key: sum(item['status'] == key for item in items)
                                        for key in ('新增', '更新', '无变化', '待确认', '拒绝')},
                items=items, status='预检', mapping=mapping)


def apply_import(state, batch):
    d.require(not any(item['batch_id'] == batch['batch_id'] for item in state['imports']), '批次已导入')
    d.require(batch['base_version'] == state['version'], '数据已变化，请重新预览核对')
    for item in batch['items']:
        record = item['record']
        if item['status'] not in ('新增', '待确认'):
            continue
        if item['status'] == '新增':
            d.require(not any(c['id'] == record['id'] for c in state['companies']), '企业已存在，请重新预览核对')
            company = {key: record.get(key) for key in d.FIELDS}
            company.update(updatedAt=d.now()[:10], is_mock=record.get('is_mock', True),
                           source_label=record.get('source_label'), external_id=record.get('external_id'))
            state['companies'].append(company)
            keys = [key for key in d.FIELDS if key not in ('id', 'source', 'sourceDate') and not d.unknown(company[key])]
        else:
            company = d.find(state, record['id'])
            keys = item['diff']
        source = record.get('source') or 'SIM-IMPORT'
        document_date = record.get('sourceDate') or (None if source.startswith('FILE-') else d.TODAY)
        for key in keys:
            note = d.FIELDS[key] + '：' + record[key]
            if item['status'] == '新增':
                evidence = d.evidence(state, company, key, record[key], note, source, document_date)
            else:
                issue = d.candidate(state, company, key, record[key], note, source, document_date)
                evidence = next(e for e in state['evidence'] if e['evidence_id'] == issue['evidence_id'])
            evidence.update(source_label=record.get('source_label'), file_row=item['row'])
    batch['status'] = '部分成功' if batch['counts']['拒绝'] or batch['counts']['待确认'] else '成功'
    state['imports'].insert(0, copy.deepcopy(batch))
    d.change(state, '导入批次', batch['batch_id'], None, batch['counts'], '文件资料经操作员确认；差异进入核对队列')
