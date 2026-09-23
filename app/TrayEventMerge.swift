import Foundation

enum TrayEventMerge {
    /// Tags each event with its source account, merges both lists, optionally
    /// drops anything already over (endDate <= now), and sorts by start time.
    /// `dropPast: false` keeps the full day for the timeline view.
    static func merge(main: [TrayEvent], mainName: String, mainColor: String,
                       seller: [TrayEvent], sellerName: String, sellerColor: String,
                       now: Date, dropPast: Bool = true) -> [TrayEvent] {
        func tag(_ events: [TrayEvent], id: String, name: String, color: String) -> [TrayEvent] {
            events.map { e in
                var e2 = e
                e2.accountId = id; e2.accountName = name; e2.accountColorHex = color
                return e2
            }
        }
        let all = tag(main, id: "main", name: mainName, color: mainColor)
            + tag(seller, id: "seller", name: sellerName, color: sellerColor)
        let filtered = dropPast
            ? all.filter { ($0.endDate ?? $0.startDate ?? .distantPast) > now }
            : all
        return filtered.sorted { ($0.startDate ?? .distantPast) < ($1.startDate ?? .distantPast) }
    }
}
