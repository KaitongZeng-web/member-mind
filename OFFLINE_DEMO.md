# 企服智台离线演示版

## 直接打开

- 在线演示（GitHub Pages 部署成功后）：https://KaitongZeng-web.github.io/member-mind/
- 单文件入口：仓库根目录 `index.html`
- 本地离线：下载 `index.html`，双击即可用现代浏览器打开；不需要 Python、Node、安装包或网络。

## 演示内容

服务概览、企业档案、AI资料助理、会员筛选、拜访简报、会议匹配、拜访计划、历史记录、数据连接设置。内置匿名演示企业数据，支持本地新增/编辑档案、筛选、生成简报、创建行动、导出 CSV / JSON / TXT。修改保存在当前浏览器 localStorage。

## 外部能力边界

页面保留 DeepSeek 与企查查 MCP 的连接说明、工具目录和执行链路，但离线 HTML 不会真实调用这些服务。连接状态不会被伪装成成功。要启用真实 API，必须另行部署有身份认证、HTTPS、密钥管理和访问控制的后端代理；不要把 DeepSeek 或企查查密钥写进公开 HTML、GitHub 仓库或浏览器 localStorage。GitHub Pages 只能托管静态文件，不能运行本项目原有 Python/FastAPI、SQLite 或 MCP 服务。

## GitHub Pages

已加入 `.github/workflows/pages.yml`。在 GitHub 仓库 Settings → Pages，将 Build and deployment 的 Source 设为 **GitHub Actions**。随后每次推送根目录 `index.html` 都会自动部署。部署完成后访问：

https://KaitongZeng-web.github.io/member-mind/

## 说明

这份离线演示是独立静态展示，不会覆盖 `frontend/`、`backend/`、SQLite 业务服务或原有项目代码。原系统完整运行仍按仓库 README 使用 Python + SQLite。
