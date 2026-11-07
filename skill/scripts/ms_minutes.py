"""纪要结构化：宿主提取 → 脚本保留原文引用并生成待核对版；人员核对后才成事实。

- extract：校验每条抽取的 quote 是否真实存在于原始纪要（防幻觉引用），
  输出 status=draft 的待核对版；推断永标记为 inference。
- confirm：按人员对具体条目的确认/否决/更正，生成已核对版；
  确认针对某一 extraction_version；否决与推断不会变成事实。
- 纪要内容一律按数据处理：其中嵌入的“更改规则/指令”文本只标记待审，绝不执行。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ms_common import (
    MSError, EXIT_NOT_FOUND, EXIT_VALIDATION, now_iso, short_hash,
    read_text, write_text_atomic, safe_join, make_envelope,
    gen_id, stable_id, norm,
)
from ms_store import Workdir

_ITEM_TYPES = {"customer_statement", "need", "commitment", "open_question",
               "inference", "no_contact"}

# 透明可见的“材料内指令”启发式标记（仅用于提醒人工复核，绝不据此改变行为）
_INJECTION_MARKERS = [
    "忽略以上", "忽略之前", " disregard", "ignore previous", "override",
    "更改规则", "修改规则", "系统指令", "system instruction", "你必须", "you must",
    "delete all", "删除所有", "导出到", "发送到", "调用工具",
]


def _find_quote_lines(raw: str, quote: str) -> Optional[dict]:
    if not quote:
        return None
    idx = raw.find(quote)
    if idx < 0:
        # 规范化空白后再找一次
        norm_raw = re.sub(r"\s+", " ", raw)
        norm_q = re.sub(r"\s+", " ", quote)
        if norm_q and norm_q in norm_raw:
            return {"verified": True, "approx": True, "line_start": None, "line_end": None}
        return {"verified": False, "line_start": None, "line_end": None}
    line_start = raw.count("\n", 0, idx) + 1
    end_idx = idx + len(quote) - 1
    line_end = raw.count("\n", 0, end_idx) + 1
    return {"verified": True, "line_start": line_start, "line_end": line_end}


def _scan_injection(raw: str) -> list:
    flags = []
    for i, line in enumerate(raw.splitlines(), 1):
        low = line.lower()
        for marker in _INJECTION_MARKERS:
            if marker in low:
                flags.append({"line": i, "marker": marker,
                              "note": "材料内含疑似指令性文本，已按数据保留，未执行"})
                break
    return flags


def _valid_iso_date(s: str) -> bool:
    import datetime as _dt
    try:
        _dt.date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


def _items_signature(items: list) -> str:
    """对规范化条目做确定性签名，用于内容派生 visit_id（A2 版本分离）。"""
    import json as _json
    norm_items = []
    for it in items:
        norm_items.append({
            "id": it["id"], "type": it["type"], "text": it.get("text", ""),
            "quote": it.get("quote", ""), "owner": it.get("owner"),
            "scope": it.get("scope"),
        })
    norm_items.sort(key=lambda x: x["id"])
    return short_hash(_json.dumps(norm_items, ensure_ascii=False, sort_keys=True), 16)


_IDENTITY_KEYS = {
    "business_key": "business_key", "会员编号": "business_key", "客户编号": "business_key",
    "uscc": "uscc", "统一社会信用代码": "uscc",
    "provider_id": "provider_id", "服务商编号": "provider_id",
    "name": "name", "企业名称": "name",
}
_BUCKETS = ("customer_statements", "needs", "commitments", "open_questions",
            "inferences", "no_contact")


def _validate_identity(wd: Workdir, entity_id: Optional[str], evidence: Any, raw: str) -> None:
    """只验证宿主提供的结构化身份依据，不猜测自然语言中的企业。"""
    if not entity_id:
        return  # 明确未关联的纯纪要入口。
    entity = wd.get_entity(entity_id)
    if entity is None:
        raise MSError(EXIT_NOT_FOUND, "关联主体不在档案中", {"entity_id": entity_id})
    if entity.get("status") == "conflicted":
        raise MSError(EXIT_VALIDATION, "主体档案存在未解决冲突，不可关联正式纪要",
                      {"entity_id": entity_id})
    if not isinstance(evidence, dict):
        raise MSError(EXIT_VALIDATION, "关联主体必须提供可定位的 identity_evidence", {})
    key_type = evidence.get("key_type")
    field = _IDENTITY_KEYS.get(key_type) if isinstance(key_type, str) else None
    value, quote = evidence.get("value"), evidence.get("raw_citation")
    if field is None or not isinstance(value, str) or not value.strip():
        raise MSError(EXIT_VALIDATION, "身份依据的 key_type/value 无效", {"key_type": key_type})
    if not isinstance(quote, str) or not quote.strip() or not (_find_quote_lines(raw, quote) or {}).get("verified"):
        raise MSError(EXIT_VALIDATION, "身份依据 raw_citation 未在原文定位", {})
    if norm(value) not in norm(quote):
        raise MSError(EXIT_VALIDATION, "身份引用未包含所声明的标识值", {"key_type": key_type})
    actual = entity.get("fields", {}).get(field)
    if field == "business_key":
        actual = entity.get("business_key") or actual
    if actual is None or norm(value) != norm(actual):
        raise MSError(EXIT_VALIDATION, "身份依据与选定主体不一致",
                      {"entity_id": entity_id, "key_type": key_type,
                       "given": value, "selected_value": actual})
    # 名称不是唯一键；有重名时必须改用明确编号。
    if field == "name" and sum(norm(e.get("fields", {}).get("name")) == norm(value)
                               for e in wd.all_entities().values()) != 1:
        raise MSError(EXIT_VALIDATION, "同名主体不能仅凭名称关联，请提供明确编号", {})


def validate_visit_evidence(wd: Workdir, visit: dict) -> str:
    """确认、计划和任务编辑共用的只读证据门槛；失败不写任何文件。"""
    raw_path = visit.get("raw_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise MSError(EXIT_VALIDATION, "纪要缺少原文快照路径", {})
    raw = read_text(safe_join(wd.path, raw_path))
    actual_hash = short_hash(raw, 16)
    if actual_hash != visit.get("raw_hash"):
        raise MSError(EXIT_VALIDATION, "原文快照内容已改变，请重新抽取并核对",
                      {"visit_id": visit.get("visit_id"), "actual_hash": actual_hash,
                       "stored_hash": visit.get("raw_hash")})
    _validate_identity(wd, visit.get("entity_id"), visit.get("identity_evidence"), raw)
    for key in _BUCKETS:
        for item in visit.get(key, []):
            if item.get("status") == "confirmed" and not (
                    _find_quote_lines(raw, item.get("quote", "")) or {}).get("verified"):
                raise MSError(EXIT_VALIDATION, "已确认条目的引用不再有效",
                              {"item_id": item.get("id"), "visit_id": visit.get("visit_id")})
    return raw


def run_extract(raw_path: str, extraction: dict, workdir_path: str) -> dict:
    if not isinstance(extraction, dict):
        raise MSError(EXIT_VALIDATION, "extraction 必须是对象", {})
    raw = read_text(raw_path)
    raw_hash = short_hash(raw, 16)
    items_in = extraction.get("items", [])
    if not isinstance(items_in, list):
        raise MSError(EXIT_VALIDATION, "extraction.items 必须是列表", {})

    wd = Workdir(workdir_path)
    entity_id = extraction.get("entity_id")
    if entity_id and wd.get_entity(entity_id) is None:
        raise MSError(EXIT_NOT_FOUND, "extraction.entity_id 不在档案中: %s" % entity_id,
                      {"entity_id": entity_id})

    minutes_date = extraction.get("minutes_date")
    if minutes_date and not _valid_iso_date(str(minutes_date)):
        # A6：日期在任何落盘前校验，无效直接拒绝，不改变已有状态
        raise MSError(EXIT_VALIDATION, "minutes_date 非法（非有效日历日期）: %r" % minutes_date,
                      {"minutes_date": minutes_date})

    identity_evidence = extraction.get("identity_evidence")
    _validate_identity(wd, entity_id, identity_evidence, raw)
    # 来源模式：显式声明优先，但**继承优先于降级**——档案/工作区已是 mock 时，
    # 即使本次抽取写 is_mock=false 也不得把纪要降回 live（粘性，见 ms_store.source_mode）。
    declared = extraction.get("is_mock")
    if declared is not None and not isinstance(declared, bool):
        raise MSError(EXIT_VALIDATION, "extraction.is_mock 必须是布尔值",
                      {"type": type(declared).__name__})
    mode = wd.source_mode(entity_id, extra_mock=bool(declared))

    # 分配条目 ID：显式 id 必须唯一；缺省按“类型+文本+引用”确定性生成（同输入重放稳定，
    # 同批碰撞按序号区分），保证唯一且可复现（A2）。
    provided = [it.get("id") for it in items_in if isinstance(it, dict) and it.get("id")]
    if len(provided) != len(set(provided)):
        dup = sorted({i for i in provided if provided.count(i) > 1})
        raise MSError(EXIT_VALIDATION, "抽取条目 id 重复: %s" % ", ".join(map(str, dup)),
                      {"duplicate_ids": dup})
    seen_ids = set(provided)
    normalized = []
    for it in items_in:
        if not isinstance(it, dict):
            raise MSError(EXIT_VALIDATION, "extraction.items 元素必须是对象", {})
        iid = it.get("id")
        if not iid:
            base = short_hash("%s|%s|%s" % (it.get("type"), it.get("text", ""), it.get("quote", "")), 8)
            iid, n = base, 1
            while iid in seen_ids:
                iid, n = "%s-%d" % (base, n), n + 1
            seen_ids.add(iid)
        normalized.append({"id": iid, "type": it.get("type"), "text": it.get("text", ""),
                           "quote": it.get("quote", ""), "owner": it.get("owner"),
                           "scope": it.get("scope")})

    # 内容派生 visit_id：相同输入→同一版本（重放幂等）；内容/主体/日期/原文改变→新版本（A2）
    extraction_hash = _items_signature(normalized)
    visit_id = stable_id("vis", raw_hash, entity_id or "-", minutes_date or "-", extraction_hash)

    if visit_id in wd.index["visits"]:
        # 相同输入重放：返回已有版本，绝不覆盖旧确认（A2）
        existing = wd.index["visits"][visit_id]
        validate_visit_evidence(wd, existing)
        from ms_export import write_minutes_outputs
        paths = write_minutes_outputs(wd, visit_id, existing)
        all_items = [r for k in ("customer_statements", "needs", "commitments",
                                 "open_questions", "inferences", "no_contact")
                     for r in existing.get(k, [])]
        return make_envelope(
            command="minutes-extract", mode=existing.get("mode", mode),
            data_version=wd.data_version, status="succeeded",
            items=all_items, issues=[],
            next_actions=["该抽取已存在，返回既有版本（未覆盖旧确认）"],
            extra={"visit_id": visit_id, "entity_id": existing.get("entity_id"),
                   "minutes_date": existing.get("minutes_date"),
                   "extraction_version": existing.get("extraction_version", 1),
                   "extraction_hash": extraction_hash, "mode": existing.get("mode", mode),
                   "is_mock": existing.get("mode", mode) == "mock",
                   "outputs": paths, "no_contact": existing.get("no_contact", []),
                   "visit_status": existing.get("status"),
                   "idempotent_replay": True,
                   "identity_evidence": existing.get("identity_evidence")},
        )

    raw_store = safe_join(wd.results_dir, "%s_minutes.txt" % visit_id)
    write_text_atomic(raw_store, raw)

    buckets = {"customer_statements": [], "needs": [], "commitments": [],
               "open_questions": [], "inferences": [], "no_contact": []}
    key_by_type = {
        "customer_statement": "customer_statements", "need": "needs",
        "commitment": "commitments", "open_question": "open_questions",
        "inference": "inferences", "no_contact": "no_contact",
    }
    issues = []
    for it in normalized:
        itype = it["type"]
        if itype not in _ITEM_TYPES:
            issues.append({"kind": "unknown_item_type", "id": it["id"],
                           "reason": "未知条目类型: %r" % itype, "blocking": True})
            continue
        quote = it["quote"]
        loc = _find_quote_lines(raw, quote) or {"verified": False}
        citation = {
            "file": "results/%s_minutes.txt" % visit_id,
            "line_start": loc.get("line_start"),
            "line_end": loc.get("line_end"),
            "verified": loc.get("verified"),
        }
        if quote and citation["verified"] is False:
            issues.append({"kind": "quote_not_located", "id": it["id"],
                           "reason": "引用未在原始纪要定位，可能是幻觉引用，需人工核对", "blocking": False})
        record = {
            "id": it["id"],
            "type": itype, "text": it["text"], "quote": quote,
            "owner": it["owner"], "citation": citation,
            "scope": it["scope"],
            "status": "inference" if itype == "inference" else "pending",
        }
        buckets[key_by_type[itype]].append(record)

    inj = _scan_injection(raw)
    for f in inj:
        issues.append({"kind": "embedded_instruction", "line": f["line"],
                       "reason": f["note"], "blocking": False})

    visit = {
        "visit_id": visit_id,
        "entity_id": entity_id,
        "minutes_date": minutes_date,
        "raw_path": "results/%s_minutes.txt" % visit_id,
        "raw_hash": raw_hash,
        "extraction_hash": extraction_hash,
        "extraction_version": 1,
        "mode": mode,
        "identity_evidence": identity_evidence,
        "status": "draft",
        "created_at": now_iso(),
        "confirmed_by": None, "confirmed_at": None, "confirmed_extraction_version": None,
        "injection_flags": inj,
        **buckets,
    }
    wd.index["visits"][visit_id] = visit
    wd.save()

    from ms_export import write_minutes_outputs
    paths = write_minutes_outputs(wd, visit_id, visit)

    env = make_envelope(
        command="minutes-extract", mode=mode, data_version=wd.data_version,
        status="needs_review",
        items=[r for b in buckets.values() for r in b],
        issues=issues,
        next_actions=["人工逐条核对后再用 minutes-confirm 确认",
                      "确认后的需求/承诺才可用于 plan",
                      "确认请核对上方展示的主体与 identity_evidence 原文依据"],
        extra={"visit_id": visit_id, "entity_id": entity_id,
               "minutes_date": minutes_date, "extraction_version": 1,
               "extraction_hash": extraction_hash, "mode": mode,
               "is_mock": mode == "mock",
               "mode_note": ("来源模式继承自导入/工作区声明：本纪要按 mock 处理，"
                             "只表示资料来源，不作为真实承诺。"
                             if mode == "mock" else
                             "来源模式按真实本地数据处理；mode 只表示资料来源。"),
               "identity_evidence": identity_evidence,
               "outputs": paths, "no_contact": buckets["no_contact"],
               "visit_status": "draft"},
    )
    return env


def _visit_status_after(items: list) -> str:
    """根据非推断条目的核对情况判定整份纪要状态（A1）。"""
    non_inf = [i for i in items if i.get("type") != "inference"]
    pending = [i for i in non_inf if i.get("status") == "pending"]
    confirmed = [i for i in non_inf if i.get("status") == "confirmed"]
    if pending:
        return "partially_confirmed" if confirmed else "draft"
    return "confirmed" if confirmed else "draft"


def run_confirm(visit_id: str, confirmations: dict, workdir_path: str) -> dict:
    if not isinstance(confirmations, dict):
        raise MSError(EXIT_VALIDATION, "confirmations 必须是对象", {})
    wd = Workdir(workdir_path)
    visit = wd.index["visits"].get(visit_id)
    if visit is None:
        raise MSError(EXIT_NOT_FOUND, "未找到拜访记录: %s" % visit_id, {"visit_id": visit_id})
    mode = visit.get("mode") or wd.source_mode(visit.get("entity_id"))

    # A2：可选的版本/原文校验——过期版本或原文已变一律拒绝，保留原记录
    exp_ver = confirmations.get("expected_extraction_version")
    if exp_ver is not None and (isinstance(exp_ver, bool) or
                               not isinstance(exp_ver, (str, int)) or
                               not str(exp_ver).isdigit()):
        raise MSError(EXIT_VALIDATION, "expected_extraction_version 必须为整数版本", {})
    if exp_ver is not None and int(exp_ver) != int(visit.get("extraction_version", 1)):
        raise MSError(EXIT_VALIDATION,
                      "确认针对的抽取版本不匹配（期望 %s，实际 %s）"
                      % (exp_ver, visit.get("extraction_version")),
                      {"expected": exp_ver, "actual": visit.get("extraction_version")})
    if confirmations.get("raw_hash") and confirmations["raw_hash"] != visit.get("raw_hash"):
        raise MSError(EXIT_VALIDATION, "原始纪要内容已改变（hash 不符），拒绝在旧版本上确认",
                      {"given": confirmations["raw_hash"], "stored": visit.get("raw_hash")})

    raw = validate_visit_evidence(wd, visit)
    if visit.get("status") == "confirmed":
        # 幂等也必须先通过实际证据、主体及请求版本核验。
        return make_envelope(command="minutes-confirm", mode=mode,
                             data_version=wd.data_version, status="succeeded",
                             extra={"visit_id": visit_id, "visit_status": "confirmed",
                                    "note": "该纪要已确认，重复确认不改变状态"})
    confirmed_by = confirmations.get("confirmed_by")
    if not confirmed_by or not str(confirmed_by).strip():
        raise MSError(EXIT_VALIDATION, "确认必须提供 confirmed_by（核对人）", {})

    decisions_in = confirmations.get("decisions", [])
    if not isinstance(decisions_in, list):
        raise MSError(EXIT_VALIDATION, "decisions 必须是列表", {})

    all_items = []
    for key in ("customer_statements", "needs", "commitments", "open_questions",
                "inferences", "no_contact"):
        all_items.extend(visit.get(key, []))
    item_ids = {i["id"] for i in all_items}

    # A1/A2：决策必须引用唯一条目 ID；未知 ID、重复 ID、未知决定一律拒绝，不静默忽略
    seen_dec = set()
    for d in decisions_in:
        did = d.get("id")
        if did not in item_ids:
            raise MSError(EXIT_VALIDATION, "确认决策引用了不存在的条目 ID: %r" % did,
                          {"unknown_id": did})
        if did in seen_dec:
            raise MSError(EXIT_VALIDATION, "确认决策重复引用同一 ID: %r" % did, {"duplicate_id": did})
        seen_dec.add(did)
        if d.get("decision") not in ("confirm", "reject", "edit"):
            raise MSError(EXIT_VALIDATION, "未知确认决定: %r（ID %s）" % (d.get("decision"), did),
                          {"allowed": ["confirm", "reject", "edit"]})
    decisions = {d["id"]: d for d in decisions_in}

    confirmed_needs, confirmed_commitments = [], []
    issues = []
    for item in all_items:
        dec = decisions.get(item["id"])
        if item["type"] == "inference":
            # 推断永不因确认变成事实；只有显式 confirm 才升级为“已核对推断”
            if dec and dec.get("decision") in ("confirm", "edit"):
                item["status"] = "confirmed_inference"
            else:
                item["status"] = "inference"
            continue
        # 无决定：保留已确认/已否决，否则仍待核对（支持逐项部分核对，A1）
        if not dec:
            if item.get("status") not in ("confirmed", "rejected"):
                item["status"] = "pending"
            continue
        d = dec.get("decision")
        if d in ("confirm", "edit"):
            # A1：无可定位引用/无引用的条目不能被确认成事实（防幻觉引用冒充实测）
            if (item.get("citation", {}).get("verified") is not True or
                    not (_find_quote_lines(raw, item.get("quote", "")) or {}).get("verified")):
                item["status"] = "pending"
                issues.append({"kind": "confirm_refused_unverified", "id": item["id"],
                               "reason": "该条引用未在原文定位（或无引用），不能确认为事实；"
                                         "补充新事实需提供新的明确来源并重新抽取核对",
                               "blocking": True})
                continue
            item["status"] = "confirmed"
            if d == "edit" and dec.get("edit"):
                item["text"] = dec["edit"]
                item["edited"] = True
            if item["type"] == "need":
                confirmed_needs.append(item)
            elif item["type"] == "commitment":
                confirmed_commitments.append(item)
        elif d == "reject":
            item["status"] = "rejected"

    new_status = _visit_status_after(all_items)
    visit["status"] = new_status
    visit["confirmed_by"] = confirmed_by
    visit["confirmed_at"] = now_iso()
    visit["confirmed_extraction_version"] = visit.get("extraction_version", 1)
    wd.save()

    from ms_export import write_minutes_outputs
    paths = write_minutes_outputs(wd, visit_id, visit)

    return make_envelope(
        command="minutes-confirm", mode=mode, data_version=wd.data_version,
        status="succeeded",
        items=confirmed_needs + confirmed_commitments, issues=issues,
        next_actions=["用 plan 子命令把已确认需求/承诺转成后续行动",
                      "仅已确认且引用有效的条目可作为正式来源"],
        extra={"visit_id": visit_id, "entity_id": visit.get("entity_id"),
               "minutes_date": visit.get("minutes_date"), "visit_status": new_status,
               "confirmed_by": confirmed_by, "outputs": paths,
               "is_mock": mode == "mock",
               "confirmed_needs": confirmed_needs,
               "confirmed_commitments": confirmed_commitments,
               "no_contact": [n for n in visit.get("no_contact", [])
                              if n.get("status") == "confirmed"]},
    )
