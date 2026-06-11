#!/usr/bin/env bash
# Remote Terminal Web - 服务端一键安装(在你自己的 Linux 公网服务器上,以 root 运行)
# 前提:已装 nginx;域名已解析到本机;已有该域名的 TLS 证书(没有就先 certbot 签一张);
#       已在 GitHub 建好 OAuth App(回调填 https://<域名>/auth/callback)。
#
# 用法:
#   RTW_SERVER_HOST=term.example.com \
#   RTW_GITHUB_CLIENT_ID=xxx RTW_GITHUB_CLIENT_SECRET=yyy \
#   SSL_CERT=/etc/letsencrypt/live/term.example.com/fullchain.pem \
#   SSL_KEY=/etc/letsencrypt/live/term.example.com/privkey.pem \
#   bash install.sh
set -euo pipefail

APP=/opt/rtw
SRC="$(cd "$(dirname "$0")/.." && pwd)"   # 仓库根目录
say(){ printf "[install] %s\n" "$1"; }
need(){ [ -n "${!1:-}" ] || { echo "缺少环境变量 $1"; exit 1; }; }

need RTW_SERVER_HOST; need RTW_GITHUB_CLIENT_ID; need RTW_GITHUB_CLIENT_SECRET
need SSL_CERT; need SSL_KEY
[ "$(id -u)" = "0" ] || { echo "请用 root 运行"; exit 1; }
command -v nginx >/dev/null || { echo "请先安装 nginx"; exit 1; }
command -v python3 >/dev/null || { echo "请先安装 python3"; exit 1; }

SECRET="$(openssl rand -hex 32)"

say "1) 复制应用到 $APP"
mkdir -p "$APP/web"
cp "$SRC"/web/* "$APP/web/" 2>/dev/null || true
cp "$SRC"/server/rtw_backend.py "$SRC"/server/standalone_auth.py "$APP/"
for f in machines.json sessions.json reg_tokens.json rtw_sessions.json agent_tokens.json; do
  [ -f "$APP/$f" ] || echo "{}" > "$APP/$f"
done

say "2) 受限隧道用户 rtwtun"
id rtwtun >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin rtwtun
mkdir -p /home/rtwtun/.ssh && touch /home/rtwtun/.ssh/authorized_keys
chown -R rtwtun:rtwtun /home/rtwtun/.ssh && chmod 700 /home/rtwtun/.ssh && chmod 600 /home/rtwtun/.ssh/authorized_keys
grep -q "^GatewayPorts clientspecified" /etc/ssh/sshd_config || { echo "GatewayPorts clientspecified" >> /etc/ssh/sshd_config; systemctl reload sshd || systemctl reload ssh; }

say "3) systemd 后端服务(独立鉴权模式)"
cat > /etc/systemd/system/rtw-backend.service <<EOF
[Unit]
Description=Remote Terminal Web backend
After=network.target
[Service]
WorkingDirectory=$APP
Environment=RTW_STANDALONE_AUTH=1
Environment=RTW_SERVER_HOST=$RTW_SERVER_HOST
Environment=RTW_GITHUB_CLIENT_ID=$RTW_GITHUB_CLIENT_ID
Environment=RTW_GITHUB_CLIENT_SECRET=$RTW_GITHUB_CLIENT_SECRET
Environment=RTW_SECRET=$SECRET
ExecStart=/usr/bin/python3 $APP/rtw_backend.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
# 注:未写 User= 时 systemd 以 root 跑,后端才能改 authorized_keys / nginx map 并 reload(生产可收紧权限)

say "4) nginx 站点 + 动态端口映射"
echo "" > /etc/nginx/rtw-ports.map
cat > /etc/nginx/conf.d/00-rtw-map.conf <<'EOF'
map $mdev $mport { default 0; include /etc/nginx/rtw-ports.map; }
EOF
cat > /etc/nginx/conf.d/rtw.conf <<EOF
server {
  listen 80; server_name $RTW_SERVER_HOST; return 301 https://\$host\$request_uri;
}
server {
  listen 443 ssl; server_name $RTW_SERVER_HOST;
  ssl_certificate $SSL_CERT; ssl_certificate_key $SSL_KEY;

  location /terminal/ { alias $APP/web/; index home.html; try_files \$uri \$uri/ /home.html; add_header Cache-Control "no-cache"; }
  location = /terminal { return 301 /terminal/; }
  location /terminal/api/ { proxy_pass http://127.0.0.1:8092/api/; proxy_set_header Cookie \$http_cookie; }
  location /auth/ { proxy_pass http://127.0.0.1:8092/auth/; proxy_set_header Cookie \$http_cookie; }

  location = /__authcheck { internal; proxy_pass http://127.0.0.1:8092/check;
    proxy_pass_request_body off; proxy_set_header Content-Length ""; proxy_set_header X-Original-URI \$request_uri; }
  location @gh_login { return 302 https://$RTW_SERVER_HOST/terminal/; }

  location ~ ^/m/(?<mdev>[a-z0-9-]+)/(?<rest>.*)\$ {
    auth_request /__authcheck; error_page 401 = @gh_login;
    proxy_pass http://127.0.0.1:\$mport/\$rest\$is_args\$args;
    proxy_buffering off; proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection "upgrade";
    proxy_set_header Host \$host; proxy_read_timeout 86400s; proxy_send_timeout 86400s;
  }
}
EOF

say "5) 启动"
systemctl daemon-reload
systemctl enable --now rtw-backend
nginx -t && systemctl reload nginx

say "完成。打开 https://$RTW_SERVER_HOST/terminal/ 运行 login。"
say "接入机器:登录后运行 register,把一键命令拿到目标机执行。"
