import SwiftUI
import Combine
import AppKit

/// OWA-style tray calendar: compact next-meeting cue, week strip, all-day row,
/// hour timeline. Create/detail live as full-popover swaps (NSPopover-safe).
struct TrayPopoverView: View {
    @ObservedObject var store: TrayDataStore
    @State private var detailEvent: TrayEvent?
    @State private var now = Date()
    @State private var scrollNonce = 0
    private let tick = Timer.publish(every: 15, on: .main, in: .common).autoconnect()

    private let bank = AccountTint.of(TrayDataStore.mainColor)
    private let seller = AccountTint.of(TrayDataStore.sellerColor)
    private let nowStrip = Color(red: 0.35, green: 0.48, blue: 0.62)

    var body: some View {
        Group {
            if let event = detailEvent {
                EventDetailPane(
                    event: event,
                    now: now,
                    onClose: { detailEvent = nil },
                    onRespond: { response in
                        Task { await store.respond(to: event, response: response) }
                    }
                )
            } else {
                mainCalendar
            }
        }
        .frame(width: 360, height: 520)
        // Soft, theme-aware backing over the frosted panel material: calm enough
        // not to glare, opaque enough that busy wallpapers don't bleed into text.
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.72))
        .onReceive(tick) { now = $0 }
    }

    // MARK: - Main

    private var mainCalendar: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            weekStrip
            allDayStrip
            if let hero = heroEvent {
                if shouldShowFullHero(hero) {
                    HeroCard(event: hero, now: now, tint: tint(for: hero))
                        .padding(.horizontal, 10)
                        .padding(.bottom, 6)
                } else {
                    CompactHeroRow(event: hero, now: now, tint: tint(for: hero))
                        .padding(.horizontal, 10)
                        .padding(.bottom, 6)
                }
            }
            Divider().opacity(0.35)
            timeline
            footer
        }
    }

    /// One place to create meetings: the main window's «Новое событие» form, on the
    /// day being browsed here (the tray used to have its own, smaller form).
    /// Optional hour/minute pre-fill a slot click (same as Outlook calendar).
    private func createInMainWindow(hour: Int? = nil, minute: Int = 0) {
        var info: [AnyHashable: Any] = [
            "day": TrayFormatters.isoDay.string(from: store.selectedDay)
        ]
        if let hour {
            info["hour"] = hour
            info["minute"] = minute
        }
        NotificationCenter.default.post(name: .easCreateEvent, object: nil, userInfo: info)
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 8) {
            Text(dayTitle)
                .font(.system(size: 13, weight: .semibold))
                .lineLimit(1)
            Spacer(minLength: 4)
            // Day navigation beyond the ±3-day strip, and a way back.
            HStack(spacing: 2) {
                Button { store.shiftDay(-1) } label: {
                    Image(systemName: "chevron.left").font(.system(size: 11, weight: .semibold)).frame(width: 20, height: 20)
                }
                .help("Предыдущий день")
                Button("Сегодня") {
                    store.goToday()
                    now = Date()
                    scrollNonce += 1
                }
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.accentColor)
                    .padding(.horizontal, 4)
                Button { store.shiftDay(1) } label: {
                    Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold)).frame(width: 20, height: 20)
                }
                .help("Следующий день")
            }
            .buttonStyle(.plain)
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
        .padding(.bottom, 4)
    }

    private var dayTitle: String {
        let day = store.selectedDay
        let cal = Calendar.current
        let datePart = TrayFormatters.dayMonth.string(from: day)
        if cal.isDateInToday(day) { return "Сегодня, \(datePart)" }
        if cal.isDateInTomorrow(day) { return "Завтра, \(datePart)" }
        if cal.isDateInYesterday(day) { return "Вчера, \(datePart)" }
        return TrayFormatters.weekdayDayMonth.string(from: day).capitalized
    }

    // MARK: - Week strip (Mon…Sun of the selected day's week)

    private var weekStrip: some View {
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        // Monday of the week that holds the selected day (ISO weeks, like the web calendar).
        let weekday = (cal.component(.weekday, from: store.selectedDay) + 5) % 7  // Mon = 0
        let firstOffset = store.dayOffset - weekday

        return HStack(spacing: 2) {
            ForEach(firstOffset...(firstOffset + 6), id: \.self) { offset in
                let day = cal.date(byAdding: .day, value: offset, to: today) ?? today
                let selected = offset == store.dayOffset
                let isToday = offset == 0
                Button {
                    store.setDayOffset(offset)
                } label: {
                    VStack(spacing: 2) {
                        Text(TrayFormatters.weekdayLetter.string(from: day).uppercased())
                            .font(.system(size: 9, weight: .medium))
                            .foregroundColor(selected ? .white.opacity(0.85) : .secondary)
                        Text(TrayFormatters.dayNumber.string(from: day))
                            .font(.system(size: 12, weight: selected || isToday ? .semibold : .regular))
                            .foregroundColor(selected ? .white : .primary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 5)
                    .background(
                        RoundedRectangle(cornerRadius: 7)
                            .fill(selected ? bank.base : (isToday ? bank.wash : Color.clear))
                    )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 8)
        .padding(.bottom, 6)
    }

    // MARK: - All-day

    private var allDayEvents: [TrayEvent] {
        store.visibleEvents.filter(\.isAllDay)
    }

    @ViewBuilder
    private var allDayStrip: some View {
        let items = allDayEvents
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                ForEach(items) { event in
                    Button {
                        detailEvent = event
                    } label: {
                        HStack(spacing: 6) {
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(tint(for: event).base)
                                .frame(width: 3, height: 14)
                            Text(event.subject).strikethrough(event.isCancelled)
                                .font(.system(size: 11, weight: .medium))
                                .lineLimit(1)
                                .foregroundColor(.primary)
                            Spacer(minLength: 0)
                            Text("весь день")
                                .font(.system(size: 9))
                                .foregroundColor(.secondary)
                        }
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(
                            RoundedRectangle(cornerRadius: 5)
                                .fill(tint(for: event).wash)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
        }
    }

    // MARK: - Hero

    private var timedEvents: [TrayEvent] {
        store.visibleEvents.filter { !$0.isAllDay }
    }

    private var heroEvent: TrayEvent? {
        let timed = timedEvents
        if let cur = timed.first(where: {
            guard let s = $0.startDate, let e = $0.endDate else { return false }
            return s <= now && now < e
        }) { return cur }
        return timed.first(where: { ($0.startDate ?? .distantPast) > now })
    }

    private func shouldShowFullHero(_ event: TrayEvent) -> Bool {
        guard let start = event.startDate else { return false }
        if let end = event.endDate, start <= now && now < end { return true }
        return start.timeIntervalSince(now) <= 30 * 60 && start > now
    }

    private func tint(for event: TrayEvent) -> AccountTint {
        AccountTint.of(event.accountTintHex)
    }

    // MARK: - Timeline

    @ViewBuilder
    private var timeline: some View {
        // Always show the hour grid so empty slots are clickable (Outlook-style create).
        timelineScroll
    }

    private var timelineScroll: some View {
        let layout = TimelineLayout(events: store.visibleEvents, day: store.selectedDay, now: now)
        let emptyTimed = timedEvents.isEmpty
        return ScrollViewReader { proxy in
            ScrollView {
                ZStack(alignment: .topLeading) {
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
                                        .fill(Color.secondary.opacity(0.22))
                                        .frame(height: 1)
                                    Spacer(minLength: 0)
                                }
                                .frame(height: TimelineLayout.hourHeight)
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    createInMainWindow(hour: hour, minute: 0)
                                }
                                .help("Создать встречу в \(String(format: "%02d:00", hour))")
                            }
                            .frame(height: TimelineLayout.hourHeight)
                            .id("hour-\(hour)")
                        }
                    }

                    if emptyTimed {
                        Text(allDayEvents.isEmpty ? "Свободный день — нажмите на слот" : "Нет встреч по часам — нажмите на слот")
                            .font(.system(size: 12))
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 48)
                            .padding(.leading, TimelineLayout.timeColWidth + 8)
                            .allowsHitTesting(false)
                    }

                    ForEach(layout.placed) { place in
                        TimelineEventBlock(
                            event: place.event,
                            tint: tint(for: place.event),
                            now: now,
                            width: place.width,
                            height: place.height,
                            onOpen: { detailEvent = place.event }
                        )
                        .frame(width: place.width, height: place.height, alignment: .top)
                        .offset(x: place.x, y: place.y)
                        .id(place.event.id)
                    }

                    if layout.showNow, let y = layout.nowY {
                        HStack(spacing: 0) {
                            Circle()
                                .fill(nowStrip)
                                .frame(width: 6, height: 6)
                                .offset(x: TimelineLayout.timeColWidth - 1)
                            Rectangle()
                                .fill(nowStrip.opacity(0.75))
                                .frame(height: 1.5)
                        }
                        .offset(y: y - 3)
                        .allowsHitTesting(false)
                        .id("now-strip")
                    }
                }
                .frame(height: layout.totalHeight, alignment: .top)
                .padding(.trailing, 8)
                .padding(.bottom, 8)
            }
            .onAppear { scrollToNow(proxy, layout: layout) }
            .onChange(of: store.visibleEvents.map(\.id)) { _ in scrollToNow(proxy, layout: layout) }
            .onChange(of: store.dayOffset) { _ in
                detailEvent = nil
                scrollToNow(proxy, layout: layout)
            }
            .onChange(of: scrollNonce) { _ in scrollToNow(proxy, layout: layout) }
        }
    }

    private func scrollToNow(_ proxy: ScrollViewProxy, layout: TimelineLayout) {
        DispatchQueue.main.async {
            if layout.showNow {
                proxy.scrollTo("now-strip", anchor: .center)
            } else if let next = store.visibleEvents.first(where: {
                !$0.isAllDay && ($0.startDate ?? .distantPast) > now
            }) {
                proxy.scrollTo(next.id, anchor: .center)
            } else if let first = layout.hours.first {
                proxy.scrollTo("hour-\(first)", anchor: .top)
            }
        }
    }

    // MARK: - Footer

    private var footer: some View {
        VStack(spacing: 4) {
            if let err = store.lastError {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 10))
                        .foregroundColor(.orange)
                    Text(err)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .help(err)
                    Button("Повторить") {
                        Task { await store.refresh() }
                    }
                    .font(.system(size: 10, weight: .medium))
                    .buttonStyle(.plain)
                    .foregroundColor(.accentColor)
                    Spacer(minLength: 0)
                }
            }
            HStack(spacing: 8) {
                accountLegend
                Spacer(minLength: 4)
                if let t = store.lastRefreshed, store.lastError == nil {
                    Text(refreshedLabel(t))
                        .font(.system(size: 9))
                        .foregroundColor(.secondary.opacity(0.8))
                }
                Button("Создать") { createInMainWindow() }
                    .font(.system(size: 11, weight: .medium))
                    .buttonStyle(.plain)
                    .foregroundColor(.accentColor)
                Button("Календарь") {
                    NotificationCenter.default.post(name: .easShowCalendar, object: nil)
                }
                .font(.system(size: 11, weight: .medium))
                .buttonStyle(.plain)
                .foregroundColor(.accentColor)
                .help("Открыть полный календарь")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
    }

    /// Legend doubles as the calendar switch: click Bank / Seller to hide or show it.
    private var accountLegend: some View {
        HStack(spacing: 4) {
            legendToggle(id: "main", color: bank, title: "Bank")
            if store.hasSeller {
                legendToggle(id: "seller", color: seller, title: "Seller")
            }
        }
    }

    private func legendToggle(id: String, color: AccountTint, title: String) -> some View {
        let on = !store.hiddenAccounts.contains(id)
        return Button {
            store.toggleAccount(id)
        } label: {
            HStack(spacing: 4) {
                Circle()
                    .strokeBorder(color.ink, lineWidth: 1.5)
                    .background(Circle().fill(on ? color.ink : Color.clear))
                    .frame(width: 9, height: 9)
                Text(title)
                    .font(.system(size: 11, weight: on ? .medium : .regular))
                    .foregroundColor(on ? .primary : .secondary)
                    .strikethrough(!on, color: .secondary)
                    .lineLimit(1)
                    .fixedSize()
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(RoundedRectangle(cornerRadius: 5).fill(on ? color.wash : Color.clear))
        }
        .buttonStyle(.plain)
        .help(on ? "Скрыть календарь \(title)" : "Показать календарь \(title)")
    }

    private func refreshedLabel(_ date: Date) -> String {
        "обн. \(TrayFormatters.hm.string(from: date))"
    }
}

