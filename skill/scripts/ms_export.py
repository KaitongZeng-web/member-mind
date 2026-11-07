"""把结构化结果渲染为普通工作人员可读的 MD 与 CSV，并落到 results/ 目录。

CSV 导出统一过公式注入防护；名单、理由、依据、mock/待核实标识不丢失。

解释口径（Terra P2 收口）：排除/待核实条目一律呈现与 JSON **同源**的
`reason_detail`（真正决定根节点结果的分支 + NOT/OR 语义 + 实测值），
绝不从叶列表重建出“未满足: <条件>”这种把 NOT 内部 TRUE 叶子误写成未满足的文案。
"""
from __future__ import annotations

from typing import Any

from ms_common import render_csv, write_text_atomic, safe_join, now_iso, short_hash


def _write(wd, result_id: str, ext: str, text: str) -> str:
    path = wd.result_path(result_id, ext)
    write_text_atomic(path, text)
    return path


def _mode_note(mode: str, what: str) -> str:
    """来源模式说明：mock 必须显式，live 也要说明“只表示资料来源”。"""
    if mode == "mock":
        return "**演示(mock)%s**：结论仅用于体验，不作为机构政策或真实工商信息。" % what
    return "本地真实文件%s（mode 只表示资料来源，不代表已核实工商真实性）。" % what


def _rel(wd, path: str) -> str:
    try:
        return "results/" + path.split("/results/", 1)[1]
    except IndexError:
        return path


# ---- 筛选 ------------------------------------------------------------------
def write_filter_outputs(wd, result_id: str, d: dict) -> dict:
    lines = []
    lines.append("# 会员筛选结果")
    lines.append("")
    mode_note = _mode_note(d.get("mode", "live"),
                           "规则" if d.get("rule_is_mock") else "数据")
    lines.append("- 模式：%s" % mode_note)
    if d.get("data_is_mock") and not d.get("rule_is_mock"):
        lines.append("- 数据来源：本工作区导入时已声明为模拟(mock)数据，"
                     "因此即使规则未标 rule_is_mock，结论仍按 mock 呈现。")
    lines.append("- 规则版本：%s" % (d.get("rule_version") or "未指定"))
    lines.append("- 数据版本 data_version：%d" % wd.data_version)
    lines.append("- 条件树：`%s`" % _compact(d.get("conditions")))
    if d.get("hard_exclusions"):
        lines.append("- 硬排除：`%s`" % _compact(d.get("hard_exclusions")))
    lines.append("")
    c = d.get("counts", {})
    lines.append("汇总：符合 %d｜排除 %d｜待核实 %d（主体总数 %d）"
                 % (c.get("eligible", 0), c.get("excluded", 0), c.get("needs_review", 0), c.get("total", 0)))
    lines.append("")
    lines.append("> %s" % d.get("disclaimer", ""))
    lines.append("")

    lines.append("## 符合（eligible）")
    lines.append("")
    if d["eligible"]:
        lines.append("| entity_id | 名称 | 行业 | 地区 | 会员状态 | 续费 | 可联系 | 命中理由 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for e in d["eligible"]:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                e["entity_id"], e.get("name", ""), _v(e.get("industry")), _v(e.get("region")),
                _v(e.get("member_status")), _v(e.get("renewal_status")), _v(e.get("contactable_status")),
                "；".join(e.get("reasons", [])) or "-"))
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 排除（excluded）")
    lines.append("")
    if d["excluded"]:
        lines.append("| entity_id | 名称 | 排除原因（决定性分支与实测值） |")
        lines.append("|---|---|---|")
        for e in d["excluded"]:
            # 用 reason_detail：只报真正决定根节点结果的分支，含 NOT/OR 语义与实测值；
            # 绝不把 NOT 内结果为 TRUE 的叶子误写成“未满足”（那会误导普通用户）。
            lines.append("| %s | %s | %s |" % (
                e["entity_id"], e.get("name", ""), e.get("reason_detail") or e.get("reason", "")))
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 待核实（needs_review）")
    lines.append("")
    if d["needs_review"]:
        lines.append("| entity_id | 名称 | 未知字段 | 说明（决定性未知分支） |")
        lines.append("|---|---|---|---|")
        for e in d["needs_review"]:
            lines.append("| %s | %s | %s | %s |" % (
                e["entity_id"], e.get("name", ""), ", ".join(e.get("unknown_fields", [])),
                e.get("reason_detail") or e.get("reason", "")))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("---")
    lines.append("生成时间：%s｜未知字段以“未知”表达，不代表否定；优先级不代表续费意愿。" % now_iso())

    md_text = "\n".join(lines) + "\n"

    # CSV：扁平三列决策 + reason_detail（决定性分支与实测值，与 JSON/MD 完全一致）
    header = ["decision", "entity_id", "name", "industry", "region", "member_status",
              "renewal_status", "contactable_status", "reason", "reason_detail",
              "unknown_fields", "rule_is_mock"]
    rows = []
    for e in d["eligible"]:
        rows.append(["eligible", e["entity_id"], e.get("name", ""), _v(e.get("industry")), _v(e.get("region")),
                     _v(e.get("member_status")), _v(e.get("renewal_status")), _v(e.get("contactable_status")),
                     "；".join(e.get("reasons", [])), "", "", str(d.get("rule_is_mock"))])
    for e in d["excluded"]:
        # reason_detail 与 JSON 同源：含 NOT/OR 语义与实测值，绝不把 NOT 内 TRUE 叶子
        # 写成“未满足: <条件>”（那正是误导普通用户的旧缺陷）。
        rows.append(["excluded", e["entity_id"], e.get("name", ""), _v(e.get("industry")), _v(e.get("region")),
                     _v(e.get("member_status")), _v(e.get("renewal_status")), _v(e.get("contactable_status")),
                     e.get("reason", ""), e.get("reason_detail", ""), "", str(d.get("rule_is_mock"))])
    for e in d["needs_review"]:
        rows.append(["needs_review", e["entity_id"], e.get("name", ""), _v(e.get("industry")), _v(e.get("region")),
                     _v(e.get("member_status")), _v(e.get("renewal_status")), _v(e.get("contactable_status")),
                     e.get("reason", ""), e.get("reason_detail", ""),
                     ", ".join(e.get("unknown_fields", [])), str(d.get("rule_is_mock"))])
    csv_text = render_csv(rows, header)

    md = _write(wd, result_id, "md", md_text)
    csv = _write(wd, result_id, "csv", csv_text)
    return {"md": _rel(wd, md), "csv": _rel(wd, csv)}


