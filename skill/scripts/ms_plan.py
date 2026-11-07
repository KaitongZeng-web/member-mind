"""后续行动计划与任务状态更新。

- build_plan：把已核对的需求/承诺转成 FollowUpTask；缺负责人/期限保持草案；
  日期必须有效；相对日期以纪要日期换算并保留原话（缺基准不猜）；
  客户拒绝联系范围影响任务状态（命中即 blocked）。
- update_task：状态更新需证据（完成需 completion_evidence）；幂等；
  不动其他主体/纪要的任务；任务完成≠续费，复访≠客户已接受预约。
"""
from __future__ import annotations

import datetime as _dt
import copy
import re
from typing import Any, Optional

from ms_common import (
    MSError, EXIT_NOT_FOUND, EXIT_VALIDATION, now_iso, short_hash, norm,
    make_envelope, gen_id, check_no_unknown_keys,
)
from ms_store import Workdir
from ms_minutes import validate_visit_evidence

_TASK_STATUS = {"draft", "pending_assignment", "in_progress", "completed", "blocked", "cancelled"}
_EXECUTABLE = {"pending_assignment", "in_progress"}
_TERMINAL = {"completed", "cancelled"}

# run_plan 请求允许的顶层字段（未知字段报错，A6）
_PLAN_KEYS = {"visit_id", "entity_id", "minutes_date", "no_contact", "tasks", "is_mock"}
# 单个任务允许的字段（status 不能在计划阶段直接设定，A4/A6）
_TASK_KEYS = {"source_item_id", "deliverable", "owner", "deadline", "deadline_relative",
              "dependencies", "channel", "non_contact_action", "next_trigger",
              "independent", "note"}
# task-update 允许的字段（提供补齐 owner/deadline 等字段的正规入口，A4）
_UPDATE_KEYS = {"status", "owner", "deadline", "deadline_relative", "dependencies",
                "deliverable", "channel", "next_trigger", "reason",
                "completion_evidence", "note"}
# 任务可在 update 中编辑的可变字段
_MUTABLE = ("owner", "deadline", "deadline_original", "deadline_basis",
            "dependencies", "deliverable", "channel", "next_trigger")


def _valid_date(s: str) -> bool:
    try:
        _dt.date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


def _resolve_deadline(task: dict, minutes_date: Optional[str]) -> tuple:
    """返回 (deadline_or_None, original_text_or_None, basis_or_None, issue_or_None)。"""
    if task.get("deadline"):
        dl = str(task["deadline"]).strip()
        if not _valid_date(dl):
            raise MSError(EXIT_VALIDATION, "任务截止日期无效: %r" % task["deadline"],
                          {"deliverable": task.get("deliverable")})
        return dl, None, None, None
    rel = task.get("deadline_relative")
    if rel:
        text = rel.get("text", "")
        days = rel.get("days")
        if days is None:
            return None, text, None, {"reason": "相对日期缺少天数，且无纪要日期基准，保持草案不猜测"}
        if not minutes_date:
            return None, text, None, {"reason": "相对日期缺少纪要日期(minutes_date)基准，保持草案不猜测",
                                      "original": text}
        try:
            base = _dt.date.fromisoformat(minutes_date)
        except ValueError:
            return None, text, None, {"reason": "纪要日期无效，无法换算相对日期，保持草案", "original": text}
        dl = (base + _dt.timedelta(days=int(days))).isoformat()
        return dl, text, "minutes_date=%s + %d天（原话：%s）" % (minutes_date, int(days), text), None
    return None, None, None, None


def _locate_confirmed_item(visit: Optional[dict], source_item_id: str) -> tuple:
    """在已核对纪要中定位来源项，返回 (item_or_None, reason)。

    只有“存在 + 已确认 + 引用有效”的条目才能作为正式来源（A1）。
    """
    if not visit:
        return None, "无关联纪要，不能引用纪要来源项"
    for key in ("needs", "commitments", "open_questions"):
        for it in visit.get(key, []):
            if it.get("id") == source_item_id:
                if it.get("status") != "confirmed":
                    return None, "来源项未确认（status=%s）" % it.get("status")
                if it.get("citation", {}).get("verified") is not True:
                    return None, "来源项引用未在原文定位，不可作为事实来源"
                return it, None
    return None, "来源项不存在于已核对纪要"


