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

DB = "/opt/cnb-blog/data/blog.db"
MACHINES = "/opt/cnb-terminal/machines.json"
SESSIONS = "/opt/cnb-terminal/sessions.json"
REG_TOKENS = "/opt/cnb-terminal/reg_tokens.json"
RTW_SESSIONS = "/opt/cnb-terminal/rtw_sessions.json"
AGENT_TOKENS = "/opt/cnb-terminal/agent_tokens.json"
AUTHKEYS = "/home/rtwtun/.ssh/authorized_keys"
PORTS_MAP = "/etc/nginx/rtw-ports.map"
SERVER_HOST = os.environ.get("RTW_SERVER_HOST", "c-n-b.space")
LOCAL_TTYD_PORT = 7681      # 每台机器本地 ttyd 端口(固定)
LOGIN_TTL = 3 * 86400       # 网页登录会话有效期(3 天,过期需重新 GitHub 登录)
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


def valid_user(handler):
    row = cookie_user(handler)
    if not row:
        return None
    if STANDALONE:
        return row      # 独立模式:rtw_login 本身就是带签名+3天过期的会话
    # 复用模式:博客身份有效 且 平台登录会话未过期(3 天)
    sess = get_rtw_sess(handler)
    if not sess or sess.get("user") != row[0]:
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
    for mid, m in machines.items():
        if username in m.get("owners", []):
            out.append({
                "id": mid,
                "name": m.get("name", mid),
                "online": port_alive(m.get("port")),   # 隧道端口在监听 = 在线
            })
    return out


def owns_machine(username, mid):
    machines = _load(MACHINES, {})
    m = machines.get(mid)
    return bool(m) and username in m.get("owners", [])


def machine_url(mid, sid):
    return "/t/?arg=%s" % sid if mid == "default" else "/m/%s/?arg=%s" % (mid, sid)


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

        if path == "/api/config":
            return self._json(200, {"login_url": login_url(), "version": "0.1.0"})

        if STANDALONE and path == "/auth/github":
            state = secrets.token_urlsafe(12)
            self.send_response(302)
            self.send_header("Set-Cookie", "oauth_state=%s; Path=/; Max-Age=600; HttpOnly; SameSite=Lax" % state)
            self.send_header("Location", standalone_auth.authorize_url(state))
            self.end_headers()
            return

        if STANDALONE and path == "/auth/callback":
            code = (q.get("code") or [""])[0]
            user = standalone_auth.exchange_code(code) if code else None
            self.send_response(302)
            if user:
                self.send_header("Set-Cookie", "rtw_login=%s; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=%d"
                                 % (standalone_auth.make_cookie(user), standalone_auth.SESSION_TTL))
            self.send_header("Location", "/terminal/authok.html")
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
            self.send_response(200)
            self.send_header("X-Auth-User", row[0])
            self.end_headers()
            return

        if path == "/api/me":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            return self._json(200, {"user": row[0], "name": row[1]})

        if path == "/api/machines":
            row = valid_user(self)
            if row is None:
                return self._json(401, {"error": "not_logged_in"})
            return self._json(200, {"machines": my_machines(row[0])})

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
            toks = {k: v for k, v in _load(REG_TOKENS, {}).items() if v.get("expires", 0) > now}
            tk = secrets.token_urlsafe(18)
            toks[tk] = {"owner": row[0], "expires": now + 1800}
            _save(REG_TOKENS, toks)
            cmd = "curl -fsSL https://%s/terminal/init.sh | RTW_TOKEN=%s RTW_SERVER=%s bash" % (SERVER_HOST, tk, SERVER_HOST)
            return self._json(200, {"token": tk, "command": cmd, "expires_in": 1800})

        if path == "/api/login-start":
            # GitHub OAuth 回跳到这里:有博客身份就签发平台登录会话(3 天),再回主页
            row = cookie_user(self)
            now = int(time.time())
            headers = [("Location", "/terminal/authok.html")]
            if row is not None:
                sid = secrets.token_urlsafe(24)
                sess = {k: v for k, v in _load(RTW_SESSIONS, {}).items() if v.get("expires", 0) > now}
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
            pubkey = (req.get("pubkey", "") or "").strip()
            name = str(req.get("name", "机器"))[:40] or "机器"
            now = int(time.time())
            toks = _load(REG_TOKENS, {})
            info = toks.get(tk)
            if not info or info.get("expires", 0) < now:
                return self._json(403, {"error": "invalid_or_expired_token"})
            if not pubkey.startswith("ssh-"):
                return self._json(400, {"error": "bad_pubkey"})
            owner = info["owner"]
            del toks[tk]
            _save(REG_TOKENS, toks)
            machines = _load(MACHINES, {})
            used = {m.get("port") for m in machines.values() if m.get("port")}
            port = 7700
            while port in used:
                port += 1
            did = "d-" + secrets.token_hex(4)
            machines[did] = {"owners": [owner], "port": port, "name": name, "online": False}
            _save(MACHINES, machines)
            with open(AUTHKEYS, "a") as f:
                f.write('no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:%d" %s rtw-%s\n' % (port, pubkey, did))
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
