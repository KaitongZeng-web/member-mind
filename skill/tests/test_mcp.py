"""可选外部检索测试（复现 Terra P1 / Astra A3·A6 的实际歧义）：

- 工具能力状态如实分流：no_tool / unauthorized / expired / error / partial / available；
  expired/error/unauthorized 绝不 planned，planned 绝不等于已执行；
- 身份键命名空间分离：会员编号、USCC、服务商 ID 不跨类型碰撞；
  `M001` 不是合法 USCC，不得命中本地会员编号；
- 同名多主体：matched_entity_id 置空并返回候选列表，不取第一个；
- mock/partial 从子候选与证据向上保留，不被顶层 false 清洗；来源未声明不冒充真实；
- 存在未映射列时不得宣称与外部候选无冲突；
- 同秒多次 ingest 结果 ID 不碰撞；
- 未知字段/错误类型结构化失败（MSError），不静默忽略。
"""
from __future__ import annotations

import os
import tempfile

from _harness import fresh_workdir, run_tests, CSV, load_sample

from ms_import import run_import
from ms_mcp import run_mcp_search, run_mcp_ingest
from ms_store import Workdir
from ms_common import MSError, EXIT_VALIDATION

USCC_LOCAL = "91440300MA5ABCD12X"     # 18 位，合法格式
USCC_LOCAL_ALT = "91440300MA5ABCD99X"


def _setup():
    wd = fresh_workdir()
    run_import(CSV, wd)
    return wd


def _eid(wd, bk):
    for eid, e in Workdir(wd).all_entities().items():
        if e.get("business_key") == bk:
            return eid
    raise AssertionError("no %s" % bk)


def _write_csv(path, header, rows):
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


def _setup_with_uscc():
    """带 USCC 列与未映射自定义列的本地档案。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "with_uscc.csv")
    _write_csv(path,
               ["会员编号", "企业名称", "统一社会信用代码", "所在地区", "所属行业", "备注列"],
               [["M001", "本地甲公司", USCC_LOCAL, "深圳", "工业传感器", "x"],
                ["M002", "本地乙公司", USCC_LOCAL_ALT, "上海", "自动化设备", "y"]])
    run_import(path, wd)
    return wd


def _setup_same_name():
    """两个同名但不同编号的主体（Terra 29 的歧义场景）。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "same_name.csv")
    _write_csv(path, ["会员编号", "会员名称", "所在城市"],
               [["M009", "企业同名（虚构）", "深圳"],
                ["M010", "企业同名（虚构）", "东莞"]])
    run_import(path, wd)
    return wd


def _raises_validation(fn):
    try:
        fn()
    except MSError as e:
        assert e.code == EXIT_VALIDATION, "退出码应为 5，实际 %s: %s" % (e.code, e.message)
        return e
    raise AssertionError("应抛 MSError(EXIT_VALIDATION)")


# ---- mcp-search：能力状态如实分流 --------------------------------------------
def test_mcp_search_no_tool():
    wd = _setup()
    env = run_mcp_search({"region": "深圳"}, [], wd)
    assert env["external_status"] == "no_tool"
    assert env["planned"] is False
    assert env["executed"] is False and env["data_returned"] is False
    assert env["candidates"] == []
    assert env["status"] == "needs_review", "未执行检索不得记为 succeeded"
    assert "未进行任何外部检索" in env["note"]


def test_mcp_search_unauthorized():
    wd = _setup()
    env = run_mcp_search({}, [{"name": "qcc", "authorized": False, "status": "ok"}], wd)
    assert env["external_status"] == "unauthorized"
    assert env["planned"] is False
    assert env["usable_tools"] == []
    assert env["unusable_tools"][0]["name"] == "qcc"
    assert "授权" in env["unusable_tools"][0]["reason"]


def test_mcp_search_expired_authorized_is_not_planned():
    """Astra expired_mcp：已授权但过期的工具不得 planned、不得 succeeded。"""
    wd = _setup()
    env = run_mcp_search({"region": "深圳"},
                         [{"name": "example", "authorized": True, "status": "expired"}], wd)
    assert env["external_status"] == "expired"
    assert env["planned"] is False, "过期工具绝不能 planned"
    assert env["status"] == "needs_review", "过期工具不得记为 succeeded"
    assert env["executed"] is False and env["data_returned"] is False
    assert env["tools_seen"][0]["usable"] is False
    assert "过期" in env["tools_seen"][0]["unusable_reason"]
    assert any("重新授权" in a for a in env["next_actions"])


