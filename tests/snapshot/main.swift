// Renders the tray popover to PNG (light + dark) with live data from the running
// server, for eyeballing readability without clicking the menu bar:
//   bash tests/snapshot/render.sh [out_dir] [day_offset]
// Env: TRAY_PALETTES=palettes.json renders one shot per theme; TRAY_DEMO=1 fills an
// empty store (no server) with made-up meetings.
import AppKit
import SwiftUI

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
let dayOffset = CommandLine.arguments.count > 2 ? Int(CommandLine.arguments[2]) ?? 0 : 0

@MainActor
func render(_ store: TrayDataStore, appearance: NSAppearance.Name, to path: String) {
    // The real popover draws a system material behind the view; stand in with the window colour.
    let host = NSHostingView(rootView: TrayPopoverView(store: store)
        .background(Color(nsColor: .windowBackgroundColor)))
    host.frame = NSRect(x: 0, y: 0, width: 360, height: 520)
    let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
    window.appearance = NSAppearance(named: appearance)
    window.backgroundColor = appearance == .darkAqua ? NSColor(white: 0.16, alpha: 1) : NSColor(white: 0.96, alpha: 1)
    window.contentView = host
    window.orderFrontRegardless()
    // Let layout, onAppear and scroll-to-now settle.
    RunLoop.main.run(until: Date().addingTimeInterval(1.0))
    let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds)!
    host.cacheDisplay(in: host.bounds, to: rep)
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: path))
    window.orderOut(nil)
    print("wrote \(path)")
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
Task { @MainActor in
    let store = TrayDataStore()
    store.dayOffset = dayOffset
    await store.refresh()
    if let err = store.lastError { print("store error: \(err)") }
    // TRAY_DEMO=1 with no server: a few made-up meetings (both accounts, all states).
    if store.events.isEmpty, ProcessInfo.processInfo.environment["TRAY_DEMO"] == "1" {
        let iso = ISO8601DateFormatter(), local = DateFormatter()
        local.dateFormat = "yyyy-MM-dd HH:mm"
        func ev(_ id: String, _ acct: String, _ subject: String, _ from: Int, _ to: Int,
                allDay: Bool = false, response: String = "accepted", link: Bool = false) -> TrayEvent {
            let s = Date().addingTimeInterval(Double(from) * 60), e = Date().addingTimeInterval(Double(to) * 60)
            let json: [String: Any] = ["item_id": id, "subject": subject, "start_iso": iso.string(from: s),
                "end": local.string(from: e), "is_all_day": allDay, "response_type": response,
                "meeting_status": "meeting", "location": link ? "https://ktalk.example/abc" : "Переговорная 4.12",
                "organizer": ["name": "Ольга Верещагина"]]
            var t = try! JSONDecoder().decode(TrayEvent.self, from: JSONSerialization.data(withJSONObject: json))
            t.accountId = acct
            t.accountColorHex = acct == "seller" ? TrayDataStore.sellerColor : TrayDataStore.mainColor
            return t
        }
        store.events = [
            ev("1", "main", "Сверка лимитов", -20, 40, link: true),
            ev("2", "seller", "Созвон с поставщиком", 70, 120),
            ev("3", "main", "Планёрка", 150, 180, response: "tentative"),
            ev("4", "seller", "Отпуск Мирона", 0, 1440, allDay: true),
        ]
        store.lastError = nil
    }
    print("events: \(store.events.count)")
    render(store, appearance: .aqua, to: "\(outDir)/tray-light.png")
    render(store, appearance: .darkAqua, to: "\(outDir)/tray-dark.png")
    // TRAY_PALETTES=file.json ({"theme-id": palette, …}, as the web UI posts them):
    // one shot per theme, dressed exactly as the running app would be.
    if let path = ProcessInfo.processInfo.environment["TRAY_PALETTES"],
       let data = FileManager.default.contents(atPath: path),
       let all = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] {
        for (name, palette) in all.sorted(by: { $0.key < $1.key }) {
            let json = String(data: try! JSONSerialization.data(withJSONObject: palette), encoding: .utf8)!
            TrayTheme.shared.update(json: json)
            let dark = palette["dark"] as? Bool ?? false
            render(store, appearance: dark ? .darkAqua : .aqua, to: "\(outDir)/tray-\(name).png")
        }
    }
    exit(0)
}
app.run()
