import Foundation

/// Shared date formatters. Creating a DateFormatter is expensive and the popover
/// re-renders every 15 s, so views and the menu-bar label reuse these instead of
/// building new ones per event per render. All UI code runs on the main thread.
enum TrayFormatters {
    private static func make(_ format: String, locale: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: locale)
        f.dateFormat = format
        f.timeZone = .autoupdatingCurrent  // follow the Mac across time-zone changes
        return f
    }

    /// "09:30" — fixed 24 h clock regardless of region settings.
    static let hm = make("HH:mm", locale: "en_US_POSIX")
    /// "23 сентября"
    static let dayMonth = make("d MMMM", locale: "ru_RU")
    /// "ср, 23 сентября" (capitalise at the call site)
    static let weekdayDayMonth = make("EEE, d MMMM", locale: "ru_RU")
    /// "С" — single-letter weekday for the week strip.
    static let weekdayLetter = make("EEEEE", locale: "ru_RU")
    /// "23"
    static let dayNumber = make("d", locale: "ru_RU")
    /// "2026-09-23" — day handed to the web UI.
    static let isoDay = make("yyyy-MM-dd", locale: "en_US_POSIX")

    /// "09:30–10:00", or just the start when there is no end.
    static func timeRange(start: Date?, end: Date?) -> String {
        guard let start else { return "" }
        guard let end else { return hm.string(from: start) }
        return "\(hm.string(from: start))–\(hm.string(from: end))"
    }
}
