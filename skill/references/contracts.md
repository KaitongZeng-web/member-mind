# 命令契约与数据模式（v0.4.2）

本文件是 member-service 各子命令的输入/输出契约与数据模式，供宿主（WorkBuddy）
集成时对照。**普通用户不直接使用本文件**，也不直接调用脚本；脚本由宿主在内部代调用。

- 运行环境：Python 3.9+，**仅标准库**，无第三方依赖。
- 入口：`python3 scripts/ms_cli.py --workdir <目录> <子命令> ...`
- `--workdir` 默认取环境变量 `MEMBER_WORKDIR`，否则 `./member-work`。

## 统一返回信封

所有子命令成功时向 stdout 打印一个 JSON 信封（`make_envelope`）：

```
{
  "schema_version": "0.4.2",
  "request_id": "req-...",
  "command": "import|filter|brief|minutes-extract|minutes-confirm|plan|task-update|mcp-search|mcp-ingest|index",
  "ok": true,
  "mode": "live|mock|unknown",
  "as_of": "ISO8601",
  "data_version": 12,
  "rule_version": "demo-1.0.0" | null,
  "status": "succeeded|needs_review",
  "items": [...],
  "issues": [...],
  "evidence_refs": [...],
  "next_actions": [...],
  ...  // 各命令的 extra 字段被平铺进顶层（无嵌套 extra）
}
```

注意：各命令的 `extra` 字段被**平铺**到信封顶层（例如 `visit_id`、`counts`、
`external_status` 直接在顶层，不在 `env["extra"]` 下）。

### mode 的语义（来源模式，非真实性声明）

- `mock`：本步骤所依据的数据/规则是**模拟/演示数据**（导入声明 `is_mock=true`、演示规则
  `rule_is_mock=true`、mock 回包等）。
- `live`：按真实本地文件/本地计算处理（**未声明** mock 的真实文件也落这里）。
- `unknown`：来源无法确认（目前仅 `mcp-ingest` 在回包未声明且 `source_tool` 未知时使用）。
- **`mode` 只表示资料来源，不代表已经核实工商真实性。**
- **继承与粘性**：`brief`/`minutes-extract`/`plan`/`task-update`/`filter`/`index` 的 mode 由上游
  （导入声明 → 主体/工作区 → 纪要 → 任务）继承；一旦工作区混入 mock 导入即恒为 mock，
  **不被外层 `is_mock:false` 或“本次没写 is_mock”降级回 live**。模拟与真实数据建议用不同
  `--workdir`（本实现不做混合数据库判定）。

失败时向 stderr 打印 `{"ok": false, "error": {code, name, message, details}}`。

## 退出码

| code | 名称 | 含义 |
|---|---|---|
| 0 | ok | 成功 |
| 2 | usage_error | 参数/用法错误（argparse） |
| 3 | not_found | 输入文件不存在/不可读，或 entity/visit/task 不存在 |
| 4 | parse_error | 输入解析失败（损坏的 CSV/XLSX/JSON/文本） |
| 5 | validation_error | 校验失败（无效条件、未知字段、非法日期、路径越界等） |
| 6 | io_error | 读写 IO 失败 |

## 规范字段字典（CANONICAL_FIELDS）

条件树与简报只能引用这些字段，或导入时实际出现过的自定义字段。

| 字段 | 含义 | 常见表头别名（部分） |
|---|---|---|
| entity_id | 系统生成的稳定主体编号（**不从源列映射**） | entity_id |
| business_key | 业务编号（强身份键，**与 uscc 分开**） | 会员编号/客户编号/企业编号/主体编号/档案编号/编号/member_id/company_id/customer_id/id 等 |
| uscc | 统一社会信用代码（独立字段） | uscc/统一社会信用代码/信用代码/注册号/营业执照号/工商注册号 |
| name | 企业名称 | 企业名称/公司名称/客户名称/单位名称/名称/company 等 |
| industry | 所属行业 | 行业/所属行业/行业分类/领域/产业 |
| region | 所在地区 | 地区/区域/省份/所在地区/城市/所在地 |
| member_status | 会员状态/等级 | 会员状态/客户状态/状态/等级/level/status |
| renewal_status | 续费状态 | 续费状态/续费/是否续费/renewal/renewed |
| contactable_status | 可联系状态 | 可联系/可触达/联系状态/是否可联系/contactable |
| joined_date | 加入日期 | 加入日期/入会日期/注册日期/建档日期/join_date |
| last_activity_date | 最近活动 | 最近活动/最近联系/上次活动/last_contact |
| qualification | 资质/证书 | 资质/证书/认证/资质等级/certificate |
| tags | 标签 | 标签/标记 |
| source | 来源 | 来源/数据来源 |

