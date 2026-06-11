# Apple TV (tvOS) 客户端

Apple TV 没有浏览器、也没有 WKWebView,所以网页终端那条路走不通。这个原生小 app 直接连
Remote Terminal Web 的会话 WebSocket(协议同 ttyd),把 Claude Code 的终端搬到电视上。
目标就一句:**能连上、能看输出、能用蓝牙键盘打**。

## 已核实的可行性
- WebSocket:`URLSessionWebSocketTask` 自 tvOS 13 原生支持。
- 蓝牙键盘:tvOS 支持,app 用 `pressesBegan` + `UIKey` 读硬件键盘(本 app 已实现)。
  注意:tvOS 通用键盘 API 在**上架审核**会被拒——**个人 sideload 不上架,不受影响**。
- 终端渲染:用 SwiftTerm 的**无界面引擎**(它声明支持 tvOS;但现成的 iOS TerminalView
  不保证能在 tvOS 编译,所以这里自己读缓冲区渲染,绕开不确定性)。

## 构建步骤
1. Xcode 新建 **tvOS App**(SwiftUI 生命周期),把这 4 个文件加进 target:
   `RTWtvOSApp.swift`、`ContentView.swift`、`TerminalWS.swift`(本 README 不用加)。
2. 加依赖 SwiftTerm:File ▸ Add Packages ▸ `https://github.com/migueldeicaza/SwiftTerm`。
3. 编译。若 SwiftTerm 的 `TerminalDelegate` 还缺某个方法、或缓冲区读法(`getLine(row:)` /
   `line[x].getCharacter()`)签名对不上,**按编译器提示补空实现/改名**——核心连接逻辑不动。
4. 真机:Apple TV 与 Mac 同网,Xcode 选中 Apple TV 设备,Run(免费开发者账号签名 7 天有效,
   付费账号 1 年)。
5. 给 Apple TV 配一个蓝牙键盘(设置 ▸ 遥控器与设备 ▸ 蓝牙)。

## 用法
- 启动后填会话 wss 地址:
  - 给 **agent** 用:网页里 `agent new <设备>` 拿到的 `wss://.../m/<设备>/ws?arg=<会话>&agent_token=<token>`;
  - 或人类自己:登录后该会话的 `/m/<设备>/ws?arg=<会话>`(带 cookie 的浏览器才有,tvOS 用 agent_token 更简单)。
- 连上后蓝牙键盘直接打;菜单键返回重填地址。

## 还没做 / 可升级
- 颜色、光标、滚动是简化版(纯文本渲染)。要更还原 Claude Code 的 TUI,后续可换成
  SwiftTerm 的 TerminalView(等它的 tvOS UIView 就绪)或自己画 attributed buffer。
- 语音输入暂不做(手机/电脑有系统输入法语音;tvOS 要的话再单独接)。
