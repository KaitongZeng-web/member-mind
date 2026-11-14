<img width="1877" height="901" alt="屏幕截图 2026-11-14 203200" src="https://github.com/user-attachments/assets/84bed7fa-11b5-4825-bd03-263d86c39688" /># 企服智台（Member Mind）

面向企业会员服务场景的数字化工作台。项目以企业档案为数据基础，围绕企业资料检索、会员筛选、拜访简报、会议匹配和服务行动管理，帮助服务团队把分散的会员信息转化为可查询、可核验、可持续更新的服务记录。

<p align="center">
  <a href="https://kaitongzeng-web.github.io/member-mind/"><strong>🚀 在线演示（独立离线 HTML 版）</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/KaitongZeng-web/member-mind/blob/main/index.html">查看演示页面源码</a>
</p>

"E:\Hackthon\member-mind\屏幕截图 2026-11-14 203200.png"

> **演示说明：** 上方链接打开的是仓库根目录 `index.html` 部署到 GitHub Pages 的独立演示版。无需安装 Python 或启动后端，内置模拟数据，可直接体验主要页面和交互。演示版中的 DeepSeek、企查查 MCP 等外部服务入口用于展示接入位置，不代表已在该静态页面中完成真实调用。

---

## 项目简介

企服智台包含两个可独立使用的入口：

