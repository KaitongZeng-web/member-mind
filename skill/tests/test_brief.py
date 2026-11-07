"""拜访简报测试：事实带字段级引用、未知项含不在模型内的敏感项、缺实体即错。

另复现 Terra P2 / Astra P2.2 的映射缺口缺陷：存在未映射列时只能说“待字段映射”，
不得声称“源数据未提供该字段”；事实/推断/未知分开；同秒生成不碰撞 brief_id；
context 类型与材料来源缺失结构化报错。"""
from __future__ import annotations

import csv
import os

from _harness import fresh_workdir, run_tests, CSV

from ms_import import run_import
from ms_brief import run_brief, _NOT_IN_MODEL
from ms_store import Workdir
from ms_common import MSError, EXIT_NOT_FOUND, EXIT_VALIDATION


def _setup():
    wd = fresh_workdir()
    run_import(CSV, wd)
    return wd


def _eid(wd, bk):
    for eid, e in Workdir(wd).all_entities().items():
        if e.get("business_key") == bk:
            return eid
    raise AssertionError("no %s" % bk)


def _setup_unmapped():
    """Terra 型表头：多数列未映射，只识别编号与名称。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "unmapped.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["会员编号", "会员名称", "所在城市", "主营行业", "续费情况", "联系意愿"])
        w.writerow(["M001", "企业甲（虚构）", "深圳", "工业传感器", "已到期未续费", "可联系"])
    run_import(path, wd)
    return wd


def _setup_same_name():
    wd = fresh_workdir()
    path = os.path.join(wd, "same_name.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["会员编号", "会员名称", "所在地区"])
        w.writerow(["M009", "企业同名（虚构）", "深圳"])
        w.writerow(["M010", "企业同名（虚构）", "东莞"])
    run_import(path, wd)
    return wd


def test_brief_facts_have_citations():
    wd = _setup()
    eid = _eid(wd, "M001")
    env = run_brief(eid, wd, purpose="评估替代")
    facts = env["items"]
    assert any(f["field"] == "企业名称" for f in facts)
    for f in facts:
        assert "source" in f and f["source"], "事实必须带来源引用"
        assert "data_version" in f["source"], "引用应含数据版本"


def test_brief_unknowns_include_not_in_model_and_missing():
    wd = _setup()
    eid = _eid(wd, "M005")   # 续费状态未知
    env = run_brief(eid, wd)
    unknown_fields = {u["field"] for u in env["unknowns"]}
    for item in _NOT_IN_MODEL:
        assert item in unknown_fields, "不在模型内的 %s 必须列为未知，绝不编造" % item
    assert "续费状态" in unknown_fields, "缺失字段应列为未知"


def test_brief_never_fabricates_sensitive():
    wd = _setup()
    eid = _eid(wd, "M001")
    env = run_brief(eid, wd)
    for u in env["unknowns"]:
        if u["field"] in _NOT_IN_MODEL:
            assert "不编造" in u["reason"] or "无外部依据" in u["reason"]


def test_brief_entity_not_found():
    wd = _setup()
    try:
        run_brief("E-doesnotexist", wd)
        raise AssertionError("应抛 NOT_FOUND")
    except MSError as e:
        assert e.code == EXIT_NOT_FOUND


# ---- 映射缺口（Terra P2 / Astra P2.2）---------------------------------------
def test_brief_unmapped_columns_are_pending_mapping_not_missing():
    """存在未映射列时：不得声称“源数据未提供该字段”，必须提示待字段映射。"""
    wd = _setup_unmapped()
    eid = _eid(wd, "M001")
    env = run_brief(eid, wd)
    assert env["status"] == "needs_review"
    assert env["unmapped_columns"], "应列出未映射列"
    assert "custom:所在城市" in env["unmapped_columns"]
    pending = [u for u in env["unknowns"] if u.get("mapping_pending")]
    assert pending, "未映射涉及字段必须标为待字段映射"
    for u in pending:
        assert "待字段映射" in u["reason"]
        # 旧缺陷正是这句无前提的断言；新文案只能说“不能断言源数据未提供”
        assert "源数据未提供该字段" not in u["reason"], u["reason"]
        assert "不能断言源数据未提供" in u["reason"], u["reason"]
        assert u["unmapped_columns"] == env["unmapped_columns"]
    assert "所在地区" in env["pending_mapping_fields"]
    assert "所属行业" in env["pending_mapping_fields"]
    assert any(i["kind"] == "pending_field_mapping" for i in env["issues"])
    assert any("未映射" in a for a in env["next_actions"])
    assert any(w["kind"] == "pending_field_mapping" for w in env["warnings"])


def test_brief_fully_mapped_missing_field_says_source_data():
    """字段都已映射、确实没有该列时，才允许说源数据未提供。"""
    wd = _setup()
    eid = _eid(wd, "M005")
    env = run_brief(eid, wd)
    assert env["status"] == "succeeded"
    assert env["unmapped_columns"] == []
    reasons = {u["field"]: u["reason"] for u in env["unknowns"]}
    assert "源数据未提供该字段" in reasons["最近活动"]
    assert "源单元格为空" in reasons["续费状态"], "导入已记录的未知原因必须保留"
    assert all(u.get("mapping_pending") is False for u in env["unknowns"])


def test_brief_facts_inferences_unknowns_separated():
    wd = _setup()
    eid = _eid(wd, "M001")
    env = run_brief(eid, wd, context={"inferences": ["可能会续费"],
                                      "suggestions": ["准备续费方案"]})
    assert [f["field"] for f in env["items"] if f["field"] == "企业名称"], "事实只来自档案/材料"
    assert all(f["source"] for f in env["items"])
    inf = env["inferences"]
    assert inf and inf[0]["kind"] == "inference"
    assert "永不升级为事实" in inf[0]["note"]
    sug = env["suggestions"]
    assert sug and sug[0]["kind"] == "ai_suggestion"
    assert "非事实" in sug[0]["note"]
    # 推断不是事实：不得出现在 facts / evidence_refs 里
    assert all("可能会续费" not in str(f) for f in env["items"])
    assert all("可能会续费" not in str(r) for r in env["evidence_refs"])


def test_brief_material_without_source_rejected():
    wd = _setup()
    eid = _eid(wd, "M001")
    for bad in [
        {"materials": [{"field": "资质", "value": "高新技术企业"}]},          # 缺 source
        {"materials": [{"field": "", "value": "x", "source": "s"}]},        # 缺 field
        {"materials": [{"field": "资质", "source": "s"}]},                   # 缺 value
        {"materials": "x"},                                                  # 类型错
        {"suggestions": "x"},                                                # 类型错
        {"suggestions": [1]},                                                # 元素类型错
    ]:
        try:
            run_brief(eid, wd, context=bad)
            raise AssertionError("应拒绝: %r" % (bad,))
        except MSError as e:
            assert e.code == EXIT_VALIDATION, (bad, e.code, e.message)


def test_brief_context_type_and_purpose_validation():
    wd = _setup()
    eid = _eid(wd, "M001")
    try:
        run_brief(eid, wd, context="x")
        raise AssertionError("应拒绝非对象 context")
    except MSError as e:
        assert e.code == EXIT_VALIDATION
    try:
        run_brief(eid, wd, purpose=5)
        raise AssertionError("应拒绝非字符串 purpose")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_brief_unused_context_field_reported():
    wd = _setup()
    eid = _eid(wd, "M001")
    env = run_brief(eid, wd, context={"bogus": 1})
    assert any(i["kind"] == "unused_context_field" and i["fields"] == ["bogus"]
               for i in env["issues"])


def test_brief_name_ambiguity_warns_without_merging():
    wd = _setup_same_name()
    eids = [eid for eid, e in Workdir(wd).all_entities().items()
            if e["fields"].get("name") == "企业同名（虚构）"]
    env = run_brief(eids[0], wd)
    warn = [w for w in env["warnings"] if w["kind"] == "name_ambiguity"]
    assert warn, "同名主体必须提示歧义"
    assert set(warn[0]["entity_ids"]) == set(eids) - {eids[0]}
    assert "不要" in warn[0]["reason"] and "合并" in warn[0]["reason"]
    # 不合并：仍是两个主体
    assert len([e for e in Workdir(wd).all_entities().values()
                if e["fields"].get("name") == "企业同名（虚构）"]) == 2


def test_brief_same_second_ids_differ():
    """Astra A2：同秒为不同主体生成简报不得共用 brief_id/文件。"""
    wd = _setup()
    a = run_brief(_eid(wd, "M001"), wd)
    b = run_brief(_eid(wd, "M002"), wd)
    assert a["brief_id"] != b["brief_id"]
    assert a["outputs"]["md"] != b["outputs"]["md"]
    assert os.path.exists(os.path.join(wd, a["outputs"]["md"]))
    assert os.path.exists(os.path.join(wd, b["outputs"]["md"]))
    idx = Workdir(wd).index["results"]
    assert a["brief_id"] in idx and b["brief_id"] in idx


def test_brief_not_in_model_never_fabricated_with_unmapped_columns():
    wd = _setup_unmapped()
    env = run_brief(_eid(wd, "M001"), wd)
    for item in _NOT_IN_MODEL:
        u = [x for x in env["unknowns"] if x["field"] == item]
        assert u and "不编造" in u[0]["reason"]
        assert u[0].get("mapping_pending") is False, "模型外项目与字段映射无关"



def test_existing_conflicted_profile_is_history_not_current_facts():
    wd = fresh_workdir()
    path = os.path.join(wd, "conflict.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["会员编号", "企业名称", "所在地区"], ["C001", "匿名甲", "深圳"]])
    run_import(path, wd)
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["会员编号", "企业名称", "所在地区"],
                                ["C001", "匿名甲", "深圳"], ["C001", "匿名乙", "杭州"]])
    run_import(path, wd)
    env = run_brief(_eid(wd, "C001"), wd)
    assert env["status"] == "needs_review" and env["items"] == []
    assert env["historical_facts"] and len(env["conflict_variants"]) == 2
    assert any(i["kind"] == "identity_conflict" for i in env["issues"])
    assert Workdir(wd).index["results"][env["brief_id"]]["status"] == "needs_review"
    with open(os.path.join(wd, env["outputs"]["md"]), encoding="utf-8") as f:
        assert "不能作为当前拜访事实" in f.read()


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_brief: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
