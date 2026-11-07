"""行动计划测试：需已核对纪要、相对日期换算并保留原话、缺负责人/期限为草案、
拒绝联系范围只按显式 scope 屏蔽、完成需证据、更新幂等。"""
from __future__ import annotations

import copy

from _harness import fresh_workdir, run_tests, CSV, MINUTES_RAW, load_sample

from ms_import import run_import
from ms_minutes import run_extract, run_confirm
from ms_plan import run_plan, run_task_update
from ms_store import Workdir
from ms_common import MSError, EXIT_VALIDATION, EXIT_NOT_FOUND


def _eid(wd, bk):
    for eid, e in Workdir(wd).all_entities().items():
        if e.get("business_key") == bk:
            return eid
    raise AssertionError("no %s" % bk)


def _confirmed_visit(wd):
    extraction = load_sample("minutes_extraction_sample.json")
    extraction["entity_id"] = _eid(wd, "M001")
    ext = run_extract(MINUTES_RAW, extraction, wd)
    vid = ext["visit_id"]
    run_confirm(vid, load_sample("minutes_confirm_sample.json"), wd)
    return vid


def _setup_confirmed():
    wd = fresh_workdir()
    run_import(CSV, wd)
    vid = _confirmed_visit(wd)
    return wd, vid


def test_plan_requires_confirmed_visit():
    wd = fresh_workdir()
    run_import(CSV, wd)
    extraction = load_sample("minutes_extraction_sample.json")
    extraction["entity_id"] = _eid(wd, "M001")
    ext = run_extract(MINUTES_RAW, extraction, wd)  # 仍是 draft
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = ext["visit_id"]
    try:
        run_plan(req, wd)
        raise AssertionError("未核对纪要不能生成正式计划")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_relative_deadline_resolved_and_original_kept():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    env = run_plan(req, wd)
    tasks = {t["deliverable"]: t for t in env["tasks"]}
    plan_task = tasks["传感器选型对比方案（交期+本地服务响应）"]
    assert plan_task["deadline"] == "2026-10-30", plan_task["deadline"]  # 2026-10-20 + 10 天
    assert plan_task["deadline_original"] == "下周五之前", "必须保留原话"
    assert "2026-10-20" in plan_task["deadline_basis"]


def test_missing_owner_or_deadline_is_draft():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    env = run_plan(req, wd)
    tasks = {t["deliverable"]: t for t in env["tasks"]}
    # task2 国产替代资料：无 owner、无 deadline → draft
    assert tasks["国产替代传感器对比资料整理"]["status"] == "draft"
    # task4 安排样机测试：有 owner 无 deadline → draft
    assert tasks["安排样机测试"]["status"] == "draft"
    # task1 有 owner+deadline → pending_assignment
    assert tasks["传感器选型对比方案（交期+本地服务响应）"]["status"] == "pending_assignment"


def test_no_contact_blocks_only_matching_scope():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    env = run_plan(req, wd)
    tasks = {t["deliverable"]: t for t in env["tasks"]}
    phone = tasks["电话跟进方案反馈"]   # channel=电话推销 命中拒绝范围
    assert phone["status"] == "blocked"
    assert "拒绝联系" in phone["block_reason"]
    # 邮件任务不受“拒绝电话推销”影响
    assert tasks["传感器选型对比方案（交期+本地服务响应）"]["status"] != "blocked"


def test_completed_requires_evidence():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    env = run_plan(req, wd)
    tid = env["tasks"][0]["task_id"]
    try:
        run_task_update(tid, {"status": "completed"}, wd)
        raise AssertionError("无证据不能完成")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_task_update_with_evidence_and_idempotent():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    env = run_plan(req, wd)
    tid = env["tasks"][0]["task_id"]
    done = run_task_update(tid, {"status": "completed",
                                 "completion_evidence": "客户邮件确认收到"}, wd)
    assert done["items"][0]["status"] == "completed"
    assert done["items"][0]["completion_evidence"] == "客户邮件确认收到"
    # 再次完成：幂等（同状态非 completed 分支不适用；completed 会再追加，但不报错）
    again = run_task_update(tid, {"status": "completed",
                                  "completion_evidence": "客户邮件确认收到"}, wd)
    assert again["items"][0]["status"] == "completed"


