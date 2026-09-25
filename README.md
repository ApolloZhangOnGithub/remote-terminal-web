# Remote Terminal Web (rtw)
<img width="1464" height="911" alt="截屏2026-09-25 02 54 06" src="https://github.com/user-attachments/assets/1fe6da8f-2cd7-4f0e-ba6d-d364992bf0e8" />
浏览器里的远程终端 —— 让 Agent / AI 编程助手拥有一个随时可用的网页终端入口。

> 你在 c-n-b.space/terminal 打开网页，就能在浏览器里使用跑在远程机器上的终端（含 AI agent 的 TUI 界面），像操作本地终端一样实时流畅。

## 特性

- 🌐 **纯网页访问**：无需 SSH 客户端，浏览器即终端（基于 ttyd + xterm.js）
- 🔐 **认证保护**：复用既有站点登录 token（默认）或独立 GitHub OAuth（自托管模式）
- 🖥️ **多设备接入**：每台机器一条命令接入，SSH 反向隧道保持连接
- 🪟 **会话管理**：每台设备可铸造多个终端窗口（sessions.json 管理）
- 🎤 **语音通道**：浏览器麦克风 → agent 耳朵（mic_relay）
- 🧩 **Agent 友好**：提供 agent 接入示例，AI 可读可写终端

## 架构总览

```
┌────────────┐     HTTPS      ┌─────────────────────────────────┐
│  浏览器     │ ─────────────→ │ Cloudflare / nginx (公网入口)     │
│ (terminal) │                │  ├─ /terminal/  静态页 + API      │
└────────────┘                │  └─ /term/      ttyd WebSocket   │
                              └──────────┬──────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         │       服务器端 (server)         │
                         │  rtw_backend.py               │
                         │    ├─ 认证校验 (cookie/DB)     │
                         │    ├─ 设备注册 (machines.json) │
                         │    └─ 会话铸造 (sessions.json) │
                         │  ttyd → bin/web-term → tmux   │
                         └───────────────┬───────────────┘
                                         │ SSH 反向隧道 (rtwtun)
                         ┌───────────────┴───────────────┐
                         │       设备端 (你的电脑)         │
                         │  init.sh 安装接入              │
                         │  本机 ttyd :7681              │
                         │  tmux (终端会话)              │
                         └───────────────────────────────┘
```

**核心链路**：设备端跑真实终端（tmux）→ SSH 反向隧道暴露 → 服务器端 nginx/ttyd 转成网页 → 浏览器访问。

## 组件清单

| 文件 | 角色 |
|---|---|
| `rtw_backend.py` | HTTP 后端：认证校验、设备注册、会话铸造（Python 3.8+） |
| `standalone_auth.py` | 独立 GitHub OAuth 模式（`RTW_STANDALONE_AUTH=1` 启用） |
| `authcheck.py` | 认证检查辅助 |
| `bin/web-term` | tmux wrapper：`tmux new-session -A -s <sid>` |
| `mic_relay.js` | 语音中继：浏览器麦克风 → agent 耳朵（Node.js + ws） |
| `index.html` | 登录/设备/会话管理首页 |
| `t.html` / `ttyd-index.html` | 终端 UI（xterm.js 定制皮肤） |
| `xterm.js` / `xterm.css` / `xterm-fit.js` / `xterm-unicode11.js` | 终端前端库 |
| `ttyd-patch.js` | ttyd 前端补丁 |
| `init.sh` | 设备端接入脚本（注册 + 隧道 + 本机 ttyd） |
| `go.sh` | 接入命令示例 |
| `package.json` | mic_relay 依赖（ws） |
| `agent_example.py` | Agent 接入示例（如何让 AI 用这个终端） |
| `guide/` | 网页版使用指南 |
| `favicon.svg` / `authok.html` / `home.html` / `index-v0.0.1.html` | 历史/辅助页面 |

## 快速开始

### 服务器端（提供 rtw 服务的一方）

1. 部署 rtw_backend + nginx 路由 + ttyd 服务（见下方"认证与部署"）
2. 配置认证：默认复用你的站点登录 token（`blog_users` 表），或设 `RTW_STANDALONE_AUTH=1` 用独立 GitHub OAuth
3. nginx 路由示例：
   - `/terminal/` → 静态页 + `rtw_backend` API（`/api/*` → 127.0.0.1:8092 等）
   - `/term/` → ttyd WebSocket（升级协议）

### 设备端（接入你的电脑）

```bash
# 从网页 register 获取带 token 的完整命令，或：
curl -fsSL https://<server>/terminal/init.sh | RTW_TOKEN=xxx RTW_SERVER=<server> bash
```

脚本自动：安装依赖（ttyd/tmux）→ 生成隧道密钥 → 注册设备 → 建立反向隧道 → 拉起本机 ttyd。

## 认证机制（两种模式）

| 模式 | 启用方式 | 校验方式 |
|---|---|---|
| **站点复用**（默认） | 不设环境变量 | nginx `auth_request` → `GET /check` → 校验 `.c-n-b.space` token cookie（查 MySQL `blog_users`） |
| **独立 GitHub OAuth** | `RTW_STANDALONE_AUTH=1` | GitHub OAuth 授权码流程 → HMAC 签名 cookie |

安全要点（standalone 模式）：`RTW_SECRET` 必须设置（`openssl rand -hex 32`）；OAuth `state` 校验防 CSRF；cookie `Secure + HttpOnly + SameSite=Lax`。

## API

`rtw_backend.py` 提供（详见源码头部注释与 `docs/API.md`）：

| 端点 | 功能 |
|---|---|
| `GET /check` | nginx auth_request：校验登录 + 机器归属 |
| `GET /api/me` | 当前登录用户 `{user, name}` |
| `GET /api/machines` | 我绑定的设备列表 |
| `GET /api/sessions?m=<dev>` | 某设备下打开的终端窗口列表 |
| `GET /api/new?m=<dev>&name=` | 铸造新终端窗口，返回 `{id, url}` |

## 开源说明

本项目与 [cnb](https://github.com/ApolloZhangOnGithub/cnb)（Local-first project ownership for Claude）生态同源，是 cnb 平台的远程终端组件。

- 鉴权可复用你的站点体系（见上文认证）
- 也可完全自托管（standalone OAuth 模式）
- 运行时数据（`machines.json` / `sessions.json` / `agent_tokens.json`）不入库，部署时自行生成

## 安全提示

- 所有公网入口必须有认证（nginx auth_request 或 ttyd 白名单）
- 不要将 `machines.json`、`sessions.json`、token 文件提交到公开仓库
- ttyd 建议只绑 127.0.0.1，公网访问一律经 nginx + 认证
