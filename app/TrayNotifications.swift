import AppKit
import Foundation
import UserNotifications

extension Notification.Name {
    /// Bring the main mail window forward and switch the web UI to the calendar tab.
    static let easShowCalendar = Notification.Name("easShowCalendar")
    /// Settings → «Напоминать о встрече за…» changed (value in UserDefaults).
    static let easReminderLeadChanged = Notification.Name("easReminderLeadChanged")
    /// Tray «Создать»: open the main window's «Новое событие» form. userInfo["day"] = "YYYY-MM-DD".
    static let easCreateEvent = Notification.Name("easCreateEvent")
}

/// Meeting reminder lead time chosen in Settings; 0 = no reminders.
enum ReminderLead {
    static let key = "reminderMinutes"
    static let allowed = [0, 1, 2, 5, 10, 15, 30]
    static var minutes: Int {
        get { UserDefaults.standard.object(forKey: key) as? Int ?? 5 }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }
}

/// Settings → «Звук нового письма»: bundled CAF files (or silent).
enum MailSound: String {
    case notice14
    case short
    case none

    static let key = "mailSound"
    static let allowed = ["notice14", "short", "none"]
    static let `default`: MailSound = .notice14

    static var current: MailSound {
        get {
            let raw = UserDefaults.standard.string(forKey: key) ?? Self.default.rawValue
            return MailSound(rawValue: raw) ?? .default
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: key) }
    }

    /// Sound for the macOS banner; `nil` = silent notification.
    var notificationSound: UNNotificationSound? {
        switch self {
        case .none: return nil
        case .notice14, .short:
            return UNNotificationSound(named: UNNotificationSoundName("\(rawValue).caf"))
        }
    }

    /// Preview from Settings (same file the banner uses).
    func playPreview() {
        guard self != .none else { return }
        let url = Bundle.main.url(forResource: rawValue, withExtension: "caf")
        guard let url, let sound = NSSound(contentsOf: url, byReference: true) else { return }
        sound.play()
    }
}

/// Immediate local notification when the web UI detects fresh unread Inbox mail.
enum NewMailNotifier {
    static func notify(from: String, subject: String, preview: String, count: Int, account: String) {
        let content = UNMutableNotificationContent()
        if count <= 1 {
            let who = from.isEmpty ? "Новое письмо" : from
            content.title = account.isEmpty ? who : "\(who) · \(account)"
            content.subtitle = subject
            content.body = preview
        } else {
            content.title = account.isEmpty ? "Новые письма" : "Новые письма · \(account)"
            content.body = "У вас \(count) новых писем"
        }
        content.sound = MailSound.current.notificationSound
        let id = "eas-mail-\(UUID().uuidString)"
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: id, content: content, trigger: nil)
        ) { error in
            if let error { NSLog("mail: уведомление не доставлено: %@", "\(error)") }
        }
    }
}
