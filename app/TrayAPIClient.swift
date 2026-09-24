import Foundation

struct TrayAccountSummary: Decodable { let id: String }
struct TrayAccountsResponse: Decodable { let ok: Bool; let accounts: [TrayAccountSummary] }

enum TrayAPIError: Error { case noToken, badResponse, serverError(String) }

final class TrayAPIClient {
    private let baseURL = URL(string: "http://127.0.0.1:8780")!
    private let token: String?

    init() {
        let path = (NSHomeDirectory() as NSString).appendingPathComponent(".config/eas-bridge/runtime_token")
        token = try? String(contentsOfFile: path, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func post(_ path: String, _ body: [String: Any]) async throws -> Data {
        guard let token = token, !token.isEmpty else { throw TrayAPIError.noToken }
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue(token, forHTTPHeaderField: "X-Tok")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { throw TrayAPIError.badResponse }
        return data
    }

    func listEvents(acct: String, start: String, end: String) async throws -> [TrayEvent] {
        let data = try await post("/api/events", ["acct": acct, "action": "list", "start": start, "end": end, "limit": 300])
        let decoded = try JSONDecoder().decode(TrayEventListResponse.self, from: data)
        // `{"ok": false, "items": [], "error": "calendar_unavailable"}` decodes
        // cleanly as "zero events" — surface it as an error instead, or a
        // transient backend blip silently looks like an empty day.
        guard decoded.ok else {
            throw TrayAPIError.serverError(decoded.message ?? decoded.error ?? "не удалось получить события")
        }
        return decoded.items
    }

    /// RSVP: accept / tentative / decline (MeetingResponse). Notifies the organizer.
    func respondToEvent(acct: String, itemId: String, response: String) async throws {
        let data = try await post("/api/events", [
            "acct": acct, "action": "respond", "item_id": itemId,
            "response": response, "notify": true,
        ])
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        if (obj?["ok"] as? Bool) == false {
            throw TrayAPIError.serverError(obj?["message"] as? String ?? "не удалось ответить на встречу")
        }
    }

    func accountIds() async throws -> Set<String> {
        let data = try await post("/api/accounts", [:])
        let decoded = try JSONDecoder().decode(TrayAccountsResponse.self, from: data)
        return Set(decoded.accounts.map { $0.id })
    }
}
