import SwiftUI
import Combine
import AppKit

/// OWA-style tray calendar: hero card for the current/next meeting + hour
/// timeline with a "now" strip. No create — create lives in the full app.
struct TrayPopoverView: View {
    @ObservedObject var store: TrayDataStore
    @State private var expandedId: String?
    @State private var now = Date()
    private let tick = Timer.publish(every: 15, on: .main, in: .common).autoconnect()

    private let accent = Color(hex: "E8913A")
    private let nowStrip = Color(red: 0.35, green: 0.48, blue: 0.62)

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            dayNav
            if let hero = heroEvent {
                HeroCard(event: hero, now: now, accent: accent)
                    .padding(.horizontal, 10)
                    .padding(.bottom, 8)
            }
            Divider().opacity(0.4)
            timeline
            footer
        }
        .frame(width: 360, height: 520)
        .onReceive(tick) { now = $0 }
    }

    // MARK: - Header (no create)

    private var header: some View {
        HStack(spacing: 10) {
            Text("AAS mail")
                .font(.system(size: 13, weight: .semibold))
            Spacer()
            Button {
                Task { await store.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 12, weight: .medium))
                    .rotationEffect(.degrees(store.isRefreshing ? 360 : 0))
                    .animation(store.isRefreshing
                               ? .linear(duration: 0.8).repeatForever(autoreverses: false)
                               : .default,
                               value: store.isRefreshing)
            }
            .buttonStyle(.plain)
            .help("Обновить")
            .disabled(store.isRefreshing)
        }
        .foregroundColor(.primary)
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .padding(.bottom, 6)
    }

    // MARK: - Day navigator

    private var dayNav: some View {
        HStack {
            Button { store.shiftDay(-1) } label: {
                Image(systemName: "chevron.left")
                    .font(.system(size: 11, weight: .semibold))
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Spacer()
            Button {
                if store.dayOffset != 0 { store.goToday() }
            } label: {
                Text(dayTitle)
                    .font(.system(size: 13, weight: .medium))
            }
            .buttonStyle(.plain)
            .help(store.dayOffset == 0 ? "" : "Вернуться к сегодня")
            Spacer()
            Button { store.shiftDay(1) } label: {
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 6)
        .padding(.bottom, 8)
    }

    private var dayTitle: String {
        let day = store.selectedDay
        let cal = Calendar.current
        let f = DateFormatter()
        f.locale = Locale(identifier: "ru_RU")
        f.dateFormat = "d MMMM"
        let datePart = f.string(from: day)
        if cal.isDateInToday(day) { return "Сегодня, \(datePart)" }
        if cal.isDateInTomorrow(day) { return "Завтра, \(datePart)" }
        if cal.isDateInYesterday(day) { return "Вчера, \(datePart)" }
        f.dateFormat = "EEEE, d MMMM"
        return f.string(from: day).capitalized
    }

    // MARK: - Hero: current or next meeting on the viewed day

    private var heroEvent: TrayEvent? {
        let timed = store.events.filter { !$0.isAllDay }
        if let cur = timed.first(where: {
            guard let s = $0.startDate, let e = $0.endDate else { return false }
            return s <= now && now < e
        }) { return cur }
        return timed.first(where: { ($0.startDate ?? .distantPast) > now })
    }

    // MARK: - Timeline

    @ViewBuilder
    private var timeline: some View {
        if store.events.filter({ !$0.isAllDay }).isEmpty {
            VStack {
                Spacer()
                Text("Нет встреч")
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            timelineScroll
        }
    }

    private var timelineScroll: some View {
        let layout = TimelineLayout(events: store.events, day: store.selectedDay, now: now)
        return ScrollViewReader { proxy in
            ScrollView {
                ZStack(alignment: .topLeading) {
                    // Hour grid
                    VStack(spacing: 0) {
                        ForEach(layout.hours, id: \.self) { hour in
                            HStack(alignment: .top, spacing: 0) {
                                Text(String(format: "%02d:00", hour))
                                    .font(.system(size: 10, weight: .regular).monospacedDigit())
                                    .foregroundColor(.secondary)
                                    .frame(width: TimelineLayout.timeColWidth, alignment: .trailing)
                                    .padding(.trailing, 6)
                                VStack(spacing: 0) {
                                    Rectangle()
                                        .fill(Color.secondary.opacity(0.25))
                                        .frame(height: 1)
                                    Spacer(minLength: 0)
                                }
                                .frame(height: TimelineLayout.hourHeight)
                            }
                            .frame(height: TimelineLayout.hourHeight)
                            .id("hour-\(hour)")
                        }
                    }

                    // Event blocks
                    ForEach(layout.placed) { place in
                        TimelineEventBlock(
                            event: place.event,
                            expanded: expandedId == place.event.id,
                            accent: accent,
                            now: now,
                            onToggle: {
                                withAnimation(.easeInOut(duration: 0.15)) {
                                    expandedId = expandedId == place.event.id ? nil : place.event.id
                                }
                            },
                            onRespond: { response in
                                Task { await store.respond(to: place.event, response: response) }
                            }
                        )
                        .frame(width: place.width, height: max(place.height, 22), alignment: .top)
                        .offset(x: place.x, y: place.y)
                        .id(place.event.id)
                    }

                    // Now strip (only on today)
                    if layout.showNow, let y = layout.nowY {
                        HStack(spacing: 0) {
                            Circle()
                                .fill(nowStrip)
                                .frame(width: 7, height: 7)
                                .offset(x: TimelineLayout.timeColWidth - 2)
                            Rectangle()
                                .fill(nowStrip.opacity(0.55))
                                .frame(height: 14)
                                .cornerRadius(2)
                        }
                        .offset(y: y - 7)
                        .allowsHitTesting(false)
                        .id("now-strip")
                    }
                }
                .frame(height: layout.totalHeight, alignment: .top)
                .padding(.trailing, 8)
                .padding(.bottom, 8)
            }
            .onAppear { scrollToNow(proxy, layout: layout) }
            .onChange(of: store.events.map(\.id)) { _, _ in scrollToNow(proxy, layout: layout) }
            .onChange(of: store.dayOffset) { _, _ in
                expandedId = nil
                scrollToNow(proxy, layout: layout)
            }
        }
    }

    private func scrollToNow(_ proxy: ScrollViewProxy, layout: TimelineLayout) {
        DispatchQueue.main.async {
            if layout.showNow {
                proxy.scrollTo("now-strip", anchor: .center)
            } else if let next = store.events.first(where: {
                ($0.startDate ?? .distantPast) > now
            }) {
                proxy.scrollTo(next.id, anchor: .center)
            } else if let first = layout.hours.first {
                proxy.scrollTo("hour-\(first)", anchor: .top)
            }
        }
    }

    // MARK: - Footer

    private var footer: some View {
        HStack(spacing: 8) {
            if let err = store.lastError {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 10))
                    .foregroundColor(accent)
                Text(err)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                Button("Повторить") {
                    Task { await store.refresh() }
                }
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.accentColor)
                .buttonStyle(.plain)
            } else {
                Text(store.isRefreshing ? "Обновление…" : " ")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
            }
            Spacer()
            Text("v1.1.0")
                .font(.system(size: 9).monospacedDigit())
                .foregroundColor(.secondary.opacity(0.7))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
    }
}

