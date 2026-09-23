// EAS Почта — a thin native window (WKWebView) around the local eas-mail server.
// It starts the server if needed, shows the UI, and stops the server on quit.
import Cocoa
import WebKit
import Darwin

let port: UInt16 = 8780
let home = NSHomeDirectory()
let projectDir: String = {
    // Dist builds ship eas-bridge inside the app bundle.
    if let res = Bundle.main.resourcePath {
        let bundled = (res as NSString).appendingPathComponent("eas-bridge")
        if FileManager.default.fileExists(atPath: (bundled as NSString).appendingPathComponent("webapp.py")) {
            return bundled
        }
    }
    // Dev builds: project_dir.txt written by build_app.sh → worktree path.
    if let p = Bundle.main.path(forResource: "project_dir", ofType: "txt"),
       let s = try? String(contentsOfFile: p, encoding: .utf8) {
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    return home + "/пробы/eas-bridge"
}()

func portOpen() -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return false }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = port.bigEndian
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    return withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var tray: TrayStatusController?
    var startedAt = Date()
    let url = URL(string: "http://127.0.0.1:\(port)/")!

    func applicationDidFinishLaunching(_ n: Notification) {
        let cfg = WKWebViewConfiguration()
        web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "AAS mail"
        window.contentView = web
        window.setFrameAutosaveName("EASMailMain")
        if !window.setFrameUsingName("EASMailMain") { window.center() }
        window.makeKeyAndOrderFront(nil)
        buildMenu()
        NSApp.activate(ignoringOtherApps: true)
        showMessage("Запуск…")
        if !portOpen() { startServer() }
        Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] t in
            guard let self = self else { t.invalidate(); return }
            if portOpen() {
                t.invalidate()
                self.web.load(URLRequest(url: self.url))
                self.tray = TrayStatusController()
            }
            else if Date().timeIntervalSince(self.startedAt) > 90 {
                t.invalidate()
                self.showMessage("Сервер не запустился. Лог: ~/.config/eas-bridge/webapp.log")
            }
        }
    }

    func startServer() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        let uv = FileManager.default.isExecutableFile(atPath: home + "/.local/bin/uv")
            ? home + "/.local/bin/uv" : "uv"
        let venvPy = (projectDir as NSString).appendingPathComponent(".venv/bin/python")
        let vendor = (projectDir as NSString).appendingPathComponent("vendor")
        // Prefer the bundled venv (offline-friendly dist). Fall back to uv + PYTHONPATH.
        let cmd: String
        if FileManager.default.isExecutableFile(atPath: venvPy) {
            cmd = "cd \"\(projectDir)\" && export PYTHONPATH=\"\(vendor)${PYTHONPATH:+:$PYTHONPATH}\" && exec \"\(venvPy)\" webapp.py"
        } else {
            cmd = "cd \"\(projectDir)\" && export PYTHONPATH=\"\(vendor)${PYTHONPATH:+:$PYTHONPATH}\" && exec \"\(uv)\" run --python 3.12 --with requests --with urllib3 --with python-dateutil python webapp.py"
        }
        p.arguments = ["-c", cmd]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = home + "/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
        p.environment = env
        let logPath = home + "/.config/eas-bridge/webapp.log"
        try? FileManager.default.createDirectory(atPath: home + "/.config/eas-bridge",
                                                 withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logPath, contents: nil)
        if let h = FileHandle(forWritingAtPath: logPath) { h.seekToEndOfFile(); p.standardOutput = h; p.standardError = h }
        do { try p.run(); server = p } catch { showMessage("Не удалось запустить сервер: \(error.localizedDescription)") }
        startedAt = Date()
    }

    func showMessage(_ text: String) {
        web.loadHTMLString("<body style='font:15px -apple-system;color:#888;display:flex;height:100vh;align-items:center;justify-content:center;margin:0'>\(text)</body>", baseURL: nil)
    }

    // Keep the menu-bar tray alive after the mail window is closed — quitting
    // the whole app from ⌘W would defeat the point of a status-item presence.
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ n: Notification) { server?.terminate() }

    // Links to other sites and target=_blank go to the default browser.
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let u = a.request.url else { return decisionHandler(.cancel) }
        let local = u.host == "127.0.0.1" || u.host == "localhost"
        if u.scheme == "about" || u.scheme == "data" || local && a.targetFrame != nil { return decisionHandler(.allow) }
        if u.scheme == "http" || u.scheme == "https" || u.scheme == "mailto" { NSWorkspace.shared.open(u) }
        decisionHandler(.cancel)
    }
    func webView(_ w: WKWebView, decidePolicyFor r: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let cd = (r.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(cd.lowercased().hasPrefix("attachment") || !r.canShowMIMEType ? .download : .allow)
    }
    func webView(_ w: WKWebView, navigationResponse: WKNavigationResponse, didBecome d: WKDownload) { d.delegate = self }
    func webView(_ w: WKWebView, navigationAction: WKNavigationAction, didBecome d: WKDownload) { d.delegate = self }

    // Downloads land in ~/Downloads (unique name) and are revealed in Finder.
    var lastDownload: URL?
    func download(_ d: WKDownload, decideDestinationUsing r: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let safe = suggestedFilename.replacingOccurrences(of: "/", with: "_")
        var dest = dir.appendingPathComponent(safe), i = 1
        while FileManager.default.fileExists(atPath: dest.path) {
            let base = (safe as NSString).deletingPathExtension, ext = (safe as NSString).pathExtension
            dest = dir.appendingPathComponent(ext.isEmpty ? "\(base) (\(i))" : "\(base) (\(i)).\(ext)"); i += 1
        }
        lastDownload = dest
        completionHandler(dest)
    }
    func downloadDidFinish(_ d: WKDownload) { if let u = lastDownload { NSWorkspace.shared.activateFileViewerSelecting([u]) } }

    // File pickers (attachments) and JS confirm/alert.
    func webView(_ w: WKWebView, runOpenPanelWith p: WKOpenPanelParameters, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel(); panel.allowsMultipleSelection = p.allowsMultipleSelection
        panel.begin { completionHandler($0 == .OK ? panel.urls : nil) }
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Отмена")
        completionHandler(a.runModal() == .alertFirstButtonReturn)
    }
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.runModal(); completionHandler()
    }
    // target=_blank links (event and mail links) open in the default browser.
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for a: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = a.request.url, ["http", "https", "mailto"].contains(u.scheme ?? "") { NSWorkspace.shared.open(u) }
        return nil
    }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { w.reload() }

    @objc func reload() { web.load(URLRequest(url: url)) }

    func buildMenu() {
        let main = NSMenu()
        func add(_ title: String, _ items: [(String, Selector?, String)]) {
            let root = NSMenuItem(); main.addItem(root)
            let m = NSMenu(title: title); root.submenu = m
            for (t, sel, key) in items { if t == "-" { m.addItem(.separator()) } else { m.addItem(NSMenuItem(title: t, action: sel, keyEquivalent: key)) } }
        }
        add("Почта", [("Скрыть", #selector(NSApplication.hide(_:)), "h"), ("-", nil, ""), ("Выход", #selector(NSApplication.terminate(_:)), "q")])
        add("Правка", [("Отменить", Selector(("undo:")), "z"), ("Повторить", Selector(("redo:")), "Z"), ("-", nil, ""),
                       ("Вырезать", #selector(NSText.cut(_:)), "x"), ("Копировать", #selector(NSText.copy(_:)), "c"),
                       ("Вставить", #selector(NSText.paste(_:)), "v"), ("Выделить всё", #selector(NSText.selectAll(_:)), "a")])
        add("Вид", [("Обновить", #selector(reload), "r")])
        add("Окно", [("Свернуть", #selector(NSWindow.miniaturize(_:)), "m"), ("Закрыть", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