| 版本 | 入口 | 适用场景 |
|---|---|---|
| 独立演示版 | [在线打开](https://kaitongzeng-web.github.io/member-mind/) · [index.html](index.html) | 快速体验界面与核心交互；无需后端 |
| Python + SQLite 完整版 | 本地启动项目服务 | 使用 FastAPI、SQLite 持久化业务数据，并运行后端业务逻辑与 MCP 客户端 |

两个版本的定位不同：独立演示版用于快速展示，不连接本地数据库；完整版本由 Python 后端提供业务 API 和持久化能力。不要将静态演示版理解为完整后端服务。

## 核心功能

- **企业档案管理**：维护企业基础信息、会员属性、联系情况及资料完整度。
- **企业资料助理**：通过自然语言描述查询需求，结合模型与外部资料工具整理企业信息。
- **会员筛选与盘活**：按组合条件筛选企业，区分明确符合、不符合和信息待核实的记录。
- **拜访简报**：基于企业档案与可追溯资料生成拜访准备内容。
- **会议匹配**：根据会议主题和参会条件匹配候选企业，并导出名单。
- **拜访计划与行动管理**：记录负责人、截止时间、服务动作和处理状态。
- **历史记录与数据导出**：保留业务结果和变更记录，支持数据导出与数据库备份。
- **MCP 工具连接**：提供 MCP 服务配置、工具发现、白名单和人工确认调用流程。

业务数据中的事实、推断、建议和待核实信息应分别处理。公开资料不能替代内部会员台账；资料缺失不等于条件不符合；外部工具返回内容也不会自动成为已核验的企业事实。

## 技术栈

- **前端**：HTML、CSS、JavaScript
- **后端**：Python 3.11+、FastAPI、Uvicorn
- **数据存储**：SQLite
- **工具协议**：Model Context Protocol（MCP）Python SDK
- **数据校验**：jsonschema
- **测试**：pytest
- **部署**：本地 Python 进程；提供 Dockerfile 与 Docker Compose 配置

## 本地运行

### 环境要求

- Python 3.11 或更高版本
- Windows、macOS 或 Linux
- 首次安装依赖通常需要网络；Windows x64 / CPython 3.13 环境可使用仓库提供的 wheel 文件离线安装

### Windows 快速启动

1. 下载或克隆本仓库。
2. 双击项目根目录的 **启动网站.cmd**，或在终端运行：

   ```powershell
   python start.py
   ```

3. 等待服务启动，在浏览器访问：

   ```
   http://127.0.0.1:8877
   ```

启动脚本会在项目目录创建 `.venv`，并在需要时安装 `requirements.txt` 中的运行依赖。关闭服务可在启动窗口按 `Ctrl+C`。

### 手动安装与启动

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

服务默认监听 `127.0.0.1:8877`。可通过环境变量 `APP_HOST`、`APP_PORT` 和 `APP_DB_PATH` 调整监听地址、端口和数据库位置。启动后可访问：

- Web 工作台：`http://127.0.0.1:8877`
- API 文档：`http://127.0.0.1:8877/docs`
- 健康检查：`http://127.0.0.1:8877/api/health`

## 外部服务配置

### DeepSeek

在完整版本的聊天页「连接与模型」区域配置模型服务和 API Key。模型名称可按账号权限调整。密钥由本机服务端保存，不应写入前端代码、提交到 Git 或放入公开配置文件。

### 企查查 MCP

项目提供企查查相关 MCP 端点的接入配置。可在聊天导入流程中配置 API Key；也可按部署环境通过服务端环境变量 `QCC_API_KEY` 提供凭证。

### 通用 MCP

工作台支持配置 MCP 服务、传输方式、端点、鉴权环境变量名、超时和允许调用的工具列表。保存配置不会自动连接服务；需要手动测试连接并发现工具。工具调用前应检查参数并确认，服务端会再次校验工具白名单。

当前 MCP 客户端支持 **stdio** 和 **Streamable HTTP**。OAuth 交互授权、旧版 SSE、资源/提示词 UI 等能力不属于当前已实现范围。具体第三方服务是否可用，取决于其账号授权、网络环境和工具权限。

**安全提示：** 不要将真实 API Key、Bearer Token、个人凭证或生产数据提交到仓库。公网部署前必须配置 HTTPS、身份认证、访问控制、可信主机与来源白名单，并限制 MCP 服务的访问范围。当前本地默认配置面向单机演示，不是开箱即用的多租户生产环境。

## 数据存储与备份

- 默认 SQLite 数据库：`data/workspace.sqlite3`
- 数据库路径可通过 `APP_DB_PATH` 指定。
- 业务写入由后端执行，并使用 SQLite 事务；修订号用于避免并发覆盖，请求 ID 用于幂等处理。
- 网页导出的工作空间 JSON 是业务数据导出，不等同于 SQLite 完整备份。

备份数据库：

```bash
python backup_db.py backup.sqlite3
```

恢复前先停止服务，并妥善备份当前数据库文件；不要在服务运行期间直接替换正在使用的 SQLite 数据库。

## 测试

安装开发依赖：

```bash
python -m pip install -r requirements-dev.txt
```

运行测试：

```bash
python -m pytest -q tests --basetemp .test-run
```

测试使用独立数据库，避免覆盖日常工作空间数据。项目验证记录见 [验证记录.md](验证记录.md)。

## Docker

仓库提供 `Dockerfile` 和 `compose.yaml`。在安装 Docker 的环境中执行：

```bash
docker compose up -d --build
```

默认将服务端口绑定到本机 `127.0.0.1:8877`，并使用 Docker volume 持久化数据库。该配置适合本机运行验证；若要部署到公网，需要自行补充 HTTPS 反向代理、身份认证、访问控制和生产级配置。Docker 部署尚未作为当前验证记录中的已验证项目。

## 项目结构

```text
.
├── index.html                 # 独立离线演示页面（GitHub Pages 入口）
├── frontend/                  # 完整版工作台前端
├── backend/                   # FastAPI 路由、业务逻辑、数据访问与 MCP 网关
├── config/                    # 初始化数据及配置
├── tests/                     # 自动化测试
├── .github/workflows/         # GitHub Actions 工作流
├── run.py                     # 启动 FastAPI 服务
├── start.py                   # 本地环境检查、依赖安装与启动脚本
├── requirements.txt           # 运行依赖
├── requirements-dev.txt       # 开发与测试依赖
├── sample_mcp_server.py       # 本地只读 MCP 联调示例
├── backup_db.py               # SQLite 备份
├── import_v2.py               # 旧版工作空间 JSON 迁移
├── Dockerfile
├── compose.yaml
├── OFFLINE_DEMO.md            # 独立演示版说明
├── 验证记录.md                 # 当前版本验证记录
└── skill                      # 内嵌至Workbuddy，非技术人员0摩擦上手
```

## 相关链接

- [在线演示（独立离线 HTML 版）](https://kaitongzeng-web.github.io/member-mind/)
- [独立演示页面源码](https://github.com/KaitongZeng-web/member-mind/blob/main/index.html)
- [离线演示说明](OFFLINE_DEMO.md)
- [验证记录](验证记录.md)
- [GitHub Actions 部署记录](https://github.com/KaitongZeng-web/member-mind/actions)

## License

当前仓库未声明开源许可证。未经仓库维护者许可，请勿将其视为可自由再分发或商用的开源项目。
