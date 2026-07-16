#!/usr/bin/env python3
"""Remote Terminal Web 后端(Python 3.8 兼容)。
鉴权复用博客 .c-n-b.space 的 token cookie;设备来自 machines.json;
会话(每台设备打开的终端窗口)记录在 sessions.json,由本服务铸造 ID 管理。

  GET /check                 nginx auth_request:校验登录 + 机器归属
  GET /api/me                返回当前登录用户 {user, name} 或 401
  GET /api/machines          我绑定的设备列表
  GET /api/sessions?m=<dev>  某设备下我打开的终端窗口列表
  GET /api/new?m=<dev>&name= 在某设备下铸造一个新终端窗口,返回 {id, url}
"""
import http.server
import sqlite3
import json
import os
import re
import time
import secrets
import subprocess
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs
import urllib.request

DB = "/opt/cnb-blog/data/blog.db"
MACHINES = "/opt/cnb-terminal/machines.json"
SESSIONS = "/opt/cnb-terminal/sessions.json"
REG_TOKENS = "/opt/cnb-terminal/reg_tokens.json"
RTW_SESSIONS = "/opt/cnb-terminal/rtw_sessions.json"
AGENT_TOKENS = "/opt/cnb-terminal/agent_tokens.json"
FINGERPRINTS = "/opt/cnb-terminal/fingerprints.json"
MESSAGES = "/opt/cnb-terminal/messages.json"
AUTHKEYS = "/home/rtwtun/.ssh/authorized_keys"
PORTS_MAP = "/etc/nginx/rtw-ports.map"
SERVER_HOST = os.environ.get("RTW_SERVER_HOST", "c-n-b.space")
LOCAL_TTYD_PORT = 7681      # 每台机器本地 ttyd 端口(固定)
LOGIN_TTL = 365 * 86400     # 登录会话有效期(1 年)
PORT = 8092

# 鉴权模式:默认复用既有(博客)登录;开源自托管设 RTW_STANDALONE_AUTH=1 用独立 GitHub OAuth
STANDALONE = os.environ.get("RTW_STANDALONE_AUTH") == "1"
if STANDALONE:
    import standalone_auth
# 前端从 /api/config 取登录入口,与后端鉴权方式解耦
BLOG_LOGIN_URL = "https://platform.c-n-b.space/docs/auth/github?redirect=https://%s/terminal/api/login-start" % SERVER_HOST


def login_url():
    return "/auth/github" if STANDALONE else BLOG_LOGIN_URL


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def user_for_token(token):
    if not token:
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=3)
        row = con.execute(
            "SELECT username, display_name FROM blog_users WHERE token = ?",
            (token,),
        ).fetchone()
        con.close()
        return row
    except Exception:
        return None


def cookie_user(handler):
    c = SimpleCookie(handler.headers.get("Cookie", ""))
    if STANDALONE:
        lc = c.get("rtw_login")
        u = standalone_auth.verify_cookie(lc.value) if lc else None
        return (u, u) if u else None      # 独立模式:用户名即 GitHub login
    tok = c.get("token")
    return user_for_token(tok.value if tok else None)


def get_rtw_sess(handler):
    c = SimpleCookie(handler.headers.get("Cookie", ""))
    sc = c.get("rtw_sess")
    if not sc:
        return None
    rec = _load(RTW_SESSIONS, {}).get(sc.value)
    if not rec or rec.get("expires", 0) < int(time.time()):
        return None
    return rec


UNREGISTERED_TTL = 24 * 3600


def _session_alive(rec, now):
    if rec.get("expires", 0) < now:
        return False
    fp = rec.get("fp", "")
    if fp:
        fps = _load(FINGERPRINTS, {})
        if fp in fps:
            return True
    # 没有匹配设备的 session,24h 后过期
    return now - rec.get("login", 0) < UNREGISTERED_TTL


def valid_user(handler):
    if STANDALONE:
        c = SimpleCookie(handler.headers.get("Cookie", ""))
        sc = c.get("rtw_sess")
        if not sc:
            return None
        rec = _load(RTW_SESSIONS, {}).get(sc.value)
        if not rec or not _session_alive(rec, int(time.time())):
            return None
        return (rec["user"], rec.get("name", rec["user"]))
    row = cookie_user(handler)
    if not row:
        return None
    sess = get_rtw_sess(handler)
    if not sess:
        return None
    su = (sess.get("user") or "").lower()
    ru = (row[0] or "").lower()
    if su != ru and su != ru.rstrip("0123456789"):
        return None
    return row


def port_alive(port):
    import socket
    if not port:
        return False
    try:
        s = socket.create_connection(("127.0.0.1", int(port)), timeout=0.3)
        s.close()
        return True
    except Exception:
        return False


def my_machines(username):
    machines = _load(MACHINES, {})
    out = []
    ulow = username.lower()
    ustrip = ulow.rstrip("0123456789")
    for mid, m in machines.items():
        owners = [o.lower() for o in m.get("owners", [])]
        if ulow in owners or ustrip in owners:
            out.append({
                "id": mid,
                "name": m.get("name", mid),
                "online": port_alive(m.get("port")),
            })
    return out


