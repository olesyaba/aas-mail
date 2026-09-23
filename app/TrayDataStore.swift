import Foundation

@MainActor
final class TrayDataStore: ObservableObject {
    /// Events for the day shown in the popover timeline (includes past).
    @Published var events: [TrayEvent] = []
    /// Always today→tomorrow upcoming feed for the menu-bar countdown — independent
    /// of which day the user is browsing in the popover.
    @Published var labelEvents: [TrayEvent] = []
    /// True when `labelEvents` are tomorrow's (nothing left today).
    @Published var isTomorrow = false
    /// Days relative to today that the popover is showing (0 = today).
    @Published var dayOffset: Int = 0
    @Published var lastError: String?
    @Published var isRefreshing = false
    private(set) var hasSeller = false

    static let mainColor = "#501820"
    static let sellerColor = "#003830"

    /// Poll interval while the popover is open — the user is watching the list.
    static let activeInterval: TimeInterval = 60
    /// Poll interval while the popover is closed. Every request resets
    /// webapp.py's 15-minute idle window (`_last_activity` / `_cal_keepfresh`),
    /// so polling on a short beat for the whole life of the app would force a
    /// full ActiveSync sync every few minutes forever. A 20-minute beat is
    /// longer than that window, so the backend actually gets to idle, while
    /// the menu-bar label still notices events created/cancelled elsewhere.
    static let idleInterval: TimeInterval = 20 * 60

    private let client = TrayAPIClient()
    private var timer: Timer?
    private var accountsResolved = false

    var selectedDay: Date {
        Calendar.current.startOfDay(
            for: Calendar.current.date(byAdding: .day, value: dayOffset, to: Date()) ?? Date())
    }

    func start(interval: TimeInterval = 60) {
        stop()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
        Task { await refresh() }
    }

    func stop() { timer?.invalidate(); timer = nil }

    func shiftDay(_ delta: Int) {
        dayOffset += delta
        Task { await refresh() }
    }

    func goToday() {
        dayOffset = 0
        Task { await refresh() }
    }