def test_blocked_requires_reason():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    env = run_plan(req, wd)
    tid = env["tasks"][0]["task_id"]
    try:
        run_task_update(tid, {"status": "blocked"}, wd)
        raise AssertionError("阻止任务必须给原因")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_invalid_absolute_date_rejected():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    req["tasks"] = [{"deliverable": "X", "owner": "李敏", "deadline": "2026-13-99"}]
    try:
        run_plan(req, wd)
        raise AssertionError("非法日期应拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_plan_idempotent_by_business_key():
    wd, vid = _setup_confirmed()
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    a = run_plan(req, wd)
    b = run_plan(req, wd)
    # 第二次全部命中已有任务（reused），不新增
    assert b["created"] == 0, b["created"]
    assert len(Workdir(wd).index["tasks"]) == len(a["tasks"])


# ---- A1/A3/A4 回归：拒绝与保留链路 ------------------------------------------
def _plan_req(vid, tasks=None):
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    if tasks is not None:
        req["tasks"] = tasks
    return req


def test_plan_rejects_unlocatable_source_item():
    wd, vid = _setup_confirmed()
    req = _plan_req(vid, [{"deliverable": "X", "source_item_id": "不存在的来源项",
                           "owner": "李敏", "deadline": "2026-11-01"}])
    try:
        run_plan(req, wd)
        raise AssertionError("无法定位的来源项不能生成正式任务")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_plan_entity_conflict_with_visit_rejected():
    wd, vid = _setup_confirmed()
    req = _plan_req(vid)
    req["entity_id"] = _eid(wd, "M002")   # 与纪要主体 M001 冲突
    try:
        run_plan(req, wd)
        raise AssertionError("计划 entity_id 与纪要主体冲突必须拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_plan_entity_not_in_archive_rejected():
    wd = fresh_workdir()
    try:
        run_plan({"entity_id": "E-nope", "tasks": []}, wd)
        raise AssertionError("不存在的主体必须拒绝")
    except MSError as e:
        assert e.code == EXIT_NOT_FOUND


def test_plan_rejects_unknown_field():
    wd, vid = _setup_confirmed()
    req = _plan_req(vid, [{"deliverable": "X", "owner": "李敏",
                           "deadline": "2026-11-01", "surprise": 1}])
    try:
        run_plan(req, wd)
        raise AssertionError("未知字段必须报错")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_change_owner_updates_same_task_no_duplicate():
    """改负责人/期限应更新同一 task_id，不新建重复任务（A4）。"""
    wd, vid = _setup_confirmed()
    a = run_plan(_plan_req(vid), wd)
    tid = a["tasks"][0]["task_id"]
    n_before = len(Workdir(wd).index["tasks"])
    run_task_update(tid, {"owner": "新负责人", "deadline": "2026-11-15"}, wd)
    b = run_plan(_plan_req(vid), wd)
    assert b["created"] == 0
    assert len(Workdir(wd).index["tasks"]) == n_before, "不得因改负责人新增任务"
    assert Workdir(wd).task_by_id(tid)["owner"] == "新负责人"


def test_task_update_edits_fields_and_advances():
    """task-update 提供补齐 owner/deadline 并推进的正规入口（A4）。"""
    wd, vid = _setup_confirmed()
    env = run_plan(_plan_req(vid), wd)
    tasks = {t["deliverable"]: t for t in env["tasks"]}
    draft = tasks["国产替代传感器对比资料整理"]
    assert draft["status"] == "draft"
    tid = draft["task_id"]
    upd = run_task_update(tid, {"owner": "王芳", "deadline": "2026-11-05",
                                "status": "pending_assignment"}, wd)
    t = upd["items"][0]
    assert t["owner"] == "王芳" and t["deadline"] == "2026-11-05"
    assert t["status"] == "pending_assignment"
    # 未知字段报错
    try:
        run_task_update(tid, {"bogus": 1}, wd)
        raise AssertionError("未知字段必须报错")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_advance_requires_owner_and_deadline():
    wd, vid = _setup_confirmed()
    env = run_plan(_plan_req(vid), wd)
    tasks = {t["deliverable"]: t for t in env["tasks"]}
    tid = tasks["国产替代传感器对比资料整理"]["task_id"]  # draft，无 owner/deadline
    try:
        run_task_update(tid, {"status": "in_progress"}, wd)
        raise AssertionError("缺负责人/期限不得推进到执行态")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_contact_block_cannot_be_bypassed_by_status():
    """受阻电话任务不能凭 status 绕过联系限制；需新的明确依据（A4）。"""
    wd, vid = _setup_confirmed()
    env = run_plan(_plan_req(vid), wd)
    tasks = {t["deliverable"]: t for t in env["tasks"]}
    phone = tasks["电话跟进方案反馈"]
    assert phone["status"] == "blocked"
    tid = phone["task_id"]
    # 无依据直接恢复执行 → 拒绝
    try:
        run_task_update(tid, {"status": "in_progress"}, wd)
        raise AssertionError("不能凭 status 绕过拒绝联系范围")
    except MSError as e:
        assert e.code == EXIT_VALIDATION
    # 自由文本原因不能替代已核对的客户许可
    try:
        run_task_update(tid, {"status": "in_progress",
                             "reason": "客户书面同意恢复电话沟通（2026-11-01）"}, wd)
        raise AssertionError("原因不能解除限制")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_completed_is_terminal_cannot_reopen():
    wd, vid = _setup_confirmed()
    env = run_plan(_plan_req(vid), wd)
    tid = env["tasks"][0]["task_id"]
    run_task_update(tid, {"status": "completed", "completion_evidence": "已发送并获确认"}, wd)
    try:
        run_task_update(tid, {"status": "in_progress"}, wd)
        raise AssertionError("完成态不能凭 status 恢复执行")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_completion_replay_no_duplicate_history():
    wd, vid = _setup_confirmed()
    env = run_plan(_plan_req(vid), wd)
    tid = env["tasks"][0]["task_id"]
    run_task_update(tid, {"status": "completed", "completion_evidence": "证据A"}, wd)
    h1 = len(Workdir(wd).task_by_id(tid)["history"])
    run_task_update(tid, {"status": "completed", "completion_evidence": "证据A"}, wd)
    h2 = len(Workdir(wd).task_by_id(tid)["history"])
    assert h2 == h1, "同证据重复完成不应追加历史"
    assert Workdir(wd).task_by_id(tid)["completion_evidence"] == "证据A"


def test_plan_rerun_preserves_completion():
    """重复生成计划不覆盖已完成任务的状态/证据（A4）。"""
    wd, vid = _setup_confirmed()
    env = run_plan(_plan_req(vid), wd)
    tid = env["tasks"][0]["task_id"]
    run_task_update(tid, {"status": "completed", "completion_evidence": "证据A"}, wd)
    run_plan(_plan_req(vid), wd)   # 再跑一次计划
    t = Workdir(wd).task_by_id(tid)
    assert t["status"] == "completed" and t["completion_evidence"] == "证据A"


def test_two_plans_get_distinct_plan_ids():
    """同秒多次计划不覆盖旧成果（A2 碰撞安全 ID）。"""
    wd, vid = _setup_confirmed()
    a = run_plan(_plan_req(vid), wd)
    b = run_plan(_plan_req(vid), wd)
    assert a["plan_id"] != b["plan_id"]


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_plan: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
