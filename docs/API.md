# rtw API 文档

`rtw_backend.py` 与 `authcheck.py` 提供的 HTTP 端点。认证默认复用站点登录 cookie（`.c-n-b.space` token），nginx `auth_request` 校验；`RTW_STANDALONE_AUTH=1` 时走独立 GitHub OAuth。

## 认证端点

| 端点 | 方法 | 功能 |
|---|---|---|
| `/check` | GET | nginx auth_request：校验登录 cookie + 设备归属（返回 200/401） |
| `/auth/github` | GET | 发起 GitHub OAuth 授权（standalone 模式） |
| `/auth/callback` | GET | GitHub OAuth 回调：换 token、签 cookie（含 state 防 CSRF 校验） |
| `/api/login-start` | GET | 启动登录流程（OAuth 授权 URL 重定向） |
| `/api/login-sessions` | GET | 当前登录会话列表 |
| `/api/login-revoke` | GET | 吊销指定登录会话 |

## 设备管理

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/machines` | GET | 我绑定的设备列表 |
| `/api/register` | POST | 注册新设备（接入 init.sh 调用） |
| `/api/register-token` | GET | 获取注册 token（给 init.sh 用） |
| `/api/device-rename` | GET | 重命名设备 |
| `/api/device-rename-internal` | GET | 内部设备重命名（服务间调用） |
| `/api/mark-device` | GET | 标记设备状态 |
| `/api/device-remove` | GET | 移除设备 |
| `/api/init-script` | GET | 返回设备接入脚本（init.sh 内容，带设备参数） |
| `/api/provision` | GET | 设备预配置信息 |

## 会话（终端窗口）管理

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/sessions?m=<dev>` | GET | 某设备下打开的终端窗口列表 |
| `/api/new?m=<dev>&name=` | GET | 铸造新终端窗口，返回 `{id, url}` |
| `/api/rename` | GET | 重命名终端窗口 |
| `/api/close` | GET | 关闭终端窗口 |
| `/api/open` | GET | 打开/恢复终端窗口 |

## Agent 接入

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/agent-token` | GET | 为某设备铸造 agent 接入 token |
| `/api/agent-list` | GET | agent token 列表 |
| `/api/agent-revoke` | GET | 吊销 agent token |

## 消息 / 杂项

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/me` | GET | 当前登录用户 `{user, name}` |
| `/api/config` | GET | 前端配置 |
| `/api/messages` | GET | 消息列表（站点消息） |
| `/api/msg-send` | GET | 发送消息 |

## WebSocket 终端协议

终端数据流走 ttyd WebSocket（URL 形态：`wss://<server>/m/<device>/ws?arg=<session>&agent_token=<token>`），协议同 ttyd：

```
首字节 '0' = 屏幕输出（其余 '1' 标题 / '2' 偏好，忽略）
发送 '0' + data = 键盘输入
发送 '1' + JSON {columns, rows} = 改窗口大小
首帧发 JSON 完成鉴权 + 初始尺寸
```

## 会话 ID 机制（authcheck.py）

- 登录后 `GET /newsession` 铸造唯一会话 ID → 302 跳 `/t/?arg=<id>`
- ID 进 URL：刷新/断网重连回到**同一会话**；开新标签 = 新 ID = 新会话
- 会话记录在 `sessions.json`，设备归属记录在 `machines.json`，均由服务端铸造管理