# ---- 简报 ------------------------------------------------------------------
def write_brief_outputs(wd, brief_id: str, d: dict) -> dict:
    lines = ["# 拜访简报：%s" % d.get("name", d.get("entity_id", "")), ""]
    lines.append("- entity_id：%s" % d.get("entity_id", ""))
    lines.append("- 数据版本：%d｜生成：%s" % (wd.data_version, now_iso()))
    lines.append("- 来源模式：%s" % _mode_note(d.get("mode", "live"), "导入数据"))
    lines.append("")
    lines.append("## 有依据的事实")
    lines.append("")
    if d["facts"]:
        for f in d["facts"]:
            lines.append("- **%s**：%s  \n  来源：%s" % (f["field"], f["value"], f["source"]))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 未知 / 缺失（不补造）")
    lines.append("")
    if d["unknowns"]:
        for u in d["unknowns"]:
            lines.append("- %s：%s" % (u["field"], u["reason"]))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 建议（AI，非事实，需人员判断）")
    lines.append("")
    for s in d.get("suggestions", []) or ["（无）"]:
        lines.append("- %s" % s)
    lines.append("")
    lines.append("## 拜访目标")
    lines.append("")
    for g in d.get("goals", []) or ["（待填写）"]:
        lines.append("- %s" % g)
    lines.append("")
    lines.append("## 需向客户验证的问题")
    lines.append("")
    for q in d.get("questions", []) or ["（无）"]:
        lines.append("- %s" % q)
    lines.append("")
    lines.append("## 准备事项")
    lines.append("")
    for p in d.get("preparations", []) or ["（无）"]:
        lines.append("- %s" % p)
    lines.append("")
    lines.append("> 资质、风险、实控人、会员等级等无依据项未填写；外部冲突保留待核对，不覆盖本地档案。")
    md = _write(wd, brief_id, "md", "\n".join(lines) + "\n")
    return {"md": _rel(wd, md)}


