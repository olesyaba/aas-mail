import SwiftUI
import UIKit
import WebKit

/// The mail UI from the in-app server. Everything that is not 127.0.0.1 opens outside
/// (Safari, KTalk, Teams…); attachments end in the share sheet.
struct WebView: UIViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(context.coordinator, name: "aasSave")
        // iPad: the full layout; iPhone: the narrow one (as on the Fold cover screen).
        cfg.defaultWebpagePreferences.preferredContentMode = UIDevice.current.userInterfaceIdiom == .pad ? .desktop : .mobile
        let web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        // iPhone: a swipe from the left edge is «back» — history.back() closes the open letter.
        web.allowsBackForwardNavigationGestures = UIDevice.current.userInterfaceIdiom == .phone
        web.isInspectable = true
        context.coordinator.web = web
        web.load(Coordinator.pageRequest(PyServer.base))
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate {
        weak var web: WKWebView?
        private var downloads: [WKDownload: URL] = [:]

        /// The page itself needs the page key (webapp.PAGE_KEY); API calls use the token inside it.
        static func pageRequest(_ url: URL) -> URLRequest {
            var r = URLRequest(url: url)
            r.setValue(PyServer.pageKey, forHTTPHeaderField: "X-Page-Key")
            return r
        }
        private static func isOurs(_ url: URL?) -> Bool {
            url?.host == "127.0.0.1" && url?.port == PyServer.port
        }
        private static func isPage(_ url: URL) -> Bool { url.path.isEmpty || url.path == "/" || url.path == "/index.html" }

        // MARK: navigation
        func webView(_ web: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { return decisionHandler(.cancel) }
            if url.scheme == "about" || url.scheme == "blob" || url.scheme == "data" { return decisionHandler(.allow) }
            if Self.isOurs(url) {
                if action.shouldPerformDownload { return decisionHandler(.download) }
                // A reload / location change to the page loses our header: re-issue it with the key.
                if action.targetFrame?.isMainFrame == true, Self.isPage(url),
                   action.request.value(forHTTPHeaderField: "X-Page-Key") == nil {
                    decisionHandler(.cancel)
                    web.load(Self.pageRequest(url))
                    return
                }
                return decisionHandler(.allow)
            }
            decisionHandler(.cancel)
            UIApplication.shared.open(url)  // meeting links, mail links, anything outside the app
        }

        func webView(_ web: WKWebView, decidePolicyFor response: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            decisionHandler(response.canShowMIMEType && !(response.response as? HTTPURLResponse).isAttachment ? .allow : .download)
        }

        func webView(_ web: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
        func webView(_ web: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

        // target=_blank and window.open: outside the app.
        func webView(_ web: WKWebView, createWebViewWith cfg: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url {
                if Self.isOurs(url) { web.load(URLRequest(url: url)) } else { UIApplication.shared.open(url) }
            }
            return nil
        }

        // MARK: alert / confirm (without these WKWebView answers «Отмена» to every confirm())
        func webView(_ web: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            a.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            present(a) ?? completionHandler()
        }

        func webView(_ web: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            a.addAction(UIAlertAction(title: "Отмена", style: .cancel) { _ in completionHandler(false) })
            a.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
            present(a) ?? completionHandler(false)
        }

        // MARK: downloads → share sheet
        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                      suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let dest = dir.appendingPathComponent(suggestedFilename.isEmpty ? "attachment" : suggestedFilename)
            downloads[download] = dest
            completionHandler(dest)
        }

        func downloadDidFinish(_ download: WKDownload) {
            if let url = downloads.removeValue(forKey: download) { share([url]) }
        }

        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            downloads.removeValue(forKey: download)
            report(false, "Не удалось получить вложение: \(error.localizedDescription)")
        }

        // MARK: window.webkit.messageHandlers.aasSave — «Открыть», «Сохранить как…», «Сохранить все»
        func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "aasSave", let body = message.body as? [String: Any],
                  let files = body["files"] as? [[String: Any]], !files.isEmpty else { return }
            Task { @MainActor in
                var saved: [URL] = []
                for f in files {
                    guard let path = f["url"] as? String, let url = URL(string: path, relativeTo: PyServer.base) else { continue }
                    let name = (f["name"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "attachment"
                    guard let (data, resp) = try? await URLSession.shared.data(from: url),
                          (resp as? HTTPURLResponse)?.statusCode == 200 else { continue }
                    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                    let dest = dir.appendingPathComponent((name as NSString).lastPathComponent)
                    if (try? data.write(to: dest)) != nil { saved.append(dest) }
                }
                if saved.isEmpty { report(false, "Не удалось получить вложение с сервера") } else { share(saved) }
            }
        }

        private func share(_ urls: [URL]) {
            let sheet = UIActivityViewController(activityItems: urls, applicationActivities: nil)
            sheet.completionWithItemsHandler = { [weak self] _, done, _, _ in
                if done { self?.report(true, urls.count == 1 ? "Готово" : "Готово: \(urls.count) файла") }
            }
            if let pop = sheet.popoverPresentationController, let web {
                pop.sourceView = web
                pop.sourceRect = CGRect(x: web.bounds.midX, y: web.bounds.midY, width: 1, height: 1)
                pop.permittedArrowDirections = []
            }
            _ = present(sheet)
        }

        private func report(_ ok: Bool, _ text: String) {
            let js = "window.aasSaved && aasSaved(\(ok), \(String(data: try! JSONEncoder().encode(text), encoding: .utf8)!))"
            web?.evaluateJavaScript(js)
        }

        @discardableResult private func present(_ vc: UIViewController) -> Void? {
            guard var top = web?.window?.rootViewController else { return nil }
            while let next = top.presentedViewController { top = next }
            top.present(vc, animated: true)
            return ()
        }
    }
}

private extension Optional where Wrapped == HTTPURLResponse {
    var isAttachment: Bool {
        (self?.value(forHTTPHeaderField: "Content-Disposition") ?? "").lowercased().hasPrefix("attachment")
    }
}
