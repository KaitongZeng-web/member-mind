# Member Mind｜企服智台

**AI-powered Enterprise Member Service Platform**

企服智台是面向企业服务机构的会员服务数字员工 Demo。它把会员资料管理、企业信息检索、会员筛选、拜访简报、拜访纪要与后续行动计划整合到一个工作台中。

> 当前仓库为开发演示版本。仓库中的样例会员数据、业务规则和外部服务状态仅用于演示，不代表任何机构的正式业务规则或真实客户数据。

## 项目结构

```text
member-mind/
├── app/                      # Web Demo、Python 后端与前端
│   ├── backend/              # API、业务逻辑、数据访问、模型与 MCP 接入
│   ├── frontend/             # Web 前端
│   ├── config/               # 初始化与演示配置
│   ├── tests/                # 后端与前端测试
│   ├── requirements.txt
│   └── .env.example          # 环境变量模板（不含真实密钥）
├── skill/                    # 独立的企业会员服务 Skill
│   ├── member-service/       # SKILL.md、脚本、模板、参考资料与测试
│   └── sample-data/          # 模拟会员导入样例
├── demos/                    # 静态产品演示页面
└── .gitignore
```

## 快速开始

建议使用 Python 3.10+。

```bash
cd app
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python run.py
```

启动后请以终端输出的本地地址为准。若项目需要模型服务，请复制 `.env.example` 为 `.env` 并填写你自己的 API Key；**不要将 `.env`、密钥或真实客户资料提交到 GitHub**。

## 独立使用 Skill

Skill 位于 [`skill/member-service/`](skill/member-service/)，入口文件为 [`SKILL.md`](skill/member-service/SKILL.md)。其中包含可执行脚本、模板、参考规则、样例数据和测试。可先阅读 Skill 文档及其 README，再按所使用的 Agent 平台要求安装或导入。

## 安全与数据说明

- 仓库不应包含真实 API Key、访问令牌、数据库文件或真实客户隐私数据。
- `.env.example` 仅提供配置项示例；运行时凭证应保存在本地环境变量或安全密钥管理服务中。
- 样例数据为演示用途；接入真实业务前需完成授权、数据脱敏、权限控制与合规审查。
- 外部检索、模型调用和 MCP 写入能力取决于运行环境中的配置与授权。

## 项目状态

这是一个持续迭代中的 Hackathon / Prototype 项目。README 描述的是代码目标与模块划分，不代表所有外部集成在任意环境中均已开通或验证。