- 无法归一的源列 → 记为 `custom:<列名>` 原样保留（**不丢弃**）。
- 多个不同列命中同一规范字段且未显式解决 → 该字段本次判为未知并记 issue。

## 主体身份模型（import 的核心）

- **强身份键**：行有 `business_key` → `entity_id = "E-"+hash("ID|"+编号)`，
  跨文件/格式/行序稳定；同编号内容变化→更新主体并涨版本、记变更事件（保留 old_values/new_values 快照）。
- **business_key 与 uscc 分开（A3）**：身份只用显式业务编号 `business_key`；`uscc`(统一社会信用代码)
  是独立字段，**不充当身份键**，避免“以信用代码当会员编号”误绑到错误主体。
- **弱身份键**：无编号行 → 按“指纹(名称+地区+行业)+内容哈希+同组出现序号”建档；
  内容编码进身份键，**内容变动即新主体，不推断身份稳定**；同名同内容的多行按序号
  分开（`E-xxx`、`E-xxx-1`…），**绝不合并**，全部标记 `suspected_duplicate` 待人工确认。
- 源行号**只作证据**（`field_sources`），**绝不作为身份键**（避免行重排错绑）。
- 幂等：同身份键 + 同内容哈希 → `unchanged`，不涨版本、不改来源。
- `data_version` 全局数据版本，仅在主体新增/更新时 +1。

## 条件树与三值逻辑（filter）

- 条件由**宿主从自然语言生成**后传入，脚本只做可复核计算，**不含关键词→字段的 NL 解析**。
- 逻辑节点：`{"logic":"and|or|not","conditions":[...]}`；`not` 只能有一个子条件。
- 叶节点：`{"field":F,"op":OP,"value":V}`。
- 运算符 `op`：`eq ne in not_in contains gte lte gt lt exists not_exists`。
- 三值：TRUE / FALSE / UNKNOWN。
  - 字段缺失记 UNKNOWN；`NOT(UNKNOWN)=UNKNOWN`（**缺失不会因 NOT 变真**）。
  - `exists/not_exists` 反映“已知有无”：缺失字段 `exists=FALSE`（非 UNKNOWN）。
- 决策优先级：先算 `hard_exclusions`（多个条件树按 OR 合并）为 TRUE→`excluded`；
  再算 `conditions`：FALSE→`excluded`；hard 或 filt 为 UNKNOWN→`needs_review`；
  否则 `eligible`。
- `rule_is_mock:true` → `mode="mock"`，结论仅用于体验，不作为机构政策。
- eligible 内排序仅为运营处理顺序建议，**不代表续费意愿/成交概率**。

### 排除/待核实的解释口径（MD/CSV 与 JSON 同源）

每个 `excluded` / `needs_review` 项都带：

- `reason`：分类标签（如 `不满足筛选条件` / `命中硬排除规则` / `字段未知待核实`），
  只表示**类别**，不描述具体是哪一支成立。
- `reason_detail`：**人读的决定性分支文本**，由同一份计算直接生成，带 `NOT(...)`/`(A or B)`
  语义与实际值，例如 `不满足筛选条件：not(custom:主营行业 eq 贸易（实际：贸易）)`。
  **MD 与 CSV 一律渲染这一字段**，绝不把 NOT 内部为 TRUE 的叶子写成“未满足: <条件>”。
- `matched` / `failed_conditions`：决定性叶子列表（`actual`/`result`/`condition_path`）。
- `condition_tree`：带节点 TRUE/FALSE/UNKNOWN 结果的三值解释树（needs_review 为
  `{"conditions":…, "hard_exclusions":…}` 两份的合并视图）。
- 硬排除的 `reason_detail` 形如 `命中硬排除规则：<实际成立的分支与实际值>`。

## 各子命令

### import — 导入会员 CSV/XLSX
```
import --file <path> [--sheet <名>] [--mapping <JSON|@file>] [--id-column <列名>] [--mock]
```
- 输入：CSV 或 XLSX；XLSX 用标准库读取（多 sheet/公式缓存值/合并单元格/错误值均标注边界）。
- 表头取第一个非空行；`--mapping` 可覆盖自动映射；`--id-column` 显式指定编号列。
- **来源模式声明**：`--mock`（CLI）等价于导入参数 `is_mock=true`，表示“本文件是模拟/演示数据”。
  不传则 `is_mock=null`（未声明），按真实本地文件处理（`mode="live"`）。`is_mock` 非布尔 →
  validation_error。声明后立即写工作区 `data_provenance` 并给该主体打 `is_mock=true` 标记。
