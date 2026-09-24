// Tray logic tests — compiled together with the pure tray sources by tests/run_tests.sh:
//   TrayModels, TrayEventMerge, TrayLabelFormatter, TrayNotificationPlan, TrayFormatters.
// No XCTest dependency (plain swiftc build), so a tiny check harness is used.
import Foundation

var failures = 0, checks = 0
func check(_ cond: @autoclosure () -> Bool, _ msg: String, line: Int = #line) {
    checks += 1
    if !cond() { failures += 1; print("  ✘ line \(line): \(msg)") }
}
func eq<T: Equatable>(_ a: T, _ b: T, _ msg: String = "", line: Int = #line) {
    check(a == b, "\(msg) expected \(b), got \(a)", line: line)
}

let cal = Calendar.current
let iso: ISO8601DateFormatter = { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime]; return f }()
let local: DateFormatter = {
    let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd HH:mm"; return f
}()

/// Decodes like the server JSON does, so tests also cover the CodingKeys.
func event(_ subject: String, start: Date, minutes: Int = 30, allDay: Bool = false, busy: String? = "busy",
           id: String? = nil, location: String? = nil, body: String? = nil, meeting: String? = nil,
           response: String? = nil, organizer: String? = nil, attendees: Int = 0) -> TrayEvent {
    var obj: [String: Any] = [
        "item_id": id ?? subject, "subject": subject, "start_iso": iso.string(from: start),
        "end": local.string(from: start.addingTimeInterval(Double(minutes) * 60)), "is_all_day": allDay,
    ]
    if let busy { obj["busy_status"] = busy }
    if let location { obj["location"] = location }
    if let body { obj["body"] = body }
    if let meeting { obj["meeting_status"] = meeting }
    if let response { obj["response_type"] = response }
    if let organizer { obj["organizer"] = ["name": "Org", "address": organizer] }
    if attendees > 0 { obj["attendees"] = (0..<attendees).map { ["address": "a\($0)@x.test"] } }
    let data = try! JSONSerialization.data(withJSONObject: obj)
    return try! JSONDecoder().decode(TrayEvent.self, from: data)
}

func suite(_ name: String, _ body: () -> Void) {
    let before = failures
    body()
    print(failures == before ? "✔ \(name)" : "✘ \(name)")
}

// Fixed "now": today 10:00 local, so same-day math is stable whatever the wall clock says.
let now = cal.date(bySettingHour: 10, minute: 0, second: 0, of: Date())!

suite("TrayEvent decoding, dates and ids") {
    let e = event("Standup", start: now, minutes: 45)
    eq(e.startDate, now)
    eq(e.endDate, now.addingTimeInterval(45 * 60))
    var tagged = e; tagged.accountId = "seller"
    eq(tagged.id, "seller-Standup", "ids are account-prefixed")
    let data = #"{"ok": false, "error": "calendar_unavailable", "message": "loading"}"#.data(using: .utf8)!
    let r = try! JSONDecoder().decode(TrayEventListResponse.self, from: data)
    check(!r.ok && r.items.isEmpty && r.message == "loading", "error envelope without items decodes")
}

suite("canRespond (RSVP visibility)") {
    check(event("m", start: now, meeting: "meeting").canRespond, "invited meeting")
    check(!event("m", start: now, meeting: "meeting", response: "organizer").canRespond, "own meeting")
    check(!event("m", start: now, meeting: "cancelled").canRespond, "cancelled")
    check(!event("m", start: now, meeting: "appointment", organizer: "o@x").canRespond, "appointment")
    check(event("m", start: now, organizer: "o@x").canRespond, "organizer present → meeting")
    check(!event("m", start: now, attendees: 1).canRespond, "solo appointment")
}