def owns_machine(username, mid):
    machines = _load(MACHINES, {})
    m = machines.get(mid)
    return bool(m) and username in m.get("owners", [])


def _short_ua(ua):
    if not ua:
        return "未知"
    os_name = "Unknown"
    if "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"
    elif "Mac OS" in ua or "Macintosh" in ua:
        os_name = "Mac"
    elif "Android" in ua:
        os_name = "Android"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Linux" in ua:
        os_name = "Linux"
    browser = "Unknown"
    if "Edg/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua and "Safari/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    elif "Firefox/" in ua:
        browser = "Firefox"
    return "%s/%s" % (browser, os_name)


def machine_url(mid, sid):
    return "/t/?arg=%s" % sid if mid == "default" else "/m/%s/?arg=%s" % (mid, sid)


def touch_session(mid, sid):
    """每次实际连接终端时更新会话的上次访问时间。"""
    if not sid:
        return
    ss = _load(SESSIONS, {})
    s = ss.get(mid, {}).get(sid)
    if s:
        s["last_access"] = int(time.time())
        _save(SESSIONS, ss)


def sanitize_pubkey(pk):
    """严格校验 SSH 公钥并重建(只保留 类型+base64,丢弃任何注释/换行),防 authorized_keys 注入。"""
    pk = (pk or "").strip()
    if "\n" in pk or "\r" in pk:
        return None
    parts = pk.split()
    if len(parts) < 2:
        return None
    ktype, kdata = parts[0], parts[1]
    allowed = ("ssh-ed25519", "ssh-rsa",
               "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521")
    if ktype not in allowed:
        return None
    if not re.match(r'^[A-Za-z0-9+/]{40,1000}={0,3}$', kdata):
        return None
    return ktype + " " + kdata


BLOG_GITHUB_ID = os.environ.get("BLOG_GITHUB_CLIENT_ID", "")
BLOG_GITHUB_SECRET = os.environ.get("BLOG_GITHUB_CLIENT_SECRET", "")