- **同编号同批冲突（A5）**：同一批里同一 `business_key` 对应不同内容 → 该主体隔离为
  `status=conflicted`（空字段、保留全部冲突变体、发阻塞 issue），**不按行序 last-row-wins**；
  同编号同内容才去重。冲突判定与行序无关（隔离结果稳定）。
- **同名歧义**：同名但不同编号 → 各自保留为不同主体、不合并，发非阻塞 `name_ambiguity` 提示。
- 输出 extra：`import_id, file, header, suggested_mapping, field_conflicts, unmapped_columns,
  counts, total_data_rows, format_boundaries, is_mock, workspace_is_mock, mode_note`。
  `counts` 五类互斥且和=数据行数。

### filter — 条件筛选
```
filter --request <JSON|@file>
```
- request：`{conditions, hard_exclusions?, rule_version?, rule_is_mock?, sort_by?}`
  （未知字段一律拒绝）。`hard_exclusions` 是**条件树数组**（多棵按 OR 合并），不是单棵树；
  非列表直接 validation_error。
- 输出 extra：`result_id, condition_tree, hard_exclusions, eligible[], excluded[],
  needs_review[], counts, rule_is_mock, is_mock, data_is_mock, disclaimer, outputs,
  explanation_note`。
- `mode` 继承：演示规则(`rule_is_mock`)或工作区已有 mock 导入 → `mode="mock"`；
  粘性，不被外层降级。`is_mock` 与 `data_is_mock` 同值（`mode=="mock"`）。
- 导出的 CSV 固定 12 列：`decision, entity_id, name, industry, region, member_status,
  renewal_status, contactable_status, reason, reason_detail, unknown_fields, rule_is_mock`；
  MD 排除/待核实表同样渲染 `reason_detail`（与 JSON 逐字一致）。

### brief — 一页拜访简报
```
brief --entity <entity_id> [--context <JSON|@file>] [--purpose <文本>]
```
- context：`{goals[], questions[], suggestions[], preparations[], materials[]}`（宿主生成，标注 AI）。
- **brief 不接受 `is_mock` 输入**（无该参数）：mode 由主体/工作区的导入声明**继承**，
  因此不存在被外层 `is_mock:false` 降级回 `live` 的路径。
- 事实带字段级引用（来源+data_version）；不在模型内的敏感项一律列未知，不编造。
- 输出 extra：`brief_id, entity_id, outputs, is_mock, mode_note, unknowns[], inferences,
  suggestions, goals, questions, preparations, warnings, unmapped_columns,
  pending_mapping_fields, disclaimer`。
- `unmapped_columns`：源表中以 `custom:` 保留、无法归一到规范字段的列；涉及这些列的字段在
  简报中记为**“待字段映射”**（不是“源数据未提供”，也不得填猜测值），并发
  `pending_field_mapping` issue；此时 `status=needs_review`。

### minutes-extract — 结构化原始纪要 → 待核对版
```
minutes-extract --raw <文本文件> --extraction <JSON|@file>
```
- extraction：`{entity_id?, minutes_date?, is_mock?, identity_evidence?, items:[{id,type,quote,text,owner?,scope?}]}`。
  - `identity_evidence`：有关联 entity_id 时必须提供 `{key_type, value, raw_citation}`。
    `key_type` 使用 `business_key / uscc / provider_id / name`；value 必须与主体对应字段相同，
    raw_citation 必须能在原文定位且包含 value。仅用 name 时必须在档案中唯一。
    原文缺少可定位身份时，保持 entity_id 为空并让用户补充原文依据，不能猜测编号或改写原文。
    主体处于 conflicted 时不能绑定。确认、生成计划和任务编辑会重新检查身份及实际原文快照 hash。
  - `is_mock`：**可选**布尔，非布尔即 validation_error。语义是“本条纪要是模拟演示数据”；
    省略时按档案/工作区继承。**继承优先于降级**：档案或工作区已是 mock 时，即使这里写
    `is_mock:false` 也不会把纪要降回 `live`（粘性）。
  - `minutes_date` 在任何落盘前做真实日历校验，非法直接拒绝（不产生半成品状态）。
  - **纪要不自动识别主体**：`entity_id` 有值必须存在于档案（has-value-must-exist）。
