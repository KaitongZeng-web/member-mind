"""来源模式(mock/live)贯通回归：导入声明 → 简报 → 纪要 → 计划/更新 → 索引/导出。

主审实际检查发现的缺陷：`ms_import.py` 与 `ms_brief.py` 硬编码 `mode="live"`，
而 `run_demo.py` 宣称“全程 mock”——演示数据一路被标成真实来源，只有计划步骤
靠手工 `plan_req["is_mock"]=True` 补了一口，简报/纪要/任务更新/索引/导出全是 live。

本文件锁定最小明确契约：
- 导入可显式声明 `is_mock`（CLI `--mock`），落到主体与工作区（粘性，不自动降级）；
- brief / minutes-extract / plan / task-update 从上游**继承**来源模式，
  不接受能把 live 降级回去的外层输入；
- MD / CSV / README 等读者可见元信息必须明确 mock；
- 普通真实文件流程保持 live，不受影响；
- mode 只表示资料来源，**不代表已核实工商真实性**。
"""
from __future__ import annotations

import csv
import io
import json
import os

from _harness import fresh_workdir, run_tests, CSV, MINUTES_RAW, load_sample

from ms_import import run_import
from ms_filter import run_filter
from ms_brief import run_brief
from ms_minutes import run_extract, run_confirm
from ms_plan import run_plan, run_task_update
from ms_store import Workdir


def _eid(wd, bk):
    for eid, e in Workdir(wd).all_entities().items():
        if e.get("business_key") == bk:
            return eid
    raise AssertionError("no %s" % bk)


def _read(wd, rel):
    with open(os.path.join(wd, rel), "r", encoding="utf-8") as f:
        return f.read()


def _extraction_for(wd=None, **over):
    ext = load_sample("minutes_extraction_sample.json")
    ext.pop("is_mock", None)          # 不声明：必须从导入/档案继承
    if wd is not None:
        ext["entity_id"] = _eid(wd, "M001")
    ext.update(over)
    return ext


def _confirmed_visit(wd):
    ext = run_extract(MINUTES_RAW, _extraction_for(wd), wd)
    vid = ext["visit_id"]
    run_confirm(vid, load_sample("minutes_confirm_sample.json"), wd)
    return vid


def _plan_req(vid):
    req = load_sample("plan_request_sample.json")
    req["visit_id"] = vid
    req.pop("is_mock", None)          # 不写 is_mock：必须从纪要继承，不能变 live
    return req


# ---- mock 全程贯通 ------------------------------------------------------------
def test_mock_import_marks_entity_and_workspace():
    wd = fresh_workdir()
    env = run_import(CSV, wd, is_mock=True)
    assert env["mode"] == "mock"
    assert env["is_mock"] is True
    assert env["workspace_is_mock"] is True
    assert "不代表已核实工商真实性" in env["mode_note"]
    w = Workdir(wd)
    assert w.data_is_mock() is True
    assert w.index["data_provenance"]["is_mock"] is True
    assert all(e.get("is_mock") is True for e in w.all_entities().values()), \
        "mock 导入的每个主体都必须带来源标记"
    assert w.index["imports"][-1]["is_mock"] is True
    assert "含模拟(mock)数据" in _read(wd, "README.md")


def test_mock_flows_through_brief_minutes_plan_update():
    """真实贯通回归：mock 导入后，简报/纪要/计划/更新/索引/导出 mode 都不是 live。"""
    wd = fresh_workdir()
    run_import(CSV, wd, is_mock=True)
    eid = _eid(wd, "M001")

    brf = run_brief(eid, wd, purpose="评估国产替代")
    assert brf["mode"] == "mock", "brief 不得再把 mock 档案报成 live"
    assert brf["is_mock"] is True
    assert "演示(mock)导入数据" in _read(wd, brf["outputs"]["md"]), "简报 MD 必须显式 mock"

    ext = run_extract(MINUTES_RAW, _extraction_for(wd), wd)   # 未声明 is_mock
    assert ext["mode"] == "mock", "纪要必须继承档案/工作区来源模式"
    assert "演示(mock)纪要/导入数据" in _read(wd, ext["outputs"]["md"]), "纪要 MD 必须显式 mock"

    conf = run_confirm(ext["visit_id"], load_sample("minutes_confirm_sample.json"), wd)
    assert conf["mode"] == "mock"
    assert conf["is_mock"] is True

    plan = run_plan(_plan_req(ext["visit_id"]), wd)           # 未声明 is_mock
    assert plan["mode"] == "mock", "计划不得因请求没写 is_mock 就变 live"
    assert plan["is_mock"] is True
    plan_md = _read(wd, plan["outputs"]["md"])
    assert "演示(mock)" in plan_md, "计划 MD 必须显式 mock"
    plan_csv = _read(wd, plan["outputs"]["csv"])
    rows = list(csv.DictReader(io.StringIO(plan_csv)))
    assert rows and all(r["mode"] == "mock" for r in rows), "计划 CSV 必须带 mock 模式列"
    assert "mode" in plan_csv.splitlines()[0]

    upd = run_task_update(plan["tasks"][0]["task_id"],
                          {"owner": "王芳", "deadline": "2026-11-05",
                           "status": "pending_assignment"}, wd)
    assert upd["mode"] == "mock", "任务更新不得变 live"
    assert upd["is_mock"] is True
    assert Workdir(wd).task_by_id(plan["tasks"][0]["task_id"])["mode"] == "mock"

    flt = run_filter({"conditions": {"field": "region", "op": "exists"}}, wd)
    assert flt["mode"] == "mock", "筛选结果继承数据来源模式"
    assert flt["is_mock"] is True
    assert "演示(mock)数据" in _read(wd, flt["outputs"]["md"])

    readme = _read(wd, "README.md")
    assert "含模拟(mock)数据" in readme
    assert "| mock |" in readme, "结果索引表必须能看出哪些结果是 mock"


