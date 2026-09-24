// Screenshot the live web UI in WKWebView (the app's engine):
//   web_shot <out.png> [js to run after boot] [width] [height] [light|dark]
// The JS runs as an async function body; its return value is printed.
import AppKit
import WebKit

let args = CommandLine.arguments
let out = args.count > 1 ? args[1] : "shot.png"
let script = args.count > 2 ? args[2] : ""
let width = args.count > 3 ? Double(args[3]) ?? 1400 : 1400
let height = args.count > 4 ? Double(args[4]) ?? 860 : 860
let dark = args.count > 5 && args[5] == "dark"

final class Shot: NSObject, WKNavigationDelegate {
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: height))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                          styleMask: [.borderless], backing: .buffered, defer: false)
    func start() {
        window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.contentView = web
        window.orderFrontRegardless()
        web.navigationDelegate = self
        web.load(URLRequest(url: URL(string: "http://127.0.0.1:8780/")!))
    }
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !script.isEmpty {
                do {
                    let r = try await web.callAsyncJavaScript(script, contentWorld: .page)
                    if let r { print("js: \(r)") }
                } catch { print("js error: \(error)") }
                try? await Task.sleep(nanoseconds: 1_500_000_000)
            }
            let img = try? await web.takeSnapshot(configuration: nil)
            if let tiff = img?.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
               let png = rep.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: out))
                print("wrote \(out)")
            }
            exit(0)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let shot = Shot()
shot.start()
app.run()
