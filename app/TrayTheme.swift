import AppKit
import SwiftUI
import Combine

/// Resolved colours of the theme picked in Settings → Оформление, sent by the web UI
/// (`aasPalette`) straight from its CSS tokens, so the tray popover and reminder
/// banners wear the same palette as the main window instead of system grey + blue.
/// Stored in UserDefaults so the tray is themed before the web view has loaded.
struct TrayPalette: Codable, Equatable {
    var dark: Bool
    var bg, panel, line, text, muted: String
    var danger, ok, warn: String
    var bankFill, bankOn, bankInk, bankBg: String
    var sellerFill, sellerOn, sellerInk, sellerBg: String
}

final class TrayTheme: ObservableObject {
    static let shared = TrayTheme()
    private static let key = "trayPalette"

    @Published private(set) var palette: TrayPalette?

    private init() {
        if let data = UserDefaults.standard.data(forKey: Self.key) {
            palette = try? JSONDecoder().decode(TrayPalette.self, from: data)
        }
    }

    /// JSON from the web UI; anything unparsable keeps the previous palette.
    func update(json: String) {
        guard let data = json.data(using: .utf8),
              let p = try? JSONDecoder().decode(TrayPalette.self, from: data) else { return }
        let apply = {
            guard p != self.palette else { return }
            self.palette = p
            UserDefaults.standard.set(data, forKey: Self.key)
        }
        // WebKit script messages already arrive on the main thread.
        if Thread.isMainThread { apply() } else { DispatchQueue.main.async(execute: apply) }
    }

    // Surfaces and text. Without a palette: the previous system look.
    var canvas: Color { palette.map { Color(hex: $0.bg) } ?? Color(nsColor: .windowBackgroundColor) }
    /// Popover backing over the frosted material: near-opaque when themed, so the
    /// palette reads true; the system look keeps its translucent frost.
    var canvasOpacity: Double { palette == nil ? 0.72 : 0.96 }
    var card: Color { palette.map { Color(hex: $0.panel) } ?? Color(nsColor: .controlBackgroundColor) }
    var text: Color { palette.map { Color(hex: $0.text) } ?? .primary }
    var muted: Color { palette.map { Color(hex: $0.muted) } ?? .secondary }
    var line: Color { palette.map { Color(hex: $0.line) } ?? Color.secondary.opacity(0.22) }
    var danger: Color { palette.map { Color(hex: $0.danger) } ?? .red }
    var ok: Color { palette.map { Color(hex: $0.ok) } ?? .green }
    var warn: Color { palette.map { Color(hex: $0.warn) } ?? .orange }
    /// Links and the «now» accent — the main (Bank) account's text colour.
    var link: Color { palette.map { Color(hex: $0.bankInk) } ?? .accentColor }

    /// Account tint in the current palette; `fallbackHex` = the account's own brand colour.
    func tint(account: String, fallbackHex: String) -> AccountTint {
        guard let p = palette else { return AccountTint.of(fallbackHex) }
        return account == "seller"
            ? AccountTint(base: p.sellerFill, on: p.sellerOn, ink: p.sellerInk, wash: p.sellerBg)
            : AccountTint(base: p.bankFill, on: p.bankOn, ink: p.bankInk, wash: p.bankBg)
    }
    @MainActor var bank: AccountTint { tint(account: "main", fallbackHex: TrayDataStore.mainColor) }
    @MainActor var seller: AccountTint { tint(account: "seller", fallbackHex: TrayDataStore.sellerColor) }
}
