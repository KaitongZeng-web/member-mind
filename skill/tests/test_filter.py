"""三值逻辑筛选测试：缺失不因 NOT 变真、硬排除优先、eligible/excluded/needs_review
分流、非法条件即错、exists 反映已知有无、优先级免责声明。

另复现 Terra P2 / Astra P2.1 的解释缺陷：复合条件必须按三值解释树报告**决定性**
分支——NOT 内部的 FALSE 不能当成排除理由、OR 中未采用的 FALSE 分支不能平铺成排除
理由；以及同秒两次筛选不得共用结果 ID/文件。"""
from __future__ import annotations

import csv
import os

from _harness import fresh_workdir, run_tests, CSV, load_sample

from ms_import import run_import
from ms_filter import run_filter, eval_condition, validate_condition, PRIORITY_DISCLAIMER
from ms_store import Workdir
from ms_common import MSError, EXIT_VALIDATION


def _setup():
    wd = fresh_workdir()
    run_import(CSV, wd)
    return wd


def _eid(wd, bk):
    for eid, e in Workdir(wd).all_entities().items():
        if e.get("business_key") == bk:
            return eid
    raise AssertionError("no %s" % bk)


def _routes(env):
    return ({e["entity_id"] for e in env["eligible"]},
            {e["entity_id"] for e in env["excluded"]},
            {e["entity_id"] for e in env["needs_review"]})


