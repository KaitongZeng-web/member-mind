"""导入普通会员 CSV / XLSX：表头识别、字段映射、冲突/缺失/重复/异常报告、幂等入库。

只读用户文件，不联网、不装依赖。XLSX 走 ms_xlsx 标准库读取。
"""
from __future__ import annotations

import csv
import io
import os
import re
import datetime as _dt
from typing import Any, Optional

from ms_common import (
    MSError, EXIT_PARSE, EXIT_VALIDATION, EXIT_NOT_FOUND, now_iso,
    norm, is_blank, as_text, make_envelope, short_hash,
)
from ms_xlsx import read_xlsx, sheet_to_grid
from ms_schema import suggest_mapping, CANONICAL_FIELDS
from ms_store import Workdir, _content_hash

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_FIELDS = {"joined_date", "last_activity_date"}


def _fingerprint(fields: dict) -> str:
    return "|".join([norm(fields.get("name", "")), norm(fields.get("region", "")),
                     norm(fields.get("industry", ""))])


def _read_grid(path: str, sheet: Optional[str]) -> tuple:
    """读文件为二维字符串网格 + 边界说明。返回 (grid, boundaries, source_desc)。"""
    ext = os.path.splitext(path)[1].lower()
    boundaries = []
    if ext in (".xlsx", ".xlsm"):
        if not os.path.exists(path):
            raise MSError(EXIT_NOT_FOUND, "文件不存在: %s" % path, {"path": path})
        data = read_xlsx(path)
        boundaries.extend(data["boundaries"])
        sheets = data["sheets"]
        chosen = None
        if sheet:
            for s in sheets:
                if s["name"] == sheet:
                    chosen = s
                    break
            if chosen is None:
                raise MSError(EXIT_VALIDATION, "未找到指定工作表: %s" % sheet,
                              {"available": [s["name"] for s in sheets]})
        else:
            # 取第一个含数据的工作表
            chosen = sheets[0]
        grid = sheet_to_grid(chosen)
        return grid, boundaries, "%s#%s" % (os.path.basename(path), chosen["name"])
    # 其它按 CSV 处理
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            grid = [row for row in reader]
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="gbk", newline="") as f:
                reader = csv.reader(f)
                grid = [row for row in reader]
        except Exception as e:  # noqa: BLE001
            raise MSError(EXIT_PARSE, "CSV 解码失败: %s: %s" % (path, e), {"path": path})
    except OSError as e:
        raise MSError(EXIT_NOT_FOUND, "无法读取文件: %s: %s" % (path, e), {"path": path})
    # 去掉完全空行
    grid = [r for r in grid if any((c or "").strip() for c in r)]
    if not grid:
        raise MSError(EXIT_PARSE, "文件为空或无数据行: %s" % path, {"path": path})
    return grid, boundaries, os.path.basename(path)


def _detect_header(grid: list) -> int:
    """表头行索引：第一行非空行。"""
    for i, row in enumerate(grid):
        if any((c or "").strip() for c in row):
            return i
    return 0


def _validate_value(field: str, value: str) -> tuple:
    """返回 (ok, cleaned_value, reason_if_bad)。日期字段做真实有效性校验。"""
    v = value.strip()
    if field in _DATE_FIELDS:
        if _DATE_RE.match(v):
            try:
                _dt.date.fromisoformat(v)
                return True, v, None
            except ValueError:
                return False, None, "日期值无效（年月日超出范围）: %r" % value
        return False, None, "日期格式无效（应为 YYYY-MM-DD）: %r" % value
    return True, v, None


