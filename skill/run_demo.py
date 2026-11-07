#!/usr/bin/env python3
"""一条命令跑完整案例：导入(mock) → 筛选 → 简报 → 纪要抽取/核对 → 计划 → 任务更新 → MCP(mock)。

用途：让验证者快速看到各子命令的真实输出与落盘文件。
注意：脚本内的条件树、纪要抽取、计划任务均为“宿主等价输入”的样例，
  用于演示脚本的可复核计算，不代表脚本具备自然语言解析，也不代表真实企查查查询。
全程 mock 的源头在**导入**（is_mock=True）：brief/minutes/plan/task-update/index/导出
全部继承该来源模式，不在计划等处手工补 is_mock。

用法：python3 run_demo.py [--workdir ./demo-work]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "scripts"))

from ms_import import run_import           # noqa: E402
from ms_filter import run_filter           # noqa: E402
from ms_brief import run_brief             # noqa: E402
from ms_minutes import run_extract, run_confirm  # noqa: E402
from ms_plan import run_plan, run_task_update    # noqa: E402
from ms_mcp import run_mcp_search, run_mcp_ingest  # noqa: E402
from ms_store import Workdir               # noqa: E402
from ms_common import write_text_atomic    # noqa: E402

DATA = os.path.join(HERE, "data")


def _load(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
        return json.load(f)


def _line(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="./demo-work")
    args = ap.parse_args()
    wd_path = os.path.abspath(args.workdir)
    trace = []

    _line("1) 导入会员 CSV（声明为模拟数据：全程 mock 的源头）")
    imp = run_import(os.path.join(DATA, "members_sample.csv"), wd_path, is_mock=True)
    print("status=%s counts=%s data_version=%s mode=%s"
          % (imp["status"], imp["counts"], imp["data_version"], imp["mode"]))
    print("字段映射：", json.dumps(imp["suggested_mapping"], ensure_ascii=False))
    print("来源模式说明：", imp["mode_note"])
    trace.append(("import", imp["mode"], imp["counts"]))

    _line("2) 幂等重复导入（应全部 unchanged，data_version 不变；模式仍为 mock）")
    imp2 = run_import(os.path.join(DATA, "members_sample.csv"), wd_path, is_mock=True)
    print("counts=%s data_version=%s mode=%s"
          % (imp2["counts"], imp2["data_version"], imp2["mode"]))
    trace.append(("reimport-idempotent", imp2["counts"]["unchanged"], imp2["data_version"]))

    _line("3) 按演示(mock)条件树筛选")
    flt = run_filter(_load("filter_request_demo.json"), wd_path)
    print("mode=%s counts=%s" % (flt["mode"], flt["counts"]))
    for e in flt["eligible"]:
        print("  eligible:", e["entity_id"], e["name"])
    for e in flt["needs_review"]:
        print("  needs_review:", e["entity_id"], e["name"], e["unknown_fields"])
    for e in flt["excluded"]:
        print("  excluded:", e["entity_id"], e["name"], "|", e["reason"], "|", e["reason_detail"])
    trace.append(("filter", flt["mode"], flt["counts"]))

    _line("4) 为头号符合企业生成拜访简报")
    top = flt["eligible"][0]["entity_id"] if flt["eligible"] else None
    ctx = {
        "goals": ["了解扩产后的传感器采购计划", "确认国产替代评估标准"],
        "questions": ["目前传感器月用量与峰值？", "交期与本地服务响应哪项权重更高？"],
        "suggestions": ["准备交期承诺与本地服务SLA说明（AI建议，需人员判断）"],
        "preparations": ["带选型对比表", "带样机测试流程说明"],
    }
    brf = run_brief(top, wd_path, context=ctx, purpose="评估国产传感器替代与合作深化")
    print("brief_id=%s mode=%s facts=%d unknowns=%d"
          % (brf["brief_id"], brf["mode"], len(brf["items"]), len(brf["unknowns"])))
    trace.append(("brief", brf["mode"], len(brf["unknowns"])))

    _line("5) 结构化原始纪要 → 待核对版（主体绑定来自真实档案，不写死 entity_id）")
    extraction = _load("minutes_extraction_sample.json")
    # A3：按业务编号从档案解析真实 entity_id 并注入；identity_evidence 与绑定一致（不硬编码）
    _wd_obj = Workdir(wd_path)
    _target_bk = "M001"
    _target_eid = next((eid for eid, e in _wd_obj.all_entities().items()
                        if e.get("business_key") == _target_bk), None)
    if _target_eid is None:
        raise SystemExit("档案中未找到业务编号 %s，无法演示主体绑定" % _target_bk)
    extraction["entity_id"] = _target_eid
    # 不写 extraction["is_mock"]：模式由导入声明继承，验证“贯通”而非“每层各写一遍”
    extraction["identity_evidence"] = {
        "key_type": "business_key", "value": _target_bk,
        "raw_citation": "会员编号：" + _target_bk,
    }
    ext = run_extract(os.path.join(DATA, "minutes_raw.txt"), extraction, wd_path)
    visit_id = ext["visit_id"]
    _tname = _wd_obj.get_entity(_target_eid).get("fields", {}).get("name")
    print("绑定主体=%s(%s) | visit_id=%s | visit_status=%s | mode=%s"
          % (_tname, _target_eid, visit_id, ext.get("visit_status", ext["status"]), ext["mode"]))
    for i in ext["issues"]:
        print("  issue:", i.get("kind"), "-", i.get("reason", "")[:50])
    trace.append(("minutes-extract", ext["mode"], len(ext["issues"])))

    _line("6) 人员核对 → 已核对版")
    conf = run_confirm(visit_id, _load("minutes_confirm_sample.json"), wd_path)
    print("visit_status=%s mode=%s confirmed_needs=%d confirmed_commitments=%d"
          % (conf.get("visit_status", conf["status"]), conf["mode"],
             len(conf["confirmed_needs"]), len(conf["confirmed_commitments"])))
    trace.append(("minutes-confirm", conf["mode"], len(conf["confirmed_needs"])))

    _line("7) 由已核对纪要生成行动计划（模式继承自导入/纪要，不手写 is_mock）")
    plan_req = _load("plan_request_sample.json")
    plan_req["visit_id"] = visit_id
    # 不写 plan_req["is_mock"]：模式必须由上游继承，否则就是“只在计划里补一口”的假贯通
    plan = run_plan(plan_req, wd_path)
    print("plan_id=%s mode=%s created=%d" % (plan["plan_id"], plan.get("mode"), plan["created"]))
    for t in plan["tasks"]:
        print("  [%s] %s | owner=%s | deadline=%s(原话:%s) | %s"
              % (t["status"], t["deliverable"][:18], t["owner"], t["deadline"],
                 t.get("deadline_original"), t.get("block_reason") or ""))
    trace.append(("plan", plan.get("mode"), plan["created"]))

    _line("8) 补齐草案并推进（task-update 正规字段入口，A4）")
    _by_deliv = {t["deliverable"]: t for t in plan["tasks"]}
    # 8a) 国产替代资料：草案(无 owner/deadline) → 补齐 owner+deadline 后推进为待执行
    _d = _by_deliv.get("国产替代传感器对比资料整理")
    if _d and _d["status"] == "draft":
        _upd = run_task_update(_d["task_id"],
                               {"owner": "王芳", "deadline": "2026-11-05",
                                "status": "pending_assignment"}, wd_path)
        _t = _upd["items"][0]
        print("  补齐并推进：%s -> owner=%s deadline=%s status=%s（原 draft）mode=%s"
              % (_d["deliverable"], _t["owner"], _t["deadline"], _t["status"], _upd["mode"]))
        trace.append(("task-update-field", _upd["mode"], _t["status"]))
    # 8b) 安排样机测试：有 owner 无 deadline，直接推进执行态必须被拒
    _d2 = _by_deliv.get("安排样机测试")
    if _d2:
        try:
            run_task_update(_d2["task_id"], {"status": "in_progress"}, wd_path)
            print("  [异常] 缺期限竟能推进到执行态")
        except Exception as e:  # noqa: BLE001
            print("  缺期限推进被拒：", getattr(e, "message", str(e))[:40])

    _line("9) 任务完成（需证据；无证据应失败；重复完成幂等）")
    # 找一个非 blocked 的任务
    target = next((t for t in plan["tasks"] if t["status"] != "blocked"), None)
    if target:
        tid = target["task_id"]
        # 先尝试无证据完成（应报错）
        try:
            run_task_update(tid, {"status": "completed"}, wd_path)
            print("  [异常] 无证据竟完成")
        except Exception as e:  # noqa: BLE001
            print("  无证据完成被拒：", getattr(e, "message", str(e))[:40])
        # 提供证据后完成
        done = run_task_update(tid, {"status": "completed",
                                     "completion_evidence": "客户邮件确认已收到方案（2026-10-30）"}, wd_path)
        print("  完成后状态：", done["items"][0]["status"], "evidence:", done["items"][0]["completion_evidence"][:20])
        # 重复完成（幂等）
        again = run_task_update(tid, {"status": "completed",
                                      "completion_evidence": "客户邮件确认已收到方案（2026-10-30）"}, wd_path)
        print("  重复完成幂等：", again.get("note") or again["items"][0]["status"])
        trace.append(("task-update", done["mode"], done["items"][0]["status"]))

    _line("10) 外部检索意图（无工具 → no_tool，不执行）")
    ms = run_mcp_search({"region": "深圳", "industry": "工业传感器"}, [], wd_path)
    print("external_status=%s" % ms["external_status"], "| note:", ms["note"][:40])
    trace.append(("mcp-search", ms["external_status"], 0))

    _line("11) 归一化 mock 候选回包（候选≠已核实）")
    ing = run_mcp_ingest(_load("mcp_candidates_mock.json"), wd_path)
    print("mode=%s candidates=%d" % (ing["mode"], len(ing["candidates"])))
    for c in ing["candidates"]:
        print("  %s | %s | %s | is_mock=%s" % (c["name"], c["relation_to_local"],
                                               c["verification_state"], c["is_mock"]))
    trace.append(("mcp-ingest", ing["mode"], len(ing["candidates"])))

    _line("汇总：落盘文件")
    wd = Workdir(wd_path)
    print("工作目录：", wd.path)
    print("来源模式 data_is_mock：", wd.data_is_mock(),
          "（mock 源头=导入声明 is_mock=True，下游全程继承）")
    for rid, r in sorted(wd.index["results"].items()):
        print("  %s [%s] mode=%s -> %s" % (rid, r.get("type"), r.get("mode", "-"),
                                           r.get("paths", {}).get("md") or r.get("path")))
    print("索引文件：", os.path.join(wd.path, "index.json"))
    print("人类可读索引：", os.path.join(wd.path, "README.md"))

    # 写一份 demo 追踪摘要
    lines = ["# run_demo 执行追踪", "",
             "工作目录：`%s`" % wd.path, "",
             "来源模式：**mock**（导入声明 is_mock=true，下游简报/纪要/计划/更新/索引/导出全程继承，"
             "不在计划等处手工补标识）", "",
             "| 步骤 | 结果/模式 | 量 |", "|---|---|---|"]
    for step, res, n in trace:
        lines.append("| %s | %s | %s |" % (step, res, n))
    lines += ["", "> 条件树/纪要抽取/计划任务为宿主等价样例输入；全程 mock，脚本只做可复核计算，"
                    "未做真实外部调用、非真实承诺/机构政策。"]
    write_text_atomic(os.path.join(wd.path, "results", "demo_trace.md"), "\n".join(lines) + "\n")
    print("\nDEMO 完成。")


if __name__ == "__main__":
    main()
