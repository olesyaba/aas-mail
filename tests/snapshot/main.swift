// Renders the tray popover to PNG (light + dark) with live data from the running
// server, for eyeballing readability without clicking the menu bar:
//   bash tests/snapshot/render.sh [out_dir] [day_offset]
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
    print("events: \(store.events.count)")
    render(store, appearance: .aqua, to: "\(outDir)/tray-light.png")
    render(store, appearance: .darkAqua, to: "\(outDir)/tray-dark.png")
    exit(0)
}
app.run()
