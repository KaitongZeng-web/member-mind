"""member-service 命令行入口。宿主（WorkBuddy）内部代调用，普通用户不直接使用。

统一约定：
- 成功：JSON 结果打印到 stdout，退出码 0。
- 失败：JSON 错误打印到 stderr，退出码见 ms_common（2 用法/3 找不到/4 解析/5 校验/6 IO）。
所有子命令共用 --workdir（默认 ./member-work）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 让脚本可直接以 `python3 scripts/ms_cli.py` 运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ms_common import MSError, dumps, EXIT_NOT_FOUND, EXIT_PARSE, EXIT_IO  # noqa: E402


def _load_json_arg(value: str):
    """支持内联 JSON 或 @文件路径；坏 JSON/缺文件按约定错误码返回，而非原生异常栈（A6）。"""
    if value.startswith("@"):
        path = value[1:]
        if not os.path.exists(path):
            raise MSError(EXIT_NOT_FOUND, "JSON 文件不存在: %s" % path, {"path": path})
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise MSError(EXIT_PARSE, "JSON 解析失败: %s: %s" % (path, e), {"path": path})
        except OSError as e:
            raise MSError(EXIT_IO, "读取 JSON 失败: %s: %s" % (path, e), {"path": path})
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise MSError(EXIT_PARSE, "内联 JSON 解析失败: %s" % e, {})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="member-service", description="会员激活与拜访助手（本地核心）")
    p.add_argument("--workdir", default=os.environ.get("MEMBER_WORKDIR", "./member-work"),
                   help="工作目录（存放 index.json 与结果）")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("import", help="导入会员 CSV/XLSX")
    sp.add_argument("--file", required=True)
    sp.add_argument("--sheet", default=None)
    sp.add_argument("--mapping", default=None, help="列名→规范字段 的 JSON（内联或 @文件）")
    sp.add_argument("--id-column", default=None, help="显式指定作为业务编号的列名")
    sp.add_argument("--mock", action="store_true",
                    help="声明本批为模拟(mock)数据：来源模式标记为 mock 并粘性继承到下游简报/纪要/计划/导出")

    sp = sub.add_parser("filter", help="按显式条件树筛选（条件由宿主从自然语言生成）")
    sp.add_argument("--request", required=True, help="筛选请求 JSON（内联或 @文件）")

    sp = sub.add_parser("brief", help="生成指定企业一页拜访简报")
    sp.add_argument("--entity", required=True, help="entity_id")
    sp.add_argument("--context", default=None, help="宿主生成的 goals/questions/suggestions/materials JSON（内联或 @文件）")
    sp.add_argument("--purpose", default=None, help="本次拜访目的（可选）")

    sp = sub.add_parser("minutes-extract", help="结构化原始纪要，生成待核对版")
    sp.add_argument("--raw", required=True, help="原始纪要文本文件")
    sp.add_argument("--extraction", required=True, help="宿主抽取的结构化 JSON（内联或 @文件）")

    sp = sub.add_parser("minutes-confirm", help="人员核对纪要，生成已核对版")
    sp.add_argument("--visit-id", required=True)
    sp.add_argument("--confirmations", required=True, help="确认决定 JSON（内联或 @文件）")

    sp = sub.add_parser("plan", help="由已核对纪要生成后续行动计划")
    sp.add_argument("--request", required=True, help="计划请求 JSON（内联或 @文件）")

    sp = sub.add_parser("task-update", help="更新任务状态（幂等，完成需证据）")
    sp.add_argument("--task-id", required=True)
    sp.add_argument("--request", required=True, help="含 status/completion_evidence/reason 的 JSON（内联或 @文件）")

    sp = sub.add_parser("mcp-search", help="整理外部企业检索意图并报告工具可用性（不执行真实调用）")
    sp.add_argument("--query", required=True, help="结构化查询 JSON（内联或 @文件）")
    sp.add_argument("--tools", default="[]", help="宿主实际发现的工具列表 JSON（内联或 @文件）")

    sp = sub.add_parser("mcp-ingest", help="归一化外部候选回包（mock/真实），候选≠已核实")
    sp.add_argument("--payload", required=True, help="候选回包 JSON（内联或 @文件）")

    sp = sub.add_parser("index", help="打印工作目录索引摘要")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            from ms_import import run_import
            mapping = _load_json_arg(args.mapping) if args.mapping else None
            env = run_import(args.file, args.workdir, sheet=args.sheet,
                             mapping=mapping, id_column=args.id_column,
                             is_mock=True if args.mock else None)
        elif args.command == "filter":
            from ms_filter import run_filter
            request = _load_json_arg(args.request)
            env = run_filter(request, args.workdir)
        elif args.command == "brief":
            from ms_brief import run_brief
            context = _load_json_arg(args.context) if args.context else None
            env = run_brief(args.entity, args.workdir, context=context, purpose=args.purpose)
        elif args.command == "minutes-extract":
            from ms_minutes import run_extract
            extraction = _load_json_arg(args.extraction)
            env = run_extract(args.raw, extraction, args.workdir)
        elif args.command == "minutes-confirm":
            from ms_minutes import run_confirm
            confirmations = _load_json_arg(args.confirmations)
            env = run_confirm(args.visit_id, confirmations, args.workdir)
        elif args.command == "plan":
            from ms_plan import run_plan
            request = _load_json_arg(args.request)
            env = run_plan(request, args.workdir)
        elif args.command == "task-update":
            from ms_plan import run_task_update
            request = _load_json_arg(args.request)
            env = run_task_update(args.task_id, request, args.workdir)
        elif args.command == "mcp-search":
            from ms_mcp import run_mcp_search
            query = _load_json_arg(args.query)
            tools = _load_json_arg(args.tools)
            env = run_mcp_search(query, tools, args.workdir)
        elif args.command == "mcp-ingest":
            from ms_mcp import run_mcp_ingest
            payload = _load_json_arg(args.payload)
            env = run_mcp_ingest(payload, args.workdir)
        elif args.command == "index":
            from ms_store import Workdir
            wd = Workdir(args.workdir)
            env = {
                "ok": True, "command": "index", "workdir": wd.path,
                "mode": "mock" if wd.data_is_mock() else "live",
                "data_version": wd.data_version,
                "data_is_mock": wd.data_is_mock(),
                "counts": {
                    "entities": len(wd.index["entities"]),
                    "results": len(wd.index["results"]),
                    "tasks": len(wd.index["tasks"]),
                    "visits": len(wd.index["visits"]),
                },
                "known_fields": wd.index.get("known_fields", []),
            }
        else:  # pragma: no cover
            parser.error("未知命令")
        sys.stdout.write(dumps(env) + "\n")
        return 0
    except MSError as e:
        sys.stderr.write(dumps(e.to_dict()) + "\n")
        return e.code
    except BrokenPipeError:  # pragma: no cover
        return 0
    except Exception as e:  # noqa: BLE001  # 兜底：结构化非零退出，避免原生异常栈（A6）
        err = MSError(1, "未预期的内部错误: %s" % e, {"type": type(e).__name__})
        sys.stderr.write(dumps(err.to_dict()) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
