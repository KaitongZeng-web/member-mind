"""测试公共helper：路径、临时工作目录、CLI 子进程调用、用例发现与运行。

仅用标准库。每个测试函数用独立临时工作目录，互不污染。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
DATA = os.path.join(ROOT, "data")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
# 让测试能 import data/make_sample_xlsx.py
if DATA not in sys.path:
    sys.path.insert(0, DATA)

CSV = os.path.join(DATA, "members_sample.csv")
XLSX = os.path.join(DATA, "members_sample.xlsx")
MINUTES_RAW = os.path.join(DATA, "minutes_raw.txt")


def fresh_workdir() -> str:
    return tempfile.mkdtemp(prefix="ms-test-")


def cli(args, workdir):
    """真正以子进程调用 scripts/ms_cli.py，返回 CompletedProcess。"""
    cmd = [sys.executable, os.path.join(SCRIPTS, "ms_cli.py"), "--workdir", workdir] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def cli_json(args, workdir):
    """调用 CLI 并解析 stdout 为 JSON（断言退出码为 0）。"""
    r = cli(args, workdir)
    assert r.returncode == 0, "CLI 失败 rc=%s stderr=%s" % (r.returncode, r.stderr)
    return json.loads(r.stdout)


def load_sample(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
        return json.load(f)


def run_tests(ns) -> tuple:
    """运行模块命名空间内所有 test_* 函数，返回 (passed, failed, names)。"""
    tests = [(k, v) for k, v in sorted(ns.items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    fails = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            fails.append(name)
            print("  FAIL %s -> %r" % (name, e))
            traceback.print_exc()
    return passed, failed, fails
