#!/usr/bin/env bash
# 设备一键接入示例（在目标机器上运行）
# 注意：RTW_TOKEN 是设备注册令牌，请勿提交真实值——
# 在网页终端里运行 `register` 获取你自己的整条接入命令。
curl -fsSL https://<server>/terminal/init.sh | RTW_TOKEN=<register-token> RTW_SERVER=<server> bash
