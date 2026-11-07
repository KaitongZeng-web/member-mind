"""共享基础设施：错误类型、退出码、JSON/文件安全 IO、ID、时间、CSV 防注入。

仅用 Python 3.9 标准库。所有脚本模块共用本文件。
"""
from __future__ import annotations

import csv as _csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import tempfile
import threading
from typing import Any, Optional

SCHEMA_VERSION = "0.4.2"

# ---- 退出码（CLI 契约）------------------------------------------------------
EXIT_OK = 0            # 成功
EXIT_USAGE = 2         # 参数/用法错误
EXIT_NOT_FOUND = 3     # 输入文件不存在或不可读
EXIT_PARSE = 4         # 输入解析失败（损坏的 CSV/XLSX/JSON/文本）
EXIT_VALIDATION = 5    # 校验失败（无效条件、未知字段、非法日期、路径越界等）
EXIT_IO = 6            # 写入/读取 IO 失败

EXIT_NAMES = {
    EXIT_OK: "ok",
    EXIT_USAGE: "usage_error",
    EXIT_NOT_FOUND: "not_found",
    EXIT_PARSE: "parse_error",
    EXIT_VALIDATION: "validation_error",
    EXIT_IO: "io_error",
}


class MSError(Exception):
    """带退出码与结构化细节的错误。CLI 顶层捕获后转成 JSON 错误并返回退出码。"""

    def __init__(self, code: int, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "name": EXIT_NAMES.get(self.code, "error"),
                "message": self.message,
                "details": self.details,
            },
        }


# ---- 时间 -------------------------------------------------------------------
def now_iso() -> str:
    """本地时区的 ISO 8601（秒精度）。"""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today_date() -> str:
    return _dt.date.today().isoformat()


# ---- 哈希 / ID --------------------------------------------------------------
def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def short_hash(s: str, n: int = 12) -> str:
    return sha1_hex(s)[:n]


def new_request_id() -> str:
    return "req-" + short_hash(now_iso() + os.urandom(8).hex(), 10)


# ---- ID 生成（A2：碰撞安全 vs 内容派生）--------------------------------------
_id_counter = 0
_id_lock = threading.Lock()


def gen_id(prefix: str, *parts: str) -> str:
    """碰撞安全 ID：秒级时间 + 进程内单调计数 + 随机熵。

    用于每次“新操作/新成果”（plan/filter/brief/mcp 结果、缺省条目 ID 等），
    避免同秒多次调用产生同一 ID 而互相覆盖。
    """
    global _id_counter
    with _id_lock:
        _id_counter += 1
        seq = _id_counter
    entropy = "%s|%d|%s|%s" % (
        now_iso(), seq, os.urandom(8).hex(), "|".join(str(p) for p in parts))
    return "%s-%s" % (prefix, short_hash(entropy, 12))


def stable_id(prefix: str, *parts: str) -> str:
    """内容派生 ID：相同 parts 得到相同 ID（确定性）。

    用于“相同输入重放返回同一版本”的幂等场景（如纪要抽取 visit_id）：
    内容改变即得到不同 ID，从而生成新版本、不覆盖旧版本。
    """
    return "%s-%s" % (prefix, short_hash("|".join("" if p is None else str(p) for p in parts), 12))


def check_no_unknown_keys(obj: dict, allowed: set, ctx: str = "") -> None:
    """未知请求字段必须报错（A4/A6）：不静默忽略、不返回成功却不改。"""
    if not isinstance(obj, dict):
        raise MSError(EXIT_VALIDATION, "%s必须是对象" % (ctx or "请求"),
                      {"got_type": type(obj).__name__})
    unknown = sorted(k for k in obj.keys() if k not in allowed)
    if unknown:
        raise MSError(EXIT_VALIDATION,
                      "%s含未知字段: %s" % (ctx or "请求", ", ".join(unknown)),
                      {"unknown_fields": unknown, "allowed": sorted(allowed)})


