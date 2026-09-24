import Foundation

struct TrayOrganizer: Decodable {
    let name: String?
    let address: String?
}

struct TrayAttendee: Decodable {
    let name: String?
    let address: String?
    let status: String?
}

struct TrayEvent: Decodable, Identifiable {
    let itemId: String
    let subject: String
    let startISO: String
    let end: String
    let location: String?
    let isAllDay: Bool
    let busyStatus: String?
    let attendees: [TrayAttendee]?
    let body: String?
    let organizer: TrayOrganizer?
    let meetingStatus: String?
    let responseType: String?

    /// Two independent accounts are merged into one list, and their servers
    /// encode item ids differently — prefix with the account so a collision
    /// can never break `ForEach` rendering or collapse two reminders.
    var id: String { "\(accountId)-\(itemId)" }

    enum CodingKeys: String, CodingKey {
        case itemId = "item_id"
        case subject
        case startISO = "start_iso"
        case end
        case location
        case isAllDay = "is_all_day"
        case busyStatus = "busy_status"
        case attendees
        case body
        case organizer
        case meetingStatus = "meeting_status"
        case responseType = "response_type"
    }

    /// Not part of the server JSON — set by TrayEventMerge after decoding
    /// each account's response, so the popover/notifications know which
    /// calendar (and color) an event came from.
    var accountId: String = ""
    var accountName: String = ""
    var accountColorHex: String = ""

    private static let isoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static let localFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        f.locale = Locale(identifier: "en_US_POSIX")  // fixed format: never follow the region/calendar
        f.timeZone = TimeZone.current
        return f
    }()

    var startDate: Date? { Self.isoFormatter.date(from: startISO) }
    var endDate: Date? {
        guard !end.isEmpty else { return startDate }
        return Self.localFormatter.date(from: end)
    }

    /// Organizer cancelled it (Exchange keeps it in the calendar until removed).
    var isCancelled: Bool { (meetingStatus ?? "").lowercased() == "cancelled" }

    /// Invited meeting where we are not the organizer — show RSVP actions.
    var canRespond: Bool {
        let ms = (meetingStatus ?? "").lowercased()
        if ms == "appointment" || ms == "cancelled" { return false }
        let rt = (responseType ?? "").lowercased()
        if rt == "organizer" { return false }
        if ms == "meeting" { return true }
        if organizer?.address != nil { return true }
        return (attendees?.count ?? 0) > 1
    }

    /// Teams / Zoom / KTalk / Meet link from location or body HTML/text.
    var joinURL: URL? { TrayJoinLink.url(location: location, body: body) }

    /// Brand tint for the source account (matches web tabs).
    var accountTintHex: String {
        if !accountColorHex.isEmpty { return accountColorHex }
        return accountId == "seller" ? "#003830" : "#501820"
    }
}

/// Pull the first usable meeting URL out of location / body text (incl. HTML).
enum TrayJoinLink {
    private static let preferredHosts = [
        "teams.microsoft", "teams.live", "zoom.us", "ktalk", "kontur",
        "meet.google", "trueconf", "jazz.sber", "telemost.yandex", "webex.com",
    ]

    static func url(location: String?, body: String?) -> URL? {
        if let loc = location?.trimmingCharacters(in: .whitespacesAndNewlines),
           let u = Self.httpURL(loc) {
            return u
        }
        // Collect links from every variant (raw, unescaped, HTML-stripped) before
        // choosing, like the web UI's joinURL(): a Teams link that only appears
        // JSON-escaped must still beat an ordinary wiki link earlier in the body.
        let urls = [location, body].compactMap { $0 }
            .flatMap { sources(from: $0) }
            .flatMap { urls(in: $0) }
        return urls.first(where: isMeetingHost) ?? urls.first
    }

    private static func isMeetingHost(_ u: URL) -> Bool {
        let host = u.host?.lowercased() ?? ""
        return preferredHosts.contains { host.contains($0) }
    }

    /// Raw + HTML-stripped + entity-decoded variants, order preserved.
    private static func sources(from text: String) -> [String] {
        let normalized = text
            .replacingOccurrences(of: #"\/"#, with: "/")
            .replacingOccurrences(of: "&amp;", with: "&")
        let plain = stripHTML(normalized)
        var out: [String] = []
        var seen = Set<String>()
        for s in [text, normalized, plain] where seen.insert(s).inserted {
            out.append(s)
        }
        return out
    }

    private static let tagRegex = try? NSRegularExpression(pattern: "<[^>]+>")

    private static func stripHTML(_ string: String) -> String {
        guard string.contains("<"), let regex = tagRegex else { return string }
        let ns = string as NSString
        return regex.stringByReplacingMatches(
            in: string, range: NSRange(location: 0, length: ns.length), withTemplate: " ")
    }

    private static let urlRegex = try? NSRegularExpression(pattern: #"https?://[^\s<>'")\]]+"#)

    private static func urls(in text: String) -> [URL] {
        guard let regex = urlRegex else { return [] }
        let ns = text as NSString
        return regex.matches(in: text, range: NSRange(location: 0, length: ns.length)).compactMap { m in
            var s = ns.substring(with: m.range)
            while s.last == "." || s.last == "," || s.last == ";"
                    || s.last == ")" || s.last == "]" { s.removeLast() }
            return httpURL(s)
        }
    }

    private static func httpURL(_ string: String) -> URL? {
        guard let u = URL(string: string),
              let scheme = u.scheme?.lowercased(),
              scheme == "http" || scheme == "https" else { return nil }
        return u
    }
}

struct TrayEventListResponse: Decodable {
    let ok: Bool
    let items: [TrayEvent]
    /// Present when `ok` is false — e.g. `error: "calendar_unavailable"` with a
    /// human-readable `message` while the calendar cache is still loading.
    let error: String?
    let message: String?

    enum CodingKeys: String, CodingKey { case ok, items, error, message }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decode(Bool.self, forKey: .ok)
        items = try c.decodeIfPresent([TrayEvent].self, forKey: .items) ?? []
        error = try c.decodeIfPresent(String.self, forKey: .error)
        message = try c.decodeIfPresent(String.self, forKey: .message)
    }
}