def _setup_composite():
    """构造与 Terra 02 同型的复合条件数据（含 NOT 命中与未采用的 OR 分支）。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "composite.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["会员编号", "会员名称", "所在城市", "主营行业", "资质标签",
                    "续费情况", "联系意愿", "企业登记状态"])
        w.writerow(["M001", "企业甲", "深圳", "工业传感器", "高新技术企业",
                    "已到期未续费", "可联系", "存续"])
        w.writerow(["M002", "企业乙", "东莞", "自动化设备", "高新技术企业",
                    "未续费", "可联系", "存续"])
        w.writerow(["M003", "企业己", "深圳", "贸易", "高新技术企业",
                    "未续费", "可联系", "存续"])   # NOT(行业=贸易) 不成立
        w.writerow(["M004", "企业庚", "广州", "工业传感器", "",
                    "未续费", "可联系", "存续"])   # 城市不满足，OR 走 FALSE 分支
        w.writerow(["M005", "企业辛", "东莞", "工业传感器", "",
                    "已续费", "可联系", "存续"])   # 续费不满足
        w.writerow(["M006", "企业壬", "深圳", "工业传感器", "",
                    "未续费", "", "存续"])         # 联系意愿未知
    run_import(path, wd)
    return wd


def _composite_request():
    return {
        "rule_version": "composite-test", "rule_is_mock": True,
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



def test_demo_filter_split():
    wd = _setup()
    env = run_filter(load_sample("filter_request_demo.json"), wd)
    c = env["counts"]
    assert c["total"] == 10, c
    assert c["eligible"] == 3, c      # M001 M002 M009
    assert c["needs_review"] == 2, c  # M005(renewal unknown) M010(contactable unknown)
    assert c["excluded"] == 5, c      # M003 M004 M008 杭州×2
    elig, exc, review = _routes(env)
    assert _eid(wd, "M009") in elig, "加入日期未知但不在条件中的 M009 应 eligible"
    assert _eid(wd, "M005") in review
    assert _eid(wd, "M010") in review
    assert _eid(wd, "M008") in exc  # 硬排除：已注销
    assert env["mode"] == "mock"      # demo 规则标记 mock


def test_not_unknown_stays_unknown():
    wd = _setup()
    cond = {"logic": "not", "conditions": [
        {"field": "renewal_status", "op": "eq", "value": "未续费"}]}
    env = run_filter({"conditions": cond, "rule_is_mock": True}, wd)
    elig, exc, review = _routes(env)
    m005 = _eid(wd, "M005")   # renewal 未知
    m004 = _eid(wd, "M004")   # renewal 已续费 → NOT(未续费)=TRUE → eligible
    assert m005 in review, "NOT(UNKNOWN) 必须=UNKNOWN→needs_review，绝不能因 NOT 变 eligible"
    assert m004 in elig
    assert m005 not in elig and m005 not in exc


def test_missing_not_true_via_not_exists_semantics():
    """exists/not_exists 反映已知有无，缺失字段 exists=FALSE（非 UNKNOWN）。"""
    wd = _setup()
    w = Workdir(wd)
    m005 = w.all_entities()[_eid(wd, "M005")]
    r, _ = eval_condition({"field": "renewal_status", "op": "exists"}, m005)
    assert r == "FALSE", "缺失字段 exists 应=FALSE（已知无值）"
    m001 = w.all_entities()[_eid(wd, "M001")]
    r2, _ = eval_condition({"field": "renewal_status", "op": "exists"}, m001)
    assert r2 == "TRUE"


def test_kleene_not_unknown_is_unknown():
    wd = _setup()
    w = Workdir(wd)
    m005 = w.all_entities()[_eid(wd, "M005")]
    leaf = {"field": "renewal_status", "op": "eq", "value": "未续费"}
    r, _ = eval_condition({"logic": "not", "conditions": [leaf]}, m005)
    assert r == "UNKNOWN", "Kleene: NOT(UNKNOWN)=UNKNOWN"


def test_hard_exclusion_precedence():
    wd = _setup()
    # 即使筛选条件命中，硬排除（拒绝联系）也优先 excluded
    req = {
        "conditions": {"field": "contactable_status", "op": "eq", "value": "拒绝联系"},
        "hard_exclusions": [{"field": "contactable_status", "op": "eq", "value": "拒绝联系"}],
        "rule_is_mock": True,
    }
    env = run_filter(req, wd)
    elig, exc, review = _routes(env)
    m003 = _eid(wd, "M003")
    assert m003 in exc
    assert any(e.get("reason") == "命中硬排除规则" for e in env["excluded"]
               if e["entity_id"] == m003)


def test_invalid_condition_raises():
    wd = _setup()
    known = Workdir(wd).known_field_set()
    for bad in [
        {"field": "region", "op": "bogus_op", "value": "x"},   # 非法 op
        {"op": "eq", "value": "x"},                            # 缺 field
        {"field": "no_such_field", "op": "eq", "value": "x"},  # 未知字段
        {"logic": "xor", "conditions": []},                    # 非法 logic
        {"logic": "and", "conditions": []},                    # 空 conditions
    ]:
        try:
            validate_condition(bad, known)
            raise AssertionError("应拒绝非法条件: %r" % bad)
        except MSError as e:
            assert e.code == EXIT_VALIDATION


def test_priority_disclaimer_present():
    wd = _setup()
    env = run_filter(load_sample("filter_request_demo.json"), wd)
    assert "不代表续费意愿" in env["disclaimer"]


# ---- 复合条件解释（Terra P2 / Astra P2.1）------------------------------------
def _by_name(env, name):
    for e in env["eligible"] + env["excluded"] + env["needs_review"]:
        if e["name"] == name:
            return e
    raise AssertionError("no %s" % name)


def test_composite_reason_not_internal_false_not_mislabeled():
    """企业己因 NOT(行业=贸易) 不成立被排除：理由必须是 not(...)，不是平铺 FALSE 叶子。"""
    wd = _setup_composite()
    env = run_filter(_composite_request(), wd)
    j = _by_name(env, "企业己")
    assert j in env["excluded"]
    assert "not(" in j["reason_detail"], j["reason_detail"]
    assert "贸易" in j["reason_detail"]
    assert "实际：贸易" in j["reason_detail"], "必须给出真实值，不能只说条件不成立"
    fields = [l["field"] for l in j["failed_conditions"]]
    assert fields == ["custom:主营行业"], fields
    assert j["failed_conditions"][0]["result"] == "TRUE", "NOT 内部为 TRUE 才是 NOT 失败的原因"
    # 未被采用的 OR FALSE 分支（如“工业传感器 FALSE”）不得混进排除理由
    assert all("工业传感器" not in f for f in fields)


def test_composite_unused_or_branch_not_exclusion_reason():
    """企业庚城市不满足：OR 中未采用的 FALSE 分支不能被当成排除理由。"""
    wd = _setup_composite()
    env = run_filter(_composite_request(), wd)
    g = _by_name(env, "企业庚")
    assert g in env["excluded"]
    fields = [l["field"] for l in g["failed_conditions"]]
    assert fields == ["custom:所在城市"], fields
    assert "工业传感器" not in g["reason_detail"]
    assert "资质标签" not in g["reason_detail"]
    # 决定路径可定位到条件树位置
    assert g["failed_conditions"][0]["condition_path"] == "conditions.conditions[0]"


def test_composite_or_all_false_lists_all_branches():
    """OR 为 FALSE 时所有分支都决定性，必须全部列出（不漏掉真正原因）。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "or.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["会员编号", "会员名称", "行业", "资质"])
        w.writerow(["M001", "两分支都不满足", "贸易", "普通证书"])
    run_import(path, wd)
    env = run_filter({"conditions": {"logic": "or", "conditions": [
        {"field": "industry", "op": "eq", "value": "工业传感器"},
        {"field": "qualification", "op": "contains", "value": "高新技术企业"}]},
        "rule_is_mock": True}, wd)
    e = env["excluded"][0]
    fields = sorted(l["field"] for l in e["failed_conditions"])
    assert fields == ["industry", "qualification"], fields
    assert " or " in e["reason_detail"] or "；" in e["reason_detail"], e["reason_detail"]
    assert "工业传感器" in e["reason_detail"] and "高新技术企业" in e["reason_detail"]


