#!/usr/bin/env python3
"""Remote Terminal Web - 独立鉴权(开源自托管用,无需任何数据库)。

GitHub OAuth 登录 + HMAC 签名 cookie 会话。自托管者只需在 GitHub 建一个 OAuth App,
把 client id/secret 和一个随机密钥放进环境变量即可,不依赖博客或其它后端。

环境变量:
  RTW_GITHUB_CLIENT_ID       GitHub OAuth App client id
  RTW_GITHUB_CLIENT_SECRET   GitHub OAuth App client secret
  RTW_SECRET                 签名会话 cookie 的密钥(openssl rand -hex 32)
  RTW_SERVER_HOST            你的域名,如 term.example.com
GitHub OAuth App 回调填:https://<RTW_SERVER_HOST>/auth/callback

后端接法(rtw_backend 设 RTW_STANDALONE_AUTH=1 时):
  - GET /auth/github   -> 302 跳 GitHub 授权(本模块 authorize_url)
  - GET /auth/callback -> exchange_code 拿 GitHub 用户名 -> make_cookie 种 rtw_login cookie
  - 各接口的身份校验改用 verify_cookie(读 rtw_login cookie)
"""
import os
import hmac
import time
import json
import base64
import hashlib
import urllib.request

CLIENT_ID = os.environ.get("RTW_GITHUB_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("RTW_GITHUB_CLIENT_SECRET", "")
SECRET = os.environ.get("RTW_SECRET", "change-me-please").encode()
HOST = os.environ.get("RTW_SERVER_HOST", "localhost")
SESSION_TTL = 365 * 86400


CALLBACK_HOST = os.environ.get("RTW_CALLBACK_HOST", HOST)


def authorize_url(state):
    cb = "https://%s/auth/callback" % CALLBACK_HOST
    return ("https://github.com/login/oauth/authorize"
            "?client_id=%s&redirect_uri=%s&scope=read:user&state=rtw_%s" % (CLIENT_ID, cb, state))


def is_rtw_state(state):
    return state.startswith("rtw_") if state else False


def exchange_code(code):
    """用 OAuth code 换 GitHub 用户信息;返回 (login, display_name) 或 None。"""
    try:
        data = json.dumps({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": code}).encode()
        req = urllib.request.Request(
            "https://github.com/login/oauth/access_token", data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        access = json.loads(urllib.request.urlopen(req, timeout=10).read()).get("access_token")
        if not access:
            return None
        ureq = urllib.request.Request(
            "https://api.github.com/user",
            headers={"Authorization": "Bearer " + access, "Accept": "application/json"})
        info = json.loads(urllib.request.urlopen(ureq, timeout=10).read())
        login = info.get("login")
        name = info.get("name") or login
        return (login, name) if login else None
    except Exception:
        return None


def make_cookie(username):
    payload = ("%s|%d" % (username, int(time.time()) + SESSION_TTL)).encode()
    sig = hmac.new(SECRET, payload, hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + sig


def verify_cookie(value):
    """校验 rtw_login cookie,返回用户名或 None。"""
    try:
        b64, sig = value.split(".", 1)
        pad = "=" * (-len(b64) % 4)
        payload = base64.urlsafe_b64decode(b64 + pad)
        if not hmac.compare_digest(hmac.new(SECRET, payload, hashlib.sha256).hexdigest()[:32], sig):
            return None
        username, exp = payload.decode().rsplit("|", 1)
        if int(exp) < int(time.time()):
            return None
        return username
    except Exception:
        return None