// MARK: - Compact hero (next meeting > 30 min away)

private struct CompactHeroRow: View {
    let event: TrayEvent
    let now: Date
    let tint: AccountTint

    var body: some View {
        HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 1.5).fill(tint.base).frame(width: 3, height: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(event.subject).strikethrough(event.isCancelled)
                    .font(.system(size: 12, weight: .semibold))
                    .lineLimit(1)
                Text(statusText)
                    .font(.system(size: 11).monospacedDigit())
                    .foregroundColor(.secondary)
            }
            Spacer(minLength: 4)
            if let url = event.joinURL {
                Button {
                    NSWorkspace.shared.open(url)
                } label: {
                    // Same filled, labelled button as the full hero card and the
                    // web UI — an icon-only brand chip read as «a button with no text».
                    Label("Подключиться", systemImage: "video.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(tint.base)
                        .foregroundColor(.white)
                        .cornerRadius(6)
                }
                .buttonStyle(.plain)
                .help("Открыть ссылку встречи: \(url.host ?? url.absoluteString)")
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 7).fill(Color(nsColor: .controlBackgroundColor)))
    }

    private var statusText: String {
        guard let start = event.startDate else { return "" }
        let m = max(0, Int(ceil(start.timeIntervalSince(now) / 60)))
        if m < 60 { return "через \(m) мин · \(timeRange)" }
        let h = m / 60
        let rem = m % 60
        let wait = rem == 0 ? "через \(h) ч" : "через \(h) ч \(rem) мин"
        return "\(wait) · \(timeRange)"
    }

    private var timeRange: String {
        TrayFormatters.timeRange(start: event.startDate, end: event.endDate)
    }
}