def test_composite_eligible_reasons_keep_logic_and_actual_values():
    wd = _setup_composite()
    env = run_filter(_composite_request(), wd)
    jia = _by_name(env, "企业甲")
    assert jia in env["eligible"]
    reasons = jia["reasons"]
    assert any(r.startswith("not(") and "贸易" in r for r in reasons), reasons
    assert any("实际：工业传感器" in r for r in reasons), reasons
    assert any("实际：已到期未续费" in r for r in reasons), reasons
    # 解释树保留每节点结果
    tree = jia["condition_tree"]
    assert tree["type"] == "logic" and tree["result"] == "TRUE"
    assert all(c["result"] == "TRUE" for c in tree["children"])


def test_composite_needs_review_only_decisive_unknown():
    """UNKNOWN 只报决定性未知分支：已满足的分支不列为待核实。"""
    wd = _setup_composite()
    env = run_filter(_composite_request(), wd)
    ren = _by_name(env, "企业壬")
    assert ren in env["needs_review"]
    assert ren["unknown_fields"] == ["custom:联系意愿"], ren["unknown_fields"]
    assert "自定义" not in str(ren["unknown_fields"])
    assert ren["reason_detail"].startswith("筛选条件与硬排除均存在未知字段")


def test_composite_split_matches_tree_semantics():
    wd = _setup_composite()
    env = run_filter(_composite_request(), wd)
    c = env["counts"]
    assert (c["eligible"], c["excluded"], c["needs_review"]) == (2, 3, 1), c


