import Foundation

enum AvailabilityStatus: Equatable {
    case free, tentative, busy, outOfOffice, unknown

    var label: String {
        switch self {
        case .free: return "свободен"
        case .tentative: return "под вопросом"
        case .busy: return "занят"
        case .outOfOffice: return "вне офиса"
        case .unknown: return "нет данных"
        }
    }

    /// Same three-visual-state grouping as the web UI (ok / warn / bad / unknown).
    var marker: String {
        switch self {
        case .free: return "✅"
        case .tentative: return "⚠️"
        case .busy, .outOfOffice: return "⛔"
        case .unknown: return "❔"
        }
    }
}

enum TrayAvailability {
    /// `freebusy` is a digit string (one code per half-hour slot in the
    /// requested window), exactly as `people/availability` already returns
    /// it. Priority matches the web UI: busy(2) > out-of-office(3) >
    /// tentative(1) > free(0) — any busy slot in the window wins.
    static func status(freebusy: String?) -> AvailabilityStatus {
        let fb = freebusy ?? ""
        for code in ["2", "3", "1", "0"] where fb.contains(code) {
            switch code {
            case "2": return .busy
            case "3": return .outOfOffice
            case "1": return .tentative
            case "0": return .free
            default: break
            }
        }
        return .unknown
    }
}
