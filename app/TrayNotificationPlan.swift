import Foundation

struct TrayNotificationRequest: Equatable {
    let identifier: String
    let title: String
    let body: String
    let fireDate: Date
    let joinURL: URL?
}

enum TrayNotificationPlan {
    /// Given the current merged event list and "now", returns the reminder
    /// requests that should be scheduled (start - leadMinutes, only if still
    /// in the future) plus the identifiers of previously-scheduled reminders
    /// that are no longer relevant (event dropped out of the list) and must
    /// be cancelled first, to avoid duplicate/stale notifications piling up
    /// across refreshes.
    static func plan(events: [TrayEvent], now: Date, leadMinutes: Int = 5,
                      previouslyScheduledIds: Set<String>) -> (toSchedule: [TrayNotificationRequest], toCancel: [String]) {
        var toSchedule: [TrayNotificationRequest] = []
        var stillWanted: Set<String> = []
        for e in events where !e.isCancelled {  // a dropped reminder is cancelled below
            guard let start = e.startDate else { continue }
            let fireDate = start.addingTimeInterval(-Double(leadMinutes) * 60)
            guard fireDate > now else { continue }
            // e.id is account-prefixed, so two accounts' events can never
            // collapse onto one reminder identifier.
            let id = "eas-bridge-event-\(e.id)"
            stillWanted.insert(id)
            let marker = e.accountId == "seller" ? "🟢" : "🔴"
            toSchedule.append(TrayNotificationRequest(
                identifier: id,
                title: "\(marker) \(e.accountName)",
                body: "\(e.subject) через \(leadMinutes) мин",
                fireDate: fireDate,
                joinURL: e.joinURL))
        }
        let toCancel = previouslyScheduledIds.subtracting(stillWanted)
        return (toSchedule, Array(toCancel))
    }

    /// Identity of one floating-banner showing: a moved meeting (new start)
    /// deserves a fresh reminder, the same meeting on the next refresh does not.
    static func bannerKey(_ e: TrayEvent) -> String { "\(e.id)|\(e.startISO)" }

    /// Seconds until the floating banner for `e` should appear, or nil if it
    /// should not be shown at all: all-day/finished events, events already
    /// shown (`alreadyShown` keys), or events more than `grace` past their start.
    static func bannerDelay(for e: TrayEvent, now: Date, leadMinutes: Int, grace: TimeInterval,
                            alreadyShown: Set<String>) -> TimeInterval? {
        guard !e.isAllDay, !e.isCancelled, let start = e.startDate, let end = e.endDate, end > now,
              !alreadyShown.contains(bannerKey(e)) else { return nil }
        let fire = start.addingTimeInterval(-Double(leadMinutes) * 60)
        if fire > now { return fire.timeIntervalSince(now) }
        // Inside the lead window or just after start (app launched late): show now.
        return start.addingTimeInterval(grace) > now ? 0.4 : nil
    }
}
