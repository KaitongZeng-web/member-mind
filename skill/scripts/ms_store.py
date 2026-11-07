"""工作目录与索引：index.json 是唯一状态存储，支撑跨会话恢复、版本与幂等。

- entity 主体档案（稳定 entity_id、版本、字段来源、unknowns、history）
- identity_map：源行定位(row_key) → entity_id，保证重复导入幂等
- results / visits / tasks / change_events 登记
- data_version 全局数据版本；旧结果按 data_version 判定是否过期
不保存任何凭证/密钥。
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ms_common import (
    MSError, EXIT_IO, now_iso, norm, short_hash, is_blank,
    write_json_atomic, read_json, safe_join,
)

def _field_sources(content: dict, source: str, data_version: int) -> dict:
    return {f: {"source": source, "data_version": data_version} for f in content}


def _present(fields: dict) -> dict:
    """只保留有值的字段（含自定义字段）。"""
    return {k: v for k, v in fields.items() if not is_blank(v)}


def _content_hash(fields: dict) -> str:
    present = _present(fields)
    parts = ["%s=%s" % (f, norm(present[f])) for f in sorted(present)]
    return short_hash("|".join(parts), 16)


class Workdir:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.index_path = os.path.join(self.path, "index.json")
        self.readme_path = os.path.join(self.path, "README.md")
        self.results_dir = os.path.join(self.path, "results")
        self.index = self._load_or_init()

    # ---- 载入/初始化 -------------------------------------------------------
    def _load_or_init(self) -> dict:
        if os.path.exists(self.index_path):
            idx = read_json(self.index_path)
            if not isinstance(idx, dict):
                raise MSError(EXIT_IO, "index.json 结构损坏（非对象）", {"path": self.index_path})
            idx.setdefault("schema_version", "0.4.2")
            idx.setdefault("data_version", 0)
            idx.setdefault("known_fields", [])
            idx.setdefault("identity_map", {})
            idx.setdefault("entities", {})
            idx.setdefault("imports", [])
            idx.setdefault("results", {})
            idx.setdefault("visits", {})
            idx.setdefault("tasks", [])
            idx.setdefault("change_events", [])
            idx.setdefault("created_at", now_iso())
            return idx
        return {
            "schema_version": "0.4.2",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "data_version": 0,
            "known_fields": [],
            "identity_map": {},
            "entities": {},
            "imports": [],
            "results": {},
            "visits": {},
            "tasks": [],
            "change_events": [],
        }

    def save(self) -> None:
        self.index["updated_at"] = now_iso()
        write_json_atomic(self.index_path, self.index)

    # ---- 来源模式（mock / live）粘性 ---------------------------------------
    def data_provenance(self) -> dict:
        return self.index.setdefault("data_provenance", {"is_mock": False, "imports": []})

    def data_is_mock(self) -> bool:
        """工作区是否已混入模拟数据。

        粘性：任何一次导入显式声明 is_mock=true 后恒为 True，**不被后续非 mock
        导入降级**（保守方向）。模拟数据与真实数据建议用不同工作区。
        """
        return bool(self.index.get("data_provenance", {}).get("is_mock"))

    def record_import_mode(self, is_mock: Optional[bool], import_id: str, file_path: str) -> None:
        prov = self.data_provenance()
        prov.setdefault("imports", []).append({
            "import_id": import_id, "file": file_path,
            "is_mock": is_mock, "at": now_iso(),
        })
        if is_mock:
            prov["is_mock"] = True

    def mark_entity_mode(self, entity_id: str, is_mock: Optional[bool]) -> None:
        """实体级来源模式；同样粘性（已标 mock 的主体不被真实导入降级）。"""
        if not is_mock:
            return
        entity = self.index["entities"].get(entity_id)
        if entity is not None:
            entity["is_mock"] = True

    def entity_is_mock(self, entity_id: Optional[str]) -> bool:
        entity = self.index["entities"].get(entity_id) if entity_id else None
        return bool(entity and entity.get("is_mock"))

    def source_mode(self, entity_id: Optional[str] = None,
                    extra_mock: bool = False) -> str:
        """派生下游命令的 mode：来源为模拟数据即 mock，绝不被外层 false 降成 live。"""
        return "mock" if (extra_mock or self.entity_is_mock(entity_id)
                          or self.data_is_mock()) else "live"

    # ---- 版本 --------------------------------------------------------------
    @property
    def data_version(self) -> int:
        return int(self.index.get("data_version", 0))

    def bump_data_version(self) -> int:
        self.index["data_version"] = self.data_version + 1
        return self.data_version

    def result_path(self, result_id: str, ext: str) -> str:
        return safe_join(self.results_dir, "%s.%s" % (result_id, ext))

    # ---- 字段登记 ----------------------------------------------------------
    def register_fields(self, fields: list) -> None:
        known = set(self.index.get("known_fields", []))
        for f in fields:
            if f:
                known.add(f)
        self.index["known_fields"] = sorted(known)

    def known_field_set(self) -> set:
        return set(self.index.get("known_fields", []))

    # ---- 主体入库 ----------------------------------------------------------
    # 身份由调用方(import)解析后传入 identity_key：
    #   - 有显式业务编号：identity_key = "BK|"+编号（强键，稳定主体，允许更新）
    #   - 无编号：identity_key = "FPG|"+hash(指纹+内容)+"#"+同组出现序号（弱键，
    #     内容编码进键，内容变动即新主体，不推断身份稳定；重名按序号分开不合并）
    # 源行号只作为 field_sources 证据记录，绝不作为身份键（避免行重排错绑）。
    def upsert_row(self, source: str, fields: dict, unknowns: dict,
                   identity_key: str, entity_id: str, strong_key: bool,
                   is_duplicate: bool = False) -> dict:
        content = _present(fields)
        chash = _content_hash(fields)
        idmap = self.index["identity_map"]

        if identity_key in idmap:
            eid = idmap[identity_key]["entity_id"]
            entity = self.index["entities"].get(eid)
            if entity is not None and entity.get("content_hash") == chash:
                # 同主体同内容：幂等，不涨版本、不改来源（换文件/格式/行序均不变）
                return {"action": "unchanged", "entity_id": eid,
                        "version": entity.get("version", 1), "changed_fields": [], "duplicate": False}
            if entity is not None and strong_key:
                # 强键：显式编号证明同一主体，合法更新（保留旧值/新值快照，A5）
                old_fields = dict(entity.get("fields", {}))
                changed = self._diff_fields(old_fields, content)
                entity["version"] = int(entity.get("version", 1)) + 1
                entity["fields"] = content
                entity["unknowns"] = unknowns
                entity["content_hash"] = chash
                fs = dict(entity.get("field_sources", {}))
                fs.update(_field_sources({k: content.get(k) for k in changed}, source, self.data_version + 1))
                entity["field_sources"] = fs
                entity["updated_at"] = now_iso()
                entity.setdefault("history", []).append({
                    "version": entity["version"], "data_version": self.data_version + 1,
                    "changed_fields": changed, "at": now_iso(), "source": source,
                    "old_values": {k: old_fields.get(k) for k in changed},
                    "new_values": {k: content.get(k) for k in changed},
                })
                idmap[identity_key]["content_hash"] = chash
                self._record_change(eid, "updated", changed, source)
                self.bump_data_version()
                return {"action": "updated", "entity_id": eid, "version": entity["version"],
                        "changed_fields": changed, "duplicate": False}
            # 弱键但内容不一致：不推断身份稳定，落到下方按新主体建档（旧主体保留）

        return self._create_entity(source, fields, unknowns, chash,
                                   identity_key, entity_id, strong_key, is_duplicate)

    def _create_entity(self, source, fields, unknowns, chash,
                       identity_key, entity_id, strong_key, is_duplicate) -> dict:
        content = _present(fields)
        entity = {
            "entity_id": entity_id,
            "business_key": fields.get("business_key"),
            "version": 1,
            "fields": content,
            "field_sources": _field_sources(content, source, self.data_version + 1),
            "unknowns": unknowns,
            "content_hash": chash,
            "status": "suspected_duplicate" if is_duplicate else "active",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "history": [{"version": 1, "data_version": self.data_version + 1,
                         "changed_fields": sorted(content.keys()), "at": now_iso(), "source": source}],
        }
        self.index["entities"][entity_id] = entity
        self.index["identity_map"][identity_key] = {"entity_id": entity_id, "content_hash": chash}
        self._record_change(entity_id, "added", sorted(content.keys()), source)
        self.bump_data_version()
        return {"action": "added", "entity_id": entity_id, "version": 1,
                "changed_fields": sorted(content.keys()), "duplicate": is_duplicate}

    @staticmethod
    def _diff_fields(old: dict, new: dict) -> list:
        changed = []
        for k in set(old) | set(new):
            if norm(old.get(k)) != norm(new.get(k)):
                changed.append(k)
        return sorted(changed)

    def _record_change(self, entity_id: str, kind: str, changed_fields: list, source: str) -> None:
        self.index["change_events"].append({
            "event_id": "chg-" + short_hash(entity_id + kind + now_iso(), 10),
            "entity_id": entity_id,
            "kind": kind,
            "changed_fields": changed_fields,
            "source": source,
            "data_version": self.data_version,
            "at": now_iso(),
            "operator": "import",
        })

    # ---- 查询 --------------------------------------------------------------
    def get_entity(self, entity_id: str) -> Optional[dict]:
        return self.index["entities"].get(entity_id)

    def all_entities(self) -> dict:
        return self.index["entities"]

    # ---- 结果登记 ----------------------------------------------------------
    def register_result(self, result_id: str, meta: dict) -> None:
        meta = dict(meta)
        meta["created_at"] = now_iso()
        meta["data_version"] = self.data_version
        self.index["results"][result_id] = meta

    def is_result_stale(self, result_id: str) -> bool:
        r = self.index["results"].get(result_id)
        if not r:
            return False
        rv = r.get("data_version")
        return rv is not None and rv < self.data_version

    # ---- 任务存储（稳定 business_key → 幂等/更新，不因负责人/期限变化而新建）---
    def task_business_key(self, entity_id: str, visit_id: Optional[str], signature: str) -> str:
        return short_hash("|".join([entity_id or "", visit_id or "", signature]), 16)

    # 任务里可在重复计划时更新的可变字段（负责人/期限等不是身份键，A4）
    _TASK_MUTABLE = ("owner", "deadline", "deadline_original", "deadline_basis",
                     "dependencies", "deliverable", "channel", "next_trigger",
                     "source_ref", "contact_restricted_scopes")
    _TASK_TERMINAL = ("completed", "cancelled")

    def upsert_task(self, task: dict) -> tuple:
        """按 business_key 幂等写入任务；已存在只返回，不覆盖人工修改。

        - 保留完成证据 completion_evidence 与历史（不抹掉，A4）。
        - 终态(completed/cancelled)不被重复计划降级。
        - 返回 (task, created: bool)。
        """
        bk = task["business_key"]
        for existing in self.index["tasks"]:
            if existing.get("business_key") == bk:
                # 计划创建/重放只复用任务；明确字段变更统一走task-update。
                return existing, False
        self.index["tasks"].append(task)
        return task, True

    def task_by_id(self, task_id: str) -> Optional[dict]:
        for t in self.index["tasks"]:
            if t.get("task_id") == task_id:
                return t
        return None

    # ---- README -----------------------------------------------------------
    def render_readme(self) -> str:
        idx = self.index
        lines = []
        lines.append("# 会员服务工作目录索引")
        lines.append("")
        lines.append("- 生成/更新：%s" % idx.get("updated_at", ""))
        lines.append("- 数据版本 data_version：**%d**" % self.data_version)
        if self.data_is_mock():
            lines.append("- 来源模式：**含模拟(mock)数据**（至少一次导入显式声明 is_mock=true）；"
                         "此后简报/纪要/计划/导出均标 mock，**不代表真实工商信息，不作为机构政策**")
        lines.append("- 主体档案数：%d｜结果数：%d｜任务数：%d｜拜访记录数：%d"
                     % (len(idx["entities"]), len(idx["results"]), len(idx["tasks"]), len(idx["visits"])))
        lines.append("")
        lines.append("> 本文件与 index.json 由脚本维护，可在新会话中恢复工作，不依赖聊天记录，不存凭证。")
        lines.append("")
        if idx["entities"]:
            lines.append("## 主体档案")
            lines.append("")
            lines.append("| entity_id | 名称 | 行业 | 地区 | 会员状态 | 续费 | 可联系 | 版本 | 标记 |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for eid, e in sorted(idx["entities"].items()):
                f = e.get("fields", {})
                flag = "冲突隔离（旧字段仅作历史）" if e.get("status") == "conflicted" else ("疑似重名待确认" if e.get("status") == "suspected_duplicate" else "")
                lines.append("| %s | %s | %s | %s | %s | %s | %s | v%d | %s |" % (
                    eid, f.get("name", ""), f.get("industry", "") or "未知",
                    f.get("region", "") or "未知", f.get("member_status", "") or "未知",
                    f.get("renewal_status", "") or "未知", f.get("contactable_status", "") or "未知",
                    e.get("version", 1), flag))
            lines.append("")
        if idx["results"]:
            lines.append("## 结果（含是否因数据更新而过期）")
            lines.append("")
            lines.append("| result_id | 类型 | 模式 | data_version | 状态 | 过期 | 文件 |")
            lines.append("|---|---|---|---|---|---|---|")
            for rid, r in sorted(idx["results"].items()):
                stale = "是（数据已变）" if self.is_result_stale(rid) else "否"
                lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                    rid, r.get("type", ""), r.get("mode", "") or "-", r.get("data_version", ""),
                    r.get("status", ""), stale, r.get("path", "")))
            lines.append("")
        if idx["tasks"]:
            lines.append("## 后续任务")
            lines.append("")
            lines.append("| task_id | entity_id | 负责人 | 交付物 | 截止 | 状态 |")
            lines.append("|---|---|---|---|---|---|")
            for t in idx["tasks"]:
                lines.append("| %s | %s | %s | %s | %s | %s |" % (
                    t.get("task_id", ""), t.get("entity_id", ""),
                    t.get("owner", "") or "待定", t.get("deliverable", "") or "待定",
                    t.get("deadline", "") or "待定", t.get("status", "")))
            lines.append("")
        return "\n".join(lines) + "\n"

    def write_readme(self) -> None:
        from ms_common import write_text_atomic
        write_text_atomic(self.readme_path, self.render_readme())

