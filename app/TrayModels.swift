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
