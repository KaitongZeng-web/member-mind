"""导入测试：计数、字段映射、未知/异常、重名不合并、幂等、自定义字段、跨格式。"""
from __future__ import annotations

import csv
import os

from _harness import fresh_workdir, run_tests, CSV, XLSX
import make_sample_xlsx as mkx

from ms_import import run_import
from ms_store import Workdir


def _write_csv(wd, rows):
    import csv as _csv
    p = os.path.join(wd, "case.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        _csv.writer(f, lineterminator="\n").writerows(rows)
    return p


def _by_bk(wd, bk):
    for e in wd.all_entities().values():
        if e.get("business_key") == bk:
            return e
    raise AssertionError("未找到 business_key=%s" % bk)


def test_import_counts_and_data_version():
    wd = fresh_workdir()
    env = run_import(CSV, wd)
    c = env["counts"]
    assert c == {"added": 8, "updated": 0, "unchanged": 0,
                 "needs_review": 2, "rejected": 0}, c
    # 8 added + 2 重名建档 = 10 次版本 bump
    assert env["data_version"] == 10, env["data_version"]
    assert env["total_data_rows"] == 10
    assert sum(c.values()) == 10


def test_field_mapping_suggested():
    wd = fresh_workdir()
    env = run_import(CSV, wd)
    m = env["suggested_mapping"]
    assert m["会员编号"] == "business_key"
    assert m["企业名称"] == "name"
    assert m["所属行业"] == "industry"
    assert m["可联系"] == "contactable_status"
    assert m["续费状态"] == "renewal_status"


def test_blank_becomes_unknown_not_false():
    wd = fresh_workdir()
    run_import(CSV, wd)
    w = Workdir(wd)
    m005 = _by_bk(w, "M005")           # 续费状态空
    assert "renewal_status" not in m005["fields"]
    assert "renewal_status" in m005["unknowns"]
    m010 = _by_bk(w, "M010")           # 可联系空
    assert "contactable_status" not in m010["fields"]
    assert "contactable_status" in m010["unknowns"]


def test_invalid_calendar_date_dropped_and_flagged():
    wd = fresh_workdir()
    env = run_import(CSV, wd)
    w = Workdir(wd)
    m009 = _by_bk(w, "M009")           # 加入日期 2021-13-40 非法
    assert "joined_date" not in m009["fields"]
    assert "joined_date" in m009["unknowns"]
    date_issues = [i for i in env["issues"] if i.get("field") == "joined_date"]
    assert date_issues, "非法日期应记录 issue"


def test_duplicate_no_id_not_merged():
    wd = fresh_workdir()
    env = run_import(CSV, wd)
    w = Workdir(wd)
    dupes = [e for e in w.all_entities().values()
             if e.get("status") == "suspected_duplicate"]
    assert len(dupes) == 2, "两条同名无编号行应各自建档为疑似重名"
    assert len({d["entity_id"] for d in dupes}) == 2, "重名不得合并为一个主体"
    assert env["counts"]["needs_review"] == 2


def test_idempotent_reimport_no_version_bump():
    wd = fresh_workdir()
    a = run_import(CSV, wd)
    dv1 = a["data_version"]
    b = run_import(CSV, wd)
    # 8 个编号行内容不变→unchanged；2 条疑似重名行每次导入都需人工核对→needs_review。
    assert b["counts"] == {"added": 0, "updated": 0, "unchanged": 8,
                           "needs_review": 2, "rejected": 0}, b["counts"]
    assert b["data_version"] == dv1, "同内容重复导入不应涨版本（幂等核心保证）"


def test_custom_fields_preserved():
    wd = fresh_workdir()
    # 追加一个未知列“备注”
    rows = list(csv.reader(open(CSV, encoding="utf-8")))
    rows[0].append("备注")
    for r in rows[1:]:
        r.append("补充说明X")
    p = os.path.join(wd, "with_custom.csv")
    csv.writer(open(p, "w", encoding="utf-8"), lineterminator="\n").writerows(rows)
    env = run_import(p, wd)
    w = Workdir(wd)
    m001 = _by_bk(w, "M001")
    assert m001["fields"].get("custom:备注") == "补充说明X", "自定义字段必须保留"


def test_header_conflict_marked_unknown():
    wd = fresh_workdir()
    # 两个不同列都命中 name
    rows = list(csv.reader(open(CSV, encoding="utf-8")))
    rows[0].append("公司名称")
    for r in rows[1:]:
        r.append("重复名")
    p = os.path.join(wd, "conflict.csv")
    csv.writer(open(p, "w", encoding="utf-8"), lineterminator="\n").writerows(rows)
    env = run_import(p, wd)
    assert "name" in env["field_conflicts"], env["field_conflicts"]
    assert any(i["kind"] == "field_conflict" for i in env["issues"])


def test_xlsx_import_and_formula_cached_value():
    wd = fresh_workdir()
    env = run_import(XLSX, wd)
    assert env["counts"]["added"] == 6, env["counts"]
    assert any("公式" in b for b in env["format_boundaries"])
    w = Workdir(wd)
    m001 = _by_bk(w, "M001")
    # 公式列读的是缓存值，不是重算
    assert m001["fields"].get("custom:关注度(公式)") == "12"


def test_cross_format_idempotent_no_bump():
    """同内容换格式（CSV→XLSX）不涨版本：生成与 CSV 业务编号行一致的 xlsx。"""
    wd = fresh_workdir()
    run_import(CSV, wd)
    dv1 = Workdir(wd).data_version
    # 用 CSV 的 8 个 business_key 行构造一个单 sheet xlsx（同表头、同值）
    rows = list(csv.reader(open(CSV, encoding="utf-8")))
    header = rows[0]
    body = [r for r in rows[1:] if r and r[0].strip()]  # 只取有编号的 8 行
    xlsx = os.path.join(wd, "same.xlsx")
    mkx.write_workbook(xlsx, [{"name": "名单", "rows": [header] + body}])
    env = run_import(xlsx, wd)
    assert env["counts"]["unchanged"] == 8, env["counts"]
    assert env["counts"]["added"] == 0 and env["counts"]["updated"] == 0
    assert Workdir(wd).data_version == dv1, "同内容换格式不应涨版本"


# ---- A5 回归：同编号同批冲突隔离 / 去重 / 同名歧义 --------------------------
_H = ["会员编号", "企业名称", "所属行业", "所在地区"]


def test_duplicate_business_key_conflict_isolated():
    wd = fresh_workdir()
    p = _write_csv(wd, [_H,
                        ["C001", "匿名丙", "工业传感器", "深圳"],
                        ["C001", "匿名丁", "工业传感器", "杭州"]])
    env = run_import(p, wd)
    assert any(i["kind"] == "duplicate_business_key_conflict" for i in env["issues"]), "必须报告同编号冲突"
    assert env["counts"]["added"] == 0, "冲突主体不得作为新增写入"
    w = Workdir(wd)
    ent = _by_bk(w, "C001")
    assert ent["status"] == "conflicted" and ent["fields"] == {}, "冲突主体必须隔离、不按行序定值"
    assert len(ent.get("conflict_variants", [])) == 2, "必须保留两个冲突变体证据"


def test_duplicate_business_key_conflict_order_independent():
    wd1 = fresh_workdir()
    wd2 = fresh_workdir()
    rows_a = [_H, ["C001", "匿名丙", "工业传感器", "深圳"],
              ["C001", "匿名丁", "工业传感器", "杭州"]]
    rows_b = [_H, ["C001", "匿名丁", "工业传感器", "杭州"],
              ["C001", "匿名丙", "工业传感器", "深圳"]]
    run_import(_write_csv(wd1, rows_a), wd1)
    run_import(_write_csv(wd2, rows_b), wd2)
    v1 = _by_bk(Workdir(wd1), "C001")["conflict_variants"]
    v2 = _by_bk(Workdir(wd2), "C001")["conflict_variants"]
    regions1 = sorted(x["fields"].get("region") for x in v1)
    regions2 = sorted(x["fields"].get("region") for x in v2)
    assert regions1 == regions2 == ["杭州", "深圳"], "隔离结果不得按行序改变"


def test_same_business_key_same_content_dedup():
    wd = fresh_workdir()
    p = _write_csv(wd, [_H,
                        ["C001", "匿名丙", "工业传感器", "深圳"],
                        ["C001", "匿名丙", "工业传感器", "深圳"]])
    env = run_import(p, wd)
    assert not any(i["kind"] == "duplicate_business_key_conflict" for i in env["issues"])
    assert env["counts"]["added"] == 1 and env["counts"]["unchanged"] == 1, env["counts"]
    assert _by_bk(Workdir(wd), "C001")["fields"].get("region") == "深圳"


def test_name_ambiguity_flagged_not_merged():
    wd = fresh_workdir()
    p = _write_csv(wd, [_H,
                        ["M001", "同名公司", "工业传感器", "深圳"],
                        ["M002", "同名公司", "工业传感器", "广州"]])
    env = run_import(p, wd)
    amb = [i for i in env["issues"] if i["kind"] == "name_ambiguity"]
    assert amb, "同名不同编号应提示名称歧义"
    w = Workdir(wd)
    same_name = [e for e in w.all_entities().values()
                 if e.get("fields", {}).get("name") == "同名公司"]
    assert len(same_name) == 2, "同名不同编号必须保留为两个主体，不合并"



def test_existing_entity_conflict_bumps_version_and_preserves_history():
    wd = fresh_workdir()
    p = _write_csv(wd, [_H, ["C001", "匿名丙", "工业传感器", "深圳"]])
    first = run_import(p, wd)
    before = _by_bk(Workdir(wd), "C001")
    version = before["version"]
    p = _write_csv(wd, [_H, ["C001", "匿名丙", "工业传感器", "深圳"],
                            ["C001", "匿名丁", "工业传感器", "杭州"]])
    second = run_import(p, wd)
    after = _by_bk(Workdir(wd), "C001")
    assert second["data_version"] == first["data_version"] + 1
    assert after["version"] == version + 1 and after["status"] == "conflicted"
    assert after["history"][-1]["fields_snapshot"]["name"] == "匿名丙"
    assert after["history"][-1]["new_status"] == "conflicted"
    third = run_import(p, wd)
    assert third["data_version"] == second["data_version"], "同样冲突重放不得反复涨版本"


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_import: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)