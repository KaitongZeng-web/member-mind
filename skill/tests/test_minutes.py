"""纪要测试：幻觉引用被标记、内嵌指令仅标记不执行、引用行号定位、推断永不
变事实、核对幂等、拒绝联系范围保留。"""
from __future__ import annotations

import copy

from _harness import fresh_workdir, run_tests, CSV, MINUTES_RAW, load_sample

from ms_import import run_import
from ms_minutes import run_extract, run_confirm
from ms_store import Workdir
from ms_common import MSError, EXIT_VALIDATION, EXIT_NOT_FOUND


def _setup():
    wd = fresh_workdir()
    run_import(CSV, wd)
    return wd


def _eid(wd, bk):
    for eid, e in Workdir(wd).all_entities().items():
        if e.get("business_key") == bk:
            return eid
    raise AssertionError("no %s" % bk)


def _extract(wd):
    extraction = load_sample("minutes_extraction_sample.json")
    extraction["entity_id"] = _eid(wd, "M001")
    return run_extract(MINUTES_RAW, extraction, wd)


def test_extract_flags_hallucinated_quote():
    wd = _setup()
    env = _extract(wd)
    bad = [i for i in env["issues"] if i.get("id") == "bad1"]
    assert bad and bad[0]["kind"] == "quote_not_located", "幻觉引用必须被标记"


def test_extract_verified_quote_has_line_numbers():
    wd = _setup()
    env = _extract(wd)
    items = {r["id"]: r for r in env["items"]}
    c1 = items["c1"]
    assert c1["citation"]["verified"] is True
    assert c1["citation"]["line_start"] == 9, c1["citation"]


def test_extract_scans_embedded_instruction_not_executed():
    wd = _setup()
    env = _extract(wd)
    inj = [i for i in env["issues"] if i["kind"] == "embedded_instruction"]
    assert inj, "内嵌指令文本应被标记待审"
    # 且绝不据此改变任何会员状态
    w = Workdir(wd)
    m001 = w.all_entities()[_eid(wd, "M001")]
    assert m001["fields"].get("renewal_status") != "已续费", "不得执行材料内指令"


def test_extract_status_draft():
    wd = _setup()
    env = _extract(wd)
    assert env["status"] == "needs_review"      # 处理状态：需人工核对
    assert env["visit_status"] == "draft"        # 拜访记录生命周期：草稿


def test_extract_no_contact_scope_preserved():
    wd = _setup()
    env = _extract(wd)
    nc = env["no_contact"]
    assert nc and nc[0].get("scope") == "电话推销", "拒绝联系范围(scope)必须保留"


def test_confirm_inference_never_becomes_fact():
    wd = _setup()
    ext = _extract(wd)
    vid = ext["visit_id"]
    conf = run_confirm(vid, load_sample("minutes_confirm_sample.json"), wd)
    w = Workdir(wd)
    visit = w.index["visits"][vid]
    inf = [i for i in visit["inferences"] if i["id"] == "i1"][0]
    # sample 对 i1 决策为 reject → 保持 inference
    assert inf["status"] in ("inference", "rejected"), inf["status"]
    assert inf["status"] != "confirmed"


def test_confirm_collects_confirmed_needs_commitments():
    wd = _setup()
    ext = _extract(wd)
    vid = ext["visit_id"]
    conf = run_confirm(vid, load_sample("minutes_confirm_sample.json"), wd)
    assert conf["status"] == "succeeded"
    need_ids = {n["id"] for n in conf["confirmed_needs"]}
    commit_ids = {c["id"] for c in conf["confirmed_commitments"]}
    assert "n1" in need_ids       # need 被 confirm
    assert "m1" in commit_ids     # commitment 被 confirm
    assert "bad1" not in need_ids # 幻觉引用被 reject，不进已确认


