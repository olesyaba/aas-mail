import Foundation

struct TrayNotificationRequest: Equatable {
    let identifier: String
    let title: String
    let body: String
    let fireDate: Date
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
        for e in events {
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
                fireDate: fireDate))
        }
        let toCancel = previouslyScheduledIds.subtracting(stillWanted)
        return (toSchedule, Array(toCancel))
    }
}