def test_mcp_search_error_status():
    wd = _setup()
    env = run_mcp_search({}, [{"name": "qcc", "authorized": True, "status": "error"}], wd)
    assert env["external_status"] == "error"
    assert env["planned"] is False
    assert env["status"] == "needs_review"


def test_mcp_search_partial_when_some_usable():
    wd = _setup()
    env = run_mcp_search({}, [
        {"name": "qcc", "authorized": True, "status": "ok", "supports_combined_search": True},
        {"name": "ima", "authorized": True, "status": "expired"},
    ], wd)
    assert env["external_status"] == "partial"
    assert env["planned"] is True
    assert env["usable_tools"] == ["qcc"]
    assert [t["name"] for t in env["unusable_tools"]] == ["ima"]
    assert env["status"] == "needs_review", "只整理意图，不等于已检索成功"


def test_mcp_search_available_but_not_executed():
    wd = _setup()
    env = run_mcp_search({"region": "深圳"},
                         [{"name": "qcc", "authorized": True, "status": "ok"}], wd)
    assert env["external_status"] == "available"
    assert env["planned"] is True
    assert env["executed"] is False and env["data_returned"] is False
    assert env["status"] == "needs_review"
    assert "不代发调用" in env["note"]
    assert "不是“已检索成功”" in env["note"]


def test_mcp_search_read_only_tool_is_usable_for_search():
    """只读工具可用于检索（不写本地），但同样只是 planned，不等于已执行。"""
    wd = _setup()
    env = run_mcp_search({"region": "深圳"},
                         [{"name": "qcc", "authorized": True, "status": "read_only"}], wd)
    assert env["external_status"] == "available"
    assert env["usable_tools"] == ["qcc"]
    assert env["executed"] is False


def test_mcp_search_unknown_tool_status_not_usable():
    wd = _setup()
    env = run_mcp_search({}, [{"name": "qcc", "authorized": True, "status": "weird"}], wd)
    assert env["external_status"] == "error", "未知状态不得按可用处理"
    assert env["planned"] is False
    assert "未知" in env["tools_seen"][0]["unusable_reason"]


def test_mcp_search_rejects_bad_types():
    wd = _setup()
    _raises_validation(lambda: run_mcp_search("深圳", [], wd))
    _raises_validation(lambda: run_mcp_search({}, {"name": "qcc"}, wd))
    _raises_validation(lambda: run_mcp_search({}, ["qcc"], wd))


# ---- mcp-ingest：身份键命名空间 ----------------------------------------------
def test_ingest_uscc_matches_local_uscc_only():
    wd = _setup_with_uscc()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "外部甲",
                                          "uscc": USCC_LOCAL, "region": "上海"}]}, wd)
    c = env["candidates"][0]
    assert c["relation_to_local"] == "matches_local_by_id"
    assert c["matched_key_type"] == "uscc"
    assert c["verification_state"] == "id_match_pending_human_confirm"
    conflict = {x["field"]: x for x in (c.get("conflict") or [])}
    assert "region" in conflict and conflict["region"]["local"] == "深圳"
    assert c["conflict_check"] == "compared"


def test_ingest_business_key_matches_business_key():
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "外部同名",
                                          "business_key": "M001", "region": "深圳",
                                          "industry": "工业传感器"}]}, wd)
    c = env["candidates"][0]
    assert c["relation_to_local"] == "matches_local_by_id"
    assert c["matched_key_type"] == "business_key"
    assert c["matched_entity_id"] == _eid(wd, "M001")
    assert c["conflict"] is None
    assert c["conflict_check"] == "compared_no_conflict"


def test_ingest_uscc_must_not_match_member_number():
    """Terra 29 / Astra A3：USCC 命名空间与会员编号命名空间禁止跨类型碰撞。"""
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "外部公司",
                                          "uscc": "M001", "region": "上海"}]}, wd)
    c = env["candidates"][0]
    assert c["matched_entity_id"] is None, "M001 不是 USCC，不得命中本地会员编号"
    assert c["relation_to_local"] == "external_new"
    assert c["uscc_format_valid"] is False
    assert any(i["kind"] == "invalid_uscc_format" for i in env["issues"])
    assert c["conflict"] is None, "未按唯一标识命中就不得比较/宣称冲突"


