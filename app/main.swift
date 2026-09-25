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
    return home + "/пробы/aas-mail"
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

/// Settings → Оформление, forwarded from the web UI: drives the native chrome too
/// (window, tray popover, reminder banners) so the whole app shares one theme.
enum AppTheme {
    static let key = "appTheme"
    static let lightLooks: Set<String> = [
        "light", "navy-orange-light", "royal-velvet-light", "eclipse-almond-light"
    ]
    static let darkLooks: Set<String> = [
        "dark", "navy-orange", "royal-velvet", "eclipse-almond"
    ]
    static func apply(_ value: String) {
        if lightLooks.contains(value) {
            NSApp.appearance = NSAppearance(named: .aqua)
        } else if darkLooks.contains(value) {
            NSApp.appearance = NSAppearance(named: .darkAqua)
        } else {
            NSApp.appearance = nil  // follow macOS
        }
        UserDefaults.standard.set(value, forKey: key)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate,
                         WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var tray: TrayStatusController?
    var startedAt = Date()
    let url = URL(string: "http://127.0.0.1:\(port)/")!

    func applicationDidFinishLaunching(_ n: Notification) {
        AppTheme.apply(UserDefaults.standard.string(forKey: AppTheme.key) ?? "system")  // before any window shows
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(self, name: "aasTheme")
        cfg.userContentController.add(self, name: "aasPrefs")
        cfg.userContentController.add(self, name: "aasNewMail")
        cfg.userContentController.add(self, name: "aasPlaySound")
        cfg.userContentController.add(self, name: "aasBadge")
        cfg.userContentController.add(self, name: "aasSave")
        web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "AAS mail"
        // Below this the three mail columns cannot fit even with the folder drawer.
        window.minSize = NSSize(width: 640, height: 480)
        // AAS-24-06: closing the window only hides it. The default (released on
        // close) destroyed it, so neither the Dock nor the tray could bring it back
        // — only Quit + relaunch. Hidden, it also keeps the open tab and folder.
        window.isReleasedWhenClosed = false
        window.contentView = web
        window.setFrameAutosaveName("EASMailMain")
        if !window.setFrameUsingName("EASMailMain") { window.center() }
        window.makeKeyAndOrderFront(nil)
        buildMenu()
        NSApp.activate(ignoringOtherApps: true)
        NotificationCenter.default.addObserver(
            forName: .easShowCalendar, object: nil, queue: .main
        ) { [weak self] _ in self?.showCalendarFromTray() }
        NotificationCenter.default.addObserver(
            forName: .easShowMain, object: nil, queue: .main
        ) { [weak self] _ in self?.showMailFromTray() }
        NotificationCenter.default.addObserver(
            forName: .easCreateEvent, object: nil, queue: .main
        ) { [weak self] note in
            let day = note.userInfo?["day"] as? String
            let hour = note.userInfo?["hour"] as? Int
            let minute = note.userInfo?["minute"] as? Int
            self?.createEventFromTray(day: day, hour: hour, minute: minute)
        }
        showMessage("Запуск…")
        if !portOpen() { startServer() }
        Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] t in
            guard let self = self else { t.invalidate(); return }
            if portOpen() {
                t.invalidate()
                self.web.load(URLRequest(url: self.url))
                self.tray = MainActor.assumeIsolated { TrayStatusController() }
                self.startHiddenSyncNudge()
            }
            else if Date().timeIntervalSince(self.startedAt) > 90 {
                t.invalidate()
                self.showMessage("Сервер не запустился. Лог: ~/.config/eas-bridge/webapp.log")
            }
        }
    }

    /// WebKit holds back the page's timers whenever the window is closed, minimised,
    /// behind other windows or the app is in the background — «каждые 2 минуты» then
    /// slipped to 10+. Every minute ask the page whether a sync is due (it keeps the
    /// interval from Settings and skips when one ran recently).
    func startHiddenSyncNudge() {
        Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.web.evaluateJavaScript(
                    "typeof autoSyncDue==='function' ? autoSyncDue() : (typeof autoSyncTick==='function' && autoSyncTick())")
            }
        }
    }

    /// Bring the (possibly closed) main window to the front, state intact.
    @objc func showMainWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Dock icon click after the window was closed → show it again (AAS-24-06).
    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showMainWindow() }
        return true
    }

    /// Tray footer «Почта» — raise the window and switch back to the mail tab
    /// (it may have been left on the calendar); the current account stays.
    func showMailFromTray() {
        showMainWindow()
        web.evaluateJavaScript(
            "if (typeof showView==='function' && view!=='mail') { showView('mail', ACCT); if (location.hash==='#cal') history.replaceState(null, '', location.pathname + location.search); }"
        ) { _, _ in }
    }

    /// Tray footer "Календарь" — raise the mail window and switch to the cal tab.
    func showCalendarFromTray() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.evaluateJavaScript(
            "typeof showView==='function' ? (showView('cal'), location.hash='cal') : (location.hash='cal')"
        ) { [weak self] _, err in
            guard let self = self, err != nil else { return }
            var c = URLComponents(url: self.url, resolvingAgainstBaseURL: false)
            c?.fragment = "cal"
            if let u = c?.url { self.web.load(URLRequest(url: u)) }
        }
    }

    /// Tray «Создать» / click on an hour slot: the one «Новое событие» form
    /// (main window) — same fields, availability and saved links as the calendar.
    func createEventFromTray(day: String?, hour: Int? = nil, minute: Int? = nil) {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let dayArg = day.map { "'\($0.filter { $0.isNumber || $0 == "-" })'" } ?? "null"
        let hourArg = hour.map(String.init) ?? "null"
        let minuteArg = minute.map(String.init) ?? "0"
        web.evaluateJavaScript(
            "typeof eventForm==='function' && (showView('cal'), eventForm(null, {day: \(dayArg), hour: \(hourArg), minute: \(minuteArg)}))")
    }

    func startServer() {
        let p = Process()
        let fm = FileManager.default
        let dir = projectDir as NSString
        let vendor = dir.appendingPathComponent("vendor")
        let bundledPy = dir.appendingPathComponent("python/bin/python3")
        let venvPy = dir.appendingPathComponent(".venv/bin/python")
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = home + "/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
        p.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        if fm.isExecutableFile(atPath: bundledPy) {
            // Dist build: embedded interpreter + precompiled deps, started directly —
            // no shell, no network, no uv. The bundle stays read-only (signed).
            p.executableURL = URL(fileURLWithPath: bundledPy)
            p.arguments = ["-s", "webapp.py"]
            env["PYTHONPATH"] = vendor + ":" + dir.appendingPathComponent("site-packages")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONNOUSERSITE"] = "1"
            env.removeValue(forKey: "PYTHONHOME")
        } else {
            // Dev build: project .venv if present, otherwise uv resolves the deps.
            let uv = fm.isExecutableFile(atPath: home + "/.local/bin/uv") ? home + "/.local/bin/uv" : "uv"
            // Without a vendored client (dev checkout) uv fetches it from GitHub.
            let client = fm.fileExists(atPath: vendor) ? "" : "--with git+https://github.com/mainpart/outlook-activesync-mcp "
            let run = fm.isExecutableFile(atPath: venvPy)
                ? "exec \"\(venvPy)\" webapp.py"
                : "exec \"\(uv)\" run --python 3.12 \(client)--with requests --with urllib3 --with python-dateutil python webapp.py"
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = ["-c", "export PYTHONPATH=\"\(vendor)${PYTHONPATH:+:$PYTHONPATH}\" && \(run)"]
        }
        p.environment = env
        let logPath = home + "/.config/eas-bridge/webapp.log"
        try? FileManager.default.createDirectory(atPath: home + "/.config/eas-bridge",
                                                 withIntermediateDirectories: true)
        // Append across launches (the log used to be truncated on every start,
        // losing the error that prompted the restart); rotate once it gets big.
        if let size = (try? FileManager.default.attributesOfItem(atPath: logPath))?[.size] as? Int,
           size > 5_000_000 {
            try? FileManager.default.removeItem(atPath: logPath + ".1")
            try? FileManager.default.moveItem(atPath: logPath, toPath: logPath + ".1")
        }
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
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

    // Attachments: "open" (temp copy → default app), "as" (save panel), "all" (pick a folder).
    // Files come from the local server with the page token already in the URL.
    func saveAttachments(mode: String, files: [(URL, String)]) {
        guard !files.isEmpty else { return }
        switch mode {
        case "open":
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent("AAS mail attachments", isDirectory: true)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            fetchAttachments(files, into: dir) { saved in
                if let f = saved.first { NSWorkspace.shared.open(f) }
            }
        case "as":
            let panel = NSSavePanel()
            panel.nameFieldStringValue = files[0].1
            panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            panel.beginSheetModal(for: window) { [weak self] r in
                guard r == .OK, let dest = panel.url else { return }
                self?.fetchAttachments([(files[0].0, dest.lastPathComponent)], into: dest.deletingLastPathComponent(),
                                       overwrite: true) { saved in
                    if !saved.isEmpty { self?.reportSaved(saved) }
                }
            }
        default:
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true
            panel.prompt = "Сохранить сюда"
            panel.message = "Куда сохранить вложения (\(files.count))"
            panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            panel.beginSheetModal(for: window) { [weak self] r in
                guard r == .OK, let dir = panel.url else { return }
                self?.fetchAttachments(files, into: dir) { saved in self?.reportSaved(saved, of: files.count) }
            }
        }
    }

    private func reportSaved(_ saved: [URL], of total: Int = 1) {
        let ok = saved.count == total
        let text = total == 1 ? (ok ? "Сохранено: \(saved[0].lastPathComponent)" : "Не удалось сохранить вложение")
            : "Сохранено \(saved.count) из \(total)"
        let js = "window.aasSaved && aasSaved(\(ok), \(String(data: try! JSONEncoder().encode(text), encoding: .utf8)!))"
        web.evaluateJavaScript(js)
        if !saved.isEmpty { NSWorkspace.shared.activateFileViewerSelecting(saved) }
    }

    /// Download each file and write it into `dir`; names get " (2)" instead of overwriting
    /// unless the user already confirmed a replace in the save panel.
    private func fetchAttachments(_ files: [(URL, String)], into dir: URL, overwrite: Bool = false,
                                  done: @escaping ([URL]) -> Void) {
        Task { @MainActor in
            var saved: [URL] = []
            for (src, name) in files {
                guard let (data, resp) = try? await URLSession.shared.data(from: src),
                      (resp as? HTTPURLResponse)?.statusCode == 200 else { continue }
                var dest = dir.appendingPathComponent(name), i = 2
                while !overwrite && FileManager.default.fileExists(atPath: dest.path) {
                    let base = (name as NSString).deletingPathExtension, ext = (name as NSString).pathExtension
                    dest = dir.appendingPathComponent(ext.isEmpty ? "\(base) (\(i))" : "\(base) (\(i)).\(ext)"); i += 1
                }
                if (try? data.write(to: dest, options: .atomic)) != nil { saved.append(dest) }
            }
            if saved.isEmpty {
                self.web.evaluateJavaScript("window.aasSaved && aasSaved(false, 'Не удалось получить вложение с сервера')",
                                            completionHandler: nil)
                return
            }
            done(saved)
        }
    }

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

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "aasTheme", let value = message.body as? String { AppTheme.apply(value) }
        if message.name == "aasPrefs", let p = message.body as? [String: Any] {
            if let m = (p["reminder_minutes"] as? NSNumber)?.intValue, ReminderLead.allowed.contains(m),
               m != ReminderLead.minutes {
                ReminderLead.minutes = m
                NotificationCenter.default.post(name: .easReminderLeadChanged, object: nil)
            }
            if let s = p["mail_sound"] as? String, MailSound.allowed.contains(s),
               s != MailSound.current.rawValue {
                MailSound.current = MailSound(rawValue: s) ?? .default
            }
        }
        if message.name == "aasPlaySound", let s = message.body as? String,
           let sound = MailSound(rawValue: s) {
            sound.playPreview()
        }
        if message.name == "aasSave", let p = message.body as? [String: Any],
           let mode = p["mode"] as? String, let files = p["files"] as? [[String: String]] {
            saveAttachments(mode: mode, files: files.compactMap { f in
                guard let u = f["url"], let abs = URL(string: u, relativeTo: url)?.absoluteURL else { return nil }
                return (abs, (f["name"] ?? "attachment").replacingOccurrences(of: "/", with: "_"))
            })
        }
        if message.name == "aasBadge", let n = (message.body as? NSNumber)?.intValue {
            // AAS-24-05: Inbox unread of all accounts on the Dock icon; none when 0.
            NSApp.dockTile.badgeLabel = n > 0 ? (n > 999 ? "999+" : "\(n)") : nil
            NotificationCenter.default.post(name: .easUnreadChanged, object: NSNumber(value: n))
        }
        if message.name == "aasNewMail", let p = message.body as? [String: Any] {
            let count = (p["count"] as? NSNumber)?.intValue ?? 1
            NewMailNotifier.notify(
                from: p["from"] as? String ?? "",
                subject: p["subject"] as? String ?? "",
                preview: p["preview"] as? String ?? "",
                count: max(1, count),
                account: p["account"] as? String ?? "")
        }
    }

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
        add("Окно", [("Окно почты", #selector(showMainWindow), "0"), ("-", nil, ""),
                     ("Свернуть", #selector(NSWindow.miniaturize(_:)), "m"), ("Закрыть", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
