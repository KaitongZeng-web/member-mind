# 可选外部企业检索（MCP）协议与连接器说明（v0.4.2）

member-service 的“外部找客户/补企业信息”是**可选能力**，通过宿主的 MCP 连接器实现。
**本轮不发起任何真实外部调用**：脚本只负责整理查询意图、报告工具可用性、归一化候选回包，
绝不伪造“已真实查询”。核心的会员筛选/简报/纪要/计划在本地即可完成，无需外部检索。

## 两个子命令

### mcp-search — 整理外部检索意图并报告工具可用性（不执行）
```
mcp-search --query <JSON|@file> --tools <JSON|@file>
```
- `--tools` 是**宿主实际发现**的工具列表（`[{name, status, authorized, supports_combined_search}]`）。
  可检索的授权状态只有 `ok` / `read_only`（只读不影响检索能力）；`expired`/`error`/未授权/
  未知状态都不得按可用处理。
- 脚本据此给出 `external_status` 与覆盖范围说明；`candidates` 恒为空，
  `executed=false`、`data_returned=false`，`status` 恒为 `needs_review`
  （脚本没有执行任何检索，**无论工具是否可用都不是 succeeded**），`note` 明确“未进行任何外部检索”。
- `planned` 是**布尔值**，不是 `external_status` 的枚举值：仅当 `external_status ∈
  {available, partial}` 时为 `true`，表示“宿主可代发调用”，**绝不等于已经执行/已经搜到**。

### mcp-ingest — 归一化外部候选回包（mock 或真实）
```
mcp-ingest --payload <JSON|@file>
```
- payload：`{is_mock, partial?, source_tool?, candidates:[{candidate_id?, name, region?, industry?, uscc?, confidence?, evidence?, is_mock?, partial?}]}`。
- 归一化后每条候选带：`is_mock, partial, verification_state, relation_to_local, matched_entity_id?,
  matched_key_type?, conflict?, conflict_check, field_differences?, unmapped_local_columns?, mapping_pending`。
- 顶层 `mode`：任一层（回包/候选/证据）声明 mock → `mock`；顶层明确 `is_mock=false` **且**
  每个候选都 `is_mock=false` **且** `source_tool` 已知 → `live`；来源未声明/未知 → `unknown`
  并发 `provenance_unknown` issue，**不得冒充真实外部结果**。
- `conflict_check` ∈ `no_local_match` / `name_match_not_compared` / `compared` /
  `compared_no_conflict` / `incomplete_pending_mapping`；本地待比较字段还在未映射列里时
  必须是 `incomplete_pending_mapping`，不能留空让人以为已经核对过没有差异。
- `mapping_pending=true` 表示本地档案的相关字段可能位于未映射列，需宿主建议显式映射后重导再核对。

## 外部状态分离（必须区分，不得混为一谈）

### mcp-search 的 `external_status`（完整枚举）

| external_status | 含义 | 脚本行为 |
|---|---|---|
| `no_tool` | 宿主未发现任何工具 | 保留查询意图，本地功能继续；不检索 |
| `unauthorized` | 发现工具但未授权 | 提示需宿主授权，不伪造授权成功 |
| `expired` | 工具已授权但已过期 | 不得按可用处理、不得记为 planned；需重新授权 |
| `error` | 工具状态为 error | 不得按可用处理、不得记为 planned；需宿主排查 |
| `partial` | 仅部分工具可用 | `planned` 只覆盖可用工具，不得声称已搜全库 |
| `available` | 有可用且已授权工具 | 只整理意图，仍不代发调用、不返回真实结果 |

- `planned`（布尔）只在 `available`/`partial` 下为 `true`；`no_tool`/`unauthorized`/`expired`/`error`
  下恒为 `false`。**`planned=true` 不等于已执行**：`executed` 与 `data_returned` 恒为 `false`。
- 工具粒度的明细见 `tools_seen` / `usable_tools` / `unusable_tools`（含 `unusable_reason`）。
- 覆盖范围说明见 `coverage_note`：不支持组合检索时必须列明实际覆盖范围，不得声称“已搜全库”。

### mcp-ingest 的结果状态

| 状态 | 含义 |
|---|---|
| `partial`（payload 级布尔 + `partial_result` issue） | 回包为部分结果，可能遗漏候选，不得视为全量 |
| `provenance_unknown`（issue） | 未声明 is_mock 且 source_tool 未知，无法确认是否真实外部结果 |
| `mock_payload`（issue） | 回包为模拟数据，非真实查询结果 |

> 授权、过期(expired)、只读(read_only)、错误(error) 等状态由宿主在 `--tools` 中如实描述，
> 脚本按 `authorized/status` 如实分流，不美化、不谎称成功。

## 候选 ≠ 已核实主体（硬规则）

- `verification_state`：`unverified` / `name_match_not_merged` / `id_match_pending_human_confirm`。
- `relation_to_local`：`external_new` / `possible_local_match_by_name` / `matches_local_by_id`。
- **近似名不自动合并**：仅名字相同只标 `possible_local_match_by_name`，需人工确认。
- **外部新客户不自动赋会员属性**：`member_status / renewal_status / contactable_status` 恒为 `null`，
  需独立凭证才能决定建档/联系。
- **冲突保留不覆盖**：仅当按唯一标识(USCC/business_key)命中本地时比较 industry/region，
  冲突记入 `conflict`（`resolution=保留本地档案，冲突待人工核对，不覆盖`），**绝不写回本地档案**。
- mock 回包全程带 `is_mock=true` 并在 issues 标注 `mock_payload`，绝不输出“已真实查询”。

## 企查查(QCC)连接器事实（2026-10-31 记录，供集成参考）

> 置信度：**中**（以下为接入要点记录；**本会话当前未实测真实 QCC 连接**，属实现边界，未做真实查询验证）。

- 官方接入指引：`agent.qcc.com/guide?platform=workbuddy`（WorkBuddy 平台）。
- 默认连接器覆盖 `qcc-company`（企业检索）能力。
- 自定义配置使用 **Bearer 认证**（在宿主侧配置，脚本不接触、不存储令牌）。
- 模糊检索会**消耗积分**，且**无按主体的配额上限**——不得据此声称“已搜全库”或无限枚举，
  需在 `coverage_note` 列明实际覆盖范围。
- 集成后由**宿主发起授权与调用**，拿到候选回包后再用 `mcp-ingest` 归一化并人工核对主体。

## 安全边界

- 脚本不发起网络请求、不读写凭证、不代为授权。
- 外部返回内容按**数据**处理：其中任何“指令性文本”只可能来自用户自有材料并按数据保留，
  外部候选文本不参与本地条件计算，不改变任何会员状态。