def test_ingest_provider_id_namespace_separate():
    wd = fresh_workdir()
    path = os.path.join(wd, "pid.csv")
    _write_csv(path, ["会员编号", "企业名称", "服务商ID", "所在地区"],
               [["M001", "本地甲公司", "QCC-1", "深圳"]])
    # 服务商 ID 是独立键类型：表头需显式映射到 provider_id（不与会员编号混为一谈）
    run_import(path, wd, mapping={"服务商ID": "provider_id"})
    hit = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "外部甲",
                                          "provider_id": "QCC-1"}]}, wd)
    assert hit["candidates"][0]["matched_key_type"] == "provider_id"
    # 会员编号值塞进 provider_id 不得命中本地会员编号
    miss = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                           "candidates": [{"candidate_id": "c2", "name": "外部乙",
                                           "provider_id": "M001"}]}, wd)
    assert miss["candidates"][0]["matched_entity_id"] is None


def test_ingest_ambiguous_name_returns_candidates_not_first():
    """Terra 29：同名多主体不得取第一个 matched_entity_id。"""
    wd = _setup_same_name()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1",
                                          "name": "企业同名（虚构）"}]}, wd)
    c = env["candidates"][0]
    assert c["matched_entity_id"] is None, "同名多主体必须置空，不取第一个"
    assert c["relation_to_local"] == "possible_local_match_by_name"
    assert c["verification_state"] == "name_match_not_merged"
    assert len(c["name_match_candidates"]) == 2
    amb = [i for i in env["issues"] if i["kind"] == "ambiguous_name"]
    assert amb and len(amb[0]["entity_ids"]) == 2
    assert c["member_status"] is None and c["renewal_status"] is None


def test_ingest_exact_name_is_candidate_not_confirmation():
    """唯一同名只是候选：不给会员/续费/可联系属性，也不宣称已确认主体。"""
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1",
                                          "name": "深圳精密传感科技有限公司"}]}, wd)
    c = env["candidates"][0]
    assert c["verification_state"] == "name_match_not_merged"
    assert c["conflict_check"] == "name_match_not_compared"
    assert c["member_status"] is None and c["renewal_status"] is None
    assert c["contactable_status"] is None
    assert "不自动视为会员" in c["membership_note"]


def test_ingest_external_new_has_no_member_attrs():
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "全新外部公司"}]}, wd)
    c = env["candidates"][0]
    assert c["relation_to_local"] == "external_new"
    assert c["member_status"] is None and c["renewal_status"] is None
    assert c["contactable_status"] is None


def test_ingest_never_auto_member_attrs():
    wd = _setup()
    env = run_mcp_ingest(load_sample("mcp_candidates_mock.json"), wd)
    for c in env["candidates"]:
        assert c["member_status"] is None
        assert c["renewal_status"] is None
        assert c["contactable_status"] is None


