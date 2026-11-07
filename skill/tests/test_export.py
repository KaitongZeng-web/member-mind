"""导出测试：排除/待核实的 MD 与 CSV 必须用与 JSON 同源的 reason_detail。

复现主审实际检查发现的 P2 导出缺陷（Terra 复现目录
`/var/folders/pr/.../member-export-check-190d40qi`）：
- 企业己（虚构）因 `NOT(custom:主营行业 eq 贸易)` 不成立被排除，
  旧导出却写“未满足: custom:主营行业 eq 贸易”，CSV 只写“不满足筛选条件”；
- JSON 里 `reason_detail` 一直是正确的 `not(...实际：贸易)`。

即：解释逻辑（ms_filter）已修好，但**导出层**另有一套从 matched/failed_conditions
叶列表重建的文案，把 NOT 内部结果为 TRUE 的叶子误写成“未满足”，普通用户会被误导。
本文件锁定：MD/CSV/JSON 三方一致，且不再出现误导性文案。
"""
from __future__ import annotations

import csv
import io
import os

from _harness import fresh_workdir, run_tests

from ms_import import run_import
from ms_filter import run_filter
from ms_store import Workdir


def _setup():
    """复合条件数据：覆盖 NOT 失败、OR 未采用分支、硬排除、多字段未知。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "export_case.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["会员编号", "会员名称", "所在城市", "主营行业", "资质标签",
                    "续费情况", "联系意愿", "企业登记状态"])
        w.writerow(["M001", "企业甲", "深圳", "工业传感器", "高新技术企业",
                    "已到期未续费", "可联系", "存续"])   # eligible：OR 走行业分支
        w.writerow(["M002", "企业乙", "东莞", "自动化设备", "高新技术企业",
                    "未续费", "可联系", "存续"])             # eligible：OR 走资质分支
        w.writerow(["M003", "企业己", "深圳", "贸易", "高新技术企业",
                    "未续费", "可联系", "存续"])             # NOT(行业=贸易) 不成立
        w.writerow(["M004", "企业庚", "广州", "工业传感器", "",
                    "未续费", "可联系", "存续"])             # 城市不满足，OR 未采用
        w.writerow(["M005", "企业辛", "东莞", "工业传感器", "",
                    "已续费", "可联系", "存续"])             # 续费不满足
        w.writerow(["M006", "企业丙", "深圳", "工业传感器", "高新技术企业",
                    "未续费", "可联系", "注销"])             # 硬排除：登记状态
        w.writerow(["M007", "企业丁", "深圳", "工业传感器", "高新技术企业",
                    "未续费", "拒绝联系", "存续"])           # 硬排除：拒绝联系
        w.writerow(["M008", "企业壬", "深圳", "工业传感器", "",
                    "未续费", "", "存续"])                   # 联系意愿未知
        w.writerow(["M009", "企业癸", "深圳", "", "",
                    "未续费", "可联系", "存续"])             # 行业+资质均未知
    run_import(path, wd)
    return wd


def _request():
    return {
        "rule_version": "export-regression", "rule_is_mock": True,
        "conditions": {"logic": "and", "conditions": [
            {"field": "custom:所在城市", "op": "in", "value": ["深圳", "东莞"]},
            {"logic": "or", "conditions": [
                {"field": "custom:主营行业", "op": "eq", "value": "工业传感器"},
                {"field": "custom:资质标签", "op": "contains", "value": "高新技术企业"}]},
            {"logic": "not", "conditions": [
                {"field": "custom:主营行业", "op": "eq", "value": "贸易"}]},
            {"field": "custom:续费情况", "op": "in", "value": ["未续费", "已到期未续费"]},
            {"field": "custom:联系意愿", "op": "eq", "value": "可联系"}]},
        "hard_exclusions": [
            {"field": "custom:企业登记状态", "op": "eq", "value": "注销"},
            {"field": "custom:联系意愿", "op": "eq", "value": "拒绝联系"}],
    }


def _by_name(env, name):
    for e in env["eligible"] + env["excluded"] + env["needs_review"]:
        if e["name"] == name:
            return e
    raise AssertionError("no %s" % name)


def _read(wd, rel):
    with open(os.path.join(wd, rel), "r", encoding="utf-8") as f:
        return f.read()


def _rows(md_text):
    return list(csv.DictReader(io.StringIO(md_text)))


def _run():
    wd = _setup()
    env = run_filter(_request(), wd)
    return wd, env, _read(wd, env["outputs"]["md"]), _read(wd, env["outputs"]["csv"])


# ---- JSON ↔ MD ↔ CSV 三方一致 -------------------------------------------------
def test_excluded_md_renders_json_reason_detail():
    """企业己：MD 必须出现 JSON 的 reason_detail（not(...实际：贸易)），不是“未满足: ...”。"""
    wd, env, md, _ = _run()
    j = _by_name(env, "企业己")
    assert j in env["excluded"]
    assert "not(custom:主营行业 eq 贸易（实际：贸易）)" in j["reason_detail"], j["reason_detail"]
    assert j["reason_detail"] in md, "MD 必须原样呈现 JSON 的 reason_detail"
    assert "未满足" not in md, "误导性文案“未满足: <条件>”不得再出现"
    assert "未满足" not in j["reason_detail"]


def test_excluded_csv_reason_detail_matches_json():
    """CSV 必须有 reason_detail 列，且与 JSON 逐字一致（不能只写“不满足筛选条件”）。"""
    wd, env, _, csv_text = _run()
    rows = {r["entity_id"]: r for r in _rows(csv_text)}
    header = csv_text.splitlines()[0].split(",")
    assert header[:11] == ["decision", "entity_id", "name", "industry", "region",
                           "member_status", "renewal_status", "contactable_status",
                           "reason", "reason_detail", "unknown_fields"], header
    assert len(rows) == 9, "9 个主体都必须导出"
    for e in env["excluded"] + env["needs_review"]:
        r = rows[e["entity_id"]]
        assert r["decision"] == ("excluded" if e in env["excluded"] else "needs_review")
        assert r["reason_detail"] == e["reason_detail"], (r["entity_id"], r["reason_detail"])
        assert r["reason_detail"] != "", "排除/待核实不能只留分类 reason"
        assert r["reason_detail"] != r["reason"], "reason_detail 必须比分类更具体"
    # 旧缺陷复现行：CSV 只有“不满足筛选条件”，没有决定性分支与实测值
    assert rows[_by_name(env, "企业己")["entity_id"]]["reason"] == "不满足筛选条件"
    assert "贸易" in rows[_by_name(env, "企业己")["entity_id"]]["reason_detail"]


def test_excluded_md_and_csv_agree_on_same_text():
    """同一决定性文案必须同时出现在 MD 与 CSV（读者从任一口径看到同样的理由）。"""
    wd, env, md, csv_text = _run()
    for e in env["excluded"] + env["needs_review"]:
        assert e["reason_detail"] in md
        assert e["reason_detail"] in csv_text, e["entity_id"]


# ---- NOT / OR 语义不被误写 ----------------------------------------------------
def test_not_true_leaf_never_labeled_unmet():
    """NOT 内部叶子为 TRUE：不能说它“未满足”，必须说明 not(...) 未成立。"""
    wd, env, md, csv_text = _run()
    j = _by_name(env, "企业己")
    leaf = j["failed_conditions"][0]
    assert leaf["field"] == "custom:主营行业" and leaf["result"] == "TRUE"
    # 决定性叶子是 TRUE，但导出文案绝不能说“未满足 custom:主营行业 eq 贸易”
    assert "未满足: custom:主营行业" not in md
    assert "未满足: custom:主营行业" not in csv_text
    assert "not(" in j["reason_detail"]
    # 也不能把 NOT 的否定语义丢掉，只报一个裸条件
    assert "not(custom:主营行业 eq 贸易（实际：贸易）)" in md


def test_unused_or_branch_not_in_export_reason():
    """企业庚城市不满足：OR 中未采用的 FALSE 分支不得出现在 MD/CSV 理由里。"""
    wd, env, md, csv_text = _run()
    g = _by_name(env, "企业庚")
    assert g in env["excluded"]
    assert g["reason_detail"] in md and g["reason_detail"] in csv_text
    assert "工业传感器" not in g["reason_detail"], g["reason_detail"]
    assert "资质标签" not in g["reason_detail"], g["reason_detail"]
    assert "custom:所在城市 in 深圳/东莞（实际：广州）" in g["reason_detail"]


def test_hard_exclusion_detail_has_branch_and_actual():
    """硬排除也要表达实际决定分支和实际值，不能只写“命中硬排除规则”。"""
    wd, env, md, csv_text = _run()
    for name, actual in (("企业丙", "注销"), ("企业丁", "拒绝联系")):
        e = _by_name(env, name)
        assert e in env["excluded"]
        assert e["reason"] == "命中硬排除规则"
        assert "命中硬排除规则：" in e["reason_detail"], e["reason_detail"]
        assert "实际：%s" % actual in e["reason_detail"], e["reason_detail"]
        assert e["reason_detail"] in md and e["reason_detail"] in csv_text


def test_needs_review_detail_lists_decisive_unknown_only():
    """待核实：MD/CSV 的说明必须只列决定性未知分支与未知字段。"""
    wd, env, md, csv_text = _run()
    gui = _by_name(env, "企业癸")
    assert gui in env["needs_review"]
    assert gui["unknown_fields"] == ["custom:主营行业", "custom:资质标签"], gui["unknown_fields"]
    assert "字段缺失，未知" in gui["reason_detail"]
    assert gui["reason_detail"] in md and gui["reason_detail"] in csv_text
    ren = _by_name(env, "企业壬")
    assert ren["unknown_fields"] == ["custom:联系意愿"]
    assert ren["reason_detail"] in md and ren["reason_detail"] in csv_text
    # 已满足的分支不得被当成未知原因
    assert "所在城市" not in ren["reason_detail"]


def test_eligible_export_keeps_or_not_and_actual_values():
    """eligible 的命中理由同样保持 OR/NOT 语义与实测值（MD 与 CSV 一致）。"""
    wd, env, md, csv_text = _run()
    jia = _by_name(env, "企业甲")
    assert jia in env["eligible"]
    reasons = "；".join(jia["reasons"])
    assert "(custom:主营行业 eq 工业传感器（实际：工业传感器） or " \
           "custom:资质标签 contains 高新技术企业（实际：高新技术企业）)" in reasons
    assert "not(custom:主营行业 eq 贸易（实际：工业传感器）)" in reasons
    assert reasons in md, "MD 命中理由必须与 JSON 一致"
    row = {r["entity_id"]: r for r in _rows(csv_text)}[jia["entity_id"]]
    assert row["decision"] == "eligible"
    assert row["reason"] == reasons, "CSV reason 列必须与 JSON reasons 一致"
    assert row["reason_detail"] == "", "eligible 无 reason_detail（理由在 reason 列）"


def test_export_files_written_and_indexed():
    wd, env, md, csv_text = _run()
    assert md.startswith("# 会员筛选结果")
    assert "排除原因（决定性分支与实测值）" in md
    assert "说明（决定性未知分支）" in md
    idx = Workdir(wd).index["results"]
    assert env["result_id"] in idx
    assert idx[env["result_id"]]["mode"] == "mock"
    assert os.path.exists(os.path.join(wd, env["outputs"]["md"]))
    assert os.path.exists(os.path.join(wd, env["outputs"]["csv"]))


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_export: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
