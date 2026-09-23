import SwiftUI

struct EventCreateView: View {
    let hasSeller: Bool
    var onDone: () -> Void

    @State private var account = "main"
    @State private var subject = ""
    @State private var location = ""
    @State private var bodyText = ""
    @State private var attendeesText = ""
    @State private var start = Date().addingTimeInterval(3600)
    @State private var end = Date().addingTimeInterval(7200)
    @State private var availabilityRows: [(name: String, status: AvailabilityStatus)] = []
    @State private var availabilityNote: String?
    @State private var isChecking = false
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var availabilityToken = UUID()

    private let client = TrayAPIClient()
    private let isoLocal: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm"
        f.timeZone = .current
        return f
    }()
    private let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = .current
        return f
    }()

    var body: some View {
        Form {
            if hasSeller {
                Picker("Календарь", selection: $account) {
                    Text("Alfa-Bank").tag("main")
                    Text("Alfa-Seller").tag("seller")
                }.pickerStyle(.segmented)
            }
            TextField("Тема", text: $subject)
            DatePicker("Начало", selection: $start, displayedComponents: [.date, .hourAndMinute])
            DatePicker("Конец", selection: $end, displayedComponents: [.date, .hourAndMinute])
            TextField("Место / ссылка", text: $location)
            TextField("Участники (через запятую)", text: $attendeesText)
                .onChange(of: attendeesText) { _ in scheduleAvailabilityCheck() }
                .onChange(of: start) { _ in scheduleAvailabilityCheck() }
                .onChange(of: end) { _ in scheduleAvailabilityCheck() }
            if isChecking { ProgressView().controlSize(.small) }
            ForEach(availabilityRows, id: \.name) { row in
                HStack { Text(row.status.marker); Text(row.name); Spacer(); Text(row.status.label).foregroundColor(.secondary) }
            }
            if let availabilityNote { Text(availabilityNote).font(.caption).foregroundColor(.secondary) }
            TextField("Описание", text: $bodyText, axis: .vertical).lineLimit(3...6)
            if let errorMessage { Text(errorMessage).foregroundColor(.red).font(.caption) }
            HStack {
                Button("Отмена") { onDone() }
                Spacer()
                Button("Создать") { Task { await save() } }
                    .disabled(subject.isEmpty || end <= start || isSaving)
            }
        }
        .padding(16)
        .frame(width: 360)
    }

    private var attendeeList: [String] {
        attendeesText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { $0.contains("@") }
    }

    private func scheduleAvailabilityCheck() {
        let token = UUID(); availabilityToken = token
        let list = attendeeList
        guard !list.isEmpty, end > start else { availabilityRows = []; availabilityNote = nil; return }
        Task {
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard availabilityToken == token else { return }
            isChecking = true
            defer { isChecking = false }
            do {
                let (items, unresolved) = try await client.availability(
                    acct: account, who: list,
                    start: isoLocal.string(from: start), end: isoLocal.string(from: end))
                guard availabilityToken == token else { return }
                availabilityRows = items.map {
                    ($0.name ?? $0.address ?? "?", TrayAvailability.status(freebusy: $0.freebusy))
                }
                availabilityNote = unresolved.isEmpty
                    ? nil
                    : "Не найдены: \(unresolved.joined(separator: ", "))"
            } catch {
                // Stalwart (Alfa-Seller) has no free/busy lookup — mirror the
                // web UI's fallback (web/index.html:829-834): check only the
                // organizer's own calendar for overlaps instead.
                do {
                    let dayAfterEnd = Calendar.current.date(byAdding: .day, value: 1, to: end)!
                    let own = try await client.listEvents(acct: account, start: isoDay.string(from: start), end: isoDay.string(from: dayAfterEnd))
                    guard availabilityToken == token else { return }
                    let clashes = own.filter { e in
                        guard let es = e.startDate, let ee = e.endDate else { return false }
                        return es < end && ee > start && e.busyStatus != "free"
                    }
                    availabilityRows = []
                    availabilityNote = clashes.isEmpty
                        ? "ℹ️ Этот сервер не отдаёт занятость участников — у вас в это время свободно."
                        : "ℹ️ Этот сервер не отдаёт занятость участников. Пересечение: \(clashes.map { $0.subject }.joined(separator: ", "))"
                } catch {
                    guard availabilityToken == token else { return }
                    availabilityNote = "Не удалось проверить занятость"
                }
            }
        }
    }

    private func save() async {
        isSaving = true; errorMessage = nil
        defer { isSaving = false }
        do {
            try await client.createEvent(acct: account, subject: subject, location: location, body: bodyText,
                                          start: isoLocal.string(from: start), end: isoLocal.string(from: end),
                                          attendees: attendeeList)
            onDone()
        } catch {
            errorMessage = "\(error)"
        }
    }
}