- type ∈ `customer_statement|need|commitment|open_question|inference|no_contact`。
- 条目 `id` 显式给出必须唯一（重复即拒绝）；省略时按“类型+文本+引用”确定性生成，同批碰撞按序号区分。
- **内容派生 visit_id（A2）**：`visit_id = stable_id(原文hash, entity_id, minutes_date, 条目签名)`。
  相同输入重放 → 同一 `visit_id`、返回既有版本（`idempotent_replay=true`，**不覆盖旧确认**，
  mode 沿用首次落盘值）；内容/主体/日期/原文任一改变（含空抽取）→ 新 `visit_id` 新版本，
  旧版本原样保留。
- 校验每条 `quote` 是否真实存在于原文（防幻觉引用）；扫描内嵌指令性文本（只标记不执行）。
- 输出 extra：`visit_id, entity_id, minutes_date, extraction_version, extraction_hash,
  mode, is_mock, mode_note, identity_evidence, no_contact[], visit_status="draft",
  idempotent_replay, outputs`。

### minutes-confirm — 人员核对 → 已核对版
```
minutes-confirm --visit-id <id> --confirmations <JSON|@file>
```
- confirmations：`{confirmed_by, expected_extraction_version?, raw_hash?, decisions:[{id, decision:confirm|reject|edit, edit?}]}`。
- **针对特定版本核对（A1/A2）**：`expected_extraction_version`/`raw_hash` 可选校验，版本或原文已变即拒绝。
- 决策必须引用**唯一**条目 ID：未知 ID、重复 ID、未知 decision 一律拒绝，不静默忽略。
- **无可定位引用的条目不能被 confirm 成事实**（防幻觉引用冒充实测）：`citation.verified!=true` → 保持
  `pending` 并发阻塞 issue `confirm_refused_unverified`；`edit` 仅改文本、仍受引用校验约束。
- 无决定的非推断条目保留已确认/已否决，否则仍 `pending`（支持逐项部分核对）。
- 整份状态：全部非推断条目确认→`confirmed`；部分→`partially_confirmed`；否则 `draft`。
- 推断永不因确认变事实（仅可升级为 `confirmed_inference`）；已 `confirmed` 则幂等返回。
- **mode 继承**：`mode = visit["mode"] or source_mode(entity_id)`，即沿用纪要首次落盘时的来源模式，
  不被确认步骤降级；输出 extra 含 `is_mock`。
- 输出 extra：`visit_id, entity_id, minutes_date, visit_status, confirmed_by, is_mock,
  confirmed_needs, confirmed_commitments, no_contact, outputs`。

### plan — 由已核对纪要生成行动计划
```
plan --request <JSON|@file>
```
- request：`{visit_id, entity_id?, minutes_date?, no_contact?, is_mock?, tasks:[{source_item_id?, deliverable, owner?, deadline?, deadline_relative?{text,days}, dependencies?, channel?, non_contact_action?, next_trigger?, independent?, note?}]}`。
  - **未知字段一律拒绝**（A6）。计划阶段不接受 `status`/`completion_evidence`（完成证据属 task-update）。
  - `entity_id` 有值必须存在于档案，且不得与纪要主体冲突（A3，禁止计划覆盖拜访主体）。
  - `is_mock`：**可选**布尔，声明“本计划是模拟演示”。通常**不需要**传——mode 由
    纪要/档案/工作区继承；显式 `true` 只用于“纯纪要入口 + 无档案”的演示场景。
    请求不写该键也**不会**把已 mock 的上游降级回 `live`（粘性）。
- 必须是对 `status∈{confirmed, partially_confirmed}` 的纪要；否则报 validation_error。
- **来源项必须可定位（A1）**：`source_item_id` 若非空，必须在已核对纪要中“存在 + 已确认 + 引用有效”，
  否则拒绝生成正式任务；无 `source_item_id` 的独立任务允许，但标注“用户新增独立任务”，不冒充客户承诺。
- 缺负责人/期限→`draft`；命中拒绝联系 scope 的渠道→`blocked`（快照 `contact_restricted_scopes`）；否则 `pending_assignment`。
- 相对日期以纪要日期换算并保留原话；`non_contact_action` 必须为布尔。
- **稳定任务身份（A4）**：`task_id` 由 `entity_id+visit_id+交付物/来源/渠道/non_contact/independent` 的内容签名派生，
  **不含 owner/deadline**；改负责人/期限必须通过 task-update 更新同一任务；plan 重放只返回现有任务，保留人工修改与状态。
