#!/usr/bin/env python3
"""用标准库生成匿名样例 members_sample.xlsx（无需 openpyxl）。

包含两个工作表、一个合并单元格、一个公式单元格（带缓存值），用于演示
ms_xlsx 标准库 OOXML 读取及其边界处理。业务编号(M001 等)与 members_sample.csv
一致，因此按显式编号会解析到同一稳定 entity_id（跨文件/格式身份稳定）；
但本表比 CSV 多出“关注度(公式)”列，内容确有差异，故再次导入会按
“同主体、内容变化→涨版本并记变更”处理（而非“内容不变→幂等”）。
纯“同内容换格式不涨版本”的用例在 tests/ 中用生成的一致内容文件验证。

本模块同时导出 write_workbook()，供 tests/ 生成任意样例工作簿（多 sheet、
合并单元格、公式/数字/共享字符串单元格），不引入任何第三方依赖。
"""
from __future__ import annotations

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "members_sample.xlsx")

_SS_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SharedStrings:
    def __init__(self):
        self.items = []
        self.index = {}

    def add(self, text: str) -> int:
        if text in self.index:
            return self.index[text]
        i = len(self.items)
        self.items.append(text)
        self.index[text] = i
        return i

    def xml(self) -> str:
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<sst xmlns="%s" count="%d" uniqueCount="%d">' % (_SS_NS, len(self.items), len(self.items))]
        for t in self.items:
            parts.append('<si><t xml:space="preserve">%s</t></si>' % _esc(t))
        parts.append("</sst>")
        return "".join(parts)


def _cell_xml(ref: str, val, shared: SharedStrings) -> str:
    """val：str(共享字符串)/("num",n)/("formula",f,cached)/("err",text)/None(空)。"""
    if val is None:
        return ""
    if isinstance(val, tuple) and val and val[0] == "num":
        return '<c r="%s"><v>%s</v></c>' % (ref, val[1])
    if isinstance(val, tuple) and val and val[0] == "err":
        return '<c r="%s" t="e"><v>%s</v></c>' % (ref, val[1])
    if isinstance(val, tuple) and val and val[0] == "formula":
        return '<c r="%s"><f>%s</f><v>%s</v></c>' % (ref, val[1], val[2])
    return '<c r="%s" t="s"><v>%d</v></c>' % (ref, shared.add(str(val)))


def _sheet_xml(rows, shared: SharedStrings, merged=None) -> str:
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
           '<worksheet xmlns="%s">' % _SS_NS
    mc = ""
    if merged:
        mc = '<mergeCells count="%d">%s</mergeCells>' % (
            len(merged), "".join('<mergeCell ref="%s"/>' % m for m in merged))
    body = ["<sheetData>"]
    for r, row in enumerate(rows, 1):
        cells = []
        for c, val in enumerate(row):
            cx = _cell_xml("%s%d" % (col_letter(c), r), val, shared)
            if cx:
                cells.append(cx)
        if cells:
            body.append('<row r="%d">%s</row>' % (r, "".join(cells)))
    body.append("</sheetData></worksheet>")
    return head + mc + "".join(body)  # schema 顺序：mergeCells 在 sheetData 之前


def write_workbook(path: str, sheets: list, merges: dict = None) -> None:
    """sheets: [{"name": str, "rows": [[cell,...]]}], merges: {sheet_index: [refs]}。"""
    merges = merges or {}
    shared = SharedStrings()
    n = len(sheets)
    # 先序列化所有工作表（填充共享字符串表），再写 sharedStrings.xml
    sheet_xmls = [_sheet_xml(s["rows"], shared, merges.get(i)) for i, s in enumerate(sheets)]
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    for i in range(1, n + 1):
        ct.append('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i)
    ct.append('<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>')
    ct.append("</Types>")

    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="%s">'
                 '<Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>' % (_PKG_REL_NS, _REL_NS))

    wb_sheets = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_esc(s["name"]), i + 1, i + 1)
                        for i, s in enumerate(sheets))
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'
                % (_SS_NS, _REL_NS, wb_sheets))

    wb_rels_parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                     '<Relationships xmlns="%s">' % _PKG_REL_NS]
    for i in range(1, n + 1):
        wb_rels_parts.append('<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' % (i, _REL_NS, i))
    wb_rels_parts.append("</Relationships>")
    wb_rels = "".join(wb_rels_parts)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/sharedStrings.xml", shared.xml())
        for i, sx in enumerate(sheet_xmls, 1):
            z.writestr("xl/worksheets/sheet%d.xml" % i, sx)


def main():
    header = ["会员编号", "企业名称", "所属行业", "所在地区", "会员状态",
              "续费状态", "可联系", "加入日期", "资质", "关注度(公式)"]
    data = [
        ["M001", "深圳精密传感科技有限公司", "工业传感器", "深圳", "老会员", "未续费", "可联系", "2021-03-15", "高新技术企业", ("formula", "LEN(B2)", "12")],
        ["M002", "深圳智联传感股份有限公司", "工业传感器", "深圳", "老会员", "未续费", "可联系", "2020-07-01", None, ("num", "3")],
        ["M003", "广州华测仪器有限公司", "工业传感器", "广州", "老会员", "未续费", "拒绝联系", "2019-05-20", "ISO9001", ("num", "2")],
        ["M004", "深圳宏图电子有限公司", "工业传感器", "深圳", "老会员", "已续费", "可联系", "2022-01-10", None, ("num", "1")],
        ["M005", "深圳明睿传感有限公司", "工业传感器", "深圳", "老会员", None, "可联系", "2021-11-11", None, ("num", "2")],
        ["M008", "深圳注销边缘公司", "工业传感器", "深圳", "已注销", "未续费", "可联系", "2020-01-01", None, ("num", "0")],
    ]
    sheets = [
        {"name": "会员名单", "rows": [header] + data},
        {"name": "说明", "rows": [
            ["说明：本表为匿名样例，仅用于演示标准库 OOXML 读取；正式数据请用您自己的文件。", ""],
            ["第二工作表演示多 sheet 处理：导入默认取第一个含表头的工作表。", ""],
            ["本 A1:B1 为合并单元格，仅演示读取边界，不影响第一张表的可导入数据。", ""],
        ]},
    ]
    write_workbook(OUT, sheets, merges={1: ["A1:B1"]})
    print("written:", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