# ---- 冲突与未映射列 ----------------------------------------------------------
def test_ingest_pending_mapping_not_claimed_conflict_free():
    """Terra 29：未做字段映射时，本地 custom 地区与外部冲突被漏报——必须报待映射。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "terra_like.csv")
    _write_csv(path, ["会员编号", "会员名称", "所在城市", "主营行业"],
               [["M001", "企业甲（虚构）", "深圳", "工业传感器"]])
    run_import(path, wd)
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "企业甲（虚构）",
                                          "business_key": "M001", "region": "杭州",
                                          "industry": "工业传感器"}]}, wd)
    c = env["candidates"][0]
    assert c["matched_key_type"] == "business_key"
    assert c["conflict_check"] == "incomplete_pending_mapping", \
        "本地 region 落在未映射列里，不得宣称无冲突"
    pm = [i for i in env["issues"] if i["kind"] == "pending_field_mapping"]
    assert pm, "存在未映射列必须提示待字段映射"
    assert any("custom:所在城市" in col for col in pm[0]["unmapped_local_columns"])
    assert c["conflict"] is None, "未映射字段不得当成已比较无冲突"


def test_ingest_pending_mapping_reported_for_name_match_too():
    """Terra 29：名字匹配且本地值在未映射列里时，必须报“无法比较”，不得留空装作无差异。"""
    wd = fresh_workdir()
    path = os.path.join(wd, "terra_like.csv")
    _write_csv(path, ["会员编号", "会员名称", "所在城市", "主营行业"],
               [["M001", "企业甲（虚构）", "深圳", "工业传感器"]])
    run_import(path, wd)
    env = run_mcp_ingest({"is_mock": True, "source_tool": "mock-test",
                          "candidates": [{"candidate_id": "c2", "name": "企业甲（虚构）",
                                          "region": "杭州", "industry": "工业传感器"}]}, wd)
    c = env["candidates"][0]
    assert c["conflict_check"] == "name_match_not_compared"
    assert c["mapping_pending"] is True
    assert "custom:所在城市" in c["unmapped_local_columns"]
    pm = [i for i in env["issues"] if i["kind"] == "pending_field_mapping"]
    assert pm and "无法比较" in pm[0]["reason"]
    assert env["pending_mapping_entities"], "应汇总哪些本地主体因未映射而无法比较"


def test_ingest_id_match_conflict_preserved_not_overwritten():
    wd = _setup()
    m001 = Workdir(wd).all_entities()[_eid(wd, "M001")]
    assert m001["fields"]["industry"] == "工业传感器"
    payload = {
        "is_mock": False,
        "source_tool": "qcc-company",
        "candidates": [{
            "candidate_id": "x", "name": "外部同名公司",
            "business_key": "M001",      # 会员编号命名空间，显式键类型
            "region": "上海", "industry": "工业传感器",
        }],
    }
    env = run_mcp_ingest(payload, wd)
    c = env["candidates"][0]
    assert c["relation_to_local"] == "matches_local_by_id"
    assert c["verification_state"] == "id_match_pending_human_confirm"
    conflict = {x["field"]: x for x in (c.get("conflict") or [])}
    assert "region" in conflict
    assert conflict["region"]["local"] == "深圳"
    assert "不覆盖" in conflict["region"]["resolution"]
    m001b = Workdir(wd).all_entities()[_eid(wd, "M001")]
    assert m001b["fields"]["region"] == "深圳", "外部候选不得覆盖本地档案"
    assert m001b["fields"]["industry"] == "工业传感器"


# ---- mock / partial / 来源 ---------------------------------------------------
def test_ingest_mock_payload_marked():
    wd = _setup()
    env = run_mcp_ingest(load_sample("mcp_candidates_mock.json"), wd)
    assert env["mode"] == "mock"
    assert env["is_mock"] is True
    assert env["partial"] is True
    assert all(c["is_mock"] for c in env["candidates"])
    assert any(i["kind"] == "mock_payload" for i in env["issues"])
    assert any(i["kind"] == "partial_result" for i in env["issues"])
    assert "未进行真实企查查查询" in env["disclaimer"]


def test_ingest_nested_mock_not_washed_out_by_top_false():
    """Astra mock_propagation：子候选/证据 is_mock=true 不得被顶层 false 清洗。"""
    wd = _setup()
    payload = {
        "is_mock": False, "source_tool": "qcc-company",
        "candidates": [{"candidate_id": "c1", "name": "匿名测试",
                        "evidence": {"is_mock": True, "source": "mock回包"}}],
    }
    env = run_mcp_ingest(payload, wd)
    c = env["candidates"][0]
    assert c["is_mock"] is True, "证据标记为 mock，候选不得变成真实"
    assert env["is_mock"] is True, "顶层不得用 false 清洗子层 mock"
    assert env["mode"] == "mock"
    assert any(i["kind"] == "mock_payload" for i in env["issues"])


def test_ingest_unknown_provenance_is_not_real():
    """来源未声明/未知：不得冒充真实外部结果。"""
    wd = _setup()
    env = run_mcp_ingest({"candidates": [{"candidate_id": "c1", "name": "匿名测试"}]}, wd)
    assert env["is_mock"] is None
    assert env["mode"] == "unknown"
    assert env["provenance"] == "unknown"
    assert any(i["kind"] == "provenance_unknown" for i in env["issues"])


def test_ingest_declared_real_with_known_source_is_live():
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "匿名测试"}]}, wd)
    assert env["is_mock"] is False
    assert env["mode"] == "live"
    assert env["provenance"] == "declared_external"


def test_ingest_partial_flag_kept_per_candidate():
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company", "partial": True,
                          "candidates": [{"candidate_id": "c1", "name": "甲"}]}, wd)
    assert env["partial"] is True
    assert env["candidates"][0]["partial"] is True
    assert any(i["kind"] == "partial_result" for i in env["issues"])


# ---- 结构校验与 ID ----------------------------------------------------------
def test_ingest_rejects_bad_types():
    wd = _setup()
    _raises_validation(lambda: run_mcp_ingest("payload", wd))
    _raises_validation(lambda: run_mcp_ingest({"candidates": "x"}, wd))
    _raises_validation(lambda: run_mcp_ingest({"candidates": ["x"]}, wd))
    _raises_validation(lambda: run_mcp_ingest(
        {"candidates": [{"candidate_id": 1, "name": "x"}]}, wd))
    _raises_validation(lambda: run_mcp_ingest(
        {"is_mock": "false", "candidates": []}, wd))
    _raises_validation(lambda: run_mcp_ingest(
        {"is_mock": False, "candidates": [{"candidate_id": "c1", "name": "x",
                                           "is_mock": "true"}]}, wd))
    _raises_validation(lambda: run_mcp_ingest(
        {"source_tool": 5, "candidates": []}, wd))


def test_ingest_missing_name_skipped_with_issue():
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "region": "深圳"},
                                         {"candidate_id": "c2", "name": "有名字"}]}, wd)
    assert len(env["candidates"]) == 1
    assert env["candidates"][0]["candidate_id"] == "c2"
    assert any(i["kind"] == "missing_name" for i in env["issues"])


def test_ingest_duplicate_candidate_id_reported():
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "甲"},
                                         {"candidate_id": "c1", "name": "乙"}]}, wd)
    assert len(env["candidates"]) == 2, "重复 ID 不合并，分别记录"
    assert any(i["kind"] == "duplicate_candidate_id" for i in env["issues"])


def test_ingest_same_second_ids_differ():
    """Astra A2：同秒多次 ingest 不得共用 result_id（也不得覆盖任何已登记结果）。"""
    wd = _setup()
    payload = {"is_mock": False, "source_tool": "qcc-company",
               "candidates": [{"candidate_id": "c1", "name": "甲"}]}
    a = run_mcp_ingest(payload, wd)
    b = run_mcp_ingest(payload, wd)
    assert a["result_id"] != b["result_id"], "同秒结果 ID 不得碰撞"
    # 已存在的 filter/brief 结果 ID 不得被 mcp 结果占用
    from ms_filter import run_filter
    flt = run_filter({"conditions": {"field": "region", "op": "eq", "value": "深圳"},
                      "rule_is_mock": True}, wd)
    again = run_mcp_ingest(payload, wd)
    assert again["result_id"] not in (a["result_id"], b["result_id"], flt["result_id"])


def test_ingest_status_always_needs_review():
    """候选≠已核实主体：无论回包多干净，都不能自己变成 succeeded。"""
    wd = _setup()
    env = run_mcp_ingest({"is_mock": False, "source_tool": "qcc-company",
                          "candidates": [{"candidate_id": "c1", "name": "甲"}]}, wd)
    assert env["status"] == "needs_review"
    assert env["ok"] is True



def test_ingest_18_digit_member_number_does_not_enter_uscc_namespace():
    wd = fresh_workdir()
    value = "123456789012345678"
    path = os.path.join(wd, "numeric_member.csv")
    _write_csv(path, ["会员编号", "企业名称"], [[value, "本地匿名甲"]])
    run_import(path, wd)
    env = run_mcp_ingest({"is_mock": True, "source_tool": "mock-test",
                          "candidates": [{"candidate_id": "c1", "name": "外部匿名乙", "uscc": value}]}, wd)
    assert env["candidates"][0]["matched_entity_id"] is None
    assert env["candidates"][0]["relation_to_local"] == "external_new"
    # 同一个字面值明确作为业务编号时仍能正确命中，证明不是全面禁用匹配。
    env = run_mcp_ingest({"is_mock": True, "source_tool": "mock-test",
                          "candidates": [{"candidate_id": "c2", "name": "外部匿名乙", "business_key": value}]}, wd)
    assert env["candidates"][0]["matched_entity_id"] == _eid(wd, value)
    assert env["candidates"][0]["matched_key_type"] == "business_key"


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_mcp: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