suite("TrayJoinLink") {
    eq(event("m", start: now, location: "https://telemost.yandex.ru/j/1").joinURL?.absoluteString,
       "https://telemost.yandex.ru/j/1", "location URL")
    let html = #"<p>Wiki https://wiki.test/a.</p><a href="https:\/\/teams.microsoft.com\/l\/meetup-join\/x?a=1&amp;b=2">Join</a>"#
    eq(event("m", start: now, location: "Room 5", body: html).joinURL?.absoluteString,
       "https://teams.microsoft.com/l/meetup-join/x?a=1&b=2", "preferred host from HTML body")
    eq(event("m", start: now, body: "docs: https://docs.test/x).").joinURL?.absoluteString,
       "https://docs.test/x", "trailing punctuation stripped")
    check(event("m", start: now, location: "ftp://x", body: "none").joinURL == nil, "no http link")
}

suite("TrayEventMerge") {
    let past = event("past", start: now.addingTimeInterval(-7200))
    let later = event("later", start: now.addingTimeInterval(3600))
    let soon = event("soon", start: now.addingTimeInterval(600))
    let merged = TrayEventMerge.merge(main: [later, past], mainName: "Bank", mainColor: "#1",
                                      seller: [soon], sellerName: "Seller", sellerColor: "#2", now: now)
    eq(merged.map(\.subject), ["soon", "later"], "past dropped, sorted by start")
    eq(merged.map(\.accountId), ["seller", "main"])
    eq(merged[0].accountTintHex, "#2")
    let full = TrayEventMerge.merge(main: [later, past], mainName: "Bank", mainColor: "#1",
                                    seller: [], sellerName: "", sellerColor: "", now: now, dropPast: false)
    eq(full.map(\.subject), ["past", "later"], "dropPast: false keeps the day")
}

suite("TrayLabelFormatter countdown") {
    func label(_ events: [TrayEvent], at t: Date = now) -> String { TrayLabelFormatter.label(events: events, now: t) }
    eq(label([]), TrayLabelFormatter.idleGlyph)
    eq(label([event("a", start: now.addingTimeInterval(60))]), " 1m")
    eq(label([event("a", start: now.addingTimeInterval(35 * 60 - 1))]), "35m", "minutes round up")
    eq(label([event("a", start: now.addingTimeInterval(2 * 3600 + 1))]), " 3h", "hours round up")
    let tomorrow9 = cal.date(bySettingHour: 9, minute: 30, second: 0, of: cal.date(byAdding: .day, value: 1, to: now)!)!
    eq(label([event("a", start: tomorrow9)]), "09:30", "tomorrow shows clock time")
    eq(label([event("a", start: cal.date(byAdding: .day, value: 3, to: now)!)]), " 3d")
    eq(label([event("a", start: now.addingTimeInterval(600), allDay: true),
              event("b", start: now.addingTimeInterval(900), busy: "free")]),
       TrayLabelFormatter.idleGlyph, "all-day and free never drive the label")
    eq(label([event("call", start: now.addingTimeInterval(-600), minutes: 30)]), "▶20m", "in-progress remaining")
    eq(label([event("call", start: now.addingTimeInterval(-600), minutes: 30),
              event("next", start: now.addingTimeInterval(300))]), " 5m", "next start wins over in-progress")
}

suite("TrayNotificationPlan") {
    var e1 = event("A", start: now.addingTimeInterval(3600)); e1.accountId = "main"; e1.accountName = "Bank"
    var e2 = event("B", start: now.addingTimeInterval(120)); e2.accountId = "seller"; e2.accountName = "Seller"
    let (sched, cancel) = TrayNotificationPlan.plan(events: [e1, e2], now: now,
                                                    previouslyScheduledIds: ["eas-bridge-event-main-old"])
    eq(sched.map(\.identifier), ["eas-bridge-event-main-A"], "only reminders still in the future")
    eq(sched[0].fireDate, now.addingTimeInterval(55 * 60))
    eq(sched[0].title, "🔴 Bank")
    eq(cancel, ["eas-bridge-event-main-old"], "stale reminder cancelled")
}