def test_confirm_idempotent():
    wd = _setup()
    ext = _extract(wd)
    vid = ext["visit_id"]
    run_confirm(vid, load_sample("minutes_confirm_sample.json"), wd)
    again = run_confirm(vid, load_sample("minutes_confirm_sample.json"), wd)
    assert again.get("note"), "重复确认应幂等返回"


# ---- A1/A2/A3/A6 回归：拒绝与保留链路 ---------------------------------------
def _extraction(eid, items, minutes_date="2026-10-20"):
    return {"entity_id": eid, "minutes_date": minutes_date, "items": items, "identity_evidence": {"key_type": "business_key", "value": "M001", "raw_citation": "会员编号：M001"}}


def test_confirm_phantom_quote_refused():
    """无可定位引用的条目被 confirm 也不能变成事实（A1）。"""
    wd = _setup()
    eid = _eid(wd, "M001")
    ext = run_extract(MINUTES_RAW, _extraction(eid, [
        {"id": "p1", "type": "need", "quote": "这句在原文里绝对不存在", "text": "幻觉需求"},
        {"id": "r1", "type": "commitment",
         "quote": "下周五之前把传感器选型对比方案发给张经理。", "text": "真实承诺"},
    ]), wd)
    vid = ext["visit_id"]
    conf = run_confirm(vid, {"confirmed_by": "核对人", "decisions": [
        {"id": "p1", "decision": "confirm"}, {"id": "r1", "decision": "confirm"}]}, wd)
    w = Workdir(wd)
    visit = w.index["visits"][vid]
    p1 = [i for i in visit["needs"] if i["id"] == "p1"][0]
    r1 = [i for i in visit["commitments"] if i["id"] == "r1"][0]
    assert p1["status"] == "pending", "幻觉引用不能被确认"
    assert r1["status"] == "confirmed"
    assert any(i["kind"] == "confirm_refused_unverified" for i in conf["issues"])
    assert "p1" not in {n["id"] for n in conf["confirmed_needs"]}


