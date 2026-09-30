import Foundation

/// The shared webapp.py, running on a thread of this app (like PyServer.kt on Android
/// and the bundled server on the Mac).
enum PyServer {
    /// 8780 like the Mac and Android; EAS_MAIL_PORT (webapp reads it too) lets the
    /// simulator run next to a Mac app that already holds 8780.
    static let port = Int(ProcessInfo.processInfo.environment["EAS_MAIL_PORT"] ?? "") ?? 8780
    static let base = URL(string: "http://127.0.0.1:\(port)")!
    /// Only a request carrying this key gets the page (webapp.PAGE_KEY).
    static let pageKey: String = {
        var bytes = [UInt8](repeating: 0, count: 24)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
    }()
    private static var started = false

    static var dataDir: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    }
    static var logFile: URL { dataDir.appendingPathComponent("eas-bridge/eas-mail.log") }

    /// Start once per process; returns when /api/about answers with our token.
    static func start() async throws {
        if !started {
            started = true
            let bundle = Bundle.main.bundleURL
            let cache = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
            let args = [bundle.appendingPathComponent("python").path, bundle.appendingPathComponent("app").path,
                        bundle.appendingPathComponent("app_packages").path, dataDir.path, cache.path,
                        bundle.appendingPathComponent("app/web").path, pageKey]
            let t = Thread {
                let rc = args[0].withCString { h in args[1].withCString { a in args[2].withCString { p in
                    args[3].withCString { d in args[4].withCString { c in args[5].withCString { w in
                        args[6].withCString { k in aas_py_run(h, a, p, d, c, w, k) } } } } } } }
                NSLog("AAS mail: python server stopped (rc=%d)", rc)
            }
            t.stackSize = 8 << 20
            t.name = "webapp"
            t.start()
        }
        for _ in 0..<150 {
            if await alive() { return }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        throw NSError(domain: "AASMail", code: 1, userInfo: [NSLocalizedDescriptionKey: "Почтовый сервер не запустился"])
    }

    /// Up = answers with OUR token (another process on 8780 cannot know it).
    static func alive() async -> Bool {
        guard let tok = try? String(contentsOf: dataDir.appendingPathComponent("eas-bridge/runtime_token"), encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines) else { return false }
        var r = URLRequest(url: base.appendingPathComponent("api/about"), timeoutInterval: 1.5)
        r.httpMethod = "POST"
        r.setValue(tok, forHTTPHeaderField: "X-Tok")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = Data("{}".utf8)
        guard let (_, resp) = try? await URLSession.shared.data(for: r) else { return false }
        return (resp as? HTTPURLResponse)?.statusCode == 200
    }
}