// MARK: - Hero card

private struct HeroCard: View {
    let event: TrayEvent
    let now: Date
    let accent: Color

    private var isNow: Bool {
        guard let s = event.startDate, let e = event.endDate else { return false }
        return s <= now && now < e
    }

    private var statusText: String {
        guard let start = event.startDate else { return "" }
        if isNow, let end = event.endDate {
            let m = max(0, Int(ceil(end.timeIntervalSince(now) / 60)))
            return "осталось \(m) мин"
        }
        let m = max(0, Int(ceil(start.timeIntervalSince(now) / 60)))
        if m < 60 { return "через \(m) мин" }
        let h = m / 60
        let rem = m % 60
        return rem == 0 ? "через \(h) ч" : "через \(h) ч \(rem) мин"
    }

    private var progress: CGFloat {
        guard isNow, let s = event.startDate, let e = event.endDate else { return 0 }
        let total = e.timeIntervalSince(s)
        guard total > 0 else { return 0 }
        return CGFloat(min(1, max(0, now.timeIntervalSince(s) / total)))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Circle().fill(accent).frame(width: 6, height: 6)
                Text(statusText)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(accent)
            }
            Text(event.subject)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(.primary)
                .lineLimit(2)
            HStack(spacing: 6) {
                Text(timeRange)
                    .font(.system(size: 11).monospacedDigit())
                    .foregroundColor(.secondary)
                if let org = event.organizer?.name ?? event.organizer?.address {
                    Text(org)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(accent.opacity(0.2)).frame(height: 3)
                    Capsule().fill(accent).frame(width: max(3, geo.size.width * progress), height: 3)
                }
            }
            .frame(height: 3)
            .opacity(isNow ? 1 : 0)
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(accent.opacity(0.85), lineWidth: 1.5)
        )
    }

    private var timeRange: String {
        guard let start = event.startDate else { return "" }
        let a = Self.hm.string(from: start)
        guard let end = event.endDate else { return a }
        return "\(a)–\(Self.hm.string(from: end))"
    }

    private static let hm: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        return f
    }()
}

// MARK: - Timeline layout math

private struct PlacedEvent: Identifiable {
    var id: String { event.id }
    let event: TrayEvent
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat
}

