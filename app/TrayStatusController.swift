import Cocoa
import SwiftUI
import Combine

@MainActor final class TrayStatusController: NSObject, NSPopoverDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let popover = NSPopover()
    private let store = TrayDataStore()
    private var labelTimer: Timer?
    private var cancellables = Set<AnyCancellable>()
    private let notifications = NotificationScheduler()

    override init() {
        super.init()
        if let button = statusItem.button {
            button.image = Self.mailTrayImage()
            button.imagePosition = .imageLeading
            button.imageHugsTitle = true
            button.title = ""
            button.toolTip = "AAS mail — календарь"
            button.target = self
            button.action = #selector(togglePopover)
        }
        popover.behavior = .transient
        popover.delegate = self
        popover.contentSize = NSSize(width: 360, height: 520)
        popover.contentViewController = NSHostingController(
            rootView: TrayPopoverView(store: store))
        // Menu-bar countdown + notifications follow labelEvents (today/upcoming),
        // not the day the user may be browsing in the popover.
        store.$labelEvents
            .sink { [weak self] events in
                guard let self = self else { return }
                self.notifications.reschedule(for: events)
                self.refreshLabel(events: events)
            }
            .store(in: &cancellables)
        // Ask before the first data refresh can reach center.add().
        notifications.requestAuthorizationIfNeeded()
        // The popover starts closed, so start on the slow beat.
        store.start(interval: TrayDataStore.idleInterval)
        labelTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.refreshLabel() }
    }

    /// AA mark (red↓ / green↑ arrows) on transparent background — not a template,
    /// so the brand colors stay visible in the menu bar.
    private static func mailTrayImage() -> NSImage {
        if let url = Bundle.main.url(forResource: "TrayIcon", withExtension: "png"),
           let img = NSImage(contentsOf: url) {
            img.isTemplate = false
            img.size = NSSize(width: 18, height: 18)
            return img
        }
        // Fallback: tiny envelope outline if Resources weren't copied.
        let size = NSSize(width: 18, height: 18)
        let img = NSImage(size: size, flipped: false) { rect in
            let inset = rect.insetBy(dx: 1.5, dy: 2.5)
            let path = NSBezierPath(roundedRect: inset, xRadius: 2, yRadius: 2)
            path.lineWidth = 1.4
            NSColor.labelColor.setStroke()
            path.stroke()
            let flap = NSBezierPath()
            flap.move(to: NSPoint(x: inset.minX + 1, y: inset.minY + 1))
            flap.line(to: NSPoint(x: inset.midX, y: inset.minY + inset.height * 0.45))
            flap.line(to: NSPoint(x: inset.maxX - 1, y: inset.minY + 1))
            flap.lineWidth = 1.3
            flap.stroke()
            return true
        }
        return img
    }

    /// `events` is passed explicitly from the $events sink, which fires in
    /// willSet — store.events is still the previous value there.
    private func refreshLabel(events: [TrayEvent]? = nil) {
        // Icon stays put; title is the OWA-style countdown only. Idle → blank
        // title so the tray shows just the mail logo.
        let text = TrayLabelFormatter.label(
            events: events ?? store.labelEvents, now: Date(), isTomorrow: store.isTomorrow)
        statusItem.button?.attributedTitle = NSAttributedString()
        statusItem.button?.title = (text == TrayLabelFormatter.idleGlyph) ? "" : text
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown { popover.performClose(nil) }
        else { popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY) }
    }

    // Polling follows the popover: fast while it is open, slow while it is
    // closed. A `.transient` popover can also be dismissed by clicking away,
    // which never goes through togglePopover — hence the delegate.
    func popoverDidShow(_ notification: Notification) {
        store.start(interval: TrayDataStore.activeInterval)
    }

    func popoverDidClose(_ notification: Notification) {
        store.start(interval: TrayDataStore.idleInterval)
    }
}