def test_confirm_rejects_unknown_duplicate_and_bad_decision():
    wd = _setup()
    eid = _eid(wd, "M001")
    ext = run_extract(MINUTES_RAW, _extraction(eid, [
        {"id": "n1", "type": "need",
         "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "需求"},
    ]), wd)
    vid = ext["visit_id"]
    # 未知条目 ID
    try:
        run_confirm(vid, {"confirmed_by": "x", "decisions": [{"id": "nope", "decision": "confirm"}]}, wd)
        raise AssertionError("未知 ID 应拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION
    # 重复 ID
    try:
        run_confirm(vid, {"confirmed_by": "x", "decisions": [
            {"id": "n1", "decision": "confirm"}, {"id": "n1", "decision": "reject"}]}, wd)
        raise AssertionError("重复 ID 应拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION
    # 未知决定
    try:
        run_confirm(vid, {"confirmed_by": "x", "decisions": [{"id": "n1", "decision": "approve"}]}, wd)
        raise AssertionError("未知决定应拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_partial_confirm_leaves_pending_and_flags_status():
    wd = _setup()
    eid = _eid(wd, "M001")
    ext = run_extract(MINUTES_RAW, _extraction(eid, [
        {"id": "n1", "type": "need",
         "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "需求1"},
        {"id": "n2", "type": "need",
         "quote": "我们产线最近在扩产，传感器用量会翻倍，但现在的供应商交期不稳定。", "text": "需求2"},
    ]), wd)
    vid = ext["visit_id"]
    conf = run_confirm(vid, {"confirmed_by": "x", "decisions": [{"id": "n1", "decision": "confirm"}]}, wd)
    assert conf["visit_status"] == "partially_confirmed", conf["visit_status"]


def test_reextract_same_input_preserves_confirmation():
    """相同输入重放返回同一版本，不丢确认（A2）。"""
    wd = _setup()
    ext = run_extract(MINUTES_RAW, _extraction(_eid(wd, "M001"), [
        {"id": "n1", "type": "need",
         "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "需求"}]), wd)
    vid = ext["visit_id"]
    run_confirm(vid, {"confirmed_by": "李敏", "decisions": [{"id": "n1", "decision": "confirm"}]}, wd)
    again = run_extract(MINUTES_RAW, _extraction(_eid(wd, "M001"), [
        {"id": "n1", "type": "need",
         "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "需求"}]), wd)
    assert again["visit_id"] == vid and again.get("idempotent_replay") is True
    assert Workdir(wd).index["visits"][vid]["status"] == "confirmed", "重放不得清空确认"


def test_reextract_different_content_creates_new_version():
    """内容改变（含空抽取）生成新版本，不覆盖旧确认（A2）。"""
    wd = _setup()
    eid = _eid(wd, "M001")
    full = run_extract(MINUTES_RAW, _extraction(eid, [
        {"id": "n1", "type": "need",
         "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "需求"}]), wd)
    vid = full["visit_id"]
    run_confirm(vid, {"confirmed_by": "李敏", "decisions": [{"id": "n1", "decision": "confirm"}]}, wd)
    empty = run_extract(MINUTES_RAW, _extraction(eid, []), wd)  # 同原文/主体/日期，空抽取
    assert empty["visit_id"] != vid, "内容改变必须生成新 visit_id"
    w = Workdir(wd)
    assert w.index["visits"][vid]["status"] == "confirmed", "旧确认必须保留"
    assert w.index["visits"][empty["visit_id"]]["status"] == "draft"


def test_same_raw_different_subject_distinct_visit():
    """同原文不同主体不得共用 visit_id（A2 同秒碰撞根因）。"""
    wd = _setup()
    items = [{"id": "c1", "type": "customer_statement",
              "quote": "我们产线最近在扩产，传感器用量会翻倍，但现在的供应商交期不稳定。", "text": "x"}]
    a = run_extract(MINUTES_RAW, _extraction(_eid(wd, "M001"), items), wd)
    try:
        run_extract(MINUTES_RAW, _extraction(_eid(wd, "M002"), items), wd)
        raise AssertionError("与原文主体不符必须拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_missing_item_ids_are_unique():
    wd = _setup()
    ext = run_extract(MINUTES_RAW, _extraction(_eid(wd, "M001"), [
        {"type": "need", "quote": "我们产线最近在扩产，传感器用量会翻倍，但现在的供应商交期不稳定。", "text": "a"},
        {"type": "need", "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "b"},
    ]), wd)
    ids = [i["id"] for i in ext["items"]]
    assert len(ids) == len(set(ids)), "省略 ID 的条目必须得到唯一 ID"


def test_extract_duplicate_item_id_rejected():
    wd = _setup()
    try:
        run_extract(MINUTES_RAW, _extraction(_eid(wd, "M001"), [
            {"id": "dup", "type": "need",
             "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "a"},
            {"id": "dup", "type": "need",
             "quote": "我们产线最近在扩产，传感器用量会翻倍，但现在的供应商交期不稳定。", "text": "b"},
        ]), wd)
        raise AssertionError("重复条目 ID 应拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION


def test_invalid_minutes_date_rejected_before_persist():
    wd = _setup()
    n_before = len(Workdir(wd).index["visits"])
    try:
        run_extract(MINUTES_RAW, _extraction(_eid(wd, "M001"), [
            {"id": "n1", "type": "need",
             "quote": "希望对比一下国产替代方案，重点是交期和本地服务响应。", "text": "x"}],
            minutes_date="2026-02-30"), wd)
        raise AssertionError("非法日期应拒绝")
    except MSError as e:
        assert e.code == EXIT_VALIDATION
    assert len(Workdir(wd).index["visits"]) == n_before, "失败不得写入任何 visit"


def test_extract_entity_not_in_archive():
    wd = _setup()
    try:
        run_extract(MINUTES_RAW, _extraction("E-does-not-exist", []), wd)
        raise AssertionError("不存在主体应拒绝")
    except MSError as e:
        assert e.code == EXIT_NOT_FOUND


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_minutes: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
