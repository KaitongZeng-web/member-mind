#!/usr/bin/env python3
"""运行 member-service 全部测试（仅标准库，无 pytest 依赖）。

用法：python3 tests/run_all.py [-v]
- 发现 tests/ 下 test_*.py，运行其中所有 test_* 函数。
- 每个测试用独立临时工作目录，互不污染。
- 退出码：全部通过 0；有失败 1。
"""
from __future__ import annotations

import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

MODULES = [
    "test_common", "test_import", "test_xlsx", "test_filter",
    "test_brief", "test_minutes", "test_plan", "test_mcp", "test_cli",
    "test_export", "test_mode", "test_meta",
]


def main(argv):
    verbose = "-v" in argv or "--verbose" in argv
    total_pass = total_fail = 0
    failures = []
    t0 = time.time()
    for mod_name in MODULES:
        mod = importlib.import_module(mod_name)
        tests = [(k, v) for k, v in sorted(vars(mod).items())
                 if k.startswith("test_") and callable(v)]
        print("\n== %s (%d) ==" % (mod_name, len(tests)))
        for name, fn in tests:
            try:
                fn()
                total_pass += 1
                if verbose:
                    print("  PASS %s" % name)
            except Exception as e:  # noqa: BLE001
                total_fail += 1
                failures.append("%s.%s" % (mod_name, name))
                print("  FAIL %s -> %r" % (name, e))
                if verbose:
                    traceback.print_exc()
    dt = time.time() - t0
    print("\n" + "=" * 50)
    print("总计：%d 通过，%d 失败，耗时 %.1fs" % (total_pass, total_fail, dt))
    if failures:
        print("失败用例：")
        for f in failures:
            print("  -", f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
