import SwiftUI
import UIKit
import SwiftTerm

struct ContentView: View {
    @AppStorage("wsurl") private var url =
        "wss://<server>/m/<device>/ws?arg=<session>&agent_token=<token>"
    @State private var ws: TerminalWS? = nil

    var body: some View {
        if let ws = ws {
            TerminalScreen(ws: ws)
                .onExitCommand { ws.disconnect(); self.ws = nil }   // 菜单键返回配置
        } else {
            VStack(spacing: 28) {
                Text("Remote Terminal Web").font(.title)
                Text("把网页里 agent 命令给的 wss 地址填进来(或人类登录后的 /m/.../ws)")
                    .foregroundColor(.secondary)
                TextField("wss URL", text: $url)
                    .textFieldStyle(.roundedBorder).frame(width: 1300)
                Button("连接") {
                    let w = TerminalWS(urlString: url)
                    w.connect()
                    ws = w
                }.padding(.top, 10)
            }.padding(60)
        }
    }
}

/// 读 SwiftTerm 无界面引擎的缓冲区,用等宽文本渲染。够看、能连;要更精细(颜色/光标)再升级。
struct TerminalScreen: View {
    @ObservedObject var ws: TerminalWS
    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.ignoresSafeArea()
            ScrollView {
                Text(render())
                    .font(.system(size: 24, design: .monospaced))
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(20)
                    .id(ws.tick)                       // tick 变就重绘
            }
            KeyboardCatcher { ws.sendInput($0) }       // 隐形,只负责抓键盘
                .frame(width: 1, height: 1)
        }
    }
    private func render() -> String {
        let t = ws.terminal
        var s = ""
        for y in 0..<t.rows {
            if let line = t.getLine(row: y) {          // 若 API 名不同,按编译器调整
                for x in 0..<t.cols { s.append(line[x].getCharacter()) }
            }
            s += "\n"
        }
        return s
    }
}

// MARK: 蓝牙键盘捕获(pressesBegan + UIKey;个人 sideload 可用,不上架)
struct KeyboardCatcher: UIViewControllerRepresentable {
    let onKey: (String) -> Void
    func makeUIViewController(context: Context) -> KeyVC { let v = KeyVC(); v.onKey = onKey; return v }
    func updateUIViewController(_ vc: KeyVC, context: Context) { vc.onKey = onKey }
}

final class KeyVC: UIViewController {
    var onKey: ((String) -> Void)?
    override var canBecomeFirstResponder: Bool { true }
    override func viewDidAppear(_ animated: Bool) { super.viewDidAppear(animated); becomeFirstResponder() }

    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var handled = false
        for p in presses {
            guard let key = p.key else { continue }
            handled = true
            onKey?(translate(key))
        }
        if !handled { super.pressesBegan(presses, with: event) }
    }

    private func translate(_ key: UIKey) -> String {
        switch key.keyCode {
        case .keyboardReturnOrEnter:     return "\r"
        case .keyboardDeleteOrBackspace: return "\u{7f}"
        case .keyboardTab:               return "\t"
        case .keyboardEscape:            return "\u{1b}"
        case .keyboardUpArrow:           return "\u{1b}[A"
        case .keyboardDownArrow:         return "\u{1b}[B"
        case .keyboardLeftArrow:         return "\u{1b}[D"
        case .keyboardRightArrow:        return "\u{1b}[C"
        default:
            if key.modifierFlags.contains(.control),
               let c = key.charactersIgnoringModifiers.lowercased().first,
               let a = c.asciiValue, a >= 97, a <= 122 {
                return String(UnicodeScalar(a - 96))   // Ctrl+字母 -> 控制码
            }
            return key.characters
        }
    }
}
