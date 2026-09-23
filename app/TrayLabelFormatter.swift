import Foundation
import AppKit

enum TrayLabelFormatter {
    /// Events that may drive the menu-bar label. All-day / free stay in the
    /// popover but never own the countdown (same filter as OWA Widget).
    static func labelCandidates(_ events: [TrayEvent]) -> [TrayEvent] {
        events.filter { !$0.isAllDay && $0.busyStatus?.lowercased() != "free" }
    }

    /// Plain fallback when nothing upcoming (OWA leaves the icon alone).
    static let idleGlyph = "📅"

    /// Menu-bar text matching OWA Widget's default `.countdown` mode
    /// (`MenuBarCountdownFormatter`): compact duration, never the subject.
    ///
    /// - today, &lt; 1 h → `" 1m"` / `"35m"` (ceil, 2-char pad under 10)
    /// - today, ≥ 1 h → `" 1h"` / `"23h"`
    /// - tomorrow → short clock time of the start (`"09:30"`)
    /// - later → `" 2d"`
    /// - nothing upcoming → `📅`
    static func label(events: [TrayEvent], now: Date, isTomorrow: Bool = false,
                      calendar: Calendar = .current) -> String {
        let candidates = labelCandidates(events)
        // Countdown only looks at the next start *after* now. An in-progress
        // meeting is signalled by the icon (▶) only when nothing else follows.
        let upcoming = candidates
            .compactMap { e -> (TrayEvent, Date)? in
                guard let start = e.startDate, start > now else { return nil }
                return (e, start)
            }
            .sorted { $0.1 < $1.1 }

        if let (_, start) = upcoming.first {
            return countdown(from: now, to: start, calendar: calendar) ?? idleGlyph
        }

        // In a meeting with nothing after it — OWA shows icon only; we keep a
        // short remaining-time cue so the tray isn't blank mid-call.
        if let active = candidates.first(where: {
            guard let s = $0.startDate, let e = $0.endDate else { return false }
            return s <= now && now < e
        }), let end = active.endDate {
            let remaining = max(0, Int(ceil(end.timeIntervalSince(now) / 60)))
            return "▶" + pad2(remaining) + "m"
        }

        return idleGlyph
    }

    /// Kept for call sites that still build an attributed title — content is
    /// the countdown string in the *system* label color (wine/emerald tints
    /// were unreadable on the menu bar).
    static func attributedLabel(events: [TrayEvent], now: Date, isTomorrow: Bool,
                                calendar: Calendar = .current) -> NSAttributedString {
        let text = label(events: events, now: now, isTomorrow: isTomorrow, calendar: calendar)
        return NSAttributedString(string: text, attributes: [
            .foregroundColor: NSColor.labelColor,
            .font: NSFont.menuBarFont(ofSize: 0),
        ])
    }

    // MARK: - OWA MenuBarCountdownFormatter port

    private static func countdown(from now: Date, to start: Date,
                                  calendar: Calendar) -> String? {
        let interval = start.timeIntervalSince(now)
        guard interval > 0 else { return nil }

        let minuteBoundary = 60.0
        let hourBoundary = 3600.0
        let dayBoundary = 24 * hourBoundary

        if calendar.isDate(start, inSameDayAs: now) {
            if interval < hourBoundary {
                return pad2(Int(ceil(interval / minuteBoundary))) + "m"
            }
            return pad2(Int(ceil(interval / hourBoundary))) + "h"
        }

        if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now),
           calendar.isDate(start, inSameDayAs: tomorrow) {
            return shortTime(start)
        }

        if interval < hourBoundary {
            return pad2(Int(ceil(interval / minuteBoundary))) + "m"
        }
        if interval >= dayBoundary {
            return pad2(Int(ceil(interval / dayBoundary))) + "d"
        }
        return pad2(Int(ceil(interval / hourBoundary))) + "h"
    }

    /// `" 1"` / `"12"` — OWA pads single-digit values so the tray width stays steady.
    private static func pad2(_ n: Int) -> String {
        String(format: "%2d", max(0, n))
    }

    private static func shortTime(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        f.timeZone = .current
        return f.string(from: date)
    }
}
