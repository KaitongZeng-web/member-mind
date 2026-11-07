"""字段字典与表头映射：把用户表格的任意列名归一到规范字段。

不解析自然语言；只做列名→规范字段的别名匹配与冲突检测。
"""
from __future__ import annotations

from typing import Any, Optional

from ms_common import norm, is_blank

# 规范字段（数据模式）。条件树只能引用这些字段或实际导入出现过的自定义字段。
# 注意：会员编号(business_key)与统一社会信用代码(uscc)是不同命名空间，禁止跨类型混用（A3）。
CANONICAL_FIELDS = [
    "entity_id",
    "business_key",
    "uscc",
    "name",
    "industry",
    "region",
    "member_status",
    "renewal_status",
    "contactable_status",
    "joined_date",
    "last_activity_date",
    "qualification",
    "tags",
    "source",
]

# 每个规范字段的中英文别名（用于表头自动映射）。值为规范化后的别名。
# 注意：entity_id 是系统生成的稳定主体编号，不从任何源列映射；源表的编号列归一到 business_key。
# USCC（统一社会信用代码）是独立键类型，单独归一，绝不与会员编号混为同一字段（A3/A5）。
_ALIASES = {
    "entity_id": ["entity_id"],
    "business_key": ["business_key", "会员编号", "客户编号", "企业编号", "主体编号", "档案编号",
                     "编号", "member_id", "company_id", "customer_id", "id"],
    "uscc": ["uscc", "统一社会信用代码", "信用代码", "注册号", "统一社会信用代码/注册号",
             "营业执照号", "工商注册号"],
    "name": ["name", "企业名称", "公司名称", "客户名称", "会员名称", "单位名称", "名称",
             "company", "company_name", "customer", "customer_name"],
    "industry": ["industry", "行业", "所属行业", "行业分类", "领域", "产业"],
    "region": ["region", "地区", "区域", "省份", "所在地区", "城市", "所在地", "地域"],
    "member_status": ["member_status", "会员状态", "客户状态", "状态", "会员等级",
                      "等级", "level", "status", "membership"],
    "renewal_status": ["renewal_status", "续费状态", "续费", "是否续费", "renewal", "renewed"],
    "contactable_status": ["contactable_status", "可联系", "可触达", "触达状态", "联系状态",
                           "可联系状态", "是否可联系", "contactable", "reachable"],
    "joined_date": ["joined_date", "加入日期", "入会日期", "注册日期", "建档日期", "join_date"],
    "last_activity_date": ["last_activity_date", "最近活动", "最近联系", "上次活动",
                           "最后活动日期", "last_activity", "last_contact"],
    "qualification": ["qualification", "资质", "证书", "认证", "资质等级", "certificate"],
    "tags": ["tags", "标签", "标记", "标签集合"],
    "source": ["source", "来源", "数据来源"],
}

# 归一化别名 → 规范字段
_ALIAS_INDEX = {}
for _canon, _alist in _ALIASES.items():
    for _a in _alist:
        _ALIAS_INDEX.setdefault(norm(_a), _canon)


def alias_to_field(header: str) -> Optional[str]:
    """把表头归一到规范字段；无法识别返回 None。"""
    return _ALIAS_INDEX.get(norm(header))


def suggest_mapping(headers: list) -> dict:
    """根据表头列表建议 列名→规范字段 的映射。

    返回 {header: field_or_None, "__conflicts__": {field: [headers]}}。
    同一规范字段被多个不同列命中时记录冲突，交由调用方决定（不自动选）。
    """
    mapping = {}
    by_field = {}
    for h in headers:
        field = alias_to_field(h)
        mapping[h] = field
        if field:
            by_field.setdefault(field, []).append(h)
    conflicts = {f: hs for f, hs in by_field.items() if len(hs) > 1}
    return {"mapping": mapping, "conflicts": conflicts}


def is_known_field(field: str, extra_fields: Optional[set] = None) -> bool:
    if field in CANONICAL_FIELDS:
        return True
    if extra_fields and field in extra_fields:
        return True
    return False
