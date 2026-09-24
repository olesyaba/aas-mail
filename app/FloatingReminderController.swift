import AppKit
import Foundation
import SwiftUI

/// In-app floating meeting reminder (OWA Widget–style NSPanel), independent of
/// Notification Center permission. At most one panel is visible at a time.
@MainActor
final class FloatingReminderController {
    private var scheduled: [String: DispatchWorkItem] = [:]
    private var panel: NSPanel?
    private var hosting: NSHostingView<MeetingReminderBannerView>?
    private var generation: UInt64 = 0
    /// Banner keys (event + start) already presented. Every refresh republishes
    /// the event list, so without this a dismissed banner would pop up again on
    /// the next refresh for as long as the meeting is inside its lead window.
    private var shown: Set<String> = []
    /// Closes the banner a few minutes after *its* meeting starts.
    private var autoDismiss: DispatchWorkItem?
    private let postStartGrace: TimeInterval = 5 * 60
    /// Minutes before start; 0 turns banners off. Set from Settings.
    var leadMinutes: Int

    init(leadMinutes: Int = 5) {
        self.leadMinutes = leadMinutes
    }

    func reschedule(for events: [TrayEvent]) {
        generation &+= 1
        let gen = generation
        for (_, work) in scheduled { work.cancel() }
        scheduled.removeAll()
        // Forget meetings that left the feed (over, deleted) so the set stays small.
        shown.formIntersection(events.map(TrayNotificationPlan.bannerKey))
        guard leadMinutes > 0 else { return }  // «Не напоминать»

        let now = Date()
        for event in events {
            guard let delay = TrayNotificationPlan.bannerDelay(
                for: event, now: now, leadMinutes: leadMinutes, grace: postStartGrace,
                alreadyShown: shown) else { continue }
            let id = event.id
            let work = DispatchWorkItem { [weak self] in
                // asyncAfter on the main queue: already on the main actor.
                MainActor.assumeIsolated {
                    guard let self, gen == self.generation else { return }
                    self.scheduled.removeValue(forKey: id)
                    self.present(event)
                }
            }
            scheduled[id] = work
            DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
        }
    }

    private func present(_ event: TrayEvent) {
        guard shown.insert(TrayNotificationPlan.bannerKey(event)).inserted else { return }
        scheduleAutoDismiss(for: event)

        let tint = AccountTint.of(event.accountTintHex)
        let accent = tint.base
        let subtitle: String = {
            guard let start = event.startDate else { return event.accountName }
            if let end = event.endDate {
                return "\(event.accountName) · \(TrayFormatters.timeRange(start: start, end: end))"
            }
            return "\(event.accountName) · \(TrayFormatters.hm.string(from: start)) · через \(leadMinutes) мин"
        }()

        let joinURL = event.joinURL
        let view = MeetingReminderBannerView(
            title: event.subject.isEmpty ? "(без темы)" : event.subject,
            subtitle: subtitle,
            joinURL: joinURL,
            accent: accent,
            ink: tint.ink,
            onJoin: { [weak self] in
                if let url = joinURL { NSWorkspace.shared.open(url) }
                self?.dismiss()
            },
            onDismiss: { [weak self] in self?.dismiss() },
            onOpenCalendar: { [weak self] in
                NotificationCenter.default.post(name: .easShowCalendar, object: nil)
                self?.dismiss()
            }
        )

        if let hosting {
            hosting.rootView = view
            resizeToFit()
            positionTopRight(animated: true)
            panel?.orderFrontRegardless()
            return
        }

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 140),
            styleMask: [.nonactivatingPanel, .borderless, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false

        let hosting = NSHostingView(rootView: view)
        hosting.translatesAutoresizingMaskIntoConstraints = false
        let container = NSView(frame: .zero)
        container.addSubview(hosting)
        NSLayoutConstraint.activate([
            hosting.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            hosting.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            hosting.topAnchor.constraint(equalTo: container.topAnchor),
            hosting.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        panel.contentView = container
        self.panel = panel
        self.hosting = hosting
        resizeToFit()
        positionTopRight(animated: false)
        panel.orderFrontRegardless()
    }

    /// Replaces any pending auto-dismiss: when a second meeting's banner reuses
    /// the panel, the first meeting's timer must not close it early.
    private func scheduleAutoDismiss(for event: TrayEvent) {
        autoDismiss?.cancel()
        autoDismiss = nil
        guard let start = event.startDate else { return }
        let until = start.addingTimeInterval(postStartGrace).timeIntervalSinceNow
        guard until > 0 else { return }
        let work = DispatchWorkItem { [weak self] in
            MainActor.assumeIsolated { self?.dismiss() }
        }
        autoDismiss = work
        DispatchQueue.main.asyncAfter(deadline: .now() + until, execute: work)
    }

    private func resizeToFit() {
        guard let panel, let hosting else { return }
        let size = hosting.fittingSize
        panel.setContentSize(NSSize(width: max(340, size.width), height: max(90, size.height)))
    }

    private func positionTopRight(animated: Bool) {
        guard let panel else { return }
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        let size = panel.frame.size
        let origin = NSPoint(
            x: screen.maxX - size.width - 16,
            y: screen.maxY - size.height - 16
        )
        if animated {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.18
                panel.animator().setFrameOrigin(origin)
            }
        } else {
            panel.setFrameOrigin(origin)
        }
    }

    func dismiss() {
        autoDismiss?.cancel()
        autoDismiss = nil
        panel?.close()
        panel = nil
        hosting = nil
    }
}
