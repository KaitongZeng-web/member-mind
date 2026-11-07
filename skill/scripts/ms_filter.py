"""自然语言条件 → 显式条件树的三值逻辑筛选。

条件树由宿主（模型）从自然语言生成，本脚本只做可复核计算，不含任何
“关键词→字段”的自然语言解析（不伪装通用 NL 能力）。

三值逻辑：TRUE / FALSE / UNKNOWN。
- 字段缺失记 UNKNOWN，NOT(UNKNOWN)=UNKNOWN，缺失不会因 NOT 变真。
- exists/not_exists 反映“有无值”这一已知事实，不因缺失返回 UNKNOWN。
先硬排除，再按筛选条件分 eligible / excluded / needs_review。

解释结构（Astra P2.1 / Terra P2）：保留**带节点结果的三值解释树**，
排除理由只报告**真正决定根节点结果**的分支：
- AND 为 FALSE → 只列结果为 FALSE 的子条件（不是所有 FALSE 叶子平铺）；
- OR 为 FALSE → 所有子条件都决定性，未采用的 TRUE 分支不会被当成排除理由；
- NOT 内部为 FALSE → 说明“not(...) 未成立”及其内部实际值，而不是把 NOT
  子条件的 FALSE 叶子误报成排除原因。
UNKNOWN 同样只报决定性未知分支；硬排除优先于筛选条件，且硬排除未知时
不得直接判定“未被排除”。每个结果 ID 用随机 uuid 生成并避开已占用 ID，
同秒多次筛选不会共用一个结果路径。
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Optional

from ms_common import MSError, EXIT_VALIDATION, norm, is_blank, make_envelope
from ms_schema import CANONICAL_FIELDS
from ms_store import Workdir

T, F, U = "TRUE", "FALSE", "UNKNOWN"

_ALLOWED_OPS = {"eq", "ne", "in", "not_in", "contains", "gte", "lte", "gt", "lt",
                "exists", "not_exists"}
_VALUE_OPS = {"eq", "ne", "in", "not_in", "contains", "gte", "lte", "gt", "lt"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_ID_LEN = 10

# 筛选请求允许的顶层字段。除此之外的字段不静默忽略：作为 unused_request_field
# issue 上报（随包示例与真实请求常带 scope 这类说明性文字，不阻断计算）。
_REQUEST_KEYS = {
    "conditions", "hard_exclusions", "rule_version", "rule_is_mock", "sort_by",
    "scope", "note", "notes", "comment", "description", "purpose",
    "requested_by", "operator", "as_of", "name", "title", "tags",
}

PRIORITY_DISCLAIMER = (" eligible 内的排序仅为运营处理顺序建议，"
                       "不代表续费意愿、成交概率或客户意图。")


def _to_num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if _NUM.match(s):
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _cmp(a: Any, b: Any) -> Optional[int]:
    fa, fb = _to_num(a), _to_num(b)
    if fa is not None and fb is not None:
        return (fa > fb) - (fa < fb)
    sa, sb = str(a).strip(), str(b).strip()
    if _ISO_DATE.match(sa) and _ISO_DATE.match(sb):
        return (sa > sb) - (sa < sb)
    na, nb = norm(a), norm(b)
    if na and nb:
        return (na > nb) - (na < nb)
    return None


def validate_condition(node: Any, known_fields: set, path: str = "conditions") -> None:
    """校验条件树结构；非法即抛错（不静默忽略）。"""
    if not isinstance(node, dict):
        raise MSError(EXIT_VALIDATION, "条件节点必须是对象: %s" % path, {"path": path})
    if "logic" in node:
        logic = node.get("logic")
        if logic not in ("and", "or", "not"):
            raise MSError(EXIT_VALIDATION, "非法逻辑运算符: %r" % logic, {"path": path})
        children = node.get("conditions")
        if not isinstance(children, list) or not children:
            raise MSError(EXIT_VALIDATION, "逻辑节点缺少 conditions 列表: %s" % path, {"path": path})
        if logic == "not" and len(children) != 1:
            raise MSError(EXIT_VALIDATION, "NOT 节点只能有一个子条件: %s" % path, {"path": path})
        for i, child in enumerate(children):
            validate_condition(child, known_fields, "%s.conditions[%d]" % (path, i))
        return
    # 叶节点
    field = node.get("field")
    op = node.get("op")
    if not isinstance(field, str) or not field.strip():
        raise MSError(EXIT_VALIDATION, "叶节点缺少 field: %s" % path, {"path": path})
    if op not in _ALLOWED_OPS:
        raise MSError(EXIT_VALIDATION, "非法运算符: %r（字段 %s）" % (op, field), {"path": path, "field": field})
    if field not in CANONICAL_FIELDS and field not in known_fields:
        raise MSError(EXIT_VALIDATION,
                      "条件引用未知字段: %r（已知字段见导入输出的字段字典）" % field,
                      {"field": field, "known_fields": sorted(known_fields)})
    if op in _VALUE_OPS and "value" not in node:
        raise MSError(EXIT_VALIDATION, "运算符 %s 需要 value: %s" % (op, path), {"field": field})
    if op in ("in", "not_in") and not isinstance(node.get("value"), list):
        raise MSError(EXIT_VALIDATION,
                      "运算符 %s 的 value 必须是列表: %s" % (op, path),
                      {"field": field, "value_type": type(node.get("value")).__name__})


def _field_state(entity: dict, field: str) -> tuple:
    """返回 (present: bool, value)。"""
    fields = entity.get("fields", {})
    if field in fields and not is_blank(fields.get(field)):
        return True, fields.get(field)
    return False, None


# ---- 三值解释树 ---------------------------------------------------------------
def _eval_leaf(node: dict, entity: dict) -> dict:
    """叶节点求值，返回带实际值与结果的节点（保留真实值，不只记 TRUE/FALSE）。"""
    field = node["field"]
    op = node["op"]
    present, value = _field_state(entity, field)
    leaf = {"type": "leaf", "field": field, "op": op, "value": node.get("value"),
            "present": present, "actual": value}
    if op == "exists":
        leaf["result"] = T if present else F
        return leaf
    if op == "not_exists":
        leaf["result"] = F if present else T
        return leaf
    if not present:
        leaf["result"] = U
        return leaf
    if op == "eq":
        r = T if norm(value) == norm(node.get("value")) else F
    elif op == "ne":
        r = F if norm(value) == norm(node.get("value")) else T
    elif op == "in":
        targets = node.get("value")
        targets = targets if isinstance(targets, list) else [targets]
        r = T if norm(value) in {norm(x) for x in targets} else F
    elif op == "not_in":
        targets = node.get("value")
        targets = targets if isinstance(targets, list) else [targets]
        r = F if norm(value) in {norm(x) for x in targets} else T
    elif op == "contains":
        r = T if norm(node.get("value")) in norm(value) else F
    elif op in ("gte", "lte", "gt", "lt"):
        c = _cmp(value, node.get("value"))
        if c is None:
            r = U
        elif op == "gt":
            r = T if c > 0 else F
        elif op == "gte":
            r = T if c >= 0 else F
        elif op == "lt":
            r = T if c < 0 else F
        else:
            r = T if c <= 0 else F
    else:  # pragma: no cover
        r = U
    leaf["result"] = r
    return leaf


def _and_result(results: list) -> str:
    if F in results:
        return F
    return U if U in results else T


def _or_result(results: list) -> str:
    if T in results:
        return T
    return U if U in results else F


def _eval_node(node: dict, entity: dict) -> dict:
    """递归求值，返回带每节点结果的解释树。"""
    if "logic" in node:
        logic = node["logic"]
        children = [_eval_node(c, entity) for c in node["conditions"]]
        results = [c["result"] for c in children]
        if logic == "and":
            res = _and_result(results)
        elif logic == "or":
            res = _or_result(results)
        else:  # not
            only = results[0]
            res = F if only == T else (T if only == F else U)
        return {"type": "logic", "logic": logic, "result": res, "children": children}
    return _eval_leaf(node, entity)


def _flatten_nodes(nodes: list) -> list:
    """把解释树压平为叶节点列表（保持出现顺序）。"""
    out = []
    for n in nodes:
        if n["type"] == "leaf":
            out.append(n)
        else:
            out.extend(_flatten_nodes(n["children"]))
    return out


def eval_condition(node: dict, entity: dict) -> tuple:
    """返回 (result ∈ {T,F,U}, leaves)。签名保持不变，leaves 现含 actual/result。"""
    tree = _eval_node(node, entity)
    return tree["result"], _flatten_nodes([tree])


def _combine_or(trees: list, entity: dict) -> dict:
    """多个条件树（硬排除）按 OR 合并为一个根节点；空列表结果为 FALSE。"""
    nodes = [_eval_node(t, entity) for t in trees]
    if not nodes:
        return {"type": "logic", "logic": "or", "result": F, "children": []}
    if len(nodes) == 1:
        return nodes[0]
    return {"type": "logic", "logic": "or",
            "result": _or_result([n["result"] for n in nodes]), "children": nodes}


# ---- 决定性分支提取 -----------------------------------------------------------
def _negate(v: str) -> str:
    return F if v == T else (T if v == F else U)


def _select_children(node: dict, want: str) -> list:
    """按逻辑语义选出“对结果为 want 起决定作用”的子节点。"""
    logic = node["logic"]
    kids = node["children"]
    if logic == "not":
        return [kids[0]]
    if logic == "and":
        if want == F:
            return [c for c in kids if c["result"] == F]
        if want == T:
            return list(kids)
        return [c for c in kids if c["result"] == U]
    # or
    if want == T:
        return [c for c in kids if c["result"] == T]
    if want == F:
        return list(kids)
    return [c for c in kids if c["result"] == U]


def _decisive_nodes(node: dict, want: str, path: str = "conditions") -> list:
    """返回使 node 得到 want 的**叶节点**（带 condition_path），不含未采用分支。"""
    if node["type"] == "leaf":
        if node["result"] != want:
            return []
        leaf = dict(node)
        leaf["condition_path"] = path
        return [leaf]
    sel = _select_children(node, want)
    out = []
    for i, child in enumerate(node["children"]):
        # 用身份比较而非值比较：结构相同的兄弟节点不得互相顶替决定路径
        if not any(child is s for s in sel):
            continue
        child_want = _negate(want) if node["logic"] == "not" else want
        out.extend(_decisive_nodes(child, child_want, "%s.conditions[%d]" % (path, i)))
    return out


def _fmt(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return "/".join(_fmt(x) for x in v)
    if v is None:
        return "空"
    return str(v)


def _leaf_text(leaf: dict) -> str:
    field, op = leaf["field"], leaf["op"]
    if op in ("exists", "not_exists"):
        text = "%s %s" % (field, op)
        return text if leaf["result"] != U else text + "（结果未知）"
    text = "%s %s %s" % (field, op, _fmt(leaf.get("value")))
    if leaf["result"] == U:
        return text + "（字段缺失，未知）"
    return "%s（实际：%s）" % (text, _fmt(leaf.get("actual")))


def _decisive_desc(node: dict, want: str) -> str:
    """单个决定性描述：只走决定性分支，未采用分支不出现在理由里。"""
    if node["type"] == "leaf":
        return _leaf_text(node)
    logic = node["logic"]
    if logic == "not":
        return "not(%s)" % _decisive_desc(node["children"][0], _negate(want))
    parts = [_decisive_desc(c, want) for c in _select_children(node, want)]
    if len(parts) == 1:
        return parts[0]
    return "(%s)" % (" %s " % logic).join(parts)


def _decisive_texts(node: dict, want: str) -> list:
    """根层解释列表：每个决定性子条件一条，不平铺无关 FALSE 叶子。"""
    if node["type"] == "leaf":
        return [_leaf_text(node)]
    if node["logic"] == "not":
        return ["not(%s)" % _decisive_desc(node["children"][0], _negate(want))]
    return [_decisive_desc(c, want) for c in _select_children(node, want)]


# ---- 结果 ID（同秒不碰撞）-----------------------------------------------------
def new_result_id(prefix: str, wd: Workdir) -> str:
    """碰撞安全结果 ID：随机 uuid，避开 index 已登记 ID 与 results/ 已有文件名。"""
    taken = set(wd.index.get("results", {}).keys())
    if os.path.isdir(wd.results_dir):
        for fn in os.listdir(wd.results_dir):
            taken.add(fn.split(".", 1)[0])
    while True:
        rid = "%s-%s" % (prefix, uuid.uuid4().hex[:_ID_LEN])
        if rid not in taken:
            return rid


def run_filter(request: dict, workdir_path: str) -> dict:
    if not isinstance(request, dict):
        raise MSError(EXIT_VALIDATION, "筛选请求必须是对象",
                      {"type": type(request).__name__})
    wd = Workdir(workdir_path)
    known = wd.known_field_set()

    conditions = request.get("conditions")
    if conditions is None:
        raise MSError(EXIT_VALIDATION, "筛选请求缺少 conditions 条件树", {})
    validate_condition(conditions, known)

    hard_in = request.get("hard_exclusions")
    if hard_in is None:
        hard_in = []
    if not isinstance(hard_in, list):
        raise MSError(EXIT_VALIDATION, "hard_exclusions 必须是列表",
                      {"type": type(hard_in).__name__})
    for i, h in enumerate(hard_in):
        validate_condition(h, known, "hard_exclusions[%d]" % i)

    rule_is_mock = request.get("rule_is_mock", False)
    if not isinstance(rule_is_mock, bool):
        raise MSError(EXIT_VALIDATION, "rule_is_mock 必须是布尔值",
                      {"type": type(rule_is_mock).__name__})
    rule_version = request.get("rule_version")
    if rule_version is not None and not isinstance(rule_version, str):
        raise MSError(EXIT_VALIDATION, "rule_version 必须是字符串",
                      {"type": type(rule_version).__name__})
    sort_by = request.get("sort_by")
    if sort_by is not None and not isinstance(sort_by, str):
        raise MSError(EXIT_VALIDATION, "sort_by 必须是字符串",
                      {"type": type(sort_by).__name__})

    issues = []
    unused = sorted(k for k in request.keys() if k not in _REQUEST_KEYS)
    if unused:
        issues.append({
            "kind": "unused_request_field", "fields": unused,
            "reason": ("筛选请求含未使用字段（%s）：本脚本只按 conditions/hard_exclusions/"
                       "rule_*/sort_by 计算；这些字段被忽略而非静默接受，如需生效请改为条件树"
                       % ", ".join(unused)),
            "blocking": False,
        })

    # 来源模式：演示规则(rule_is_mock)或导入数据本身是模拟数据，都算 mock；
    # 数据是 mock 而规则没标时不能输出“本地真实数据计算”（粘性，见 ms_store.source_mode）。
    mode = "mock" if (rule_is_mock or wd.data_is_mock()) else "live"
    rule_is_mock_effective = rule_is_mock or mode == "mock"
    entities = wd.all_entities()
    eligible, excluded, needs_review = [], [], []

    if not entities:
        issues.append({"kind": "empty_archive",
                       "reason": "工作目录无主体档案，请先执行 import 导入会员",
                       "blocking": False})

    for eid, entity in entities.items():
        fields = entity.get("fields", {})
        base = {
            "entity_id": eid,
            "name": fields.get("name", ""),
            "industry": fields.get("industry"),
            "region": fields.get("region"),
            "member_status": fields.get("member_status"),
            "renewal_status": fields.get("renewal_status"),
            "contactable_status": fields.get("contactable_status"),
            "entity_version": entity.get("version"),
            "entity_status": entity.get("status"),
        }
        if entity.get("status") == "conflicted":
            needs_review.append({
                **base, "reason": "主体档案冲突，已隔离待核对",
                "reason_detail": "同一业务编号对应冲突资料；旧字段仅为历史，不能用于本次筛选结论",
                "unknown_fields": ["identity"],
                "conflict_variants": entity.get("conflict_variants", []),
            })
            continue
        hard_root = _combine_or(hard_in, entity)
        filt_root = _eval_node(conditions, entity)
        hard_res, filt_res = hard_root["result"], filt_root["result"]

        if hard_res == T:
            # 硬排除优先：报告真正命中的硬排除分支
            excluded.append({
                **base,
                "reason": "命中硬排除规则",
                "reason_detail": "命中硬排除规则：" + "；".join(_decisive_texts(hard_root, T)),
                "matched": _decisive_nodes(hard_root, T, "hard_exclusions"),
                "condition_tree": hard_root,
            })
        elif filt_res == F:
            excluded.append({
                **base,
                "reason": "不满足筛选条件",
                "reason_detail": "不满足筛选条件：" + "；".join(_decisive_texts(filt_root, F)),
                "failed_conditions": _decisive_nodes(filt_root, F),
                "condition_tree": filt_root,
            })
        elif hard_res == U or filt_res == U:
            unknown_leaves = []
            if filt_res == U:
                unknown_leaves.extend(_decisive_nodes(filt_root, U))
            if hard_res == U:
                unknown_leaves.extend(_decisive_nodes(hard_root, U, "hard_exclusions"))
            unknown_fields = sorted({l["field"] for l in unknown_leaves})
            if filt_res == U and hard_res == U:
                detail = "筛选条件与硬排除均存在未知字段，无法确认是否符合或是否应排除"
            elif filt_res == U:
                detail = "筛选条件存在未知字段，无法确认是否符合"
            else:
                detail = "硬排除存在未知字段，无法确认是否应排除"
            needs_review.append({
                **base,
                "reason": "关键字段未知，无法确认是否符合/是否被排除",
                "reason_detail": detail + "：" + "；".join(
                    _leaf_text(l) for l in unknown_leaves),
                "unknown_fields": unknown_fields,
                "unknown_detail": entity.get("unknowns", {}),
                "condition_tree": {"conditions": filt_root, "hard_exclusions": hard_root},
            })
        else:
            eligible.append({
                **base,
                "reasons": _decisive_texts(filt_root, T),
                "evidence": _evidence_refs(entity),
                "condition_tree": filt_root,
            })

    # 排序：运营处理顺序建议（非续费意愿）
    if sort_by:
        if sort_by in known | set(CANONICAL_FIELDS):
            eligible.sort(key=lambda x: norm(str(x.get(sort_by, ""))))
        else:
            issues.append({"kind": "unknown_sort_field", "field": sort_by,
                           "reason": "sort_by 不是已知字段，已按 entity_id 排序（不静默忽略）",
                           "blocking": False})
            eligible.sort(key=lambda x: x["entity_id"])
    else:
        eligible.sort(key=lambda x: x["entity_id"])

    result_id = new_result_id("flt", wd)
    counts = {"eligible": len(eligible), "excluded": len(excluded),
              "needs_review": len(needs_review), "total": len(entities)}

    from ms_export import write_filter_outputs
    paths = write_filter_outputs(wd, result_id, {
        "mode": mode, "rule_version": rule_version,
        "rule_is_mock": rule_is_mock, "data_is_mock": mode == "mock",
        "conditions": conditions, "hard_exclusions": hard_in,
        "eligible": eligible, "excluded": excluded, "needs_review": needs_review,
        "counts": counts, "disclaimer": PRIORITY_DISCLAIMER.strip(),
    })

    status = "succeeded"
    if not entities:
        status = "needs_review"
    elif needs_review:
        status = "needs_review"

    wd.register_result(result_id, {
        "type": "filter", "status": status, "rule_version": rule_version,
        "rule_is_mock": rule_is_mock_effective, "counts": counts,
        "entity_ids": [e["entity_id"] for e in eligible],
        "paths": paths, "path": paths.get("md"), "mode": mode,
    })
    wd.save()
    wd.write_readme()

    next_actions = []
    if rule_is_mock_effective:
        next_actions.append("当前使用演示(mock)规则或模拟数据，结论仅用于体验，不作为机构政策")
    if needs_review:
        next_actions.append("补查 needs_review 中的未知字段后重跑筛选")
    if eligible:
        next_actions.append("对 eligible 中的目标用 brief 子命令生成拜访简报")

    env = make_envelope(
        command="filter", mode=mode, data_version=wd.data_version,
        rule_version=rule_version, status=status,
        items=eligible + excluded + needs_review, issues=issues,
        evidence_refs=[r for e in eligible for r in e.get("evidence", [])],
        next_actions=next_actions,
        extra={
            "result_id": result_id,
            "condition_tree": conditions,
            "hard_exclusions": hard_in,
            "eligible": eligible, "excluded": excluded, "needs_review": needs_review,
            "counts": counts,
            "rule_is_mock": rule_is_mock,
            "is_mock": mode == "mock",
            "data_is_mock": mode == "mock",
            "disclaimer": PRIORITY_DISCLAIMER.strip(),
            "outputs": paths,
            "explanation_note": ("excluded.failed_conditions/matched 只含决定根节点结果的"
                                 "分支；每项的 condition_tree 为带节点结果的三值解释树"),
        },
    )
    return env


def _evidence_refs(entity: dict) -> list:
    refs = []
    for field, src in entity.get("field_sources", {}).items():
        refs.append({"field": field, "source": src})
    return refs