private struct TimelineLayout {
    static let hourHeight: CGFloat = 52
    static let timeColWidth: CGFloat = 40
    static let contentWidth: CGFloat = 360 - timeColWidth - 16

    let hours: [Int]
    let placed: [PlacedEvent]
    let totalHeight: CGFloat
    let showNow: Bool
    let nowY: CGFloat?

    init(events: [TrayEvent], day: Date, now: Date) {
        let cal = Calendar.current
        let dayStart = cal.startOfDay(for: day)
        let timed = events.filter { !$0.isAllDay && $0.startDate != nil }

        var minH = 8
        var maxH = 20
        for e in timed {
            if let s = e.startDate {
                minH = min(minH, cal.component(.hour, from: s))
            }
            if let end = e.endDate {
                let h = cal.component(.hour, from: end)
                let m = cal.component(.minute, from: end)
                maxH = max(maxH, m > 0 ? h + 1 : max(h, 1))
            }
        }
        minH = max(0, min(minH, 23))
        maxH = min(24, max(maxH, minH + 1))
        let hourList = Array(minH..<maxH)
        self.hours = hourList.isEmpty ? [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18] : hourList

        let gridStart = cal.date(bySettingHour: self.hours.first ?? 8, minute: 0, second: 0, of: dayStart)
            ?? dayStart

        func yOffset(for date: Date) -> CGFloat {
            CGFloat(date.timeIntervalSince(gridStart) / 3600.0) * Self.hourHeight
        }

        // Column packing for overlaps
        var columns: [[TrayEvent]] = []
        let sorted = timed.sorted { ($0.startDate ?? .distantPast) < ($1.startDate ?? .distantPast) }
        for e in sorted {
            let s = e.startDate ?? .distantPast
            var placedCol = false
            for i in columns.indices {
                if let last = columns[i].last,
                   let lastEnd = last.endDate ?? last.startDate,
                   lastEnd <= s {
                    columns[i].append(e)
                    placedCol = true
                    break
                }
            }
            if !placedCol { columns.append([e]) }
        }

        // Group overlapping clusters to share width
        var result: [PlacedEvent] = []
        // Simpler: assign each event its column index and max concurrent
        var colIndex: [String: Int] = [:]
        for (i, col) in columns.enumerated() {
            for e in col { colIndex[e.id] = i }
        }

        for e in sorted {
            guard let start = e.startDate else { continue }
            let end = e.endDate ?? start.addingTimeInterval(30 * 60)
            let y = yOffset(for: start)
            let h = max(22, yOffset(for: end) - y - 2)
            let col = colIndex[e.id] ?? 0
            // How many columns overlap this event's interval
            let concurrent = columns.indices.filter { ci in
                columns[ci].contains { other in
                    guard let os = other.startDate, let oe = other.endDate ?? other.startDate else { return false }
                    return os < end && oe > start
                }
            }.count
            let n = max(1, concurrent)
            let w = (Self.contentWidth / CGFloat(n)) - 4
            let x = Self.timeColWidth + 6 + CGFloat(col) * (w + 4)
            result.append(PlacedEvent(event: e, x: x, y: y, width: w, height: h))
        }
        self.placed = result
        self.totalHeight = CGFloat(self.hours.count) * Self.hourHeight

        let isToday = cal.isDateInToday(day)
        self.showNow = isToday
        if isToday {
            let y = yOffset(for: now)
            self.nowY = (y >= -8 && y <= self.totalHeight + 8) ? y : nil
        } else {
            self.nowY = nil
        }
    }
}

// MARK: - Event block on the timeline

private struct TimelineEventBlock: View {
    let event: TrayEvent
    let expanded: Bool
    let accent: Color
    let now: Date
    var onToggle: () -> Void
    var onRespond: (String) -> Void

    @State private var sending: String?
    @State private var localResponse: String?
    @State private var errorText: String?

    private var borderColor: Color {
        // Warm amber like the reference; seller tint for the second calendar.
        event.accountId == "seller" ? Color(hex: "2A9B7A") : accent
    }

    private var isNow: Bool {
        guard let s = event.startDate, let e = event.endDate else { return false }
        return s <= now && now < e
    }

