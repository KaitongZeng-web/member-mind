# 验证记录与边界（v0.4.2）

本文件是 member-service 的**随包验证说明**：哪些行为有测试保证、对应哪些防错目标
（A1–A6）、如何在本机复跑、以及**如实**的未验证边界。供复核者对照，不夸大。

- 运行环境：Python 3.9+，**仅标准库**，无第三方依赖，不联网。
- 复跑全部测试：`python3 tests/run_all.py`（每个用例独立临时工作目录，互不污染）。
- 复跑完整案例：`python3 run_demo.py --workdir ./demo-work`（全程 mock，无真实外部调用）。
- 当前规模：**154 项测试**（12 个模块：common/import/xlsx/filter/brief/minutes/plan/mcp/cli/
  export/mode/meta），`python3 tests/run_all.py` 全部通过。

## 测试覆盖与防错目标映射

| 目标 | 保证 | 主要测试 |
|---|---|---|
| **A1 确认门 / 来源可定位** | 无可定位引用的条目不能 confirm 成事实；计划来源项必须“存在+已确认+引用有效”；推断永不因确认变事实 | test_minutes（phantom 拒绝、部分核对）、test_plan（不可定位来源拒绝） |
| **A2 碰撞安全 ID / 版本** | 内容派生 visit_id：同输入重放保留确认、异内容/主体/日期/原文→新版本；同秒多计划不覆盖 | test_minutes（重放/新版本/同原文异主体）、test_plan（两计划 distinct plan_id） |
| **A3 主体绑定** | entity_id 有值必须存在、不得与纪要主体冲突；business_key 与 uscc 分开；identity_evidence 必须与原文和档案对应；不硬编码具体企业 | test_plan（entity 冲突/不存在拒绝）、test_minutes（主体不存在拒绝）、run_demo（从档案解析主体） |
| **A4 任务身份与迁移** | task_id 不含 owner/deadline（改负责人/期限更新同一任务）；正规字段编辑入口；终态不回退；完成需证据且幂等；联系限制每次重校、自由文本原因不能解禁；本版不支持原任务解除联系限制 | test_plan（改 owner 不新建、字段编辑+推进、终态/完成/受阻/联系限制） |
| **A5 导入冲突隔离** | 同编号同批冲突隔离为 conflicted、不按行序 last-row-wins、与行序无关；同名不合并；去重与重排幂等 | test_import（冲突隔离/行序无关/同内容去重/同名歧义）、test_cli（行重排不改变绑定） |
| **A6 前置校验 / 结构化错误** | 计划/任务未知字段拒绝；非法日期写盘前拒绝；CLI 坏 JSON/@缺失文件走约定退出码而非异常栈 | test_plan、test_minutes、test_cli |
| **P2 交付元数据** | SKILL.md frontmatter 8 必填字段、版本号、作者不伪称官方 | test_meta |
| **P2 导出解释一致** | 排除/待核实的 MD/CSV 与 JSON 用同源 `reason_detail`（决定性分支 + NOT/OR 语义 + 实测值）；NOT 内 TRUE 叶子不得写成“未满足”；未采用的 OR FALSE 分支不得进理由；硬排除须带实际决定分支与实际值 | test_export（9 项） |
| **来源模式贯通（mock/live）** | 导入 `is_mock`（CLI `--mock`）落到主体与工作区；brief/纪要/计划/任务更新/索引/导出继承且不被外层 `false` 降级；MD/CSV/README 读者可见 mock；真实文件流程保持 live；纯纪要入口 mock 也贯通 | test_mode（7 项） |

> 上述为**回归测试**覆盖的行为；测试通过不等于“业务结论正确”，只等于“契约被遵守”。

## 已在本机验证（Python 3.9，仅标准库）

- CSV/XLSX 导入、字段别名映射、冲突/缺失/重复/异常报告、稳定主体身份、幂等重导入、跨格式不涨版本。
- 同编号同批冲突隔离、同名歧义标记、未映射列提示。
- 三值逻辑筛选（缺失不因 NOT 变真；硬排除优先；eligible/excluded/needs_review 分流）。
- 一页简报：事实带字段级引用、未知项单列、不编造。
- 纪要结构化（幻觉引用标记、内嵌指令只标记不执行、引用行号定位）、逐项/部分核对、版本绑定。
- 计划/任务：草案/受阻/完成证据/字段编辑/联系限制重校/幂等。
- CSV 导出公式注入防护、路径越界拒绝、原子写。
- CLI 退出码（0/2/3/4/5/6）与统一返回信封（extra 平铺顶层）。
- **导出解释一致性**（`tools_verify_export.py` 亦可单独实测）：用与主审复现同型的表头与
  `AND{...NOT(...)...OR{...}}` 条件树跑真实 CLI，核对 JSON/MD/CSV 三方 `reason_detail`
  逐字一致、且不再出现“未满足: <条件>”这类误导文案。
- **来源模式贯通**：`import --mock` → brief/minutes-extract/confirm/plan/task-update/filter/index
  全链 mode=mock，MD/CSV/README 读者可见；粘性不被后续真实导入或 `is_mock:false` 降级；
  未声明 mock 的真实文件流程全程 live。

## 未验证 / 需宿主确认（不夸大）

- **WorkBuddy 安装与运行未验证**：不保证开箱即用；脚本由宿主按 SKILL.md 在内部代调用。
- **自然语言→条件树 / →纪要结构化由宿主模型完成**：脚本只做确定性计算，不解析自然语言，
  不假装具备通用 NL 能力。
- **真实 QCC / 腾讯企业检索未执行**：当前本环境未实测真实连接；`mcp-search` 无工具时返回
  `no_tool`，`mcp-ingest` 仅用 mock 回包演示归一化（`is_mock=true`）。可在授权范围内用真实
  工具/schema 查询并导入回执，但不得据此声称已真实查询。
- 具体宿主的权限、工具发现、授权流程以实际环境为准。
- 结论性提醒：演示条件树/纪要抽取/计划任务均为“宿主等价输入”样例，**非真实业务承诺**。
- `mode`（live/mock）只表示**资料来源**，不代表已核实工商真实性；`mock` 只说明数据是演示/模拟数据。
- 粘性来源模式是保守设计：一旦工作区混入 mock 导入即全程 mock，**不做混合数据库判定**；
  模拟数据与真实数据建议使用不同 `--workdir`。

## 如何判定一个结果是否过期

结果按生成时的 `data_version` 登记；`--workdir/README.md` 对每条结果标注是否因后续导入
导致数据变更而过期。跨会话指向同一 `--workdir`（或设 `MEMBER_WORKDIR`）即可恢复，不依赖聊天记录。