def test_hard_exclusion_unknown_is_not_cleared():
    """硬排除字段未知：不得判定“未被排除”，必须 needs_review。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "hard_unknown.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["会员编号", "会员名称", "联系意愿"])
        w.writerow(["M001", "有意愿的公司", "可联系"])
        w.writerow(["M002", "联系意愿空缺", ""])
    run_import(path, wd)
    env = run_filter({"conditions": {"field": "name", "op": "exists"},
                      "hard_exclusions": [{"field": "custom:联系意愿", "op": "eq",
                                           "value": "拒绝联系"}],
                      "rule_is_mock": True}, wd)
    e = [x for x in env["needs_review"] if x["name"] == "联系意愿空缺"][0]
    assert e["unknown_fields"] == ["custom:联系意愿"]
    assert "无法确认是否应排除" in e["reason_detail"]


def test_eval_condition_leaves_carry_actual_and_result():
    wd = _setup()
    w = Workdir(wd)
    m001 = w.all_entities()[_eid(wd, "M001")]
    r, leaves = eval_condition({"field": "region", "op": "eq", "value": "杭州"}, m001)
    assert r == "FALSE"
    assert leaves[0]["actual"] == "深圳"
    assert leaves[0]["result"] == "FALSE"
    r2, leaves2 = eval_condition({"field": "joined_date", "op": "eq", "value": "x"}, m001)
    assert r2 in ("TRUE", "FALSE")
    assert "actual" in leaves2[0]


# ---- 请求校验与同秒结果 -------------------------------------------------------
def test_filter_request_type_errors():
    wd = _setup()
    for bad in [
        "conditions",
        {"conditions": "region"},
        {"conditions": {"field": "region", "op": "eq", "value": "深圳"},
         "hard_exclusions": {"field": "region", "op": "eq", "value": "深圳"}},
        {"conditions": {"field": "region", "op": "eq", "value": "深圳"},
         "rule_is_mock": "false"},
        {"conditions": {"field": "region", "op": "eq", "value": "深圳"},
         "rule_version": 1},
        {"conditions": {"field": "region", "op": "eq", "value": "深圳"}, "sort_by": 3},
        {"conditions": {"field": "region", "op": "in", "value": "深圳"}},
    ]:
        try:
            run_filter(bad, wd)
            raise AssertionError("应拒绝: %r" % (bad,))
        except MSError as e:
            assert e.code == EXIT_VALIDATION, (bad, e.code, e.message)


def test_filter_unused_request_field_reported_not_silent():
    """未知请求字段必须明示（不静默忽略）；随包示例的 scope 属已知说明性字段，不阻断。"""
    wd = _setup()
    env = run_filter(load_sample("filter_request_demo.json"), wd)
    assert env["counts"]["total"] == 10, "scope 这类说明性字段不影响计算"
    assert not any(i["kind"] == "unused_request_field" for i in env["issues"])
    env2 = run_filter({"conditions": {"field": "region", "op": "exists"},
                       "bogus_key": 1, "rule_is_mock": True}, wd)
    issue = [i for i in env2["issues"] if i["kind"] == "unused_request_field"]
    assert issue and issue[0]["fields"] == ["bogus_key"], env2["issues"]


def test_filter_unknown_sort_field_reported():
    wd = _setup()
    env = run_filter({"conditions": {"field": "region", "op": "exists"},
                      "sort_by": "no_such_field", "rule_is_mock": True}, wd)
    assert any(i["kind"] == "unknown_sort_field" for i in env["issues"])


def test_filter_same_second_results_do_not_collide():
    """Astra A2 / filter_result_collision：同秒两次不同筛选不得共用结果 ID 与文件。"""
    wd = _setup()
    a = run_filter({"conditions": {"field": "region", "op": "eq", "value": "深圳"},
                    "rule_is_mock": True}, wd)
    b = run_filter({"conditions": {"field": "region", "op": "eq", "value": "杭州"},
                    "rule_is_mock": True}, wd)
    assert a["result_id"] != b["result_id"], "同秒结果 ID 不得相同"
    assert a["outputs"]["md"] != b["outputs"]["md"]
    assert os.path.exists(os.path.join(wd, a["outputs"]["md"]))
    assert os.path.exists(os.path.join(wd, b["outputs"]["md"]))
    idx = Workdir(wd).index["results"]
    assert a["result_id"] in idx and b["result_id"] in idx
    assert len([k for k in idx if k.startswith("flt-")]) == 2



def test_existing_conflicted_entity_never_eligible_even_when_condition_true():
    wd = fresh_workdir()
    path = os.path.join(wd, "conflict.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["会员编号", "企业名称", "所在地区"], ["C001", "匿名甲", "深圳"]])
    run_import(path, wd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["会员编号", "企业名称", "所在地区"],
                                ["C001", "匿名甲", "深圳"], ["C001", "匿名乙", "杭州"]])
    run_import(path, wd)
    env = run_filter({"conditions": {"field": "business_key", "op": "exists"}}, wd)
    assert env["eligible"] == [] and env["excluded"] == []
    assert len(env["needs_review"]) == 1
    assert "冲突" in env["needs_review"][0]["reason_detail"]


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_filter: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
