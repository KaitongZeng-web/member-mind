# member-service（v0.4.2）

本地“会员激活与拜访助手”。导入会员 Excel/CSV → 按显式条件树做三值逻辑筛选 → 生成一页  
拜访简报 → 结构化原始纪要并人工核对 → 生成后续行动计划。**纯 Python 3.9 标准库，零第三方  
依赖，不联网、不存凭证。** 外部企业检索(QCC等)为可选增强，无工具时主线仍可用。

## 目录结构

```
member-service/
├── SKILL.md                 # WorkBuddy Skill 入口（frontmatter + 编排指引）
├── README.md                # 本文件
├── run_demo.py              # 一条命令跑通完整案例
├── scripts/                 # 本地核心（宿主内部代调用）
│   ├── ms_cli.py            #   命令行入口（import/filter/brief/minutes-*/plan/task-update/mcp-*/index）
│   ├── ms_common.py         #   错误/退出码/原子写/路径安全/CSV防注入/信封
│   ├── ms_schema.py         #   字段字典与表头别名映射
│   ├── ms_xlsx.py           #   标准库 OOXML(.xlsx) 读取
│   ├── ms_store.py          #   index.json 状态存储、主体身份、幂等、任务
│   ├── ms_import.py         #   导入（映射/冲突/缺失/重复/异常/幂等）
│   ├── ms_filter.py         #   三值逻辑筛选
│   ├── ms_brief.py          #   一页拜访简报
│   ├── ms_minutes.py        #   纪要结构化 + 人工核对
│   ├── ms_plan.py           #   行动计划 + 任务状态更新
│   ├── ms_mcp.py            #   可选外部检索（意图/候选归一化，不真实调用）
│   └── ms_export.py         #   MD/CSV 渲染
├── data/                    # 匿名样例与 mock 数据
│   ├── members_sample.csv / members_sample.xlsx
│   ├── make_sample_xlsx.py  #   标准库生成样例 xlsx（可复用）
│   ├── filter_request_demo.json / minutes_*.json / plan_request_sample.json / mcp_candidates_mock.json
│   └── minutes_raw.txt
├── templates/               # 人工填写模板（纪要/简报/计划，纸面兜底）
├── references/              # 契约与说明
│   ├── contracts.md         #   命令契约 + 数据模式 + index.json
│   ├── mcp-catalog.md       #   外部检索协议 + QCC 连接器要点
│   ├── validation.md        #   验证覆盖(A1–A6)与未验证边界
│   └── independent-use.md   #   独立使用/连接状态/已验证与未验证边界
└── tests/                   # 仅标准库测试（无 pytest）
    ├── run_all.py           #   运行全部
    └── test_*.py            #   common/import/xlsx/filter/brief/minutes/plan/mcp/cli/export/mode/meta
```

## 快速开始

```bash
# 运行全部测试（应全部通过）
python3 tests/run_all.py

# 一条命令跑通完整案例（导入→筛选→简报→纪要→计划→任务→MCP mock）
python3 run_demo.py --workdir ./demo-work
```

|                                                                |               |
| -------------------------------------------------------------- | ------------- |
| 查看 `./demo-work/README.md`（人类可读索引）与 \`./demo-work/results/*.md | .csv\`（各步结果）。 |

## 单独调用示例（宿主代调用）

```bash
WD=./my-work
python3 scripts/ms_cli.py --workdir $WD import --file data/members_sample.csv
python3 scripts/ms_cli.py --workdir $WD filter --request @data/filter_request_demo.json
python3 scripts/ms_cli.py --workdir $WD index
```

> 普通用户不做上述操作；这些由宿主在内部调用。用户只上传/粘贴文件、查看结果、确认。  
> 演示数据在导入处加 `--mock` 声明来源模式；此后 brief/minutes/plan/task-update/index/导出  
> 全程继承 mock，不需要（也不允许）在后续步骤手工补 `is_mock`。

## 关键设计（防错要点）

- **稳定主体身份**：有业务编号按编号定主体（跨文件/格式/行序稳定）；无编号行按“指纹+内容+序号”  
  建档，内容变即新主体，**不推断身份稳定**；**重名绝不合并**，标记待人工确认；源行号只作证据。
- **三值逻辑**：缺失=UNKNOWN，`NOT(UNKNOWN)=UNKNOWN`（缺失不因 NOT 变真）；先硬排除，再分  
  eligible/excluded/needs_review。
- **不编造**：资质/风险/实际控制人/真实会员等级无依据一律标未知。
- **纪要**：引用必须真实存在于原文（防幻觉）；内嵌指令只标记不执行；推断永不因确认变事实；  
  未核对前不作为已确认承诺。
- **计划/任务**：缺负责人/期限→草案；相对日期以纪要日期换算并保留原话；命中拒绝联系范围→受阻；  
  完成需证据；**任务完成≠续费**；按 business_key 幂等，不动其他主体任务。
- **确认门（A1）**：仅“已核对且引用在原文真实定位”的纪要条目才能成为事实/计划来源；推断永不因确认变事实。
- **碰撞安全与内容派生 ID（A2）**：会话级 ID 用 时间+计数器+随机；`visit_id` 由内容派生——  
  同输入重放保留旧确认，内容/主体/日期/原文改变才生成新版本。
- **任务身份（A4）**：`task_id` 不含负责人/期限，改期/换人更新同一任务；正规字段编辑入口保留历史；  
  终态不回退；拒绝联系范围每次迁移重校，自由文本原因不能解禁；本版不支持原任务解除联系限制。
- **导入冲突隔离（A5）**：同编号同批冲突隔离待核对，不按行序覆盖；重名不合并。
- **来源模式贯通（mock/live）**：导入显式声明 `is_mock`（CLI `--mock`）→ 落到主体与工作区；  
  brief/纪要/计划/任务更新/索引/导出全部**继承**，且不被外层 `is_mock:false` 降级回 live  
  （粘性，建议模拟与真实数据用不同工作区）。`mode` 只表示资料来源，**不代表已核实工商真实性**。
- **导出解释与 JSON 同源**：排除/待核实的 MD 与 CSV 一律用 `reason_detail`（决定性分支 + NOT/OR  
  语义 + 实测值），绝不把 NOT 内部为 TRUE 的叶子写成“未满足”。
- **mock 全程标记**；外部候选≠已核实，不自动合并/不自动赋会员属性；冲突保留待核对不覆盖本地。
- **安全**：CSV 导出防公式注入；路径越界拒绝；原子写；不存凭证；不做真实外部调用。

## 已验证 / 未验证

- **已验证**（Python 3.9，仅标准库）：CSV/XLSX 导入、映射与各类报告、稳定身份与幂等、同编号  
  冲突隔离、三值逻辑筛选、简报、纪要结构化/逐项核对/版本绑定、计划/任务（含字段编辑与联系  
  限制重校）、CSV 防注入、路径安全、CLI 退出码与信封；`python3 tests/run_all.py` 全部通过  
  （当前 **154 项**，含 A1–A6/P2 回归、导出解释一致性与 mock 贯通回归）。
- **未验证（不夸大）**：WorkBuddy 安装/运行未验证，不保证开箱即用；自然语言→条件树/纪要结构化  
  由宿主模型完成；真实 QCC/腾讯检索未执行（本环境未实测真实连接，属实现边界）。

详见 `references/validation.md`（验证覆盖与边界）与 `references/independent-use.md`（连接状态）。