def _blog_exchange_code(code):
    proxy = os.environ.get("RTW_GITHUB_PROXY", "")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy})) if proxy else urllib.request.build_opener()
    try:
        data = json.dumps({"client_id": BLOG_GITHUB_ID, "client_secret": BLOG_GITHUB_SECRET, "code": code}).encode()
        req = urllib.request.Request("https://github.com/login/oauth/access_token", data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        resp_data = json.loads(opener.open(req, timeout=10).read())
        access = resp_data.get("access_token")
        if not access:
            print("[blog-oauth] token exchange failed: %s" % resp_data, flush=True)
            return None
        ureq = urllib.request.Request("https://api.github.com/user",
            headers={"Authorization": "Bearer " + access, "Accept": "application/json"})
        info = json.loads(opener.open(ureq, timeout=10).read())
        login = info.get("login")
        if not login:
            return None
        return {"login": login, "name": info.get("name") or login, "id": info.get("id"), "avatar_url": info.get("avatar_url")}
    except Exception as e:
        print("[blog-oauth] exception: %s" % e, flush=True)
        return None


def _make_self_contained_init(did, port, server_host, privkey):
    return '''#!/usr/bin/env bash
# RTW 自包含初始化脚本(服务端预注册,无需回调)
set -euo pipefail
RTW_DIR="$HOME/.rtw"
RTW_CONF="$RTW_DIR/config"
say(){ printf "[RTW] %%s\\n" "$1"; }
die(){ printf "[RTW] 错误: %%s\\n" "$1" >&2; exit 1; }
OS="$(uname -s)"

# 1) 依赖
if [ "$OS" = "Darwin" ]; then
  command -v brew >/dev/null || die "需要 Homebrew(https://brew.sh)"
  command -v ttyd >/dev/null || { say "安装 ttyd"; brew install ttyd >/dev/null; }
  command -v tmux >/dev/null || { say "安装 tmux"; brew install tmux >/dev/null; }
else
  command -v tmux >/dev/null || { say "安装 tmux"; sudo apt-get update -qq && sudo apt-get install -y tmux >/dev/null; }
  command -v ttyd >/dev/null || die "请先安装 ttyd"
fi
TTYD_BIN="$(command -v ttyd)"; SSH_BIN="$(command -v ssh)"; TMUX_BIN="$(command -v tmux)"

# 2) 写入预生成的密钥和配置
mkdir -p "$RTW_DIR"
KEY="$RTW_DIR/tunnel_key"
cat > "$KEY" << 'KEYEOF'
%(privkey)s
KEYEOF
chmod 600 "$KEY"

cat > "$RTW_CONF" << 'CONFEOF'
DID="%(did)s"
TPORT="%(port)d"
TUSER="rtwtun"
THOST="%(server)s"
RTW_SERVER="%(server)s"
RTW_LOCAL_PORT="7681"
CONFEOF

DID="%(did)s"; TPORT="%(port)d"; RTW_LOCAL_PORT="7681"
say "设备已预注册:$DID,端口 $TPORT"

# 3) ttyd 包装
WRAP="$RTW_DIR/web-term"
cat > "$WRAP" << 'WEOF'
#!/bin/bash
S="${1:-main}"; S="$(printf '%%s' "$S" | tr -cd 'a-zA-Z0-9_-' | cut -c1-32)"; [ -z "$S" ] && S=main
exec "$TMUX_BIN" new-session -A -s "$S"
WEOF
chmod +x "$WRAP"

TUN_OPTS="-N -R 127.0.0.1:$TPORT:localhost:$RTW_LOCAL_PORT -i $KEY -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o BatchMode=yes"

# 4) 常驻服务
if [ "$OS" = "Darwin" ]; then
  LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
  cat > "$LA/com.rtw.ttyd.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.rtw.ttyd</string>
  <key>ProgramArguments</key><array>
    <string>$TTYD_BIN</string><string>-W</string><string>-a</string>
    <string>-i</string><string>127.0.0.1</string><string>-p</string><string>$RTW_LOCAL_PORT</string>
    <string>$WRAP</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>StandardErrorPath</key><string>/tmp/rtw-ttyd.err</string>
</dict></plist>
EOF
  cat > "$LA/com.rtw.tunnel.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.rtw.tunnel</string>
  <key>ProgramArguments</key><array>
    <string>$SSH_BIN</string>$(for o in $TUN_OPTS; do printf '<string>%%s</string>' "$o"; done)<string>rtwtun@%(server)s</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>StandardErrorPath</key><string>/tmp/rtw-tunnel.err</string>
</dict></plist>
EOF
  launchctl unload "$LA/com.rtw.ttyd.plist" 2>/dev/null || true; launchctl load "$LA/com.rtw.ttyd.plist"
  launchctl unload "$LA/com.rtw.tunnel.plist" 2>/dev/null || true; launchctl load "$LA/com.rtw.tunnel.plist"
  say "macOS:请把 ttyd 加入 完全磁盘访问:$TTYD_BIN"
  say "并执行一次 sudo pmset -c sleep 0 关闭插电休眠"
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
else
  SD="$HOME/.config/systemd/user"; mkdir -p "$SD"
  cat > "$SD/rtw-ttyd.service" << EOF
[Unit]
Description=RTW ttyd
[Service]
ExecStart=$TTYD_BIN -W -a -i 127.0.0.1 -p $RTW_LOCAL_PORT $WRAP
Restart=always
[Install]
WantedBy=default.target
EOF
  cat > "$SD/rtw-tunnel.service" << EOF
[Unit]
Description=RTW reverse tunnel
[Service]
ExecStart=$SSH_BIN $TUN_OPTS rtwtun@%(server)s
Restart=always
RestartSec=10
[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now rtw-ttyd.service rtw-tunnel.service
  say "Linux:已用 systemd --user 启动"
fi

# 5) 上报主机名
HNAME="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo '新设备')"
curl -fsSk "https://%(server)s/terminal/api/device-rename-internal?m=%(did)s&name=$HNAME&key=%(did)s" >/dev/null 2>&1 || true
say "完成。设备名:$HNAME (可在网页用 rn 改名)"
''' % {"did": did, "port": port, "server": server_host, "privkey": privkey.strip()}


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path == "/api/device-rename-internal":
            mid = (q.get("m") or [""])[0]
            name = (q.get("name") or [""])[0].strip()[:40]
            key = (q.get("key") or [""])[0]
            if mid and name and key == mid:
                machines = _load(MACHINES, {})
                m = machines.get(mid)
                if m and m.get("name") == "新设备":
                    m["name"] = name
                    _save(MACHINES, machines)
                    return self._json(200, {"ok": True})
            return self._json(200, {"ok": False})

        if path == "/api/config":
            return self._json(200, {"login_url": login_url(), "version": "0.1.0"})

        if STANDALONE and path == "/auth/github":
            state = secrets.token_urlsafe(12)
            self.send_response(302)
            self.send_header("Set-Cookie", "oauth_state=%s; Path=/; Max-Age=600; HttpOnly; SameSite=Lax" % state)
            self.send_header("Location", standalone_auth.authorize_url(state))
            self.end_headers()
            return

        if path == "/auth/callback":
            code = (q.get("code") or [""])[0]
            state = (q.get("state") or [""])[0]
            is_rtw = state.startswith("rtw_")
            if not is_rtw:
                blog_result = _blog_exchange_code(code) if code else None
                if not blog_result:
                    self.send_response(302)
                    self.send_header("Location", "https://blog.c-n-b.space/login")
                    self.end_headers()
                    return
                import urllib.request as ur
                try:
                    blog_url = "http://127.0.0.1:8090/auth/github-proxy-callback"
                    data = json.dumps({"login": blog_result["login"], "name": blog_result["name"],
                                       "id": blog_result["id"], "avatar_url": blog_result.get("avatar_url", ""),
                                       "state": state}).encode()
                    req = ur.Request(blog_url, data=data, headers={"Content-Type": "application/json", "Cookie": self.headers.get("Cookie", "")})
                    resp = ur.urlopen(req, timeout=10)
                    self.send_response(resp.status)
                    for h in ("Set-Cookie", "Location"):
                        for val in resp.headers.get_all(h) or []:
                            self.send_header(h, val)
                    self.end_headers()
                    body = resp.read()
                    if body:
                        self.wfile.write(body)
                except Exception as e:
                    self.send_response(302)
                    self.send_header("Location", "https://blog.c-n-b.space/login")
                    self.end_headers()
                return
            result = standalone_auth.exchange_code(code) if (STANDALONE and code) else None
            self.send_response(302)
            if result:
                login, display_name = result["login"], result["name"]
                now = int(time.time())
                sess = {k: v for k, v in _load(RTW_SESSIONS, {}).items() if v.get("expires", 0) > now}
                ua = self.headers.get("User-Agent", "")
                # 复用已有 session
                sid = None
                for k, v in sess.items():
                    if v.get("user") == login:
                        sid = k
                        v["expires"] = now + LOGIN_TTL
                        v["name"] = display_name
                        if not v.get("ua") and ua:
                            v["ua"] = ua[:120]
                        break
                if not sid:
                    sid = secrets.token_urlsafe(24)
                    sess[sid] = {"user": login, "name": display_name, "expires": now + LOGIN_TTL, "login": now, "ua": ua[:120]}
                _save(RTW_SESSIONS, sess)
                self.send_header("Set-Cookie", "rtw_sess=%s; Path=/; Domain=.c-n-b.space; Secure; HttpOnly; SameSite=Lax; Max-Age=%d" % (sid, LOGIN_TTL))
            self.send_header("Location", "https://%s/terminal/authok.html" % SERVER_HOST)
            self.end_headers()
            return

        # nginx auth_request
        if path == "/check":
            uri = self.headers.get("X-Original-URI", "/")
            pp = urlparse(uri)
            qq = parse_qs(pp.query)
            machines = _load(MACHINES, {})
            if pp.path.startswith("/m/"):
                parts = pp.path.split("/")
                mid = parts[2] if len(parts) > 2 else ""
                m = machines.get(mid)
                owners = None if m is None else m.get("owners", [])
            else:
                mid = "default"
                m = machines.get("default")
                owners = m.get("owners", []) if m else []
            if owners is None:
                return self._send_empty(404)
            # agent token 通道(无需登录 cookie):token 必须属于该设备且未过期
            agent_tok = (qq.get("agent_token") or [""])[0]
            if agent_tok:
                now = int(time.time())
                ats = _load(AGENT_TOKENS, {})
                at = ats.get(agent_tok)
                if at and at.get("device") == mid and at.get("owner") in (owners or []) \
                        and (at.get("expires", 0) == 0 or at["expires"] > now):
                    # 使用溯源:次数 / 上次时间 / 来源 IP / 会话
                    at["use_count"] = at.get("use_count", 0) + 1
                    at["last_used"] = now
                    at["last_ip"] = self.headers.get("X-Real-IP", "") or self.headers.get("X-Forwarded-For", "")
                    at["last_session"] = (qq.get("arg") or [""])[0]
                    _save(AGENT_TOKENS, ats)
                    touch_session(mid, at["last_session"])
                    self.send_response(200)
                    self.send_header("X-Auth-User", at["owner"])
                    self.end_headers()
                    return
                return self._send_empty(401)
            # 人类登录通道
            row = valid_user(self)
            if row is None:
                return self._send_empty(401)
            if owners and row[0] not in owners:
                return self._send_empty(403)
            touch_session(mid, (qq.get("arg") or [""])[0])
            self.send_response(200)
            self.send_header("X-Auth-User", row[0])
            self.end_headers()
            return

        if path == "/api/me":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            # 自动清理同用户的旧 session，只保留当前
            cur_sid = SimpleCookie(self.headers.get("Cookie", "")).get("rtw_sess")
            if cur_sid:
                sessions = _load(RTW_SESSIONS, {})
                stale = [k for k, v in sessions.items() if v.get("user") == row[0] and k != cur_sid.value]
                if stale:
                    for k in stale:
                        del sessions[k]
                    _save(RTW_SESSIONS, sessions)
            return self._json(200, {"user": row[0], "name": row[1]})

        if path == "/api/login-sessions":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            now = int(time.time())
            cur_sid = SimpleCookie(self.headers.get("Cookie", "")).get("rtw_sess")
            cur_sid = cur_sid.value if cur_sid else ""
            sessions = _load(RTW_SESSIONS, {})
            fps = _load(FINGERPRINTS, {})
            fp_to_dev = {fp: did for fp, did in fps.items()}
            machines = _load(MACHINES, {})
            out = []
            for sid, s in sessions.items():
                if s.get("user") == row[0] and _session_alive(s, now):
                    ua = s.get("ua", "")
                    browser = _short_ua(ua)
                    device_name = ""
                    sfp = s.get("fp", "")
                    if sfp and sfp in fp_to_dev:
                        m = machines.get(fp_to_dev[sfp])
                        if m:
                            device_name = m.get("name", "")
                    out.append({"sid": sid[:8],
                                "login": s.get("last_seen", s.get("login")),
                                "registered": s.get("login"),
                                "browser": browser, "device_name": device_name,
                                "current": sid == cur_sid})
            return self._json(200, {"sessions": out})

        if path == "/api/login-revoke":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            target = (q.get("sid") or [""])[0]
            if not target:
                return self._json(400, {"error": "missing_sid"})
            sessions = _load(RTW_SESSIONS, {})
            matches = [k for k in sessions if k.startswith(target) and sessions[k].get("user") == row[0]]
            if not matches:
                return self._json(404, {"error": "not_found"})
            for k in matches:
                del sessions[k]
            _save(RTW_SESSIONS, sessions)
            return self._json(200, {"revoked": len(matches)})

        if path == "/api/messages":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            cur_device = (q.get("device") or [""])[0]
            msgs = _load(MESSAGES, {})
            all_msgs = msgs.get(row[0], [])
            visible = [m for m in all_msgs if m.get("public") or not m.get("to") or m.get("to") == cur_device or m.get("from_id") == cur_device]
            # 记录当前设备的首次阅读时间
            now = int(time.time())
            dirty = False
            if cur_device:
                for m in visible:
                    if m.get("from_id") != cur_device:
                        reads = m.setdefault("read_by", {})
                        if cur_device not in reads:
                            reads[cur_device] = now
                            dirty = True
            if dirty:
                _save(MESSAGES, msgs)
            return self._json(200, {"messages": visible[-50:]})

        if path == "/api/msg-send":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            text = (q.get("text") or [""])[0].strip()[:500]
            device = (q.get("device") or [""])[0][:40]
            to_device = (q.get("to") or [""])[0][:40]
            if not text:
                return self._json(400, {"error": "empty"})
            machines = _load(MACHINES, {})
            from_name = machines.get(device, {}).get("name", device) if device else "web"
            to_name = machines.get(to_device, {}).get("name", to_device) if to_device else ""
            msg = {"from": from_name, "from_id": device, "text": text, "ts": int(time.time()), "public": not to_device}
            if to_device:
                msg["to"] = to_device
                msg["to_name"] = to_name
            msgs = _load(MESSAGES, {})
            if row[0] not in msgs:
                msgs[row[0]] = []
            msgs[row[0]].append(msg)
            msgs[row[0]] = msgs[row[0]][-200:]
            _save(MESSAGES, msgs)
            return self._json(200, {"ok": True})

        if path == "/api/machines":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            fp = (q.get("fp") or [""])[0]
            # 把 fingerprint 和 UA 补到当前 session 上
            if fp:
                c = SimpleCookie(self.headers.get("Cookie", ""))
                sc = c.get("rtw_sess")
                if sc:
                    sessions = _load(RTW_SESSIONS, {})
                    s = sessions.get(sc.value)
                    if s:
                        if s.get("fp") != fp:
                            s["fp"] = fp
                        ua = self.headers.get("User-Agent", "")
                        if not s.get("ua") and ua:
                            s["ua"] = ua[:120]
                        s["last_seen"] = int(time.time())
                        _save(RTW_SESSIONS, sessions)
            result = {"machines": my_machines(row[0])}
            if fp:
                fps = _load(FINGERPRINTS, {})
                matched = fps.get(fp)
                if matched and owns_machine(row[0], matched):
                    result["this_device"] = matched
            return self._json(200, result)

        if path == "/api/sessions":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            mid = (q.get("m") or ["default"])[0]
            if not owns_machine(row[0], mid):
                return self._json(403, {"error": "not_your_machine"})
            sessions = _load(SESSIONS, {})
            devsess = sessions.get(mid, {})
            mine = []
            for sid, s in devsess.items():
                if s.get("owner") == row[0]:
                    mine.append({"id": sid, "name": s.get("name", sid),
                                 "created": s.get("created", 0),
                                 "last_access": s.get("last_access", s.get("created", 0)),
                                 "url": machine_url(mid, sid)})
            mine.sort(key=lambda x: x.get("last_access", 0), reverse=True)
            return self._json(200, {"machine": mid, "sessions": mine})

        if path == "/api/new":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            mid = (q.get("m") or ["default"])[0]
            if not owns_machine(row[0], mid):
                return self._json(403, {"error": "not_your_machine"})
            name = (q.get("name") or [""])[0].strip()[:40]
            sid = "s-" + secrets.token_hex(4)
            now = int(time.time())
            sessions = _load(SESSIONS, {})
            dev = sessions.setdefault(mid, {})
            if not name:
                name = "会话" + str(len(dev) + 1)
            dev[sid] = {"name": name, "owner": row[0], "created": now, "last_access": now}
            _save(SESSIONS, sessions)
            return self._json(200, {"id": sid, "name": name, "url": machine_url(mid, sid)})

        if path == "/api/rename":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            mid = (q.get("m") or ["default"])[0]
            sid = (q.get("s") or [""])[0]
            name = (q.get("name") or [""])[0].strip()[:40]
            if not owns_machine(row[0], mid):
                return self._json(403, {"error": "not_your_machine"})
            if not name:
                return self._json(400, {"error": "empty_name"})
            sessions = _load(SESSIONS, {})
            s = sessions.get(mid, {}).get(sid)
            if not s or s.get("owner") != row[0]:
                return self._json(404, {"error": "no_such_session"})
            s["name"] = name
            _save(SESSIONS, sessions)
            return self._json(200, {"id": sid, "name": name})

        if path == "/api/device-rename":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            mid = (q.get("m") or [""])[0]
            name = (q.get("name") or [""])[0].strip()[:40]
            if not owns_machine(row[0], mid):
                return self._json(403, {"error": "not_your_machine"})
            if not name:
                return self._json(400, {"error": "empty_name"})
            machines = _load(MACHINES, {})
            machines[mid]["name"] = name
            _save(MACHINES, machines)
            return self._json(200, {"id": mid, "name": name})

        if path == "/api/mark-device":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            mid = (q.get("m") or [""])[0]
            fp = (q.get("fp") or [""])[0]
            if not fp or not mid:
                return self._json(400, {"error": "missing_params"})
            if not owns_machine(row[0], mid):
                return self._json(403, {"error": "not_your_machine"})
            fps = _load(FINGERPRINTS, {})
            # 清掉指向同一设备的旧 fingerprint
            fps = {k: v for k, v in fps.items() if v != mid}
            fps[fp] = mid
            _save(FINGERPRINTS, fps)
            return self._json(200, {"ok": True, "device": mid})

        if path == "/api/close":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            mid = (q.get("m") or ["default"])[0]
            sid = (q.get("s") or [""])[0]
            if not owns_machine(row[0], mid):
                return self._json(403, {"error": "not_your_machine"})
            sessions = _load(SESSIONS, {})
            dev = sessions.get(mid, {})
            if sid in dev and dev[sid].get("owner") == row[0]:
                del dev[sid]
                _save(SESSIONS, sessions)
                return self._json(200, {"closed": sid})
            return self._json(404, {"error": "no_such_session"})

        if path == "/api/open":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            mid = (q.get("m") or ["default"])[0]
            sid = (q.get("s") or [""])[0]
            if not owns_machine(row[0], mid):
                return self._json(403, {"error": "not_your_machine"})
            sessions = _load(SESSIONS, {})
            s = sessions.get(mid, {}).get(sid)
            if s and s.get("owner") == row[0]:
                s["last_access"] = int(time.time())
                _save(SESSIONS, sessions)
            return self._json(200, {"url": machine_url(mid, sid)})

        if path == "/api/register-token":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            now = int(time.time())
            toks = _load(REG_TOKENS, {})
            # 清理过期 + 同一用户只保留一个活跃 token(新的替掉旧的)
            toks = {k: v for k, v in toks.items()
                    if v.get("expires", 0) > now and v.get("owner") != row[0]}
            tk = secrets.token_urlsafe(18)
            toks[tk] = {"owner": row[0], "expires": now + 1800}
            _save(REG_TOKENS, toks)
            cmd = "curl -fsSL https://%s/terminal/init.sh | RTW_TOKEN=%s RTW_SERVER=%s bash" % (SERVER_HOST, tk, SERVER_HOST)
            return self._json(200, {"token": tk, "command": cmd, "expires_in": 1800})

        if path.startswith("/api/init-script"):
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            tk = (q.get("token") or [""])[0]
            now = int(time.time())
            toks = _load(REG_TOKENS, {})
            info = toks.get(tk)
            if not info or info.get("expires", 0) < now or info.get("owner") != row[0]:
                return self._json(403, {"error": "invalid_or_expired_token"})
            owner = info["owner"]
            del toks[tk]
            _save(REG_TOKENS, toks)
            # 服务端预注册:生成密钥、分配端口、写 authorized_keys
            import tempfile
            td = tempfile.mkdtemp()
            kp = os.path.join(td, "key")
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", kp, "-C", "rtw-pre"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            with open(kp) as f:
                privkey = f.read()
            with open(kp + ".pub") as f:
                pubkey = f.read().strip()
            os.unlink(kp); os.unlink(kp + ".pub"); os.rmdir(td)
            clean_pk = sanitize_pubkey(pubkey)
            if not clean_pk:
                return self._json(500, {"error": "keygen_failed"})
            machines = _load(MACHINES, {})
            # 查是否已有同 owner 未绑定 pubkey 的设备(不重复建)
            used = {m.get("port") for m in machines.values() if m.get("port")}
            port = 7700
            while port in used:
                port += 1
            did = "d-" + secrets.token_hex(4)
            machines[did] = {"owners": [owner], "port": port, "name": "新设备", "online": False, "pubkey": clean_pk}
            _save(MACHINES, machines)
            with open(AUTHKEYS, "a") as f:
                f.write('no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:%d" %s rtw-%s\n' % (port, clean_pk, did))
            with open(PORTS_MAP, "a") as f:
                f.write("%s %d;\n" % (did, port))
            try:
                subprocess.run(["nginx", "-s", "reload"], timeout=10, check=False)
            except Exception:
                pass
            # 生成自包含 init 脚本(不需要回调服务器)
            script = _make_self_contained_init(did, port, SERVER_HOST, privkey)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-shellscript")
            self.send_header("Content-Disposition", "attachment; filename=rtw-init.sh")
            self.end_headers()
            self.wfile.write(script.encode())
            return

        if path == "/api/provision":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            name = (q.get("name") or [""])[0].strip()[:40]
            owner = row[0]
            import tempfile
            td = tempfile.mkdtemp()
            kp = os.path.join(td, "key")
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", kp, "-C", "rtw-pre"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            with open(kp) as f:
                privkey = f.read()
            with open(kp + ".pub") as f:
                pubkey = f.read().strip()
            os.unlink(kp); os.unlink(kp + ".pub"); os.rmdir(td)
            clean_pk = sanitize_pubkey(pubkey)
            if not clean_pk:
                return self._json(500, {"error": "keygen_failed"})
            machines = _load(MACHINES, {})
            used = {m.get("port") for m in machines.values() if m.get("port")}
            port = 7700
            while port in used:
                port += 1
            did = "d-" + secrets.token_hex(4)
            machines[did] = {"owners": [owner], "port": port, "name": name or "新设备", "online": False, "pubkey": clean_pk}
            _save(MACHINES, machines)
            with open(AUTHKEYS, "a") as f:
                f.write('no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:%d" %s rtw-%s\n' % (port, clean_pk, did))
            with open(PORTS_MAP, "a") as f:
                f.write("%s %d;\n" % (did, port))
            try:
                subprocess.run(["nginx", "-s", "reload"], timeout=10, check=False)
            except Exception:
                pass
            return self._json(200, {
                "device_id": did,
                "tunnel_port": port,
                "tunnel_user": "rtwtun",
                "tunnel_host": SERVER_HOST,
                "private_key": privkey.strip(),
                "local_ttyd_port": LOCAL_TTYD_PORT,
                "setup": [
                    "1. Save private_key to ~/.rtw/tunnel_key (chmod 600)",
                    "2. Install ttyd and tmux if not present",
                    "3. Start ttyd: ttyd -W -a -i 127.0.0.1 -p %d ~/.rtw/web-term" % LOCAL_TTYD_PORT,
                    "4. Create ~/.rtw/web-term: #!/bin/bash\\nS=\"${1:-main}\"; S=$(printf '%%s' \"$S\" | tr -cd 'a-zA-Z0-9_-' | cut -c1-32); [ -z \"$S\" ] && S=main; exec tmux new-session -A -s \"$S\"",
                    "5. Start reverse tunnel: ssh -N -R 127.0.0.1:%d:localhost:%d -i ~/.rtw/tunnel_key -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes rtwtun@%s" % (port, LOCAL_TTYD_PORT, SERVER_HOST),
                    "6. Set up as persistent services (launchd on macOS, systemd on Linux)",
                ],
                "rename_url": "/terminal/api/device-rename-internal?m=%s&name=<hostname>&key=%s" % (did, did),
            })

        if path == "/api/login-start":
            # GitHub OAuth 回跳到这里:有博客身份就签发平台登录会话,再回主页
            row = cookie_user(self)
            now = int(time.time())
            headers = [("Location", "/terminal/authok.html")]
            if row is not None:
                sess = {k: v for k, v in _load(RTW_SESSIONS, {}).items() if v.get("expires", 0) > now}
                # 复用已有 session，不重复创建
                sid = None
                for k, v in sess.items():
                    if v.get("user") == row[0]:
                        sid = k
                        v["expires"] = now + LOGIN_TTL
                        break
                if not sid:
                    sid = secrets.token_urlsafe(24)
                    sess[sid] = {"user": row[0], "expires": now + LOGIN_TTL, "login": now}
                _save(RTW_SESSIONS, sess)
                headers.append(("Set-Cookie", "rtw_sess=%s; Path=/; Domain=.c-n-b.space; Secure; HttpOnly; SameSite=Lax; Max-Age=%d" % (sid, LOGIN_TTL)))
            self.send_response(302)
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            return

        if path == "/api/device-remove":
            # 撤销一台设备:删配置 + 删隧道公钥 + 删路由 + reload(取消远程鉴权)
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            did = (q.get("id") or [""])[0]
            machines = _load(MACHINES, {})
            m = machines.get(did)
            if not m or row[0] not in m.get("owners", []):
                return self._json(403, {"error": "not_your_device"})
            del machines[did]
            _save(MACHINES, machines)
            ss = _load(SESSIONS, {})
            ss.pop(did, None)
            _save(SESSIONS, ss)
            try:
                lines = open(AUTHKEYS).read().splitlines()
                keep = [l for l in lines if not l.rstrip().endswith("rtw-" + did)]
                with open(AUTHKEYS, "w") as f:
                    f.write(("\n".join(keep) + "\n") if keep else "")
            except Exception:
                pass
            try:
                lines = open(PORTS_MAP).read().splitlines()
                keep = [l for l in lines if not l.strip().startswith(did + " ")]
                with open(PORTS_MAP, "w") as f:
                    f.write(("\n".join(keep) + "\n") if keep else "")
            except Exception:
                pass
            try:
                subprocess.run(["nginx", "-s", "reload"], timeout=10, check=False)
            except Exception:
                pass
            return self._json(200, {"removed": did})

        if path == "/api/agent-token":
            # 签发 agent token;label 备注,ttl 秒(0=不过期)。完整串只返回这一次
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            dev = (q.get("device") or [""])[0]
            if not owns_machine(row[0], dev):
                return self._json(403, {"error": "not_your_device"})
            label = (q.get("label") or [""])[0].strip()[:40]
            try:
                ttl = int((q.get("ttl") or ["0"])[0] or 0)
            except Exception:
                ttl = 0
            now = int(time.time())
            ats = {k: v for k, v in _load(AGENT_TOKENS, {}).items() if v.get("expires", 0) == 0 or v["expires"] > now}
            tok = "agt-" + secrets.token_urlsafe(20)
            tid = secrets.token_hex(4)
            ats[tok] = {"tid": tid, "owner": row[0], "device": dev, "label": label,
                        "expires": (now + ttl) if ttl else 0, "created": now,
                        "use_count": 0, "last_used": 0, "last_ip": "", "last_session": ""}
            _save(AGENT_TOKENS, ats)
            ws = "wss://%s/m/%s/ws?arg=<会话id>&agent_token=%s" % (SERVER_HOST, dev, tok)
            return self._json(200, {"token": tok, "tid": tid, "device": dev, "ws_url": ws})

        if path == "/api/agent-list":
            # 列出我的 token + 使用溯源(不含完整串,只给 tid)
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            now = int(time.time())
            ats = _load(AGENT_TOKENS, {})
            mine = [{"tid": v.get("tid", ""), "device": v.get("device"), "label": v.get("label", ""),
                     "expires": v.get("expires", 0), "created": v.get("created", 0),
                     "use_count": v.get("use_count", 0), "last_used": v.get("last_used", 0),
                     "last_ip": v.get("last_ip", ""), "last_session": v.get("last_session", "")}
                    for v in ats.values()
                    if v.get("owner") == row[0] and (v.get("expires", 0) == 0 or v["expires"] > now)]
            mine.sort(key=lambda x: x.get("created", 0), reverse=True)
            return self._json(200, {"tokens": mine})

        if path == "/api/agent-revoke":
            # 按 tid 前缀吊销:0 个匹配 404,多个匹配 409,唯一 200
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            key = (q.get("token") or [""])[0].strip()
            if not key:
                return self._json(400, {"error": "empty"})
            ats = _load(AGENT_TOKENS, {})
            matches = [tok for tok, v in ats.items()
                       if v.get("owner") == row[0] and v.get("tid", "").startswith(key)]
            if not matches:
                return self._json(404, {"error": "no_match"})
            if len(matches) > 1:
                return self._json(409, {"error": "ambiguous"})
            del ats[matches[0]]
            _save(AGENT_TOKENS, ats)
            return self._json(200, {"revoked": key})

        self._send_empty(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/register":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(raw or b"{}")
            except Exception:
                req = {}
            tk = req.get("token", "")
            clean_pk = sanitize_pubkey(req.get("pubkey", ""))
            name = str(req.get("name", "机器"))[:40] or "机器"
            now = int(time.time())
            toks = _load(REG_TOKENS, {})
            info = toks.get(tk)
            if not info or info.get("expires", 0) < now:
                return self._json(403, {"error": "invalid_or_expired_token"})
            if not clean_pk:
                return self._json(400, {"error": "bad_pubkey"})
            owner = info["owner"]
            del toks[tk]
            _save(REG_TOKENS, toks)
            machines = _load(MACHINES, {})
            # pubkey 已注册过 → 返回已有设备,不重复建
            existing = None
            for mid, m in machines.items():
                if m.get("pubkey") == clean_pk and owner in m.get("owners", []):
                    existing = (mid, m)
                    break
            if existing:
                did, m = existing
                return self._json(200, {
                    "device_id": did, "tunnel_port": m["port"],
                    "tunnel_user": "rtwtun", "tunnel_host": SERVER_HOST,
                    "local_ttyd_port": LOCAL_TTYD_PORT,
                    "already_registered": True,
                })
            used = {m.get("port") for m in machines.values() if m.get("port")}
            port = 7700
            while port in used:
                port += 1
            did = "d-" + secrets.token_hex(4)
            machines[did] = {"owners": [owner], "port": port, "name": name, "online": False, "pubkey": clean_pk}
            _save(MACHINES, machines)
            with open(AUTHKEYS, "a") as f:
                f.write('no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:%d" %s rtw-%s\n' % (port, clean_pk, did))
            with open(PORTS_MAP, "a") as f:
                f.write("%s %d;\n" % (did, port))
            try:
                subprocess.run(["nginx", "-s", "reload"], timeout=10, check=False)
            except Exception:
                pass
            return self._json(200, {
                "device_id": did, "tunnel_port": port,
                "tunnel_user": "rtwtun", "tunnel_host": SERVER_HOST,
                "local_ttyd_port": LOCAL_TTYD_PORT,
            })
        self._send_empty(404)

    def _send_empty(self, code):
        self.send_response(code)
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