// MARK: - Full hero (now or ≤30 min)

private struct HeroCard: View {
    let event: TrayEvent
    let now: Date
    let tint: AccountTint

    private var isNow: Bool {
        guard let s = event.startDate, let e = event.endDate else { return false }
        return s <= now && now < e
    }

    private var statusText: String {
        guard let start = event.startDate else { return "" }
        if isNow, let end = event.endDate {
            let m = max(0, Int(ceil(end.timeIntervalSince(now) / 60)))
            return "Сейчас · осталось \(m) мин"
        }
        let m = max(0, Int(ceil(start.timeIntervalSince(now) / 60)))
        if m < 60 { return "Через \(m) мин" }
        let h = m / 60
        let rem = m % 60
        return rem == 0 ? "Через \(h) ч" : "Через \(h) ч \(rem) мин"
    }

    private var progress: CGFloat {
        guard isNow, let s = event.startDate, let e = event.endDate else { return 0 }
        let total = e.timeIntervalSince(s)
        guard total > 0 else { return 0 }
        return CGFloat(min(1, max(0, now.timeIntervalSince(s) / total)))
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 2).fill(tint.base).frame(width: 4)
            VStack(alignment: .leading, spacing: 4) {
                Text(statusText)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(tint.ink)
                Text(event.subject).strikethrough(event.isCancelled)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.primary)
                    .lineLimit(2)
                HStack(spacing: 6) {
                    Text(timeRange)
                        .font(.system(size: 11).monospacedDigit())
                    if let org = event.organizer?.name ?? event.organizer?.address {
                        Text("· \(org)")
                            .font(.system(size: 11))
                            .lineLimit(1)
                    }
                }
                .foregroundColor(.secondary)
                if isNow {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Color.secondary.opacity(0.2)).frame(height: 3)
                            Capsule().fill(tint.ink).frame(width: max(3, geo.size.width * progress), height: 3)
                        }
                    }
                    .frame(height: 3)
                    .padding(.top, 2)
                }
            }
            Spacer(minLength: 0)
            if let url = event.joinURL {
                Button {
                    NSWorkspace.shared.open(url)
                } label: {
                    Label("Подключиться", systemImage: "video.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(tint.base)
                        .foregroundColor(.white)
                        .cornerRadius(6)
                }
                .buttonStyle(.plain)
                .help("Открыть ссылку встречи: \(url.host ?? url.absoluteString)")
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))
    }

    private var timeRange: String {
        TrayFormatters.timeRange(start: event.startDate, end: event.endDate)
    }
}

