# rtw Server 部署包

服务器端部署组件与配置模板（`cnb-terminal/` 代码 + `deploy/` 部署配置）。

## 架构（服务器视角）

```
公网浏览器
   │ HTTPS (域名 + Cloudflare 或直连)
   ▼
nginx ──┬─ /terminal/        → 静态页 + 前端
        ├─ /terminal/api/*   → rtw_backend (127.0.0.1:8092)   会话/设备/铸造
        ├─ /auth/*           → rtw_backend OAuth
        ├─ /__authcheck      → internal: rtw_backend /check    认证闸门
        ├─ /mic/<dev>        → mic relay (127.0.0.1:7690)      语音
        └─ /m/<dev>/*        → 设备本地端口(ports.map)         终端 WebSocket
                                ↑ 经 SSH 反向隧道(rwtun 用户)
设备端(你的电脑)
   └─ 反向隧道 -R <port>:localhost:7681 (ttyd) → server 的 rtwtun 端口
```

## 部署清单

### 1. rtwtun 隧道用户（接收设备反向隧道）

```bash
useradd -m -s /bin/bash rtwtun
# authorized_keys 限制: 只允许反向隧道 + 只允许 listen 指定端口
# /home/rtwtun/.ssh/authorized_keys 每行(示例):
# no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:PORT" ssh-ed25519 AAAA... rtw-<hostname>
```

### 2. 设备端口注册表 `/etc/nginx/rtw-ports.map`

```nginx
map $mdev $mport {
    default 0;
    # <设备ID> <该设备反向隧道的本地端口>;
    d-server01 7682;
    d-example   7700;
}
```

### 3. systemd 服务

| 服务 | 命令 | 角色 |
|---|---|---|
| `cnb-term-auth.service` | `python3 /opt/cnb-terminal/rtw_backend.py` | HTTP 后端 (8092)：认证/设备/会话/agent token |
| `rtw-ttyd-local.service` | `ttyd -p 7682 -I ttyd-index.html web-term` | 本机终端网页服务（server 自身终端） |
| `rtw-mic-relay.service` | `node mic_relay.js` | 浏览器语音 → agent 耳朵 |

模板见 `deploy/systemd/*.service`。

### 4. nginx 路由

完整模板见 `deploy/nginx/rtw-site.conf.example`。核心路由：

```nginx
# 认证闸门: 所有 /m/ 和 /mic/ 请求先过 auth_request
location = /__authcheck {
    internal;
    proxy_pass http://127.0.0.1:8092/check;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URI $request_uri;
    proxy_set_header X-Real-IP $remote_addr;
}

# 设备终端: /m/<dev>/<rest> → 按 ports.map 反代到设备本地端口
location ~ ^/m/(?<mdev>[a-z0-9-]+)/(?<rest>.*)$ {
    auth_request /__authcheck;
    error_page 401 = @gh_login;
    proxy_pass http://127.0.0.1:$mport/$rest$is_args$args;
    proxy_buffering off;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
}
```

### 5. 认证配置

| 模式 | 环境变量 | 校验 |
|---|---|---|
| 站点复用（默认） | 无 | `/check` 查站点登录 token（`blog_users` DB） |
| 独立 GitHub OAuth | `RTW_STANDALONE_AUTH=1` | GitHub 授权码 → HMAC cookie |

standalone 必须设 `RTW_SECRET`（`openssl rand -hex 32`），禁止默认值。

### 6. 运行时数据（部署时生成，不入库）

- `/opt/cnb-terminal/machines.json` — 设备注册表
- `/opt/cnb-terminal/sessions.json` — 会话（终端窗口）ID 注册
- `/opt/cnb-terminal/agent_tokens.json` / `reg_tokens.json` — 接入 token
- `/etc/nginx/term.htpasswd` — 可选 basic auth

## 安全清单

- [ ] nginx `auth_request` 保护所有 `/m/` `/mic/` 路由（无裸奔 ttyd）
- [ ] ttyd 只绑 `127.0.0.1`，公网一律经 nginx
- [ ] `RTW_STANDALONE_AUTH=1` 时 `RTW_SECRET` 必设、state 校验开
- [ ] 反向隧道 key 用 `permitlisten` 锁端口（不放开任意转发）
- [ ] 运行数据文件 `chmod 600`，不入 git
- [ ] cookie `Secure + HttpOnly + SameSite=Lax`