suite("Cancelled meetings: no reminder, no banner, no countdown") {
    var c = event("Отменена", start: now.addingTimeInterval(3600), meeting: "cancelled"); c.accountId = "main"
    let (sched, cancel) = TrayNotificationPlan.plan(events: [c], now: now,
                                                    previouslyScheduledIds: ["eas-bridge-event-main-Отменена"])
    check(sched.isEmpty, "no reminder for a cancelled meeting")
    eq(cancel, ["eas-bridge-event-main-Отменена"], "reminder set before the cancellation is removed")
    check(TrayNotificationPlan.bannerDelay(for: c, now: now, leadMinutes: 5, grace: 300, alreadyShown: []) == nil,
          "no floating banner")
    check(TrayLabelFormatter.labelCandidates([c]).isEmpty, "never drives the menu-bar label")
}

suite("Floating banner: shown once, at the right time") {
    let lead = 5, grace: TimeInterval = 300
    let future = event("F", start: now.addingTimeInterval(3600))
    eq(TrayNotificationPlan.bannerDelay(for: future, now: now, leadMinutes: lead, grace: grace, alreadyShown: []),
       55 * 60, "fires lead minutes before start")
    let inLead = event("L", start: now.addingTimeInterval(120))
    eq(TrayNotificationPlan.bannerDelay(for: inLead, now: now, leadMinutes: lead, grace: grace, alreadyShown: []),
       0.4, "inside lead window → now")
    check(TrayNotificationPlan.bannerDelay(for: inLead, now: now, leadMinutes: lead, grace: grace,
                                           alreadyShown: [TrayNotificationPlan.bannerKey(inLead)]) == nil,
          "dismissed banner is not shown again on the next refresh")
    let moved = event("L", start: now.addingTimeInterval(180))
    check(TrayNotificationPlan.bannerDelay(for: moved, now: now, leadMinutes: lead, grace: grace,
                                           alreadyShown: [TrayNotificationPlan.bannerKey(inLead)]) != nil,
          "a rescheduled meeting gets a new banner")
    let longAgo = event("P", start: now.addingTimeInterval(-600), minutes: 60)
    check(TrayNotificationPlan.bannerDelay(for: longAgo, now: now, leadMinutes: lead, grace: grace, alreadyShown: []) == nil,
          "past the grace period → skip")
    check(TrayNotificationPlan.bannerDelay(for: event("D", start: now.addingTimeInterval(600), allDay: true),
                                           now: now, leadMinutes: lead, grace: grace, alreadyShown: []) == nil,
          "all-day → skip")
}

suite("Reminder lead from Settings") {
    let e = event("L15", start: now.addingTimeInterval(20 * 60))
    eq(TrayNotificationPlan.bannerDelay(for: e, now: now, leadMinutes: 15, grace: 300, alreadyShown: []),
       5 * 60, "15-minute lead fires 5 min from now for a meeting in 20")
    let (s15, _) = TrayNotificationPlan.plan(events: [e], now: now, leadMinutes: 15, previouslyScheduledIds: [])
    eq(s15.first?.body, "L15 через 15 мин")
    let (off, cancel) = TrayNotificationPlan.plan(events: [], now: now, leadMinutes: 1,
                                                  previouslyScheduledIds: ["eas-bridge-event-x"])
    check(off.isEmpty && cancel == ["eas-bridge-event-x"], "turning reminders off cancels pending ones")
}

suite("MailSound from Settings") {
    eq(MailSound.default, MailSound.notice14)
    check(MailSound.allowed.contains("notice14") && MailSound.allowed.contains("short")
          && MailSound.allowed.contains("none"), "three sound choices")
    eq(MailSound.none.notificationSound == nil, true, "none → silent banner")
    check(MailSound.notice14.notificationSound != nil, "notice14 has a named sound")
    check(MailSound.short.notificationSound != nil, "short has a named sound")
    check(MailSound(rawValue: "boom") == nil, "unknown raw value rejected")
}

suite("TrayFormatters") {
    eq(TrayFormatters.timeRange(start: now, end: now.addingTimeInterval(5400)), "10:00–11:30")
    eq(TrayFormatters.timeRange(start: now, end: nil), "10:00")
    eq(TrayFormatters.timeRange(start: nil, end: now), "")
}

print("\(checks - failures)/\(checks) checks passed")
exit(failures == 0 ? 0 : 1)