def run_import(file_path: str, workdir_path: str, sheet: Optional[str] = None,
               mapping: Optional[dict] = None, id_column: Optional[str] = None,
               is_mock: Optional[bool] = None) -> dict:
    """导入会员表。

    is_mock：显式声明本批数据的来源模式（True=模拟/演示数据，False=真实本地文件，
    None=未声明，按真实本地文件处理）。**模式只表示资料来源，不代表已核实工商真实性。**
    声明 mock 后工作区粘性标记，下游简报/纪要/计划/导出全部继承 mock，不被降级。
    """
    if is_mock is not None and not isinstance(is_mock, bool):
        raise MSError(EXIT_VALIDATION, "is_mock 必须是布尔值",
                      {"type": type(is_mock).__name__})
    grid, boundaries, source_desc = _read_grid(file_path, sheet)
    hidx = _detect_header(grid)
    header = [as_text(c) for c in grid[hidx]]
    data_rows = grid[hidx + 1:]
    if not header or all(is_blank(h) for h in header):
        raise MSError(EXIT_PARSE, "未检测到有效表头", {"file": file_path})

    suggestion = suggest_mapping(header)
    auto_map = suggestion["mapping"]
    conflicts = suggestion["conflicts"]

    # 应用显式映射（覆盖自动建议）；校验字段名合法
    col_to_field = {}
    for col in header:
        if mapping and col in mapping:
            field = mapping[col]
            if field and field not in CANONICAL_FIELDS:
                # 自定义字段允许，但需登记
                col_to_field[col] = field
            else:
                col_to_field[col] = field or None
        else:
            col_to_field[col] = auto_map.get(col)

    if id_column:
        if id_column not in header:
            raise MSError(EXIT_VALIDATION, "指定的编号列不存在于表头: %s" % id_column,
                          {"header": header})
        col_to_field[id_column] = "business_key"

    issues = []
    for field, cols in conflicts.items():
        # 冲突：多个源列指向同一规范字段，未显式解决 → 该字段判为未知
        resolved = mapping is not None and any(
            (mapping.get(c) == field) for c in cols)
        if not resolved:
            issues.append({
                "kind": "field_conflict", "field": field, "columns": cols,
                "reason": "多列命中同一字段，未显式指定映射，暂按未知处理",
                "blocking": False,
            })

    # 冲突字段集合（未解决的）→ 判未知
    conflicted_fields = set()
    for field, cols in conflicts.items():
        resolved = mapping is not None and any((mapping.get(c) == field) for c in cols)
        if not resolved:
            conflicted_fields.add(field)

    wd = Workdir(workdir_path)
    counts = {"added": 0, "updated": 0, "unchanged": 0, "needs_review": 0, "rejected": 0}
    row_reports = []
    custom_fields_seen = set()
    name_ambiguity_reported = set()
    quarantined_done = set()

    def _quarantine_conflict(nbk, bk, rows):
        """同编号同批冲突：隔离该主体（status=conflicted），保留两个变体证据，不按行序决定。"""
        qeid = "E-" + short_hash("ID|" + nbk, 12)
        variants = [{"row": r["row_num"], "fields": r["fields"]} for r in rows]
        wd.register_fields(sorted({key for variant in variants for key in variant["fields"]}))
        existing = wd.get_entity(qeid)
        signature = sorted(_content_hash(v["fields"]) for v in variants)
        previous_signature = sorted(_content_hash(v.get("fields", {}))
                                    for v in (existing or {}).get("conflict_variants", []))
        changed = (existing is None or existing.get("status") != "conflicted"
                   or signature != previous_signature)
        old_status = existing.get("status") if existing else None
        if changed:
            wd.bump_data_version()
        if existing is None:
            wd.index["entities"][qeid] = {
                "entity_id": qeid, "business_key": bk, "version": 1,
                "fields": {}, "field_sources": {},
                "unknowns": {"business_key": {"reason": "同编号同批内容冲突，已隔离待人工核对"}},
                "content_hash": "", "status": "conflicted",
                "created_at": now_iso(), "updated_at": now_iso(),
                "conflict_variants": variants,
                "history": [{"version": 1, "data_version": wd.data_version, "at": now_iso(),
                             "note": "同编号同批冲突，隔离待核对", "variants": variants}],
            }
        else:
            if changed:
                existing["version"] = int(existing.get("version", 1)) + 1
                existing["updated_at"] = now_iso()
                existing.setdefault("history", []).append({
                    "version": existing["version"], "data_version": wd.data_version,
                    "at": now_iso(), "note": "同编号冲突，旧资料仅保留为历史",
                    "old_status": old_status, "new_status": "conflicted",
                    "fields_snapshot": dict(existing.get("fields", {})),
                    "field_sources_snapshot": dict(existing.get("field_sources", {})),
                    "variants": variants,
                })
            existing["status"] = "conflicted"
            existing["conflict_variants"] = variants
        if changed:
            wd.index["change_events"].append({
                "event_id": "chg-" + short_hash(qeid + "|conflict|" + str(wd.data_version), 12),
                "entity_id": qeid, "kind": "identity_conflict",
                "old_status": old_status, "new_status": "conflicted",
                "data_version": wd.data_version, "at": now_iso(), "variants": variants,
            })
        wd.mark_entity_mode(qeid, is_mock)
        issues.append({"kind": "duplicate_business_key_conflict", "business_key": bk,
                       "rows": [r["row_num"] for r in rows], "variants": variants,
                       "reason": "同一业务编号在同一批输入中对应不同内容，未按行序覆盖，已隔离该主体待人工核对",
                       "blocking": True})

    def _check_name_ambiguity(name_val, this_eid):
        nname = norm(name_val)
        if not nname or nname in name_ambiguity_reported:
            return
        others = [eid for eid, e in wd.all_entities().items()
                  if eid != this_eid and norm(e.get("fields", {}).get("name", "")) == nname]
        if others:
            name_ambiguity_reported.add(nname)
            issues.append({"kind": "name_ambiguity", "name": name_val,
                           "entity_ids": [this_eid] + others,
                           "reason": "存在同名但不同编号的主体，已保留为不同实体、未合并；请人工确认是否同一主体",
                           "blocking": False})

    # 第一遍：解析所有数据行为 fields/unknowns，并算内容哈希
    parsed = []
    for offset, raw in enumerate(data_rows):
        row_num = hidx + 1 + offset + 1  # 1 基源文件行号（仅作证据）
        cells = [as_text(raw[i]) if i < len(raw) else "" for i in range(len(header))]
        fields = {}
        unknowns = {}
        row_issues = []
        for col, field in col_to_field.items():
            if not field:
                continue
            idx = header.index(col)
            raw_val = cells[idx]
            if field in conflicted_fields:
                unknowns[field] = {"reason": "多列冲突未解决: %s" % ", ".join(conflicts.get(field, []))}
                continue
            if is_blank(raw_val):
                unknowns[field] = {"reason": "源单元格为空（第 %d 行）" % row_num}
                continue
            ok, cleaned, reason = _validate_value(field, raw_val)
            if not ok:
                unknowns[field] = {"reason": reason}
                row_issues.append({"row": row_num, "field": field, "reason": reason, "blocking": False})
                continue
            fields[field] = cleaned
            if field not in CANONICAL_FIELDS:
                custom_fields_seen.add(field)
        for col in header:
            if col_to_field.get(col) or is_blank(col):
                continue
            idx = header.index(col)
            raw_val = cells[idx]
            key = "custom:" + norm(col)
            if not is_blank(raw_val):
                fields[key] = raw_val.strip()
                custom_fields_seen.add(key)

        has_name = not is_blank(fields.get("name"))
        has_bk = not is_blank(fields.get("business_key"))
        parsed.append({
            "row_num": row_num, "fields": fields, "unknowns": unknowns,
            "row_issues": row_issues, "has_identity": has_name or has_bk,
            "chash": _content_hash(fields),
        })

    # 第二遍：无编号行按“指纹+内容”分组，统计重名规模并分配稳定序号
    group_size = {}
    for p in parsed:
        if not p["has_identity"] or not is_blank(p["fields"].get("business_key")):
            continue
        fp = _fingerprint(p["fields"])
        gkey = short_hash("FP|" + fp + "|" + p["chash"], 16)
        p["gkey"] = gkey
        group_size[gkey] = group_size.get(gkey, 0) + 1
    group_seen = {}

    # 业务编号同批冲突预检（A5）：同编号同内容可去重；同编号冲突字段必须隔离，不按行序覆盖
    bk_rows = {}
    for p in parsed:
        b = p["fields"].get("business_key")
        if not is_blank(b):
            bk_rows.setdefault(norm(b), []).append(p)
    conflicted_bks = set()
    for nbk, rows in bk_rows.items():
        if len(rows) > 1 and len({r["chash"] for r in rows}) > 1:
            conflicted_bks.add(nbk)

    # 第三遍：逐行入库
    for p in parsed:
        row_num = p["row_num"]
        fields = p["fields"]
        source = "%s#row%d" % (source_desc, row_num)
        if not p["has_identity"]:
            counts["rejected"] += 1
            row_reports.append({
                "row": row_num, "action": "rejected",
                "reason": "缺少主体识别信息（无名称且无编号）", "source": source,
            })
            continue

        bk = fields.get("business_key")
        if not is_blank(bk):
            nbk = norm(bk)
            if nbk in conflicted_bks:
                # 同编号同批冲突：隔离，不 upsert、不按行序覆盖（A5）
                counts["needs_review"] += 1
                row_reports.append({
                    "row": row_num, "action": "needs_review", "business_key": bk,
                    "reason": "同编号同批内容冲突，已隔离，待人工核对", "source": source,
                })
                if nbk not in quarantined_done:
                    quarantined_done.add(nbk)
                    _quarantine_conflict(nbk, bk, bk_rows[nbk])
                continue
            identity_key = "BK|" + nbk
            entity_id = "E-" + short_hash("ID|" + nbk, 12)
            strong_key = True
            is_duplicate = False
        else:
            gkey = p["gkey"]
            occ = group_seen.get(gkey, 0)
            group_seen[gkey] = occ + 1
            identity_key = "FPG|" + gkey + "#" + str(occ)
            base = "E-" + short_hash("FPG|" + gkey, 12)
            entity_id = base if occ == 0 else base + "-" + str(occ)
            strong_key = False
            is_duplicate = group_size.get(gkey, 1) > 1  # 重名不合并，全部标记待确认

        result = wd.upsert_row(source, fields, p["unknowns"], identity_key,
                               entity_id, strong_key, is_duplicate)
        wd.register_fields(list(fields.keys()))
        wd.mark_entity_mode(result["entity_id"], is_mock)
        if not is_blank(fields.get("name")):
            _check_name_ambiguity(fields["name"], result["entity_id"])
        action = result["action"]
        if is_duplicate:
            counts["needs_review"] += 1
            row_reports.append({
                "row": row_num, "action": "needs_review", "entity_id": result["entity_id"],
                "reason": "同名同区同业但无编号，未自动合并，待人工确认是否同一主体",
                "source": source,
            })
            issues.append({
                "kind": "suspected_duplicate", "row": row_num, "entity_id": result["entity_id"],
                "reason": "重名未合并（无编号佐证）", "blocking": False,
            })
        elif action == "added":
            counts["added"] += 1
            row_reports.append({"row": row_num, "action": "added",
                                "entity_id": result["entity_id"], "source": source})
        elif action == "updated":
            counts["updated"] += 1
            row_reports.append({"row": row_num, "action": "updated",
                                "entity_id": result["entity_id"], "changed_fields": result["changed_fields"],
                                "source": source})
        else:
            counts["unchanged"] += 1
            row_reports.append({"row": row_num, "action": "unchanged",
                                "entity_id": result["entity_id"], "source": source})
        for ri in p["row_issues"]:
            issues.append(ri)

    total = sum(counts.values())
    # 校验互斥类别总数 = 输入数据行数
    if total != len(data_rows):
        issues.append({
            "kind": "count_mismatch", "reason": "分类总数 %d 与数据行数 %d 不一致" % (total, len(data_rows)),
            "blocking": True,
        })

    import_id = "imp-" + short_hash(file_path + now_iso(), 10)
    wd.record_import_mode(is_mock, import_id, os.path.abspath(file_path))
    wd.index["imports"].append({
        "import_id": import_id, "file": os.path.abspath(file_path),
        "sheet": sheet, "data_version": wd.data_version, "at": now_iso(),
        "counts": counts, "data_rows": len(data_rows), "boundaries": boundaries,
        "is_mock": is_mock,
    })
    wd.save()
    wd.write_readme()

    status = "succeeded"
    if counts["rejected"] or counts["needs_review"] or any(i.get("blocking") for i in issues):
        status = "needs_review"

    # 未映射列（保留为 custom 字段，但提示宿主可建议显式映射，P2.2）
    unmapped_cols = [col for col in header
                     if col_to_field.get(col) is None and not is_blank(col)]

    env = make_envelope(
        command="import",
        mode="mock" if is_mock else "live",
        data_version=wd.data_version,
        status=status,
        items=row_reports,
        issues=issues,
        extra={
            "import_id": import_id,
            "file": os.path.abspath(file_path),
            "is_mock": bool(is_mock),
            "header": header,
            "suggested_mapping": {k: v for k, v in auto_map.items()},
            "field_conflicts": conflicts,
            "unmapped_columns": unmapped_cols,
            "counts": counts,
            "total_data_rows": len(data_rows),
            "format_boundaries": boundaries,
            "workspace_is_mock": wd.data_is_mock(),
            "workdir": wd.path,
            "mode_note": ("本批数据已声明为模拟(mock)数据：来源模式为 mock，"
                          "仅表示资料来源为演示数据，不代表已核实工商真实性；"
                          "工作区已粘性标记，下游简报/纪要/计划/导出均继承 mock。"
                          if is_mock else
                          "本批数据按真实本地文件处理（未声明 is_mock）；"
                          "mode 只表示资料来源，不代表已核实工商真实性。"),
            "next_actions": [
                "核对 needs_review 的重名、同编号冲突与字段冲突",
                *(["以下列未映射到规范字段、已按自定义列保留，宿主可建议显式映射：%s"
                   % ", ".join(unmapped_cols)] if unmapped_cols else []),
                "用 filter 子命令按显式条件筛选（条件由宿主从自然语言生成）",
            ],
        },
    )
    return env
