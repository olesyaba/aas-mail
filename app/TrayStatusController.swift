import Cocoa
import SwiftUI
import Combine

/// Menu-bar tray calendar. Uses a floating ``NSPanel`` (not ``NSPopover``) so the
/// popup stays above fullscreen apps and follows Spaces — same idea as OWA Widget's
/// reminder panels. ``NSPopover`` sits in the normal window level and disappears
/// behind Space / Stage Manager / fullscreen.
@MainActor final class TrayStatusController: NSObject {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let store = TrayDataStore()
    private var labelTimer: Timer?
    private var cancellables = Set<AnyCancellable>()
    private let notifications = NotificationScheduler()
    private let floatingReminders = FloatingReminderController(leadMinutes: ReminderLead.minutes)

    private var panel: NSPanel?
    private var hosting: NSHostingView<TrayPopoverView>?
    private var dismissMonitor: Any?
    private var globalDismissMonitor: Any?
    private let panelSize = NSSize(width: 360, height: 520)

    override init() {
        super.init()
        if let button = statusItem.button {
            button.image = Self.mailTrayImage()
            button.imagePosition = .imageLeading
            button.imageHugsTitle = true
            button.title = ""
            button.toolTip = "AAS mail — календарь"
            button.target = self
            button.action = #selector(togglePanel)
        }
        store.$labelEvents
            .sink { [weak self] events in
                guard let self = self else { return }
                self.notifications.reschedule(for: events)
                self.floatingReminders.reschedule(for: events)
                self.refreshLabel(events: events)
            }
            .store(in: &cancellables)
        notifications.leadMinutes = ReminderLead.minutes
        NotificationCenter.default.addObserver(
            forName: .easReminderLeadChanged, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                let m = ReminderLead.minutes
                self.notifications.leadMinutes = m
                self.floatingReminders.leadMinutes = m
                self.notifications.reschedule(for: self.store.labelEvents)
                self.floatingReminders.reschedule(for: self.store.labelEvents)
            }
        }
        notifications.requestAuthorizationIfNeeded()
        store.start(interval: TrayDataStore.idleInterval)
        labelTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refreshLabel() }
        }
        for name in [Notification.Name.easShowCalendar, .easCreateEvent] {
            NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.closePanel() }
            }
        }
        // Clicking another app / Mission Control: panel stays up unless we close on resign.
        NotificationCenter.default.addObserver(
            forName: NSApplication.didResignActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.closePanel() }
        }
    }

    private static func mailTrayImage() -> NSImage {
        if let url = Bundle.main.url(forResource: "TrayIcon", withExtension: "png"),
           let img = NSImage(contentsOf: url) {
            img.isTemplate = false
            img.size = NSSize(width: 18, height: 18)
            return img
        }
        let size = NSSize(width: 18, height: 18)
        return NSImage(size: size, flipped: false) { rect in
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
    }

    private func refreshLabel(events: [TrayEvent]? = nil) {
        let list = events ?? store.labelEvents
        let text = TrayLabelFormatter.label(
            events: list, now: Date(), isTomorrow: store.isTomorrow)
        statusItem.button?.attributedTitle = NSAttributedString()
        statusItem.button?.title = (text == TrayLabelFormatter.idleGlyph) ? "" : text
        if let next = TrayLabelFormatter.labelCandidates(list)
            .compactMap({ e -> (TrayEvent, Date)? in
                guard let s = e.startDate, s > Date() else { return nil }
                return (e, s)
            })
            .sorted(by: { $0.1 < $1.1 })
            .first?.0 {
            statusItem.button?.toolTip = "\(next.subject)\nAAS mail — календарь"
        } else {
            statusItem.button?.toolTip = "AAS mail — календарь"
        }
    }

    @objc private func togglePanel() {
        if panel?.isVisible == true { closePanel() }
        else { openPanel() }
    }

    private func openPanel() {
        guard let button = statusItem.button else { return }

        if panel == nil {
            let panel = NSPanel(
                contentRect: NSRect(origin: .zero, size: panelSize),
                styleMask: [.nonactivatingPanel, .borderless, .fullSizeContentView],
                backing: .buffered,
                defer: false
            )
            panel.isFloatingPanel = true
            // Above fullscreen apps; joins every Space (same as reminder banner).
            panel.level = .statusBar
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
            panel.backgroundColor = .clear
            panel.isOpaque = false
            panel.hasShadow = true
            panel.isReleasedWhenClosed = false
            panel.hidesOnDeactivate = false
            panel.becomesKeyOnlyIfNeeded = true
            panel.animationBehavior = .utilityWindow

            let hosting = NSHostingView(rootView: TrayPopoverView(store: store))
            hosting.frame = NSRect(origin: .zero, size: panelSize)
            hosting.autoresizingMask = [.width, .height]
            // Soft rounded chrome so it still reads as a menu-bar popover.
            hosting.wantsLayer = true
            hosting.layer?.cornerRadius = 12
            hosting.layer?.masksToBounds = true

            // The borderless panel is fully transparent by itself, so the text sat
            // right on the wallpaper. A frosted popover material plus a soft tint
            // (TrayPopoverView) keeps it readable over any background.
            let container = NSVisualEffectView(frame: NSRect(origin: .zero, size: panelSize))
            container.material = .popover
            container.blendingMode = .behindWindow
            container.state = .active
            container.wantsLayer = true
            container.layer?.cornerRadius = 12
            container.layer?.masksToBounds = true
            container.layer?.borderWidth = 0.5
            container.layer?.borderColor = NSColor.separatorColor.cgColor
            container.addSubview(hosting)
            panel.contentView = container

            self.panel = panel
            self.hosting = hosting
        } else {
            hosting?.rootView = TrayPopoverView(store: store)
        }

        positionPanel(near: button)
        panel?.orderFrontRegardless()
        // Key so Escape / text fields / date pickers inside the tray work.
        panel?.makeKey()
        store.start(interval: TrayDataStore.activeInterval)
        installDismissMonitor()
    }

    private func positionPanel(near button: NSView) {
        guard let panel else { return }
        let buttonRect = button.window?.convertToScreen(
            button.convert(button.bounds, to: nil)
        ) ?? .zero
        // Prefer the screen that owns the menu-bar item (multi-monitor).
        let screen = button.window?.screen ?? NSScreen.main
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        var origin = NSPoint(
            x: buttonRect.midX - panelSize.width / 2,
            y: buttonRect.minY - panelSize.height - 6
        )
        // Keep fully on-screen horizontally.
        origin.x = min(max(origin.x, visible.minX + 8), visible.maxX - panelSize.width - 8)
        // If it would go below the dock/screen, flip under… actually above the bar
        // is impossible; clamp to bottom of visible frame.
        if origin.y < visible.minY + 8 {
            origin.y = visible.minY + 8
        }
        panel.setFrame(NSRect(origin: origin, size: panelSize), display: true)
    }

    private func closePanel() {
        removeDismissMonitor()
        panel?.orderOut(nil)
        store.start(interval: TrayDataStore.idleInterval)
    }

    /// Click outside / Escape closes — mirrors NSPopover `.transient`.
    private func installDismissMonitor() {
        removeDismissMonitor()
        dismissMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown, .keyDown]
        ) { [weak self] event in
            guard let self else { return event }
            return self.handleDismissEvent(event)
        }
        // Global monitors can fire off the main thread — bounce to MainActor.
        // Clicks in other apps / Spaces only appear here (not in the local monitor).
        globalDismissMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown]
        ) { [weak self] _ in
            Task { @MainActor in
                self?.dismissIfClickOutside()
            }
        }
    }

    /// Shared hit-test using ``NSEvent.mouseLocation`` (screen coords, bottom-left).
    /// Avoids convertToScreen quirks with borderless / nonactivating panels.
    private func dismissIfClickOutside() {
        guard let panel, panel.isVisible else { return }
        let pt = NSEvent.mouseLocation
        if panel.frame.contains(pt) { return }
        if let button = statusItem.button,
           let br = button.window?.convertToScreen(button.convert(button.bounds, to: nil)),
           br.contains(pt) {
            return
        }
        closePanel()
    }

    private func handleDismissEvent(_ event: NSEvent) -> NSEvent? {
        if event.type == .keyDown {
            if event.keyCode == 53 { // Escape
                closePanel()
                return nil
            }
            return event
        }
        dismissIfClickOutside()
        return event
    }

    private func removeDismissMonitor() {
        if let dismissMonitor {
            NSEvent.removeMonitor(dismissMonitor)
            self.dismissMonitor = nil
        }
        if let globalDismissMonitor {
            NSEvent.removeMonitor(globalDismissMonitor)
            self.globalDismissMonitor = nil
        }
    }
}