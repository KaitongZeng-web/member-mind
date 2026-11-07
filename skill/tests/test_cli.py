"""CLI 子进程集成测试。

重点：test_cli_reorder_preserves_binding —— 真正以子进程调用 ms_cli.py 导入
“行序颠倒”的 CSV，断言主体 entity_id 稳定、业务编号不错绑、任务→主体关联不变。
另覆盖 CLI 退出码契约（0 成功 / 2 用法 / 3 找不到 / 5 校验）。
"""
from __future__ import annotations

import csv
import json
import os

from _harness import fresh_workdir, run_tests, cli, CSV, MINUTES_RAW, load_sample


def _read_index(wd):
    with open(os.path.join(wd, "index.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _entity_snapshot(wd):
    idx = _read_index(wd)
    return {eid: (e.get("business_key"), e.get("fields", {}).get("name"))
            for eid, e in idx["entities"].items()}


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _make_reordered_csv(wd):
    rows = list(csv.reader(open(CSV, encoding="utf-8")))
    reordered = [rows[0]] + list(reversed(rows[1:]))
    p = os.path.join(wd, "reordered.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(reordered)
    return p


def test_cli_reorder_preserves_binding():
    wd = fresh_workdir()

    # 1) CLI 导入原始 CSV
    r = cli(["import", "--file", CSV], wd)
    assert r.returncode == 0, r.stderr
    env1 = json.loads(r.stdout)
    assert env1["schema_version"] and env1["command"] == "import"
    snap_before = _entity_snapshot(wd)
    m001_ids = [eid for eid, (bk, nm) in snap_before.items() if bk == "M001"]
    assert m001_ids, "应存在 M001 主体"
    m001_id = m001_ids[0]
    m001_name = snap_before[m001_id][1]

    # 2) 通过完整 CLI 链路为该主体生成任务（extract → confirm → plan）
    extraction = load_sample("minutes_extraction_sample.json")
    extraction["entity_id"] = m001_id
    ext_path = os.path.join(wd, "ext.json")
    _write_json(ext_path, extraction)
    r = cli(["minutes-extract", "--raw", MINUTES_RAW, "--extraction", "@" + ext_path], wd)
    assert r.returncode == 0, r.stderr
    vid = json.loads(r.stdout)["visit_id"]

    r = cli(["minutes-confirm", "--visit-id", vid,
             "--confirmations", "@" + os.path.join(os.path.dirname(CSV), "minutes_confirm_sample.json")], wd)
    assert r.returncode == 0, r.stderr

    plan_req = load_sample("plan_request_sample.json")
    plan_req["visit_id"] = vid
    plan_path = os.path.join(wd, "plan.json")
    _write_json(plan_path, plan_req)
    r = cli(["plan", "--request", "@" + plan_path], wd)
    assert r.returncode == 0, r.stderr
    plan = json.loads(r.stdout)
    tasks_before = sorted((t["task_id"], t["entity_id"]) for t in plan["tasks"])
    assert tasks_before, "应生成任务"
    assert all(eid == m001_id for _, eid in tasks_before), "任务应绑定到 M001 主体"

    # 3) CLI 导入“行序颠倒”的 CSV 到同一工作目录
    reordered = _make_reordered_csv(wd)
    r = cli(["import", "--file", reordered], wd)
    assert r.returncode == 0, r.stderr
    imp2 = json.loads(r.stdout)
    assert imp2["counts"]["added"] == 0 and imp2["counts"]["updated"] == 0, imp2["counts"]
    assert imp2["counts"]["unchanged"] == 8 and imp2["counts"]["needs_review"] == 2, imp2["counts"]

    # 4) 主体集合与绑定不变
    snap_after = _entity_snapshot(wd)
    assert set(snap_before) == set(snap_after), "entity_id 集合必须稳定（行重排不得改身份）"
    for eid, val in snap_before.items():
        assert snap_after[eid] == val, "主体 %s 绑定被行重排改变: %r -> %r" % (eid, val, snap_after[eid])
    # 顶层 business_key 与 fields.business_key 一致（错绑的典型症状）
    idx = _read_index(wd)
    for eid, e in idx["entities"].items():
        tb, fb = e.get("business_key"), e.get("fields", {}).get("business_key")
        if tb and fb:
            assert tb == fb, "顶层/字段 business_key 不一致（疑似错绑）: %s %s %s" % (eid, tb, fb)
    assert snap_after[m001_id] == ("M001", m001_name), "M001 主体不得被别行字段污染"

    # 5) 任务→主体关联不变
    tasks_after = sorted((t["task_id"], t["entity_id"]) for t in _read_index(wd)["tasks"])
    assert tasks_after == tasks_before, "任务→主体关联必须不变"
    for tid, eid in tasks_after:
        assert snap_after.get(eid, (None, None))[0] == "M001", "任务 %s 关联到错误主体" % tid


def test_cli_exit_codes():
    wd = fresh_workdir()

    # 用法错误 → 2
    r = cli(["no-such-command"], wd)
    assert r.returncode == 2, (r.returncode, r.stderr)

    # 文件不存在 → 3
    r = cli(["import", "--file", os.path.join(wd, "missing.csv")], wd)
    assert r.returncode == 3, (r.returncode, r.stderr)
    err = json.loads(r.stderr)
    assert err["ok"] is False and err["error"]["code"] == 3

    # 导入一个有效 CSV 后再测校验/找不到
    assert cli(["import", "--file", CSV], wd).returncode == 0

    # 未知字段条件 → 5（校验）
    bad_cond = json.dumps({"conditions": {"field": "no_such_field", "op": "eq", "value": "x"}})
    r = cli(["filter", "--request", bad_cond], wd)
    assert r.returncode == 5, (r.returncode, r.stderr)

    # --id-column 不存在 → 5（校验）
    r = cli(["import", "--file", CSV, "--id-column", "不存在的列"], wd)
    assert r.returncode == 5, (r.returncode, r.stderr)

    # brief 不存在实体 → 3
    r = cli(["brief", "--entity", "E-nope"], wd)
    assert r.returncode == 3, (r.returncode, r.stderr)

    # index 成功 → 0
    r = cli(["index"], wd)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["counts"]["entities"] == 10


def test_cli_bad_json_is_structured_parse_error():
    """坏 JSON 应由 CLI 输出约定错误(退出4)，而非原生异常栈（A6）。"""
    wd = fresh_workdir()
    r = cli(["filter", "--request", "{ not valid json"], wd)
    assert r.returncode == 4, (r.returncode, r.stderr)
    err = json.loads(r.stderr)
    assert err["ok"] is False and err["error"]["code"] == 4
    assert "Traceback" not in r.stderr


def test_cli_missing_json_file_is_not_found():
    wd = fresh_workdir()
    r = cli(["filter", "--request", "@" + os.path.join(wd, "nope.json")], wd)
    assert r.returncode == 3, (r.returncode, r.stderr)
    err = json.loads(r.stderr)
    assert err["error"]["code"] == 3


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_cli: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
