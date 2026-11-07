"""随包元数据结构测试：WorkBuddy SKILL.md frontmatter 必填 8 字段（P2.4）。

仅标准库；用轻量解析提取顶层 frontmatter 键（不依赖第三方 YAML）。
"""
from __future__ import annotations

import os
import re

from _harness import ROOT, run_tests

SKILL = os.path.join(ROOT, "SKILL.md")

# WorkBuddy 官方当前明确的 8 个必填字段
REQUIRED = ["name", "display_name", "display_name_en", "description",
            "description_zh", "description_en", "version", "author"]


def _frontmatter(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines and lines[0].strip() == "---", "SKILL.md 必须以 frontmatter 开头"
    keys = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip()
    return keys


def test_skill_frontmatter_has_eight_required_fields():
    keys = _frontmatter(SKILL)
    for k in REQUIRED:
        assert keys.get(k), "缺少必填 frontmatter 字段: %s" % k


def test_skill_version_is_current():
    keys = _frontmatter(SKILL)
    assert keys.get("version") == "0.4.2", keys.get("version")


def test_skill_author_not_claiming_official():
    keys = _frontmatter(SKILL)
    author = keys.get("author", "")
    # 作者字段不得伪称出题方/官方
    assert "非出题方" in author or "非官方" in author or "待正式发布" in author, author


if __name__ == "__main__":
    p, f, fails = run_tests(globals())
    print("test_meta: %d passed, %d failed %s" % (p, f, fails))
    raise SystemExit(1 if f else 0)
