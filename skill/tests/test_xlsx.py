"""ms_xlsx 标准库 OOXML 读取测试：多 sheet、公式缓存值、合并单元格、错误值单元格。"""
from __future__ import annotations

import os

from _harness import fresh_workdir, run_tests, XLSX
import make_sample_xlsx as mkx

from ms_xlsx import read_xlsx, sheet_to_grid, _col_to_index
from ms_common import MSError, EXIT_PARSE


def test_col_to_index():
    assert _col_to_index("A1") == 0
    assert _col_to_index("B3") == 1
    assert _col_to_index("Z1") == 25
    assert _col_to_index("AA1") == 26


def test_read_shipped_sample():
    data = read_xlsx(XLSX)
    names = [s["name"] for s in data["sheets"]]
    assert names == ["会员名单", "说明"], names
    s1 = data["sheets"][0]
    assert s1["formula_cells"] == 1
    grid = sheet_to_grid(s1)
    assert grid[0][0] == "会员编号"
    assert grid[1][0] == "M001"
    # 合并单元格在说明表
    assert data["sheets"][1]["merged"] == ["A1:B1"]


def test_formula_cached_value_not_recomputed():
    data = read_xlsx(XLSX)
    grid = sheet_to_grid(data["sheets"][0])
    # J 列(第10列) M001 行读的是缓存值 "12"（LEN 结果），不重算
    assert grid[1][9] == "12"
    assert grid[2][9] == "3"


def test_multi_sheet_boundary_reported():
    data = read_xlsx(XLSX)
    assert any("工作表" in b for b in data["boundaries"])


def test_merged_and_error_and_num_cells():
    wd = fresh_workdir()
    p = os.path.join(wd, "edge.xlsx")
    rows = [
        ["标题", None, ("err", "#REF!")],
        ["A", ("num", "42"), "B"],
    ]
    mkx.write_workbook(p, [{"name": "S", "rows": rows}], merges={0: ["A1:B1"]})
    data = read_xlsx(p)
    s = data["sheets"][0]
    assert s["merged"] == ["A1:B1"]
    assert s["error_cells"] == 1
    assert any("错误值" in b for b in data["boundaries"])
    grid = sheet_to_grid(s)
    assert grid[0][0] == "标题"
    assert grid[0][2] == "#REF!"
    assert grid[1][1] == "42"


def test_bad_zip_raises_parse_error():
    wd = fresh_workdir()
    p = os.path.join(wd, "bad.xlsx")
    with open(p, "wb") as f:
        f.write(b"not a zip")
    try:
        read_xlsx(p)
        raise AssertionError("应抛 MSError")
    except MSError as e:
        assert e.code == EXIT_PARSE


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_xlsx: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