// MARK: - Detail pane (replaces popover content — no clip)

private struct EventDetailPane: View {
    let event: TrayEvent
    let now: Date
    var onClose: () -> Void
    var onRespond: (String) -> Void

    @State private var sending: String?
    @State private var localResponse: String?
    @State private var errorText: String?

    private var accent: Color { Color(hex: event.accountTintHex) }

    private var currentResponse: String {
        (localResponse ?? event.responseType ?? "").lowercased()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Button(action: onClose) {
                    Label("Назад", systemImage: "chevron.left")
                        .font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.plain)
                Spacer()
                if let url = event.joinURL {
                    Button {
                        NSWorkspace.shared.open(url)
                    } label: {
                        Label("Подключиться", systemImage: "video.fill")
                            .font(.system(size: 12, weight: .semibold))
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(accent)
                            .foregroundColor(.white)
                            .cornerRadius(6)
                    }
                    .buttonStyle(.plain)
                    .help("Открыть ссылку встречи: \(url.host ?? url.absoluteString)")
                }
            }
            .padding(.horizontal, 12)
            .padding(.top, 12)
            .padding(.bottom, 8)

            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    Text(event.subject).strikethrough(event.isCancelled)
                        .font(.system(size: 16, weight: .semibold))
                    Text(event.isAllDay ? "Весь день" : timeRange)
                        .font(.system(size: 12).monospacedDigit())
                        .foregroundColor(.secondary)