def test_mock_is_sticky_not_downgraded():
    """混入模拟后不能自动降为真实：后续真实导入/显式 is_mock=false 都不得翻回 live。"""
    wd = fresh_workdir()
    run_import(CSV, wd, is_mock=True)
    # 1) 同一工作区再导入真实文件（不声明 mock）→ 工作区仍为 mock
    run_import(CSV, wd)
    assert Workdir(wd).data_is_mock() is True, "粘性：不得被后续非 mock 导入降级"
    # 2) 纪要显式写 is_mock=false → 仍不得把已 mock 的来源降成 live
    ext = run_extract(MINUTES_RAW, _extraction_for(wd, is_mock=False), wd)
    assert ext["mode"] == "mock", "外层 false 不得把 mock 来源降级成 live"
    # 3) 计划同样不被降级
    conf = run_confirm(ext["visit_id"], load_sample("minutes_confirm_sample.json"), wd)
    assert conf["mode"] == "mock"
    plan = run_plan(_plan_req(ext["visit_id"]), wd)
    assert plan["mode"] == "mock"


def test_cli_mock_flag_and_index_report_mode():
    """CLI --mock 与 index 摘要必须如实反映来源模式。"""
    from _harness import cli, cli_json
    wd = fresh_workdir()
    r = cli(["import", "--file", CSV, "--mock"], wd)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["mode"] == "mock" and env["is_mock"] is True
    idx = cli_json(["index"], wd)
    assert idx["data_is_mock"] is True and idx["mode"] == "mock"


# ---- 普通真实文件流程保持可用 --------------------------------------------------
def test_real_import_stays_live_end_to_end():
    wd = fresh_workdir()
    env = run_import(CSV, wd)
    assert env["mode"] == "live" and env["is_mock"] is False
    assert "按真实本地文件处理" in env["mode_note"]
    assert Workdir(wd).data_is_mock() is False

    brf = run_brief(_eid(wd, "M001"), wd)
    assert brf["mode"] == "live"
    assert "本地真实文件导入数据" in _read(wd, brf["outputs"]["md"])

    ext = run_extract(MINUTES_RAW, _extraction_for(wd), wd)
    assert ext["mode"] == "live", "未声明 mock 的真实档案必须仍是 live"
    conf = run_confirm(ext["visit_id"], load_sample("minutes_confirm_sample.json"), wd)
    assert conf["mode"] == "live"
    plan = run_plan(_plan_req(ext["visit_id"]), wd)
    assert plan["mode"] == "live"
    rows = list(csv.DictReader(io.StringIO(_read(wd, plan["outputs"]["csv"]))))
    assert rows and all(r["mode"] == "live" for r in rows)
    upd = run_task_update(plan["tasks"][0]["task_id"], {"owner": "王芳"}, wd)
    assert upd["mode"] == "live"
    assert "含模拟(mock)数据" not in _read(wd, "README.md")


def test_declared_mock_minutes_only_entry_also_marked():
    """纯纪要入口（无会员表）显式声明 mock 也必须贯通到确认与计划。"""
    wd = fresh_workdir()
    ext = run_extract(MINUTES_RAW, _extraction_for(entity_id=None, is_mock=True), wd)
    assert ext["mode"] == "mock" and ext["is_mock"] is True
    conf = run_confirm(ext["visit_id"], load_sample("minutes_confirm_sample.json"), wd)
    assert conf["mode"] == "mock"
    plan = run_plan(_plan_req(ext["visit_id"]), wd)
    assert plan["mode"] == "mock", "纯纪要入口的 mock 标记必须贯通到计划"
    assert "演示(mock)" in _read(wd, plan["outputs"]["md"])


def test_import_is_mock_type_checked():
    from ms_common import MSError, EXIT_VALIDATION
    wd = fresh_workdir()
    run_import(CSV, wd)
    try:
        run_import(CSV, wd, is_mock="true")
        raise AssertionError("is_mock 必须是布尔值")
    except MSError as e:
        assert e.code == EXIT_VALIDATION
    try:
        run_extract(MINUTES_RAW, _extraction_for(wd, is_mock="true"), wd)
        raise AssertionError("extraction.is_mock 必须是布尔值")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_mode: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
