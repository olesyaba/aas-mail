import AppKit
import Foundation
import UserNotifications

let trayNotificationIdPrefix = "eas-bridge-event-"

/// macOS suppresses notification banners for the frontmost app unless a
/// delegate opts in. This is a mail client whose main window is frequently
/// frontmost, so without this the reminders would silently do nothing in
/// exactly the situation where the user is sitting at their desk.
final class ForegroundNotificationPresenter: NSObject, UNUserNotificationCenterDelegate {
    static let categoryID = "EAS_MEETING_JOIN"
    static let joinActionID = "JOIN_MEETING"
    static let joinURLKey = "joinURL"

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                 willPresent notification: UNNotification,
                                 withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                 didReceive response: UNNotificationResponse,
                                 withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        if response.actionIdentifier == Self.joinActionID
            || response.actionIdentifier == UNNotificationDefaultActionIdentifier,
           let s = info[Self.joinURLKey] as? String,
           let url = URL(string: s) {
            NSWorkspace.shared.open(url)
        }
        completionHandler()
    }
}

@MainActor
final class NotificationScheduler {
    private var scheduledIds: Set<String> = []
    /// Minutes before start; 0 = no reminders (Settings).
    var leadMinutes = 5
    /// UNUserNotificationCenter.delegate is `weak` — this is the strong ref.
    private let presenter = ForegroundNotificationPresenter()
    private var adoptedPending = false
    private var lastEvents: [TrayEvent] = []
    private var rescheduledBeforeAdoption = false

    init() {
        UNUserNotificationCenter.current().delegate = presenter
    }

    func requestAuthorizationIfNeeded() {
        let join = UNNotificationAction(
            identifier: ForegroundNotificationPresenter.joinActionID,
            title: "Подключиться",
            options: [.foreground])
        let cat = UNNotificationCategory(
            identifier: ForegroundNotificationPresenter.categoryID,
            actions: [join],
            intentIdentifiers: [],
            options: [])
        UNUserNotificationCenter.current().setNotificationCategories([cat])
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
        adoptPendingRequests()
    }

    /// UNUserNotificationCenter keeps pending requests across launches, but
    /// `scheduledIds` starts empty every launch — so a reminder for an event
    /// that was deleted or moved while the app was closed would never be
    /// recognised as stale and would keep firing forever. Seed the set from
    /// whatever the system still holds for us, before the first reschedule.
    private func adoptPendingRequests() {
        UNUserNotificationCenter.current().getPendingNotificationRequests { [weak self] requests in
            let ids = requests.map { $0.identifier }.filter { $0.hasPrefix(trayNotificationIdPrefix) }
            // The callback runs on an arbitrary queue; scheduledIds is main-actor state.
            Task { @MainActor in self?.finishAdoption(ids: ids) }
        }
    }

    private func finishAdoption(ids: [String]) {
        guard !adoptedPending else { return }
        adoptedPending = true
        scheduledIds.formUnion(ids)
        // If a refresh beat the (async) adoption, redo it now that the stale
        // identifiers are known, so they get cancelled straight away.
        if rescheduledBeforeAdoption {
            rescheduledBeforeAdoption = false
            reschedule(for: lastEvents)
        }
    }

    /// Called on every TrayDataStore refresh with the freshly merged event
    /// list — cancels reminders for events that dropped out (deleted /
    /// already started) and schedules the current ones, via
    /// TrayNotificationPlan's pure diff so this stays a thin side-effecting
    /// wrapper around UNUserNotificationCenter.
    func reschedule(for events: [TrayEvent]) {
        lastEvents = events
        if !adoptedPending { rescheduledBeforeAdoption = true }
        let (toSchedule, toCancel) = TrayNotificationPlan.plan(
            events: leadMinutes > 0 ? events : [], now: Date(), leadMinutes: max(leadMinutes, 1),
            previouslyScheduledIds: scheduledIds)
        let center = UNUserNotificationCenter.current()
        if !toCancel.isEmpty { center.removePendingNotificationRequests(withIdentifiers: toCancel) }
        for req in toSchedule {
            let content = UNMutableNotificationContent()
            content.title = req.title
            content.body = req.body
            content.sound = .default
            content.categoryIdentifier = ForegroundNotificationPresenter.categoryID
            if let url = req.joinURL {
                content.userInfo = [ForegroundNotificationPresenter.joinURLKey: url.absoluteString]
                content.body = req.body + " · Подключиться"
            }
            let interval = max(1, req.fireDate.timeIntervalSinceNow)
            let trigger = UNTimeIntervalNotificationTrigger(timeInterval: interval, repeats: false)
            center.add(UNNotificationRequest(identifier: req.identifier, content: content, trigger: trigger)) { error in
                if let error = error { NSLog("tray: не удалось запланировать напоминание %@: %@", req.identifier, "\(error)") }
            }
        }
        scheduledIds.subtract(Set(toCancel))
        scheduledIds.formUnion(toSchedule.map { $0.identifier })
    }
}