# ---- 字符串规范化 -----------------------------------------------------------
_WS = re.compile(r"\s+")


def norm(s: Any) -> str:
    """身份比较用规范化：去首尾、压缩内部空白、忽略大小写。"""
    if s is None:
        return ""
    return _WS.sub(" ", str(s)).strip().casefold()


def is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def as_text(v: Any) -> str:
    """把任意单元格值转成去首尾空白的字符串（数字保留原样）。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# ---- JSON / 文件 IO（原子写 + 路径安全）------------------------------------
def write_json_atomic(path: str, obj: Any) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str) -> Any:
    if not os.path.exists(path):
        raise MSError(EXIT_NOT_FOUND, "文件不存在: %s" % path, {"path": path})
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise MSError(EXIT_PARSE, "JSON 解析失败: %s: %s" % (path, e), {"path": path})
    except OSError as e:
        raise MSError(EXIT_IO, "读取失败: %s: %s" % (path, e), {"path": path})


def read_text(path: str) -> str:
    if not os.path.exists(path):
        raise MSError(EXIT_NOT_FOUND, "文件不存在: %s" % path, {"path": path})
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        # 尝试 GBK 兜底（常见于 Windows 导出的 CSV）
        try:
            with open(path, "r", encoding="gbk") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            raise MSError(EXIT_PARSE, "文本解码失败: %s: %s" % (path, e), {"path": path})
    except OSError as e:
        raise MSError(EXIT_IO, "读取失败: %s: %s" % (path, e), {"path": path})


def write_text_atomic(path: str, text: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_join(base: str, *parts: str) -> str:
    """限制在 base 目录内，拒绝 .. 与绝对路径逃逸。"""
    base_abs = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base_abs, *parts))
    if target != base_abs and not target.startswith(base_abs + os.sep):
        raise MSError(EXIT_VALIDATION, "路径越界，已拒绝", {"base": base, "parts": list(parts)})
    return target


# ---- CSV 公式注入防护（导出侧）---------------------------------------------
_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def csv_safe(v: Any) -> str:
    """中和 CSV 公式注入：以 = + @ TAB CR 开头的前缀单引号；负号后非纯数字才处理。"""
    if v is None:
        return ""
    s = str(v)
    if not s:
        return s
    c = s[0]
    if c in ("=", "+", "@", "\t", "\r"):
        return "'" + s
    if c == "-":
        return s if _NUM_RE.match(s) else "'" + s
    return s


def render_csv(rows: list, header: Optional[list] = None) -> str:
    """把二维数据渲染为 CSV 文本，所有单元格过公式注入防护。"""
    buf = io.StringIO()
    w = _csv.writer(buf, quoting=_csv.QUOTE_MINIMAL, lineterminator="\n")
    if header is not None:
        w.writerow([csv_safe(h) for h in header])
    for row in rows:
        w.writerow([csv_safe(c) for c in row])
    return buf.getvalue()


# ---- 标准返回信封 -----------------------------------------------------------
def make_envelope(
    *,
    command: str,
    mode: str = "live",
    data_version: Optional[int] = None,
    rule_version: Optional[str] = None,
    status: str = "succeeded",
    items: Optional[list] = None,
    issues: Optional[list] = None,
    evidence_refs: Optional[list] = None,
    next_actions: Optional[list] = None,
    extra: Optional[dict] = None,
) -> dict:
    env = {
        "schema_version": SCHEMA_VERSION,
        "request_id": new_request_id(),
        "command": command,
        "ok": True,
        "mode": mode,
        "as_of": now_iso(),
        "data_version": data_version,
        "rule_version": rule_version,
        "status": status,
        "items": items if items is not None else [],
        "issues": issues if issues is not None else [],
        "evidence_refs": evidence_refs if evidence_refs is not None else [],
        "next_actions": next_actions if next_actions is not None else [],
    }
    if extra:
        env.update(extra)
    return env


def dumps(env: dict) -> str:
    return json.dumps(env, ensure_ascii=False, indent=2)
