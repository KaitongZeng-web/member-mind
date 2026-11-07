"""可选外部企业检索（企查查等）的意图结构化与候选归一化。

本轮不发起任何真实外部调用。脚本职责：
- mcp-search：把结构化外部查询意图整理好，并按宿主实际发现的工具**如实**报告能力状态
  （no_tool / unauthorized / expired / error / partial / available）。只有确有可用且已授权
  的工具才 planned；planned 只表示“宿主可代发调用”，绝不等于“已经执行/已经搜到”。
- mcp-ingest：归一化“候选回包”（可为 mock）为候选记录；candidate≠verified，
  需按唯一标识/显式选择确认；近似名不自动合并；外部新客户不自动标成会员/
  未续费/可联系；与本地档案冲突保留待核对，不覆盖。

身份键命名空间分离（禁止跨类型碰撞）：
- business_key（会员编号/客户编号）↔ 本地 business_key；
- uscc（统一社会信用代码，18 位）↔ 本地显式 uscc 字段；格式不符一律不当 USCC，
  也不得回落到会员编号命名空间去命中；
- provider_id（服务商内部 ID）↔ 本地 provider_id 字段；
- entity_id 是本地系统编号，绝不用外部值匹配。
同名多主体不取第一个：matched_entity_id 置空并返回候选列表。
mock / partial 标记从子候选与证据向上保留，绝不被顶层 false 清洗；来源未声明时
不得冒充真实外部结果。
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Optional

from ms_common import (
    MSError, EXIT_VALIDATION, norm, is_blank, make_envelope, now_iso,
)
from ms_store import Workdir

# USCC 仅做18位数字/大写字母的形式初筛，不代表完整国家标准或校验位验证：
# 回包可能来自外部系统或模拟数据，校验位不符不应阻断归一化，但格式明显不符必须标记。
_USCC_RE = re.compile(r"^[0-9A-Z]{18}$")
_ID_LEN = 10

# 与本地档案比较的字段（契约规定：仅 industry/region）
_COMPARE_FIELDS = ("industry", "region")


# ---- 身份键工具 --------------------------------------------------------------
def valid_uscc(value: Any) -> bool:
    """是否通过18位数字/大写字母形式初筛；不证明真实有效或校验位正确。"""
    if is_blank(value):
        return False
    return bool(_USCC_RE.match(str(value).strip().upper()))


def new_result_id(prefix: str, taken: Optional[set] = None) -> str:
    """碰撞安全的结果 ID：随机 uuid，避开已占用的 ID（同秒多次调用不覆盖）。"""
    taken = taken if taken is not None else set()
    while True:
        rid = "%s-%s" % (prefix, uuid.uuid4().hex[:_ID_LEN])
        if rid not in taken:
            return rid


def _index(value: Any) -> Optional[str]:
    if is_blank(value):
        return None
    return norm(value)


# ---- mcp-search：能力状态如实分流 --------------------------------------------
def _classify_tools(tools: list) -> dict:
    """按 authorized/status 如实分类；返回可用与不可用明细。

    可检索的授权状态：ok / read_only（只读不影响检索能力）。expired/error/
    未授权/未知状态都不得按可用处理。
    """
    usable, unauthorized, expired, errored = [], [], [], []
    for t in tools:
        name = t.get("name") or "?"
        authorized = bool(t.get("authorized"))
        status = t.get("status")
        rec = {"name": name, "status": status, "authorized": authorized,
               "supports_combined_search": bool(t.get("supports_combined_search"))}
        if authorized and status in ("ok", "read_only"):
            rec["usable"] = True
            if status == "read_only":
                rec["note"] = "只读(read_only)：可用于检索，不改变本地档案"
            usable.append(rec)
            continue
        rec["usable"] = False
        if status == "expired":
            rec["unusable_reason"] = "已授权但工具已过期(expired)，不能按可用处理"
            expired.append(rec)
        elif status == "error":
            rec["unusable_reason"] = "工具状态为 error，需宿主排查"
            errored.append(rec)
        elif not authorized:
            rec["unusable_reason"] = "工具未授权，需宿主发起授权"
            unauthorized.append(rec)
        else:
            rec["unusable_reason"] = "工具状态未知(%r)，不能按可用处理" % status
            errored.append(rec)
    return {"usable": usable, "unauthorized": unauthorized,
            "expired": expired, "errored": errored}


def _capability_status(classes: dict, has_tools: bool) -> str:
    if not has_tools:
        return "no_tool"
    if classes["usable"]:
        blocked = (classes["unauthorized"] + classes["expired"] + classes["errored"])
        return "partial" if blocked else "available"
    if classes["expired"]:
        return "expired"
    if classes["errored"]:
        return "error"
    return "unauthorized"


_CAP_NOTES = {
    "no_tool": ("当前宿主未发现可用的外部企业检索工具；已保留可执行查询意图，"
                "核心会员筛选/简报可继续在本地完成。未进行任何外部检索。"),
    "unauthorized": ("发现外部检索工具但未授权；需宿主发起授权，本脚本不伪造授权成功，"
                     "未进行任何外部检索。"),
    "expired": ("工具已授权但已过期(expired)：过期工具不能按可用处理，也不得记为 planned；"
                "需宿主重新授权。未进行任何外部检索。"),
    "error": ("工具状态为 error：不能按可用处理，也不得记为 planned；需宿主排查后重试。"
              "未进行任何外部检索。"),
    "partial": ("仅部分工具可用（其余未授权/过期/报错）：planned 只覆盖下列可用工具，"
                "不得声称已搜全库。本脚本不代发调用。"),
    "available": ("已整理查询意图，宿主可按可用工具代发检索；本脚本不代发调用、"
                  "不返回真实结果，因此本结果不是“已检索成功”。"),
}


def _coverage_note(tools: list, classes: dict) -> str:
    if not tools:
        return "无可用工具，覆盖范围为空。"
    usable = [t["name"] for t in classes["usable"]]
    if not usable:
        return "无可用工具（%s），覆盖范围为空。" % ", ".join(
            t["name"] for t in (classes["unauthorized"] + classes["expired"] + classes["errored"]))
    names = ", ".join(usable)
    combined = any(t.get("supports_combined_search") for t in classes["usable"])
    base = ("工具支持组合检索：%s" % names) if combined else (
        "工具 %s 未必支持组合条件全库检索；只能按单能力有限查询，"
        "不得声称已搜全库，需列明实际覆盖范围。" % names)
    blocked = classes["unauthorized"] + classes["expired"] + classes["errored"]
    if blocked:
        base += " 另有不可用工具：%s（不计入覆盖范围）。" % ", ".join(
            "%s/%s" % (t["name"], t.get("unusable_reason", "不可用")) for t in blocked)
    return base


def run_mcp_search(query: dict, available_tools: list, workdir_path: str) -> dict:
    """整理外部检索意图并如实报告工具可用性；不执行真实调用。"""
    if not isinstance(query, dict):
        raise MSError(EXIT_VALIDATION, "mcp-search 的 query 必须是对象",
                      {"type": type(query).__name__})
    if not isinstance(available_tools, list):
        raise MSError(EXIT_VALIDATION, "mcp-search 的 tools 必须是列表",
                      {"type": type(available_tools).__name__})
    for i, t in enumerate(available_tools):
        if not isinstance(t, dict):
            raise MSError(EXIT_VALIDATION, "tools[%d] 必须是对象" % i, {"index": i})

    tools = available_tools or []
    classes = _classify_tools(tools)
    external_status = _capability_status(classes, bool(tools))
    planned = external_status in ("available", "partial")

    wd = Workdir(workdir_path)
    result_id = new_result_id("mcpq", set(wd.index.get("results", {}).keys()))

    if external_status == "no_tool":
        next_actions = [
            "本地会员筛选/简报可继续完成，不依赖外部检索",
            "如需外部找客户，由宿主接入并授权相应连接器后再执行检索",
            "拿到候选回包后用 mcp-ingest 归一化并人工核对主体",
        ]
    elif external_status == "unauthorized":
        next_actions = [
            "由宿主持有凭证发起授权；脚本不代授权、不伪造授权成功",
            "授权成功后重跑 mcp-search 复查能力状态",
        ]
    elif external_status == "expired":
        next_actions = [
            "工具授权已过期：先由宿主重新授权，再重跑 mcp-search",
            "过期工具不得按可用处理，也不得记为 planned",
        ]
    elif external_status == "error":
        next_actions = [
            "工具状态为 error：由宿主排查连接器后重试",
            "排查期间不要声称已外部检索",
        ]
    elif external_status == "partial":
        next_actions = [
            "仅可用工具可 planned：%s" % (", ".join(t["name"] for t in classes["usable"]) or "无"),
            "不可用工具（未授权/过期/报错）不得计入覆盖范围",
            "拿到候选回包后用 mcp-ingest 归一化并人工核对主体",
        ]
    else:
        next_actions = [
            "由宿主按可用工具代发检索；脚本只整理意图，不代发调用",
            "planned 不等于已执行；真实回包到手后再 mcp-ingest",
            "本地已有会员请改用 filter，不必外部检索",
        ]

    env = make_envelope(
        command="mcp-search", mode="live", data_version=None,
        # 脚本没有执行任何检索：无论工具是否可用，都不是 succeeded
        status="needs_review",
        next_actions=next_actions,
        extra={
            "result_id": result_id,
            "external_status": external_status,
            "planned": planned,
            "executed": False,
            "data_returned": False,
            "structured_query": query,
            "coverage_note": _coverage_note(tools, classes),
            "tools_seen": [
                {"name": t["name"], "status": t["status"], "authorized": t["authorized"],
                 "usable": t["usable"],
                 "unusable_reason": t.get("unusable_reason")}
                for t in (classes["usable"] + classes["unauthorized"]
                          + classes["expired"] + classes["errored"])
            ],
            "usable_tools": [t["name"] for t in classes["usable"]],
            "unusable_tools": [
                {"name": t["name"], "reason": t.get("unusable_reason")}
                for t in (classes["unauthorized"] + classes["expired"] + classes["errored"])
            ],
            "is_mock": False,
            "note": _CAP_NOTES[external_status],
            "candidates": [],
        },
    )
    return env


# ---- mcp-ingest：候选归一化 ---------------------------------------------------
def _build_local_index(wd: Workdir) -> dict:
    """本地档案的身份键索引：三个键命名空间 + 名称索引（互不混用）。"""
    by_bk, by_uscc, by_pid, by_name = {}, {}, {}, {}
    for eid, e in wd.all_entities().items():
        fields = e.get("fields", {}) or {}
        bks = [e.get("business_key"), fields.get("business_key")]
        for bk in bks:
            k = _index(bk)
            if not k:
                continue
            by_bk.setdefault(k, []).append(eid)
        for key, target in (("uscc", by_uscc), ("provider_id", by_pid)):
            v = fields.get(key)
            if is_blank(v):
                v = e.get(key)
            k = _index(v)
            if k:
                target.setdefault(k, []).append(eid)
        nk = _index(fields.get("name"))
        if nk:
            by_name.setdefault(nk, []).append(eid)
    return {"business_key": by_bk, "uscc": by_uscc, "provider_id": by_pid,
            "name": by_name}


def _pick(matches: list) -> tuple:
    """唯一命中才返回；多个命中不取第一个。"""
    uniq = sorted(set(matches))
    if len(uniq) == 1:
        return uniq[0], None
    if len(uniq) > 1:
        return None, {"kind": "ambiguous_id_match",
                      "reason": "同一标识命中多个本地主体（%s），不自动选定" % ", ".join(uniq),
                      "blocking": False, "entity_ids": uniq}
    return None, None


def _evidence_is_mock(evidence: Any) -> Optional[bool]:
    if isinstance(evidence, dict) and "is_mock" in evidence:
        v = evidence.get("is_mock")
        return bool(v) if isinstance(v, bool) else None
    return None


def _declared_bool(payload: dict, key: str) -> Optional[bool]:
    if key not in payload or payload.get(key) is None:
        return None
    v = payload.get(key)
    if not isinstance(v, bool):
        raise MSError(EXIT_VALIDATION, "payload.%s 必须是布尔值" % key,
                      {"key": key, "type": type(v).__name__})
    return v


def run_mcp_ingest(payload: dict, workdir_path: str) -> dict:
    """归一化候选回包；区分 mock/真实、候选/已确认、外部新客户/本地匹配。"""
    if not isinstance(payload, dict):
        raise MSError(EXIT_VALIDATION, "mcp-ingest 的 payload 必须是对象",
                      {"type": type(payload).__name__})
    candidates_in = payload.get("candidates", [])
    if candidates_in is None:
        candidates_in = []
    if not isinstance(candidates_in, list):
        raise MSError(EXIT_VALIDATION, "payload.candidates 必须是列表",
                      {"type": type(candidates_in).__name__})

    payload_mock = _declared_bool(payload, "is_mock")
    payload_partial = _declared_bool(payload, "partial")
    source_tool = payload.get("source_tool")
    if source_tool is not None and not isinstance(source_tool, str):
        raise MSError(EXIT_VALIDATION, "payload.source_tool 必须是字符串",
                      {"type": type(source_tool).__name__})
    source_known = bool(source_tool) and norm(source_tool) not in ("unknown", "none", "null")

    wd = Workdir(workdir_path)
    index = _build_local_index(wd)

    candidates = []
    issues = []
    seen_ids = set()
    pending_mapping_entities = {}

    for pos, c in enumerate(candidates_in):
        if not isinstance(c, dict):
            raise MSError(EXIT_VALIDATION, "candidates[%d] 必须是对象" % pos, {"index": pos})
        cand_id = c.get("candidate_id")
        if cand_id is not None and not isinstance(cand_id, str):
            raise MSError(EXIT_VALIDATION, "candidates[%d].candidate_id 必须是字符串" % pos,
                          {"index": pos})
        name = c.get("name")
        if name is not None and not isinstance(name, str):
            raise MSError(EXIT_VALIDATION, "candidates[%d].name 必须是字符串" % pos, {"index": pos})
        if is_blank(name):
            issues.append({"kind": "missing_name", "index": pos,
                           "candidate_id": cand_id,
                           "reason": "候选缺少名称，无法归一化匹配，已跳过该候选", "blocking": False})
            continue
        if cand_id:
            if cand_id in seen_ids:
                issues.append({"kind": "duplicate_candidate_id", "candidate_id": cand_id,
                               "reason": "回包内 candidate_id 重复，后者仍单独记录但不合并",
                               "blocking": False})
            seen_ids.add(cand_id)
        cand_id = cand_id or new_result_id("cand", seen_ids)
        seen_ids.add(cand_id)

        # ---- 身份键：三个命名空间分别取用，互不回落 ----
        uscc = c.get("uscc")
        bk = c.get("business_key")
        pid = c.get("provider_id")
        uscc_ok = valid_uscc(uscc)
        if not is_blank(uscc) and not uscc_ok:
            issues.append({
                "kind": "invalid_uscc_format", "candidate_id": cand_id, "uscc": uscc,
                "reason": ("uscc 值不是合法的统一社会信用代码格式（18 位数字/大写字母）；"
                           "已拒绝用它匹配任何本地编号，也不回落到会员编号命名空间"),
                "blocking": False,
            })

        relation = "external_new"
        ver_state = "unverified"
        matched = None
        key_type = None
        for key, value, ok in (("provider_id", pid, True), ("uscc", uscc, uscc_ok),
                               ("business_key", bk, True)):
            if is_blank(value) or not ok:
                continue
            hit, amb = _pick(index[key].get(norm(value), []))
            if amb:
                amb = dict(amb)
                amb["candidate_id"] = cand_id
                amb["key_type"] = key
                issues.append(amb)
                break
            if hit:
                matched, key_type = hit, key
                relation = "matches_local_by_id"
                ver_state = "id_match_pending_human_confirm"
                break

        # ---- 名字匹配：只是候选，多主体不取第一个 ----
        name_matches = index["name"].get(norm(name), [])
        if matched is None and name_matches:
            relation = "possible_local_match_by_name"
            ver_state = "name_match_not_merged"
            if len(name_matches) == 1:
                matched = name_matches[0]
                key_type = "name"
            else:
                issues.append({
                    "kind": "ambiguous_name", "candidate_id": cand_id, "name": name,
                    "reason": "本地存在 %d 个同名主体（%s），不自动合并、不取第一个，返回候选列表"
                              % (len(name_matches), ", ".join(name_matches)),
                    "blocking": False, "entity_ids": name_matches,
                })

        # ---- 冲突比较：仅唯一标识命中时；本地 canonical 缺失但有未映射列时不得宣称无冲突 ----
        conflict = None
        conflict_check = "no_local_match"
        field_differences = None
        unmapped_local = []
        mapping_pending = False
        if matched:
            ent = wd.get_entity(matched) or {}
            ent_fields = ent.get("fields", {}) or {}
            unmapped_local = sorted(k for k in ent_fields if k.startswith("custom:"))
            not_compared = [f for f in _COMPARE_FIELDS if is_blank(ent_fields.get(f))]
            if key_type == "name":
                conflict_check = "name_match_not_compared"
                diffs = []
                for f in _COMPARE_FIELDS:
                    lv, cv = ent_fields.get(f), c.get(f)
                    if not is_blank(lv) and not is_blank(cv) and norm(lv) != norm(cv):
                        diffs.append({"field": f, "local": lv, "external": cv,
                                      "note": "名字匹配未确证主体，差异仅供参考；确认主体后按冲突处理"})
                field_differences = diffs or None
                # 名字匹配本就不比较冲突；若本地值还在未映射列里，必须说明“无法比较”，
                # 不能留空让人以为已经核对过没有差异。
                if unmapped_local and not_compared:
                    mapping_pending = True
                    pending_mapping_entities.setdefault(matched, unmapped_local)
                    issues.append({
                        "kind": "pending_field_mapping", "candidate_id": cand_id,
                        "entity_id": matched, "fields": not_compared,
                        "unmapped_local_columns": unmapped_local,
                        "reason": ("名字匹配未合并，且本地 %s 可能位于未映射列 %s 中：无法比较外部候选，"
                                   "不得视为无差异；需宿主建议显式映射后重导再核对"
                                   % (", ".join(not_compared), ", ".join(unmapped_local))),
                        "blocking": False,
                    })
            else:
                items = []
                pending = []
                for f in _COMPARE_FIELDS:
                    lv, cv = ent_fields.get(f), c.get(f)
                    if is_blank(lv):
                        if unmapped_local:
                            pending.append(f)
                        continue
                    if not is_blank(cv) and norm(lv) != norm(cv):
                        items.append({"field": f, "local": lv, "external": cv,
                                      "resolution": "保留本地档案，冲突待人工核对，不覆盖"})
                conflict = items or None
                if pending:
                    conflict_check = "incomplete_pending_mapping"
                    mapping_pending = True
                    pending_mapping_entities.setdefault(matched, unmapped_local)
                    issues.append({
                        "kind": "pending_field_mapping", "candidate_id": cand_id,
                        "entity_id": matched, "fields": pending,
                        "unmapped_local_columns": unmapped_local,
                        "reason": ("本地档案的 %s 未映射（存在未映射列 %s）：不能宣称与外部候选"
                                   "无冲突，需宿主建议显式映射后重导再核对"
                                   % (", ".join(pending), ", ".join(unmapped_local))),
                        "blocking": False,
                    })
                elif conflict:
                    conflict_check = "compared"
                else:
                    conflict_check = "compared_no_conflict"

        # ---- mock / partial 逐候选保留，不被顶层 false 清洗 ----
        ev_mock = _evidence_is_mock(c.get("evidence"))
        declared_mock = c.get("is_mock")
        if declared_mock is not None and not isinstance(declared_mock, bool):
            raise MSError(EXIT_VALIDATION,
                          "candidates[%d].is_mock 必须是布尔值" % pos, {"index": pos})
        # 任一层（回包/候选/证据）声明 mock 即为 mock；顶层明确声明非 mock 且子层无相反
        # 标记时继承该声明；顶层未声明时不冒充真实外部结果。
        if payload_mock or declared_mock or ev_mock:
            cand_mock = True
        elif payload_mock is False or declared_mock is False:
            cand_mock = False
        else:
            cand_mock = None
        cand_partial = bool(c.get("partial")) if "partial" in c else bool(payload_partial)

        rec = {
            "candidate_id": cand_id,
            "name": name,
            "region": c.get("region"),
            "industry": c.get("industry"),
            "uscc": uscc,
            "uscc_format_valid": uscc_ok if not is_blank(uscc) else None,
            "business_key": bk,
            "provider_id": pid,
            "is_mock": cand_mock,
            "partial": cand_partial,
            "verification_state": ver_state,
            "relation_to_local": relation,
            "matched_entity_id": matched,
            "matched_key_type": key_type,
            "name_match_candidates": name_matches or None,
            # 外部候选绝不自动赋予下列会员属性
            "member_status": None, "renewal_status": None, "contactable_status": None,
            "membership_note": "外部候选不自动视为会员/未续费/可联系，需独立凭证",
            "evidence": c.get("evidence", {}),
            "confidence": c.get("confidence"),
            "conflict": conflict,
            "conflict_check": conflict_check,
            "field_differences": field_differences,
            "unmapped_local_columns": unmapped_local or None,
            "mapping_pending": mapping_pending,
        }
        candidates.append(rec)

    # ---- 顶层 mock / partial：任何一层为 mock 即为 mock，不用 false 覆盖 ----
    any_mock = payload_mock is True or any(
        c["is_mock"] is True or _evidence_is_mock(c.get("evidence")) is True for c in candidates)
    all_declared_real = payload_mock is False and all(
        c["is_mock"] is False for c in candidates) if candidates else payload_mock is False
    if any_mock:
        top_mock, mode = True, "mock"
    elif all_declared_real and source_known:
        top_mock, mode = False, "live"
    else:
        # 来源未声明/未知：不得冒充真实外部结果
        top_mock, mode = None, "unknown"
        issues.append({
            "kind": "provenance_unknown",
            "reason": ("回包未明确声明 is_mock=false 或 source_tool 未知，无法确认是否为真实外部"
                       "结果；不得据此宣称已真实查询企查查"),
            "blocking": False,
        })

    top_partial = bool(payload_partial) or any(c["partial"] for c in candidates)
    if payload_partial:
        issues.append({"kind": "partial_result",
                       "reason": "回包为部分结果，可能遗漏候选，不得视为全量", "blocking": False})
    if top_mock:
        issues.append({"kind": "mock_payload",
                       "reason": "本回包为模拟(mock)数据，非真实企查查查询结果", "blocking": False})

    result_id = new_result_id("mpci", set(wd.index.get("results", {}).keys()))

    env = make_envelope(
        command="mcp-ingest", mode=mode, data_version=wd.data_version,
        status="needs_review",
        items=candidates, issues=issues,
        next_actions=[
            "人工按唯一标识(USCC/会员编号/服务商ID)或显式选择确认候选主体",
            "确认后才可据独立凭证决定是否建档/联系；候选≠已核实主体",
            "本地档案字段冲突保留待核对，不覆盖；存在未映射列时先补字段映射",
        ],
        extra={
            "result_id": result_id,
            "is_mock": top_mock,
            "partial": top_partial,
            "source_tool": source_tool if source_tool is not None else "unknown",
            "provenance": ("declared_mock" if top_mock else
                           ("declared_external" if top_mock is False else "unknown")),
            "candidates": candidates,
            "pending_mapping_entities": {eid: cols for eid, cols
                                         in sorted(pending_mapping_entities.items())},
            "disclaimer": ("mock 回包全程标记为模拟；未进行真实企查查查询。"
                           "候选搜索≠已核实主体；外部新客户不自动成为会员/可联系。"),
        },
    )
    return env