    private var currentResponse: String {
        (localResponse ?? event.responseType ?? "").lowercased()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onToggle) {
                HStack(alignment: .top, spacing: 0) {
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(borderColor)
                        .frame(width: 3)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(alignment: .top, spacing: 4) {
                            Text(event.subject)
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundColor(.primary)
                                .lineLimit(expanded ? 3 : 2)
                                .multilineTextAlignment(.leading)
                            Spacer(minLength: 0)
                            if let url = joinURL {
                                Button {
                                    NSWorkspace.shared.open(url)
                                } label: {
                                    Image(systemName: "arrow.up.right.circle.fill")
                                        .font(.system(size: 12))
                                        .foregroundColor(borderColor)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        Text(timeRange)
                            .font(.system(size: 10).monospacedDigit())
                            .foregroundColor(borderColor.opacity(0.95))
                        if !expanded, let badge = responseBadge {
                            Text(badge.label)
                                .font(.system(size: 9))
                                .foregroundColor(badge.color)
                        }
                    }
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                meetingDetail
                    .padding(.horizontal, 8)
                    .padding(.bottom, 6)
                    .padding(.leading, 3)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 5)
                .fill(Color(nsColor: .controlBackgroundColor).opacity(isNow ? 1 : 0.92))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 5)
                .stroke(isNow ? borderColor.opacity(0.6) : Color.secondary.opacity(0.15), lineWidth: 1)
        )
    }

    private var joinURL: URL? {
        guard let loc = event.location, !loc.isEmpty,
              let url = URL(string: loc), url.scheme?.hasPrefix("http") == true else { return nil }
        return url
    }

    @ViewBuilder
    private var meetingDetail: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let org = event.organizer {
                Label {
                    Text(org.name ?? org.address ?? "организатор").font(.system(size: 10))
                } icon: {
                    Image(systemName: "person.crop.circle")
                }
                .foregroundColor(.secondary)
            }
            if let location = event.location, !location.isEmpty {
                if let url = URL(string: location), url.scheme?.hasPrefix("http") == true {
                    Link(destination: url) {
                        Label(location, systemImage: "video").font(.system(size: 10)).lineLimit(2)
                    }
                } else {
                    Label(location, systemImage: "mappin").font(.system(size: 10)).lineLimit(2)
                        .foregroundColor(.secondary)
                }
            }
            if let attendees = event.attendees, !attendees.isEmpty {
                Text("Участники · \(attendees.count)")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.secondary)
                ForEach(Array(attendees.prefix(5).enumerated()), id: \.offset) { _, a in
                    HStack(spacing: 3) {
                        Text(a.name ?? a.address ?? "?")
                            .font(.system(size: 9))
                            .lineLimit(1)
                        if let st = a.status, !st.isEmpty {
                            Text("· \(statusRu(st))")
                                .font(.system(size: 9))
                                .foregroundColor(.secondary)
                        }
                    }
                }
                if attendees.count > 5 {
                    Text("и ещё \(attendees.count - 5)…")
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
            }

            if event.canRespond {
                Divider().padding(.vertical, 1)
                HStack(spacing: 4) {
                    rsvpButton(title: "Принять", response: "accept", activeKey: "accepted", color: .green)
                    rsvpButton(title: "?", response: "tentative", activeKey: "tentative", color: .orange)
                    rsvpButton(title: "Откл.", response: "decline", activeKey: "declined", color: .red)
                }
                if let errorText {
                    Text(errorText).font(.system(size: 9)).foregroundColor(.red)
                }
            }
        }
    }

    private func rsvpButton(title: String, response: String, activeKey: String, color: Color) -> some View {
        let active = currentResponse == activeKey
        let busy = sending == response
        return Button {
            sending = response
            errorText = nil
            onRespond(response)
            localResponse = activeKey
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { sending = nil }
        } label: {
            HStack(spacing: 3) {
                if busy { ProgressView().controlSize(.mini) }
                Text(title).font(.system(size: 9, weight: active ? .semibold : .regular))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
            .background(active ? color.opacity(0.18) : Color(nsColor: .controlBackgroundColor))
            .foregroundColor(active ? color : .primary)
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(active ? color : Color.secondary.opacity(0.25)))
            .cornerRadius(5)
        }
        .buttonStyle(.plain)
        .disabled(sending != nil)
    }

    private var timeRange: String {
        guard let start = event.startDate else { return "" }
        let a = Self.hm.string(from: start)
        guard let end = event.endDate else { return a }
        return "\(a)–\(Self.hm.string(from: end))"
    }

    private var responseBadge: (label: String, color: Color)? {
        switch currentResponse {
        case "accepted": return ("✓ принято", .green)
        case "tentative": return ("? под вопросом", .orange)
        case "declined": return ("✕ отклонено", .red)
        case "not_responded", "none": return ("… без ответа", .secondary)
        default: return nil
        }
    }

    private func statusRu(_ s: String) -> String {
        switch s.lowercased() {
        case "accepted": return "принял"
        case "tentative": return "под вопросом"
        case "declined": return "отклонил"
        case "not_responded", "none": return "без ответа"
        case "organizer": return "организатор"
        default: return s
        }
    }

    private static let hm: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        return f
    }()
}

extension Color {
    init(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        s.removeAll { $0 == "#" }
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        self.init(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }
}
