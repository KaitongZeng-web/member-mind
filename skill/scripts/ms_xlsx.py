"""用 Python 标准库读取 .xlsx（OOXML），不依赖 openpyxl 等第三方库。

返回每个工作表为稀疏单元格网格，并标注公式、合并单元格、日期序列号等边界。
仅支持读取，不写 xlsx。
"""
from __future__ import annotations

import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from ms_common import MSError, EXIT_PARSE

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _col_to_index(ref: str) -> int:
    """把 'B3' 之类的列字母转成 0 基索引。"""
    m = re.match(r"([A-Z]+)", ref)
    if not m:
        return 0
    letters = m.group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


class Cell:
    __slots__ = ("ref", "value", "type", "formula")

    def __init__(self, ref: str, value: str, ctype: str, formula: Any = None):
        self.ref = ref
        self.value = value
        self.type = ctype
        self.formula = formula

    def to_dict(self) -> dict:
        d = {"ref": self.ref, "value": self.value, "type": self.type}
        if self.formula is not None:
            d["formula"] = self.formula
        return d


def _parse_shared_strings(z: zipfile.ZipFile) -> list:
    texts = []
    try:
        data = z.read("xl/sharedStrings.xml")
    except KeyError:
        return texts
    root = ET.fromstring(data)
    for si in root:
        if _local(si.tag) != "si":
            continue
        parts = []
        for node in si.iter():
            if _local(node.tag) == "t" and node.text is not None:
                parts.append(node.text)
        texts.append("".join(parts))
    return texts


def _cell_value(c: ET.Element, shared: list) -> Cell:
    ref = c.get("r", "")
    ctype = c.get("t", "n")
    formula = None
    value_text = ""
    for child in c:
        ln = _local(child.tag)
        if ln == "f":
            formula = child.text if child.text is not None else ""
        elif ln == "v":
            value_text = child.text if child.text is not None else ""
        elif ln == "is":
            # inlineStr
            parts = [n.text for n in child.iter() if _local(n.tag) == "t" and n.text]
            value_text = "".join(p for p in parts if p)
    if ctype == "s":
        try:
            value = shared[int(value_text)]
        except (ValueError, IndexError):
            value = ""
        return Cell(ref, value, "s", formula)
    if ctype == "inlineStr":
        return Cell(ref, value_text, "inlineStr", formula)
    if ctype == "b":
        return Cell(ref, "TRUE" if value_text == "1" else "FALSE", "b", formula)
    if ctype == "str":
        return Cell(ref, value_text, "str", formula)
    if ctype == "e":
        return Cell(ref, value_text, "e", formula)
    # 数字或空
    return Cell(ref, value_text, "n" if value_text != "" else "empty", formula)


def _parse_sheet(z: zipfile.ZipFile, path: str, shared: list) -> dict:
    root = ET.fromstring(z.read(path))
    rows_map = {}
    max_col = -1
    formula_cells = 0
    error_cells = 0
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        rnum = int(row.get("r", "0"))
        cells = []
        for c in row:
            if _local(c.tag) != "c":
                continue
            cell = _cell_value(c, shared)
            ci = _col_to_index(cell.ref) if cell.ref else len(cells)
            if cell.formula is not None:
                formula_cells += 1
            if cell.type == "e":
                error_cells += 1
            cells.append((ci, cell))
            if ci > max_col:
                max_col = ci
        rows_map[rnum] = cells
    merged = []
    for mc in root.iter():
        if _local(mc.tag) == "mergeCell":
            ref = mc.get("ref")
            if ref:
                merged.append(ref)
    # 组装成按行号排序的稀疏行（每行为 {col_index: Cell}）
    rows = []
    for rnum in sorted(rows_map):
        row_cells = {}
        for ci, cell in rows_map[rnum]:
            row_cells[ci] = cell
        rows.append({"row_num": rnum, "cells": row_cells})
    return {
        "rows": rows,
        "max_col": max_col,
        "merged": merged,
        "formula_cells": formula_cells,
        "error_cells": error_cells,
    }


def _sheet_paths(z: zipfile.ZipFile) -> list:
    """返回 [(sheet_name, worksheet_path)]，按 workbook 顺序。"""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rel_map = {}
    for rel in rels:
        rel_map[rel.get("Id")] = rel.get("Target")
    result = []
    for sheet in wb.iter():
        if _local(sheet.tag) != "sheet":
            continue
        name = sheet.get("name", "")
        rid = sheet.get(_REL_NS + "id") or sheet.get("id")
        target = rel_map.get(rid, "")
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = "xl/" + target if not target.startswith("xl/") else target
        result.append((name, path))
    return result


def read_xlsx(path: str) -> dict:
    """读取 xlsx，返回 {sheets:[{name, rows, max_col, merged, formula_cells, error_cells}], boundaries:[...]}。"""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise MSError(EXIT_PARSE, "不是有效的 xlsx（zip）文件: %s: %s" % (path, e), {"path": path})
    except OSError as e:
        raise MSError(EXIT_PARSE, "无法打开 xlsx: %s: %s" % (path, e), {"path": path})
    boundaries = []
    with z:
        names = z.namelist()
        if "xl/workbook.xml" not in names:
            raise MSError(EXIT_PARSE, "xlsx 缺少 workbook.xml，可能不是标准 xlsx", {"path": path})
        shared = _parse_shared_strings(z)
        sheet_defs = _sheet_paths(z)
        if not sheet_defs:
            raise MSError(EXIT_PARSE, "xlsx 未包含任何工作表", {"path": path})
        sheets = []
        for name, spath in sheet_defs:
            try:
                parsed = _parse_sheet(z, spath, shared)
            except KeyError:
                boundaries.append("工作表 %r 的数据部分缺失（%s）" % (name, spath))
                continue
            parsed["name"] = name
            sheets.append(parsed)
        if len(sheets) > 1:
            boundaries.append(
                "检测到 %d 个工作表（%s）；导入默认取第一个含表头的工作表，其余需显式指定。"
                % (len(sheets), "、".join(s["name"] for s in sheets))
            )
        total_formula = sum(s["formula_cells"] for s in sheets)
        total_error = sum(s["error_cells"] for s in sheets)
        if total_formula:
            boundaries.append(
                "检测到 %d 个公式单元格；读取的是公式缓存值，不重算公式，公式本身在 formula 字段保留。" % total_formula
            )
        if total_error:
            boundaries.append("检测到 %d 个错误值单元格（如 #REF!/#DIV/0!），已按错误文本读取。" % total_error)
        merged_total = sum(len(s["merged"]) for s in sheets)
        if merged_total:
            boundaries.append(
                "检测到 %d 处合并单元格；仅保留合并区左上角的值，其余位置为空，可能与视觉不一致。" % merged_total
            )
    return {"sheets": sheets, "boundaries": boundaries}


def sheet_to_grid(sheet: dict) -> list:
    """把稀疏行转成二维字符串网格（list[list[str]]），按 max_col 补齐。"""
    width = sheet["max_col"] + 1
    grid = []
    for row in sheet["rows"]:
        cells = row["cells"]
        line = []
        for ci in range(width):
            cell = cells.get(ci)
            line.append(cell.value if cell is not None else "")
        grid.append(line)
    return grid
