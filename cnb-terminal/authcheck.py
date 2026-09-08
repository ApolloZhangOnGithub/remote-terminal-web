#!/usr/bin/env python3
"""WebTerm 鉴权 + 会话 ID 分配后端(Python 3.8 兼容)。
  GET /check       —— nginx auth_request:校验 .c-n-b.space token cookie + 机器归属
  GET /newsession  —— 登录后铸造一个唯一会话 ID,302 跳到 /t/?arg=<id>
                      (ID 进 URL → 刷新/断网重连回到同一会话;开新标签=新 ID=新会话)
"""
import http.server
import sqlite3
import json
import secrets
from http.cookies import SimpleCookie
from urllib.parse import urlparse

DB = "/opt/cnb-blog/data/blog.db"
MACHINES = "/opt/cnb-terminal/machines.json"
PORT = 8092
LOGIN = "https://platform.c-n-b.space/docs/auth/github?redirect=https://c-n-b.space/t/new"


def load_machines():
    try:
        with open(MACHINES) as f:
            return json.load(f)
    except Exception:
        return {}


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
    tok = c.get("token")
    return user_for_token(tok.value if tok else None)


def owners_for_uri(uri):
    machines = load_machines()
    if uri.startswith("/m/"):
        parts = uri.split("/")
        mid = parts[2] if len(parts) > 2 else ""
        m = machines.get(mid)
        return None if m is None else m.get("owners", [])
    m = machines.get("default")
    return m.get("owners", []) if m else []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/check":
            row = cookie_user(self)
            uri = self.headers.get("X-Original-URI", "/")
            owners = owners_for_uri(uri)
            if owners is None:
                self.send_response(404); self.end_headers(); return
            if row is None:
                self.send_response(401); self.end_headers(); return
            if owners and row[0] not in owners:
                self.send_response(403); self.end_headers(); return
            self.send_response(200)
            self.send_header("X-Auth-User", row[0])
            self.end_headers()
            return

        if path == "/newsession":
            row = cookie_user(self)
            if row is None:
                self.send_response(302)
                self.send_header("Location", LOGIN)
                self.end_headers()
                return
            sid = "s-" + secrets.token_hex(4)
            self.send_response(302)
            self.send_header("Location", "/t/?arg=%s" % sid)
            self.end_headers()
            return

        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