    func refresh() async {
        isRefreshing = true
        defer { isRefreshing = false }

        if !accountsResolved {
            do {
                hasSeller = try await client.accountIds().contains("seller")
                accountsResolved = true
            } catch {
                lastError = "Нет связи с сервером: \(Self.short(error))"
                return
            }
        }
        let cal = Calendar.current
        let now = Date()
        let df = ISO8601DateFormatter(); df.formatOptions = [.withFullDate]; df.timeZone = TimeZone.current

        // Menu-bar feed: today (upcoming only), fall back to tomorrow if empty.
        let todayStart = cal.startOfDay(for: now)
        let todayEnd = cal.date(byAdding: .day, value: 1, to: todayStart)!
        var notes: [String] = []
        async let mainToday = fetchAccount("main", start: todayStart, end: todayEnd, formatter: df)
        async let sellerToday = fetchAccount("seller", start: todayStart, end: todayEnd, formatter: df)
        let (mainTodayR, sellerTodayR) = await (mainToday, sellerToday)
        let mainTodayItems = resolve(mainTodayR, acct: "main", name: "Alfa-Bank", notes: &notes)
        let sellerTodayItems = resolve(sellerTodayR, acct: "seller", name: "Alfa-Seller", notes: &notes)
        var label = TrayEventMerge.merge(
            main: mainTodayItems, mainName: "Alfa-Bank", mainColor: Self.mainColor,
            seller: sellerTodayItems, sellerName: "Alfa-Seller", sellerColor: Self.sellerColor,
            now: now, dropPast: true)
        var tomorrow = false
        if label.isEmpty && notes.isEmpty {
            let tomorrowStart = todayEnd
            let tomorrowEnd = cal.date(byAdding: .day, value: 1, to: tomorrowStart)!
            async let mainTomorrow = fetchAccount("main", start: tomorrowStart, end: tomorrowEnd, formatter: df)
            async let sellerTomorrow = fetchAccount("seller", start: tomorrowStart, end: tomorrowEnd, formatter: df)
            let (mainNextR, sellerNextR) = await (mainTomorrow, sellerTomorrow)
            let mainNext = resolve(mainNextR, acct: "main", name: "Alfa-Bank", notes: &notes, carryOver: false)
            let sellerNext = resolve(sellerNextR, acct: "seller", name: "Alfa-Seller", notes: &notes, carryOver: false)
            label = TrayEventMerge.merge(
                main: mainNext, mainName: "Alfa-Bank", mainColor: Self.mainColor,
                seller: sellerNext, sellerName: "Alfa-Seller", sellerColor: Self.sellerColor,
                now: todayEnd.addingTimeInterval(-1), dropPast: true)
            tomorrow = !label.isEmpty
        }

        // Popover day: full day including past (timeline needs them).
        let dayStart = selectedDay
        let dayEnd = cal.date(byAdding: .day, value: 1, to: dayStart)!
        // Reuse today's fetch when browsing today to avoid a duplicate round-trip.
        let dayEvents: [TrayEvent]
        if dayOffset == 0 {
            dayEvents = TrayEventMerge.merge(
                main: mainTodayItems, mainName: "Alfa-Bank", mainColor: Self.mainColor,
                seller: sellerTodayItems, sellerName: "Alfa-Seller", sellerColor: Self.sellerColor,
                now: now, dropPast: false)
        } else {
            async let mainDay = fetchAccount("main", start: dayStart, end: dayEnd, formatter: df)
            async let sellerDay = fetchAccount("seller", start: dayStart, end: dayEnd, formatter: df)
            let (mainDayR, sellerDayR) = await (mainDay, sellerDay)
            let mainDayItems = resolve(mainDayR, acct: "main", name: "Alfa-Bank", notes: &notes, carryOver: false)
            let sellerDayItems = resolve(sellerDayR, acct: "seller", name: "Alfa-Seller", notes: &notes, carryOver: false)
            dayEvents = TrayEventMerge.merge(
                main: mainDayItems, mainName: "Alfa-Bank", mainColor: Self.mainColor,
                seller: sellerDayItems, sellerName: "Alfa-Seller", sellerColor: Self.sellerColor,
                now: now, dropPast: false)
        }

        isTomorrow = tomorrow
        lastError = notes.isEmpty ? nil : notes.joined(separator: " · ")
        labelEvents = label
        events = dayEvents
    }

    /// One account's fetch, as a Result so a failure on one side never
    /// discards the other side's good data (`async let` + `try await` used to
    /// abandon both).
    private func fetchAccount(_ acct: String, start: Date, end: Date,
                               formatter: ISO8601DateFormatter) async -> Result<[TrayEvent], Error> {
        if acct == "seller" && !hasSeller { return .success([]) }
        do {
            return .success(try await client.listEvents(acct: acct, start: formatter.string(from: start),
                                                         end: formatter.string(from: end)))
        } catch {
            return .failure(error)
        }
    }

    /// On failure: record a note naming the account and (for today's window)
    /// keep that account's last known events, so a transient backend blip does
    /// not blank the list — which would also cancel every pending reminder.
    private func resolve(_ result: Result<[TrayEvent], Error>, acct: String, name: String,
                          notes: inout [String], carryOver: Bool = true) -> [TrayEvent] {
        switch result {
        case .success(let items):
            return items
        case .failure(let error):
            notes.append("\(name): \(Self.short(error))")
            return carryOver ? events.filter { $0.accountId == acct } : []
        }
    }

    private static func short(_ error: Error) -> String {
        if let api = error as? TrayAPIError, case .serverError(let message) = api { return message }
        return (error as? LocalizedError)?.errorDescription ?? "\(error)"
    }

    /// RSVP from the tray meeting card; refreshes the list after a successful reply.
    func respond(to event: TrayEvent, response: String) async {
        do {
            try await client.respondToEvent(acct: event.accountId, itemId: event.itemId, response: response)
            await refresh()
        } catch {
            lastError = "Ответ на встречу: \(Self.short(error))"
        }
    }
}