- 每个任务落盘时带 `mode`（继承自本计划的 mode），后续 task-update 沿用。
- 预检全部通过才落盘（A6：任何无效输入在写盘前拒绝）。
- 输出 extra：`plan_id, entity_id, visit_id, tasks, created, reused, no_contact,
  outputs, mode, is_mock, mode_note, disclaimer`。

### task-update — 更新任务状态 / 编辑字段
```
task-update --task-id <id> --request <JSON|@file>
```
- request：`{status?, owner?, deadline?, deadline_relative?, dependencies?, deliverable?, channel?, next_trigger?, reason?, completion_evidence?, note?}`。
  **未知字段一律拒绝**（A6）。status ∈ `draft|pending_assignment|in_progress|completed|blocked|cancelled`。
- **正规字段编辑入口（A4）**：可补齐/修改 owner、deadline（绝对或相对）、dependencies、deliverable、
  channel、next_trigger，并记录 old/new 变更历史；支持“补齐 owner/deadline 并同次推进”。
- **最终状态校验（每次编辑均检查，即使请求没有 status）**：
  - 终态(completed/cancelled)不能通过 status 直接恢复执行态（重开请新建任务）。
  - `completed` 必须有 `completion_evidence`；同证据重复完成幂等（不重复追加历史）。
  - `blocked/cancelled` 必须有 `reason`。
  - 推进到执行态(pending_assignment/in_progress)必须有 owner 且 deadline 有效。
  - **联系限制每次重校**：命中限制不能进入执行/完成态，任意 reason 均不能解除；内部准备不可同时填对外渠道。
    本版不提供原任务解禁接口；客户偏好改变须提供新原文、重新核对并生成独立新任务，旧受阻任务保留或取消。
- 完成证据不因普通更新被抹掉；`updated_at`/`history` 记录全部变更。
- **mode 继承**：`mode` 取任务落盘时的 `mode`，或回落到该主体/工作区的来源模式；
  **不接受 `is_mock` 输入参数**，因此不存在被本次更新降级回 `live` 的路径；
  输出 extra 含 `is_mock`。
- 输出 extra：`task_id, old_status, new_status, changed_fields, is_mock, disclaimer`。

### mcp-search / mcp-ingest — 可选外部检索（本轮不发起真实调用）
见 `mcp-catalog.md`。要点：
- `mcp-search` 的 `external_status` ∈ `no_tool|unauthorized|expired|error|partial|available`；
  `planned` 是**布尔值**（不是 `external_status` 的枚举值），`executed`/`data_returned`
  恒为 `false`，`status` 恒为 `needs_review`。
- `mcp-ingest` 顶层 `mode` 可为 `mock|live|unknown`（回包未声明且 `source_tool` 未知时 `unknown`），
  每条候选带 `verification_state / relation_to_local / conflict_check / field_differences /
  unmapped_local_columns / mapping_pending`；`conflict_check` 含
  `incomplete_pending_mapping`（本地待比较字段还在未映射列时）——**不得留空让人以为已核对**。

### index — 工作目录摘要
```
index
```
- 输出顶层：`workdir, data_version, mode, data_is_mock, counts{entities,results,tasks,visits},
  known_fields`。`mode` 为工作区来源模式（混入 mock 导入后恒为 `mock`）。

## 工作目录与 index.json（跨会话恢复）

`--workdir` 下由脚本维护：
- `index.json`：唯一状态存储（`schema_version, data_version, known_fields,
  identity_map, entities, imports, results, visits, tasks, change_events,
  data_provenance`）。
  - `data_provenance`：`{"is_mock": bool, "imports": [{import_id, file, is_mock, at}]}`。
    `is_mock` 是**粘性**标记：任一次 `is_mock=true` 的导入后恒为 true，不会被后续真实导入降级。
  - `entities[id]["is_mock"]`：该主体档案来自 mock 导入时为 true。
  - `imports[]` / `visits[]` / `results[]` / `tasks[]` 各自记录落盘时的 `mode`（live/mock）。
- `README.md`：人类可读索引（主体表/结果过期标记/任务表），并显示“含模拟(mock)数据”与各结果模式列。
- `results/`：各结果的 `.md`（人读）与 `.csv`（导出，已过公式注入防护）。

**不存储任何凭证/密钥。** 结果按生成时的 `data_version` 判定是否因数据更新而过期。
