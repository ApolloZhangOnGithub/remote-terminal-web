# Remote Terminal Web

在浏览器里安全地进入你自己的任意机器的终端。GitHub 登录,选设备,开会话。
无需 App、无需 VPN、目标机不开任何入站端口。自托管,谁的服务器都能跑。

当前版本:v0.1.0

## 目录
- [它是什么](#它是什么)
- [架构](#架构)
- [直接用(已托管实例)](#直接用已托管实例)
- [自己服务器部署](#自己服务器部署)
- [命令参考](#命令参考)
- [接入一台机器](#接入一台机器)
- [安全机制](#安全机制)
- [Agent API(给 AI agent 接入)](#agent-api给-ai-agent-接入)
- [路线图](#路线图)

## 它是什么
一套自托管的网页终端平台。你在浏览器里用 GitHub 登录,看到你绑定的机器,进入某台机器的某个 tmux 会话,在新标签里得到一个完整终端(带移动端按键栏)。程序始终跑在你自己的机器上,浏览器只是窗口。

## 架构
```
浏览器(网页终端 t.html: xterm + 移动端按键栏)
   │  https / wss
中转服务器(你的 VPS):nginx + 鉴权/注册后端(rtw_backend.py) + 受限隧道用户 rtwtun
   │  SSH 反向隧道(机器主动拨出,permitlisten 限定到本机分配的端口)
目标机器:ttyd(本地 127.0.0.1)→ tmux 常驻会话 → 你的程序
```
- 登录身份:复用一个 GitHub OAuth(token cookie 种在主域,全站共享)。
- 平台会话:`rtw_sess`,3 天过期(登录定期失效);与设备授权分离。
- 路由:`/m/<device-id>/` 经 nginx `map` 动态映射到该设备隧道端口。
- 会话:每个会话唯一 id,记录名称/创建/上次访问;id 进 URL,刷新重连不丢。

## 直接用(已托管实例)
1. 打开 `https://c-n-b.space/terminal/`,运行 `login` 用 GitHub 登录。
2. `ld` 看设备,`cd <设备>` 进入,`session --new` 新建会话(新标签打开),`ld` 后 `session <序号>` 进入已有会话。
3. 新增自己的机器:`register` 拿一键命令,在那台机器上跑。

## 访问与连通性
- **正常访问**:直接打开 `https://<server>/terminal/`。终端全程走 **443 / wss**,这是标准 HTTPS,绝大多数网络都能直接访问。
- **关于 80 端口**:若域名未在大陆 ICP 备案,80 端口可能显示备案提示页或被重定向——**这不影响终端**,因为终端不经过 80。
- **如果某个网络确实打不开**(个别 ISP 对未备案域名在 443 上偶发 RST):
  1. **自托管(最稳)**:在自己的服务器(可在用户所在地区)按下文部署,从根上避开。
  2. **用代理**:任意代理/科学上网工具把该域名走代理即可(对 wss 是 TLS 透传,不影响)。
  3. **运营者侧**:给域名做 ICP 备案,或把 Web 入口迁到香港/海外节点(免备案,当天可恢复)。SSH 反向隧道走 22 端口,迁移入口不影响已接入的设备。
- **自查**:`printf 'GET /terminal/ HTTP/1.1\r\nHost: <域名>\r\nConnection: close\r\n\r\n' | openssl s_client -quiet -connect <域名>:443` 能返回 200 即服务正常;若你本机带全局代理(如 Shadowrocket),用 `curl` 测 443 可能假阳性,以 openssl 直连握手为准。

## 自己服务器部署
前提:一台有公网 IP 的 Linux,nginx,python3,一个 GitHub OAuth App。

1. **GitHub OAuth App**:回调填 `https://<你的域名>/auth/callback`,记下 client id/secret。需要一个能在 `.<域名>` 全域种 token cookie、并能按 cookie 查出用户名的后端(参考 `server/` 里复用博客那套,或自己实现一个最小 OAuth 回调)。
2. **后端**:`server/rtw_backend.py` 跑成 systemd 服务,监听 `127.0.0.1:8092`,环境变量 `RTW_SERVER_HOST=<你的域名>`。它读 `/opt/<app>/machines.json|sessions.json|reg_tokens.json|rtw_sessions.json`。
3. **隧道用户**:`useradd -m -s /usr/sbin/nologin rtwtun`;sshd 开 `GatewayPorts clientspecified`。
4. **nginx**(关键片段):
   ```
   map $mdev $mport { default 0; include /etc/nginx/rtw-ports.map; }
   server {
     location /terminal/ { alias /opt/<app>/; add_header Cache-Control "no-cache"; }
     location /terminal/api/ { proxy_pass http://127.0.0.1:8092/api/; proxy_set_header Cookie $http_cookie; }
     location = /__authcheck { internal; proxy_pass http://127.0.0.1:8092/check;
       proxy_pass_request_body off; proxy_set_header Content-Length ""; proxy_set_header X-Original-URI $request_uri; }
     location @gh_login { return 302 https://<你的域名>/terminal/; }
     location ~ ^/m/(?<mdev>[a-z0-9-]+)/(?<rest>.*)$ {
       auth_request /__authcheck; error_page 401 = @gh_login;
       proxy_pass http://127.0.0.1:$mport/$rest$is_args$args;
       proxy_buffering off; proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";
       proxy_read_timeout 86400s;
     }
   }
   ```
5. 部署 `web/`(index.html、t.html、init.sh、authok.html、xterm.js、xterm.css、xterm-fit.js)到 `/opt/<app>/`。

## 命令参考(网页主页)
```
login        登录;login --switch 切换账号;login --logout 退出
register     生成一键命令,把一台新机器接入
ld           列出我的设备(实时在线状态)
device       device --remove <设备> 撤销一台设备的远程授权
cd <设备>    进入一台设备
ls           列出当前设备的会话
session <会话>          进入(新标签)
session --new [名字]    新建会话并进入
session --rename <会话> 名字   重命名会话
session --close <会话>  关闭会话
help / help --about / help --docs / clear / whoami
```
会话终端页(t.html)底部按键栏:Esc/Tab/Ctrl/Alt/方向键/Home/End/^C/^D/^Z/`C-b`(tmux 前缀)/`%`/`"`/`|`/`?`/`/`/`~`。
分屏直接用 tmux:`C-b %` 竖分、`C-b "` 横分、`C-b o` 切换、`C-b c` 新窗口。

## 接入一台机器
```
curl -fsSL https://<server>/terminal/init.sh | RTW_TOKEN=<token> RTW_SERVER=<server> bash
```
脚本做:装 ttyd/tmux、生成隧道密钥、注册(换 id+端口)、起 ttyd(本地)+ 反向隧道(开机自启、断线重连)。
macOS 额外:给 ttyd 授「完全磁盘访问」一次(远程才不被权限弹窗卡死),`sudo pmset -c sleep 0`。
机器若开了全局代理(Shadowrocket/Clash),把中转服务器加成 DIRECT,否则注册的 HTTPS 会被打断。

## 安全机制
- **登录定期失效**:平台会话 `rtw_sess` 3 天过期,过期需重新 GitHub 登录(设备隧道不受影响)。
- **撤销设备**:`device --remove` 删该设备的隧道公钥(从 rtwtun)、nginx 路由、配置,立即断access。
- **隧道最小权限**:rtwtun 无 shell;每台机器的 key 用 `no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:<port>"`,只能转发自己那个端口。
- **身份隔离**:每个接口都校验 GitHub 身份 + 平台会话,且只能操作自己 owner 的设备/会话。

## Agent API(给 AI agent 接入)
任何 agent(含 Claude Code)可接入某个会话:实时读终端屏幕(可滚动)、发命令/操作。**已实现并端到端验证。**

**用法(三步):**
1. 网页里运行 `agent <设备>`,拿到 token 和连接地址:
   ```
   agent 1
   token:   agt-XXXXXXXX
   ws_url:  wss://<server>/m/<device>/ws?arg=<会话id>&agent_token=agt-XXXXXXXX
   ```
   `<会话id>` 用 `session` 命令里看到的具体会话(如 `s-ab12cd34`);也可以是任意名字,会自动建一个同名 tmux 会话。
2. agent 用 WebSocket 连上面的地址(无需登录 cookie)。
3. 协议同 ttyd:
   - 连上后**首帧**发 `{"AuthToken":"","columns":N,"rows":M}`(JSON)
   - 收到帧首字节 `0` = 屏幕输出(累积即"当前屏幕 + 可滚动历史");`1` 标题、`2` 偏好可忽略
   - 发送 `0`+data = 键盘输入(命令末尾带 `\n`);`1`+JSON{columns,rows} = 改窗口大小

**参考客户端**:`web/agent_example.py`(也可在线下载 `https://<server>/terminal/agent_example.py`)
```
pip install websockets
python3 agent_example.py 'wss://<server>/m/<device>/ws?arg=<会话>&agent_token=<token>'
```
- 环境若有 TLS 证书校验问题(某些代理/沙箱),设环境变量 `RTW_INSECURE=1` 跳过校验。

**鉴权与权限**:token 由登录用户用 `agent <设备>` 签发,绑定到该设备的 owner;`agent --list` 列出、`agent --revoke <token>` 吊销;可在签发时带 `ttl`(秒)设过期。`/check` 同时接受登录 cookie 或 `agent_token`。

**协作**:人和 agent 可同时连同一会话(tmux 天然多客户端共享),互相看得到对方的命令和输出——你在网页里能看到 agent 打的命令,agent 也能看到你打的。

**只读模式**(规划中):需在前面加一层 WS 代理丢弃输入帧;当前 token 为读写。

## 路线图
- 会话活跃检测(tmux has-session,需设备侧上报小助手)
- Agent API 与 agent token 鉴权(见上)
- 设备侧管理(反向看访问日志、主动下线)
- session --close 真正杀掉目标机 tmux(需设备侧助手)
