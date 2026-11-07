"""拜访简报：把档案中的“有依据事实 / 推断 / 未知”分开，附字段级引用。

事实只来自已导入档案的字段（带源文件行/版本引用）；建议、目标、问题由宿主
（模型）生成后在 --context 传入，脚本标注为 AI 建议。资质、风险、实控人、
真实会员等级无依据一律列未知，不补造。

映射缺口不得谎称资料缺失（Astra P2.2 / Terra P2）：当档案里存在未映射列
（`custom:*`）时，某个规范字段没有值，只能写“待字段映射”，并列出未映射列
交由宿主建议显式映射；**不能**说“源数据未提供该字段”。只有当源表字段都已
映射、确实没有该列时，才允许说源数据未提供。
事实 / 推断 / 未知三类分开；同名主体只提示歧义，不合并、不判定坏数据。
brief_id 用随机 uuid 生成并避开已占用 ID，同秒多次生成不会互相覆盖。
来源模式(mode)只从档案/工作区继承：导入声明 mock 即全程 mock，brief 不接受外层
is_mock 输入，因此不存在被外层 false 降级成 live 的路径。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from ms_common import MSError, EXIT_NOT_FOUND, EXIT_VALIDATION, make_envelope
from ms_store import Workdir

# 简报关注、但绝不编造的敏感字段
_SENSITIVE = ["qualification"]
# 明确声明不在会员数据模型内、需外部依据的项
_NOT_IN_MODEL = ["经营风险", "实际控制人", "工商实时状态", "真实资质等级"]

# 简报通常想了解的字段（用于列出未知缺口）
_BRIEF_FIELDS = [
    ("name", "企业名称"), ("industry", "所属行业"), ("region", "所在地区"),
    ("member_status", "会员状态"), ("renewal_status", "续费状态"),
    ("contactable_status", "可联系状态"), ("joined_date", "加入日期"),
    ("last_activity_date", "最近活动"), ("qualification", "资质"),
]

_FIELD_LABELS = dict(_BRIEF_FIELDS)
_LABEL_TO_FIELD = {v: k for k, v in _BRIEF_FIELDS}

# context 允许的顶层字段；其余作为 unused_context_field issue 上报，不静默忽略
_CONTEXT_KEYS = {"materials", "suggestions", "goals", "questions", "preparations",
                 "inferences", "note", "notes", "comment", "description", "purpose",
                 "visit_date", "operator", "requested_by", "as_of"}
_CONTEXT_LIST_KEYS = ("suggestions", "goals", "questions", "preparations", "inferences")

_ID_LEN = 10


def _cite(entity: dict, field: str) -> str:
    src = entity.get("field_sources", {}).get(field)
    if not src:
        return "档案（无字段级来源）"
    return "%s（data_version=%s）" % (src.get("source", "?"), src.get("data_version", "?"))


def new_result_id(prefix: str, wd: Workdir) -> str:
    """碰撞安全 brief_id：随机 uuid，避开 index 已登记 ID 与 results/ 已有文件名。"""
    taken = set(wd.index.get("results", {}).keys())
    if os.path.isdir(wd.results_dir):
        for fn in os.listdir(wd.results_dir):
            taken.add(fn.split(".", 1)[0])
    while True:
        rid = "%s-%s" % (prefix, uuid.uuid4().hex[:_ID_LEN])
        if rid not in taken:
            return rid


def _validate_context(context: Optional[dict], issues: list) -> dict:
    """校验宿主补充材料；类型/来源缺失即结构化报错，不静默忽略。"""
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise MSError(EXIT_VALIDATION, "brief 的 context 必须是对象",
                      {"type": type(context).__name__})
    for key in _CONTEXT_LIST_KEYS:
        v = context.get(key)
        if v is None:
            continue
        if not isinstance(v, list):
            raise MSError(EXIT_VALIDATION, "context.%s 必须是列表" % key,
                          {"key": key, "type": type(v).__name__})
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise MSError(EXIT_VALIDATION, "context.%s[%d] 必须是字符串" % (key, i),
                              {"key": key, "index": i, "type": type(item).__name__})
    materials = context.get("materials")
    if materials is not None:
        if not isinstance(materials, list):
            raise MSError(EXIT_VALIDATION, "context.materials 必须是列表",
                          {"type": type(materials).__name__})
        for i, m in enumerate(materials):
            if not isinstance(m, dict):
                raise MSError(EXIT_VALIDATION, "context.materials[%d] 必须是对象" % i,
                              {"index": i})
            field = m.get("field")
            if not isinstance(field, str) or not field.strip():
                raise MSError(EXIT_VALIDATION,
                              "context.materials[%d] 缺少 field（补充资料必须说明对应字段）" % i,
                              {"index": i})
            if m.get("value") is None:
                raise MSError(EXIT_VALIDATION,
                              "context.materials[%d] 缺少 value（字段 %s）" % (i, field),
                              {"index": i, "field": field})
            source = m.get("source")
            if not isinstance(source, str) or not source.strip():
                raise MSError(EXIT_VALIDATION,
                              "context.materials[%d] 缺少 source：无来源的内容不能作为事实进入简报"
                              % i, {"index": i, "field": field})
    unused = sorted(k for k in context.keys() if k not in _CONTEXT_KEYS)
    if unused:
        issues.append({
            "kind": "unused_context_field", "fields": unused,
            "reason": ("context 含未使用字段（%s）：脚本只取 materials/suggestions/goals/"
                       "questions/preparations/inferences；不静默接受" % ", ".join(unused)),
            "blocking": False,
        })
    return context


def run_brief(entity_id: str, workdir_path: str, context: Optional[dict] = None,
              purpose: Optional[str] = None) -> dict:
    if purpose is not None and not isinstance(purpose, str):
        raise MSError(EXIT_VALIDATION, "purpose 必须是字符串",
                      {"type": type(purpose).__name__})
    issues: list = []
    context = _validate_context(context, issues)

    wd = Workdir(workdir_path)
    entity = wd.get_entity(entity_id)
    if entity is None:
        raise MSError(EXIT_NOT_FOUND, "未找到主体: %s" % entity_id,
                      {"entity_id": entity_id,
                       "hint": "用 index 子命令查看已有 entity_id，或先 import"})
    fields = entity.get("fields", {})
    unknowns_map = entity.get("unknowns", {})

    # ---- 未映射列：映射缺口的前提，绝不据此谎称资料缺失 ----
    unmapped = sorted(k for k in fields.keys() if str(k).startswith("custom:"))

    facts = []
    for f, label in _BRIEF_FIELDS:
        if f in fields and fields.get(f) not in (None, ""):
            facts.append({"field": label, "value": fields[f], "source": _cite(entity, f)})
    # 附加资料中的事实（宿主提供，必须带来源，已在 _validate_context 校验）
    for m in context.get("materials", []) or []:
        facts.append({"field": m["field"], "value": m["value"],
                      "source": "补充资料：%s" % m["source"]})
    conflicted = entity.get("status") == "conflicted"
    historical_facts = facts if conflicted else []
    if conflicted:
        facts = []

    # ---- 未知 / 待映射：区分“源数据没有”与“还没映射” ----
    unknowns = []
    pending_mapping_fields = []
    for f, label in _BRIEF_FIELDS:
        if f in fields and fields.get(f) not in (None, ""):
            continue
        recorded = unknowns_map.get(f, {}) or {}
        if unmapped:
            reason = ("待字段映射：源表存在未映射列 %s，该字段可能就在其中；"
                      "不能断言源数据未提供。请宿主结合当前任务建议显式映射后重导再核对。"
                      % ", ".join(unmapped))
            pending_mapping_fields.append(label)
            unknowns.append({"field": label, "reason": reason, "mapping_pending": True,
                             "unmapped_columns": unmapped})
        elif recorded.get("reason"):
            unknowns.append({"field": label, "reason": recorded["reason"],
                             "mapping_pending": False})
        else:
            unknowns.append({"field": label, "reason": "源数据未提供该字段（已映射列中无此字段）",
                             "mapping_pending": False})
    for item in _NOT_IN_MODEL:
        unknowns.append({"field": item, "reason": "不在会员数据模型内，无外部依据，不编造",
                         "mapping_pending": False})

    # ---- 事实 / 推断 / 建议 分开 ----
    inferences = [{"text": t, "kind": "inference",
                   "note": "宿主推断，需人员核对；推断永不升级为事实"}
                  for t in (context.get("inferences", []) or [])]
    suggestions = [{"text": s, "kind": "ai_suggestion",
                    "note": "AI 建议，非事实，需人员判断"}
                   for s in (context.get("suggestions", []) or [])]
    goals = list(context.get("goals", []) or [])
    questions = list(context.get("questions", []) or [])
    preparations = list(context.get("preparations", []) or [])
    if purpose:
        goals.insert(0, "本次拜访目的：%s" % purpose)

    # ---- 歧义与重名提示（不合并、不判定坏数据）----
    warnings = []
    if conflicted:
        reason = "主体档案存在同编号资料冲突，已隔离；旧字段仅作历史依据，不能作为当前拜访事实"
        warnings.append({"kind": "identity_conflict", "reason": reason,
                         "variants": entity.get("conflict_variants", [])})
        issues.append({"kind": "identity_conflict", "reason": reason, "blocking": True})
        unknowns.insert(0, {"field": "主体及当前资料", "reason": reason,
                            "mapping_pending": False})
    same_name = sorted(eid for eid, e in wd.all_entities().items()
                       if eid != entity_id
                       and (e.get("fields", {}) or {}).get("name")
                       and (e.get("fields", {}) or {}).get("name") == fields.get("name"))
    if entity.get("status") == "suspected_duplicate":
        warnings.append({
            "kind": "suspected_duplicate",
            "reason": "该主体在导入时被标记为疑似重复/重名，简报只描述当前档案，使用前先人工确认主体",
            "entity_id": entity_id,
        })
    if same_name:
        warnings.append({
            "kind": "name_ambiguity", "entity_ids": same_name,
            "reason": ("本地存在 %d 个同名主体（%s）：简报仅描述 %s，不要把不同主体的事实合并"
                       % (len(same_name) + 1, ", ".join(same_name + [entity_id]), entity_id)),
        })
    if unmapped:
        warnings.append({
            "kind": "pending_field_mapping", "unmapped_columns": unmapped,
            "fields": pending_mapping_fields,
            "reason": ("源表有未映射列 %s：涉及字段按“待字段映射”处理，未映射前不得声称源数据"
                       "未提供，也不得把猜测值当事实" % ", ".join(unmapped)),
        })
        issues.append({
            "kind": "pending_field_mapping", "unmapped_columns": unmapped,
            "fields": pending_mapping_fields,
            "reason": ("未映射列 %s 未归一；宿主可建议显式映射后重导，再重新生成简报"
                       % ", ".join(unmapped)),
            "blocking": False,
        })

    brief_id = new_result_id("brf", wd)
    # 来源模式继承：实体或工作区为模拟数据即 mock；brief 不接受外层 is_mock 输入，
    # 因此不存在“被外层 false 降成 live”的路径（粘性，见 ms_store.source_mode）。
    mode = wd.source_mode(entity_id)
    data = {
        "entity_id": entity_id,
        "name": fields.get("name", ""),
        "facts": facts, "unknowns": unknowns,
        "suggestions": [s["text"] for s in suggestions], "goals": goals,
        "questions": questions, "preparations": preparations,
        "mode": mode,
    }
    from ms_export import write_brief_outputs
    paths = write_brief_outputs(wd, brief_id, data)
    status = "needs_review" if (unmapped or conflicted) else "succeeded"
    wd.register_result(brief_id, {
        "type": "brief", "status": status, "entity_id": entity_id,
        "entity_version": entity.get("version"), "paths": paths, "path": paths.get("md"),
        "mode": mode,
    })
    wd.save()
    wd.write_readme()

    next_actions = ["人员核对简报中的未知项"]
    if unmapped:
        next_actions.append(
            "源表有未映射列（%s）：由宿主建议显式字段映射后重导，再重新生成本简报"
            % ", ".join(unmapped))
    next_actions.append("如需外部资质/风险，走 mcp-candidates（当前无工具时保留待核实）")
    if mode == "mock":
        next_actions.insert(0, "本简报基于模拟(mock)导入数据：仅用于演示，不作为机构政策或真实工商信息")

    env = make_envelope(
        command="brief", mode=mode, data_version=wd.data_version, status=status,
        items=facts,
        evidence_refs=[{"field": f["field"], "source": f["source"]} for f in facts],
        issues=issues,
        next_actions=next_actions,
        extra={
            "brief_id": brief_id, "entity_id": entity_id, "outputs": paths,
            "is_mock": mode == "mock",
            "mode_note": ("来源模式继承自导入声明：本简报所依据的档案来自模拟(mock)数据，"
                          "只表示资料来源，不代表已核实工商真实性。"
                          if mode == "mock" else
                          "来源模式按真实本地文件处理；mode 只表示资料来源，不代表已核实工商真实性。"),
            "unknowns": unknowns,
            "inferences": inferences,
            "suggestions": suggestions, "goals": goals,
            "questions": questions, "preparations": preparations,
            "warnings": warnings,
            "historical_facts": historical_facts,
            "conflict_variants": entity.get("conflict_variants", []) if conflicted else [],
            "unmapped_columns": unmapped,
            "pending_mapping_fields": pending_mapping_fields,
            "disclaimer": ("事实均带字段级引用；推断与 AI 建议不是事实；未知项不补造。"
                           "存在未映射列时相关字段为“待字段映射”，不是“源数据未提供”。"),
        },
    )
    return env
