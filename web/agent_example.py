#!/usr/bin/env python3
"""Remote Terminal Web - 参考 agent 客户端。
任何 AI agent(含 Claude Code)可据此接入一个会话:实时读终端屏幕、发命令。

用法:
  pip install websockets
  python3 agent_example.py wss://<server>/m/<device>/ws?arg=<session>'&'agent_token=<token>

协议(同 ttyd):
  收到帧首字节 '0' = 屏幕输出(其余 '1' 标题 '2' 偏好,忽略)
  发送 '0'+data = 键盘输入;'1'+JSON{columns,rows} = 改窗口大小;首帧发 JSON 鉴权/初始尺寸
"""
import asyncio
import os
import ssl
import sys
import json
import websockets


async def main(url):
    # TLS 容错:环境若有证书校验问题(如某些代理/沙箱),设 RTW_INSECURE=1 跳过校验
    ctx = None
    if url.startswith("wss"):
        ctx = ssl.create_default_context()
        if os.environ.get("RTW_INSECURE") == "1":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    async with websockets.connect(url, subprotocols=["tty"], max_size=None, ssl=ctx) as ws:
        # 1) 首帧:初始尺寸(agent_token 已在 URL 里,AuthToken 留空)
        await ws.send(json.dumps({"AuthToken": "", "columns": 120, "rows": 40}))

        screen = []  # agent 可在此累积/解析屏幕(可滚动历史)

        async def reader():
            async for msg in ws:
                data = msg if isinstance(msg, str) else msg.decode("utf-8", "ignore")
                if data[:1] == "0":
                    out = data[1:]
                    screen.append(out)
                    sys.stdout.write(out)
                    sys.stdout.flush()

        async def send_input(text):
            # 发送一条命令(末尾回车)
            await ws.send(b"0" + (text + "\n").encode())

        rt = asyncio.create_task(reader())
        # 演示:agent 发一条命令,然后持续读屏
        await asyncio.sleep(1)
        await send_input("echo agent-connected && uname -a")
        await asyncio.sleep(3)
        rt.cancel()
        print("\n--- 已累积屏幕字节:", sum(len(s) for s in screen), "---")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 agent_example.py '<wss-url-from-agent-command>'")
        sys.exit(1)
    try:
        asyncio.run(main(sys.argv[1]))            # python 3.7+
    except AttributeError:
        loop = asyncio.get_event_loop()           # python 3.6 兼容
        loop.run_until_complete(main(sys.argv[1]))
