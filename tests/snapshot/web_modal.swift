// Drives the live web UI in WKWebView (same engine as the app): opens the compose
// modal, drags it by its title, checks floating/pass-through/discard-guard state,
// and saves before/after screenshots.
//   swiftc tests/snapshot/web_modal.swift -o /tmp/wm && /tmp/wm <out_dir>
import AppKit
import WebKit

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."

final class Driver: NSObject, WKNavigationDelegate {
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 820))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                          styleMask: [.borderless], backing: .buffered, defer: false)
    var failures = 0

    func start() {
        window.contentView = web
        window.orderFrontRegardless()
        web.navigationDelegate = self
        web.load(URLRequest(url: URL(string: "http://127.0.0.1:8780/")!))
    }

    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        Task { @MainActor in await run() }
    }

    @MainActor func js(_ src: String) async -> Any? {
        do { return try await web.callAsyncJavaScript(src, contentWorld: .page) }
        catch { print("  js error: \(error)"); failures += 1; return nil }
    }

    @MainActor func check(_ cond: Bool, _ msg: String) {
        print(cond ? "  ✔ \(msg)" : "  ✘ \(msg)")
        if !cond { failures += 1 }
    }

    @MainActor func snap(_ name: String) async {
        let img = try? await web.takeSnapshot(configuration: nil)
        guard let tiff = img?.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else { return }
        try? png.write(to: URL(fileURLWithPath: "\(outDir)/\(name).png"))
    }

    @MainActor func run() async {
        try? await Task.sleep(nanoseconds: 2_500_000_000)  // boot fetches + folder list
        _ = await js("composeForm(); return 1;")
        try? await Task.sleep(nanoseconds: 300_000_000)
        let before = await js("""
            const r = document.querySelector('#modal').getBoundingClientRect();
            return {left: r.left, top: r.top, bg: getComputedStyle(document.querySelector('#ov')).backgroundColor,
                    cursor: getComputedStyle(document.querySelector('#modal h3')).cursor};
            """) as? [String: Any] ?? [:]
        check((before["cursor"] as? String) == "grab", "title shows a grab cursor")
        await snap("modal-centered")

        let after = await js("""
            const m = document.querySelector('#modal'), h = m.querySelector('h3');
            const r = h.getBoundingClientRect(), x = r.left + 40, y = r.top + 10;
            const ev = (type, cx, cy) => h.dispatchEvent(new PointerEvent(type, {bubbles: true, clientX: cx, clientY: cy,
                pointerId: 1, button: 0, buttons: 1, pointerType: 'mouse', isPrimary: true}));
            ev('pointerdown', x, y); ev('pointermove', x - 150, y + 30); ev('pointermove', x - 330, y + 90); ev('pointerup', x - 330, y + 90);
            const r2 = m.getBoundingClientRect();
            // A point on the page, away from the modal: must reach the page, not the overlay.
            const hit = document.elementFromPoint(innerWidth - 30, innerHeight - 30);
            return {dx: r2.left - r.left + 40 - 40, left: r2.left, top: r2.top,
                    floating: document.querySelector('#ov').classList.contains('floating'),
                    hitOverlay: hit && hit.id === 'ov', hit: hit ? (hit.id || hit.className || hit.tagName) : null};
            """) as? [String: Any] ?? [:]
        let movedX = ((after["left"] as? Double) ?? 0) - ((before["left"] as? Double) ?? 0)
        let movedY = ((after["top"] as? Double) ?? 0) - ((before["top"] as? Double) ?? 0)
        check(abs(movedX + 330) < 2 && abs(movedY - 90) < 2, "modal follows the drag (moved \(Int(movedX)), \(Int(movedY)))")
        check((after["floating"] as? Bool) == true, "dragged modal floats (no backdrop)")
        check((after["hitOverlay"] as? Bool) == false, "clicks outside reach the page (hit: \(after["hit"] ?? "nil"))")

        let clamp = await js("""
            const m = document.querySelector('#modal'), h = m.querySelector('h3');
            const r = h.getBoundingClientRect(), x = r.left + 40, y = r.top + 10;
            const ev = (type, cx, cy) => h.dispatchEvent(new PointerEvent(type, {bubbles: true, clientX: cx, clientY: cy, pointerId: 1, button: 0, buttons: 1}));
            ev('pointerdown', x, y); ev('pointermove', x, y - 5000); ev('pointerup', x, y - 5000);
            return m.getBoundingClientRect().top;
            """) as? Double
        check((clamp ?? -1) >= -0.5, "title cannot be dragged off the top edge (top=\(clamp ?? -1))")

        let guardState = await js("""
            const t = document.querySelector('#cbody'); t.value += 'черновик'; t.dispatchEvent(new Event('input', {bubbles: true}));
            window.confirm = () => false;
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
            const stillOpen = document.querySelector('#ov').classList.contains('on');
            let threw = false; try { eventForm(); } catch (e) { threw = !!e.silent; }
            const keptText = document.querySelector('#cbody')?.value.includes('черновик');
            return {stillOpen, threw, keptText};
            """) as? [String: Any] ?? [:]
        check((guardState["stillOpen"] as? Bool) == true, "Esc with typed text asks first (declined → stays open)")
        check((guardState["threw"] as? Bool) == true && (guardState["keptText"] as? Bool) == true,
              "opening another form over a draft asks first (declined → draft kept)")
        _ = await js("document.querySelector('#cbody').scrollIntoView(); return 1;")
        await snap("modal-floating")

        let reset = await js("""
            const h = document.querySelector('#modal h3');
            h.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
            return {t: document.querySelector('#modal').style.transform, f: document.querySelector('#ov').classList.contains('floating')};
            """) as? [String: Any] ?? [:]
        check((reset["t"] as? String) == "" && (reset["f"] as? Bool) == false, "double-click on title re-centres")

        // Regression: a primary button that is a link inside a modal (event «▶ Подключиться»)
        // must be readable in both themes — `#modal a` once gave it the fill colour.
        for theme in ["light", "dark"] {
            let ratio = await js("""
                window.confirm = () => true; closeModalSafe(); applyTheme('\(theme)');
                openModal('<h3>t</h3><a class="aas-btn aas-btn--primary" id="probe" href="#">▶ Подключиться</a>');
                const cs = getComputedStyle(document.querySelector('#probe'));
                const lum = s => { const [r, g, b] = s.match(/\\d+/g).slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
                                   return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
                const a = lum(cs.color), b = lum(cs.backgroundColor);
                return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
                """) as? Double ?? 0
            check(ratio >= 4.5, "«Подключиться» in a modal is readable in \(theme) (contrast \(String(format: "%.1f", ratio)):1)")
        }
        _ = await js("window.confirm = () => true; closeModalSafe(); return 1;")
        let closed = await js("return document.querySelector('#ov').classList.contains('on');") as? Bool
        check(closed == false, "confirmed close shuts the modal")
        print(failures == 0 ? "ALL OK" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let driver = Driver()
driver.start()
app.run()