def _current_entity(wd, entity_id):
    if not entity_id:
        return
    entity = wd.get_entity(entity_id)
    if entity is None:
        raise MSError(EXIT_NOT_FOUND, "关联主体不存在", {"entity_id": entity_id})
    if entity.get("status") == "conflicted":
        raise MSError(EXIT_VALIDATION, "主体资料冲突，不能据此生成或推进正式行动", {"entity_id": entity_id})


def _contact_hits(scopes, channel, internal=False):
    ch = norm(channel or "")
    if internal:
        if ch and ch not in ("内部", "internal", "内部准备", "不适用"):
            raise MSError(EXIT_VALIDATION, "内部准备不能同时指定对外联系渠道", {"channel": channel})
        return []
    aliases = {"phone": "电话", "telephone": "电话", "email": "邮件", "mail": "邮件"}
    ch = aliases.get(ch, ch)
    scopes = [norm(x) for x in scopes if x]
    if scopes and not ch:
        return ["存在联系限制且渠道尚未明确"]
    all_scopes = {"all", "all_outbound", "全部", "全部联系", "所有联系", "拒绝联系", "不再联系"}
    return [x for x in scopes if x in all_scopes or (ch and (ch in x or x in ch))]


def run_plan(request: dict, workdir_path: str) -> dict:
    check_no_unknown_keys(request, _PLAN_KEYS, "plan 请求")
    wd = Workdir(workdir_path)
    visit_id = request.get("visit_id")
    entity_id = request.get("entity_id")
    minutes_date = request.get("minutes_date")
    visit = None
    if visit_id:
        visit = wd.index["visits"].get(visit_id)
        if visit is None:
            raise MSError(EXIT_NOT_FOUND, "未找到拜访记录: %s" % visit_id, {"visit_id": visit_id})
        if visit.get("status") not in ("confirmed", "partially_confirmed"):
            raise MSError(EXIT_VALIDATION,
                          "只能对已核对的纪要生成正式计划（当前 status=%s）" % visit.get("status"),
                          {"visit_id": visit_id, "hint": "先 minutes-confirm"})
        # A3：请求的 entity_id 不得与纪要主体冲突（有值必须一致）
        if entity_id and entity_id != visit.get("entity_id"):
            raise MSError(EXIT_VALIDATION,
                          "计划 entity_id 与纪要主体不一致，禁止错绑: 计划=%s 纪要=%s"
                          % (entity_id, visit.get("entity_id")),
                          {"request_entity_id": entity_id, "visit_entity_id": visit.get("entity_id")})
        validate_visit_evidence(wd, visit)
        if not minutes_date:
            minutes_date = visit.get("minutes_date")
        if not entity_id:
            entity_id = visit.get("entity_id")

    # A3：无 visit 时，有值的 entity_id 必须存在于档案（has-value-must-exist）
    if entity_id and wd.get_entity(entity_id) is None:
        raise MSError(EXIT_NOT_FOUND, "plan.entity_id 不在档案中: %s" % entity_id,
                      {"entity_id": entity_id})

    _current_entity(wd, entity_id)

    # 来源模式：请求显式声明 is_mock=true 或上游（纪要/档案/工作区）为 mock 即 mock；
    # 不被“请求没写 is_mock”降级成 live（粘性）。演示数据必须一路带 mock 标识。
    mode = wd.source_mode(entity_id, extra_mock=bool(request.get("is_mock"))
                          or (visit or {}).get("mode") == "mock")

    if minutes_date and not _valid_date(str(minutes_date)):
        raise MSError(EXIT_VALIDATION, "minutes_date 非法: %r" % minutes_date, {})

    # 拒绝联系范围：以纪要已确认 scope 为权威，请求只能新增、不能移除（A4）
    nc_items = []
    if visit:
        nc_items = [n for n in visit.get("no_contact", []) if n.get("status") == "confirmed"]
    for nc in request.get("no_contact", []) or []:
        if isinstance(nc, dict) and nc.get("scope"):
            nc_items.append(nc)
    nc_scopes = [norm(nc.get("scope", "")) for nc in nc_items if nc.get("scope")]

    tasks_in = request.get("tasks", [])
    if not isinstance(tasks_in, list):
        raise MSError(EXIT_VALIDATION, "tasks 必须是列表", {})

    issues = []
    # 预检 + 计算（任何无效输入在此拒绝，尚未落盘，A6）
    prepared = []
    for t in tasks_in:
        if not isinstance(t, dict):
            raise MSError(EXIT_VALIDATION, "tasks 元素必须是对象", {})
        check_no_unknown_keys(t, _TASK_KEYS, "任务")
        deliverable = t.get("deliverable")
        if not deliverable or not str(deliverable).strip():
            issues.append({"kind": "missing_deliverable", "reason": "任务缺少交付物，跳过", "blocking": False})
            continue
        # non_contact_action 必须是布尔，不接受随意类型（A4）
        nca = t.get("non_contact_action", False)
        if not isinstance(nca, bool):
            raise MSError(EXIT_VALIDATION,
                          "non_contact_action 必须是布尔值（内部准备 true / 对外触达 false），收到 %r" % nca,
                          {"deliverable": deliverable})

        source_item_id = t.get("source_item_id")
        if source_item_id:
            src_item, reason = _locate_confirmed_item(visit, source_item_id)
            if src_item is None:
                # A1：无效/未确认/引用不定位的来源，拒绝生成正式任务
                raise MSError(EXIT_VALIDATION,
                              "任务来源无效，拒绝生成正式任务: %s（%s）" % (source_item_id, reason),
                              {"deliverable": deliverable, "source_item_id": source_item_id,
                               "reason": reason,
                               "hint": "补充新事实请提供新的明确来源并重新抽取核对"})
            source_ref = "%s[%s]: %s" % ("confirmed", source_item_id, src_item.get("text", ""))
        else:
            # 用户明确新增的独立任务：允许，但标注自己的来源，不冒充客户承诺
            source_ref = "用户新增独立任务（无纪要来源）"

        deadline, original, basis, dl_issue = _resolve_deadline(t, minutes_date)
        if dl_issue:
            issues.append({"kind": "deadline_issue", "deliverable": deliverable, **dl_issue, "blocking": False})

        owner = t.get("owner")
        channel = t.get("channel")
        blocked = False
        block_reason = None
        restricted_scopes = list(nc_scopes)
        hits = _contact_hits(nc_scopes, channel, nca)
        if hits:
            blocked = True
            block_reason = "命中客户拒绝联系范围：%s" % (channel or "渠道未明确")

        if blocked:
            status = "blocked"
        elif not owner or not deadline:
            status = "draft"
        else:
            status = "pending_assignment"

        # 稳定任务身份：不含 owner/deadline，改负责人/期限更新同一任务而非新建（A4）
        signature = "|".join([str(deliverable), str(source_item_id or ""), str(channel or ""),
                              "1" if nca else "0", "1" if t.get("independent") else "0"])
        business_key = wd.task_business_key(entity_id, visit_id, signature)
        prepared.append({
            "task_id": "tsk-" + short_hash(business_key, 12),
            "business_key": business_key,
            "entity_id": entity_id, "visit_id": visit_id,
            "deliverable": deliverable, "owner": owner or None,
            "deadline": deadline, "deadline_original": original, "deadline_basis": basis,
            "dependencies": t.get("dependencies", []) or [],
            "completion_evidence": None,
            "next_trigger": t.get("next_trigger"),
            "channel": channel, "block_reason": block_reason,
            "contact_restricted_scopes": restricted_scopes,
            "non_contact_action": nca, "source_item_id": source_item_id,
            "source_extraction_hash": (visit or {}).get("extraction_hash"),
            "status": status, "source_ref": source_ref,
            "mode": mode,
            "created_at": now_iso(), "updated_at": now_iso(),
            "history": [{"status": status, "at": now_iso(), "note": "创建"}],
        })

    # 落库（幂等/更新）
    created, existing = [], []
    for task in prepared:
        saved, is_new = wd.upsert_task(task)
        if not is_new and any(saved.get(k) != task.get(k) for k in _MUTABLE):
            issues.append({"kind": "existing_task_preserved", "task_id": saved["task_id"],
                           "reason": "已保留现有任务及后续编辑；修改请使用 task-update，重放计划不覆盖进度",
                           "blocking": False})
        (created if is_new else existing).append(saved)

    plan_id = gen_id("pln", visit_id or "", entity_id or "")
    from ms_export import write_plan_outputs
    all_tasks = created + existing
    paths = write_plan_outputs(wd, plan_id, {
        "entity_id": entity_id, "visit_id": visit_id, "tasks": all_tasks, "mode": mode,
    })
    wd.register_result(plan_id, {
        "type": "plan", "status": "succeeded", "entity_id": entity_id,
        "visit_id": visit_id, "paths": paths, "path": paths.get("md"), "mode": mode,
    })
    wd.save()
    wd.write_readme()

    return make_envelope(
        command="plan", mode=mode, data_version=wd.data_version, status="succeeded",
        items=all_tasks, issues=issues,
        next_actions=["分派负责人与确认期限，使草案转为待执行",
                      "用 task-update 补齐 owner/deadline 或推进状态；完成后记录证据",
                      "任务完成≠续费"]
        + (["本计划基于模拟(mock)数据/演示纪要：仅用于体验，不作为真实承诺或机构政策"]
           if mode == "mock" else []),
        extra={"plan_id": plan_id, "entity_id": entity_id, "visit_id": visit_id,
               "tasks": all_tasks, "created": len(created), "reused": len(existing),
               "no_contact": nc_items, "outputs": paths, "mode": mode,
               "is_mock": mode == "mock",
               "mode_note": ("来源模式继承自导入/纪要声明：演示数据一路带 mock 标识，"
                             "只表示资料来源，不作为真实承诺或机构政策。"
                             if mode == "mock" else
                             "来源模式按真实本地数据处理；mode 只表示资料来源，"
                             "不代表已核实工商真实性。"),
               "disclaimer": "任务完成≠续费；复访触发≠客户已接受预约；缺负责人/期限为草案；"
                             "仅已确认且引用有效的纪要来源项才能成为正式任务"},
    )