# ---- 纪要（待核对版 / 已核对）---------------------------------------------
def write_minutes_outputs(wd, visit_id: str, d: dict) -> dict:
    status = d.get("status")
    confirmed = status == "confirmed"
    title = {"confirmed": "拜访纪要（已核对）",
             "partially_confirmed": "拜访纪要（部分核对）"}.get(status, "拜访纪要（待核对版）")
    lines = ["# %s：%s" % (title, d.get("entity_id", "（未关联会员）")), ""]
    lines.append("- visit_id：%s" % visit_id)
    lines.append("- 纪要日期：%s" % (d.get("minutes_date") or "未提供"))
    lines.append("- 状态：%s" % (status or ""))
    lines.append("- 来源模式：%s" % _mode_note(d.get("mode", "live"), "纪要/导入数据"))
    if confirmed or status == "partially_confirmed":
        lines.append("- 核对人：%s｜核对时间：%s｜核对版本：%s"
                     % (d.get("confirmed_by", ""), d.get("confirmed_at", ""),
                        d.get("confirmed_extraction_version", "")))
    lines.append("")
    if confirmed:
        note = "以下条目已经人员逐条核对，可作为事实回写；推断项仍不为事实。"
    elif status == "partially_confirmed":
        note = ("**仅部分条目已核对**：标 ✅ 的为已确认，⏳/❌ 的尚未确认为事实；"
                "**不得将整份纪要作为事实回写**。")
    else:
        note = "以下为脚本整理的结构化草稿，**未经人员核对前不作为已确认承诺**；推断项永不为事实。"
    lines.append("> %s" % note)
    lines.append("")

    def section(name, items, show_status=True):
        lines.append("## %s" % name)
        lines.append("")
        if not items:
            lines.append("（无）")
            lines.append("")
            return
        for it in items:
            badge = ""
            if show_status:
                st = it.get("status", "pending")
                badge = {"pending": "⏳待核对", "confirmed": "✅已确认",
                         "rejected": "❌已否决", "inference": "🔎推断",
                         "confirmed_inference": "🔎推断(已核对)"}.get(st, st)
            cite = it.get("citation", {})
            loc = ""
            if cite:
                loc = "  \n  引用：%s 行%s-%s%s" % (
                    cite.get("file", ""), cite.get("line_start", "?"), cite.get("line_end", "?"),
                    ("（原文校验：%s）" % cite.get("verified")) if cite.get("verified") is not None else "")
            lines.append("- [%s] %s%s" % (badge, it.get("text", ""), loc))
        lines.append("")

    section("客户表述", d.get("customer_statements", []))
    section("明确需求", d.get("needs", []))
    section("双方承诺", d.get("commitments", []))
    section("未决问题", d.get("open_questions", []))
    section("推断（AI，需核对）", d.get("inferences", []))

    nc = d.get("no_contact", [])
    if nc:
        lines.append("## 客户拒绝联系要求")
        lines.append("")
        for n in nc:
            if isinstance(n, dict):
                label = n.get("scope") or n.get("text") or "（未注明范围）"
                st = n.get("status", "")
                lines.append("- 范围：%s%s（将影响后续触达；仅已核对项生效）"
                             % (label, ("［%s］" % st) if st else ""))
            else:
                lines.append("- %s（将影响后续触达）" % n)
        lines.append("")
    md = _write(wd, visit_id, "md", "\n".join(lines) + "\n")
    return {"md": _rel(wd, md)}


# ---- 行动计划 --------------------------------------------------------------
def write_plan_outputs(wd, plan_id: str, d: dict) -> dict:
    mode = d.get("mode", "live")
    lines = ["# 后续行动计划：%s" % d.get("entity_id", ""), ""]
    lines.append("- plan_id：%s｜数据版本：%d｜生成：%s｜模式：%s"
                 % (plan_id, wd.data_version, now_iso(),
                    "演示(mock)：仅用于体验，不作为真实承诺或机构政策" if mode == "mock"
                    else "本地（只表示资料来源，不代表已核实工商真实性）"))
    lines.append("- 来源 visit_id：%s" % (d.get("visit_id") or "（无）"))
    lines.append("")
    lines.append("| task_id | 负责人 | 交付物 | 截止 | 依赖 | 状态 | 来源 |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in d.get("tasks", []):
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            t.get("task_id", ""), t.get("owner") or "待定", t.get("deliverable") or "待定",
            t.get("deadline") or "待定", t.get("dependencies") or "-", t.get("status", ""),
            t.get("source_ref", "") or "-"))
    lines.append("")
    lines.append("> 缺负责人/期限的任务保持草案(draft)；相对日期以纪要日期换算并保留原话；"
                 "任务完成≠续费，复访≠客户已接受预约。")
    md = _write(wd, plan_id, "md", "\n".join(lines) + "\n")

    header = ["task_id", "entity_id", "owner", "deliverable", "deadline", "dependencies",
              "completion_evidence", "next_trigger", "status", "source_ref", "mode"]
    rows = []
    for t in d.get("tasks", []):
        rows.append([t.get("task_id", ""), t.get("entity_id", ""), t.get("owner") or "",
                     t.get("deliverable") or "", t.get("deadline") or "",
                     t.get("dependencies") or "", t.get("completion_evidence") or "",
                     t.get("next_trigger") or "", t.get("status", ""), t.get("source_ref") or "",
                     t.get("mode") or mode])
    csv = _write(wd, plan_id, "csv", render_csv(rows, header))
    return {"md": _rel(wd, md), "csv": _rel(wd, csv)}


# ---- 工具 ------------------------------------------------------------------
def _v(x: Any) -> str:
    return "" if x is None else str(x)


def _compact(obj: Any) -> str:
    import json
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= 200 else s[:197] + "..."