                    if let org = event.organizer {
                        Label {
                            Text(org.name ?? org.address ?? "организатор").font(.system(size: 12))
                        } icon: {
                            Image(systemName: "person.crop.circle")
                        }
                        .foregroundColor(.secondary)
                    }

                    if let location = event.location, !location.isEmpty {
                        if let url = URL(string: location), url.scheme?.hasPrefix("http") == true {
                            Link(destination: url) {
                                Label(location, systemImage: "video").font(.system(size: 11)).lineLimit(3)
                            }
                        } else {
                            Label(location, systemImage: "mappin")
                                .font(.system(size: 11))
                                .foregroundColor(.secondary)
                        }
                    }

                    if let attendees = event.attendees, !attendees.isEmpty {
                        Text("Участники · \(attendees.count)")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.secondary)
                            .padding(.top, 4)
                        ForEach(Array(attendees.enumerated()), id: \.offset) { _, a in
                            HStack(spacing: 4) {
                                Text(a.name ?? a.address ?? "?")
                                    .font(.system(size: 11))
                                    .lineLimit(1)
                                if let st = a.status, !st.isEmpty {
                                    Text("· \(statusRu(st))")
                                        .font(.system(size: 11))
                                        .foregroundColor(.secondary)
                                }
                            }
                        }
                    }

                    if event.canRespond {
                        Divider().padding(.vertical, 4)
                        Text("Ваш ответ")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.secondary)
                        HStack(spacing: 6) {
                            rsvpButton(title: "Принять", response: "accept", activeKey: "accepted", color: .green)
                            rsvpButton(title: "Возможно", response: "tentative", activeKey: "tentative", color: .orange)
                            rsvpButton(title: "Отклонить", response: "decline", activeKey: "declined", color: .red)
                        }
                        if let errorText {
                            Text(errorText).font(.system(size: 11)).foregroundColor(.red)
                        }
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 16)
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
                Text(title).font(.system(size: 10, weight: active ? .semibold : .regular))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .background(active ? color.opacity(0.18) : Color(nsColor: .controlBackgroundColor))
            .foregroundColor(active ? color : .primary)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(active ? color : Color.secondary.opacity(0.25)))
            .cornerRadius(6)
        }
        .buttonStyle(.plain)
        .disabled(sending != nil)
    }

    private var timeRange: String {
        TrayFormatters.timeRange(start: event.startDate, end: event.endDate)
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
    static let hourHeight: CGFloat = 56
    static let timeColWidth: CGFloat = 40
    static let gap: CGFloat = 3
    static let contentWidth: CGFloat = 360 - timeColWidth - 6 - 8

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
        self.hours = Array(minH..<maxH)

        let gridStart = cal.date(bySettingHour: self.hours.first ?? 8, minute: 0, second: 0, of: dayStart)
            ?? dayStart
        func yOffset(for date: Date) -> CGFloat {
            CGFloat(date.timeIntervalSince(gridStart) / 3600.0) * Self.hourHeight
        }

        func interval(_ e: TrayEvent) -> (Date, Date) {
            let s = e.startDate ?? .distantPast
            // Zero-length items still occupy a sliver so they get a column of their own.
            return (s, max(e.endDate ?? s, s.addingTimeInterval(15 * 60)))
        }
        let sorted = timed.sorted {
            let (a0, a1) = interval($0), (b0, b1) = interval($1)
            return a0 != b0 ? a0 < b0 : a1 > b1  // longer first on ties
        }

        // Overlap clusters: every event in a cluster shares the cluster's column
        // count, so widths are consistent and blocks never overlap each other.
        var result: [PlacedEvent] = []
        var cluster: [(event: TrayEvent, col: Int)] = []
        var columnEnds: [Date] = []
        var clusterEnd = Date.distantPast

        func flush() {
            let n = max(1, columnEnds.count)
            let colW = Self.contentWidth / CGFloat(n)
            for (e, col) in cluster {
                let (s, en) = interval(e)
                // Stretch right across columns that stay free for this event's span.
                var span = 1
                for c in (col + 1)..<max(col + 1, n) {
                    let blocked = cluster.contains { other in
                        guard other.col == c else { return false }
                        let (os, oe) = interval(other.event)
                        return os < en && oe > s
                    }
                    if blocked { break }
                    span += 1
                }
                let y = yOffset(for: s)
                let h = max(18, yOffset(for: e.endDate ?? en) - y - 2)
                result.append(PlacedEvent(
                    event: e,
                    x: Self.timeColWidth + 6 + CGFloat(col) * colW,
                    y: y,
                    width: colW * CGFloat(span) - Self.gap,
                    height: h))
            }
            cluster.removeAll()
            columnEnds.removeAll()
            clusterEnd = .distantPast
        }

        for e in sorted {
            let (s, en) = interval(e)
            if s >= clusterEnd { flush() }
            if let free = columnEnds.firstIndex(where: { $0 <= s }) {
                columnEnds[free] = en
                cluster.append((e, free))
            } else {
                columnEnds.append(en)
                cluster.append((e, columnEnds.count - 1))
            }
            clusterEnd = max(clusterEnd, en)
        }
        flush()

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

// MARK: - Event block on the timeline (tap → detail pane)

private struct TimelineEventBlock: View {
    let event: TrayEvent
    let tint: AccountTint
    let now: Date
    let width: CGFloat
    let height: CGFloat
    var onOpen: () -> Void

    private var isNow: Bool {
        guard let s = event.startDate, let e = event.endDate else { return false }
        return s <= now && now < e
    }
    private var isPast: Bool { (event.endDate ?? .distantFuture) <= now }
    private var response: String { (event.responseType ?? "").lowercased() }
    private var declined: Bool { response == "declined" }
    /// Only states that need attention get a label — "accepted" is the norm.
    private var pending: Bool { ["not_responded", "none", "tentative"].contains(response) || event.busyStatus == "tentative" }

    /// How much fits: one line (≤ ~30 min), title + time, or the full card.
    private enum Density { case line, compact, full }
    private var density: Density { height < 34 ? .line : (height < 60 ? .compact : .full) }

    var body: some View {
        Button(action: onOpen) {
            HStack(alignment: .top, spacing: 0) {
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(pending ? tint.base.opacity(0.35) : tint.base)
                    .frame(width: 3)
                content
                    .padding(.leading, 5)
                    .padding(.trailing, 4)
                    .padding(.vertical, density == .line ? 2 : 4)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(
            // Opaque base so nothing underneath ever shows through the text.
            RoundedRectangle(cornerRadius: 4)
                .fill(Color(nsColor: .windowBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 4).fill(tint.wash))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .stroke(isNow ? tint.ink.opacity(0.8) : Color.clear, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .opacity(declined ? 0.45 : (isPast ? 0.75 : 1))
        .help("\(event.subject)\n\(timeRange)")
    }

    @ViewBuilder
    private var content: some View {
        switch density {
        case .line:
            HStack(spacing: 4) {
                title.lineLimit(1)
                if width > 150 { time }
                Spacer(minLength: 0)
                if width > 110 { joinIcon }
            }
        case .compact:
            VStack(alignment: .leading, spacing: 1) {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    title.lineLimit(1)
                    Spacer(minLength: 0)
                    if width > 110 { joinIcon }
                }
                HStack(spacing: 4) {
                    time
                    if pending && width > 150 { pendingLabel }
                }
            }
        case .full:
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    title.lineLimit(width > 150 ? 2 : 3)
                    Spacer(minLength: 0)
                    joinIcon
                }
                time
                if pending { pendingLabel }
            }
        }
    }

    private var title: some View {
        Text(event.subject.isEmpty ? "(без темы)" : event.subject).strikethrough(event.isCancelled)
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(.primary)
            .strikethrough(declined)
            .multilineTextAlignment(.leading)
    }

    private var time: some View {
        Text(timeRange)
            .font(.system(size: 10).monospacedDigit())
            .foregroundColor(.secondary)
            .lineLimit(1)
    }

    @ViewBuilder
    private var joinIcon: some View {
        if event.joinURL != nil {
            Image(systemName: "video.fill")
                .font(.system(size: 9))
                .foregroundColor(tint.ink)
        }
    }

    private var pendingLabel: some View {
        Text(response == "tentative" || event.busyStatus == "tentative" ? "под вопросом" : "без ответа")
            .font(.system(size: 9, weight: .medium))
            .foregroundColor(.orange)
            .lineLimit(1)
    }

    private var timeRange: String {
        TrayFormatters.timeRange(start: event.startDate, end: event.endDate)
    }
}

// MARK: - Account colours

/// Brand colours that stay readable in light and dark menus: `base` for bars and
/// filled buttons (white text on top), `ink` for coloured text/icons on the popover
/// background (lightened in dark mode — the raw wine/emerald is near-black there),
/// `wash` for soft block backgrounds.
struct AccountTint {
    let base: Color
    let ink: Color
    let wash: Color

    private static var cache: [String: AccountTint] = [:]

    static func of(_ hex: String) -> AccountTint {
        if let hit = cache[hex] { return hit }
        let t = AccountTint(hex: hex)
        cache[hex] = t
        return t
    }

    private init(hex: String) {
        let b = NSColor(trayHex: hex) ?? .systemGray
        func dark(_ ap: NSAppearance) -> Bool { ap.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua }
        base = Color(nsColor: b)
        // Dark mode: same hue, brighter and still saturated, so Bank (rose) and
        // Seller (teal) stay distinguishable instead of both fading to grey.
        let hsb = b.usingColorSpace(.sRGB) ?? b
        let bright = NSColor(hue: hsb.hueComponent, saturation: min(hsb.saturationComponent, 0.55),
                             brightness: 0.88, alpha: 1)
        ink = Color(nsColor: NSColor(name: nil) { ap in dark(ap) ? bright : b })
        wash = Color(nsColor: NSColor(name: nil) { ap in
            dark(ap) ? b.withAlphaComponent(0.55) : b.withAlphaComponent(0.09)
        })
    }
}

extension NSColor {
    convenience init?(trayHex hex: String) {
        var s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                  blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }
}

extension Color {
    init(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        s.removeAll { $0 == "#" }
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        self.init(red: Double((v >> 16) & 0xFF) / 255,
                  green: Double((v >> 8) & 0xFF) / 255,
                  blue: Double(v & 0xFF) / 255)
    }
}