def run_task_update(task_id: str, request: dict, workdir_path: str) -> dict:
    check_no_unknown_keys(request, _UPDATE_KEYS, "task-update 请求")
    wd = Workdir(workdir_path)
    original = wd.task_by_id(task_id)
    if original is None:
        raise MSError(EXIT_NOT_FOUND, "未找到任务", {"task_id": task_id})
    task = copy.deepcopy(original)
    _current_entity(wd, task.get("entity_id"))
    visit = None
    scopes = list(task.get("contact_restricted_scopes") or [])
    if task.get("visit_id"):
        visit = wd.index["visits"].get(task["visit_id"])
        if visit is None:
            raise MSError(EXIT_VALIDATION, "任务来源纪要不存在", {})
        validate_visit_evidence(wd, visit)
        if task.get("entity_id") != visit.get("entity_id"):
            raise MSError(EXIT_VALIDATION, "任务与纪要主体不一致", {})
        if visit.get("status") not in ("confirmed", "partially_confirmed"):
            raise MSError(EXIT_VALIDATION, "任务来源纪要尚未核对", {})
        source_id = task.get("source_item_id")
        if not source_id:
            m = re.match(r"confirmed\[([^]]+)\]", task.get("source_ref", ""))
            source_id = m.group(1) if m else None
        if source_id:
            item, why = _locate_confirmed_item(visit, source_id)
            if item is None:
                raise MSError(EXIT_VALIDATION, "任务来源无效: " + why, {})
        scopes += [x.get("scope") for x in visit.get("no_contact", [])
                   if x.get("status") == "confirmed" and x.get("scope")]
    old_status = task.get("status")
    target = request.get("status", old_status)
    if target not in _TASK_STATUS:
        raise MSError(EXIT_VALIDATION, "非法任务状态", {"status": target})
    if old_status in _TERMINAL and target != old_status:
        raise MSError(EXIT_VALIDATION, "终态任务不能直接回退，请保留历史另建行动", {})
    reason = request.get("reason")
    if target != old_status and target in ("blocked", "cancelled"):
        if not isinstance(reason, str) or not reason.strip():
            raise MSError(EXIT_VALIDATION, "阻止/取消任务必须提供原因", {})
    for field in ("owner", "deliverable", "channel", "next_trigger"):
        if field in request:
            value = request[field]
            if value is not None and not isinstance(value, str):
                raise MSError(EXIT_VALIDATION, field + "必须是字符串或null", {})
            value = value.strip() if isinstance(value, str) else None
            if field == "deliverable" and not value:
                raise MSError(EXIT_VALIDATION, "任务交付物不能为空", {})
            task[field] = value or None
    if "dependencies" in request:
        dep = request["dependencies"]
        if not isinstance(dep, list) or any(not isinstance(x, str) for x in dep):
            raise MSError(EXIT_VALIDATION, "dependencies必须是字符串列表", {})
        task["dependencies"] = dep
    if "deadline" in request:
        dl = request["deadline"]
        if dl is not None and (not isinstance(dl, str) or not _valid_date(dl)):
            raise MSError(EXIT_VALIDATION, "任务期限必须是有效日期或null", {})
        task["deadline"] = dl
        task["deadline_original"] = None
        task["deadline_basis"] = "task-update明确修改"
    if "deadline_relative" in request:
        rel = request["deadline_relative"]
        if not isinstance(rel, dict) or not isinstance(rel.get("days"), int) or isinstance(rel.get("days"), bool):
            raise MSError(EXIT_VALIDATION, "相对期限需要整数days及纪要日期", {})
        dl, text, basis, issue = _resolve_deadline({"deadline_relative": rel}, (visit or {}).get("minutes_date"))
        if not dl:
            raise MSError(EXIT_VALIDATION, "无法解析相对期限，请明确日期", {})
        task.update(deadline=dl, deadline_original=text, deadline_basis=basis)
    hits = _contact_hits(scopes, task.get("channel"), task.get("non_contact_action", False))
    # 每次编辑都检查最终状态；普通reason不能替代客户的新偏好证据。
    if target in _EXECUTABLE | {"completed"}:
        if not task.get("owner") or not _valid_date(task.get("deadline")):
            raise MSError(EXIT_VALIDATION, "执行或完成任务必须有负责人和有效期限", {})
        if hits:
            raise MSError(EXIT_VALIDATION, "客户联系限制仍有效，不能用状态或原因解除",
                          {"channel": task.get("channel"), "restrictions": hits})
    task["status"] = target
    if target == "completed":
        evidence = request.get("completion_evidence", task.get("completion_evidence"))
        if not evidence or not isinstance(evidence, (str, dict)) or (isinstance(evidence, str) and not evidence.strip()):
            raise MSError(EXIT_VALIDATION, "任务完成必须提供完成证据", {})
        task["completion_evidence"] = evidence
    elif "completion_evidence" in request:
        raise MSError(EXIT_VALIDATION, "完成证据应与completed状态一起记录", {})
    if reason and target in ("blocked", "cancelled"):
        task["block_reason"] = reason
    changes = [{"field": k, "old": original.get(k), "new": v}
               for k, v in task.items() if k not in ("history", "updated_at") and original.get(k) != v]
    mode = wd.source_mode(task.get("entity_id"), extra_mock=task.get("mode") == "mock")
    if not changes:
        return make_envelope(command="task-update", mode=mode, data_version=wd.data_version,
                             status="succeeded", items=[original],
                             extra={"task_id": task_id, "old_status": old_status, "new_status": target,
                                    "note": "无变化，幂等返回", "is_mock": mode == "mock"})
    task["updated_at"] = now_iso()
    task.setdefault("history", []).append({"status": target, "at": now_iso(),
        "note": reason or "字段/状态更新", "changed": changes,
        "evidence": request.get("completion_evidence")})
    for i, existing in enumerate(wd.index["tasks"]):
        if existing["task_id"] == task_id:
            wd.index["tasks"][i] = task
            break
    wd.save()
    wd.write_readme()
    return make_envelope(command="task-update", mode=mode, data_version=wd.data_version,
                         status="succeeded", items=[task],
                         extra={"task_id": task_id, "old_status": old_status, "new_status": target,
                                "changed_fields": [c["field"] for c in changes], "is_mock": mode == "mock"},
                         next_actions=["任务完成不改变续费状态；复访仍需另行约定"])
