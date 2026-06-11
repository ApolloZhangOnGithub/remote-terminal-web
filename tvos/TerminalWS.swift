import Foundation
import SwiftTerm

/// 连接 Remote Terminal Web 的会话 WebSocket,说 ttyd 协议,把输出喂给无界面的 SwiftTerm 引擎。
/// 渲染交给 SwiftUI 读 terminal 的缓冲区(见 ContentView)。连接层是确定可用的;
/// SwiftTerm 的 delegate/缓冲区 API 在不同版本签名略有差异,编译器报哪个就按提示补/改。
final class TerminalWS: NSObject, ObservableObject, TerminalDelegate {
    let terminal: Terminal
    @Published var tick = 0          // 每次有新输出 +1,触发 SwiftUI 重绘
    @Published var connected = false

    private var ws: URLSessionWebSocketTask?
    private let urlString: String

    init(urlString: String, cols: Int = 100, rows: Int = 30) {
        self.urlString = urlString
        terminal = Terminal(delegate: nil, options: TerminalOptions(cols: cols, rows: rows))
        super.init()
        terminal.delegate = self
    }

    func connect() {
        guard let url = URL(string: urlString) else { return }
        let session = URLSession(configuration: .default)
        ws = session.webSocketTask(with: url, protocols: ["tty"])
        ws?.resume()
        // ttyd 首帧:鉴权(agent_token 已在 URL 里,AuthToken 留空)+ 初始尺寸
        rawSend(string: "{\"AuthToken\":\"\",\"columns\":\(terminal.cols),\"rows\":\(terminal.rows)}")
        connected = true
        receiveLoop()
    }

    func disconnect() { ws?.cancel(with: .goingAway, reason: nil); connected = false }

    private func receiveLoop() {
        ws?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let msg):
                switch msg {
                case .data(let d):   self.handle([UInt8](d))
                case .string(let s): self.handle([UInt8](s.utf8))
                @unknown default:    break
                }
                self.receiveLoop()
            case .failure:
                DispatchQueue.main.async { self.connected = false }
            }
        }
    }

    private func handle(_ bytes: [UInt8]) {
        guard let cmd = bytes.first else { return }
        if cmd == UInt8(ascii: "0") {                 // OUTPUT
            terminal.feed(byteArray: ArraySlice(bytes.dropFirst()))
            DispatchQueue.main.async { self.tick &+= 1 }
        }                                             // '1' 标题 / '2' 偏好:忽略
    }

    /// 键盘输入 -> 发给终端(0 + data)
    func sendInput(_ s: String) { rawSend(bytes: [UInt8]("0".utf8) + [UInt8](s.utf8)) }
    func sendBytes(_ b: [UInt8]) { rawSend(bytes: [UInt8]("0".utf8) + b) }

    private func rawSend(string: String) { ws?.send(.string(string)) { _ in } }
    private func rawSend(bytes: [UInt8])  { ws?.send(.data(Data(bytes))) { _ in } }

    // MARK: TerminalDelegate(终端要回送数据时,当作输入发出去)
    func send(source: Terminal, data: ArraySlice<UInt8>) { sendBytes([UInt8](data)) }
    func sizeChanged(source: Terminal, newCols: Int, newRows: Int) {}
    func setTerminalTitle(source: Terminal, title: String) {}
    func scrolled(source: Terminal, yDisp: Int) {}
    func bufferActivated(source: Terminal) {}
    func bell(source: Terminal) {}
    func mouseModeChanged(source: Terminal) {}
    func hostCurrentDirectoryUpdated(source: Terminal, directory: String?) {}
    func cursorStyleChanged(source: Terminal, newStyle: CursorStyle) {}
    // 若编译器提示还缺别的 TerminalDelegate 方法,补成空实现即可。
}
