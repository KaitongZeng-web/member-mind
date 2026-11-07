"""ms_common 基础设施测试：CSV 防注入、路径安全、原子写、规范化。"""
from __future__ import annotations

import os

from _harness import fresh_workdir, run_tests

from ms_common import (
    csv_safe, render_csv, safe_join, norm, is_blank, as_text,
    write_json_atomic, read_json, write_text_atomic, read_text,
    make_envelope, MSError, EXIT_VALIDATION, SCHEMA_VERSION,
)


def test_csv_safe_neutralizes_formula_prefixes():
    assert csv_safe("=1+1") == "'=1+1"
    assert csv_safe("+cmd") == "'+cmd"
    assert csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert csv_safe("\tTab") == "'\tTab"
    # 负的纯数字不处理（避免误伤合法负数）
    assert csv_safe("-12.5") == "-12.5"
    # 负号后非数字则中和
    assert csv_safe("-2+3") == "'-2+3"
    # 普通文本不动
    assert csv_safe("深圳精密") == "深圳精密"
    assert csv_safe("") == ""
    assert csv_safe(None) == ""


def test_render_csv_escapes_all_cells():
    text = render_csv([["=evil", "ok"], ["+bad", "fine"]], header=["a", "b"])
    assert "'=evil" in text
    assert "'+bad" in text
    assert "a,b" in text


def test_safe_join_rejects_escape():
    base = fresh_workdir()
    assert safe_join(base, "sub", "f.json").startswith(os.path.abspath(base))
    for bad in ["../escape", "/etc/passwd", "a/../../b"]:
        try:
            safe_join(base, bad)
            raise AssertionError("应拒绝越界路径: %r" % bad)
        except MSError as e:
            assert e.code == EXIT_VALIDATION


def test_norm_and_blank():
    assert norm("  Hello   World ") == "hello world"
    assert norm(None) == ""
    assert is_blank("  ") is True
    assert is_blank("x") is False
    assert as_text(3.0) == "3"
    assert as_text(None) == ""


def test_atomic_roundtrip():
    wd = fresh_workdir()
    p = os.path.join(wd, "x.json")
    write_json_atomic(p, {"b": 1, "a": 2})
    assert read_json(p) == {"a": 2, "b": 1}
    tp = os.path.join(wd, "t.txt")
    write_text_atomic(tp, "内容\n")
    assert read_text(tp) == "内容\n"


def test_envelope_shape():
    env = make_envelope(command="import", mode="live", status="succeeded",
                        items=[1], extra={"k": "v"})
    for key in ("schema_version", "request_id", "command", "ok", "mode",
                "as_of", "data_version", "rule_version", "status",
                "items", "issues", "evidence_refs", "next_actions", "k"):
        assert key in env, key
    assert env["schema_version"] == SCHEMA_VERSION
    assert env["ok"] is True


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_common: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
