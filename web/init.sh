#!/usr/bin/env bash
# Remote Terminal Web - 本地接入脚本(开源,服务器地址可配)
# 用法(从网页 register 复制):
#   curl -fsSL https://<server>/terminal/init.sh | RTW_TOKEN=xxx RTW_SERVER=<server> bash
set -euo pipefail

RTW_SERVER="${RTW_SERVER:-c-n-b.space}"
RTW_TOKEN="${RTW_TOKEN:-}"
RTW_LOCAL_PORT="${RTW_LOCAL_PORT:-7681}"     # 本机 ttyd 端口,默认 7681
RTW_DIR="$HOME/.rtw"
say(){ printf "[RTW] %s\n" "$1"; }
die(){ printf "[RTW] 错误: %s\n" "$1" >&2; exit 1; }

[ -n "$RTW_TOKEN" ] || die "缺少 RTW_TOKEN(在网页里运行 register 获取整条接入命令)"
OS="$(uname -s)"

# 1) 依赖
if [ "$OS" = "Darwin" ]; then
  command -v brew >/dev/null || die "需要 Homebrew(https://brew.sh)"
  command -v ttyd >/dev/null || { say "安装 ttyd"; brew install ttyd >/dev/null; }
  command -v tmux >/dev/null || { say "安装 tmux"; brew install tmux >/dev/null; }
else
  command -v tmux >/dev/null || { say "安装 tmux"; sudo apt-get update -qq && sudo apt-get install -y tmux >/dev/null; }
  command -v ttyd >/dev/null || die "请先安装 ttyd(apt/yum 或 https://github.com/tsl0922/ttyd)"
fi
TTYD_BIN="$(command -v ttyd)"; SSH_BIN="$(command -v ssh)"; TMUX_BIN="$(command -v tmux)"

# 2) 隧道密钥
mkdir -p "$RTW_DIR"
KEY="$RTW_DIR/tunnel_key"
[ -f "$KEY" ] || ssh-keygen -t ed25519 -N "" -f "$KEY" -C "rtw-$(hostname)" >/dev/null
PUB="$(cat "$KEY.pub")"

# 3) 注册
HOSTN="$(hostname | sed 's/\.local$//')"
say "向 $RTW_SERVER 注册本机($HOSTN)"
RESP="$(curl -fsS -X POST "https://$RTW_SERVER/terminal/api/register" \
  -H "Content-Type: application/json" \
  -d "{\"token\":\"$RTW_TOKEN\",\"pubkey\":\"$PUB\",\"name\":\"$HOSTN\"}")" || die "注册请求失败"
get(){ printf '%s' "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }
DID="$(get device_id)"; TPORT="$(get tunnel_port)"; TUSER="$(get tunnel_user)"; THOST="$(get tunnel_host)"
[ -n "$DID" ] && [ -n "$TPORT" ] || die "注册返回异常: $RESP"
say "已注册:设备 id $DID,隧道端口 $TPORT"

# 4) ttyd 包装(按 URL 的 arg 重连同名 tmux 会话,状态不丢)
WRAP="$RTW_DIR/web-term"
cat > "$WRAP" <<WEOF
#!/bin/bash
S="\${1:-main}"; S="\$(printf '%s' "\$S" | tr -cd 'a-zA-Z0-9_-' | cut -c1-32)"; [ -z "\$S" ] && S=main
exec "$TMUX_BIN" new-session -A -s "\$S"
WEOF
chmod +x "$WRAP"

TUN_OPTS="-N -R 127.0.0.1:$TPORT:localhost:$RTW_LOCAL_PORT -i $KEY -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o BatchMode=yes"

# 5) 常驻服务
if [ "$OS" = "Darwin" ]; then
  LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
  cat > "$LA/com.rtw.ttyd.plist" <<EOF
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
  cat > "$LA/com.rtw.tunnel.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.rtw.tunnel</string>
  <key>ProgramArguments</key><array>
    <string>$SSH_BIN</string>$(for o in $TUN_OPTS; do printf '<string>%s</string>' "$o"; done)<string>$TUSER@$THOST</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>StandardErrorPath</key><string>/tmp/rtw-tunnel.err</string>
</dict></plist>
EOF
  launchctl unload "$LA/com.rtw.ttyd.plist" 2>/dev/null || true; launchctl load "$LA/com.rtw.ttyd.plist"
  launchctl unload "$LA/com.rtw.tunnel.plist" 2>/dev/null || true; launchctl load "$LA/com.rtw.tunnel.plist"
  say "macOS:请把 ttyd 加入 完全磁盘访问(否则远程访问受保护目录会被弹窗卡死):$TTYD_BIN"
  say "并执行一次 sudo pmset -c sleep 0 关闭插电休眠"
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
else
  SD="$HOME/.config/systemd/user"; mkdir -p "$SD"
  cat > "$SD/rtw-ttyd.service" <<EOF
[Unit]
Description=RTW ttyd
[Service]
ExecStart=$TTYD_BIN -W -a -i 127.0.0.1 -p $RTW_LOCAL_PORT $WRAP
Restart=always
[Install]
WantedBy=default.target
EOF
  cat > "$SD/rtw-tunnel.service" <<EOF
[Unit]
Description=RTW reverse tunnel
[Service]
ExecStart=$SSH_BIN $TUN_OPTS $TUSER@$THOST
Restart=always
RestartSec=10
[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now rtw-ttyd.service rtw-tunnel.service
  say "Linux:已用 systemd --user 启动;长期可达建议 loginctl enable-linger $USER"
fi

say "完成。回到 https://$RTW_SERVER/terminal/ 运行 ld 就能看到这台设备。"
